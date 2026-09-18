#!/usr/bin/env python3
"""原创度自检：对比对标原文与仿写稿的文本相似度

指标：
  1. 字符 3-gram Jaccard 相似度（整体文本重叠度，警戒线 0.30）
  2. 连续公共子串 ≥ 8 字片段列表（直接抄袭风险，要求为空）

用法：
  python3 check_originality.py --original ../radar/output/benchmark_note.json --rewrite rewrite_001.json
"""
import argparse
import json
import re


def clean_text(s: str) -> str:
    """去空白/换行，保留中文、英文、数字、emoji 与常用符号"""
    return re.sub(r"\s+", "", s)


def ngrams(s: str, n: int = 3) -> set:
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def common_substrings(a: str, b: str, min_len: int = 8) -> list:
    """找出两文本中所有 >= min_len 的连续公共片段"""
    results = set()
    # 以 a 的每个位置为起点，向 b 查找最长公共延伸
    for i in range(len(a)):
        for j in range(len(b)):
            k = 0
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            if k >= min_len:
                results.add(a[i : i + k])
                i += k  # 跳过已匹配部分（简单去重）
    # 只保留不被其他片段包含的
    final = [s for s in results if not any(s != t and s in t for t in results)]
    return sorted(final, key=len, reverse=True)


def main():
    parser = argparse.ArgumentParser(description="原创度自检")
    parser.add_argument("--original", required=True, help="对标笔记 JSON（含 description 字段）")
    parser.add_argument("--rewrite", required=True, help="仿写稿 JSON（含 body 字段）")
    args = parser.parse_args()

    with open(args.original, encoding="utf-8") as f:
        original = clean_text(json.load(f)["description"])
    with open(args.rewrite, encoding="utf-8") as f:
        rw = json.load(f)
    rewrite = clean_text(rw["body"] + "".join(rw.get("title_candidates", [])))

    sim = jaccard(ngrams(original, 3), ngrams(rewrite, 3))
    overlaps = common_substrings(original, rewrite, 8)

    print(f"原文长度: {len(original)} 字 | 仿写长度: {len(rewrite)} 字")
    print(f"字符 3-gram Jaccard 相似度: {sim:.3f}（警戒线 0.30，设计文档阈值 0.60）")
    print(f"连续公共片段（>=8 字）: {len(overlaps)} 处")
    for s in overlaps[:10]:
        print(f"  - {s}")
    verdict = "通过" if sim < 0.30 and not overlaps else "需重写"
    print(f"\n判定: {verdict}")


if __name__ == "__main__":
    main()
