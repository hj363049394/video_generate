"""选题雷达编排：红狐抓取 → 数值评分 → 当日选题清单落盘（供 /选题 与每日推送）

包装 poc/radar 的 fetch_redfox.py 与 score_topics.py（改造清单 4.4）。
语义评分（四维 LLM 评分）属 Phase 1.5；当前清单按数值热度排序。
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

RADAR_DIR = Path(__file__).resolve().parents[2] / "poc" / "radar"

# 笔记详情页前缀（note_id → 可点开/可粘贴触发拉模式的链接）
XHS_NOTE_URL = "https://www.xiaohongshu.com/explore/"


def run_radar(keywords: list, max_items: int = 20,
              heat_threshold: float | None = None, min_likes: int | None = None,
              bot_id: str = "default") -> str:
    """跑一轮雷达：逐关键词抓取 + 数值评分，产出 agent/workspace/<bot_id>/topic_list_YYYY-MM-DD.json。
    返回清单路径。门槛不传时用 score_topics.py 默认值（H≥25 且 赞≥500）。
    bot_id 与 Router.workspace 的分层一致（v1.1 多 Bot 隔离）。"""
    if not keywords:
        raise ValueError("radar.keywords 未配置")
    env = dict(os.environ)
    # 每轮独立输出，避免旧 search_results 干扰本轮 input 集合
    result_files = []
    for kw in keywords:
        subprocess.run(
            [sys.executable, str(RADAR_DIR / "fetch_redfox.py"),
             "--keyword", kw, "--max-items", str(max_items)],
            check=True, cwd=str(RADAR_DIR), env=env)
        hits = sorted(glob.glob(str(RADAR_DIR / "output" / f"search_results_redfox_search_*{kw}*.json")))
        if hits:
            result_files.append(hits[-1])
    if not result_files:
        raise RuntimeError("红狐抓取无输出（检查 REDFOX_API_KEY 与网络）")
    cmd = [sys.executable, str(RADAR_DIR / "score_topics.py"), "--input", *result_files]
    if heat_threshold is not None:
        cmd += ["--heat-threshold", str(heat_threshold)]
    if min_likes is not None:
        cmd += ["--min-likes", str(min_likes)]
    subprocess.run(cmd, check=True, cwd=str(RADAR_DIR), env=env)
    candidates = json.loads((RADAR_DIR / "output" / "candidates.json").read_text(encoding="utf-8"))
    passed = candidates.get("passed", [])[:10]  # 数值热度 Top 10（语义评分 Phase 1.5）
    out_dir = Path(__file__).resolve().parents[1] / "workspace" / bot_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"topic_list_{date.today().isoformat()}.json"
    out_path.write_text(json.dumps({
        "date": date.today().isoformat(),
        "note": "数值热度排序（LLM 语义评分 Phase 1.5 接入）",
        "topic_list": passed,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out_path)


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

    每条附笔记链接：可点开查看对标原文，也可直接粘贴给 bot 触发拉模式仿写。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    topics = data.get("topic_list") or []
    if not topics:
        return f"{data.get('date', '')} 雷达无过门槛选题（可放宽阈值或换关键词）"
    lines = [f"📅 {data.get('date', '')} 选题清单 Top{min(top, len(topics))}",
             "回复「确认 N」触发仿写；粘贴链接可直接仿写该条：", ""]
    for i, t in enumerate(topics[:top], 1):
        lines.append(f"{i}. {t.get('title', '')[:36]}")
        lines.append(f"   热度{t.get('heat', '-')} 赞{t.get('likes', '-') or '-'} "
                     f"藏{t.get('collects', '-') or '-'}")
        nid = str(t.get("note_id") or "").strip()
        if nid:
            lines.append(f"   🔗 {XHS_NOTE_URL}{nid}")
        if t.get("rewrite_angle"):
            lines.append(f"   角度：{t['rewrite_angle'][:40]}")
        lines.append("")
    return "\n".join(lines).strip()
