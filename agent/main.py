"""小红书仿写 Agent · 入口

用法：
  python3 main.py --qr-login     # 首次：微信扫码登录（token 落盘 state/weixin/）
  python3 main.py --radar-now    # 立即跑一轮雷达并推送选题清单（验证 V6 可用此代替）
  python3 main.py                # 启动 bot（长轮询 + 每日雷达定时推送）
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


def load_config() -> dict:
    cfg_path = BASE / "config" / "config.yaml"
    if not cfg_path.exists():
        example = BASE / "config" / "config.example.yaml"
        raise SystemExit(f"缺少配置：cp {example} {cfg_path} 后填写（详见文件内注释）")
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}


async def radar_push_loop(router: Router, adapter: WeixinTriggerAdapter, cfg: dict) -> None:
    """每日定时：跑雷达 → 推送选题清单到 home_uid；推送失败自动入补发队列（设计 3.4）。"""
    home_uid = (cfg.get("channel", {}).get("weixin", {}) or {}).get("home_uid", "")
    hour = int((cfg.get("radar") or {}).get("daily_hour", 8))
    if not home_uid:
        logger.warning("channel.weixin.home_uid 未配置，雷达推送跳过（清单仍会落盘）")
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
                    logger.info("选题清单已推送 %s", home_uid)
                except Exception as exc:
                    logger.warning("推送失败（%s），已入补发队列", exc)
                    router.queue_push(home_uid, text)
        except Exception:
            logger.exception("雷达运行失败")


async def run(cfg: dict) -> None:
    gen = build_gen_router(cfg.get("imagegen") or {})
    adapter = WeixinTriggerAdapter((cfg.get("channel") or {}).get("weixin") or {},
                                   state_dir=str(BASE / "state"))
    router = Router(adapter, gen, cfg, base_dir=str(BASE))
    asyncio.create_task(radar_push_loop(router, adapter, cfg))
    logger.info("启动微信适配器（生图主通道：%s）", gen.primary)
    try:
        await adapter.start(router.handle)
    finally:
        await adapter.stop()


async def radar_now(cfg: dict) -> None:
    path = radar_mod.run_radar(
        (cfg.get("radar") or {}).get("keywords", []),
        (cfg.get("radar") or {}).get("max_items", 20))
    print(radar_mod.format_topic_list(path, top=5))
    print(f"\n清单已落盘：{path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="小红书仿写 Agent")
    parser.add_argument("--qr-login", action="store_true", help="微信扫码登录（首次）")
    parser.add_argument("--radar-now", action="store_true", help="立即跑一轮雷达并打印清单")
    args = parser.parse_args()
    cfg = load_config()
    if args.qr_login:
        asyncio.run(qr_login(str(BASE / "state")))
    elif args.radar_now:
        asyncio.run(radar_now(cfg))
    else:
        try:
            asyncio.run(run(cfg))
        except KeyboardInterrupt:
            print("\n已退出")


if __name__ == "__main__":
    main()
