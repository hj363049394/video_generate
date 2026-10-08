"""成本账本（P0-3，v1.4.0）：全链路资源调用记账 → 每篇任务可查「花了多少钱」

设计要点：
  - pipeline 各环节是纯函数、不持有任务上下文 → 用 ContextVar 绑定当前记账范围
    （asyncio.to_thread / create_task 均复制上下文，跨线程跨协程可见；未绑定时
    记入 scope="adhoc" 桶，账不丢）
  - 记原始用量（字符/张/次）+ 按 config.pricing 单价折算金额；两种口径都落库，
    单价缺省为 0（只显示用量不折钱），按实际采购价填 config 即可
  - 独立 cost_log 表（与 tasks 同库 state.{bot}.db），不动 tasks 表结构，
    聚合对账走 SQL，不干扰任务流
  - 记账失败永不抛错（账本绝不能打断生产线）

埋点分布（v1.4.0 首批）：
  llm_chars        rewrite.llm_call_factory / vision_call —— 每次对话 提示词+输出字符
  vision_images    rewrite._chat_once_vision —— 多模态看图张数
  redfox_search    radar.fetch_search_notes —— 每页搜索
  redfox_detail    note_fetch.fetch_note_detail —— 详情抓取（选题补图/拉模式/回采共用）
  redfox_transcript note_fetch.fetch_video_transcript —— 视频提文案
  gen_image        imagegen.ImageGenRouter.generate —— 每张图（含通道名）
  tts_chars        video.tts —— 配音字符
"""
from __future__ import annotations

import contextvars
import logging
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger("pipeline.costing")

# 记账范围（"任务ID" / "radar:日期" / "adhoc"）——由调用方 bind，未绑定落 adhoc
_scope: contextvars.ContextVar[str] = contextvars.ContextVar("cost_scope", default="adhoc")

_lock = threading.Lock()
_db: sqlite3.Connection | None = None
_pricing: dict = {}


def init(db_path: str, pricing: dict | None = None) -> None:
    """初始化账本（幂等）。Router / main 各入口启动时调用一次。"""
    global _db, _pricing
    try:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("""CREATE TABLE IF NOT EXISTS cost_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT, item TEXT,
            qty REAL, ts REAL, note TEXT)""")
        conn.commit()
        with _lock:
            if _db is not None:
                _db.close()
            _db = conn
            _pricing = dict(pricing or {})
        logger.info("成本账本已初始化：%s（单价项 %d）", db_path, len(_pricing))
    except Exception:  # noqa: BLE001 —— 初始化失败降级为空账本（record 静默丢弃）
        logger.exception("成本账本初始化失败（本进程记账停用）")


def bind(scope: str) -> None:
    """绑定当前协程/线程的记账范围（_run_task 绑任务ID；run_radar 绑 radar:日期）。"""
    _scope.set(scope)


def record(item: str, qty: float, note: str = "") -> None:
    """记一笔用量。单价取 _pricing[item]（缺省 0，只记用量不折钱）。永不抛错。"""
    if _db is None or qty <= 0:
        return
    try:
        with _lock:
            _db.execute(
                "INSERT INTO cost_log(scope, item, qty, ts, note) VALUES (?,?,?,?,?)",
                (_scope.get(), item, float(qty), time.time(), note[:120]))
            _db.commit()
    except Exception:  # noqa: BLE001 —— 账本绝不打断生产线
        logger.debug("记账失败 item=%s", item, exc_info=True)


def _unit_price(item: str) -> float:
    """单价（config.pricing，元/单位）。缺省 0 = 只记用量不折钱。"""
    try:
        return float(_pricing.get(item, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def summary(scope: str) -> dict:
    """按范围聚合：{item: {qty, cost}} + total。无记录返回空 items。"""
    if _db is None:
        return {"scope": scope, "items": {}, "total": 0.0}
    try:
        with _lock:
            rows = _db.execute(
                "SELECT item, SUM(qty), COUNT(*) FROM cost_log WHERE scope=? "
                "GROUP BY item ORDER BY SUM(qty) DESC", (scope,)).fetchall()
    except Exception:  # noqa: BLE001
        logger.exception("成本聚合失败 scope=%s", scope)
        return {"scope": scope, "items": {}, "total": 0.0}
    items = {}
    total = 0.0
    for item, qty, calls in rows:
        cost = round((qty or 0) * _unit_price(item), 4)
        items[str(item)] = {"qty": round(qty or 0, 1), "calls": int(calls or 0), "cost": cost}
        total += cost
    return {"scope": scope, "items": items, "total": round(total, 4)}


# 用量展示名（/成本 与质量报告共用）
_LABELS = {
    "llm_chars": "LLM 字符", "vision_images": "看图（张）",
    "redfox_search": "红狐搜索（次）", "redfox_detail": "红狐详情（次）",
    "redfox_transcript": "红狐提文案（次）", "gen_image": "AI 生图（张）",
    "tts_chars": "配音（字符）",
}


def format_summary(scope: str) -> str:
    """单行成本摘要（交付消息/质量报告用）：
    「LLM 12.3k 字 · 生图 4 张 · 红狐 6 次 · 估算 ¥0.42」；无记录返回空串。"""
    s = summary(scope)
    if not s["items"]:
        return ""
    parts = []
    redfox = 0
    for item, d in s["items"].items():
        if item.startswith("redfox_"):
            redfox += d["calls"]
            continue
        label = _LABELS.get(item, item)
        parts.append(f"{label} {_fmt_qty(d['qty'])}")
    if redfox:
        parts.append(f"红狐 {redfox} 次")
    text = " · ".join(parts)
    if s["total"] > 0:
        text += f" · 估算 ¥{s['total']:.2f}"
    return text


def _fmt_qty(q: float) -> str:
    return f"{q / 1000:.1f}k" if q >= 1000 else (f"{q:.0f}" if q == int(q) else f"{q:.1f}")


def format_breakdown(scope: str) -> str:
    """多行明细（/成本 指令用）。"""
    s = summary(scope)
    if not s["items"]:
        return f"「{scope}」暂无成本记录。"
    lines = [f"💰 成本明细 · {scope}"]
    for item, d in s["items"].items():
        label = _LABELS.get(item, item)
        price = _unit_price(item)
        money = f" · ¥{d['cost']:.2f}" if price else ""
        lines.append(f"· {label}：{_fmt_qty(d['qty'])}（{d['calls']} 次{money}）")
    if s["total"] > 0:
        lines.append(f"合计估算：¥{s['total']:.2f}（单价见 config pricing，按实际采购价调整）")
    else:
        lines.append("（pricing 未配单价，仅记用量；填 config.pricing 后自动折算金额）")
    return "\n".join(lines)


def recent_scopes(days: float = 7, limit: int = 20) -> list:
    """近 N 天有消耗的范围（/成本 总览 + 回采对账用）。"""
    if _db is None:
        return []
    try:
        with _lock:
            rows = _db.execute(
                "SELECT scope, COUNT(*) FROM cost_log "
                "WHERE ts >= ? GROUP BY scope ORDER BY MAX(ts) DESC LIMIT ?",
                (time.time() - days * 86400, limit)).fetchall()
        out = []
        for scope, calls in rows:
            s = summary(scope)
            out.append({"scope": scope, "calls": calls,
                       "total": s["total"], "items": s["items"]})
        return out
    except Exception:  # noqa: BLE001
        logger.exception("近段成本查询失败")
        return []


def format_recent(days: float = 7) -> str:
    """/成本 总览：近 N 天各范围一行摘要。"""
    scopes = recent_scopes(days)
    if not scopes:
        return f"近 {days:.0f} 天暂无成本记录（跑一单后可查）。"
    lines = [f"📊 近 {days:.0f} 天消耗总览（/成本 任务ID 查单篇明细）："]
    total = 0.0
    for s in scopes[:10]:
        one = format_summary(s["scope"])
        if one:
            lines.append(f"· {s['scope']}：{one}")
            total += s["total"]
    if total > 0:
        lines.append(f"合计估算：¥{total:.2f}")
    return "\n".join(lines)
