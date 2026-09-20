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
import os
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
                (cfg.get("radar") or {}).get("max_items", 20),
                (cfg.get("radar") or {}).get("heat_threshold"),
                (cfg.get("radar") or {}).get("min_likes"))
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
        (cfg.get("radar") or {}).get("max_items", 20),
        (cfg.get("radar") or {}).get("heat_threshold"),
        (cfg.get("radar") or {}).get("min_likes"))
    print(radar_mod.format_topic_list(path, top=5))
    print(f"\n清单已落盘：{path}")
    # 跑完立即推送到 home_uid（与 bot 内每日推送行为一致；bot 不在线也能推，tokenless）
    home_uid = ((cfg.get("channel") or {}).get("weixin") or {}).get("home_uid", "")
    if home_uid:
        try:
            adapter = WeixinTriggerAdapter(((cfg.get("channel") or {}).get("weixin") or {}),
                                            state_dir=str(BASE / "state"))
            adapter.bot_id = profile
            await adapter.push_text(home_uid, radar_mod.format_topic_list(path, top=5))
            print(f"已推送到 {home_uid}")
        except Exception as exc:
            print(f"推送失败（{exc}）——bot 在线时会经补发队列重试；也可在微信发 /选题 手动拉取")
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
