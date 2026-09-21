"""拆解仿写：提示词构建 + LLM 调用 + 原创度自检

提示词外置（v1.2.1）：本模块消费 agent/prompts/rewrite.md 与 xhs-copy.md，
人设经 promptkit.load_soul 组装（config persona.soul > agent/SOUL.md）——
调提示词只改 prompts/ 文件，不改代码。
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Callable, Dict, Optional

from pipeline.promptkit import load_prompt, load_soul

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


# ─── 提示词（外置于 agent/prompts/，调提示词改文件不改代码） ────────────

REWRITE_PROMPT = load_prompt("rewrite")


def build_rewrite_prompt(benchmark: dict, persona: dict, analysis: Optional[dict] = None,
                         angle: Optional[str] = None) -> str:
    persona = persona or {}
    analysis_section = ""
    if analysis:
        cs = analysis.get("content_structure", {})
        skeleton = cs.get("skeleton") or []
        skeleton_text = "\n".join(
            f"  {i + 1}. {u.get('unit', '')}——{u.get('desc', '')}"
            for i, u in enumerate(skeleton))
        img_text = "\n".join(
            f"  图 {im.get('idx', i + 1)}：{im.get('kind', '')}｜{im.get('text_layout', '')}"
            f"｜{im.get('style', '')}"
            for i, im in enumerate(analysis.get("image_structure") or []))
        analysis_section = f"""
## 对标结构拆解（已由拆解引擎产出，仿写必须逐项对标）
标题公式：{cs.get('title_pattern', '')}
正文骨架（仿写正文按此逐单元同构，单元数保持一致）：
{skeleton_text or '（无骨架数据）'}
口吻：{cs.get('tone', '')}
图卡结构（仿写 image_units 数量与之一致，每张对标其 kind/风格）：
{img_text or '（无图卡数据）'}
整体调性：{analysis.get('style_summary', '')}
"""
    angle_section = ""
    if angle and str(angle).strip():
        angle_section = f"""
## 指定仿写角度（用户 /换角度 或语义评分指定，优先级高于默认差异化策略）
{str(angle).strip()}
"""
    return REWRITE_PROMPT.format(
        analysis_section=analysis_section,
        angle_section=angle_section,
        benchmark_title=benchmark.get("title", ""),
        benchmark_content=benchmark.get("description") or benchmark.get("content") or "",
        likes=benchmark.get("likes", 0), collects=benchmark.get("collects", 0),
        comments=benchmark.get("comments", 0), heat=benchmark.get("heat", ""),
        persona_soul=load_soul(persona))


def parse_llm_output(text: str) -> dict:
    """从 LLM 回复中提取 JSON 块。"""
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S) or re.search(r"(\{.*\})", text, re.S)
    if not m:
        raise ValueError(f"LLM 输出无 JSON 块：{text[:200]}")
    return json.loads(m.group(1))


# ─── 输出质量校验（P0-2/P0-3：schema 硬校验 + 对标原图引用拦截） ────────

# 对标图床/链接域名——生图提示词中出现即判"复用原图"（CHECKLIST #2 的代码实现）
_BENCH_URL_RE = re.compile(r"xiaohongshu\.com|xhslink\.com|xhscdn\.com|sns-img", re.I)


def contains_benchmark_url(*texts: str) -> Optional[str]:
    """返回首个命中的对标图床 URL 片段；全部干净则 None。rewrite 与 imagepack 共用。"""
    for t in texts:
        if t:
            m = _BENCH_URL_RE.search(str(t))
            if m:
                return m.group(0)
    return None


def validate_rewrite_output(result: dict) -> tuple:
    """仿写输出 schema 校验（CHECKLIST #3 的代码实现）。

    返回 (errors, warnings)：errors 非空 = 硬失败（run_rewrite 自动重试一次，
    仍失败则任务失败）；warnings 仅提示（进质量报告，不阻断）。
    """
    errors, warnings = [], []
    title = str(result.get("title") or "").strip()
    content = str(result.get("content") or "").strip()
    tags = [str(t).strip() for t in (result.get("tags") or []) if str(t).strip()]
    units = result.get("image_units") or []

    if not title:
        errors.append("标题为空")
    elif len(title) > 20:
        warnings.append(f"标题 {len(title)} 字超 20（小红书标题栏会截断）")
    if len(content) < 50:
        errors.append(f"正文过短（{len(content)} 字 < 50）")
    if len(tags) < 3:
        errors.append(f"标签仅 {len(tags)} 个 < 3")
    if len(units) < 2:
        errors.append(f"图片单元仅 {len(units)} 个 < 2")
    for i, u in enumerate(units):
        if not str((u or {}).get("prompt") or "").strip():
            errors.append(f"图片单元 {i + 1} 缺生图提示词")
    hit = contains_benchmark_url(*[str((u or {}).get("prompt") or "") for u in units])
    if hit:
        errors.append(f"图片单元提示词引用对标图床（{hit}），禁止复用原图")
    return errors, warnings


def run_rewrite(llm_call: Callable[[str], str], benchmark: dict, persona: dict,
                analysis: Optional[dict] = None, angle: Optional[str] = None) -> dict:
    """执行仿写 + schema 校验（失败自动重试一次）+ 原创度自检。

    返回 {title, content, tags, image_units, similarity, warnings}。
    analysis（可选）：note_analyze 拆解产出的结构规格——有则仿写按骨架逐单元同构、
    image_units 数量对标图卡结构；无则维持隐式拆解（向后兼容）。
    angle（可选）：指定仿写角度（/换角度 或语义评分 rewrite_angle/persona_hook）。
    """
    last_err: Optional[Exception] = None
    for _attempt in range(2):  # schema 失败重试一次（LLM 非确定性，重试显著提升通过率）
        raw = llm_call(build_rewrite_prompt(benchmark, persona, analysis, angle))
        result = parse_llm_output(raw)
        errors, warnings = validate_rewrite_output(result)
        if errors:
            last_err = ValueError("仿写输出未过 schema 校验：" + "；".join(errors))
            continue
        result["warnings"] = warnings
        original = (benchmark.get("description") or benchmark.get("content") or "")
        result["similarity"] = round(
            jaccard_ngram(original, result.get("content", "")), 3)
        return result
    raise last_err  # type: ignore[misc] —— 两轮均失败，向上抛详细原因


# ─── 小红书发布文案（LLM 排版，手机阅读习惯） ──────────────────────────

# 提示词外置（v1.2.1）：agent/prompts/xhs-copy.md——调提示词改文件，不改代码
XHS_COPY_PROMPT = load_prompt("xhs-copy")


def format_xhs_copy(llm_call: Callable[[str], str], result: dict) -> str:
    """把仿写稿排版成小红书发布文案（可直接复制发布）。

    LLM 排版失败时回落朴素模板（标题 + 正文 + 标签），不影响交付。
    """
    title = result.get("title", "")
    content = result.get("content", "")
    tags = " ".join(result.get("tags") or [])
    try:
        text = llm_call(XHS_COPY_PROMPT.format(title=title, content=content, tags=tags))
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text.strip()).strip()
        if text:
            return text
    except Exception:
        pass
    return f"{title}\n\n{content}\n\n{tags}".strip()


# ─── LLM 调用（OpenAI 兼容接口，config.llm 注入） ──────────────────────

def _post_chat(base_url: str, api_key: str, model: str, messages: list,
               temperature: float | None = 0.7, timeout: int = 300) -> str:
    """POST chat/completions（messages 任意结构，文本/多模态共用；temperature=None 不传）。"""
    payload = {"model": model, "messages": messages}
    if temperature is not None:
        payload["temperature"] = temperature
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base_url}/chat/completions", data=body, method="POST",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def _chat_once(base_url: str, api_key: str, model: str, prompt: str, timeout: int = 300) -> str:
    """单模型一次对话调用。部分模型（如 kimi-k3）不支持 temperature 参数，自动去参重试。"""
    messages = [{"role": "user", "content": prompt}]
    try:
        return _post_chat(base_url, api_key, model, messages, temperature=0.7)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore") if exc.fp else ""
        if "temperature" in detail:  # 该模型不支持 temperature，去参重试
            return _post_chat(base_url, api_key, model, messages, temperature=None)
        raise


def _chat_once_vision(base_url: str, api_key: str, model: str,
                      prompt: str, images_b64: list, timeout: int = 300) -> str:
    """多模态调用：文本 prompt + base64 图片列表（OpenAI 兼容 image_url 格式）。"""
    content = [{"type": "text", "text": prompt}] + [
        {"type": "image_url",
         "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        for b64 in images_b64]
    return _post_chat(base_url, api_key, model,
                      [{"role": "user", "content": content}])


def llm_call_factory(llm_config: dict) -> Callable[[str], str]:
    """构建同步 llm_call(prompt) -> str。

    支持模型优先级列表（config.llm.models，按序 fallback，如
    [qwen3.8-max, glm-5.3, deepseek-v4-pro]）或单模型（config.llm.model）。
    """
    base_url = (llm_config.get("base_url") or "").rstrip("/")
    api_key = llm_config.get("api_key") or os.environ.get("LLM_API_KEY", "")
    models = list(llm_config.get("models") or [])
    if llm_config.get("model"):
        models.append(llm_config["model"])
    if not (base_url and api_key and models):
        raise RuntimeError(
            "未配置仿写 LLM：请在 agent/config/config.yaml 的 llm 段填 base_url/api_key"
            " 及 models（优先级列表）或 model（单模型），OpenAI 兼容 chat/completions 接口")

    def llm_call(prompt: str) -> str:
        errors = []
        for model in models:  # 按优先级逐个尝试，成功即返回
            try:
                return _chat_once(base_url, api_key, model, prompt)
            except Exception as exc:  # noqa: BLE001 —— fallback 需吞掉单模型异常
                errors.append(f"{model}: {exc}")
        raise RuntimeError("全部 LLM 模型失败 -> " + " | ".join(errors))

    return llm_call


def llm_vision_call_factory(llm_config: dict) -> Optional[Callable[[str, list], str]]:
    """构建多模态 vision_call(prompt, [图片b64]) -> str。

    读取 config.llm.vision_model（须为多模态模型，如 qwen-vl / glm-4v / kimi-vision 系）。
    未配置返回 None——调用方降级为纯文字拆解，不阻断。
    """
    cfg = llm_config or {}
    base_url = (cfg.get("base_url") or "").rstrip("/")
    api_key = cfg.get("api_key") or os.environ.get("LLM_API_KEY", "")
    model = cfg.get("vision_model") or ""
    if not (base_url and api_key and model):
        return None

    def vision_call(prompt: str, images_b64: list) -> str:
        return _chat_once_vision(base_url, api_key, model, prompt, images_b64)

    return vision_call
