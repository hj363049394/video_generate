# Agent 结构评估与调整指南（v1.2.1）

> 日期：2026-09-21 · 评估基线：`2026-09-19-agent-refactor-plan.md` §9/§12（Transformer 五层配置模式 + Hermes 形态 C）
> 用途：① 结构遵从度评估结论 ② 业务流程质量链路评估 ③ **调整入口地图**（想改什么 → 去哪个文件）
> **v1.3 实施状态**：§4 全部 P0/P1/P2 项 + 两个新场景已于 2026-09-21 落地（详见文末「§6 实施状态」）

---

## 0. 结论摘要

| 维度 | 评级 | 一句话 |
|---|---|---|
| Hermes/Transformer 五层结构 | 🟡 基本符合 | 五层载体齐备，但有 3 处失真/缺失（CHECKLIST 虚假声明、拆解环节无 SKILL、人设钩子硬编码） |
| 提示词外置（v1.2.1） | 🟢 符合 | 5 个 LLM 模板全部外置 `agent/prompts/`，代码零副本，文件即单一可信源 |
| 业务流程（获取→拆解→仿写→版式→交付） | 🟡 主链合理 | v1.2 拆解驱动后主链闭环；**最大缺口在入口端：雷达语义评分未接线**，选题质量无保障 |
| 质量保障链路 | 🔴 断裂 | 自动检查 4 项中 2.5 项虚置（CHECKLIST 声称自动但代码未实现）；人工终审 5 项无程序载体 |

---

## 1. 结构遵从度评估（Transformer 五层 + 提示词层）

改造方案 §9 定义的五层配置模式，当前落地状态逐层核对：

### 1.1 SOUL.md（人设层）——🟡 基本符合，两处残留

- ✅ v1.2.1 已真实接线：`promptkit.load_soul`（`config.yaml` persona.soul > `agent/SOUL.md`）注入仿写提示词
- ⚠️ **只注入了仿写（rewrite）阶段**——拆解（analyze）、版式（layout）两阶段的提示词不含人设段。拆解阶段客观中立可以接受；版式阶段不含人设但 `layout.md` 模板里手写了 CTA 文案要求，勉强算覆盖
- ❌ **`imagepack.py` 的 `DEFAULT_CTA`（约 L42-45）硬编码人设钩子**（"关注 @ 行程规划旅行家"/"帮你出定制行程"）——这是人设的代码副本，违背"调人设只改 .md"原则。改人设时这里不会跟着变

### 1.2 system-prompt.md（系统提示词层）——🟡 定位已重定义

- 当前定位（v1.2.1 重写后）：**人读总纲**，不参与任何 LLM 调用
- 结构事实：所有 LLM 调用都是单轮 user 消息（`_chat_once`），无 system 角色；各阶段提示词自成一体
- 评估：这是**形态 C 的合理裁剪**（流水线任务分解，不需要全局 system prompt），不算违规——但要知道：**改 system-prompt.md 不会改变任何运行时行为**，真正生效的是 5 个模板文件

### 1.3 SKILL 层——🟡 缺一个

- 流水线 LLM 环节共 5 个：`note_fetch`（无 LLM）→ `note_analyze`（拆解）→ `rewrite`（仿写）→ `imagepack.plan_layout`（版式）→ `video`（无 LLM）
- SKILL.md 覆盖 4 个：xhs-note-fetch / xhs-rewrite / xhs-imagepack / xhs-video
- ❌ **v1.2 新增的拆解环节 `note_analyze` 没有 SKILL.md**——五层结构在 v1.2 演进时漏了同步
- 说明：本项目 SKILL 是**契约文档**（人读，声明接口/输入输出/异常），非 Hermes 运行时调度——形态 C 下的正确形态

### 1.4 config.yaml（配置层）——✅ 符合，一处双源隐患

- 覆盖完整：channel（微信）/ imagegen（三通道）/ llm（模型优先级+vision_model）/ persona / radar（关键词+门槛）/ deliver
- ⚠️ `poc/radar/profile.yaml` 还有一套 persona + keywords + 语义评分配置（hard_gates/weights）——**agent 链路完全不消费它**，属双源漂移隐患（语义评分接线时应收编进 agent config，见 §4）

### 1.5 CHECKLIST.md（验收层）——🔴 声明与实现失真

CHECKLIST 头部声称"router 在任务执行过程中自动完成代码层检查（#1-#4）"，逐项核对代码：

| # | 声称 | 实际 | 判定 |
|---|---|---|---|
| 1 原创度 ≤0.30 | 代码自动·阻塞 | `router._run_task` L252-254 真实卡点 | ✅ 属实 |
| 2 不复用对标原图 | 代码自动·阻塞 | **无任何代码检查**（image_prompt 由 LLM 生成，无原图 URL 校验） | ❌ 虚置 |
| 3 内容单元结构完整（标题≤20字/标签≥3/单元≥2） | 代码自动·阻塞 | **无 schema 校验**（`run_rewrite` 只做 parse + 相似度） | ❌ 虚置 |
| 4 视频规格（1080×1440/25fps/30-90s/≤max_mb） | 代码自动·不阻塞 | 分辨率帧率由 ffmpeg 参数保证✅；大小由 deliver 阈值判断✅；**时长 30-90s 无校验** | ⚠ 半实现 |

另：CHECKLIST 内容停留在 v1.1——#7 写"4 张成品图 + 5 张视频帧"、#9 写"住宿卡 CTA / stay_hook.cta"，v1.2 已是 3-6 张卡片 DSL + 末卡 cta，**文档未同步**。人工项 #5-#9 的"检查结果模板"从未被程序渲染落盘，终审无载体。

### 1.6 提示词层（v1.2.1 新增的第 6 层）——🟢 最符合的一层

| 模板 | 消费方 | 状态 |
|---|---|---|
| `prompts/analyze.md` | note_analyze | ✅ 外置加载 |
| `prompts/describe-image.md` | note_analyze（多模态） | ✅ 外置加载 |
| `prompts/rewrite.md` | rewrite | ✅ 外置加载（含 analysis 注入段 + persona_soul） |
| `prompts/layout.md` | imagepack | ✅ 外置加载 |
| `prompts/xhs-copy.md` | rewrite（发布文案） | ✅ 外置加载 |
| ❌ 语义评分提示词 | （无人消费） | 留在 `poc/radar/prompts/topic_scoring.md`，**未收编未接线** |

---

## 2. 业务流程评估（质量保障链路视角）

```
【获取】雷达（推模式）                     【获取】拉模式（粘贴链接）
  fetch_redfox ×N 关键词                     note_fetch：链接解析→红狐详情
  → score_topics 数值评分                    → 视频笔记加提口播文案
  → topic_list Top10 落盘                    ↓
        ↓                                    ↓
  ❌ 语义四维评分【未接线】———————→ 【拆解】note_analyze
  ❌ rewrite_angle/persona_hook               下载图集→多模态看图(可选)→结构规格 JSON
     （设计有、代码无）                       （标题公式/正文骨架/逐张图卡 kind）
                                             失败→降级无拆解仿写 ✅
                                                ↓
                                             【仿写】rewrite
                                             按骨架逐单元同构 + image_units 对齐 ✅
                                             原创度 Jaccard ≤0.30 硬卡点 ✅
                                             ❌ schema 校验【未实现】
                                                ↓
                                             【版式】plan_layout 卡片 DSL
                                             第 N 卡对标爆款第 N 图卡 ✅（v1.2 核心）
                                             末卡 CTA 软校验自动补 ✅
                                                ↓
                                             【生图渲染】generate_pack
                                             双通道底图 + PIL 通用块渲染 ✅
                                                ↓
                                             【交付】deliver_note + xhs_copy + 视频
                                             能力降级链 ✅｜视频超限人工取件 ✅
                                             ❌ 质量报告【不落地】
```

### 2.1 爆款内容获取——当前最大缺口 🔴

- **推模式（雷达）只有数值门槛**（H≥25 且 赞≥500），`topic_scoring.md` 设计的四维语义评分（relevance 赛道相关 / virality 爆款潜力 / persona_fit 人设匹配 / conversion 转化空间，含硬门槛淘汰 + 机会分加权）**从未接入代码**
- 直接后果：
  1. 选题清单可能混入探店、纯风景、与人设无关的"高热度笔记"——仿写再好也是偏题
  2. `rewrite_angle`（仿写角度）/ `persona_hook`（人设钩子）不产出 → `format_topic_list` 里的角度显示是**死代码路径**，`/换角度` 指令是**桩**（只回一句"Phase 1.5 接入"）
- 拉模式（粘贴链接）完整：图文详情 / 视频 transcript 均就绪 ✅

### 2.2 拆解（v1.2 新增）——✅ 符合上次改造要求

结构规格 JSON（content_structure + image_structure + style_summary）+ 多模态可选 + 无图推断降级，链路完整。唯一注意：`vision_model` 留空时从正文推断图卡，拆解精度打折（可接受的设计取舍，已文档化）。

### 2.3 仿写——主链 ✅，校验 ⚠

- 拆解注入逐项对标 ✅；原创度硬卡点 ✅
- ⚠ `run_rewrite` 对 LLM 输出**无 schema 校验**：标题超 20 字、标签不足 3 个、image_units 缺失都会放行——LLM 偶发偷懒时低质产出直达交付

### 2.4 版式与渲染——✅ v1.2 核心诉求已落地

卡片 DSL 逐张对标爆款图卡结构（用户上次点名的问题已解决），渲染器只做通用块（list/rows/lines/cta × full/banner），版式决策全在 LLM + 拆解基准。

### 2.5 交付与终审——交付 ✅，终审 🔴

交付降级链完整；但**终审环节两个问题**：自动项虚置（§1.5）；人工项 #5-#9 没有程序载体（质量报告模板无人渲染，用户收到成品时看不到 9 项检查结果，只能凭感觉 review）。

### 2.6 POC 依赖（结构债，不阻塞但要知道）

- `radar.py` 以 subprocess 调 `poc/radar/{fetch_redfox,score_topics}.py`（改造清单 4.4 标"收编"，实际是包装）
- 字体/BGM 默认指向 `poc/production/assets/`（`pipeline.assets_dir` 可配置）
- **删掉 poc/ 目录 agent 跑不起来**——部署时必须整仓

---

## 3. 调整入口地图（想改什么 → 去哪里）

### 3.1 改提示词（调生成质量的第一入口）

| 想调什么 | 文件 | 说明 |
|---|---|---|
| 拆解粒度/图卡 kind 定义 | `agent/prompts/analyze.md` | kind 枚举改动需同步 `imagepack` 的 kind→DSL 映射（`_analysis_section`） |
| 仿写规则/原创红线/输出格式 | `agent/prompts/rewrite.md` | 占位符 `{analysis_section}` 由代码注入，别删 |
| 卡片版式规则（块类型/密度/CTA） | `agent/prompts/layout.md` | 块类型枚举改动需同步 `imagepack.py` 的 BLOCK_TYPES + 渲染器 |
| 发布文案排版风格 | `agent/prompts/xhs-copy.md` | 纯文本模板，随便改 |
| 多模态看图描述口径 | `agent/prompts/describe-image.md` | 60 字内约束在此 |
| 提示词总纲（人读） | `agent/prompts/system-prompt.md` | **不进运行时**，改它=改文档 |
| 语义评分提示词 | （待收编）`poc/radar/prompts/topic_scoring.md` | 尚未接线，收编后放 `agent/prompts/topic-scoring.md` |

语法：`{var}` 占位；字面花括号写 `{{ }}`；改完**重启进程生效**（模块加载时读取）。

### 3.2 改人设

| 想调什么 | 文件 |
|---|---|
| 人设正文（口吻/定位/行为准则） | `agent/SOUL.md`（正式基准；`config.yaml` persona.soul 显式值会覆盖它） |
| 人设钩子兜底 | ⚠ `pipeline/imagepack.py` 的 `DEFAULT_CTA`（代码副本，改 SOUL 不生效——待修） |
| 选题侧人设（语义评分用） | ⚠ `poc/radar/profile.yaml`（双源，未消费——接线时应统一到 load_soul） |

### 3.3 改配置

| 想调什么 | 位置（config.yaml） |
|---|---|
| 仿写模型优先级 | `llm.models`（失败自动 fallback） |
| 多模态看图 | `llm.vision_model`（留空=纯文字拆解） |
| 选题关键词/热度门槛 | `radar.keywords` / `radar.heat_threshold` / `radar.min_likes` |
| 视频直发上限 | `deliver.video_max_mb` |
| 生图通道 | `imagegen.primary`（或微信里 `/生图通道`） |
| 白名单/管理员 | `channel.weixin.allow_users` / `home_uid` |

### 3.4 改流程/校验逻辑（动代码）

| 想调什么 | 文件 |
|---|---|
| 任务流水线编排（⓪拆解→①仿写→②版式→③交付→④视频） | `bot/router.py` `_run_task` |
| 原创度阈值 | `pipeline/rewrite.py` `ORIGINALITY_THRESHOLD`（router 里还有一处硬编码 0.30） |
| 卡片数量/校验规则 | `pipeline/imagepack.py` `plan_layout`（3-6 张、等长、CTA 补全） |
| 交付降级策略 | `bot/deliver.py` |
| 指令集 | `bot/router.py` `_dispatch` + `HELP_TEXT` |
| 提示词加载机制 | `pipeline/promptkit.py` |

### 3.5 改验收标准

`agent/CHECKLIST.md`——改之前先看 §1.5 的失真表：#2/#3 目前是"声称自动实际没有"，改声明或补实现二选一。

---

## 4. 改进建议（优先级排序）

### 🔴 P0 · 质量链路补全（直接决定产出质量）

| # | 事项 | 方案 | 改动面 |
|---|---|---|---|
| P0-1 | **雷达语义评分接线** | `topic_scoring.md` 收编为 `agent/prompts/topic-scoring.md`（persona 段改用 `load_soul`，评分门槛/权重进 `config.yaml radar.semantic`）；`radar.py` 数值门槛后加 LLM 四维评分；产出 `rewrite_angle`/`persona_hook` 入选题清单 | radar.py + promptkit + 新模板 + config |
| P0-2 | **仿写 schema 校验** | `run_rewrite` 加输出校验：标题≤20 字、tags≥3、image_units≥1（有拆解时数量≈图卡数）、content 非空——不合格直接判失败重试 | rewrite.py 小改 |
| P0-3 | **CHECKLIST 打假** | 实现"不复用原图"检查（image_prompt 含 xhslink/小红书 CDN 域名即拦）或改为人工项声明；同步 v1.2 版式描述 | imagepack/rewrite + CHECKLIST.md |

### 🟡 P1 · 结构补齐

| # | 事项 | 方案 |
|---|---|---|
| P1-1 | note_analyze 补 SKILL.md | 仿照 xhs-rewrite 格式：触发/职责/输入输出 schema/异常/协作图 |
| P1-2 | DEFAULT_CTA 单一来源化 | 移到 `config.yaml persona.cta`（缺省回落 SOUL.md 派生），imagepack 只读配置 |
| P1-3 | 质量报告落地 | 任务收尾把 9 项检查结果渲染成 `quality_report.txt` 随交付发送（自动项程序填、人工项留空待勾） |
| P1-4 | `/换角度` 实装 | 依赖 P0-1 的 rewrite_angle；改 topic 的仿写角度字段并重触发任务 |

### 🟢 P2 · 结构债清理

| # | 事项 | 方案 |
|---|---|---|
| P2-1 | radar 收编 | fetch/score 逻辑进 `pipeline/`，摆脱 poc 目录依赖（或部署文档声明整仓依赖） |
| P2-2 | 视频时长校验 | make_video 输出后 ffprobe 时长，超出 30-90s 告警（不阻塞） |
| P2-3 | assets 收编 | 字体/BGM 移入 `agent/assets/`，`pipeline.assets_dir` 默认值改指内部 |

---

## 5. 评估方法备忘（下次自查怎么复现）

1. **五层核对**：SOUL 是否被 load_soul 消费 → system-prompt 是否进 LLM 调用 → SKILL 数 vs 流水线 LLM 环节数 → config 覆盖面 → CHECKLIST 声明 vs 代码 grep 逐项对
2. **提示词核对**：grep `"""` 大模板字符串于 pipeline/*.py，应只剩注释级；所有模板在 `agent/prompts/`
3. **质量链路核对**：沿 _run_task 逐环节问"输入不合格会不会被拦"——当前答案：选题（不拦）、拆解（降级）、仿写（拦原创度，不拦 schema）、版式（拦 DSL 结构）、交付（拦视频大小）

---

## 6. 实施状态（v1.3，2026-09-21 落地）

| 项 | 内容 | 落地位置 | 状态 |
|---|---|---|---|
| P0-1 | 雷达语义四维评分接线（含 rewrite_angle/persona_hook 产出） | `prompts/topic-scoring.md` + `radar.semantic_score` + main/router 接线 | ✅ |
| P0-2 | 仿写 schema 校验（标题/正文/标签/单元数，失败重试一次） | `rewrite.validate_rewrite_output` | ✅ |
| P0-3 | CHECKLIST 打假（原图引用拦截实装 + 文档同步 v1.2 版式） | `rewrite.contains_benchmark_url`（rewrite+imagepack 两处拦截）+ CHECKLIST v1.3 | ✅ |
| P1-1 | note_analyze 补 SKILL.md | `skills/xhs-analyze/SKILL.md` | ✅ |
| P1-2 | DEFAULT_CTA 单一来源化 | `promptkit.load_cta` + SOUL.md「默认 CTA」段 + config persona.cta 覆盖；imagepack 代码副本已删 | ✅ |
| P1-3 | 质量报告落地 | `bot/quality.py` + router 随交付发送 + /视频 后更新 | ✅ |
| P1-4 | /换角度 实装 | `router._cmd_angle`（更新选题角度）+ rewrite angle 注入 | ✅ |
| P2-1 | radar 收编（fetch/score 进 pipeline，公式不变） | `radar.fetch_search_notes/score_numeric`（删除 subprocess 依赖） | ✅ |
| P2-2 | 视频时长校验（30-90s 告警） | `quality.probe_video/check_video_spec` | ✅ |
| P2-3 | assets 收编（agent/assets） | 字体/BGM 复制 + assets_dir 优先级 config > agent/assets > poc | ✅ |
| 场景1 | /定位 命令（赛道/人设/服务钩子/关键词输入模板） | `router._cmd_persona` + user_profiles 表 + `_persona_for`（仿写/评分人设）+ `/选题 抓取` 按用户关键词 | ✅ |
| 场景2a | 直发主题/爆款内容生成 | `/仿写 内容`（含链接自动转拉模式）+ 直发长文本回「仿写」确认 | ✅ |
| 场景2b | 图文/视频流程拆开 | `_run_task` 不再自动合成视频；交付提示 `/视频 任务ID`；`_cmd_video` 补生成（列表/生成/规格报告） | ✅ |

同步更新：SOUL.md（CTA 段+素材类型表）、system-prompt.md（v1.3 总纲+指令表）、
xhs-video SKILL（触发方式）、config.example.yaml（persona.cta/pipeline.assets_dir/radar 语义评分说明）。

### §6.1 定位调整（2026-09-21 v1.3.1）

**变更**：SOUL.md 从"行程规划窄赛道"放宽为"旅游产品自媒体·专业旅行家·五大内容方向"
（①行程规划 ②旅行知识 ③人生与旅行 ④旅行好物 ⑤机动，均在旅游垂类内）。

**动因**：窄定位与 v1.2 同构异题原则冲突——情绪型爆款（旅行的意义）被硬套行程表；
且 config 雷达关键词 2026-09-20 已按五大方向配置，人设层属半改状态。

**同步落地**：SOUL.md 重写（五大方向表+类型化钩子表+起号配比+合集策略）；
topic-scoring.md 增 content_direction 归类与方向化 persona_fit/conversion；
layout.md cta 按方向类型化；radar/router 贯通 content_direction（清单展示+仿写注入）；
promptkit 兜底人设同步；CHECKLIST #9 改为方向匹配；system-prompt.md 角色与规则同步。

### §6.2 雷达拆解图集补全 + 单图对标冲突修复（2026-09-22 v1.3.2）

**问题一（数据缺口）**：雷达链路只用红狐搜索列表接口（仅封面 1 张，无图集），
拆解只能推断图卡结构，仿写版式还原度低于拉模式。红狐详情接口 `get_work` 有完整
图集（拉模式在用），属"没去拿"而非"拿不到"。

**修复（方案A）**：Router `_run_task` 载入 topic 后新增 `_enrich_topic_images`——
无图集且有 note_id 的选题（雷达确认/直发）调一次详情接口补图集（含更全正文），
幂等跳过拉模式/manual，失败降级封面/文字拆解不阻断。成本 1 次详情调用/篇。

**问题二（机制冲突，2026-09-22 实测任务 b812b79e 失败）**：单图/视频封面型对标的
拆解 image_structure 仅 1 张，仿写提示词"image_units 数量与之一致"指令与 schema
硬校验「≥2」直接冲突，两轮重试同败。

**修复**：rewrite.build_rewrite_prompt 按拆解图卡数分支——≥2 张维持"数量一致"；
≤1 张改为扩充指引（3-4 个 image_units，首图沿用对标 kind/风格，其余按骨架扩展），
硬校验下限不放松。

### §6.3 视频黑屏（微信端）兼容修复（2026-09-22 v1.3.3）

**现象**：图文交付正常，/视频 产出的 video.mp4 质检通过（36.1s/13.1MB）但在
微信里全黑只有声音。沙箱同链路复现（ffmpeg 6.1）无黑场、字幕正常；13.1MB 码率
（~2.6Mbps 视频轨）证明文件内有画面——问题在封装/播放兼容层，不在合成逻辑。

**根因**：ffmpeg 默认 mp4 的 moov 索引写在文件尾，本地播放器可容忍，微信手机端
流式播放读不到索引 → 黑屏有声（实测修复前 moov 位于文件尾、修复后前置到偏移 36）。

**修复（video.py 三件套）**：① 拼接与成片两步加 `-movflags +faststart`（moov 前置）；
② 成片后 blackdetect 黑场自检（黑场 >80% 总时长即 raise，拦截真黑屏静默交付，
报错提示反馈 ffmpeg -version）；③ drawtext 字幕 `text=` 改 `textfile=`（UTF-8
文件读取，规避中文 Windows 按 ANSI codepage 解析命令行导致的字幕乱码）。

**附带发现**：质检 #4（probe_video）只校验时长/大小，无画面内容校验——黑场自检
补上该盲区。微信上传链路 no_need_thumb=True 且曾有"图片灰图"协议先例，若 faststart
后仍黑，需进一步排查 CDN 转码层。

#### §6.3.1 协议层根因与修复（2026-09-22 v1.3.4）

**现象（v1.3.3 后复现）**：faststart 修复后微信端仍"黑屏"，且点击播放一直转圈、
画面永不出现（有声）。用户 ffmpeg 9.0.1 环境下黑场自检未报错，排除合成端。

**根因（协议层，非封装层）**：`bot_weixin.py` 发视频时 `video_item.play_length`
硬编码 0。微信官方文档确认 `play_length | uint32 | 视频秒数`（示例值 24）——
传 0 等于告诉客户端"这是一段 0 秒的视频"，播放器初始化进度/缓冲拿到非法时长
→ 转圈、视频层不渲染；音频流不受该字段影响，故"黑屏有声"与 v1.3.3 症状叠加。
hermes 上游对照：`play_length: kw.get("play_length", 0)` 参数化但 `_send_file`
从不传入（全仓库仅 weixin.py 一处），即上游 iLink 视频路径本就未经真机验证的
缺陷，非移植引入。图片链路正常排除传输层（同 CDN/AES/sendmessage）。

**修复（bot_weixin.py）**：新增 `_video_play_length(path)`——ffprobe 探测
`format=duration` 四舍五入为秒（uint32），探测失败回退 1s 并告警（0 是已知
致错值，绝不回退 0）；`video_item.play_length` 改填真实时长。
`no_need_thumb=True` 保持不动：hermes 原版同为硬编码，缩略图上传协议无文档，
不引入未知风险。

**验证**：沙箱冒烟 4 项全过——① 4s 测试视频探测=4；② 完整 sendmessage payload
play_length=4/video_size/aes_key=base64(hex) 均正确；③ ffprobe 缺失时回退=1
非 0；④ image_item 无 play_length 字段，图片链路零影响。
（待真机验收：git pull → /视频 重生成 → 微信端确认画面+进度条）

#### §6.3.2 编码兼容层根因与修复（2026-09-22 v1.3.5）

**现象（用户本机复现）**：生成的 video.mp4 在 Win11 媒体播放器报
"无法打开。它使用不受支持的编码设置。0x80004005"（时长 0:00:36 可识别，
即 v1.3.3 faststart 后容器正常，解码器初始化失败）。

**根因（编码兼容层）**：TTS 源为 24kHz 单声道 mp3，`_make_clip` 以
`-c:a aac -ar 24000` 直编继承采样率/声道，join 未指定 -ar 继续继承，
成片 `-c:a copy` 原样透传 → 成品音频 = **AAC-LC 24kHz 单声道**（全文件
最不标准组合）。MF 文档名义支持 24kHz 但实测 Windows 播放器兼容性差；
视频流 H.264 High@L4.0/yuv420p/25fps 经复现确认完全标准。另：同款报错
也存在机器级成因（音频驱动，见 Dell KB），修复策略为参数钉死 + 自检拦截
+ 机器侧诊断三管齐下。

**修复（video.py）**：① 新增 `AUD_AR, AUD_CH = 44100, 2`，`_make_clip`
与 join 两处 `-ar 44100 -ac 2` 显式重采样（TTS 保持 24k 请求不变，本地
重采样，无 API 风险）；② join/final 两处加 `-pix_fmt yuv420p` 显式保险；
③ 新增 `_verify_compat()` 兼容性自检门（成片后校验：音频 AAC ≥32kHz ≤2ch、
视频 H.264/yuv420p，违规 raise）——堵"本机 ffmpeg 行为漂移静默产出坏文件"
的质检盲区，与黑场自检同哲学。

**验证**：真实 make_video 全管线冒烟（stub TTS，2 镜头+xfade+BGM 混音）：
成品 AAC-LC 44100Hz stereo + H.264 High@L4.0 yuv420p + faststart（moov@36
早于 mdat）；自检门负例（24kHz/mono）正确拦截并给出可读报错。
（待真机验收：git pull → /视频 重生成 → 本机播放器+微信双端确认）

#### §6.3.3 视频流色彩范围根因与修复（2026-09-22 v1.3.6）

**现象（v1.3.5 自检门正确拦截）**：用户 ffmpeg 9.0.1 环境 /视频 报
"成片编码兼容性自检未通过：视频流 h264/yuvj420p 非 H.264/yuv420p"——
音频已达标（44.1kHz 立体声），视频流被 ffprobe 判为 yuvj420p（full-range H.264）。

**根因（证据驱动，非猜测）**：yuvj420p = JPEG full-range YUV（ffmpeg 7+
已弃用该标签）。
- 源 JPEG 解码输出 full-range 数据：沙箱 ffprobe 实测 yuvj420p,pc（与用户机一致）。
- 版本行为差异（沙箱直跑不复现的解释）：ffmpeg 6.1.1 的 format=yuv420p 滤镜
  做数据压缩（signalstats 实测白图 255→235）+ 标签转 tv，全链 yuv420p 通过；
  ffmpeg 7+（用户机 9.0.1）弃用 yuvj420p 后走"元数据直通"——解码输出
  yuv420p + color_range=pc，format 只换格式标签不动范围元数据，range=pc 帧
  直通 libx264 → x264 按 full-range 编码 → 成片 ffprobe 显示 yuvj420p。
- 机制模拟复现（沙箱 6.1.1 强制直通路径）：`format=yuv420p,setparams=range=pc`
  → 输出 yuvj420p,pc，与用户报错特征一致，为根因直接证据。

**修复（video.py _make_clip 一处，最小改动）**：vf 链 `format=yuv420p,` 后追加
`scale=out_range=limited`——9.x 路径：scale 读取输入 pc 元数据，实际压缩数据
255→235 并标 tv；6.1.1 路径：format 已转 tv，scale 为无操作，两版本行为均正确。
join/final 的输入经 _make_clip 修复后已是 tv，无需改动。不采用
setrange=tv / 输出端 -color_range tv（只改元数据不转数据，播放端按 limited
解读 full 数据会发白）。

**验证**：① 修复后全管线回归（stub TTS）成品 h264/yuv420p + aac/44100/2ch；
② 修复前后同帧 signalstats 逐位一致（YMIN=7/YAVG=125.745/YMAX=249）——
证明 6.1.1 上新增滤镜为真无操作、无双重压缩；③ 自检门负例（模拟产物
yuvj420p,pc）仍正确拦截，报错文案与用户所见一致。
（待真机验收：git pull → /视频 重生成）

#### §6.4 交付链路：CDN 上传重试与任务状态自愈（2026-09-22 v1.3.7）

**现象**：v1.3.6 后视频合成通过自检，但微信 CDN PUT 返回 HTTP 500（空 body）
异常冒泡至顶层；任务状态卡死 composing，/视频 重发被状态门挡住无法重试。

**根因（证据驱动）**：bot_weixin._send_media 的 CDN PUT 为单次尝试零重试
（对比：同文件文本发送已有限流退避重试 SEND_RETRIES=4）；同一链路图片此前
交付成功（任务能到 delivered）→ 请求格式/AES 加密/鉴权均无问题，novac2c
5xx 空 body 属服务端瞬时故障，重试即可自愈。次要缺陷：① router._cmd_video
未捕获 deliver_video 异常 → 状态不回滚；② 合成失败残留 video.mp4 无清理。

**修复（三处）**：
1. bot_weixin：CDN_UPLOAD_RETRIES=3 退避重试（2s/4s），仅对 5xx 与
   aiohttp.ClientError/TimeoutError 重试（4xx 立即失败）；错误信息带密文
   体积便于后续诊断；wait_for 300s 总闸不变。
2. router：deliver_video 包 try/except → 状态回滚 delivered + 用户可读提示，
   成片保留；合成失败时清理残片，确立"video.mp4 存在=通过自检"不变式。
3. router：重试路径若 video.mp4 存在且复跑 _verify_compat 通过则直接重传
   （省 3-5 分钟重合成，残片/坏片自动重新合成）；composing 超 15 分钟视为
   上次中断，允许重发。

**验证**：py_compile 通过；重传前强制自检复验为负例护栏。
（待真机验收：git pull → 重发 /视频 c93b47a9 直接重传）

#### §6.5 视频封面（thumb）：黑屏气泡修复（2026-09-22 v1.3.8）

**现象**：视频消息在微信气泡中未播放时显示纯黑——此前 getuploadurl 硬编码
`no_need_thumb=True` 且 video_item 无 thumb 字段（hermes 原版同为硬编码，
当时判定"缩略图上传协议无文档"故未做）。

**协议依据（本轮实证）**：weixin-agent（Rust crate，镜像 iLink Bot API 类型）
types.rs 揭示完整 thumb 协议——GetUploadUrlRequest 含 `no_need_thumb`/
`thumb_rawsize`/`thumb_rawfilemd5`/`thumb_filesize`（明文尺寸/明文 MD5/密文
尺寸），GetUploadUrlResponse 含独立 `thumb_upload_param`；VideoItem 含
`thumb_media`（CdnMedia: encrypt_query_param+aes_key+encrypt_type）+
`thumb_size`/`thumb_width`/`thumb_height`。

**实现（bot_weixin，全链路降级保护）**：
1. `_video_thumb()`：ffprobe 探视频分辨率 → ffmpeg 抽首帧等比缩放到宽
   THUMB_WIDTH=240 的 JPEG（q:v=4，输出约 6KB），返回 (字节, 宽, 高)；
   管线图生视频首帧即完整画面无黑帧，取第一帧即可。
2. `_send_media`：video 时封面与视频**共用同一 AES key** 加密（getuploadurl
   只接受一个 aeskey，服务器无其他密钥来源）；getuploadurl 传
   no_need_thumb=False + 三个 thumb 元数据字段；CDN PUT 用响应的
   thumb_upload_param 对称拼 URL（同一 filekey），取 x-encrypted-param。
3. video_item 补 thumb_media（aes_key 同主 media 的 base64(hex)）+
   thumb_size/thumb_width/thumb_height。
4. 降级链：封面生成失败 / getuploadurl 未返回 thumb_upload_param / 封面
   PUT 失败 → 三处均 warning 日志 + 无封面发送（不拖垮视频本身）；
   非视频类型完全走原路径。
5. 附带重构：_upload 闭包参数化为 _cdn_upload(url, data)，视频与封面
   共用 3 次退避重试逻辑。

**验证**：py_compile 通过；沙箱 ffmpeg 6.1.1 实测 2160x2880 成片 → 240x320
mjpeg 6017 字节（等比正确）；负例（不存在的视频）按预期抛
CalledProcessError 由调用方降级。
（待真机验收：重发 /视频 后气泡应显示首帧画面而非黑屏）

#### §6.6 /主题 指令：临时主题抓取，定位与主题解耦（2026-09-22 v1.3.9）

**问题（用户反馈）**：换主题找爆款需重发整个 /定位（赛道/人设/服务钩子/
关键词四行）——定位是长期属性、主题只是定位下的临时内容方向，二者
耦合导致流程冗长。

**设计**：定位与主题解耦——
- `/定位`（长期）：赛道/人设/服务钩子 + 默认关键词，一次配置长期生效；
- `/主题 词1, 词2`（临时）：按该主题立即抓取评分推送清单（约 1-3 分钟），
  仅本次生效，不覆盖 /定位 的默认关键词；
- `/选题 抓取`：仍按 /定位 默认关键词跑；
- `/确认 N`：衔接最新一次抓取的当日清单（run_radar 按天落盘、最新覆盖，
  语义即"当前意图"，无需额外关联）。

**实现（router.py 四处）**：HELP_TEXT 与 PERSONA_TEMPLATE 说明同步；
_dispatch 加 `^/?主题\s*(.*)$` 分支（置于 /选题 后，前缀不冲突）；
新增 _cmd_topic_search（无参回用法提示）；_run_radar_now 加 theme 参数
（theme 时 re.split 切分为关键词列表，否则回退定位/config 默认）。

**验证**：py_compile 通过；正则单测——带参提取/裸词命中/无参空串/
普通聊天文本不误触发/其他指令不受影响；中英文逗号顿号空格混排切分正确。

#### §6.7 /主题 探索模式：数值门槛全放 + 空清单统计透出（2026-09-23 v1.3.10）

**现象（真机）**：`/主题 让我印象最深刻的城市` 返回"雷达无过门槛选题"。

**根因**：数值门槛（DEFAULT_HEAT_THRESHOLD=25.0 / MIN_LIKES=500）是为
无人值守自动雷达防噪音设计的爆款级门槛；/主题 是用户主动探索，且口语化
长句作搜索词召回的多为中低互动笔记 → 全被拦截 → 清单为空。链路排查：
抓取本身成功（否则报"红狐抓取无输出"），拦截点在 score_numeric。

**修复（对齐"探索"语义）**：
- router._run_radar_now：theme 模式传 heat_threshold=0 / min_likes=0
  （run_radar 中 0≠None 生效），质量把关交给语义评分硬门槛
  （relevance≥6 且 virality≥5）；语义降级时 Top5 照出（主动探索下
  低互动清单也有参考价值）。config 门槛只作用于 /选题 抓取；
- radar.run_radar 落盘 JSON 新增 stats 漏斗（candidates 去重候选数/
  numeric_passed 过数值门槛数/heat_threshold/min_likes 实际生效值）；
- format_topic_list 空清单提示透出统计 + 建议：
  "…共搜到 N 篇，数值门槛（热度≥X 且赞≥Y）通过 0 篇；建议换更短的
  搜索词（如：城市旅行 回忆杀）重试 /主题"；
- /主题 无参用法提示补"主题词越短越好（2-6 字，像搜索词）"。

**验证**：py_compile 通过；stub 抓取（3 篇中低互动笔记）三场景——
默认门槛全拦出统计提示 / 探索模式 0/0 全进清单且语义评分产出角度与
机会分 / 语义异常降级数值排序清单不空。注意点：semantic_score 内部
延迟导入 llm_call_factory，monkeypatch 需打在 pipeline.rewrite 源头。

#### §6.8 /主题 探索模式二期：语义硬门槛也放开（2026-09-23 v1.3.11）

**现象（真机）**：v1.3.10 后重发 `/主题 让我印象最深刻的城市` 仍空清单，
但提示已透出漏斗——"共搜到 4 篇，数值门槛（热度≥0 且赞≥0）通过 4 篇"。

**根因**：拦截点不在数值阶段而在语义阶段——topic-scoring.md 硬门槛
（relevance<6 或 virality<5 → status=rejected）由 LLM 判定，4 篇长句
召回的城市泛内容/低互动笔记全被判 rejected，radar 只保留 selected → 空。
v1.3.10 的设计缺陷：数值门槛放开了，语义硬门槛没跟上，探索语义不完整。

**修复（v1.3.11）**：
- radar.run_radar / semantic_score 新增 explore_mode 参数（签名末尾，
  默认 False——main.py 两处无人值守调用不传，防噪音硬门槛不变）；
- semantic_score 探索模式下 LLM 淘汰项也进清单：附 reject_reason 落盘
  备查，无机会分（0）排序自然落尾部（selected 按机会分在前，rejected
  按热度序在后）；漏评保留逻辑不变；
- format_topic_list 空清单提示分阶段：数值通过但语义拦光 → "…数值门槛
  全部通过，但语义评分认为与账号定位契合度不足，N 篇全被淘汰"（仅
  非探索模式会出现；探索模式清单永不空，除非抓取本身无输出）；
- 机会分展示条件 `is not None` → 真值判断（rejected 的 0 分不显示
  "机会分0"）；
- router._run_radar_now：调用末尾传 `bool(theme)`。

**/确认 N 兼容**：rejected 条目无 rewrite_angle/persona_hook，仿写注入
处 `if x` 过滤后 angle=None 正常走无注入路径；/换角度 N 可事后补。

**验证**：stub 四场景——探索模式全 rejected（清单非空按热度序、无机会
分展示、reject_reason 落盘）/ 非探索全 rejected（语义拦光新提示）/
探索模式混合（selected 机会分序在前 rejected 热度序在后）/ 默认门槛
数值拦光（回归 v1.3.10 原提示）。首轮两断言写错（stub 热度序假设），
清单行为本身正确，修正后全 PASS。

#### §6.9 雷达时间硬筛 + 翻页上限补偿（2026-09-23 v1.3.12）

**背景**：对照用户人肉找爆款逻辑（主题搜 → 按赞/藏排序 → 近半年时间
筛 → 取前 10 篇）评估定稿：多维热度公式与语义评分保留（不退回单维排
序），"近半年"时间硬筛是真实缺口——"最热"排序天然被老爆款霸榜（2019
年 10 万赞攻略永远置顶），时间衰减只降权不剔除，而老笔记对"仿写当下
爆款"参考价值低。

**修复（v1.3.12）**：
- radar.run_radar 新增 time_filter_days 参数（默认 180=近半年，0=关闭）：
  去重后按 workPublishTime 硬筛；**无发布时间的笔记保留不误杀**
  （no_ts 单独计数透出，交语义评分兜底）；筛后为空 raise 并给可操作
  提示（调大 radar.time_filter_days 或设 0 关闭）；
- radar.fetch_search_notes 新增 raw_cap 翻页上限：时间筛会淘汰过半
  老笔记，run_radar 传 raw_cap=max_items×2 放大翻页补偿过滤损耗，
  None 时抓满 max_items 即停（调用方未启用时间筛零开销）；
- stats 漏斗补全：fetched（原始抓取）/no_ts/time_dropped/
  time_filter_days 落盘，format_topic_list 空清单提示透出"另 N 篇被
  近 X 天时间筛剔除"；
- router._run_radar_now 传 `(config.radar).time_filter_days`（默认
  180）；main.py 两处无人值守调用不传参吃默认 180——**有意为之**：
  老笔记对每日推送同样低参考价值，防霸榜对所有场景一致生效。

**观察项（未实施）**：红狐 hot 模式 search_hot_notes 服务端原生日期
筛 + 官方时效分（POC fetch_redfox.py 验证过字段映射）后续可 A/B；
黑马榜同理。

**验证**：py_compile 通过；stub 十六断言全 PASS——时间筛淘汰老笔记/
无时间戳保留且计数/0 关闭全保留/筛后空 raise 带指引/raw_cap 翻页上限
（无 raw_cap 抓满即停、=20 翻页到 20、run_radar 传 2×max_items、关闭
时传 None）/空清单提示透出时间筛漏斗。测试脚本首轮自误两处（stub 泄
漏到 D 场景未还原、cap_seen[-2] 时序错），库代码本身无误。
```