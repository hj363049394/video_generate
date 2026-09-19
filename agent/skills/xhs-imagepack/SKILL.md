---
name: xhs-imagepack
description: >
  小红书图文卡片生成 Skill。把 xhs-rewrite 产出的仿写稿编排成 4 张图文卡片（含视频分镜旁白）。
  两步式数据驱动：① LLM 版式编排 plan_layout → ② 双通道生图 + PIL 排版 generate_pack。
  产出 1242×1656（3:4）4 张：cover / itinerary / spots / stay_hook，供 xhs-video 复用为视频帧。
  当 xhs-rewrite 完成后由 router 顺序调用。
version: 1.0.0
author: xhs-travel-planner
tags:
  - xiaohongshu
  - imagepack
  - layout
  - pil
  - seedream
---

# xhs-imagepack Skill

## 触发条件

| 触发源 | 触发动作 |
|---|---|
| Router `_run_task` | xhs-rewrite 完成后顺序调用，传 rewrite_result + ImageGenRouter |
| 用户 `/生图 描述` | 跳过本 Skill，直接调 imagegen（调试用） |

## 职责

1. **版式编排（LLM）**：把仿写稿编排成 4 张卡的版式数据 + 5 段视频分镜旁白
2. **图文同源铁律保证**：第 N 段旁白只讲第 N 张图上承载的内容，不得串图
3. **底图生图**：走 imagegen 双通道（ark 主 / redfox_gpt 备 / redfox_doubao 备）
4. **PIL 排版渲染**：底图 + 信息卡层合成 1242×1656 成品图

## 执行步骤

### Step 1: 版式编排（plan_layout）

调 `pipeline/imagepack.py` 的 `plan_layout(llm_call, rewrite_result)`：

```python
layout = imagepack.plan_layout(llm, result)
(work_dir / "layout.json").write_text(
    json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
```

LLM 提示词核心约束：
- **图文同源铁律**：第 N 段旁白只讲第 N 张图上承载的内容
- **排版密度**：每行不超 22 字，内容行最多 2 行
- **分镜旁白**：去书面化、短句、每段 30-60 字

### Step 2: 底图生图 + PIL 排版（generate_pack）

调 `pipeline/imagepack.py` 的 `generate_pack(layout, gen, out_dir, assets_dir)`：

```python
pack = imagepack.generate_pack(
    layout, self.gen, str(work_dir / "images"), self.assets_dir)
```

内部流程：
1. 从 layout.image_prompt 取每张图的生图提示词
2. 调 `gen.generate(prompt, out_path, "portrait", ref_images=None)` 走双通道
3. PIL 加载底图 → 顶部横幅 / 信息卡层 / 文字渲染 → 合成成品图

### Step 3: 输出 Schema

```python
{
  "cards": ["cover.jpg", "itinerary.jpg", "spots.jpg", "stay_hook.jpg"],
  "video_frames": ["frame_0.jpg", "frame_1.jpg", "frame_2.jpg", "frame_3.jpg", "frame_4.jpg"]
}
```

## 输出 Layout Schema

```json
{
  "cover": {"image_prompt": "封面底图提示词", "title": "大标题", "subtitle": "副标题"},
  "itinerary": {"image_prompt": "路线卡顶部横幅提示词", "title": "3 日路线总表",
                "days": [{"tag": "Day 1", "theme": "...", "content": "...", "traffic": "..."}],
                "footer": "节奏原则"},
  "spots": {"title": "重点点位 · 最佳时段",
            "spots": [{"image_prompt": "...", "name": "...", "rows": [...]}]},
  "stay_hook": {"image_prompt": "住宿卡顶部横幅提示词",
                "rules": [{"num": "①", "name": "...", "desc": "..."}],
                "cta": {"line1": "...", "line2": "...", "line3": "...", "account": "关注 @..."}},
  "narrations": ["镜头1旁白", "镜头2旁白", "镜头3旁白", "镜头4旁白", "镜头5旁白"]
}
```

## 视觉规范（继承 POC 定稿）

| 维度 | 规格 |
|---|---|
| 画布 | 1242×1656（3:4） |
| 边距 | 66px |
| 主色 | BG(251,246,238) / CARD(255,255,255) / AMBER(180,83,9) / MAPLE(194,65,12) |
| 字体 | NotoSansSC-Regular / NotoSansSC-Bold |
| 圆角 | 信息卡 20px |

## 图组标准结构

| # | role | 内容 |
|---|---|---|
| 1 | cover | 标题大字 + Day1/2/3 预告（开场钩子） |
| 2 | itinerary | 顶部横幅 + 3 日路线总表 |
| 3 | spots | 3 个点位行（实景小图 + 文字） |
| 4 | stay_hook | 顶部横幅 + 住宿三原则 + 服务钩子 |

## 依赖工具

- `pipeline/imagepack.py`：核心执行（plan_layout + generate_pack）
- `pipeline/imagegen.py`：双通道生图 ImageGenRouter
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
| 全部生图通道失败 | 抛 ImageGenError，列出每家错误明细 |
| 字体文件缺失 | 自动从 `poc/production/assets` 兜底加载 |
| PIL 未安装 | 提示 `pip install pillow` |

## 与其他 Skill 的协作

```
xhs-rewrite → rewrite.json
     ↓
xhs-imagepack（本 Skill）
     ↓
  ┌──────────────────┐
  ↓                  ↓
deliver（4 图直发）  xhs-video
                    （复用 video_frames + narrations）
```

## 示例

**输入**：rewrite.json（kimi-k3 仿写产出）
**输出**：
- 4 张 1242×1656 图文卡：cover.jpg / itinerary.jpg / spots.jpg / stay_hook.jpg
- 5 张视频帧：frame_0.jpg ~ frame_4.jpg
- layout.json（编排数据，含 narrations）

实测耗时（端到端，沙箱）：LLM 版式编排 193s + ark 生 6 底图 + 4 卡渲染 141s = **总约 5.5 分钟**。
