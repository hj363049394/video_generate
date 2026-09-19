# Agent 化改造清单（仿写生产线 → 微信 ClawBot 智能体）

- 版本：v1.0 · 日期：2026-09-19
- 依据：设计文档 v1.1 第 3 章（TriggerAdapter 触发层）+ MyAgent/Transformer 架构模式 + hermes WeixinAdapter 源码预验证结论
- 范围：将 POC 脚本集合改造为独立 Agent 工程，**微信 ClawBot 为首发渠道**（飞书架构位保留，不实现），生图双通道（火山 Agent Plan + 红狐）

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

## 九、Transformer 五层配置对照（回应「是否还需配置 Hermes」）

本工程走**形态 C（独立进程，只移植 WeixinAdapter 协议层）**，不运行 hermes 主程序，因此**不需要** hermes 的 gateway / 模型 / skills 目录等任何运行时配置。但知识库 Transformer 的五层配置模式**全部采纳**，载体换成本工程：

| Transformer 五层 | 本工程载体 | 状态 |
|---|---|---|
| SOUL.md（人设） | `config.yaml` persona.soul | 已做（Phase 1.5 独立成 `agent/SOUL.md`） |
| system-prompt.md（系统提示词） | `pipeline/rewrite.py` build_rewrite_prompt()（源自 xhs-standard-prompts.md） | 已做 |
| SKILL（技能） | 三份：xhs-rewrite / xhs-imagepack / xhs-video | rewrite 已代码化；图文排版、视频合成两份属 Phase 1.5 |
| config.yaml（配置） | `agent/config/config.yaml` | 已做 |
| checklist（验收） | 原创度自检（3-gram，代码内）+ 发布前人工终审清单 | 自检已做；终审清单 Phase 1.5 |

说明：验证手册里的 hermes gateway 安装仅是**通道验证工具**（V1-V8），验证产出的凭据喂给本工程，两者不冲突、不共存。

## 十、Phase 1.5 · 三份 SKILL 化说明

| SKILL | 做什么 | 现状 | 依赖 |
|---|---|---|---|
| xhs-rewrite | 五层拆解 → 同构异题仿写 → 内容单元 JSON | **已基本代码化**（pipeline/rewrite.py，差 LLM key 联调） | 事项 #1 |
| xhs-imagepack | 任意仿写稿 → 4 张图文卡片（底图生图 + HTML 排版层） | POC 脚本为单篇硬编码，需参数化 | 不依赖微信通道，可先行 |
| xhs-video | 内容单元 → 图文同源分镜 → TTS + Ken Burns + xfade + BGM 成片 | 同上 | 同上 |

三份 SKILL 均不依赖微信 token（pipeline 内核独立），验证期间可并行开发；仅端到端联调需等事项 #3/#4。
