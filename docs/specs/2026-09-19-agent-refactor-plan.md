# Agent 化改造清单（仿写生产线 → 微信 ClawBot 智能体）

- 版本：v1.1 · 日期：2026-09-19（v1.1 修正 ClawBot 风险定性、新增 §11 部署形态演进、§12 五层 .md 落地说明）
- 依据：设计文档 v1.1 第 3 章（TriggerAdapter 触发层）+ MyAgent/Transformer 架构模式 + hermes WeixinAdapter 源码预验证结论
- 范围：将 POC 脚本集合改造为独立 Agent 工程，**微信 ClawBot 为首发渠道**（飞书架构位保留，不实现），生图双通道（火山 Agent Plan + 红狐）

> **v1.1 风险定性修正**：v1.0 文档中将 ClawBot 风险隐含描述为"风控/封号"是过度定性。ClawBot 是微信 2026.3 开放的官方 iLink Bot 协议（域名 `ilinkai.weixin.qq.com`），属于合规产品形态，不存在"灰产/封号"风险。真实存在的风险维度是**性能/稳定性/常驻进程**三方面，详见 §11 修正后口径。

## 一、总体架构改造（POC 脚本 → Agent 工程）

| # | 改造点 | 说明 |
|---|---|---|
| 1.1 | 新建 `agent/` 工程目录 | `bot/`（触发层）+ `pipeline/`（生产内核）+ `config/`，与 `poc/` 隔离，POC 保留为验证资产 |
| 1.2 | `bot/base.py`：TriggerAdapter 抽象基类 | `start / send_text / send_image / send_video / send_file / capabilities` + 统一 Intent Schema；Router 与 pipeline 不 import 渠道 SDK（设计 3.3） |
| 1.3 | `bot/router.py`：指令路由 | 正则优先匹配设计 3.5 指令集（/选题 /确认 N /状态 /换角度 /help /生图通道），LLM 意图解析兜底（Phase 1.5）；确认卡点状态机 |
| 1.4 | 多用户会话与隔离 | SQLite（`agent/state.db`）：users / sessions / tasks / pending_push；产出目录 `workspace/users/{uid}/`（设计 3.6） |
| 1.5 | `bot/deliver.py`：交付降级 | 图文逐张直发 + 文案文本；视频 ≤ 阈值直发、超限提示人工取件（网盘链接 Phase 2）；按 adapter.capabilities() 自动降级 |

## 二、渠道改造（飞书 → 微信 ClawBot）

| # | 改造点 | 说明 |
|---|---|---|
| 2.1 | `bot/bot_weixin.py`：移植 hermes WeixinAdapter（MIT） | 形态 C：裁剪为 iLink 协议薄适配层，实现 TriggerAdapter 接口 |
| 2.2 | 保留的协议能力 | 35s 长轮询 getupdates；QR 扫码登录（token 持久化，重启免扫）；context_token 磁盘缓存；AES-128-ECB 加密 CDN 媒体收发；-2 限流退避 + 熔断；-14 过期处理 |
| 2.3 | 裁剪掉的 hermes 机制 | agent 回合 / typing 指示 / plugin hooks / profile 多路复用 / markdown 分片花活（保留基础 2000 字切分） |
| 2.4 | 白名单准入 | `config.yaml` 的 `allow_users`（wx_id 列表）；空列表 = 配对码制（首个发消息用户凭配对码入白名单）——对应 ClawBot DM pairing |
| 2.5 | 推送降级与补发 | tokenless 降级发送（源码路径移植）；失败 → `pending_push` 落盘 → 用户下次消息时自动补发（设计 3.4） |
| 2.6 | 飞书位保留 | base 接口即飞书位；`bot_feishu.py` 暂不实现（后续按 Transformer bot.py 模式补） |

## 三、生图双通道改造（重点新增）

| # | 改造点 | 说明 |
|---|---|---|
| 3.1 | `pipeline/imagegen.py`：统一 ImageGenProvider | `generate(prompt, out_path, orientation, ref_images) -> path`；业务比例（竖版 3:4 / 横幅 16:9）由各 Provider 内部翻译为自己的参数格式 |
| 3.2 | 通道 A · 火山 Agent Plan（主，现状收编） | doubao-seedream-5.0-lite，`api/plan/v3/images/generations`，竖版 1728×2304 / 横幅 2304×1728（POC 已验证尺寸）；同步直连 |
| 3.3 | 通道 B · 红狐 GPT-Image-2（新增） | `gpt_image.submit/result`：resolution 1k/2k/4k + size 3:4/16:9；**支持 ≤2 张参考图 URL（图生图）**——对标笔记图片要素调整场景（设计文档第 7 章能力项）；异步 submit + 轮询 |
| 3.4 | 通道 C · 红狐豆包 Seedream Lite 转发（备源） | `doubao_image.lite_submit/result`：像素尺寸直传 + 图生图（URL/Base64）；用于 Agent Plan 额度耗尽时的同源备源 |
| 3.5 | 路由策略 | config 指定 primary；失败/超时自动 fallback 到下一通道；`/生图通道 ark|gpt|doubao` 指令运行时切换（供质量对比） |
| 3.6 | 成本与水印 | ark：watermark=false（POC 已验证）；红狐两通道按红狐计费（¥0.0x/次级）；各 Provider 记录耗时/成功率到 state.db 供对比 |

**生图双通道实测记录（2026-09-19，沙箱）**：
- `ark`（火山 Agent Plan）：**通过**——943KB 竖版图，20.9s，POC 尺寸规格不变
- `redfox_gpt`（GPT-Image-2）：代码就绪，**调用被拒（3203）**——红狐生图接口已升级为**仅付费调用**，免费积分不可抵扣（官方说明：长期遭批量消耗，运营成本难以为继）；需在 redfox.hk 充值后启用
- `redfox_doubao`（豆包转发）：同上 3203 仅付费
- **决策待定**：是否为红狐生图充值（其独有价值 = GPT-Image-2 支持 ≤2 张参考图做对标图要素调整）。不充值则当前自动回落 ark 单通道，**充值后零代码改动即启用**（config 开关）

## 四、流水线模块化（包装现有 POC，渐进收编）

| # | 改造点 | 说明 |
|---|---|---|
| 4.1 | `pipeline/rewrite.py` | 拆解仿写任务对象 + 提示词模板（源自 `docs/prompts/xhs-standard-prompts.md`）+ LLM 调用（OpenAI 兼容接口，config 注入）；**原创度自检（3-gram Jaccard ≤ 0.30）纯函数实现** |
| 4.2 | `pipeline/images.py` | 图文卡片生成：调 imagegen 双通道出底图 + HTML 排版层（POC gen_images.py 逻辑）；Phase 1 以 subprocess 包装 POC 脚本过渡 |
| 4.3 | `pipeline/video.py` | 图文同源分镜 + TTS（openspeech plan 端点）+ Ken Burns + xfade + BGM ducking（POC make_video.py 逻辑）；Phase 1 subprocess 包装过渡 |
| 4.4 | `pipeline/radar.py` | 雷达：fetch_redfox + score_topics 收编；每日 cron → 选题清单落盘 → 经 bot 推送 Top 5（tokenless 路径） |
| 4.5 | 任务编排 | 确认选题 → 顺序 rewrite → images → video → deliver；状态机（queued/rewriting/imaging/composing/delivered/failed），/状态 可查 |

## 五、配置与运行

| # | 改造点 | 说明 |
|---|---|---|
| 5.1 | `agent/config/config.yaml` | 渠道 token/白名单、生图通道与 key、LLM、雷达关键词与 cron、交付阈值（video_max_mb） |
| 5.2 | `agent/main.py` | 装配入口：adapter + router + pipeline + 雷达定时线程；`--qr-login` 扫码引导 |
| 5.3 | `agent/SOUL.md` + SKILL 提示词 | 从 `docs/prompts/xhs-standard-prompts.md` 迁移拆分为可执行 SKILL 文件（Phase 1.5 正式 Skill 化） |
| 5.4 | requirements.txt | aiohttp / cryptography / redfox-python-sdk / pyyaml |

## 六、依赖用户验证结果的项（不阻塞开发，config 留占位）

| 验证项 | 影响的改造点 | 占位策略 |
|---|---|---|
| V1 扫码登录 | 2.1 token/account_id | config 空值；提供 `--qr-login` 流程 |
| V4 视频阈值 | 1.5 video_max_mb | 默认 25MB，实测后调 |
| V6 隔夜推送 | 2.5 补发默认策略 | 补发逻辑默认开启（实测通过可关） |
| V7 多用户配对 | 2.4 准入模式 | allow_users 列表制先行 |

## 七、目录规划（落地形态）

```
agent/
  main.py                  # 入口
  requirements.txt
  config/config.example.yaml
  bot/
    base.py                # TriggerAdapter ABC + Intent（设计 3.3）
    router.py              # 指令路由 / 会话 / 任务队列 / 补发（设计 3.5/3.6）
    deliver.py             # 交付降级（设计 3.7 微信列）
    bot_weixin.py          # iLink 协议适配（移植 hermes，MIT）
  pipeline/
    imagegen.py            # 生图双通道 Provider + 路由
    rewrite.py             # 拆解仿写 + 原创度自检
    images.py / video.py   # 图文/视频（Phase 1 包装 POC）
    radar.py               # 选题雷达
  state.db                 # 运行时生成（SQLite）
  workspace/users/{uid}/   # 每用户产出
```

## 八、需要用户提供 / 决策的事项（2026-09-19 梳理）

| # | 事项 | 类型 | 去向 | 是否阻塞 |
|---|---|---|---|---|
| 1 | **仿写 LLM 的 API key**（OpenAI 兼容 chat 接口，智谱 / 火山 doubao chat / DeepSeek 任选一家） | 提供 | `config.yaml` llm 段 | **阻塞** /确认 N 全流程（现状：无 key 时仿写步骤失败并提示） |
| 2 | **红狐生图充值决策**（3203 仅付费） | 决策 | 不充 = ark 单通道照常跑；充值后 config 打开 redfox_gpt 即启用（零代码改动） | 不阻塞 |
| 3 | V1 扫码凭据（account_id / token） | 验证产出 | `config.yaml` channel.weixin（或本工程 `--qr-login` 自动落盘） | 阻塞微信联调 |
| 4 | 你的 ilink user_id | 验证产出 | `allow_users` + `home_uid`（首条消息的日志可查） | 阻塞微信联调 |
| 5 | V3/V4 视频直发阈值 | 验证回填 | `deliver.video_max_mb`（默认 25MB 占位） | 不阻塞（有默认值） |
| 6 | V6 隔夜推送结果 | 验证回填 | 补发队列策略（当前默认开启，实测通过可关） | 不阻塞 |
| 7 | V7 多用户 ID 列表 | 验证回填 | `allow_users` 扩展 | 不阻塞 |

已有 key（无需再提供）：`ARK_API_KEY`（火山 Agent Plan，生图+TTS，已实测可用）、`REDFOX_API_KEY`（雷达数据源，已实测可用）。

### §8.1 · POC 与 Agent 化的「执行者对照」（为什么还缺一个 chat key）

POC 全链路跑通 ≠ 全自动跑通——生成类环节在 POC 中由**对话 AI 人肉完成**（产出已固化在脚本/文档里），代码只负责确定性环节。Agent 化 = 把人肉环节换成程序可调用的 API：

| 环节 | POC 时的执行者 | Agent 化后 | 是否需要新 key |
|---|---|---|---|
| 雷达抓取（红狐） | 代码 `fetch_redfox.py` | 代码（已收编 radar.py） | 否（红狐 key 已有） |
| 数值热度评分 | 代码 `score_topics.py` | 代码（已收编） | 否 |
| 语义评分（四维） | 对话 AI 人肉 | **chat LLM API**（Phase 1.5） | **是** |
| **五层拆解 + 同构异题仿写** | **对话 AI 人肉**（rewrite_001.md 即其产出） | **chat LLM API**（rewrite.py 已就绪） | **是（唯一阻塞）** |
| 图文卡片排版 | 代码（HTML/PIL，文案由 AI 设计后硬编码） | 底图 = 生图 API；版式 Phase 1.5 参数化 | 生图否（Agent Plan 已有） |
| **底图生图** | 代码 `gen_assets_ark.py` → Agent Plan | 代码（imagegen.py，实测通过） | 否 |
| **视频分镜口播文案** | **对话 AI 人肉**（图文同源分镜） | **chat LLM API**（Phase 1.5） | **是（同一个 key）** |
| TTS 配音 | 代码 → Agent Plan TTS（openspeech plan 端点） | 代码（沿用，**不需替换**） | 否 |
| Ken Burns/xfade/BGM 合成 | 代码 `make_video.py` + ffmpeg | 代码（Phase 1.5 收编） | 否 |

结论：**视频链路的 TTS 与生图继续用 Agent Plan，不替换**；缺的只是一个 chat 对话模型 key（仿写、分镜文案、语义评分三处共用）。火山 Agent Plan key 是专属端点 key（仅视觉+TTS），实测调 chat 报 AuthenticationError，故不可复用。

### §8.2 · 仿写模型选型实测（2026-09-19，百炼平台，同一对标笔记横向对比）

| 模型 | 耗时 | 原创度相似度 | 图片单元 | 人设感亮点 | 风险 |
|---|---|---|---|---|---|
| **kimi-k3** | 126s | 0.035 | **5（最全）** | **最强**：独有"为什么这么排"逻辑段（存体力/观光车接驳/雨天兜底）+ 分项预算表（住宿/门票/餐饮/交通）+ 实操洞察（提前2-3周锁房） | 标题钩子稍弱；速度最慢（异步场景可接受） |
| **glm-5.3** | 82s | **0.027（最原创）** | 4 | 强：避晒时间策略 + 人均预算 + 省钱技巧 + 钩子示例格式 | 无 |
| qwen3.8-max | 104s | 0.175 | 2 | 节奏表（体力星级+预算列） | 相似度偏高；图片单元不足 |
| deepseek-v4-pro | **53s** | 0.115 | 2 | 点位细节最具体 | **虚构"亲子民宿绿皮书"资产**（账号可信度风险） |

- **推荐顺序：kimi-k3（主力）→ glm-5.3（fallback）→ qwen3.8-max → deepseek-v4-pro**；仿写为异步任务，速度差异（53-126s）不构成决策因素，质量优先
- 待补测：MiniMax-M3、step-3.7-flash（百炼上需先在控制台开通对应模型服务，免费）；豆包不在百炼（火山系），如需测须另开通火山方舟标准推理
- 逐模型原始产出：`agent/workspace/model_compare_YYYY-MM-DD.json`

## 九、Transformer 五层配置对照（回应「是否还需配置 Hermes」）

本工程走**形态 C（独立进程，只移植 WeixinAdapter 协议层）**，不运行 hermes 主程序，因此**不需要** hermes 的 gateway / 模型 / skills 目录等任何运行时配置。但知识库 Transformer 的五层配置模式**全部采纳**，载体换成本工程：

| Transformer 五层 | 本工程载体 | 状态（v1.1 复盘） |
|---|---|---|
| SOUL.md（人设） | `agent/SOUL.md` | ❌ v1.0 称"已做"是错的（`config.yaml` persona.soul 为空字符串）；**§12 落地补齐** |
| system-prompt.md（系统提示词） | `agent/prompts/system-prompt.md` | ❌ v1.0 称"已做"是错的（揉在 rewrite.py 内，无 .md）；**§12 落地补齐** |
| SKILL（技能） | `agent/skills/{name}/SKILL.md` ×3 | ⚠ 代码已实现（rewrite/imagepack/video.py），但无 .md 声明契约；**§12 落地补齐** |
| config.yaml（配置） | `agent/config/config.yaml` | ✅ 已做 |
| checklist（验收） | `agent/CHECKLIST.md` | ❌ v1.0 称"Phase 1.5"未落地；**§12 落地补齐** |

说明：验证手册里的 hermes gateway 安装仅是**通道验证工具**（V1-V8），验证产出的凭据喂给本工程，两者不冲突、不共存。

## 十、Phase 1.5 · 三份 SKILL 化说明

| SKILL | 做什么 | 状态 |
|---|---|---|
| xhs-rewrite | 五层拆解 → 同构异题仿写 → 内容单元 JSON | ✅ 完成并实测（四模型对比，kimi-k3 定稿主力） |
| xhs-imagepack | 任意仿写稿 → 4 张图文卡片（LLM 版式编排 + 双通道底图 + PIL 排版） | ✅ 完成：`pipeline/imagepack.py`（plan_layout + generate_pack） |
| xhs-video | 内容单元 → 图文同源 5 镜头 → TTS + Ken Burns + xfade + BGM 成片 | ✅ 完成：`pipeline/video.py`（make_video，数据驱动） |

**端到端实测（2026-09-19，沙箱，全自动无人干预）**：kimi-k3 仿写（126s）→ kimi-k3 版式编排（193s）→ ark 生 6 底图 + 4 卡渲染（141s）→ TTS 5 段 + ffmpeg 合成（291s）= **单篇全链路约 12 分钟**，产出 4 张 1242×1656 图文卡 + 74.4s / 1080×1440 / 30MB 成片（产物：`agent/workspace/e2e/`）。注意：30MB 超过默认 video_max_mb=25，微信交付时将触发超限降级（人工取件或调码率/V4 实测后调阈值）。

三份 SKILL 均已接入 router 任务流水线（确认选题 → 仿写 → 编排 → 4 图交付 → 视频交付，视频失败不影响图文）。

## 十一、部署形态演进与风险修正口径（v1.1 新增）

### 11.1 风险定性修正（关键）

v1.0 文档隐含将 ClawBot 视为"风控风险"是过度定性。修正口径：

| 风险类型 | 是否真实存在 | 严重度 | 说明 |
|---|---|---|---|
| 协议合规风险 | ❌ 不存在 | 无 | ClawBot = 微信 2026.3 开放的官方 iLink Bot 协议（域名 `ilinkai.weixin.qq.com`），合规产品形态 |
| 单微信号消息频率上限 | ⚠ 真实但未测 | 中 | 5-10 人内测安全；20+ 用户阈值由 V5 实测确定，不可先判 |
| 常驻进程约束 | ✅ 真实 | 高 | 长轮询需常驻进程，关机=bot 掉线（任何 bot 都有此约束） |
| token 过期（-14） | ✅ 真实 | 中 | 源码已知有 10 分钟自动恢复，长期稳定性由 V8 一周压测确认 |
| 媒体大小阈值未知 | ✅ 真实 | 低 | V4 实测出真实值，写入 `deliver.video_max_mb` |
| 多人并发任务排队 | ✅ 真实 | 中 | 单篇 12 分钟，3 人并发即排队拥堵，已有队列机制 |
| 生图/TTS/LLM 各家限流 | ✅ 真实 | 中 | 已有 fallback，多人并发会触发；非"风控"性质 |
| 机房 IP 触发风控 | ❌ 撤回 | 无依据 | 此前的"机房 IP 必触发风控"是无依据臆测；ClawBot 既然是官方协议，云部署可行性需实测确认，不能先判 |

**v1.0 提到的"准备独立小号防封/朋友圈养号/节流加固"等待办全部撤销**——这些是建立在错误前提上的伪需求。

### 11.2 部署形态演进路径

| 形态 | 部署位置 | 通道 | 电脑要开机 | 商业化 | 适用规模 | 当前进展 |
|---|---|---|---|---|---|---|
| **P0 本地电脑** | 个人 PC/笔记本 | ClawBot 个人号 | ✅ 必须 | ❌ 单用户 | 1 人 | 代码就绪，待 V1 扫码联调 |
| **P1 本地常驻机** | 树莓派/Rock 5/旧 Mac mini | 同上 | ✅ 但常驻 | ⚠ 5-10 用户 | 5-10 人 | P0 稳定后迁移 |
| **P2 轻量云 + 微信云控** | 阿里云/腾讯云 + 家中常驻机 | ClawBot（家中常驻） | ❌ 不用 | ✅ 中小规模 | 50-200 人 | 待 P1 稳定后重构 |
| **P3 企业微信 + SaaS** | ECS/K8s | 企业微信官方 API | ❌ 不用 | ✅ 规模化 | B 端 | 重做通道 |

### 11.3 当前阶段（P0）真待办

| # | 行动 | 落地位置 | 必做性 |
|---|---|---|---|
| 1 | 扫码登录让 bot 号上线 | `python3 main.py --qr-login` | 必做 |
| 2 | 加好友、发消息、收回复 | V2-V5 验证 | 必做 |
| 3 | main.py 加 `--profile` 参数支持多 Bot 实例 | §13 落地 | 必做（支持 10 内多用户） |
| 4 | SQLite schema 加 bot_id + 产物路径分层 | §13 落地 | 必做（为 P2 铺路） |
| 5 | 失败降级（连续失败 N 次 → 转人工提示） | router.py（已有部分） | 中（稳定性） |

## 十二、Hermes 五层 .md 落地（v1.1 新增）

为让工程符合 Hermes 形态、可被非工程师参与迭代，v1.0 文档 §9 标"已做"但实际未落地的四层 .md 在 v1.1 全部补齐：

| 载体 | 路径 | 内容来源 |
|---|---|---|
| SOUL.md | `agent/SOUL.md` | 新建：旅游行程规划师人设 |
| system-prompt.md | `agent/prompts/system-prompt.md` | 合并 `docs/prompts/xhs-standard-prompts.md` + rewrite.py build_rewrite_prompt() |
| xhs-rewrite SKILL | `agent/skills/xhs-rewrite/SKILL.md` | 提示词①② + rewrite.py 接口契约 |
| xhs-imagepack SKILL | `agent/skills/xhs-imagepack/SKILL.md` | 提示词③ + imagepack.py plan_layout/generate_pack 契约 |
| xhs-video SKILL | `agent/skills/xhs-video/SKILL.md` | 提示词④ + video.py make_video 契约 + 附录 B/C（音色、API 接入） |
| CHECKLIST.md | `agent/CHECKLIST.md` | 新建：发布前 9 项终审清单（含原创度、要素完整、风格统一等） |

落地后影响：

- 改提示词 = 改 .md（运营/策划可参与，无需改代码）
- 复用做新 Agent = 复制目录改 .md
- 可审计 = .md 全可见可 diff
- 与 hermes 主程序接入路径保留（如未来需要）
