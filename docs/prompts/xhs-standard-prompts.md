# 小红书内容生产标准提示词 v1.2

> 来源：2026-09 POC 全链路验证 → v1.2 拆解驱动重构（2026-09-21）
> 用途：「拆解 → 仿写 → 版式编排 → 生图 → 视频」全环节提示词的设计基准。
> **落地形态（v1.2.1）**：可执行模板外置于 `agent/prompts/*.md`，由 `pipeline/promptkit.py`
> 启动加载（文件即单一可信源，代码零模板副本）；本文档是设计与调优基准，二者保持同步。
> 数据流总览：①爆款拆解（结构规格 JSON）→ ②结构对标仿写 → ③卡片 DSL 版式编排（含分镜旁白）
> → ④配图生图 → ⑤视频合成。

---

## 使用流程

```
对标笔记数据(标准JSON) ──→ ①爆款拆解 ──→ analysis.json（结构规格）
                                          │ + 账号人设（load_soul）
                                          ▼
                              ②结构对标仿写 ──→ rewrite.json（含 image_units）
                                          │
                                          ▼
                              ③卡片 DSL 版式编排 ──→ layout.json（cards + narrations）
                                          │
                    ┌─────────────────────┴─────────────────────┐
                    ▼                                           ▼
          ④配图生图(卡级底图/行级小图)                    ⑤视频合成
                    │                                           │
                    ▼                                           ▼
              3-6 张图文卡片（1242×1656）             TTS → Ken Burns → 合成
```

拆解失败自动降级：无 analysis 时，②由 LLM 隐式拆解仿写、③从正文分段推断图卡结构
（叙事段 full+lines / 清单段 banner+list）——不阻断主线。

---

## 提示词 ① · 爆款五层拆解（结构规格版）

**模板文件**：`agent/prompts/analyze.md`（多模态单图描述：`agent/prompts/describe-image.md`）
**使用时机**：拿到对标笔记结构化数据后（router `_run_task` 第⓪步，失败降级）。
**输入变量**：`{title}` `{content}` `{likes}` `{collects}` `{comments}` `{image_section}`。

```text
你是小红书爆款拆解专家。对下面这篇爆款笔记做五层拆解（选题/标题/正文/视觉/数据层），
输出「可仿写的结构规格」——后续仿写将严格按这个结构逐项对标，所以规格必须具体到可执行。

## 对标笔记
标题：{title}
正文：{content}
互动：赞 {likes} / 藏 {collects} / 评 {comments}
{image_section}    ← 有图集且配置 vision_model：逐张多模态实测描述；
                      无图：按正文分段推断（desc 标注「推断」）

## 输出（严格 JSON，无其他文字）
{
  "content_structure": {
    "title_pattern": "标题钩子类型与公式（如：反差对比+具体数字+场景锚点）",
    "skeleton": [ {"unit": "钩子开场", "desc": "该单元的作用与写法（30字内）"} ],
    "tone": "口吻与视角（20字内）",
    "tags_strategy": "标签策略（20字内）"
  },
  "image_structure": [
    {"idx": 1, "kind": "full_photo_cover", "role": "cover",
     "desc": "图上有什么（30字内）", "text_layout": "文字排版形态（20字内）", "style": "色调与摄影风格（20字内）"}
  ],
  "style_summary": "整体视觉调性一句话"
}
```

kind 枚举（拆解→版式映射基准）：`full_photo_cover`（整页照片+大字标题）/ `list_card`（清单条目卡）/
`rows_card`（信息行卡）/ `lines_quote`（整页图+金句）/ `mixed`（混合）。

**铁律**：skeleton 单元数与正文实际段落数一致；image_structure 张数与实际图数一致；
不复述笔记内容，只输出结构规格。

---

## 提示词 ② · 结构对标仿写

**模板文件**：`agent/prompts/rewrite.md`
**使用时机**：拆解完成后。**输入变量**：`{analysis_section}`（①产出注入段，无拆解为空）
`{benchmark_title}` `{benchmark_content}` `{likes}` `{collects}` `{comments}` `{heat}` `{persona_soul}`。
**人设组装**：`promptkit.load_soul`——`config.yaml` persona.soul > `agent/SOUL.md`。

```text
你是小红书爆款拆解仿写专家。先对对标笔记做五层拆解（选题/标题/正文/视觉/数据层），
再用「同构异题」策略仿写：保留结构骨架、钩子模式、排版节奏、标签策略；替换主题细节、案例、数据、口吻。
{analysis_section}    ← 标题公式 / 正文骨架（逐单元同构，单元数一致）/ 图卡结构
                        （image_units 数量与之一致，每张对标其 kind/风格）/ 整体调性
## 对标笔记
标题：{benchmark_title}
正文：{benchmark_content}
互动：赞 {likes} / 藏 {collects} / 评 {comments}
热度：{heat}

## 人设（旅行家定位）
{persona_soul}

## 输出（严格 JSON）
{
  "title": "仿写标题（沿用对标钩子类型，20 字内）",
  "content": "仿写正文（结构骨架与对标一致，主题细节全部替换，含结尾服务钩子）",
  "tags": ["#标签1", "#标签2", "#标签3"],
  "image_units": [
    {"role": "cover", "prompt": "封面底图生图提示词（无文字，中文，旅行摄影风格）"},
    {"role": "page", "prompt": "内页底图生图提示词"}
  ]
}
```

**仿写规则（同构异题）**：保留结构骨架/钩子模式/段落节奏/标签策略；替换幅度大到"一眼是新内容"
但结构对齐精确到段；软广段一律删除替换为人设专业价值；结尾固定服务钩子；原创红线——
不复用原文连续 8 字以上片段（程序自检：字符 3-gram Jaccard ≤ 0.30）。

---

## 提示词 ③ · 卡片 DSL 版式编排（v1.2 新增，拆解驱动）

**模板文件**：`agent/prompts/layout.md`
**使用时机**：仿写完成后（imagepack `plan_layout(llm, result, analysis)`）。
**输入变量**：`{title}` `{content}` `{tags}` `{analysis_section}`（爆款图卡结构对标基准段）。

```text
你是小红书图文排版师。把仿写稿排成卡片 DSL——每张卡的版式必须对标
拆解引擎产出的爆款图卡结构（第 N 张对第 N 张），禁止套统一模板，禁止自行发明版式。

## 卡片 DSL 规格
- cards 3-6 张；第 N 张对标拆解 image_structure 第 N 张
  （>6 张合并同类内容项，<3 张按内容需要补足）
- 每张卡：name（英文短名）/ image_mode（full 整页底图｜banner 顶部横幅+白卡内容区）/
  image_prompt（对标该张拆解 style 与 desc，旅行摄影，无人物无文字）/
  title / subtitle（full 用）/ pill（仅封面）/ blocks（内容块数组）
- 四种块类型：
  list   条目清单（tag+label+text）
  rows   信息行（label+text，可选行级 382px 小图，最多 2 行）
  lines  金句/叙事行（最多 3 行）
  cta    人设服务钩子（line1/line2/line3/account）
- 约束：full 卡最多 lines+cta；banner 卡内容块 1-2 个、cta 最多 1 个、
  全部内容块合计不超 6 个条目；每行不超 22 字

## 输出（严格 JSON）
{
  "cards": [ {...} ],
  "narrations": ["镜头1旁白", "镜头N旁白"]   ← 与 cards 等长（图文同源，不串图）
}
```

**kind → DSL 映射**：full_photo_cover→full+title/subtitle/pill；list_card→banner+list；
rows_card→banner+rows；lines_quote→full+lines；mixed→按内容择优组合。
末卡缺 cta 时程序自动补人设钩子（软校验）。

---

## 提示词 ④ · 配图生图（Seedream 文生图 / 图生图）

**给生图模型**（非 LLM）。**使用时机**：卡片 DSL 确定后，为每张卡生成底图。

### 4a. 封面·图生图（对标封面要素替换，首选）

```text
参考这张图的构图、色调和画面氛围，将场景替换为{目标场景描述}：{场景要素1}，{场景要素2}，{场景要素3}，温暖午后光线，旅行摄影风格，竖版构图，画面干净，无人物，无文字，无水印
```

- `{目标场景描述}`：仿写内容的核心地标/主体（如"南京明孝陵石象路秋景"）
- `{场景要素}`：具体视觉锚点（如"古石像分列道路两侧、金色梧桐树成荫、地面铺满落叶"）
- 参考图：对标笔记封面原图 URL
- 尺寸：1728x2304（3:4）；模型名 `doubao-seedream-5.0-lite`（Agent Plan 专属命名，最小 3686400 像素）

### 4b. 文生图（卡级底图 / banner 横幅 / rows 行级小图）

```text
{场景主体描述}，{氛围与光线}，{风格锚点}，{构图方向}，无人物，无文字，无水印
```

v1.2 双模式：full 卡 → 竖版整页底图；banner 卡 → 横幅底图（顶部 382px 高，等比裁切）；
rows 块行级小图 → 382x382 方图（图文行）。

风格锚点按赛道选用（保持全组图统一）：

| 赛道 | 风格锚点 |
|---|---|
| 旅行·秋色 | 金色梧桐/银杏，暖秋色调，温暖午后光线，旅行摄影风格 |
| 旅行·海岛 | 阳光沙滩，清澈海水，明亮通透色调，度假感旅拍风格 |
| 美食 | 食材质感特写，暖光，日系清新风格 |
| 城市探索 | 街景人文，生活气息，自然光纪实风格 |

### 4c. 生图铁律

1. **中文文字一律不让 AI 画**——AI 只出无字底图，所有标题/文案由排版层（PIL）渲染，规避错字变形
2. 全组图共用同一组风格锚点，色调统一
3. 信息密度高的内容（路线表/点位卡/原则清单）用排版卡片承载，AI 图只做横幅与情绪图

---

## 提示词 ⑤ · 视频合成参数（图文同源）

**使用时机**：卡片 + narrations 产出后。v1.2 起分镜旁白由提示词③随卡片 DSL 一并产出
（narrations 与 cards 等长），视频环节不再单独调 LLM。

### 口播文案规则（提示词③内嵌）

1. 去书面化：删 emoji、删标签符号，短句，口语连接词
2. 每镜头 1 个信息组，30~60 字，超过拆镜头
3. 开头 3 秒必须有钩子（痛点/悬念/承诺）
4. 结尾固定服务钩子（对齐账号变现路径）
5. 图文同源铁律：每段旁白只讲对应卡片上呈现的内容，不串图

### 合成参数标准（非模型输入，供脚本执行）

- 音色：清新女声 zh_female_qingxinnvsheng_uranus_bigtts（旅行赛道验证款；备选清单见附B）
- Ken Burns：奇偶镜头交替缓推(1.0→1.18)/缓拉(1.18→1.0)
- 转场：crossfade 0.5s（音频同步 acrossfade）
- BGM：轻快尤克里里/原声吉他类，音量 16%，首尾 1.5s/2.2s 淡入淡出
- 每镜头时长 = 旁白时长 + 0.9s 呼吸感；整体首尾 0.5s 淡入淡出
- 镜头数 = 卡片数（3-6，随 DSL）

---

## 附录 A · 变量与输入 Schema

**对标笔记 JSON（提示词①输入）**：note_id / url / title / content / cover_image / images[] / tags[] / likes / collects / comments / shares / published_at / author{name, followers} / top_comments[]（手动导出或红狐 `get_note_detail` 产出）。

**结构规格 analysis.json（①产出 → ②③输入）**：content_structure{title_pattern, skeleton[], tone, tags_strategy} / image_structure[{idx, kind, role, desc, text_layout, style}] / style_summary。

**账号人设（提示词②输入）**：`config.yaml` persona.soul > `agent/SOUL.md`（promptkit.load_soul 组装）。

**多模态看图（可选）**：`config.llm.vision_model`（如 qwen-vl-max / glm-4v）；留空则纯文字拆解。

## 附录 B · 已验证音色清单（doubao-seed-tts-2.0 / seed-tts-2.0 资源）

| 音色 | voice_type | 适用 |
|---|---|---|
| 清新女声 ✅当前默认 | zh_female_qingxinnvsheng_uranus_bigtts | 文艺旅行 vlog |
| 邻家女孩 | zh_female_linjianvhai_uranus_bigtts | 亲切分享 |
| 爽快思思 | zh_female_shuangkuaisisi_uranus_bigtts | 攻略节奏感 |
| 知性灿灿 | zh_female_cancan_uranus_bigtts | 知性规划师 |
| 阳光青年 | zh_male_yangguangqingnian_uranus_bigtts | 活力男声 |
| 渊博小叔 | zh_male_yuanboxiaoshu_uranus_bigtts | 带娃旅行博主 |

兼容规则：仅 `*_uranus_bigtts`（2.0 系）音色可用于 seed-tts-2.0 资源；`*_moon_bigtts`（1.0 系）会报 55000000。

## 附录 C · API 接入速查（已踩平的坑）

| 能力 | 端点 | 关键点 |
|---|---|---|
| 生图 | `https://ark.cn-beijing.volces.com/api/plan/v3/images/generations` | Bearer 鉴权；模型名必须 `doubao-seedream-5.0-lite`；size ≥3686400 像素（3:4 用 1728x2304） |
| TTS | `https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional` | X-Api-Key + X-Api-Resource-Id: seed-tts-2.0；响应为 chunked JSON 行，data 为 base64 mp3 |
| 笔记数据 | 红狐 `get_note_detail` / `search_note` | 免费 400 次额度内实测字段全 |
