"""选题雷达编排（v1.3：收编 POC + 语义四维评分接线）

链路：
  ① 红狐关键词搜索（收编自 poc/radar/fetch_redfox.py，改造清单 4.4 完成，
     公式/字段映射不变，摆脱 poc 目录 subprocess 依赖）
  ② 数值热度评分（收编自 poc/radar/score_topics.py：
     E = 赞 + 藏×2 + 评×3 + 享×2；H = log10(E+1)×10×时间衰减；默认 H≥25 且赞≥500）
  ③ LLM 语义四维评分（P0-1：agent/prompts/topic-scoring.md——relevance/virality/
     persona_fit/conversion 四维 + 硬门槛淘汰 + 机会分排序，产出 rewrite_angle
     仿写角度与 persona_hook 人设钩子；未配置 llm 或评分失败自动降级数值排序）
  ④ topic_list 落盘（供 /选题 与每日推送；rewrite_angle 在清单中展示，
     /确认 N 后注入仿写提示词，/换角度 N 可改）
"""
from __future__ import annotations

import glob
import json
import logging
import math
import os
import re
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger("pipeline.radar")

# 笔记详情页前缀（note_id → 可点开/可粘贴触发拉模式的链接）
XHS_NOTE_URL = "https://www.xiaohongshu.com/explore/"

# 内容方向 → 中文标签（与 SOUL.md 五大内容方向一致，清单展示用）
DIR_LABELS = {"itinerary": "行程规划", "knowledge": "旅行知识", "life": "人生与旅行",
              "gear": "旅行好物", "other": "机动"}

DEFAULT_HEAT_THRESHOLD = 25.0
DEFAULT_MIN_LIKES = 500
_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


# ─── ① 红狐搜索（收编自 poc/radar/fetch_redfox.py） ────────────────────

_client_cache: dict = {}


def _get_client(api_key: str):
    """延迟创建 RedFoxClient（无 key 时明确报错，不崩整个模块）。"""
    if not api_key:
        raise RuntimeError("未配置 REDFOX_API_KEY（雷达抓取必需）")
    if api_key in _client_cache:
        return _client_cache[api_key]
    from redfox import RedFoxClient
    client = RedFoxClient(api_key=api_key)
    _client_cache[api_key] = client
    return client


def _normalize_search(item: dict, keyword: str) -> dict:
    """search_articles 响应 → 内部 topic Schema（与 note_fetch.fetch_note_detail 同构）。"""
    return {
        "note_id": item.get("workId", ""),
        "url": item.get("workUrl", ""),
        "type": "video" if item.get("workType") == "video" else "image",
        "title": item.get("workTitle", ""),
        "description": item.get("workDesc", ""),
        "cover_image": item.get("coverUrl", ""),
        "likes": item.get("workLikedCount") or 0,
        "collects": item.get("workCollectedCount") or 0,
        "comments": item.get("workCommentsCount") or 0,
        "shares": item.get("workSharedCount") or 0,
        "published_at": item.get("workPublishTime", ""),
        "author": {"nickname": item.get("accountNickname", "")},
        "source_keyword": keyword,
        "data_source": "redfox",
    }


def fetch_search_notes(keyword: str, max_items: int, api_key: str) -> list:
    """关键词搜索笔记（sort_type='2' 最热排序，自动翻页），返回内部 Schema 列表。"""
    client = _get_client(api_key)
    raw, offset = [], 0
    while len(raw) < max_items:
        r = client.xiaohongshu.search_articles(keyword=keyword, offset=offset, sort_type="2")
        lst = r.get("list", [])
        if not lst or not r.get("hasMore"):
            raw.extend(lst)
            break
        raw.extend(lst)
        offset += len(lst)
    return [_normalize_search(n, keyword) for n in raw[:max_items]]


# ─── ② 数值热度评分（收编自 poc/radar/score_topics.py，公式不变） ───────

def weighted_engagement(n: dict) -> int:
    return (n.get("likes", 0) + n.get("collects", 0) * 2
            + n.get("comments", 0) * 3 + n.get("shares", 0) * 2)


def _parse_published(s) -> datetime | None:
    if isinstance(s, (int, float)):
        return datetime.fromtimestamp(s)
    if isinstance(s, str):
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    return None


def time_decay(published: datetime | None) -> float:
    if published is None:
        return 1.0
    days = (date.today() - published.date()).days
    if days <= 7:
        return 1.2
    if days <= 30:
        return 1.0
    if days <= 90:
        return 0.8
    return 0.5


def compute_heat(n: dict) -> dict:
    e = weighted_engagement(n)
    h_raw = math.log10(e + 1) * 10
    decay = time_decay(_parse_published(n.get("published_at")))
    return {"weighted_engagement": e, "heat": round(h_raw * decay, 1), "decay": decay}


def score_numeric(notes: list, heat_threshold: float, min_likes: int) -> list:
    """数值评分 + 过门槛，返回按热度降序的通过列表。"""
    scored = [{**n, **compute_heat(n)} for n in notes]
    return sorted([n for n in scored
                   if n["heat"] >= heat_threshold and n.get("likes", 0) >= min_likes],
                  key=lambda x: x["heat"], reverse=True)


# ─── ③ 语义四维评分（P0-1：topic-scoring.md 驱动） ─────────────────────

def _parse_json_array(text: str) -> list:
    """从 LLM 回复提取 JSON 数组（容忍 ```json 围栏与前后杂文字）。"""
    m = (re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
         or re.search(r"(\[.*\])", text, re.S))
    if not m:
        raise ValueError(f"语义评分输出无 JSON 数组：{text[:200]}")
    return json.loads(m.group(1))


def semantic_score(topics: list, llm_config: dict, persona: dict | None = None,
                   explore_mode: bool = False) -> list:
    """对过数值门槛的候选做 LLM 四维评分。

    返回通过硬门槛（relevance≥6 且 virality≥5）的列表，按机会分降序；
    每条附 opportunity_score/sub_scores/rewrite_angle/persona_hook。
    explore_mode（v1.3.11，/主题 探索模式）：硬门槛放开——LLM 淘汰项也保留，
    附 reject_reason 且无机会分，排序自然落到尾部，用户可自行判断。
    评分失败向上抛（run_radar 捕获后降级数值排序）。
    """
    from pipeline.promptkit import load_prompt, load_soul
    from pipeline.rewrite import llm_call_factory

    slim = []
    for t in topics:
        slim.append({
            "note_id": t.get("note_id", ""),
            "title": t.get("title", ""),
            "description": (t.get("description") or "")[:200],
            "likes": t.get("likes", 0), "collects": t.get("collects", 0),
            "comments": t.get("comments", 0), "heat": t.get("heat"),
            "source_keyword": t.get("source_keyword", ""),
        })
    prompt = load_prompt("topic-scoring").format(
        persona_section=load_soul(persona),
        candidates_json=json.dumps(slim, ensure_ascii=False))
    llm = llm_call_factory(llm_config or {})
    arr = _parse_json_array(llm(prompt))

    by_id = {str(e.get("note_id")): e for e in arr
             if isinstance(e, dict) and e.get("note_id")}
    selected = []
    for t in topics:
        e = by_id.get(str(t.get("note_id")))
        if not e:
            continue
        if str(e.get("status")) == "selected":
            selected.append({**t,
                             "opportunity_score": e.get("opportunity_score"),
                             "sub_scores": e.get("sub_scores") or {},
                             "content_direction": (e.get("content_direction") or "").strip(),
                             "rewrite_angle": (e.get("rewrite_angle") or "").strip(),
                             "persona_hook": (e.get("persona_hook") or "").strip()})
        elif explore_mode:  # v1.3.11：探索模式淘汰项也进清单（排序落尾部），理由落盘备查
            selected.append({**t,
                             "opportunity_score": e.get("opportunity_score") or 0,
                             "sub_scores": e.get("sub_scores") or {},
                             "content_direction": (e.get("content_direction") or "").strip(),
                             "rewrite_angle": (e.get("rewrite_angle") or "").strip(),
                             "persona_hook": (e.get("persona_hook") or "").strip(),
                             "reject_reason": (e.get("reject_reason") or "").strip()})
    # LLM 漏评的候选保留在尾部（不因漏评丢选题）
    missed = [t for t in topics if str(t.get("note_id")) not in by_id]
    selected.extend(missed)
    selected.sort(key=lambda t: (t.get("opportunity_score") or 0), reverse=True)
    return selected


# ─── ④ 主入口：抓取 → 数值 → 语义 → 落盘 ──────────────────────────────

def run_radar(keywords: list, max_items: int = 20,
              heat_threshold: float | None = None, min_likes: int | None = None,
              bot_id: str = "default", llm_config: dict | None = None,
              persona: dict | None = None, api_key: str = "",
              explore_mode: bool = False) -> str:
    """跑一轮雷达，产出 agent/workspace/<bot_id>/topic_list_YYYY-MM-DD.json，返回清单路径。

    llm_config/persona 传入时启用语义四维评分（产出仿写角度/人设钩子）；
    未配置或评分失败自动降级数值排序，不阻断。
    explore_mode（v1.3.11，/主题 探索模式）：数值门槛全放（调用方传 0/0）+
    语义硬门槛放开（LLM 淘汰项保留清单尾部）；默认 False，无人值守每日雷达
    维持防噪音硬门槛不变。
    """
    if not keywords:
        raise ValueError("radar.keywords 未配置")
    api_key = api_key or os.environ.get("REDFOX_API_KEY", "")
    if not api_key:
        raise RuntimeError("未配置 REDFOX_API_KEY（雷达抓取必需）")

    notes = []
    for kw in keywords:
        try:
            notes.extend(fetch_search_notes(kw, max_items, api_key))
        except Exception as exc:  # noqa: BLE001 —— 单关键词失败不拖垮整轮
            logger.warning("关键词「%s」抓取失败（跳过）: %s", kw, exc)
    if not notes:
        raise RuntimeError("红狐抓取无输出（检查 REDFOX_API_KEY 与网络）")

    seen, deduped = set(), []          # 多关键词去重（同笔记保留首条）
    for n in notes:
        nid = n.get("note_id")
        if nid and nid in seen:
            continue
        seen.add(nid)
        deduped.append(n)

    passed = score_numeric(deduped,
                           DEFAULT_HEAT_THRESHOLD if heat_threshold is None else heat_threshold,
                           DEFAULT_MIN_LIKES if min_likes is None else min_likes)

    selected = passed[:10]
    note = "数值热度排序（语义评分未启用：未配置 llm）"
    if passed and llm_config:
        try:
            selected = semantic_score(passed[:10], llm_config, persona, explore_mode)
            note = ("语义评分排序（探索模式：硬门槛放开，淘汰项保留在尾部）" if explore_mode
                    else "语义四维评分排序（relevance/virality/persona_fit/conversion，含仿写角度）")
        except Exception as exc:  # noqa: BLE001 —— 降级不阻断
            logger.warning("语义评分失败，降级数值排序: %s", exc)
            note = f"数值热度排序（语义评分失败降级）"

    out_dir = Path(__file__).resolve().parents[1] / "workspace" / bot_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"topic_list_{date.today().isoformat()}.json"
    used_heat = DEFAULT_HEAT_THRESHOLD if heat_threshold is None else heat_threshold
    used_likes = DEFAULT_MIN_LIKES if min_likes is None else min_likes
    out_path.write_text(json.dumps({
        "date": date.today().isoformat(),
        "note": note,
        "stats": {  # v1.3.10：漏斗统计——空清单时提示语透出"差在哪"
            "candidates": len(deduped), "numeric_passed": len(passed),
            "heat_threshold": used_heat, "min_likes": used_likes,
        },
        "topic_list": selected,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out_path)


# ─── 清单读取（/选题 指令与每日推送共用） ──────────────────────────────

def latest_topic_list(workspace_dir) -> str | None:
    """最近一次的当日/历史选题清单路径（优先当日）。

    兼容回退：bot 分层目录（workspace/<bot_id>/）无清单时，回落到
    v1.0 的共享目录（workspace/），避免多 Bot 重构前的落盘读不到。
    """
    hits = sorted(glob.glob(str(Path(workspace_dir) / "topic_list_*.json")))
    if hits:
        return hits[-1]
    parent = Path(workspace_dir).parent
    if parent.name == "workspace":  # workspace/<bot_id> → workspace/
        hits = sorted(glob.glob(str(parent / "topic_list_*.json")))
        if hits:
            return hits[-1]
    return None


def _topic_dirs(workspace_dir) -> list:
    """候选清单目录：bot 分层目录优先 + v1.0 共享目录回退（去重）。"""
    ws = Path(workspace_dir)
    dirs = [ws]
    if ws.parent.name == "workspace" and ws.parent != ws:
        dirs.append(ws.parent)
    return dirs


def list_topic_dates(workspace_dir) -> list:
    """所有历史选题清单日期（YYYY-MM-DD 升序）。含 v1.0 共享目录回退。"""
    dates = set()
    for d in _topic_dirs(workspace_dir):
        for f in glob.glob(str(d / "topic_list_*.json")):
            m = re.search(r"topic_list_(\d{4}-\d{2}-\d{2})\.json$", f)
            if m:
                dates.add(m.group(1))
    return sorted(dates)


def topic_list_by_date(workspace_dir, date_str: str) -> str | None:
    """按日期查清单路径。date_str 支持 MM-DD / M-D / YYYY-MM-DD / YYYY-M-D。"""
    parts = re.findall(r"\d+", date_str)
    if len(parts) == 3:  # YYYY-M-D
        iso = f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    elif len(parts) == 2:  # M-D / MM-DD → 补当年
        iso = f"{date.today().year}-{int(parts[0]):02d}-{int(parts[1]):02d}"
    else:
        return None
    for d in _topic_dirs(workspace_dir):
        p = d / f"topic_list_{iso}.json"
        if p.exists():
            return str(p)
    return None


def format_topic_list(path: str, top: int = 5) -> str:
    """把选题清单渲染成微信推送文本（纯文本 + 序号，设计 3.7 微信列）。

    每条附笔记链接与仿写角度（语义评分产出，/换角度 N 可调整）。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    topics = data.get("topic_list") or []
    if not topics:
        stats = data.get("stats") or {}
        date_str = data.get("date", "")
        if stats.get("numeric_passed", 0) > 0:  # v1.3.11：数值全过但语义阶段拦光
            return (f"{date_str} 搜到 {stats.get('candidates', 0)} 篇，数值门槛全部通过，"
                    f"但语义评分认为与账号定位契合度不足，{stats.get('numeric_passed', 0)} 篇全被淘汰；"
                    "建议换更贴旅游主题的短词（如：城市旅行 回忆杀）重试 /主题")
        base = f"{date_str} 雷达无过门槛选题"
        if stats:
            base += (f"：共搜到 {stats.get('candidates', 0)} 篇，数值门槛"
                     f"（热度≥{stats.get('heat_threshold', '-')} 且赞≥{stats.get('min_likes', '-')}）"
                     f"通过 {stats.get('numeric_passed', 0)} 篇")
        return base + "；建议换更短的搜索词（如：城市旅行 回忆杀）重试 /主题"
    lines = [f"📅 {data.get('date', '')} 选题清单 Top{min(top, len(topics))}",
             "回复「确认 N」触发仿写；粘贴链接可直接仿写该条：", ""]
    for i, t in enumerate(topics[:top], 1):
        lines.append(f"{i}. {t.get('title', '')[:36]}")
        score = t.get("opportunity_score")
        heat = f"热度{t.get('heat', '-')}"
        dir_label = DIR_LABELS.get(str(t.get("content_direction") or ""), "")
        lines.append(f"   {heat} 赞{t.get('likes', '-') or '-'} "
                     f"藏{t.get('collects', '-') or '-'}"
                     + (f" 机会分{score}" if score else "")  # 0 分（探索模式淘汰项）不显示
                     + (f"｜{dir_label}" if dir_label else ""))
        nid = str(t.get("note_id") or "").strip()
        if nid:
            lines.append(f"   🔗 {XHS_NOTE_URL}{nid}")
        if t.get("rewrite_angle"):
            lines.append(f"   角度：{t['rewrite_angle'][:40]}")
        lines.append("")
    return "\n".join(lines).strip()
