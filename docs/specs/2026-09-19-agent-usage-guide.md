# 小红书仿写 Agent · 使用与验收指南

- 版本：v1.0 · 日期：2026-09-19
- 对象：部署在你本地电脑（Windows + WSL2 Ubuntu 或任意 Linux/macOS）的正式运行指南

## 0. 当前能力边界（先看这个）

| 能力 | 状态 | 说明 |
|---|---|---|
| 微信收发（文本/图片/视频/文件） | ✅ 代码就绪 | 等 V1 扫码凭据联调 |
| 生图 · 火山 Agent Plan | ✅ 实测通过 | 943KB 竖图 20.9s，POC 尺寸不变 |
| 生图 · 红狐 GPT/豆包 | ⏸ 代码就绪 | 红狐 3203 仅付费，充值后 config 开关即用 |
| 雷达选题（抓取+数值评分+推送） | ✅ 代码就绪 | 语义评分属 Phase 1.5 |
| 拆解仿写（文案+原创度自检） | ✅ 端到端实测通过 | kimi-k3 主力 + 三级 fallback（百炼） |
| 图文卡片排版（4 张成品图） | ✅ 端到端实测通过 | LLM 版式编排 + 双通道底图 + PIL 排版 |
| 视频合成 | ✅ 端到端实测通过 | 图文同源 5 镜头，74s 成片 |

**全链路已实测**（2026-09-19，沙箱）：仿写 → 编排 → 4 图 → 视频，单篇全自动约 12 分钟。`/确认 N` 完整产出 = 文案 + 4 图 + 成片视频。

## 1. 部署（一次性）

### 1.1 环境

- WSL2 Ubuntu（或 Linux/macOS）+ Python ≥ 3.11
- 安装 uv（可选，也可用 pip）：`curl -LsSf https://astral.sh/uv/install.sh | sh`

### 1.2 获取代码

从开发沙箱拿到 `agent/` 目录 + `docs/` 目录（两种方式任选）：

- 方式 A（推荐）：建一个 GitHub 私有仓库（你已有 GitHub 账号），把仓库地址给开发方推送，然后本地 `git clone`
- 方式 B：打包 zip 经预览服务器下载，解压到 `~/xhs-agent/`

### 1.3 安装依赖

```bash
cd ~/xhs-agent/agent
pip install -r requirements.txt        # aiohttp / cryptography / pyyaml / redfox-sdk / qrcode
```

### 1.4 配置

```bash
cp config/config.example.yaml config/config.yaml
```

必填四处：

| 配置项 | 填什么 | 怎么获取 |
|---|---|---|
| `llm.base_url / api_key / models` | 仿写对话模型（OpenAI 兼容，支持优先级列表 + 自动 fallback） | 阿里百炼（推荐，聚合三家模型）：[bailian.aliyun.com](https://bailian.aliyun.com) → 开通 → API-KEY → 创建 `sk-` key；base_url `https://dashscope.aliyuncs.com/compatible-mode/v1`；models 按优先级 `qwen3.8-max / glm-5.3 / deepseek-v4-pro`（2026-09-19 实测三家均可用） |
| `channel.weixin.account_id / token` | 可留空，走 §2.1 扫码自动落盘 | `--qr-login` 产物；或从验证手册 V1 的 `~/.hermes/weixin/accounts/*.json` 抄录 |
| `channel.weixin.allow_users` | 你的 ilink user_id | 首次发消息时看终端日志（有打印指引） |
| `channel.weixin.home_uid` | `weixin:{你的ilink_user_id}` | 雷达每日推送目标 |

生图 key 用环境变量（也可写进 config）：`export ARK_API_KEY=ark-9790…`、`export REDFOX_API_KEY=ak_890e…`

## 2. 首次启动

### 2.1 扫码登录（一次性）

```bash
python3 main.py --qr-login
```

终端出现二维码 URL → 浏览器打开 → 手机微信扫码确认 → 提示"微信登录成功"，token 落盘 `state/weixin/`，重启免扫。

### 2.2 启动 bot

```bash
python3 main.py            # 前台运行；Ctrl+C 退出
# 长期常驻：nohup python3 main.py > bot.log 2>&1 &
# （后续可配 systemd user service，同验证手册方式）
```

### 2.3 白名单（首条消息后）

1. 微信给机器人发任意消息 → 收到"未授权用户"回复，其中带你的 ID
2. 把该 ID 填入 `config.yaml` 的 `allow_users`，并把 `home_uid` 填为 `weixin:{该ID}`
3. 重启 `main.py`，再发消息即正常

### 2.4 立即测雷达（可选）

```bash
python3 main.py --radar-now    # 抓取+评分+打印当日清单
```

## 3. 日常使用（微信里发指令）

| 指令 | 作用 |
|---|---|
| /help | 指令帮助 |
| /选题 | 查看当日选题清单 Top 5（热度+角度） |
| /确认 2（或"仿写第2条"） | 确认选题 → 自动仿写 → 回传文案 + 相似度 + 封面底图 |
| /状态 | 查最近 5 个任务进度 |
| /生图 一段画面描述 | 直接生成竖版图（实测生图通道用） |
| /生图通道 ark / gpt / doubao | 切换生图主通道 |
| 粘贴小红书笔记链接 | 手动对标入队（Phase 1.5 全自动） |

每日自动：`radar.daily_hour`（默认 8 点）自动跑雷达并推送清单到你微信；推送失败自动进补发队列，你下次发消息时补送。

## 4. 验收流程（判断 Agent 是否正常）

按顺序 5 步，每步通过再下一步：

| 步骤 | 操作 | 通过标准 |
|---|---|---|
| ① 通道 | 微信发 /help | 数秒内收到指令帮助 |
| ② 生图 | 微信发 /生图 秋天南京梧桐大道 | 收到一张竖版图（默认 ark 通道） |
| ③ 雷达 | 本地跑 --radar-now，微信发 /选题 | 收到当日清单（含热度与角度） |
| ④ 仿写 | 微信发 /确认 1 | 收到：标题+正文+标签+服务钩子、相似度报告（<0.30 ✓）、封面底图 1 张 |
| ⑤ 推送 | 等次日早 8 点（或 --radar-now 后隔日观察） | 微信自动收到选题清单 |

Phase 1.5 完成（图文排版+视频合成）后追加第 ⑥ 步：/确认 N → 收到 4 张成品图文卡片 + 成片视频。

## 5. 常见问题

| 现象 | 原因与处理 |
|---|---|
| bot 完全无响应 | token 失效：重跑 `--qr-login`；或 `main.py` 终端看报错 |
| 仿写报"未配置仿写 LLM" | `config.yaml` llm 段没填（见 §1.4） |
| 生图报"3203 仅付费" | 用了红狐通道但未充值：`/生图通道 ark` 切回，或充值后启用 |
| 发消息 10 分钟无回复又自动恢复 | iLink -14 会话过期（已知行为，源码预验证 #6）；频繁出现反馈给开发 |
| 推送没收到 | 在补发队列：给 bot 发任意消息触发补发；仍无则看 main.py 日志 |
