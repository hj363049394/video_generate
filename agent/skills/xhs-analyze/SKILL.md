---
name: xhs-analyze
description: >
  爆款笔记显式拆解 Skill（v1.2 拆解驱动仿写的核心前置）。对对标笔记做五层拆解，
  产出「结构规格」JSON：content_structure（标题公式/正文骨架逐单元/口吻/标签策略）+
  image_structure（逐张图卡 kind/文字排版/风格）+ style_summary。有图集且配置
  llm.vision_model 时多模态逐张看图；无图从正文分段推断。拆解失败自动降级为
  无拆解仿写（隐式拆解），不阻断主线。在 router._run_task 第⓪步调用。
version: 1.2.0
author: xhs-travel-planner
tags:
  - xiaohongshu
  - analyze
  - benchmark
  - structure
---

# xhs-analyze Skill

## 触发条件

| 触发源 | 触发动作 |
|---|---|
| Router `_run_task` ⓪ | /确认 N、粘贴链接、/仿写 内容 三种入队路径统一先跑拆解 |

## 职责

1. 下载对标图集（拉模式 topic.images，最多 9 张；失败跳过单张）
2. 多模态看图（可选）：config.llm.vision_model 逐张描述（describe-image.md）
3. LLM 五层拆解 → 结构规格 JSON（analyze.md）
4. 基础校验：image_structure ≥1 张、skeleton ≥1 单元，否则抛错（router 捕获降级）

## 执行步骤

```python
from pipeline import note_analyze
analysis = note_analyze.run_analyze(topic, work_dir, config.llm)
# 落盘 work_dir/analysis.json
```

内部链路：`download_images` → `describe_images`（可选）→ `build_analyze_prompt`
（agent/prompts/analyze.md，占位 {title}{content}{likes}{collects}{comments}{image_section}）
→ `llm_call` → `parse_llm_output` → 校验。

## 输出 Schema（analysis.json）

```json
{
  "content_structure": {
    "title_pattern": "标题钩子类型与公式",
    "skeleton": [{"unit": "钩子开场", "desc": "该单元的作用与写法"}],
    "tone": "口吻与视角",
    "tags_strategy": "标签策略"
  },
  "image_structure": [
    {"idx": 1, "kind": "full_photo_cover", "role": "cover",
     "desc": "图上有什么", "text_layout": "文字排版形态", "style": "色调与摄影风格"}
  ],
  "style_summary": "整体视觉调性一句话"
}
```

kind 枚举：`full_photo_cover` / `list_card` / `rows_card` / `lines_quote` / `mixed`。

## 降级链路

| 场景 | 行为 |
|---|---|
| 选题无图集（雷达确认/直发） | Router 先经红狐详情接口补图（`_enrich_topic_images`），仍无则封面/文字拆解 |
| 图片下载失败 | 跳过单张，其余继续 |
| 未配置 vision_model | 纯文字拆解（从正文分段推断图卡，desc 标注「推断」） |
| 看图单张失败 | 占位描述「该图视觉信息缺失」 |
| LLM 输出缺 image_structure/skeleton | 抛 ValueError → router 降级无拆解仿写 |

## 依赖工具

- `pipeline/note_analyze.py`：核心执行
- `pipeline/promptkit.py`：analyze.md / describe-image.md 加载
- `config.llm`：chat 模型 + 可选 vision_model

## 与其他 Skill 的协作

```
note_fetch（拉模式详情）/ 雷达选题 / 用户直发内容
        ↓
xhs-analyze（本 Skill）→ analysis.json
        ↓                         ↓
xhs-rewrite（骨架逐单元同构）  xhs-imagepack（第 N 卡对标第 N 图卡）
```
