# 小红书笔记仿写 Agent · 产品方案文档

> 版本：v1.0 · 日期：2026-09-20
> 状态：代码完整 + 端到端验证通过 + 已推送 `trae/agent-F784ti` 分支
> 后续：基于本文档展开网站版本开发

---

## 1. 产品定位

### 1.1 一句话定位

面向**旅游内容创作者**的小红书爆款笔记自动化生产 Agent：抓取对标爆款 → 同构异题仿写 → 生成 4 张图文卡 + 1 个成片视频 → 通过微信对话交付。

### 1.2 用户画像

| 角色 | 场景 |
|---|---|
| 旅游博主（主） | 需要稳定产出小红书图文+视频笔记，但没时间持续盯爆款/写文案/做视频 |
| 旅游行程规划师（次） | 提供行程定制服务，需通过内容种草获客 |
| 多账号矩阵运营者 | 多微信号同时运营，每号独立配置独立产出 |

### 1.3 商业化路径

- Phase 1（内测）：微信 Bot 私聊交付，5-10 个种子用户
- Phase 2（验证）：双轨——微信 Bot + Web Dashboard
- Phase 3（规模化）：SaaS 多租户 + 企业微信 + API 开放

### 1.4 人设定位（账号侧）

**提供旅游行程规划与定制服务的旅行家**：
- 内容强化规划感（天数/预算/节奏表格化）
- 对标讲"去哪" → 仿写强化"怎么排"
- 结尾固定服务钩子（"评论区报人数/天数/预算，帮你重排一版"）
- 口吻：懂行的朋友给建议，专业但不端着

---

## 2. 需求场景

### 2.1 三大核心需求

| # | 场景 | 触发方式 |
|---|---|---|
| 1 | 主动选题（推模式） | 每日 8:00 雷达自动推送选题清单到管理员微信 |
| 2 | 选题确认生产（拉模式） | 用户回复 `/确认 N` 触发仿写 |
| 3 | 自有对标笔记（拉模式） | 用户粘贴 `xhslink.com` / `xiaohongshu.com` 链接 |

### 2.2 输入支持

| 输入类型 | 处理路径 |
|---|---|
| 红狐 search_note 产出 | 进选题雷达评分 → 选题清单 → `/确认 N` 触发 |
| 红狐 get_work 详情（图文笔记） | 直接进拆解仿写流水线 |
| 红狐 get_work 详情（视频笔记） | 详情 + transcript 提取口播文案 → 进流水线 |
| 用户粘贴链接 | 自动调红狐 get_work → 进流水线 |
| `/生图 描述` | 跳过仿写，直接出图（调试用） |
| `/生图通道 ark\|gpt\|doubao` | 切换生图主通道 |
| `/选题` | 返回当日雷达清单 Top 5 |
| `/状态` | 查询最近任务进度 |
| `/help` | 指令帮助 |

### 2.3 输出形态

| 产出 | 规格 |
|---|---|
| 4 张图文卡片 | 1242×1656（3:4 竖版），PIL 渲染中文 |
| 1 个成片视频 | 1080×1440，25fps，30-90s，TTS 旁白 + Ken Burns + BGM ducking |
| 仿写文案文本 | 标题 + 正文 + 标签（含结尾服务钩子） |
| 相似度报告 | 字符 3-gram Jaccard ≤ 0.30 |

---

## 3. 业务流

### 3.1 完整业务流（端到端）

```
┌─────────────────────────────────────────────────────────┐
│ ① 触发层（4 种入口）                                       │
│   - 雷达 cron（推） / 选题清单（拉）/ 链接（拉）/ /生图（调） │
└─────────────────────────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────┐
│ ② 笔记抓取层（note_fetch）                                 │
│   - 链接 → 红狐 get_work 拿详情                            │
│   - 视频笔记 → 红狐 transcript 提取口播文案                  │
│   - 归一化为内部 topic dict                                 │
└─────────────────────────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────┐
│ ③ 拆解仿写层（rewrite）                                    │
│   - 五层拆解（选题/标题/正文/视觉/数据）                     │
│   - 同构异题仿写（结构骨架保留 + 主题细节替换）              │
│   - 原创度自检（3-gram Jaccard ≤ 0.30）                     │
│   - LLM 按 models 列表 fallback                            │
│   - 输出 rewrite.json（含 image_units 内容单元）            │
└─────────────────────────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────┐
│ ④ 版式编排层（imagepack · plan_layout）                    │
│   - LLM 决定 4 张图内容分配 + 5 段视频分镜旁白              │
│   - 图文同源铁律：第 N 段旁白只讲第 N 张图内容              │
│   - 输出 layout.json                                       │
└─────────────────────────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────┐
│ ⑤ 生图层（imagegen 双通道）                                │
│   - 通道 A（主）：火山 Agent Plan doubao-seedream-5.0-lite  │
│   - 通道 B/C（备）：红狐 GPT-Image-2 / 红狐豆包 Seedream Lite │
│   - 自动 fallback                                          │
└─────────────────────────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────┐
│ ⑥ 排版渲染层（imagepack · generate_pack）                 │
│   - PIL 加载底图 + 信息卡层 + 文字渲染                      │
│   - 输出 4 张成品卡 + 5 张视频帧                            │
└─────────────────────────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────┐
│ ⑦ 视频合成层（video · make_video）                         │
│   - TTS 旁白（Agent Plan seed-tts-2.0，清新女声音色）       │
│   - Ken Burns（zoompan 推/拉交替）                          │
│   - crossfade 0.5s + BGM ducking 16%                       │
│   - 首尾 0.5s 淡入淡出                                      │
└─────────────────────────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────┐
│ ⑧ 交付层（deliver）                                        │
│   - 图文逐张直发微信                                         │
│   - 视频 ≤25MB 直发，超限提示人工取件                        │
│   - 推送失败 → pending_push 队列补发                         │
└─────────────────────────────────────────────────────────┘
```

### 3.2 视频笔记业务流（差异化部分）

```
用户粘视频笔记链接
    ↓
note_fetch.fetch_topic_from_user_text
    ├─ 红狐 get_work → type="video" + video_url
    ├─ 红狐 transcript_submit → taskId
    ├─ 轮询 transcript_result（间隔 5s，超时 180s）
    └─ 把口播文案拼到 description 末尾
    ↓
（后续与图文笔记业务流相同）
```

---

## 4. 使用的技术栈及作用

### 4.1 整体架构（五层 Hermes 形态）

```
┌──────────────────────────────────────────────────────┐
│ 触发层（TriggerAdapter）                              │
│ - bot/base.py（抽象基类，含 bot_id 多 Bot 隔离）       │
│ - bot/bot_weixin.py（微信 ClawBot 适配器）            │
└──────────────────────────────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│ 路由层（Router）                                      │
│ - bot/router.py（指令解析 + 任务队列 + 推送补发）       │
│ - bot/deliver.py（交付降级）                          │
└──────────────────────────────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│ 生产内核（Pipeline · 6 个 SKILL 模块）                │
│ - pipeline/note_fetch.py    笔记抓取                   │
│ - pipeline/rewrite.py       拆解仿写                  │
│ - pipeline/imagepack.py     版式编排 + PIL 渲染         │
│ - pipeline/imagegen.py      生图双通道                 │
│ - pipeline/video.py         视频合成                  │
│ - pipeline/radar.py          选题雷达                  │
└──────────────────────────────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│ 配置层（config/）                                     │
│ - config.yaml / config.{profile}.yaml（多 Bot 实例）   │
└──────────────────────────────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│ 五层 .md 契约（Hermes 形态）                          │
│ - SOUL.md / prompts/system-prompt.md                  │
│ - skills/xhs-{note-fetch,rewrite,imagepack,video}/    │
│ - CHECKLIST.md                                       │
└──────────────────────────────────────────────────────┘
```

### 4.2 技术清单

| 层 | 技术 | 作用 |
|---|---|---|
| 触发层 | ClawBot iLink Bot 协议（2026.3 开放） | 微信个人号 bot 接入，35s 长轮询，QR 扫码登录 |
| 触发层 | hermes WeixinAdapter（MIT 移植） | 裁剪为薄适配层，1258 行协议代码 |
| 触发层 | `bot/base.py` TriggerAdapter | 渠道可插拔，支持未来企业微信 |
| 路由层 | Python asyncio + SQLite | 单进程异步，状态持久化 |
| 路由层 | 多进程独立实例（方案 A） | `python3 main.py --profile botN` 多 Bot 隔离 |
| 抓取层 | redfox-python-sdk | 红狐 API：search_articles / get_work / transcript |
| 仿写层 | OpenAI 兼容 chat/completions | 阿里百炼 kimi-k3 / glm-5.3 / qwen3.8-max / deepseek-v4-pro |
| 仿写层 | 字符 3-gram Jaccard | 原创度自检 |
| 编排层 | LLM 输出 JSON 解析 | 版式编排数据驱动 |
| 生图层 | 火山方舟 Agent Plan | doubao-seedream-5.0-lite（主通道） |
| 生图层 | 红狐 SDK doubao_image / gpt_image | 备用生图通道 |
| 排版层 | PIL (Pillow) + NotoSansSC 字体 | 图文卡合成（圆角 + 渐变 + 阴影） |
| 视频层 | 火山 Agent Plan TTS（seed-tts-2.0） | 清新女声 zh_female_qingxinnvsheng_uranus_bigtts |
| 视频层 | ffmpeg + ffprobe | zoompan Ken Burns + crossfade + BGM ducking |
| 视频层 | Incompetech Carefree.mp3 | BGM 资产（CC-BY 授权） |

### 4.3 数据源策略（双腿架构）

| 用途 | 数据源 | 选择理由 |
|---|---|---|
| 笔记抓取 | 红狐 RedFoxHub | 数据可靠性 + API 稳定性最佳（三轮评估定稿） |
| 内容生成 | 火山方舟 Agent Plan | Agent Plan 额度可复用生图+TTS，单篇 1-3 元 |

---

## 5. 相关 API Key 与配置

### 5.1 API Key 清单

| Key | 来源 | 用途 | 单篇成本 |
|---|---|---|---|
| `REDFOX_API_KEY` | redfox.hk 控制台 | 雷达搜索 + 笔记详情抓取 + 视频提文案 | ~0.1 元/次 |
| `ARK_API_KEY` | 火山方舟控制台 | 生图（doubao-seedream-5.0-lite） + TTS（seed-tts-2.0） | ~1-2 元/篇 |
| `LLM_API_KEY` | 阿里百炼控制台 | 仿写 + 版式编排（kimi-k3 主力） | ~0.01-0.05 元/篇 |

### 5.2 配置文件分布

| 配置项 | 文件位置 | 示例 |
|---|---|---|
| 微信 ClawBot 凭据 | `config.yaml` channel.weixin | account_id / token |
| 生图双通道 | `config.yaml` imagegen | providers.{ark,redfox_gpt,redfox_doubao} |
| LLM 优先级 | `config.yaml` llm.models | [kimi-k3, glm-5.3, qwen3.8-max, deepseek-v4-pro] |
| 雷达配置 | `config.yaml` radar | daily_hour / keywords / redfox_api_key |
| 交付阈值 | `config.yaml` deliver | video_max_mb=25 |
| 人设 | `agent/SOUL.md` | 旅游行程规划师 |
| 系统提示词 | `agent/prompts/system-prompt.md` | 仿写规则 + 五层拆解要求 |
| 验收清单 | `agent/CHECKLIST.md` | 9 项发布前必查 |

### 5.3 多 Bot 实例配置

```bash
# 默认 Bot
cp config.example.yaml config.yaml  # 填自己的微信 + key

# bot2 实例（不同微信号）
cp config.example.yaml config.bot2.yaml  # 填另一个微信号 + 同套 API key

# 启动
python3 main.py &                       # default Bot
python3 main.py --profile bot2 &        # bot2 Bot
```

每个 profile 隔离：
- `state.weixin/` token 缓存（扫码后落盘）
- `state.{profile}.db` SQLite 数据库（任务/补发队列）
- `workspace/{profile}/users/{uid}/` 产物目录

---

## 6. 模型选择逻辑

### 6.1 LLM 仿写模型优先级

实测对比（2026-09-19，4 模型实测定稿）：

| 优先级 | 模型 | 实测相似度 | 优点 | 缺点 |
|---|---|---|---|---|
| 主力 | kimi-k3 | 0.035 | 人设感最强，图片单元 5 个最全 | 速度中等（~126s） |
| Fallback 1 | glm-5.3 | 0.027 | 最原创，速度快（~82s） | 图片单元较少 |
| Fallback 2 | qwen3.8-max | - | 综合 | 中规中矩 |
| Fallback 3 | deepseek-v4-pro | - | 兜底 | **虚构资产风险**，仅兜底 |

**选择逻辑**：
1. 主力 kimi-k3 优先（质量优先）
2. 限流/失败自动 fallback 下一家
3. deepseek-v4-pro 仅作最后兜底（实测会虚构"亲子民宿绿皮书"等不存在的资产）
4. kimi-k3 不支持 temperature 参数 → 自动去参重试（`_chat_once` 已实现）

### 6.2 生图模型路由

| 通道 | Provider | 模型 | 状态 | 切换指令 |
|---|---|---|---|---|
| ark（主） | 火山 Agent Plan | doubao-seedream-5.0-lite | ✅ 实测通过 | 默认 / `/生图通道 ark` |
| redfox_gpt（备1） | 红狐 GPT-Image-2 | GPT-Image-2 | ⏸ 红狐 3203 仅付费 | `/生图通道 gpt` |
| redfox_doubao（备2） | 红狐豆包转发 | 豆包 Seedream Lite | ⏸ 红狐 3203 仅付费 | `/生图通道 doubao` |

**选择逻辑**：
- 主走 ark（火山 Agent Plan 额度可复用生图+TTS）
- 红狐付费后开启备源
- 自动 fallback：主通道失败 → 按顺序尝试备源

### 6.3 TTS 音色选择

音色兼容铁律：**仅 `*_uranus_bigtts`（2.0 系）音色可用于 seed-tts-2.0 资源**；`*_moon_bigtts`（1.0 系）会报 55000000。

当前默认：`zh_female_qingxinnvsheng_uranus_bigtts`（清新女声，旅行博主感）

可选音色清单详见 [agent/skills/xhs-video/SKILL.md](file:///workspace/agent/skills/xhs-video/SKILL.md)。

---

## 7. 定位、提示词与 Skill 清单

### 7.1 五层 .md 配置（Hermes 形态）

| 层 | 路径 | 内容 | 修改影响 |
|---|---|---|---|
| SOUL.md | [agent/SOUL.md](file:///workspace/agent/SOUL.md) | 人格定义 + 行为准则 + 禁止事项 | 改人格 = 改 SOUL.md |
| system-prompt.md | [agent/prompts/system-prompt.md](file:///workspace/agent/prompts/system-prompt.md) | 角色定义 + 仿写规则 + 五层拆解 + 输出格式 | 改仿写策略 = 改此文件 |
| SKILL.md ×4 | [agent/skills/](file:///workspace/agent/skills) | 各 Skill 触发条件 + 执行步骤 + 输出 Schema | 改流程 = 改对应 SKILL |
| config.yaml | [agent/config/config.yaml](file:///workspace/agent/config/config.yaml) | API key + 模型优先级 + 阈值 | 改配置 = 改 yaml |
| CHECKLIST.md | [agent/CHECKLIST.md](file:///workspace/agent/CHECKLIST.md) | 发布前 9 项验收清单（自动 + 人工） | 改验收标准 = 改此文件 |

### 7.2 Skill 清单

| Skill | 路径 | 入口函数 | 触发 |
|---|---|---|---|
| xhs-note-fetch | [skills/xhs-note-fetch/SKILL.md](file:///workspace/agent/skills/xhs-note-fetch/SKILL.md) | `note_fetch.fetch_topic_from_user_text` | 用户粘小红书链接 |
| xhs-rewrite | [skills/xhs-rewrite/SKILL.md](file:///workspace/agent/skills/xhs-rewrite/SKILL.md) | `rewrite.run_rewrite` | `/确认 N` 或链接抓取后 |
| xhs-imagepack | [skills/xhs-imagepack/SKILL.md](file:///workspace/agent/skills/xhs-imagepack/SKILL.md) | `imagepack.plan_layout + generate_pack` | rewrite 完成后顺序触发 |
| xhs-video | [skills/xhs-video/SKILL.md](file:///workspace/agent/skills/xhs-video/SKILL.md) | `video.make_video` | imagepack 完成后顺序触发 |

### 7.3 核心提示词要点（详见 system-prompt.md）

**同构异题铁律**：
- **保留**：结构骨架、钩子模式、段落节奏、排版符号习惯、标签策略、图组信息分工
- **替换**：主题细节、案例、点位/产品、数据、人设口吻——替换幅度到"一眼是新内容"
- **差异化**：对标讲"去哪"→ 仿写强化"怎么排/怎么做"的规划感
- **软广处理**：对标笔记中商业植入（约 40% 软广段）一律删除，替换为人设专业价值
- **结尾服务钩子**：与账号变现路径一致（"评论区报：人数/天数/预算，帮你重排一版"）
- **原创红线**：不得复用原文连续 8 字以上片段

**图文同源铁律**：第 N 段视频旁白只讲第 N 张图上承载的内容，不得串图。

**输出格式**：严格 JSON（title/content/tags/image_units/similarity）。

---

## 8. 部署形态演进

| 形态 | 部署位置 | 通道 | 电脑要开机 | 适用规模 | 当前 |
|---|---|---|---|---|---|
| **P0 本地电脑** | 个人 PC | ClawBot 个人号 | ✅ 必须 | 1 人 | 代码就绪 |
| **P1 本地常驻机** | 树莓派/Rock 5 | 同上 | ✅ 但常驻 | 5-10 人 | P0 稳定后迁移 |
| **P2 轻量云 + 微信云控** | 阿里云 + 家中常驻机 | ClawBot（家中常驻） | ❌ 不用 | 50-200 人 | 待 P1 稳定后重构 |
| **P3 企业微信 + SaaS** | ECS/K8s | 企业微信官方 API | ❌ 不用 | B 端 | 重做通道 |

### 8.1 多 Bot 隔离（v1.1 已落地）

方案 A 多进程独立实例：
- `python3 main.py --profile botN` 加载独立 config 文件
- bot_id（profile 名）从 base.TriggerAdapter 注入到 Router
- workspace 路径分层 `workspace/{bot_id}/users/{uid}/`
- SQLite `state.{bot_id}.db` 物理隔离
- 10 用户内本地多 Bot 实测可行（参考 [refactor-plan §11](file:///workspace/docs/specs/2026-09-19-agent-refactor-plan.md)）

---

## 9. 风险定性（v1.1 修正口径）

| 风险 | 是否真实 | 严重度 | 说明 |
|---|---|---|---|
| 协议合规风险 | ❌ 不存在 | 无 | ClawBot 是微信 2026.3 开放官方协议，合规产品形态 |
| 单微信号消息频率上限 | ⚠ 真实 | 中 | 5-10 人安全，20+ 用户阈值由 V5 实测 |
| 常驻进程约束 | ✅ 真实 | 高 | 长轮询需常驻设备 |
| token 过期（-14） | ✅ 真实 | 中 | 源码已知有 10 分钟自动恢复 |
| 媒体大小阈值未知 | ✅ 真实 | 低 | V4 实测后写 config |
| 多人并发任务排队 | ✅ 真实 | 中 | 单篇 12 分钟，3 人并发即排队 |
| 各家 API 限流 | ✅ 真实 | 中 | 已有 fallback |

---

## 10. 网站版本开发指引

基于本文档展开 Web 版开发时，可复用以下组件：

### 10.1 可直接复用的内核

- `agent/pipeline/` 全套（note_fetch / rewrite / imagepack / imagegen / video / radar）
- `agent/config/config.yaml` 结构
- `agent/SOUL.md` + `prompts/system-prompt.md` + `skills/*/SKILL.md` + `CHECKLIST.md`

### 10.2 需要重做的层

| 层 | 微信版 | Web 版改造 |
|---|---|---|
| 触发层 | bot_weixin.py + TriggerAdapter | 新建 `bot_web.py`（HTTP API 触发）或走 RESTful API |
| 路由层 | router.py（含 SQLite） | 改用任务队列（Celery/RQ + Redis）+ 数据库（PostgreSQL） |
| 交付层 | deliver.py（微信直发） | 改为文件下载链接 + 站内通知 + 邮件 |
| 多租户隔离 | bot_id + SQLite 分文件 | 数据库加 tenant_id 字段 + S3 路径分层 |
| 鉴权 | 微信白名单 | JWT / OAuth |

### 10.3 网站版最小可行产品（MVP）

| 页面 | 功能 |
|---|---|
| 首页 | 提交链接 / 关键词 |
| 任务列表 | 显示状态：抓取中→仿写中→生图中→视频合成中→完成 |
| 任务详情 | 4 张图文卡 + 视频播放器 + 下载按钮 |
| 设置 | API key 配置（用户自带） |
| 历史 | 已生产笔记归档 |

---

## 11. 附录

- 设计文档：[2026-09-17-xhs-content-agent-design.md](file:///workspace/docs/specs/2026-09-17-xhs-content-agent-design.md)
- 改造清单：[2026-09-19-agent-refactor-plan.md](file:///workspace/docs/specs/2026-09-19-agent-refactor-plan.md)
- ClawBot 验证清单：[2026-09-19-clawbot-integration-checklist.md](file:///workspace/docs/specs/2026-09-19-clawbot-integration-checklist.md)
- Agent 使用指南：[2026-09-19-agent-usage-guide.md](file:///workspace/docs/specs/2026-09-19-agent-usage-guide.md)
- 标准提示词源文档：[docs/prompts/xhs-standard-prompts.md](file:///workspace/docs/prompts/xhs-standard-prompts.md)
- 代码仓库：[github.com/hj363049394/video_generate/tree/trae/agent-F784ti](https://github.com/hj363049394/video_generate/tree/trae/agent-F784ti)
