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
