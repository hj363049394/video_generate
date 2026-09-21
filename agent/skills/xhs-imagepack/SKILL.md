---
name: xhs-imagepack
description: >
  小红书图文卡片生成 Skill。把仿写稿按「爆款拆解结构」编排成卡片 DSL（3-6 张，
  第 N 张卡对标爆款第 N 张图卡的 kind/排版/风格——v1.2 拆解驱动，不套固定模板），
  再走双通道生图 + PIL 通用块渲染出成品卡。产出与分镜旁白等长，供 xhs-video 复用。
  在 xhs-rewrite 完成后由 router 顺序调用。
version: 1.2.0
author: xhs-travel-planner
tags:
  - xiaohongshu
  - imagepack
  - card-dsl
  - pil
  - seedream
---

# xhs-imagepack Skill

## 触发条件

| 触发源 | 触发动作 |
|---|---|
| Router `_run_task` | xhs-rewrite 完成后顺序调用，传 rewrite_result + analysis + ImageGenRouter |
| 用户 `/生图 描述` | 跳过本 Skill，直接调 imagegen（调试用） |

## 职责

1. **版式编排（LLM，拆解驱动）**：按 `agent/prompts/layout.md` 把仿写稿 + 拆解结构编排成卡片 DSL——
   第 N 张卡对标爆款第 N 张图卡，禁止套统一模板、禁止自行发明版式
2. **图文同源铁律保证**：narrations 与 cards 等长，第 N 段旁白只讲第 N 张卡上承载的内容
3. **底图生图**：卡级底图（full 竖版 / banner 横幅）+ rows 块行级小图，走 imagegen 双通道
4. **PIL 渲染**：通用块类型渲染（list/rows/lines/cta × full/banner 两种模式自由组合），
   合成 1242×1656 成品卡

## 执行步骤

### Step 1: 版式编排（plan_layout）

调 `pipeline/imagepack.py` 的 `plan_layout(llm_call, rewrite_result, analysis)`：

```python
layout = imagepack.plan_layout(llm, result, analysis)
(work_dir / "layout.json").write_text(
    json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
```

校验（不合法抛 ValueError）：
- cards 3-6 张，name 唯一非空、每张含 image_prompt
- image_mode ∈ {full, banner}；blocks 类型 ∈ {list, rows, lines, cta}
- narrations 与 cards 等长
- 末卡缺 cta 块时自动补人设服务钩子（软校验）

拆解 kind → DSL 映射（无拆解数据时从正文分段推断：叙事段 full+lines / 清单段 banner+list）：

| 拆解 kind | DSL 版式 |
|---|---|
| full_photo_cover | `full` + title/subtitle/pill |
| list_card | `banner` + `list` 块 |
| rows_card | `banner` + `rows` 块 |
| lines_quote | `full` + `lines` 块 |
| mixed | 按内容择优组合 |

提示词模板外置：`agent/prompts/layout.md`（promptkit 启动加载，调提示词改文件不改代码）。

### Step 2: 底图生图 + PIL 渲染（generate_pack）

调 `pipeline/imagepack.py` 的 `generate_pack(layout, gen, out_dir, assets_dir)`：

```python
pack = imagepack.generate_pack(
    layout, self.gen, str(work_dir / "images"), self.assets_dir)
```

内部流程：
1. 每张卡按 image_mode 生底图：full → portrait 竖版整页 / banner → landscape 顶部横幅
2. rows 块带 image_prompt 的行生 382px 行级小图（图文行，最多 2 行）
3. PIL 渲染：底图 + 块内容（白卡内容区/大字标题/金句行/CTA 钩子）
4. 文件名 `{序号}_{name}.jpg`（序号保证图集顺序）

### Step 3: 输出 Schema

```python
{
  "cards": ["1_cover.jpg", "2_tips.jpg", "...", "N_ending.jpg"],   # 3-6 张
  "video_frames": [...]   # 与 cards 同源（图文同源，视频帧 = 卡片）
}
```

## 输出 Layout Schema（卡片 DSL）

```json
{
  "cards": [
    {"name": "cover", "image_mode": "full", "image_prompt": "封面底图提示词",
     "title": "大标题", "subtitle": "副标题", "pill": "角标短语", "blocks": []},
    {"name": "tips", "image_mode": "banner", "image_prompt": "横幅提示词",
     "title": "卡标题",
     "blocks": [
       {"type": "list", "items": [{"tag": "①", "label": "短语", "text": "说明"}]},
       {"type": "rows", "items": [{"label": "标签", "text": "内容", "image_prompt": "可选小图"}]},
       {"type": "lines", "items": ["叙事行/金句"]},
       {"type": "cta", "line1": "过渡句", "line2": "评论区报：人数 / 天数 / 预算",
        "line3": "钩子", "account": "关注 @ 行程规划旅行家"}
     ]}
  ],
  "narrations": ["镜头1旁白", "...", "镜头N旁白"]
}
```

约束：full 卡最多 lines + cta 两种块；banner 卡内容块 1-2 个、cta 最多 1 个；
banner 卡全部内容块合计不超 6 个条目；每行不超 22 字。

## 视觉规范（继承 POC 定稿）

| 维度 | 规格 |
|---|---|
| 画布 | 1242×1656（3:4） |
| 边距 | 66px |
| 主色 | BG(251,246,238) / CARD(255,255,255) / AMBER(180,83,9) / MAPLE(194,65,12) |
| 字体 | NotoSansSC-Regular / NotoSansSC-Bold |
| 圆角 | 信息卡 20px |

## 依赖工具

- `pipeline/imagepack.py`：核心执行（plan_layout + generate_pack + Renderer）
- `pipeline/imagegen.py`：双通道生图 ImageGenRouter
- `pipeline/promptkit.py`：layout.md 加载（提示词外置单一可信源）
- `assets/fonts/NotoSansSC-*.otf`：字体资产（默认指向 `poc/production/assets`）
- LLM（与 xhs-rewrite 共用 config.llm）

## 生图通道

| 通道 | Provider | 模型 | 状态 |
|---|---|---|---|
| ark | `ArkImageGen` | doubao-seedream-5.0-lite | ✅ 实测通过 |
| redfox_gpt | `RedfoxGptImageGen` | GPT-Image-2 | ⏸ 3203 仅付费 |
| redfox_doubao | `RedfoxDoubaoLiteImageGen` | 豆包转发 | ⏸ 3203 仅付费 |

切换通道：`/生图通道 ark|gpt|doubao`

## 异常处理

| 场景 | 处理方式 |
|---|---|
| LLM 版式编排无 JSON 输出 | 抛 ValueError，提示重试 |
| cards 数量/块类型/等长校验失败 | 抛 ValueError，提示重试 |
| 全部生图通道失败 | 抛 ImageGenError，列出每家错误明细 |
| 字体文件缺失 | 自动从 `poc/production/assets` 兜底加载 |
| PIL 未安装 | 提示 `pip install pillow` |

## 与其他 Skill 的协作

```
note_analyze → analysis.json（结构规格）
     ↓
xhs-rewrite → rewrite.json（结构对标仿写稿）
     ↓
xhs-imagepack（本 Skill：拆解驱动卡片 DSL → 3-6 张成品卡）
     ↓
  ┌──────────────────┐
  ↓                  ↓
deliver（N 图直发）  xhs-video
                    （复用 video_frames + narrations，等长）
```

## 示例

**输入**：rewrite.json（仿写稿）+ analysis.json（爆款拆解：5 张图卡结构）
**输出**：
- 3-6 张 1242×1656 图文卡（`1_cover.jpg` … `N_ending.jpg`，版式逐张对标爆款）
- narrations（与卡片等长的分镜旁白）
- layout.json（卡片 DSL，含 narrations）
