"""小红书仿写 Agent · 入口

用法（v1.1：支持 --profile 多 Bot 实例）：
  python3 main.py --qr-login                 # 首次：微信扫码登录（token 落盘 state/weixin/）
  python3 main.py --radar-now                # 立即跑一轮雷达并打印清单
  python3 main.py                           # 启动默认 Bot（加载 config/config.yaml）
  python3 main.py --profile bot2            # 启动 bot2 实例（加载 config/config.bot2.yaml）
  python3 main.py --profile bot2 --qr-login  # bot2 首次扫码

多 Bot 隔离（v1.1：方案 A 多进程独立实例）：
  - 每个 --profile 加载独立 config 文件，token/state.db/workspace 全隔离
  - bot_id = profile 名（默认 "default"），传给 adapter 与 router
  - 启动多个进程即可同时挂多个微信号，互不干扰
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from bot.bot_weixin import WeixinTriggerAdapter, qr_login  # noqa: E402
from bot.router import Router  # noqa: E402
from pipeline.imagegen import build_router as build_gen_router  # noqa: E402
from pipeline import costing  # noqa: E402
from pipeline import note_fetch as note_fetch_mod  # noqa: E402
from pipeline import radar as radar_mod  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("main")


def _load_dotenv(env_path: Path) -> None:
    """简易 .env 加载器（不引入 python-dotenv 依赖）。

    规则：
      - 只读 KEY=VALUE 行，跳过注释 / 空行
      - 不覆盖已存在的环境变量（让 shell export 优先级最高）
      - 值两端引号会被剥离
    """
    if not env_path.exists():
        return
    # utf-8-sig：兼容 PowerShell Set-Content -Encoding UTF8 写出的 BOM 头
    # （BOM 会粘在第一行 key 上导致 ARK_API_KEY 解析失败）
    for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def _apply_env(cfg: dict) -> None:
    """环境变量回填 config（仅在 config 字段为空时填入，不覆盖显式值）。

    回填映射：
      ARK_API_KEY     → imagegen.providers.ark.api_key
      REDFOX_API_KEY  → imagegen.providers.redfox_gpt.api_key / redfox_doubao.api_key / radar.redfox_api_key
      LLM_API_KEY     → llm.api_key
      HOME_UID        → channel.weixin.home_uid
    """
    ark_key = os.environ.get("ARK_API_KEY", "")
    redfox_key = os.environ.get("REDFOX_API_KEY", "")
    llm_key = os.environ.get("LLM_API_KEY", "")
    home_uid = os.environ.get("HOME_UID", "")

    providers = ((cfg.get("imagegen") or {}).get("providers") or {})
    for name, spec in providers.items():
        if not isinstance(spec, dict) or spec.get("api_key"):
            continue
        if name == "ark" and ark_key:
            spec["api_key"] = ark_key
        elif name in ("redfox_gpt", "redfox_doubao") and redfox_key:
            spec["api_key"] = redfox_key

    llm = cfg.get("llm") or {}
    if not llm.get("api_key") and llm_key:
        llm["api_key"] = llm_key

    radar = cfg.get("radar") or {}
    if not radar.get("redfox_api_key") and redfox_key:
        radar["redfox_api_key"] = redfox_key

    wx = (cfg.get("channel") or {}).get("weixin") or {}
    if not wx.get("home_uid") and home_uid:
        wx["home_uid"] = home_uid


def load_config(profile: str = "default") -> dict:
    """按 profile 加载对应 config 文件，并回填环境变量。

    优先级（高 → 低）：
      1. config.yaml 中的显式值
      2. 环境变量（含 shell export）
      3. .env 文件（由 _load_dotenv 注入到 os.environ）

    - profile="default" → config/config.yaml
    - profile="bot2"    → config/config.bot2.yaml
    """
    _load_dotenv(BASE.parent / ".env")
    if profile == "default":
        cfg_path = BASE / "config" / "config.yaml"
    else:
        cfg_path = BASE / "config" / f"config.{profile}.yaml"
    if not cfg_path.exists():
        example = BASE / "config" / "config.example.yaml"
        hint = f"cp {example} {cfg_path}"
        raise SystemExit(f"缺少配置 [{profile}]：{cfg_path}\n创建：{hint} 后填写（详见文件内注释）")
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    _apply_env(cfg)
    return cfg


async def radar_push_loop(router: Router, adapter: WeixinTriggerAdapter, cfg: dict) -> None:
    """每日定时：跑雷达 → 推送选题清单（v1.4.0 P0-5 重方案：每人各评各推）。

    链路（设计 v1.4.0 重方案）：
      ① 全局雷达（user_id=None）→ 清单落全局目录 → 推 home_uid（主理人视角）；
         同时是无 /定位 用户的 /选题 回退清单
      ② 遍历 user_profiles：每个用户按自己的关键词 + 人设独立评分，
         清单落 workspace/{bot}/users/{uid}/ → 推送该用户（人均几分钱成本）
      - 推送失败自动入补发队列（设计 3.4）；单用户失败不拖垮其他用户
      - 多用户串行跑：防红狐搜索限流，也避免清单推送互相挤占
    """
    home_uid = (cfg.get("channel", {}).get("weixin", {}) or {}).get("home_uid", "")
    hour = int((cfg.get("radar") or {}).get("daily_hour", 8))
    if not home_uid:
        logger.warning("[%s] channel.weixin.home_uid 未配置，全局清单推送跳过（清单仍会落盘）",
                       router.bot_id)
    while True:
        now = datetime.now()
        next_run = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())
        radar_cfg = cfg.get("radar") or {}
        common = dict(  # 全局/用户雷达共用的抓取参数
            max_items=radar_cfg.get("max_items", 20),
            heat_threshold=radar_cfg.get("heat_threshold"),
            min_likes=radar_cfg.get("min_likes"),
            bot_id=router.bot_id,
            llm_config=cfg.get("llm") or {},
            api_key=radar_cfg.get("redfox_api_key", ""),
            time_filter_days=radar_cfg.get("time_filter_days", 180),
        )
        # ① 全局雷达：主理人视角清单（也是无定位用户的回退）
        try:
            path = await asyncio.to_thread(
                radar_mod.run_radar, radar_cfg.get("keywords", []),
                persona=cfg.get("persona") or {}, **common)
            if home_uid:
                text = radar_mod.format_topic_list(path, top=5)
                try:
                    await adapter.push_text(home_uid, text)  # tokenless 推送
                    logger.info("[%s] 全局选题清单已推送 %s", router.bot_id, home_uid)
                except Exception as exc:
                    logger.warning("[%s] 推送失败（%s），已入补发队列", router.bot_id, exc)
                    router.queue_push(home_uid, text)
        except Exception:
            logger.exception("[%s] 全局雷达运行失败", router.bot_id)

        # ② 重方案：每用户独立评分、独立推送（关键词/人设取自 /定位）
        for profile in router.all_user_profiles():
            uid = profile["uid"]
            keywords = profile["keywords"] or radar_cfg.get("keywords", [])
            persona = ({"soul": profile["persona"]} if profile["persona"]
                        else (cfg.get("persona") or {}))
            try:
                path = await asyncio.to_thread(
                    radar_mod.run_radar, keywords,
                    persona=persona, user_id=uid, **common)
                text = radar_mod.format_topic_list(path, top=5)
            except Exception as exc:  # noqa: BLE001 —— 单用户失败不拖垮其他用户
                logger.warning("[%s] 用户雷达失败 uid=%s：%s", router.bot_id, uid, exc)
                continue
            try:
                await adapter.push_text(uid, text)
                logger.info("[%s] 用户选题清单已推送 %s", router.bot_id, uid)
            except Exception as exc:
                logger.warning("[%s] 用户清单推送失败 uid=%s（%s），已入补发队列",
                               router.bot_id, uid, exc)
                router.queue_push(uid, text)


async def metrics_collection_loop(router: Router, adapter: WeixinTriggerAdapter,
                                 cfg: dict) -> None:
    """每日定时（v1.4.0 P0-4）：回采已发布笔记的赞藏评 → 写回任务 → 增量日报推送。

    代运营核心链路：/已发布 登记链接 → 本循环每日回采（近 metrics_days 天内
    发布的笔记，老笔记数据趋稳不再回采）→ 有增量才推日报（省推送配额）；
    沉默用户也能收到（tokenless 推送，失败入补发队列）。
    """
    hour = int((cfg.get("radar") or {}).get("metrics_hour", 9))
    while True:
        now = datetime.now()
        next_run = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())
        try:
            await _collect_metrics_once(router, adapter, cfg)
        except Exception:
            logger.exception("[%s] 效果回采循环失败", router.bot_id)


async def _collect_metrics_once(router: Router, adapter: WeixinTriggerAdapter,
                                cfg: dict) -> None:
    """单轮回采：遍历已发布任务 → 红狐抓详情 → 写回 metrics → 按用户聚日报。"""
    radar_cfg = cfg.get("radar") or {}
    window_days = int(radar_cfg.get("metrics_days", 30))
    api_key = radar_cfg.get("redfox_api_key", "") or os.environ.get("REDFOX_API_KEY", "")
    rows = router.published_tasks(since=time.time() - window_days * 86400)
    if not rows:
        return
    costing.bind(f"metrics:{date.today().isoformat()}")  # 回采消耗独立范围对账
    digest: dict = {}  # uid -> [(task_id, title, old, new)]
    for task_id, uid, title, url, metrics_json in rows:
        try:
            raw = await asyncio.to_thread(
                note_fetch_mod.fetch_note_detail, "", url, api_key)
            fresh = note_fetch_mod.normalize(raw)
            new = {"likes": fresh.get("likes", 0), "collects": fresh.get("collects", 0),
                   "comments": fresh.get("comments", 0), "shares": fresh.get("shares", 0),
                   "ts": time.time()}
            old = json.loads(metrics_json) if metrics_json else {}
            router.update_task_metrics(task_id, json.dumps(new, ensure_ascii=False))
            digest.setdefault(uid, []).append((task_id, title, old, new))
        except Exception as exc:  # noqa: BLE001 —— 单条失败不拖垮整轮
            logger.warning("[%s] 回采失败 %s：%s", router.bot_id, task_id, exc)
        await asyncio.sleep(2)  # 温和限速：防红狐风控
    for uid, items in digest.items():
        text = _format_metrics_digest(items)
        if not text:
            continue
        try:
            await adapter.push_text(uid, text)
            logger.info("[%s] 数据日报已推送 %s", router.bot_id, uid)
        except Exception as exc:
            logger.warning("[%s] 数据日报推送失败 uid=%s（%s），已入补发队列",
                           router.bot_id, uid, exc)
            router.queue_push(uid, text)


def _format_metrics_digest(items: list) -> str:
    """增量日报文本；全部零增长时返回空字符串（不推，省每日推送配额）。

    首次回采（无基线）只报总量；有基线报「总量（+增量）」。
    """
    lines = [f"📊 每日数据回采 · {date.today().isoformat()}", ""]
    shown = 0
    for task_id, title, old, new in items:
        title = (title or "")[:20]
        if old:
            d = {k: new[k] - (old.get(k) or 0) for k in ("likes", "collects", "comments")}
            if all(v <= 0 for v in d.values()):
                continue  # 零增长的老笔记不刷屏
            sign = lambda v: f"+{v}" if v > 0 else str(v)  # noqa: E731
            lines.append(
                f"· {task_id}「{title}」\n"
                f"  👍 {new['likes']}（{sign(d['likes'])}） · "
                f"⭐ {new['collects']}（{sign(d['collects'])}） · "
                f"💬 {new['comments']}（{sign(d['comments'])}）")
        else:
            lines.append(
                f"· {task_id}「{title}」\n"
                f"  👍 {new['likes']} · ⭐ {new['collects']} · 💬 {new['comments']}")
        shown += 1
    if not shown:
        return ""
    lines += ["", "💡 明细随时可查：/效果"]
    return "\n".join(lines)


async def run(cfg: dict, profile: str = "default") -> None:
    gen = build_gen_router(cfg.get("imagegen") or {})
    adapter = WeixinTriggerAdapter((cfg.get("channel") or {}).get("weixin") or {},
                                   state_dir=str(BASE / "state"))
    adapter.bot_id = profile  # v1.1：注入 bot_id（多 Bot 隔离）
    router = Router(adapter, gen, cfg, base_dir=str(BASE))
    asyncio.create_task(radar_push_loop(router, adapter, cfg))
    asyncio.create_task(metrics_collection_loop(router, adapter, cfg))  # v1.4.0（P0-4）：每日效果回采
    logger.info("[%s] 启动微信适配器（生图主通道：%s）", router.bot_id, gen.primary)
    try:
        await adapter.start(router.handle)
    finally:
        await adapter.stop()


async def radar_now(cfg: dict, profile: str = "default") -> None:
    path = radar_mod.run_radar(
        (cfg.get("radar") or {}).get("keywords", []),
        (cfg.get("radar") or {}).get("max_items", 20),
        (cfg.get("radar") or {}).get("heat_threshold"),
        (cfg.get("radar") or {}).get("min_likes"),
        profile,
        cfg.get("llm") or {},         # v1.3：语义四维评分（未配置自动降级数值排序）
        cfg.get("persona") or {})
    print(radar_mod.format_topic_list(path, top=5))
    print(f"\n清单已落盘：{path}")
    # 跑完立即推送到 home_uid（与 bot 内每日推送行为一致；bot 不在线也能推，tokenless）
    home_uid = ((cfg.get("channel") or {}).get("weixin") or {}).get("home_uid", "")
    if home_uid:
        adapter = None
        try:
            adapter = WeixinTriggerAdapter(((cfg.get("channel") or {}).get("weixin") or {}),
                                           state_dir=str(BASE / "state"))
            adapter.bot_id = profile
            await adapter.open_sender()  # 只初始化发送通道，不启动收消息轮询
            await adapter.push_text(home_uid, radar_mod.format_topic_list(path, top=5))
            print(f"已推送到 {home_uid}")
        except Exception as exc:
            print(f"推送失败（{exc}）——bot 在线时会经补发队列重试；也可在微信发 /选题 手动拉取")
        finally:
            if adapter:
                await adapter.close_sender()
    else:
        print("未配置 home_uid，跳过推送（微信里发 /选题 可手动拉取）")


def main() -> None:
    parser = argparse.ArgumentParser(description="小红书仿写 Agent")
    parser.add_argument("--qr-login", action="store_true", help="微信扫码登录（首次）")
    parser.add_argument("--radar-now", action="store_true", help="立即跑一轮雷达并打印清单")
    parser.add_argument("--profile", default="default",
                        help="Bot 实例名（加载 config/config.{profile}.yaml；default=config.yaml）")
    args = parser.parse_args()
    cfg = load_config(args.profile)
    if args.qr_login:
        # 扫码登录按 profile 隔离 state 目录
        state_dir = str(BASE / "state") if args.profile == "default" else str(BASE / f"state.{args.profile}")
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        asyncio.run(qr_login(state_dir))
    elif args.radar_now:
        asyncio.run(radar_now(cfg, args.profile))
    else:
        try:
            asyncio.run(run(cfg, args.profile))
        except KeyboardInterrupt:
            print("\n已退出")


if __name__ == "__main__":
    main()
