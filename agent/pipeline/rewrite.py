"""拆解仿写：提示词构建 + LLM 调用 + 原创度自检

标准提示词全文见 docs/prompts/xhs-standard-prompts.md（Skill 化的内核）；
本模块是其可执行精简版：五层拆解 → 同构异题仿写 → 内容单元 JSON 产出。
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Callable, Dict

# 原创度门槛：字符 3-gram Jaccard 相似度（POC 定稿 0.30，验收样例 0.014）
ORIGINALITY_THRESHOLD = 0.30


# ─── 原创度自检（纯函数） ───────────────────────────────────────────────

def jaccard_ngram(a: str, b: str, n: int = 3) -> float:
    """字符 n-gram Jaccard 相似度：0 完全不同，1 完全一致。"""
    a, b = re.sub(r"\s+", "", a), re.sub(r"\s+", "", b)
    if not a or not b:
        return 0.0
    ga = {a[i:i + n] for i in range(len(a) - n + 1)}
    gb = {b[i:i + n] for i in range(len(b) - n + 1)}
    return len(ga & gb) / len(ga | gb) if ga | gb else 0.0


# ─── 提示词（xhs-standard-prompts.md 的执行版） ────────────────────────

def build_rewrite_prompt(benchmark: dict, persona: dict) -> str:
    persona = persona or {}
    return f"""你是小红书爆款拆解仿写专家。先对对标笔记做五层拆解（选题/标题/正文/视觉/数据层），
再用「同构异题」策略仿写：保留结构骨架、钩子模式、排版节奏、标签策略；替换主题细节、案例、数据、口吻。

## 对标笔记
标题：{benchmark.get('title', '')}
正文：{benchmark.get('description') or benchmark.get('content') or ''}
互动：赞 {benchmark.get('likes', 0)} / 藏 {benchmark.get('collects', 0)} / 评 {benchmark.get('comments', 0)}
热度：{benchmark.get('heat', '')}

## 人设（旅行家定位）
{persona.get('soul', '提供旅游行程规划与定制服务的旅行家：内容强化规划感（天数/预算/节奏表格化），'
 '对标讲"去哪"仿写强化"怎么排"，结尾固定服务钩子"评论区留言人数/天数/预算，帮你出定制行程"，'
 '口吻专业但不端着，像懂行的朋友给建议。')}

## 输出要求
严格输出如下 JSON（不要输出其他文字）：
```json
{{
  "title": "仿写标题（沿用对标钩子类型，20 字内）",
  "content": "仿写正文（结构骨架与对标一致，主题细节全部替换，含结尾服务钩子）",
  "tags": ["#标签1", "#标签2", "#标签3"],
  "image_units": [
    {{"role": "cover", "prompt": "封面底图生图提示词（无文字，中文，旅行摄影风格）"}},
    {{"role": "page", "prompt": "内页底图生图提示词"}}
  ]
}}
```"""


def parse_llm_output(text: str) -> dict:
    """从 LLM 回复中提取 JSON 块。"""
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S) or re.search(r"(\{.*\})", text, re.S)
    if not m:
        raise ValueError(f"LLM 输出无 JSON 块：{text[:200]}")
    return json.loads(m.group(1))


def run_rewrite(llm_call: Callable[[str], str], benchmark: dict, persona: dict) -> dict:
    """执行仿写并自检原创度。返回 {title, content, tags, image_units, similarity}。"""
    raw = llm_call(build_rewrite_prompt(benchmark, persona))
    result = parse_llm_output(raw)
    original = (benchmark.get("description") or benchmark.get("content") or "")
    result["similarity"] = round(
        jaccard_ngram(original, result.get("content", "")), 3)
    return result


# ─── LLM 调用（OpenAI 兼容接口，config.llm 注入） ──────────────────────

def llm_call_factory(llm_config: dict) -> Callable[[str], str]:
    """构建同步 llm_call(prompt) -> str。未配置时抛出可读错误。"""
    base_url = (llm_config.get("base_url") or "").rstrip("/")
    api_key = llm_config.get("api_key") or os.environ.get("LLM_API_KEY", "")
    model = llm_config.get("model") or ""
    if not (base_url and api_key and model):
        raise RuntimeError(
            "未配置仿写 LLM：请在 agent/config/config.yaml 的 llm 段填 base_url/api_key/model"
            "（OpenAI 兼容 chat/completions 接口，如智谱/火山方舟 chat 模型）")

    def llm_call(prompt: str) -> str:
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
        }).encode()
        req = urllib.request.Request(
            f"{base_url}/chat/completions", data=body, method="POST",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]

    return llm_call
