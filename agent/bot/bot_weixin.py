"""微信渠道适配器（ClawBot · iLink 协议）—— TriggerAdapter 实现

移植自 NousResearch/hermes-agent `gateway/platforms/weixin.py`（MIT License，
Copyright (c) 2025 Nous Research），按本项目 TriggerAdapter 接口裁剪改造：
  保留：iLink 长轮询、QR 扫码登录、context_token 磁盘缓存、AES-128-ECB 加密
        CDN 媒体收发、-2 限流退避、-14 会话过期处理、tokenless 降级发送
  裁掉：hermes agent 回合、typing 指示、plugin hooks、群聊、markdown 分片花活

设计对应：设计文档 v1.1 §3.3（TriggerAdapter）、§3.4（推送降级）、§3.7（微信渠道列）
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

from bot.base import Intent, TriggerAdapter

logger = logging.getLogger("bot.weixin")

try:
    import aiohttp
except ImportError:  # pragma: no cover
    aiohttp = None
try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError:  # pragma: no cover
    default_backend = Cipher = algorithms = modes = None

# ─── iLink 协议常量（源自 hermes 预验证，见验证手册附录 A） ──────────────
ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
WEIXIN_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
ILINK_APP_ID, CHANNEL_VERSION, ILINK_APP_CLIENT_VERSION = "bot", "2.2.0", (2 << 16) | (2 << 8) | 0
EP_GET_UPDATES = "ilink/bot/getupdates"
EP_SEND_MESSAGE = "ilink/bot/sendmessage"
EP_GET_UPLOAD_URL = "ilink/bot/getuploadurl"
EP_GET_BOT_QR = "ilink/bot/get_bot_qrcode"
EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"
LONG_POLL_TIMEOUT_MS, API_TIMEOUT_MS, QR_TIMEOUT_MS = 35_000, 15_000, 35_000
MAX_CONSECUTIVE_FAILURES, RETRY_DELAY_SECONDS, BACKOFF_DELAY_SECONDS = 3, 2, 30
SESSION_EXPIRED_ERRCODE, RATE_LIMIT_ERRCODE = -14, -2
MAX_TEXT_LEN = 1800          # iLink 约 2048 字符处切块，hermes 实测阈值取 1800
CHUNK_DELAY_SECONDS = 1.5    # 分片发送间隔
SEND_RETRIES = 4
CDN_UPLOAD_RETRIES = 3       # CDN PUT 重试：5xx/网络异常退避重试（500 空 body 多为瞬时故障）
MEDIA_IMAGE, MEDIA_VIDEO, MEDIA_FILE = 1, 2, 3      # getuploadurl media_type
ITEM_TEXT, ITEM_IMAGE, ITEM_FILE, ITEM_VIDEO = 1, 2, 4, 5  # item_list 类型
MSG_TYPE_BOT, MSG_STATE_FINISH = 2, 2
_CDN_ALLOWLIST = frozenset({
    "novac2c.cdn.weixin.qq.com", "ilinkai.weixin.qq.com", "wx.qlogo.cn",
    "thirdwx.qlogo.cn", "res.wx.qq.com", "mmbiz.qpic.cn", "mmbiz.qlogo.cn"})


# ─── AES-128-ECB（CDN 媒体加密，hermes 已踩平的实现） ────────────────────

def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _aes128_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend()).encryptor()
    return enc.update(_pkcs7_pad(plaintext)) + enc.finalize()


def _aes128_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    dec = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend()).decryptor()
    padded = dec.update(ciphertext) + dec.finalize()
    pad_len = padded[-1] if padded else 0
    if 1 <= pad_len <= 16 and padded.endswith(bytes([pad_len]) * pad_len):
        return padded[:-pad_len]
    return padded


def _parse_aes_key(aes_key_b64: str) -> bytes:
    decoded = base64.b64decode(aes_key_b64)
    if len(decoded) == 16:
        return decoded
    text = decoded.decode("ascii", errors="ignore") if len(decoded) == 32 else ""
    if text and all(ch in "0123456789abcdefABCDEF" for ch in text):
        return bytes.fromhex(text)
    raise ValueError(f"unexpected aes_key format ({len(decoded)} decoded bytes)")


def _video_play_length(path: str) -> int:
    """ffprobe 探测视频时长（秒，uint32）。微信协议 play_length 语义 = 视频秒数
    （官方文档示例值 24）；hermes 原版恒传 0 未经验证，实测 play_length=0 时
    客户端播放器初始化异常——转圈、视频层不渲染（黑屏有声，音频流不受影响）。
    探测失败回退 1s 并告警（0 是已知致错值，不可回退到 0）。"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path], capture_output=True, text=True,
            check=True, timeout=30)
        return max(1, int(round(float(r.stdout.strip()))))
    except Exception as exc:
        logger.warning("[weixin] play_length 探测失败，回退 1s: %s", exc)
        return 1


# ─── 错误分类（hermes 预验证结论：-2 有两种语义） ────────────────────────

def _is_stale_session(ret, errcode, errmsg) -> bool:
    """-2 + 'unknown error'/'prepare failed' = 会话陈旧（非真限流）。"""
    return (ret == RATE_LIMIT_ERRCODE or errcode == RATE_LIMIT_ERRCODE) and \
        (errmsg or "").lower() in {"unknown error", "prepare failed"}


def _is_session_expired(resp, ret, errcode) -> bool:
    return SESSION_EXPIRED_ERRCODE in (ret, errcode) or \
        _is_stale_session(ret, errcode, resp.get("errmsg") or resp.get("msg"))


def _headers(token: Optional[str], body: str) -> Dict[str, str]:
    uin = base64.b64encode(str(int.from_bytes(secrets.token_bytes(4), "big")).encode()).decode()
    h = {"Content-Type": "application/json", "AuthorizationType": "ilink_bot_token",
         "Content-Length": str(len(body.encode("utf-8"))), "X-WECHAT-UIN": uin,
         "iLink-App-Id": ILINK_APP_ID, "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION)}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


# ─── 账号与 context_token 持久化 ─────────────────────────────────────────

class AccountStore:
    """token / account_id / context_token 落盘（重启免扫、会话续接）。"""

    def __init__(self, state_dir: str):
        self.dir = Path(state_dir) / "weixin"
        self.dir.mkdir(parents=True, exist_ok=True)

    def save_account(self, account_id: str, token: str, base_url: str, user_id: str = "") -> None:
        path = self.dir / f"{account_id}.json"
        path.write_text(json.dumps({
            "token": token, "base_url": base_url, "user_id": user_id,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass  # Windows 不支持 Unix 权限，忽略

    def load_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        path = self.dir / f"{account_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def first_account_id(self) -> Optional[str]:
        """扫描 state/weixin/ 目录，返回第一个已登录账号的 account_id。
        排除 *.context-tokens.json 和 *.sync.json 等辅助文件，只认 {account_id}.json。
        用于 config.weixin.account_id 为空时自动加载扫码登录产物，免去手工填写。
        """
        if not self.dir.exists():
            return None
        for p in sorted(self.dir.glob("*.json")):
            name = p.name
            if name.endswith(".context-tokens.json") or name.endswith(".sync.json"):
                continue
            account_id = name[:-5]  # 去掉 .json 后缀
            data = self.load_account(account_id)
            if data and data.get("token"):
                return account_id
        return None

    # context_token：按 peer 持久化（磁盘缓存，hermes ContextTokenStore 简化版）
    def _ctx_path(self, account_id: str) -> Path:
        return self.dir / f"{account_id}.context-tokens.json"

    def load_context_tokens(self, account_id: str) -> Dict[str, str]:
        path = self._ctx_path(account_id)
        if not path.exists():
            return {}
        try:
            return {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items()
                    if isinstance(v, str) and v}
        except Exception:
            return {}

    def save_context_token(self, account_id: str, peer: str, token: str) -> None:
        data = self.load_context_tokens(account_id)
        data[peer] = token
        self._ctx_path(account_id).write_text(json.dumps(data), encoding="utf-8")

    def drop_context_token(self, account_id: str, peer: str) -> None:
        data = self.load_context_tokens(account_id)
        data.pop(peer, None)
        self._ctx_path(account_id).write_text(json.dumps(data), encoding="utf-8")


# ─── QR 扫码登录（供 main.py --qr-login 使用） ──────────────────────────

async def qr_login(state_dir: str, timeout_seconds: int = 480) -> Optional[Dict[str, str]]:
    """拉二维码 → 打印 URL → 轮询扫码状态 → 保存账号。返回凭据 dict 或 None。"""
    async with aiohttp.ClientSession() as session:
        async def get(url, timeout_ms):
            async def _do():
                async with session.get(url) as r:
                    return json.loads(await r.text())
            return await asyncio.wait_for(_do(), timeout=timeout_ms / 1000)

        qr = await get(f"{ILINK_BASE_URL}/{EP_GET_BOT_QR}?bot_type=3", QR_TIMEOUT_MS)
        qrcode_value, qrcode_url = str(qr.get("qrcode") or ""), str(qr.get("qrcode_img_content") or "")
        if not qrcode_value:
            logger.error("获取二维码失败: %s", qr)
            return None
        print("\n请用微信扫描此二维码（浏览器打开链接或扫终端图形）：")
        print(qrcode_url or qrcode_value)
        try:
            import qrcode
            qr_obj = qrcode.QRCode()
            qr_obj.add_data(qrcode_url or qrcode_value)
            qr_obj.make(fit=True)
            qr_obj.print_ascii(invert=True)
        except Exception:
            print("（终端二维码渲染失败，请直接打开上面的链接扫码）")

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                r = await get(f"{ILINK_BASE_URL}/{EP_GET_QR_STATUS}?qrcode={qrcode_value}", QR_TIMEOUT_MS)
            except Exception:
                await asyncio.sleep(1)
                continue
            status = str(r.get("status") or "wait")
            if status == "wait":
                print(".", end="", flush=True)
            elif status == "scaned":
                print("\n已扫码，请在微信里确认...")
            elif status == "expired":
                print("\n二维码已过期，请重新执行 --qr-login")
                return None
            elif status == "confirmed":
                creds = {"account_id": str(r.get("ilink_bot_id") or ""),
                         "token": str(r.get("bot_token") or ""),
                         "base_url": str(r.get("baseurl") or ILINK_BASE_URL),
                         "user_id": str(r.get("ilink_user_id") or "")}
                if not creds["account_id"] or not creds["token"]:
                    logger.error("扫码确认但凭据不完整: %s", r)
                    return None
                AccountStore(state_dir).save_account(**creds)
                print(f"\n微信登录成功 account_id={creds['account_id']}，已保存到 {state_dir}/weixin/")
                return creds
            await asyncio.sleep(1)
        print("\n登录超时。")
        return None


# ─── 适配器本体 ──────────────────────────────────────────────────────────

class WeixinTriggerAdapter(TriggerAdapter):
    """iLink 长轮询适配器。白名单外消息一律忽略；入站媒体下载解密后随 Intent 交给 Router。"""

    name = "weixin"

    def __init__(self, config: dict, state_dir: str):
        self._cfg = config or {}
        self._store = AccountStore(state_dir)
        self._account_id = str(self._cfg.get("account_id") or "")
        self._token = str(self._cfg.get("token") or "")
        self._base_url = str(self._cfg.get("base_url") or ILINK_BASE_URL).rstrip("/")
        self._cdn = str(self._cfg.get("cdn_base_url") or WEIXIN_CDN_BASE_URL).rstrip("/")
        self._allow = {str(u).strip() for u in (self._cfg.get("allow_users") or []) if str(u).strip()}
        # config 未填 account_id 时，自动扫描 state/weixin/ 加载扫码登录产物
        if not self._account_id:
            auto = self._store.first_account_id()
            if auto:
                self._account_id = auto
                logger.info("[weixin] config 未填 account_id，自动加载已登录账号 %s", auto)
        # 落盘凭据优先于 config 内联值（--qr-login 产物）
        persisted = self._store.load_account(self._account_id) if self._account_id else None
        if persisted:
            self._token = self._token or str(persisted.get("token") or "")
            self._base_url = str(persisted.get("base_url") or self._base_url).rstrip("/")
        self._ctx_tokens = self._store.load_context_tokens(self._account_id) if self._account_id else {}
        self._poll_session: Optional[aiohttp.ClientSession] = None
        self._send_session: Optional[aiohttp.ClientSession] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._running = False
        self._seen_msg_ids: set = set()  # 简易去重（近期消息 id）
        self._on_intent: Optional[Callable[[Intent], None]] = None

    # ---- 生命周期 ----

    async def start(self, on_intent) -> None:
        if aiohttp is None or Cipher is None:
            raise RuntimeError("weixin 通道需要 aiohttp 与 cryptography（pip install -r requirements.txt）")
        if not self._token or not self._account_id:
            raise RuntimeError(
                "weixin 未配置 token/account_id：请先运行 `python main.py --qr-login` 扫码，"
                "或手工运行验证手册 V1（hermes gateway）后把 ~/.hermes/weixin/accounts/*.json "
                "中的值填入 config.yaml")
        self._on_intent = on_intent
        self._running = True
        self._poll_session = aiohttp.ClientSession()
        self._send_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None))  # send 走 wait_for 超时，禁用会话级超时
        self._poll_task = asyncio.create_task(self._poll_loop(), name="weixin-poll")
        logger.info("[weixin] 已连接 account=%s…", self._account_id[:8])
        try:
            await self._poll_task  # 阻塞直到 poll_task 结束（被 stop() cancel 或异常退出）
        except asyncio.CancelledError:
            pass  # 正常停止：stop() 主动 cancel，不当作错误

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except (asyncio.CancelledError, Exception):
                pass
        for attr in ("_poll_session", "_send_session"):
            s = getattr(self, attr)
            if s and not s.closed:
                await s.close()
            setattr(self, attr, None)

    async def open_sender(self) -> None:
        """仅初始化发送通道（不启动收消息轮询）。

        用于 bot 进程之外的一次性推送（--radar-now 等）：start() 会阻塞在
        poll_loop 上，只推送时用本方法即可。
        """
        if not self._token or not self._account_id:
            raise RuntimeError(
                "weixin 未配置 token/account_id：请先运行 `python main.py --qr-login` 扫码")
        if not self._send_session or self._send_session.closed:
            self._send_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None))

    async def close_sender(self) -> None:
        """关闭 open_sender() 打开的发送通道。"""
        if self._send_session and not self._send_session.closed:
            await self._send_session.close()
        self._send_session = None

    # ---- HTTP 基础 ----

    async def _api_post(self, endpoint: str, payload: dict, token: Optional[str], timeout_ms: int) -> dict:
        body = json.dumps({**payload, "base_info": {"channel_version": CHANNEL_VERSION}},
                          ensure_ascii=False, separators=(",", ":"))

        async def _do():
            async with self._send_session.post(
                    f"{self._base_url}/{endpoint}", data=body, headers=_headers(token, body)) as r:
                raw = await r.text()
                if not r.ok:
                    raise RuntimeError(f"iLink {endpoint} HTTP {r.status}: {raw[:200]}")
                return json.loads(raw)
        return await asyncio.wait_for(_do(), timeout=timeout_ms / 1000)

    async def _api_get(self, url: str, timeout_ms: int) -> dict:
        async def _do():
            async with self._poll_session.get(url, headers={
                    "iLink-App-Id": ILINK_APP_ID,
                    "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION)}) as r:
                return json.loads(await r.text())
        return await asyncio.wait_for(_do(), timeout=timeout_ms / 1000)

    # ---- 入站：长轮询 ----

    async def _poll_loop(self) -> None:
        sync_path = self._store.dir / f"{self._account_id}.sync.json"
        sync_buf = ""
        if sync_path.exists():
            try:
                sync_buf = json.loads(sync_path.read_text(encoding="utf-8")).get("get_updates_buf", "")
            except Exception:
                pass
        failures = 0
        while self._running:
            try:
                r = await self._api_post(
                    EP_GET_UPDATES, {"get_updates_buf": sync_buf}, self._token, LONG_POLL_TIMEOUT_MS)
                ret, errcode = r.get("ret", 0), r.get("errcode", 0)
                if ret not in (0, None) or errcode not in (0, None):
                    if _is_session_expired(r, ret, errcode):
                        logger.error("[weixin] 会话过期(-14)，暂停 10 分钟")
                        await asyncio.sleep(600)
                        failures = 0
                        continue
                    failures += 1
                    logger.warning("[weixin] getUpdates 失败 ret=%s errcode=%s (%d/%d)",
                                   ret, errcode, failures, MAX_CONSECUTIVE_FAILURES)
                    await asyncio.sleep(BACKOFF_DELAY_SECONDS if failures >= MAX_CONSECUTIVE_FAILURES
                                        else RETRY_DELAY_SECONDS)
                    failures = 0 if failures >= MAX_CONSECUTIVE_FAILURES else failures
                    continue
                failures = 0
                new_buf = r.get("get_updates_buf") or ""
                msgs = r.get("msgs") or []
                logger.info("[weixin] poll 收到响应 msgs=%d 条 sync_buf 更新=%s",
                            len(msgs), bool(new_buf))
                if new_buf:
                    sync_buf = str(new_buf)
                    sync_path.write_text(json.dumps({"get_updates_buf": sync_buf}), encoding="utf-8")
                for message in msgs:
                    logger.info("[weixin] 处理消息 from=%s msg_id=%s",
                                str(message.get("from_user_id") or "")[:12],
                                str(message.get("message_id") or "")[:8])
                    asyncio.create_task(self._process_message_safe(message))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                failures += 1
                logger.error("[weixin] poll 异常 (%d/%d): %s", failures, MAX_CONSECUTIVE_FAILURES, exc)
                await asyncio.sleep(BACKOFF_DELAY_SECONDS if failures >= MAX_CONSECUTIVE_FAILURES
                                    else RETRY_DELAY_SECONDS)
                failures = 0 if failures >= MAX_CONSECUTIVE_FAILURES else failures

    async def _process_message_safe(self, message: dict) -> None:
        try:
            await self._process_message(message)
        except Exception:
            logger.exception("[weixin] 入站消息处理异常 from=%s",
                             str(message.get("from_user_id") or "")[:8])

    async def _process_message(self, message: dict) -> None:
        sender = str(message.get("from_user_id") or "").strip()
        msg_id = str(message.get("message_id") or "").strip()
        if not sender or sender == self._account_id:
            return
        if msg_id:
            if msg_id in self._seen_msg_ids:
                return
            self._seen_msg_ids.add(msg_id)
            if len(self._seen_msg_ids) > 500:
                self._seen_msg_ids = set(list(self._seen_msg_ids)[-250:])
        # 群聊不支持（iLink bot 身份进不了普通群，预验证结论 #8）：room 消息直接忽略
        if str(message.get("room_id") or message.get("chat_room_id") or "").strip():
            return
        # 白名单（Phase 1 列表制；空名单 = 全拒绝并提示配置方法）
        if sender not in self._allow:
            logger.info("[weixin] 白名单外消息 sender=%s（加入 config.allow_users 可放行）", sender[:12])
            try:
                await self._send_text_chunk(sender, "未授权用户。请管理员在 config.yaml "
                                                    "channel.weixin.allow_users 中添加你的 ID: "
                                                    f"{sender}", None)
            except Exception:
                pass
            return
        # context_token 缓存（出站回复必须回显 peer 最新 token）
        ctx = str(message.get("context_token") or "").strip()
        if ctx:
            self._ctx_tokens[sender] = ctx
            self._store.save_context_token(self._account_id, sender, ctx)
        # 解析文本与媒体
        item_list = message.get("item_list") or []
        text = self._extract_text(item_list)
        media = await self._download_media_items(item_list)
        if not text and not media:
            return
        intent = Intent(user_id=f"weixin:{sender}", channel="weixin",
                        text=text, media=media, ts=time.time())
        if self._on_intent:
            # Router.handle 是 async 协程，必须 await（否则协程从不执行，消息被静默丢弃）
            res = self._on_intent(intent)
            if asyncio.iscoroutine(res):
                await res

    @staticmethod
    def _extract_text(item_list: List[dict]) -> str:
        for item in item_list:
            if item.get("type") == ITEM_TEXT:
                return str((item.get("text_item") or {}).get("text") or "")
        return ""

    async def _download_media_items(self, item_list: List[dict]) -> List[str]:
        """入站媒体：CDN 下载 + AES 解密，缓存到临时目录，返回本地路径列表。"""
        paths: List[str] = []
        specs = {ITEM_IMAGE: ("image_item", ".jpg"), ITEM_VIDEO: ("video_item", ".mp4"),
                 ITEM_FILE: ("file_item", None)}
        for item in item_list:
            item_key = specs.get(item.get("type"))
            if not item_key:
                continue
            key, suffix = item_key
            payload = item.get(key) or {}
            media = payload.get("media") or {}
            filename = str(payload.get("file_name") or "") or (suffix or "file.bin")
            try:
                url, aes = self._media_url(media)
                async def _fetch():
                    async with self._poll_session.get(url) as r:
                        r.raise_for_status()
                        return await r.read()
                data = await asyncio.wait_for(_fetch(), timeout=120)
                if aes:
                    data = _aes128_ecb_decrypt(data, _parse_aes_key(aes))
                out = Path(self._store.dir) / "inbox" / f"{uuid.uuid4().hex[:8]}_{filename}"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)
                paths.append(str(out))
            except Exception as exc:
                logger.warning("[weixin] 媒体下载失败: %s", exc)
        return paths

    def _media_url(self, media: dict) -> Tuple[str, Optional[str]]:
        if media.get("encrypt_query_param"):
            url = (f"{self._cdn}/download?encrypted_query_param="
                   f"{quote(str(media['encrypt_query_param']), safe='')}")
        elif media.get("full_url"):
            full = str(media["full_url"])
            host = re.sub(r"^https?://", "", full).split("/")[0]
            if host not in _CDN_ALLOWLIST:
                raise ValueError(f"非白名单媒体域: {host}")
            url = full
        else:
            raise ValueError("媒体项无 encrypt_query_param 也无 full_url")
        return url, media.get("aes_key") or None

    # ---- 出站：文本（分片 + 限流退避 + tokenless 降级） ----

    async def send_text(self, uid: str, text: str) -> None:
        peer = uid.split(":", 1)[1] if ":" in uid else uid
        chunks = [text[i:i + MAX_TEXT_LEN] for i in range(0, len(text), MAX_TEXT_LEN)] or [text]
        for idx, chunk in enumerate(chunks):
            await self._send_text_chunk(peer, chunk, self._ctx_tokens.get(peer))
            if idx < len(chunks) - 1:
                await asyncio.sleep(CHUNK_DELAY_SECONDS)

    async def push_text(self, uid: str, text: str) -> None:
        """主动推送（雷达 cron）：tokenless 路径（预验证结论 #7）。
        失败抛异常，由 Router 落 pending_push 补发队列。"""
        peer = uid.split(":", 1)[1] if ":" in uid else uid
        await self._send_text_chunk(peer, text, None)

    async def _send_text_chunk(self, peer: str, chunk: str, context_token: Optional[str]) -> None:
        last_error: Optional[Exception] = None
        retried_tokenless = False
        attempt = 0
        while True:
            try:
                msg = {"from_user_id": "", "to_user_id": peer,
                       "client_id": f"xhs-{uuid.uuid4().hex}",
                       "message_type": MSG_TYPE_BOT, "message_state": MSG_STATE_FINISH,
                       "item_list": [{"type": ITEM_TEXT, "text_item": {"text": chunk}}]}
                if context_token:
                    msg["context_token"] = context_token
                r = await self._api_post(EP_SEND_MESSAGE, {"msg": msg}, self._token, API_TIMEOUT_MS)
                ret, errcode = r.get("ret"), r.get("errcode")
                if (ret not in (None, 0)) or (errcode not in (None, 0)):
                    errmsg = r.get("errmsg") or r.get("msg")
                    # 会话陈旧：去掉 token 重发一次（tokenless 降级，hermes 实证可行）
                    if _is_session_expired(r, ret, errcode) and context_token and not retried_tokenless:
                        retried_tokenless = True
                        context_token = None
                        self._ctx_tokens.pop(peer, None)
                        self._store.drop_context_token(self._account_id, peer)
                        continue
                    if _is_stale_session(ret, errcode, errmsg):
                        raise RuntimeError(
                            f"iLink 会话未就绪（用户需先给 bot 发一条消息）: {errmsg}")
                    if ret == RATE_LIMIT_ERRCODE or errcode == RATE_LIMIT_ERRCODE:
                        if attempt >= SEND_RETRIES:
                            raise RuntimeError(f"iLink 限流重试耗尽: {errmsg}")
                        attempt += 1
                        await asyncio.sleep(1.0 * 3 * attempt)  # 3 倍退避
                        continue
                    raise RuntimeError(f"iLink sendmessage 错误: ret={ret} errcode={errcode} {errmsg}")
                return
            except RuntimeError:
                raise
            except Exception as exc:  # 网络层异常：退避重试
                if attempt >= SEND_RETRIES:
                    raise RuntimeError(f"iLink 发送失败（重试 {attempt} 次）: {exc}") from exc
                attempt += 1
                await asyncio.sleep(1.0 * attempt)

    # ---- 出站：媒体（AES 加密上传 CDN） ----

    async def send_image(self, uid: str, path: str, caption: str = "") -> None:
        await self._send_media(uid, path, MEDIA_IMAGE, caption)

    async def send_video(self, uid: str, path: str, caption: str = "") -> None:
        await self._send_media(uid, path, MEDIA_VIDEO, caption)

    async def send_file(self, uid: str, path: str, caption: str = "") -> None:
        await self._send_media(uid, path, MEDIA_FILE, caption)

    async def _send_media(self, uid: str, path: str, media_type: int, caption: str = "") -> None:
        peer = uid.split(":", 1)[1] if ":" in uid else uid
        plaintext = Path(path).read_bytes()
        aes_key = secrets.token_bytes(16)
        filekey = secrets.token_hex(16)
        rawsize, md5 = len(plaintext), hashlib.md5(plaintext).hexdigest()
        ciphertext = _aes128_ecb_encrypt(plaintext, aes_key)
        up = await self._api_post(EP_GET_UPLOAD_URL, {
            "filekey": filekey, "media_type": media_type, "to_user_id": peer,
            "rawsize": rawsize, "rawfilemd5": md5, "filesize": len(ciphertext),
            "no_need_thumb": True,
            # hermes 实证坑：aes_key 必须是 base64(hex字符串)，否则图片灰图
            "aeskey": aes_key.hex()}, self._token, API_TIMEOUT_MS)
        upload_url = str(up.get("upload_full_url") or "") or (
            f"{self._cdn}/upload?encrypted_query_param="
            f"{quote(str(up.get('upload_param') or ''), safe='')}&filekey={quote(filekey, safe='')}"
            if up.get("upload_param") else "")
        if not upload_url:
            raise RuntimeError(f"getUploadUrl 无上传地址: {up}")

        async def _upload():
            detail = ""
            for attempt in range(1, CDN_UPLOAD_RETRIES + 1):
                try:
                    async with self._send_session.post(
                            upload_url, data=ciphertext,
                            headers={"Content-Type": "application/octet-stream"}) as r:
                        param = r.headers.get("x-encrypted-param") if r.status == 200 else None
                        if param:
                            return param
                        detail = f"HTTP {r.status}: {(await r.text())[:200]}"
                        if r.status < 500:  # 4xx 为请求问题，重试无意义
                            break
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    detail = f"{type(exc).__name__}: {exc}"
                logger.warning("CDN 上传第 %d/%d 次失败（密文 %.1fMB）: %s",
                               attempt, CDN_UPLOAD_RETRIES, len(ciphertext) / 1048576, detail)
                if attempt < CDN_UPLOAD_RETRIES:
                    await asyncio.sleep(2.0 * attempt)
            raise RuntimeError(f"CDN 上传失败（重试 {CDN_UPLOAD_RETRIES} 次，密文 {len(ciphertext)} 字节）: {detail}")
        encrypted_param = await asyncio.wait_for(_upload(), timeout=300)

        media_field = {MEDIA_IMAGE: ("image_item", {"mid_size": len(ciphertext)}),
                       MEDIA_VIDEO: ("video_item", {"video_size": len(ciphertext),
                                                    "play_length": _video_play_length(path),
                                                    "video_md5": md5}),
                       MEDIA_FILE: ("file_item", {"file_name": Path(path).name, "len": str(rawsize)})}[media_type]
        key, extra = media_field
        item = {"type": {MEDIA_IMAGE: ITEM_IMAGE, MEDIA_VIDEO: ITEM_VIDEO,
                         MEDIA_FILE: ITEM_FILE}[media_type],
                key: {"media": {"encrypt_query_param": encrypted_param,
                                "aes_key": base64.b64encode(aes_key.hex().encode()).decode(),
                                "encrypt_type": 1}, **extra}}
        items = ([{"type": ITEM_TEXT, "text_item": {"text": caption}}] if caption else []) + [item]
        context_token = self._ctx_tokens.get(peer)
        retried = False
        while True:
            r = await self._api_post(EP_SEND_MESSAGE, {"msg": {
                "from_user_id": "", "to_user_id": peer, "client_id": f"xhs-{uuid.uuid4().hex}",
                "message_type": MSG_TYPE_BOT, "message_state": MSG_STATE_FINISH,
                "item_list": items, **({"context_token": context_token} if context_token else {})}},
                self._token, API_TIMEOUT_MS)
            ret, errcode = r.get("ret"), r.get("errcode")
            if (ret in (None, 0)) and (errcode in (None, 0)):
                return
            if _is_session_expired(r, ret, errcode) and context_token and not retried:
                retried, context_token = True, None
                continue
            raise RuntimeError(f"iLink 媒体发送错误: ret={ret} errcode={errcode} "
                               f"{r.get('errmsg') or r.get('msg')}")

    def capabilities(self) -> set:
        return {"text", "image", "video", "file"}
