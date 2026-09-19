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
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from bot.bot_weixin import WeixinTriggerAdapter, qr_login  # noqa: E402
from bot.router import Router  # noqa: E402
from pipeline.imagegen import build_router as build_gen_router  # noqa: E402
from pipeline import radar as radar_mod  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("main")


def load_config(profile: str = "default") -> dict:
    """按 profile 加载对应 config 文件。

    - profile="default" → config/config.yaml
    - profile="bot2"    → config/config.bot2.yaml
    """
    if profile == "default":
        cfg_path = BASE / "config" / "config.yaml"
    else:
        cfg_path = BASE / "config" / f"config.{profile}.yaml"
    if not cfg_path.exists():
        example = BASE / "config" / "config.example.yaml"
        hint = f"cp {example} {cfg_path}"
        raise SystemExit(f"缺少配置 [{profile}]：{cfg_path}\n创建：{hint} 后填写（详见文件内注释）")
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}


async def radar_push_loop(router: Router, adapter: WeixinTriggerAdapter, cfg: dict) -> None:
    """每日定时：跑雷达 → 推送选题清单到 home_uid；推送失败自动入补发队列（设计 3.4）。"""
    home_uid = (cfg.get("channel", {}).get("weixin", {}) or {}).get("home_uid", "")
    hour = int((cfg.get("radar") or {}).get("daily_hour", 8))
    if not home_uid:
        logger.warning("[%s] channel.weixin.home_uid 未配置，雷达推送跳过（清单仍会落盘）",
                       router.bot_id)
    while True:
        now = datetime.now()
        next_run = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())
        try:
            path = await asyncio.to_thread(
                radar_mod.run_radar,
                (cfg.get("radar") or {}).get("keywords", []),
                (cfg.get("radar") or {}).get("max_items", 20))
            if home_uid:
                text = radar_mod.format_topic_list(path, top=5)
                try:
                    await adapter.push_text(home_uid, text)  # tokenless 推送
                    logger.info("[%s] 选题清单已推送 %s", router.bot_id, home_uid)
                except Exception as exc:
                    logger.warning("[%s] 推送失败（%s），已入补发队列", router.bot_id, exc)
                    router.queue_push(home_uid, text)
        except Exception:
            logger.exception("[%s] 雷达运行失败", router.bot_id)


async def run(cfg: dict, profile: str = "default") -> None:
    gen = build_gen_router(cfg.get("imagegen") or {})
    adapter = WeixinTriggerAdapter((cfg.get("channel") or {}).get("weixin") or {},
                                   state_dir=str(BASE / "state"))
    adapter.bot_id = profile  # v1.1：注入 bot_id（多 Bot 隔离）
    router = Router(adapter, gen, cfg, base_dir=str(BASE))
    asyncio.create_task(radar_push_loop(router, adapter, cfg))
    logger.info("[%s] 启动微信适配器（生图主通道：%s）", router.bot_id, gen.primary)
    try:
        await adapter.start(router.handle)
    finally:
        await adapter.stop()


async def radar_now(cfg: dict, profile: str = "default") -> None:
    path = radar_mod.run_radar(
        (cfg.get("radar") or {}).get("keywords", []),
        (cfg.get("radar") or {}).get("max_items", 20))
    print(radar_mod.format_topic_list(path, top=5))
    print(f"\n清单已落盘：{path}")


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
