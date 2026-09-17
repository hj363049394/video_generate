#!/usr/bin/env python3
"""选题雷达 · 数值热度计算与候选筛选

公式（与 docs/specs/2026-09-17-xhs-content-agent-design.md §3.3 一致）：
  加权互动量 E = 点赞 + 收藏×2 + 评论×3 + 分享×2
  原始热度 H_raw = log10(E + 1) × 10
  时间衰减 D = 发布≤7天 ×1.2 / ≤30天 ×1.0 / ≤90天 ×0.8 / 更早 ×0.5
  热度 H = H_raw × D

门槛（默认与 profile.yaml 保持一致，可命令行覆盖）：H ≥ 25 且 点赞 ≥ 500

用法：
  python3 score_topics.py --input sample_search_results.json          # 演示数据
  python3 score_topics.py --input output/search_results_带娃游.json    # 真实抓取结果
"""
import argparse
import json
import math
import os
from datetime import date, datetime

DEFAULT_HEAT_THRESHOLD = 25.0
DEFAULT_MIN_LIKES = 500
DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def weighted_engagement(n: dict) -> int:
    return (
        n.get("likes", 0)
        + n.get("collects", 0) * 2
        + n.get("comments", 0) * 3
        + n.get("shares", 0) * 2
    )


def parse_published(s) -> datetime | None:
    if isinstance(s, (int, float)):  # 秒级时间戳
        return datetime.fromtimestamp(s)
    if isinstance(s, str):
        for fmt in DATE_FORMATS:
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
    decay = time_decay(parse_published(n.get("published_at")))
    return {
        "weighted_engagement": e,
        "heat": round(h_raw * decay, 1),
        "decay": decay,
    }


def main():
    parser = argparse.ArgumentParser(description="数值热度计算与候选筛选")
    parser.add_argument("--input", required=True, nargs="+", help="搜索结果 JSON 路径（支持多个）")
    parser.add_argument("--heat-threshold", type=float, default=DEFAULT_HEAT_THRESHOLD)
    parser.add_argument("--min-likes", type=int, default=DEFAULT_MIN_LIKES)
    args = parser.parse_args()

    all_notes = []
    for path in args.input:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        all_notes.extend(data.get("notes", []))

    # 多关键词去重（同一笔记可能命中多个关键词，保留首条）
    seen, deduped = set(), []
    for n in all_notes:
        nid = n.get("note_id")
        if nid and nid in seen:
            continue
        seen.add(nid)
        deduped.append(n)
    all_notes = deduped

    scored = []
    for n in all_notes:
        metrics = compute_heat(n)
        passed = metrics["heat"] >= args.heat_threshold and n.get("likes", 0) >= args.min_likes
        scored.append({**n, **metrics, "passed": passed})
    all_notes = scored

    passed_notes = sorted(
        [n for n in all_notes if n["passed"]], key=lambda x: x["heat"], reverse=True
    )

    out = {
        "generated_at": date.today().isoformat(),
        "input": args.input,
        "thresholds": {"heat": args.heat_threshold, "min_likes": args.min_likes},
        "summary": {
            "total": len(all_notes),
            "passed": len(passed_notes),
            "rejected_by_threshold": len(all_notes) - len(passed_notes),
        },
        "passed": passed_notes,
        "all": all_notes,
    }

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "candidates.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"输入 {out['summary']['total']} 条 | 过数值门槛 {out['summary']['passed']} 条 | 淘汰 {out['summary']['rejected_by_threshold']} 条")
    print(f"{'热度':>6}  {'标题'}")
    for n in sorted(all_notes, key=lambda x: x["heat"], reverse=True):
        flag = "✓ 候选" if n["passed"] else "✗ 淘汰"
        print(f"{n['heat']:>6}  {flag}  {n['title'][:24]}")
    print(f"结果已写入 {out_path}（下一步：按 prompts/topic_scoring.md 做语义评分）")


if __name__ == "__main__":
    main()
