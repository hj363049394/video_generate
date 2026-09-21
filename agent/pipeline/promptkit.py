"""提示词外置加载器（v1.2.1：Hermes 结构落地——prompts/ 文件即单一可信源）

所有 LLM 提示词外置于 agent/prompts/*.md，代码零模板副本；
调提示词只改文件（重启生效），不动 .py——这是 SKILL.md / system-prompt.md
自 v1.1 起声称的加载链路，此前从未接线，本模块使其真实生效：

  analyze.md        爆款结构拆解（note_analyze 消费）
  describe-image.md 多模态单图描述（note_analyze 消费）
  rewrite.md        同构异题仿写（rewrite 消费）
  layout.md         卡片 DSL 版式编排（imagepack 消费）
  xhs-copy.md       小红书发布文案排版（rewrite 消费）
  system-prompt.md  总纲基准（角色/规则/边界，供人阅读对齐，不参与 format）

模板语法：str.format 风格——{var} 占位；字面花括号（JSON 示例等）写 {{ }}。
文件头部 <!-- --> 注释为使用说明，加载时自动剥离。

人设（SOUL.md）同样在此接线：persona.soul（config 显式值）> agent/SOUL.md
> RuntimeError（此前实际生效的是 rewrite.py 内置人设副本，SOUL.md 形同虚设）。
"""
from __future__ import annotations

import re
from pathlib import Path

_AGENT_DIR = Path(__file__).resolve().parent.parent
PROMPTS_DIR = _AGENT_DIR / "prompts"
SOUL_FILE = _AGENT_DIR / "SOUL.md"

# 兜底：SOUL.md 也缺失时的人设（保证流水线可跑，但日志告警提示恢复）
_FALLBACK_SOUL = (
    "提供旅游行程规划与定制服务的旅行家：内容强化规划感（天数/预算/节奏表格化），"
    "对标讲\"去哪\"仿写强化\"怎么排\"，结尾固定服务钩子"
    "\"评论区留言人数/天数/预算，帮你出定制行程\"，口吻专业但不端着，像懂行的朋友给建议。")


# 兜底：SOUL.md 也缺失时的末卡 CTA（与 SOUL.md「默认 CTA」段保持一致，仅最后防线）
_FALLBACK_CTA = {"line1": "每次出发都值得认真规划",
                 "line2": "评论区报：人数 / 天数 / 预算",
                 "line3": "帮你出定制行程",
                 "account": "关注 @ 行程规划旅行家"}
_CTA_LINE_RE = re.compile(r"^-\s*(line1|line2|line3|account)\s*[:：]\s*(.+?)\s*$", re.M)
_CTA_SECTION_RE = re.compile(r"##\s*默认\s*CTA.*?\n(.*?)(?=\n##\s|\Z)", re.S)


def _read_text(path: Path) -> str:
    """读文件（utf-8-sig 兼容 BOM），剥离 <!-- --> 说明注释，返回净模板。"""
    text = path.read_text(encoding="utf-8-sig")
    return re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()


def load_prompt(name: str) -> str:
    """加载 agent/prompts/{name}.md 为提示词模板（净文本，含 {var} 占位）。

    文件即唯一真相：缺失/为空直接抛 RuntimeError（不回退代码副本，
    避免文件与代码双源漂移——v1.2 前的病根）。
    """
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise RuntimeError(
            f"提示词文件缺失：{path}\n"
            f"promptkit 不内置代码副本——请从 git 恢复该文件（git checkout -- {path}）")
    text = _read_text(path)
    if not text:
        raise RuntimeError(f"提示词文件为空：{path}")
    return text


def load_soul(persona: dict | None = None) -> str:
    """人设单一来源：persona.soul（config）> agent/SOUL.md > 内置兜底。

    SOUL.md 头部说明行（> 引用块）对人设注入无害，全文注入；
    「## 默认 CTA」段属排版层兜底数据（load_cta 消费），注入前剥离避免噪音。
    """
    explicit = ((persona or {}).get("soul") or "").strip()
    if explicit:
        return explicit
    if SOUL_FILE.exists():
        text = SOUL_FILE.read_text(encoding="utf-8-sig").strip()
        if text:
            return _CTA_SECTION_RE.sub("", text).strip()
    return _FALLBACK_SOUL


def load_cta(persona: dict | None = None) -> dict:
    """末卡 CTA 兜底值单一来源（P1-2）：config persona.cta > SOUL.md「默认 CTA」段 > 内置兜底。

    imagepack.plan_layout 末卡缺 cta 时用此值补全——改 CTA 文案只改 SOUL.md 或 config，
    不再动 imagepack.py 代码（v1.2.1 前的 DEFAULT_CTA 代码副本已移除）。
    """
    explicit = (persona or {}).get("cta")
    if isinstance(explicit, dict):
        merged = dict(_FALLBACK_CTA)
        merged.update({k: str(v).strip() for k, v in explicit.items()
                       if k in _FALLBACK_CTA and str(v).strip()})
        if merged.get("line1") and merged.get("line2"):
            return merged
    if SOUL_FILE.exists():
        m = _CTA_SECTION_RE.search(SOUL_FILE.read_text(encoding="utf-8-sig"))
        if m:
            pairs = dict(_CTA_LINE_RE.findall(m.group(1)))
            if pairs.get("line1") and pairs.get("line2"):
                merged = dict(_FALLBACK_CTA)
                merged.update(pairs)
                return merged
    return dict(_FALLBACK_CTA)
