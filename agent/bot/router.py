"""Router：指令解析 · 确认卡点 · 多用户会话 · 任务队列 · 推送补发

设计文档 v1.1 §3.5（指令集）、§3.6（多用户隔离）、§3.4（推送降级与补发）。
指令解析：正则优先；LLM 意图兜底属 Phase 1.5（TODO）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

from bot.base import Intent, TriggerAdapter
from bot.deliver import deliver_note, deliver_video
from bot import quality as quality_mod
from pipeline import rewrite as rewrite_mod
from pipeline.imagegen import ImageGenRouter
from pipeline import promptkit
from pipeline import radar as radar_mod
from pipeline import imagepack
from pipeline import video as video_mod
from pipeline import note_fetch as note_fetch_mod  # v1.1：拉模式 - 用户粘贴链接抓取
from pipeline import note_analyze as note_analyze_mod  # v1.2：爆款显式拆解

logger = logging.getLogger("bot.router")

# 用户友好通道名 → provider 注册名
GEN_ALIAS = {"ark": "ark", "火山": "ark", "gpt": "redfox_gpt", "红狐gpt": "redfox_gpt",
             "doubao": "redfox_doubao", "红狐豆包": "redfox_doubao"}

# 内容方向（语义评分 content_direction）→ 中文标签（SOUL.md 五大方向，注入仿写提示词用）
_DIR_LABELS = {"itinerary": "行程规划", "knowledge": "旅行知识", "life": "人生与旅行",
               "gear": "旅行好物", "other": "机动方向"}

HELP_TEXT = """【小红书仿写助手 · 指令】
/选题 —— 查看当日选题清单（Top 5，附链接与仿写角度）
/选题 抓取 —— 按我的关键词立即抓取评分（配合 /定位）
/选题 列表 —— 查看历史清单日期；/选题 MM-DD 查指定日期
/确认 N 或 仿写第N条 —— 按选题生成图文笔记（含发布文案）
/换角度 N 描述 —— 调整第 N 条选题的仿写角度
/视频 —— 查看可补生成视频的任务；/视频 任务ID —— 生成视频笔记
/仿写 内容 —— 直发主题或爆款笔记文字（首行=标题，其余=正文）
粘贴小红书笔记链接 —— 抓取该笔记并仿写（图文/视频笔记均支持）
/定位 —— 查看/设置我的赛道·人设·关键词（新用户从这里开始）
/状态 —— 查询生产进度
/生图通道 ark|gpt|doubao —— 切换生图通道（ark=火山Seedream，gpt=红狐GPT-Image-2，doubao=红狐豆包）
/生图 描述文字 —— 直接生一张竖图（通道实测用）
/help —— 本帮助"""

# /定位 用户模板（场景1：新用户输入赛道/人设/服务钩子/关键词）
PERSONA_TEMPLATE = """【/定位 模板】复制以下四行、填好后整体发送（至少填「人设」或「关键词」一项）：
/定位
赛道：亲子游行程规划
人设：两个娃的爸爸，专写带娃行程怎么排
服务钩子：评论区报人数/天数/预算，帮你出定制行程
关键词：带娃游, 亲子旅行, 避寒, 行程规划
· 赛道/人设/服务钩子 → 仿写与选题评分按你的定位走
· 关键词 → /选题 抓取 按你的关键词搜索爆款
· 重置：发送 /定位 重置"""

TASK_STAGES = ["queued", "rewriting", "imaging", "delivered", "failed"]


class Router:
    def __init__(self, adapter: TriggerAdapter, gen: ImageGenRouter, config: dict, base_dir: str):
        self.adapter = adapter
        self.gen = gen
        self.config = config or {}
        self.base = Path(base_dir)
        # v1.1：多 Bot 隔离——bot_id 从 adapter.bot_id 取（单 Bot 默认 "default"）
        # 数据库 / 产物路径均按 bot_id 分层，为 P2 多进程多 Bot 铺路
        self.bot_id = getattr(adapter, "bot_id", "default")
        self.workspace = self.base / "workspace" / self.bot_id
        self.workspace.mkdir(parents=True, exist_ok=True)
        # 排版/TTS/视频的共享资产（字体/BGM）：
        # config pipeline.assets_dir > agent/assets（v1.3 P2-3 收编）> poc 资产兜底
        _assets_candidates = [
            (self.config.get("pipeline") or {}).get("assets_dir"),
            str(self.base / "assets"),
            str(self.base.parent / "poc" / "production" / "assets"),
        ]
        self.assets_dir = next((c for c in _assets_candidates if c and Path(c).exists()),
                               _assets_candidates[-1])
        self.video_max_mb = float((self.config.get("deliver") or {}).get("video_max_mb", 25))
        # 场景2：未带指令直发长文本 → 暂存待「仿写」确认（避免误触发生产线）
        self._pending_direct: dict = {}
        # SQLite 按 bot_id 分文件，物理隔离
        db_path = self.base / f"state.{self.bot_id}.db"
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute("""CREATE TABLE IF NOT EXISTS tasks(
            id TEXT PRIMARY KEY, uid TEXT, note_id TEXT, title TEXT,
            status TEXT, detail TEXT, created REAL, updated REAL)""")
        self._db.execute("""CREATE TABLE IF NOT EXISTS pending_push(
            id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, text TEXT, created REAL)""")
        # 场景1：用户定位画像（/定位 存，仿写人设与选题关键词按此走）
        self._db.execute("""CREATE TABLE IF NOT EXISTS user_profiles(
            uid TEXT PRIMARY KEY, persona TEXT, keywords TEXT, updated REAL)""")
        self._db.commit()

    # ─── 主入口（Adapter 回调） ─────────────────────────────────────────

    async def handle(self, intent: Intent) -> None:
        try:
            await self._flush_pending(intent.user_id)
            await self._dispatch(intent)
        except Exception:
            logger.exception("处理消息失败 uid=%s", intent.user_id)
            await self._safe_send(intent.user_id, "处理出错，请稍后重试或 /help 查看用法。")

    async def _dispatch(self, intent: Intent) -> None:
        uid, text = intent.user_id, intent.text.strip()
        if not text:
            return
        if m := re.match(r"^/?选题\s*(.*)$|^今天有什么选题|^/?雷达$", text):
            await self._cmd_topics(uid, (m.group(1) or "").strip())
        elif m := re.match(r"^/?确认\s*(\d+)$|^仿写第\s*(\d+)\s*条", text):
            n = int(m.group(1) or m.group(2))
            await self._cmd_confirm(uid, n)
        elif re.match(r"^/?状态$", text):
            await self._cmd_status(uid)
        elif m := re.match(r"^/?换角度\s*(\d+)\s+(.+)", text, re.S):
            await self._cmd_angle(uid, int(m.group(1)), m.group(2).strip())
        elif m := re.match(r"^/?定位\s*(.*)$", text, re.S):
            await self._cmd_persona(uid, (m.group(1) or "").strip())
        elif m := re.match(r"^/?仿写\s+(.+)", text, re.S):
            await self._cmd_direct_rewrite(uid, m.group(1).strip())
        elif m := re.match(r"^/?视频(?:\s+(\S+))?\s*$", text):
            await self._cmd_video(uid, (m.group(1) or "").strip())
        elif m := re.match(r"^/?生图通道\s*(\S+)", text):
            await self._cmd_switch_gen(uid, m.group(1))
        elif m := re.match(r"^/?生图\s+(.+)", text, re.S):
            await self._cmd_gen(uid, m.group(1).strip())
        elif re.search(r"xhslink\.com|xiaohongshu\.com", text):
            await self._cmd_manual_note(uid, text)
        elif text == "仿写":
            # 场景2：用户直发长文本后回复「仿写」确认 → 用暂存内容直接生产
            pending = self._pending_direct.pop(uid, None)
            if pending:
                await self._cmd_direct_rewrite(uid, pending)
            else:
                await self._safe_send(
                    uid, "用法：/仿写 首行标题，换行后写正文或爆款笔记内容")
        elif re.match(r"^/?help$|^帮助$|^你好$|^hi$", text.lower()):
            await self._safe_send(uid, HELP_TEXT)
        else:
            if len(text) >= 30:
                # 场景2：直发主题/爆款内容识别——长文本暂存，回「仿写」即生成
                self._pending_direct[uid] = text
                await self._safe_send(
                    uid, "看起来是一段笔记内容 📝\n"
                         "回复「仿写」直接按它生成；或发送 /仿写 + 内容；指令见 /help")
            else:
                await self._safe_send(uid, "暂时没听懂。试试这些指令：\n" + HELP_TEXT)

    # ─── 指令实现 ───────────────────────────────────────────────────────

    async def _cmd_topics(self, uid: str, arg: str = "") -> None:
        """选题清单：无参=当日；「抓取」=按我的关键词立即抓取；「列表」=历史日期；MM-DD=指定日期。"""
        if arg in ("抓取", "刷新"):
            asyncio.create_task(self._run_radar_now(uid))
            await self._safe_send(
                uid, "雷达抓取中（关键词搜索 + 数值评分 + 语义四维评分，约 1-3 分钟）…完成后自动推送")
            return
        if arg in ("列表", "历史", "list"):
            dates = radar_mod.list_topic_dates(self.workspace)
            if not dates:
                await self._safe_send(uid, "暂无任何选题清单（含历史）。可先跑一次雷达生成。")
                return
            lines = ["📚 历史选题清单：", ""]
            lines += [f"· {d}　→ /选题 {d[5:]}" for d in reversed(dates)]
            lines += ["", "⏰ 超过 3 天的选题热度窗口可能已过，仿写前留意时效"]
            await self._safe_send(uid, "\n".join(lines))
            return
        if arg:
            path = radar_mod.topic_list_by_date(self.workspace, arg)
            if not path:
                await self._safe_send(
                    uid, f"没有 {arg} 的选题清单。/选题 列表 查看所有日期。")
                return
        else:
            path = radar_mod.latest_topic_list(self.workspace)
            if not path:
                await self._safe_send(
                    uid, "还没有当日选题清单。管理员可运行：python3 agent/main.py --radar-now（立即抓取并推送）")
                return
        await self._safe_send(uid, radar_mod.format_topic_list(path, top=5))

    async def _cmd_confirm(self, uid: str, n: int) -> None:
        path = radar_mod.latest_topic_list(self.workspace)
        if not path:
            await self._safe_send(uid, "当日无选题清单，先 /选题 查看或等待雷达推送。")
            return
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        topics = data.get("topic_list") or data.get("passed") or []
        if n < 1 or n > len(topics):
            await self._safe_send(uid, f"序号超范围（1-{len(topics)}），/选题 重新查看。")
            return
        topic = topics[n - 1]
        task_id = uuid.uuid4().hex[:8]
        self._db.execute(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)",
            (task_id, uid, topic.get("note_id", ""), topic.get("title", ""),
             "queued", json.dumps(topic, ensure_ascii=False), time.time(), time.time()))
        self._db.commit()
        await self._safe_send(uid, f"已确认选题 #{n}：{topic.get('title', '')[:30]}\n"
                                   f"任务 {task_id} 已入队，/状态 查看进度。")
        asyncio.create_task(self._run_task(uid, task_id))

    async def _cmd_status(self, uid: str) -> None:
        rows = self._db.execute(
            "SELECT id, title, status FROM tasks WHERE uid=? ORDER BY created DESC LIMIT 5",
            (uid,)).fetchall()
        if not rows:
            await self._safe_send(uid, "暂无任务。/选题 选一条，或直接粘贴笔记链接。")
            return
        lines = [f"{r[0]} · {r[2]:<9} · {(r[1] or '')[:24]}" for r in rows]
        await self._safe_send(uid, "最近任务（ID · 状态 · 标题）：\n" + "\n".join(lines))

    async def _cmd_switch_gen(self, uid: str, name: str) -> None:
        provider = GEN_ALIAS.get(name.lower()) or GEN_ALIAS.get(name)
        if not provider:
            await self._safe_send(uid, f"未知通道 {name}。可选：ark / gpt / doubao")
            return
        self.gen.set_primary(provider)
        await self._safe_send(uid, f"生图主通道已切换为 {provider}（当前顺序：{' → '.join(self.gen._order)}）")

    async def _cmd_gen(self, uid: str, prompt: str) -> None:
        out_dir = self.workspace / "users" / uid.replace(":", "_")
        out_dir.mkdir(parents=True, exist_ok=True)  # v1.1：workspace 已含 bot_id 分层
        out = str(out_dir / f"{int(time.time())}.jpg")
        await self._safe_send(uid, f"生图中（通道 {self.gen.primary}）…")
        try:
            path, used = await asyncio.to_thread(
                self.gen.generate, prompt, out, "portrait", None)
            await self.adapter.send_image(uid, path)
            await self._safe_send(uid, f"完成（通道 {used}）。")
        except Exception as exc:
            await self._safe_send(uid, f"生图失败：{exc}")

    async def _cmd_manual_note(self, uid: str, text: str) -> None:
        """v1.1 拉模式：用户粘贴小红书链接 → 红狐详情抓取 → 入队 → 触发 _run_task

        支持图文笔记和视频笔记：
          - 图文笔记：直接抓详情进流水线
          - 视频笔记：抓详情 + 提取口播文案 → 进流水线（仿写产出对应图文卡 + 视频）
        """
        await self._safe_send(uid, "收到链接，正在抓取笔记详情（红狐）…")
        try:
            api_key = (self.config.get("radar") or {}).get("redfox_api_key", "") or os.environ.get("REDFOX_API_KEY", "")
            topic = await asyncio.to_thread(note_fetch_mod.fetch_topic_from_user_text, text, api_key)
        except Exception as exc:
            await self._safe_send(uid, f"笔记抓取失败：{exc}\n请检查链接是否完整或稍后重试。")
            return
        note_type = topic.get("type", "image")
        task_id = uuid.uuid4().hex[:8]
        self._db.execute(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)",
            (task_id, uid, topic.get("note_id", ""),
             topic.get("title", "")[:60], "queued",
             json.dumps(topic, ensure_ascii=False),
             time.time(), time.time()))
        self._db.commit()
        type_label = "视频笔记（已提取口播文案）" if note_type == "video" else "图文笔记"
        await self._safe_send(
            uid, f"已抓取【{type_label}】：{topic.get('title', '')[:30]}\n"
                 f"点赞 {topic.get('likes', 0)} · 收藏 {topic.get('collects', 0)} · 评论 {topic.get('comments', 0)}\n"
                 f"任务 {task_id} 已入队，开始仿写+生图+视频合成…\n"
                 f"/状态 查看进度。")
        asyncio.create_task(self._run_task(uid, task_id))

    # ─── 场景1：/定位（用户赛道/人设/关键词输入） ────────────────────────

    def _get_user_profile(self, uid: str) -> Optional[dict]:
        row = self._db.execute(
            "SELECT persona, keywords FROM user_profiles WHERE uid=?", (uid,)).fetchone()
        if not row:
            return None
        keywords = [k for k in re.split(r"[，,、\s]+", row[1] or "") if k]
        return {"persona": (row[0] or "").strip(), "keywords": keywords}

    def _persona_for(self, uid: str) -> dict:
        """仿写/评分人设：用户 /定位 人设 > config persona（soul 留空回落 SOUL.md）。"""
        profile = self._get_user_profile(uid)
        if profile and profile["persona"]:
            return {"soul": profile["persona"]}
        return self.config.get("persona") or {}

    async def _cmd_persona(self, uid: str, body: str) -> None:
        """场景1：查看/设置/重置用户定位（赛道/人设/服务钩子/关键词）。"""
        if not body:
            profile = self._get_user_profile(uid)
            if profile:
                cur = (f"人设：{profile['persona'] or '（未填）'}\n"
                       f"关键词：{' / '.join(profile['keywords']) or '（未填）'}")
            else:
                cur = "（未设置——当前按系统默认 SOUL.md 定位）"
            await self._safe_send(uid, f"📋 我的当前定位：\n{cur}\n\n{PERSONA_TEMPLATE}")
            return
        if body in ("重置", "reset"):
            self._db.execute("DELETE FROM user_profiles WHERE uid=?", (uid,))
            self._db.commit()
            await self._safe_send(uid, "已重置为系统默认定位（SOUL.md）。")
            return
        fields: dict = {}
        for ln in body.splitlines():
            m = re.match(r"^(赛道|人设|服务钩子|关键词)\s*[:：]\s*(.+)$", ln.strip())
            if m:
                fields[m.group(1)] = m.group(2).strip()
        if not fields:
            await self._safe_send(uid, f"未识别到模板字段（需 赛道/人设/服务钩子/关键词 行）。\n\n{PERSONA_TEMPLATE}")
            return
        persona = "。".join(f"{k}：{v}" for k, v in fields.items() if k != "关键词")
        keywords = [k for k in re.split(r"[，,、\s]+", fields.get("关键词", "")) if k]
        if not persona and not keywords:
            await self._safe_send(uid, "至少填写「人设」或「关键词」一项。\n\n" + PERSONA_TEMPLATE)
            return
        self._db.execute(
            "INSERT INTO user_profiles(uid, persona, keywords, updated) VALUES(?,?,?,?) "
            "ON CONFLICT(uid) DO UPDATE SET persona=excluded.persona, "
            "keywords=excluded.keywords, updated=excluded.updated",
            (uid, persona, " ".join(keywords), time.time()))
        self._db.commit()
        await self._safe_send(
            uid, "✅ 定位已保存\n"
                 + (f"人设：{persona}\n" if persona else "人设：（未填，按系统默认）\n")
                 + (f"关键词：{' / '.join(keywords)}\n" if keywords else "关键词：（未填，按系统默认）\n")
                 + "接下来：/选题 抓取 按你的关键词搜索；/确认 N 或 /仿写 内容 按你的人设仿写")

    async def _run_radar_now(self, uid: str) -> None:
        """按用户关键词（未设置则 config 默认）立即跑一轮雷达并推送清单。"""
        profile = self._get_user_profile(uid) or {}
        keywords = profile.get("keywords") or (self.config.get("radar") or {}).get("keywords", [])
        try:
            path = await asyncio.to_thread(
                radar_mod.run_radar, keywords,
                (self.config.get("radar") or {}).get("max_items", 20),
                (self.config.get("radar") or {}).get("heat_threshold"),
                (self.config.get("radar") or {}).get("min_likes"),
                self.bot_id, self.config.get("llm") or {}, self._persona_for(uid),
                (self.config.get("radar") or {}).get("redfox_api_key", ""))
            text = radar_mod.format_topic_list(path, top=5)
        except Exception as exc:  # noqa: BLE001 —— 推送失败原因给用户
            logger.warning("按需雷达失败 uid=%s: %s", uid, exc)
            text = f"雷达抓取失败：{exc}"
        try:
            await self.adapter.push_text(uid, text)  # tokenless 推送
        except Exception:
            self.queue_push(uid, text)

    # ─── 场景2a：/仿写（直发主题或爆款笔记内容） ────────────────────────

    async def _cmd_direct_rewrite(self, uid: str, text: str) -> None:
        """直发内容仿写：首行=标题，其余=正文/爆款内容；含链接则转拉模式详情抓取。"""
        if re.search(r"xhslink\.com|xiaohongshu\.com", text):
            await self._cmd_manual_note(uid, text)
            return
        lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
        if not lines:
            await self._safe_send(uid, "内容为空。用法：/仿写 首行标题，换行后写正文或爆款笔记内容")
            return
        title = lines[0][:40]
        description = "\n".join(lines[1:]) if len(lines) > 1 else lines[0]
        topic = {"note_id": f"manual-{int(time.time())}", "url": "", "type": "manual",
                 "title": title, "description": description,
                 "likes": 0, "collects": 0, "comments": 0, "images": [],
                 "data_source": "user_direct"}
        task_id = uuid.uuid4().hex[:8]
        self._db.execute(
            "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)",
            (task_id, uid, topic["note_id"], title[:60], "queued",
             json.dumps(topic, ensure_ascii=False), time.time(), time.time()))
        self._db.commit()
        await self._safe_send(
            uid, f"已收到内容（标题：{title[:24]}）\n"
                 f"任务 {task_id} 已入队：拆解 → 仿写 → 图文卡片 + 发布文案\n"
                 f"视频笔记在图文交付后按需生成（/视频 {task_id}）。/状态 查看进度。")
        asyncio.create_task(self._run_task(uid, task_id))

    # ─── 场景2b：/视频（图文先行，视频按需生成） ─────────────────────────

    async def _cmd_video(self, uid: str, arg: str) -> None:
        """无参=列出可补生成视频的已交付任务；带任务ID=补生成该任务的视频笔记。"""
        if not arg:
            rows = self._db.execute(
                "SELECT id, title FROM tasks WHERE uid=? AND status='delivered' "
                "AND (detail IS NULL OR detail='' OR detail NOT LIKE 'video_done%') "
                "ORDER BY updated DESC LIMIT 5", (uid,)).fetchall()
            if not rows:
                await self._safe_send(uid, "暂无可生成视频的任务（先 /确认 N 或 /仿写 内容 生成图文笔记）")
                return
            lines = ["🎬 可生成视频笔记的任务（发送 /视频 任务ID）：", ""]
            lines += [f"· {r[0]}　{(r[1] or '')[:24]}" for r in rows]
            await self._safe_send(uid, "\n".join(lines))
            return
        row = self._db.execute("SELECT uid, status, updated FROM tasks WHERE id=?", (arg,)).fetchone()
        if not row or row[0] != uid:
            await self._safe_send(uid, f"任务 {arg} 不存在（/视频 查看可生成列表）")
            return
        if row[1] != "delivered":
            # composing 超 15 分钟视为上次合成/上传中断（进程崩溃、CDN 故障等），允许重试
            stale = row[1] == "composing" and time.time() - float(row[2] or 0) > 900
            if not stale:
                await self._safe_send(uid, f"任务 {arg} 状态为 {row[1]}，需图文交付完成（delivered）后才能生成视频")
                return
        work_dir = self.workspace / "users" / uid.replace(":", "_") / arg
        layout_path = work_dir / "layout.json"
        if not layout_path.exists():
            await self._safe_send(uid, f"任务 {arg} 缺 layout.json（旧版任务不支持补生成视频）")
            return
        try:
            layout = json.loads(layout_path.read_text(encoding="utf-8"))
        except Exception as exc:
            await self._safe_send(uid, f"layout.json 读取失败：{exc}")
            return
        frames = sorted(str(p) for p in (work_dir / "images").glob("*.jpg"))
        if len(frames) < 3:
            await self._safe_send(uid, f"任务 {arg} 缺成品卡片图，无法生成视频")
            return
        await self._safe_send(uid, f"视频合成中（{len(frames)} 镜头，TTS + Ken Burns + BGM，约 3-5 分钟）…")
        self._db.execute("UPDATE tasks SET status='composing', updated=? WHERE id=?",
                         (time.time(), arg))
        self._db.commit()
        video_path = work_dir / "video.mp4"
        video = None
        if video_path.exists():  # 已通过自检的成片直接重传（上传失败重试场景）
            try:
                video_mod._verify_compat(str(video_path))
                video = str(video_path)
            except Exception:
                video = None
        if video is None:
            try:
                video = await asyncio.to_thread(
                    video_mod.make_video, frames, layout.get("narrations") or [],
                    str(video_path), self.assets_dir)
            except Exception as exc:
                video_path.unlink(missing_ok=True)  # 清理残片，维持"存在=通过自检"不变式
                self._db.execute("UPDATE tasks SET status='delivered', updated=? WHERE id=?",
                                 (time.time(), arg))
                self._db.commit()
                await self._safe_send(uid, f"视频合成失败：{exc}\n（可稍后重发 /视频 {arg}）")
                return
        try:
            await deliver_video(self.adapter, uid, video, self.video_max_mb)
        except Exception as exc:
            self._db.execute("UPDATE tasks SET status='delivered', updated=? WHERE id=?",
                             (time.time(), arg))
            self._db.commit()
            await self._safe_send(uid, f"视频上传/发送失败：{exc}\n"
                                       f"（成片已保留，稍后重发 /视频 {arg} 直接重传，无需重新合成）")
            return
        vinfo = quality_mod.probe_video(video)
        self._db.execute("UPDATE tasks SET status='delivered', detail='video_done', updated=? WHERE id=?",
                         (time.time(), arg))
        self._db.commit()
        try:  # 质量报告更新（#4 视频规格），失败不影响交付
            result = json.loads((work_dir / "rewrite.json").read_text(encoding="utf-8"))
            report = quality_mod.render_quality_report(result, layout, vinfo, self.video_max_mb)
            (work_dir / "quality_report.txt").write_text(report, encoding="utf-8")
            await self._safe_send(uid, report)
        except Exception as exc:
            logger.warning("质量报告更新失败（不影响视频交付）: %s", exc)
        vline, _ok = quality_mod.check_video_spec(vinfo, self.video_max_mb)
        await self._safe_send(uid, f"视频已交付 🎬 规格：{vline}")

    # ─── P1-4：/换角度（更新选题的仿写角度，/确认 时注入仿写提示词） ──────

    async def _cmd_angle(self, uid: str, n: int, desc: str) -> None:
        path = radar_mod.latest_topic_list(self.workspace)
        if not path:
            await self._safe_send(uid, "当日无选题清单，先 /选题 抓取 或等待雷达推送。")
            return
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        topics = data.get("topic_list") or []
        if n < 1 or n > len(topics):
            await self._safe_send(uid, f"序号超范围（1-{len(topics)}），/选题 重新查看。")
            return
        topics[n - 1]["rewrite_angle"] = desc
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        await self._safe_send(
            uid, f"已更新第 {n} 条仿写角度：{desc[:60]}\n/确认 {n} 将按此角度仿写。")

    # ─── 任务执行（rewrite → 底图 → 交付） ──────────────────────────────

    def _enrich_topic_images(self, topic: dict) -> dict:
        """方案A（2026-09-22）：雷达确认/直发选题无图集时，经红狐详情接口补图集。

        拉模式（粘贴链接）topic 已含详情图集，幂等跳过；manual 任务无 note_id 跳过。
        失败降级现状（封面/文字拆解），不阻断主线。
        """
        nid = str(topic.get("note_id") or "").strip()
        if not nid or nid.startswith("manual-") or topic.get("images"):
            return topic
        api_key = ((self.config.get("radar") or {}).get("redfox_api_key", "")
                   or os.environ.get("REDFOX_API_KEY", ""))
        if not api_key:
            return topic
        try:
            detail = note_fetch_mod.normalize(
                note_fetch_mod.fetch_note_detail(work_id=nid, api_key=api_key))
            if detail.get("images"):
                topic = {**topic,
                         "images": detail["images"],
                         "cover_image": detail.get("cover_image") or topic.get("cover_image", ""),
                         "description": detail.get("description") or topic.get("description", ""),
                         "data_source": f"{topic.get('data_source', '')}+detail"}
                logger.info("选题 %s 详情补图 %d 张", nid, len(detail["images"]))
            else:
                logger.info("选题 %s 详情无图集（视频/单图型），维持封面拆解", nid)
        except Exception as exc:
            logger.warning("详情补图失败（降级封面/文字拆解）note_id=%s: %s", nid, exc)
        return topic

    async def _run_task(self, uid: str, task_id: str) -> None:
        def _update(status: str, detail: str = ""):
            self._db.execute("UPDATE tasks SET status=?, detail=?, updated=? WHERE id=?",
                             (status, detail, time.time(), task_id))
            self._db.commit()

        row = self._db.execute("SELECT detail FROM tasks WHERE id=?", (task_id,)).fetchone()
        topic = json.loads(row[0]) if row and row[0] else {}
        # 方案A：无图集选题（雷达确认/直发）先补详情图集，拆解输入与拉模式同构
        topic = await asyncio.to_thread(self._enrich_topic_images, topic)
        work_dir = self.workspace / "users" / uid.replace(":", "_") / task_id
        work_dir.mkdir(parents=True, exist_ok=True)  # v1.1：workspace 已含 bot_id 分层
        try:
            # ⓪ 拆解对标（v1.2：显式拆解爆款结构——文案骨架/图卡结构/风格；
            #    失败降级为无拆解仿写，不阻断主线）
            analysis = None
            try:
                analysis = await asyncio.to_thread(
                    note_analyze_mod.run_analyze, topic, str(work_dir),
                    self.config.get("llm") or {})
                (work_dir / "analysis.json").write_text(
                    json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:
                logger.warning("拆解失败，降级为无拆解仿写: %s", exc)
            # ① 拆解仿写（v1.3：人设按用户 /定位 优先；注入=内容方向（钩子/视角按
            #    五大方向适配，见 SOUL 钩子表）+ 语义评分/换角度产出的角度与钩子；
            #    schema 校验失败自动重试一次）
            _update("rewriting")
            llm = rewrite_mod.llm_call_factory(self.config.get("llm") or {})
            persona = self._persona_for(uid)
            angle_parts = []
            dir_label = _DIR_LABELS.get(str(topic.get("content_direction") or ""))
            if dir_label:
                angle_parts.append(f"内容方向：{dir_label}（视角与结尾钩子按此方向适配，见人设钩子策略表）")
            angle_parts += [str(x) for x in
                            (topic.get("rewrite_angle"), topic.get("persona_hook")) if x]
            angle = " ".join(angle_parts) or None
            result = await asyncio.to_thread(
                rewrite_mod.run_rewrite, llm, topic, persona, analysis, angle)
            sim = result.get("similarity", 1.0)
            if sim > 0.30:  # 原创度门槛（POC 定稿 0.30）
                raise RuntimeError(f"原创度自检未通过（相似度 {sim:.2f} > 0.30），需人工复写")
            (work_dir / "rewrite.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            # ② 版式编排（拆解驱动卡片 DSL：第 N 张卡对标爆款第 N 张图卡；
            #    末卡 CTA 兜底值经 load_cta 单一来源注入）+ 图文卡片
            _update("imaging")
            layout = await asyncio.to_thread(
                imagepack.plan_layout, llm, result, analysis,
                promptkit.load_cta(self.config.get("persona") or {}))
            (work_dir / "layout.json").write_text(
                json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
            pack = await asyncio.to_thread(
                imagepack.generate_pack, layout, self.gen, str(work_dir / "images"), self.assets_dir)
            # ③ 交付：文案 + N 张图文卡
            await deliver_note(
                self.adapter, uid, result.get("title", ""), result.get("content", ""),
                pack["cards"], video_path=None)
            # ③b 小红书发布文案（LLM 按手机阅读习惯排版，可直接复制发布）
            try:
                xhs_copy = await asyncio.to_thread(rewrite_mod.format_xhs_copy, llm, result)
                (work_dir / "xhs_copy.txt").write_text(xhs_copy, encoding="utf-8")
                await self._safe_send(uid, "📝 发布文案（可直接复制发小红书）：\n\n" + xhs_copy)
            except Exception as exc:
                logger.warning("发布文案生成失败（不影响交付）: %s", exc)
            # ③c 质量报告（P1-3：CHECKLIST 9 项程序载体，自动项判定 + 人工项待勾）
            report = quality_mod.render_quality_report(result, layout, None, self.video_max_mb)
            (work_dir / "quality_report.txt").write_text(report, encoding="utf-8")
            await self._safe_send(uid, report)
            # ④ 图文先行交付（场景2b：不自动合成视频；用户按需 /视频 任务ID 补生成）
            _update("delivered")
            await self._safe_send(
                uid, f"图文交付完成 ✅ 相似度 {sim:.2f}｜任务目录 {work_dir}\n"
                     f"🎬 如需视频笔记：发送 /视频 {task_id}（或 /视频 查看可生成列表）")
        except Exception as exc:
            _update("failed", str(exc))
            logger.exception("任务 %s 失败", task_id)
            await self._safe_send(uid, f"任务 {task_id} 失败：{exc}")

    # ─── 推送补发（设计 3.4：tokenless 失败 → 落盘 → 下次对话补发） ────

    def queue_push(self, uid: str, text: str) -> None:
        self._db.execute("INSERT INTO pending_push(uid, text, created) VALUES (?,?,?)",
                         (uid, text, time.time()))
        self._db.commit()
        logger.info("推送失败已入补发队列 uid=%s", uid)

    async def _flush_pending(self, uid: str) -> None:
        rows = self._db.execute(
            "SELECT id, text FROM pending_push WHERE uid=? ORDER BY id", (uid,)).fetchall()
        for pid, text in rows:
            try:
                await self.adapter.send_text(uid, f"[补发] {text}")
                self._db.execute("DELETE FROM pending_push WHERE id=?", (pid,))
                self._db.commit()
            except Exception:
                break  # 仍失败，留队列下次再试

    async def _safe_send(self, uid: str, text: str) -> None:
        try:
            await self.adapter.send_text(uid, text)
        except Exception:
            logger.exception("回复失败 uid=%s", uid)
