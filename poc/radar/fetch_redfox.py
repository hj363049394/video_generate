#!/usr/bin/env python3
"""选题雷达 · 红狐数据（RedFoxHub）适配器 —— 主数据源

基于官方 redfox-python-sdk（pip install redfox-python-sdk）。

两种模式：
  search（默认）：关键词搜笔记，sort_type='2' 最热排序，爆款浓度高
  hot：爆款洞察，自带相关性/热度/时效评分 + 热门话题 + 相关搜索词

统一输出内部 Schema（与 fetch_apify.py 一致），供 score_topics.py 消费。

字段映射（2026-09-17 实测）：
  search: workId/workUrl/workType/workTitle/workDesc/coverUrl/
          workLikedCount/workCollectedCount/workCommentsCount/workSharedCount/
          workPublishTime/accountNickname
  hot:    id/shareInfoLink/title/desc/cover/likedCount/collectedCount/
          commentsCount/sharedCount/createTime/authorNickname (+ redfox_* 评分字段)

用法：
  export REDFOX_API_KEY=ak_xxx
  python3 fetch_redfox.py --keyword "带娃游" --max-items 20
  python3 fetch_redfox.py --keyword "带娃游" --mode hot --start-date 2026-09-10 --end-date 2026-09-17
"""
import argparse
import json
import os
import sys
from datetime import date, timedelta

from redfox import RedFoxClient


def normalize_search(item: dict, keyword: str) -> dict:
    """search_articles 响应 -> 内部 Schema"""
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


def normalize_hot(item: dict, keyword: str) -> dict:
    """search_hot_notes 响应 -> 内部 Schema（保留红狐自带评分，辅助语义评分）"""
    return {
        "note_id": item.get("id", ""),
        "url": item.get("shareInfoLink", ""),
        "type": "image",
        "title": item.get("title", ""),
        "description": item.get("desc", ""),
        "cover_image": item.get("cover", ""),
        "likes": item.get("likedCount") or 0,
        "collects": item.get("collectedCount") or 0,
        "comments": item.get("commentsCount") or 0,
        "shares": item.get("sharedCount") or 0,
        "published_at": item.get("createTime", ""),
        "author": {"nickname": item.get("authorNickname", ""), "fans": item.get("authorFans")},
        "source_keyword": keyword,
        "data_source": "redfox_hot",
        "redfox_scores": {
            "relevance": item.get("relevanceScore"),
            "popularity": item.get("popularityScore"),
            "recency": item.get("recencyScore"),
            "total": item.get("totalScore"),
        },
    }


def fetch_search(client, keyword: str, max_items: int) -> list:
    """关键词搜索（最热排序），自动翻页"""
    raw, offset = [], 0
    while len(raw) < max_items:
        r = client.xiaohongshu.search_articles(keyword=keyword, offset=offset, sort_type="2")
        lst = r.get("list", [])
        if not lst or not r.get("hasMore"):
            raw.extend(lst)
            break
        raw.extend(lst)
        offset += len(lst)
    return [normalize_search(n, keyword) for n in raw[:max_items]]


def fetch_hot(client, keyword: str, start: str, end: str) -> list:
    """爆款洞察（近 N 天）"""
    r = client.xiaohongshu.search_hot_notes(keyword=keyword, start_date=start, end_date=end)
    return [normalize_hot(n, keyword) for n in r.get("articles", [])]


def main():
    parser = argparse.ArgumentParser(description="小红书关键词笔记抓取（红狐数据适配器）")
    parser.add_argument("--keyword", required=True, help="搜索关键词")
    parser.add_argument("--max-items", type=int, default=20, help="最大抓取条数（search 模式）")
    parser.add_argument("--mode", choices=["search", "hot"], default="search")
    parser.add_argument("--days", type=int, default=7, help="hot 模式：回看天数")
    args = parser.parse_args()

    key = os.environ.get("REDFOX_API_KEY", "")
    if not key:
        print("错误：未设置 REDFOX_API_KEY 环境变量（redfox.hk 控制台获取）", file=sys.stderr)
        sys.exit(1)

    client = RedFoxClient(api_key=key)
    if args.mode == "hot":
        end = date.today().isoformat()
        start = (date.today() - timedelta(days=args.days)).isoformat()
        notes = fetch_hot(client, args.keyword, start, end)
        suffix = "hot"
    else:
        notes = fetch_search(client, args.keyword, args.max_items)
        suffix = "search"

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"search_results_redfox_{suffix}_{args.keyword}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": date.today().isoformat(), "notes": notes}, f, ensure_ascii=False, indent=2)
    print(f"抓取完成：{len(notes)} 条 -> {out_path}")


if __name__ == "__main__":
    main()
