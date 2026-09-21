---
name: xhs-rewrite
description: >
  小红书爆款拆解仿写 Skill。对对标笔记做五层拆解（选题/标题/正文/视觉/数据），
  用「同构异题」策略仿写一篇新笔记，输出结构化 JSON（含内容单元 image_units），
  供后续 xhs-imagepack / xhs-video 复用。本 Skill 是生产流水线的入口。
  当用户 /确认 N 或粘贴小红书链接触发生产任务时调用此 Skill。
version: 1.0.0
author: xhs-travel-planner
tags:
  - xiaohongshu
  - rewrite
  - benchmark
  - travel
---

# xhs-rewrite Skill

## 触发条件

| 触发源 | 触发动作 |
|---|---|
| Router `_cmd_confirm` | /确认 N → 从选题清单取第 N 条进流水线 |
| Router `_cmd_manual_note` | 粘贴 xhslink.com / xiaohongshu.com 链接（Phase 1.5 抓取后入队） |
| 雷达 cron | 推送选题清单后用户回 /确认 N |

## 职责

1. 构建仿写提示词（参考 `agent/prompts/system-prompt.md`）
2. 调 LLM（OpenAI 兼容 chat/completions，按 config.llm.models 优先级 fallback）
3. 解析 LLM 输出 JSON（标题/正文/标签/图片单元）
4. 自检原创度：字符 3-gram Jaccard 相似度，阈值 0.30
5. 产出落盘 `workspace/{bot_id}/{uid}/{task_id}/rewrite.json`

## 执行步骤

### Step 1: 提示词构建

调 `pipeline/rewrite.py` 的 `build_rewrite_prompt(benchmark, persona, analysis)`，注入：
- 对标笔记字段：title / description|content / likes / collects / comments / heat
- persona：`pipeline/promptkit.py` 的 `load_soul` 组装——`config.yaml` persona.soul > `agent/SOUL.md`
- analysis（v1.2）：note_analyze 拆解产出的结构规格（正文骨架/图卡结构），有则注入
  「对标结构拆解」段，仿写逐单元同构、image_units 数量对齐图卡结构；无则 LLM 隐式拆解

提示词模板外置：`agent/prompts/rewrite.md`（promptkit 启动加载，调提示词改文件不改代码）

### Step 2: LLM 调用

调 `llm_call_factory(config.llm)` 构造的 `llm_call(prompt)`，按 `config.llm.models` 列表顺序 fallback：

```python
llm = rewrite_mod.llm_call_factory(config.get("llm") or {})
result = run_rewrite(llm, topic, config.get("persona") or {})
```

### Step 3: 输出解析

调 `parse_llm_output(text)` 提取 JSON 块，匹配顺序：
1. ` ```json ... ``` ` 代码块
2. 裸 JSON `{ ... }`

### Step 4: 原创度自检

```python
sim = jaccard_ngram(original_text, result["content"])
if sim > ORIGINALITY_THRESHOLD:  # 0.30
    raise RuntimeError(f"原创度自检未通过（相似度 {sim:.2f} > 0.30），需人工复写")
```

### Step 5: 产出落盘

```python
(work_dir / "rewrite.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
```

## 输出 Schema

```json
{
  "title": "仿写标题（20 字内，沿用对标钩子类型）",
  "content": "仿写正文（结构骨架与对标一致，主题细节全部替换，含结尾服务钩子）",
  "tags": ["#标签1", "#标签2", "#标签3"],
  "image_units": [
    {"role": "cover", "prompt": "封面底图生图提示词（无文字，中文，旅行摄影风格）"},
    {"role": "page", "prompt": "内页底图生图提示词"}
  ],
  "similarity": 0.035
}
```

## 依赖工具

- `pipeline/rewrite.py`：核心执行
- `pipeline/note_analyze.py`：上游拆解引擎（v1.2 前置环节，产出 analysis 注入本 Skill）
- `config.llm`：模型优先级 + key + base_url
- 提示词：`agent/prompts/rewrite.md`（模板）+ `agent/prompts/system-prompt.md`（总纲）

## 异常处理

| 场景 | 处理方式 |
|---|---|
| 未配置 llm base_url/api_key/models | 抛 RuntimeError，提示用户填 config |
| LLM 输出无 JSON 块 | 抛 ValueError，提示重试 |
| 全部 LLM 模型失败 | 抛 RuntimeError，列出每家错误明细 |
| 部分模型不支持 temperature | 自动去参重试（`_chat_once` 已实现） |
| 相似度 > 0.30 | 抛 RuntimeError，不交付，需人工复写 |

## 与其他 Skill 的协作

```
用户 /确认 N 或粘贴链接
        ↓
  xhs-rewrite（本 Skill）→ rewrite.json
        ↓
  ┌────────────────────────────┐
  ↓                            ↓
xhs-imagepack              xhs-video
（读 rewrite.json 编排版式）  （读 rewrite.json + layout 分镜）
```

## 示例

**输入**：雷达选题清单第 1 条
```json
{"note_id": "...", "title": "国庆带娃去南京，3天赏秋路线直接抄", "description": "...", "likes": 8500}
```

**输出**：
```json
{
  "title": "国庆带娃去南京，怕人多又想看秋色？这份三天的赏秋路线直接抄作业",
  "content": "三天这样排：第一天钟山赏秋线...",
  "tags": ["#南京旅行", "#带娃出行", "#赏秋路线"],
  "image_units": [...],
  "similarity": 0.035
}
```

实测耗时：kimi-k3 约 126s；glm-5.3 约 82s。
