"""爆款笔记显式拆解（2026-09-21 v1.2：拆解驱动仿写）

对对标笔记做结构化拆解，产出可仿写的「结构规格」：
  content_structure —— 标题公式 / 正文结构骨架 / 口吻 / 标签策略
  image_structure   —— 逐张图卡结构（类型/文字排版形态/视觉风格）
  style_summary     —— 整体视觉调性

图片：有图集（拉模式）则下载 + 多模态看图（config.llm.vision_model 可选）；
     无图集（雷达模式只有封面）则从正文分段推断图卡结构（小红书图文的
     图卡内容通常映射正文的 emoji 分段）。

拆解结果落盘 work_dir/analysis.json，供 rewrite（同构仿写）与
imagepack（版式 DSL）消费——仿写不再千篇一律，逐项对标爆款结构。
"""
from __future__ import annotations

import base64
import json
import logging
import re
import urllib.request
from pathlib import Path
from typing import List, Optional

from pipeline.promptkit import load_prompt
from pipeline.rewrite import llm_call_factory, llm_vision_call_factory, parse_llm_output

logger = logging.getLogger("pipeline.note_analyze")

_MAX_IMAGES = 9          # 多模态看图上限（小红书图集最多 18，取前 9 张足够推结构）
_DL_TIMEOUT = 30
_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
       "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148")


# ─── 图片下载 ─────────────────────────────────────────────────

def download_images(urls: List[str], out_dir: str, limit: int = _MAX_IMAGES) -> List[str]:
    """下载图集到 out_dir/img_{i}.jpg，返回本地路径列表（单张失败跳过）。"""
    paths = []
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for i, url in enumerate([u for u in urls if u][:limit]):
        dst = out / f"img_{i}.jpg"
        if dst.exists():
            paths.append(str(dst))
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=_DL_TIMEOUT) as resp:
                data = resp.read()
            if len(data) < 1024:  # 异常响应（防盗链占位图等）
                continue
            dst.write_bytes(data)
            paths.append(str(dst))
        except Exception as exc:
            logger.warning("图片下载失败（跳过）url=%s err=%s", url[:80], exc)
    return paths


# ─── 多模态看图 ──────────────────────────────────────────────

# 提示词外置（v1.2.1）：agent/prompts/describe-image.md——调提示词改文件，不改代码
DESCRIBE_PROMPT = load_prompt("describe-image")


def describe_images(vision_call, img_paths: List[str]) -> List[str]:
    """逐张多模态看图（vision_call(prompt, [图片路径])）。失败的单张返回占位描述。"""
    descs = []
    for p in img_paths:
        try:
            with open(p, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            descs.append(vision_call(DESCRIBE_PROMPT, [b64]))
        except Exception as exc:
            logger.warning("看图失败（跳过）path=%s err=%s", p, exc)
            descs.append("（该图视觉信息缺失）")
    return descs


# ─── 拆解提示词 ────────────────────────────────────────────────

# 提示词外置（v1.2.1）：agent/prompts/analyze.md——调提示词改文件，不改代码
ANALYZE_PROMPT = load_prompt("analyze")


def _image_section(descs: List[str], n_claimed: int) -> str:
    if descs:
        lines = [f"图集（共 {len(descs)} 张，多模态实测描述）："]
        lines += [f"图 {i + 1}：{d}" for i, d in enumerate(descs)]
        return "\n".join(lines)
    if n_claimed > 0:
        return (f"图集：笔记声称共 {n_claimed} 张，但图片不可见——"
                f"从正文分段与文案结构推断图卡结构（小红书图图文的图卡内容通常映射正文 emoji 分段），"
                f"image_structure 的 desc 标注「推断」")
    return "图集：无图片信息——单图/封面型，从标题正文推断整体结构"


def build_analyze_prompt(topic: dict, img_descs: List[str]) -> str:
    return ANALYZE_PROMPT.format(
        title=topic.get("title", ""),
        content=topic.get("description") or topic.get("content") or "",
        likes=topic.get("likes", 0), collects=topic.get("collects", 0),
        comments=topic.get("comments", 0),
        image_section=_image_section(img_descs, len(topic.get("images") or [])))


# ─── 主入口 ──────────────────────────────────────────────────

def run_analyze(topic: dict, work_dir: str, llm_config: dict) -> dict:
    """拆解对标笔记 → 结构规格 dict。图片下载失败/无 vision 均自动降级文字拆解。

    返回 {content_structure, image_structure, style_summary}。
    拆解本身失败向上抛（router 侧降级为无拆解仿写，不阻断主线）。
    """
    img_dir = str(Path(work_dir) / "benchmark")
    urls = topic.get("images") or ([topic["cover_image"]] if topic.get("cover_image") else [])
    img_paths = download_images(urls, img_dir) if urls else []

    descs: List[str] = []
    if img_paths:
        vision = llm_vision_call_factory(llm_config or {})
        if vision:
            descs = describe_images(vision, img_paths)
            if descs:
                logger.info("多模态看图完成 %d 张", len(descs))
        else:
            logger.info("未配置 llm.vision_model，跳过看图（文字拆解）")

    llm = llm_call_factory(llm_config or {})
    raw = llm(build_analyze_prompt(topic, descs))
    analysis = parse_llm_output(raw)
    # 基本校验：image_structure 至少 1 张，skeleton 至少 1 单元
    if not (analysis.get("image_structure") or []):
        raise ValueError("拆解输出缺 image_structure")
    if not (analysis.get("content_structure", {}).get("skeleton") or []):
        raise ValueError("拆解输出缺 content_structure.skeleton")
    return analysis
