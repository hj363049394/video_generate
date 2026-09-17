#!/usr/bin/env python3
"""选题雷达 · 数据抓取适配器

主源：Apify · SocialDataX（socialdatax/socialdatax-xhs-data-api）
备源：Apify · ethereal_wool（ethereal_wool/xiaohongshu-rednote-scraper）

统一输出内部 Schema 到 output/search_results_<关键词>.json，供 score_topics.py 消费。

用法：
  export APIFY_TOKEN=你的apify_token
  python3 fetch_apify.py --keyword "带娃游" --max-items 20 [--actor socialdatax|ethereal]

注意：SocialDataX 的响应字段名以实际 API 返回为准（本适配器按其公开文档约定映射），
首次真实抓取后如字段不一致，仅需调整 normalize() 中的映射表。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API_BASE = "https://api.apify.com/v2/actors/{actor}/run-sync-get-dataset-items"

# actor 标识 -> Apify actor ID
ACTORS = {
    "socialdatax": "socialdatax~socialdatax-xhs-data-api",
    "ethereal": "ethereal_wool~xiaohongshu-rednote-scraper",
}


def build_input(actor: str, keyword: str, max_items: int, sort: str, time_range: str) -> dict:
    """构造各 actor 的输入参数
    SocialDataX sort_type 枚举：general / time_descending / like_count_descending /
    comment_count_descending / collect_count_descending（爆款优先用 like_count_descending）
    publish_time_range 枚举：all / day / week / half_year
    """
    if actor == "socialdatax":
        return {
            "operation": "search_notes",
            "keyword": keyword,
            "sort_type": sort,
            "publish_time_range": time_range,
            "max_items": max_items,
        }
    # ethereal_wool：关键词搜索，按热度降序
    return {
        "searchKeywords": [keyword],
        "maxItems": max_items,
        "sortType": "popularity_descending",
        "includeComments": False,
    }


def _first(item: dict, *keys, default=""):
    """从候选字段名中取第一个非空值（兼容不同 actor 的字段命名）"""
    for k in keys:
        if item.get(k):
            return item[k]
    return default


def normalize(actor: str, item: dict, keyword: str) -> dict:
    """不同数据源的字段 -> 内部统一 Schema
    SocialDataX 字段名（2026-09-17 实测）：note_url / summary / like_count /
    collect_count / comment_count / share_count / publish_time(秒级) / author_name
    """
    author = item.get("author") or {}
    return {
        "note_id": _first(item, "noteId", "note_id", "id"),
        "url": _first(item, "url", "noteUrl", "note_url"),
        "type": _first(item, "type", "note_type", "noteType", default="image"),
        "title": _first(item, "title"),
        "description": _first(item, "summary", "description", "desc"),
        "cover_image": _first(item, "cover_image_url", "coverUrl"),
        "likes": int(_first(item, "like_count", "likedCount", "likes", "likeCount", default=0)),
        "collects": int(_first(item, "collect_count", "collectedCount", "collects", "collectCount", default=0)),
        "comments": int(_first(item, "comment_count", "commentsCount", "comments", "commentCount", default=0)),
        "shares": int(_first(item, "share_count", "sharedCount", "shares", "shareCount", default=0)),
        "published_at": _first(item, "publish_time", "publishedAt", "timestamp", "published_at"),
        "author": {
            "nickname": _first(item, "author_name") or _first(author, "nickname", "name"),
        },
        "source_keyword": keyword,
    }


def fetch(token: str, actor: str, keyword: str, max_items: int, sort: str, time_range: str) -> list:
    """调用 Apify 同步接口抓取并归一化"""
    url = API_BASE.format(actor=ACTORS[actor]) + f"?token={token}"
    payload = json.dumps(
        build_input(actor, keyword, max_items, sort, time_range)
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        items = json.loads(resp.read().decode("utf-8"))
    return [normalize(actor, it, keyword) for it in items]


def main():
    parser = argparse.ArgumentParser(description="小红书关键词笔记抓取（Apify 适配器）")
    parser.add_argument("--keyword", required=True, help="搜索关键词")
    parser.add_argument("--max-items", type=int, default=20, help="最大抓取条数")
    parser.add_argument("--actor", choices=list(ACTORS), default="socialdatax", help="数据源")
    parser.add_argument(
        "--sort", default="like_count_descending",
        help="排序：like_count_descending(爆款优先，默认) / time_descending(最新) / general(综合)",
    )
    parser.add_argument(
        "--time-range", default="all", choices=["all", "day", "week", "half_year"],
        help="发布时间范围",
    )
    args = parser.parse_args()

    token = os.environ.get("APIFY_TOKEN", "")
    if not token:
        print("错误：未设置 APIFY_TOKEN 环境变量。", file=sys.stderr)
        print("POC 演示模式请直接运行: python3 score_topics.py --input sample_search_results.json", file=sys.stderr)
        sys.exit(1)

    notes = fetch(token, args.actor, args.keyword, args.max_items, args.sort, args.time_range)
    out_dir = os.path.join(os.path.dirname(__file__), "output")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"search_results_{args.keyword}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {"fetched_at": __import__("datetime").date.today().isoformat(), "notes": notes},
            f, ensure_ascii=False, indent=2,
        )
    print(f"抓取完成：{len(notes)} 条 -> {out_path}")


if __name__ == "__main__":
    main()
