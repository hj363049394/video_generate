"""选题雷达编排：红狐抓取 → 数值评分 → 当日选题清单落盘（供 /选题 与每日推送）

包装 poc/radar 的 fetch_redfox.py 与 score_topics.py（改造清单 4.4）。
语义评分（四维 LLM 评分）属 Phase 1.5；当前清单按数值热度排序。
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

RADAR_DIR = Path(__file__).resolve().parents[2] / "poc" / "radar"


def run_radar(keywords: list, max_items: int = 20) -> str:
    """跑一轮雷达：逐关键词抓取 + 数值评分，产出 agent/workspace/topic_list_YYYY-MM-DD.json。
    返回清单路径。"""
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
    subprocess.run(
        [sys.executable, str(RADAR_DIR / "score_topics.py"), "--input", *result_files],
        check=True, cwd=str(RADAR_DIR), env=env)
    candidates = json.loads((RADAR_DIR / "output" / "candidates.json").read_text(encoding="utf-8"))
    passed = candidates.get("passed", [])[:10]  # 数值热度 Top 10（语义评分 Phase 1.5）
    out_dir = Path(__file__).resolve().parents[1] / "workspace"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"topic_list_{date.today().isoformat()}.json"
    out_path.write_text(json.dumps({
        "date": date.today().isoformat(),
        "note": "数值热度排序（LLM 语义评分 Phase 1.5 接入）",
        "topic_list": passed,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out_path)


def latest_topic_list(workspace_dir) -> str | None:
    """最近一次的当日/历史选题清单路径（优先当日）。"""
    hits = sorted(glob.glob(str(Path(workspace_dir) / "topic_list_*.json")))
    return hits[-1] if hits else None


def format_topic_list(path: str, top: int = 5) -> str:
    """把选题清单渲染成微信推送文本（纯文本 + 序号，设计 3.7 微信列）。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    topics = data.get("topic_list") or []
    if not topics:
        return f"{data.get('date', '')} 雷达无过门槛选题（可放宽阈值或换关键词）"
    lines = [f"📅 {data.get('date', '')} 选题清单 Top{min(top, len(topics))}",
             "回复「确认 N」触发仿写：", ""]
    for i, t in enumerate(topics[:top], 1):
        lines.append(f"{i}. {t.get('title', '')[:36]}")
        lines.append(f"   热度{t.get('heat', '-')} 赞{t.get('likes', '-') or '-'} "
                     f"藏{t.get('collects', '-') or '-'}")
        if t.get("rewrite_angle"):
            lines.append(f"   角度：{t['rewrite_angle'][:40]}")
        lines.append("")
    return "\n".join(lines).strip()
