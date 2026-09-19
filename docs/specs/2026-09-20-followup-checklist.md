# 小红书笔记仿写 Agent · 后续待做清单

> 版本：v1.0 · 日期：2026-09-20
> 状态：代码完整、回归测试 8 项全过、已推送 `trae/agent-F784ti` 分支
> 目的：明确剩余手工步骤和后续开发清单

---

## 一、用户必做的手工步骤（验证 + 部署）

以下步骤涉及微信扫码、API 控制台等无法自动化的环节，必须由用户在本地执行。

### M1. 微信扫码登录 V1 验证（必须，5 分钟）

**目的**：让 bot 微信号上线，建立 bot 与用户的微信通道。

**详细流程**：

```bash
# 1. 在本地电脑 clone 代码
git clone -b trae/agent-F784ti https://github.com/hj363049394/video_generate.git
cd video_generate/agent

# 2. 安装依赖
pip install -r requirements.txt  # 主要：redfox-python-sdk / aiohttp / cryptography / pillow / openai
sudo apt install ffmpeg ffprobe   # 视频合成需要

# 3. 配置 API key
cp config.example.yaml config.yaml
# 编辑 config.yaml 填入：
#   channel.weixin.account_id = （先留空，扫码后自动落盘）
#   imagegen.providers.ark.api_key = ARK_API_KEY_PLACEHOLDER
#   llm.api_key = sk-ws-...（百炼）
#   radar.redfox_api_key = ak_890e5aa9450d40eabfeb15d71d3c53f9

# 4. 扫码登录
export ARK_API_KEY=ARK_API_KEY_PLACEHOLDER
python3 main.py --qr-login
# 屏幕会显示二维码，用准备好的 bot 微信号扫码
# 看到 "登录成功" 后 token 自动落盘到 state.weixin/

# 5. 启动 bot
python3 main.py
# 看到 "Bot default 启动，等待消息..." 即可
```

**验证标准**：
- 扫码后无报错，看到"登录成功"
- 重启 `python3 main.py` 不需要再扫码（token 已缓存）
- bot 微信号在线（手机端可看到）

---

### M2. 微信功能实测 V2-V5（必须，约 30 分钟）

**目的**：验证从微信端到端能用所有指令。

**详细流程**（用你的个人微信号加 bot 微信号为好友后依次发送）：

| 步骤 | 发什么 | 期望返回 | 验证点 |
|---|---|---|---|
| V2 | `/help` | 帮助文本 | bot 在线 + 指令响应 |
| V3 | `/生图 南京梧桐大道秋日午后` | 1 张竖图 | 生图主通道（火山 Agent Plan） |
| V4 | `/生图通道 gpt` 然后 `/生图 测试` | 提示未开启或图 | 通道切换逻辑 |
| V5 | `/选题` | 当日选题清单 Top 5 | 雷达拉取 + 缓存读取 |

**失败处理**：
- V2 失败 → 检查 bot 是否在线 + allow_users 白名单
- V3 失败 → 检查 ARK_API_KEY 环境变量
- V5 失败 → 检查 REDFOX_API_KEY + 雷达是否当日跑过

---

### M3. 拉模式端到端 V6（必须，约 15 分钟）

**目的**：验证用户粘贴小红书链接的完整生产链路。

**详细流程**：

```
1. 在微信里给 bot 发：
   https://www.xiaohongshu.com/explore/6aaa973b000000002603a54e

2. bot 应回复：
   收到链接，正在抓取笔记详情（红狐）…
   已抓取【图文笔记】：中秋国庆带娃游沙巴 邂逅山海萤火果冻海
   点赞 1011 · 收藏 233 · 评论 208
   任务 xxx 已入队，开始仿写+生图+视频合成…
   /状态 查看进度。

3. 等待约 12-15 分钟，期间可发 /状态 查看进度

4. 最终收到：
   - 4 张图文卡（逐张发送）
   - 1 个视频文件（≤25MB 直发，超限提示人工取件）
```

**验证标准**：
- 链接抓取成功（看到笔记标题和点赞数）
- 仿写完成（标题与原笔记不同，但同构异题）
- 4 张图文卡完整收到
- 视频能播放

**失败处理**：
- 抓取失败 → 检查 REDFOX_API_KEY + 网络到 redfox.hk
- 仿写失败 → 检查 llm.api_key（百炼）
- 生图失败 → 检查 ARK_API_KEY
- 视频合成失败 → 检查 ffmpeg 已安装
- 视频过大未直发 → 调整 `config.yaml` 的 `deliver.video_max_mb` 或手动取件

---

### M4. 视频笔记场景 V7（建议，约 20 分钟）

**目的**：验证视频笔记的口播文案提取功能。

**详细流程**：

```
1. 找一个公开的视频笔记链接，发给 bot

2. bot 应回复：
   收到链接，正在抓取笔记详情（红狐）…
   已抓取【视频笔记（已提取口播文案）】：xxx
   ...
   任务 xxx 已入队...

3. 后续流程同 M3
```

**验证标准**：
- 标题前缀显示"视频笔记（已提取口播文案）"
- 仿写正文长度合理（说明拿到了口播文案）

---

### M5. 多 Bot 隔离 V8（可选，仅多号用户）

**目的**：验证多微信号并行使用。

**详细流程**：

```bash
# 1. 复制配置
cp config.yaml config.bot2.yaml
# 编辑 config.bot2.yaml：留同一套 API key，account_id/token 留空（待扫码）

# 2. bot2 扫码
python3 main.py --profile bot2 --qr-login
# 用第二个微信号扫码

# 3. 同时启动两个 Bot
python3 main.py &
python3 main.py --profile bot2 &

# 4. 用两个微信号分别给两个 bot 发消息，验证：
#    - 数据库隔离（state.default.db vs state.bot2.db）
#    - workspace 隔离（workspace/default/ vs workspace/bot2/）
#    - 一个 bot 失败不影响另一个
```

---

## 二、代码层后续开发清单（开发者）

### D1. `/换角度 N 描述` 指令实装（中优先级）

**当前状态**：[router.py](file:///workspace/agent/bot/router.py) 是桩，回复"Phase 1.5 接入"。

**实装思路**：
- 解析 N 和描述 → 修改 tasks 表 topic 的 description 字段
- 重新触发 _run_task

### D2. LLM 意图解析兜底（中优先级）

**当前状态**：自由文本（如"仿写一下"）无法触发流程。

**实装思路**：
- router._dispatch 末尾增加 LLM 意图分类
- 意图 → 指令映射（如"仿写第 2 条" → /确认 2）

### D3. 视频压缩降级（高优先级，M3 后做）

**当前状态**：视频 > 25MB 直接提示人工取件，用户体验差。

**实装思路**：
- video.py 输出后调 `ffmpeg -i in.mp4 -crf 28 -b:v 1M out.mp4`
- 循环压缩直到 ≤ video_max_mb 或画质不可接受

### D4. 推送节流（中优先级）

**当前状态**：连续推送可能撞限。

**实装思路**：
- router._safe_send 加队列 + 随机延迟 5-15s
- 批量消息合并发送

### D5. 任务状态查询优化（低优先级）

**当前状态**：/状态 只返回最近 1 条。

**实装思路**：
- 返回最近 5 条 + 各阶段进度（抓取/仿写/生图/视频）
- 进度条文本展示

### D6. 视频笔记字段透传（低优先级）

**当前状态**：视频笔记的 cover_image 没用上（设计上走新生图，不复用原图）。

**可选改进**：把 cover_image 作为 reference_image 传给生图，提升首图相关性。

### D7. 失败任务重试（低优先级）

**当前状态**：失败任务状态 = failed，无重试入口。

**实装思路**：
- 新增 `/重试 N` 指令
- 从 failed 状态恢复为 queued 重新触发 _run_task

---

## 三、运营 / 内容层后续清单

### O1. 选题关键词调优（每周一次）

**当前**：`config.yaml` 的 `radar.keywords` 是初始值 `[带娃游, 亲子旅行, 行程规划, 避寒]`。

**调优依据**：
- 红狐返回的 Top 5 笔记标题里高频出现的关键词
- 小红书旅游赛道当周热搜词

### O2. 人设调优（每月一次）

**当前**：[agent/SOUL.md](file:///workspace/agent/SOUL.md) 是初始版本。

**调优依据**：
- 仿写产出的笔记风格是否稳定
- 用户反馈（"不像旅游规划师"等）

### O3. 仿写提示词调优（每月一次）

**当前**：[agent/prompts/system-prompt.md](file:///workspace/agent/prompts/system-prompt.md) 是初始版本。

**调优依据**：
- 原创度连续接近 0.30 红线 → 提示词加"必须替换更多细节"
- 仿写风格偏离人设 → 提示词加人设锚点

---

## 四、商业化 / 架构演进清单

### A1. 本地常驻机迁移（P0 → P1）

**触发**：单 bot 稳定运行 2 周后。

**做法**：
- 买一台树莓派 5 / Rock 5B / 旧 Mac mini
- 同一套代码 + 配置搬过去
- systemd 开机自启 + 崩溃重启

### A2. Web Dashboard MVP（P2 启动）

**触发**：5-10 个种子用户验证产品形态后。

**复用范围**：
- `agent/pipeline/` 全套（note_fetch / rewrite / imagepack / imagegen / video / radar）
- `agent/SOUL.md` + `prompts/system-prompt.md` + `skills/*/SKILL.md` + `CHECKLIST.md`
- `agent/config/config.yaml` 结构

**重做范围**：
- 触发层：bot_weixin.py → RESTful API
- 路由层：SQLite → PostgreSQL + Celery/RQ 任务队列
- 交付层：微信直发 → 文件下载链接 + 站内通知
- 多租户：bot_id → tenant_id 字段
- 鉴权：微信白名单 → JWT/OAuth

详见 [产品方案文档 §10](file:///workspace/docs/specs/2026-09-20-xhs-agent-product-spec.md)。

### A3. 企业微信双轨（P3）

**触发**：ClawBot 协议稳定性出现问题时。

**做法**：新建 `bot/bot_qiyeweixin.py` 实现 TriggerAdapter，作为备选渠道。

---

## 五、安全清单

### S1. API Key 安全（高优先级，立即做）

**当前问题**：`config.yaml` 含真实 key 已推送到 GitHub。

**处理流程**：

1. **检查仓库可见性**：访问 [github.com/hj363049394/video_generate/settings](https://github.com/hj363049394/video_generate/settings)
   - 如果是 public → 立即改为 private（Settings → Change visibility → Private）
   - 如果已经是 private → 跳到第 2 步

2. **重置 key**（如果曾 public 过）：
   - 红狐：redfox.hk 控制台 → 重置 key → 更新 config.yaml
   - 百炼：bailian.console.aliyun.com → 重置 key → 更新 config.yaml
   - 火山：console.volcengine.com → 重置 key → 更新 config.yaml

3. **加 .gitignore**（防止再次提交）：
   ```
   agent/config/config.yaml
   agent/state/
   agent/workspace/
   ```
   把 `config.example.yaml` 作为唯一提交的配置模板，key 通过环境变量注入。

### S2. 微信 bot 号专用（建议）

- 不用日常微信号做 bot
- 准备一个专用号或新号
- 即使出问题也不影响主号社交

---

## 优先级总览

| 优先级 | 事项 | 类型 | 工作量 |
|---|---|---|---|
| 🔴 立即 | S1 API Key 安全检查 | 安全 | 5 分钟 |
| 🔴 必须 | M1 微信扫码 V1 | 手工验证 | 5 分钟 |
| 🔴 必须 | M2 微信功能 V2-V5 | 手工验证 | 30 分钟 |
| 🔴 必须 | M3 拉模式端到端 V6 | 手工验证 | 15 分钟 |
| 🟡 建议 | M4 视频笔记 V7 | 手工验证 | 20 分钟 |
| 🟡 建议 | M5 多 Bot 隔离 V8 | 手工验证 | 视情况 |
| 🟡 中 | D3 视频压缩降级 | 代码 | 中 |
| 🟡 中 | D1 /换角度 实装 | 代码 | 小 |
| 🟡 中 | D2 LLM 意图兜底 | 代码 | 中 |
| 🟢 低 | D4 推送节流 | 代码 | 小 |
| 🟢 低 | D5 状态查询优化 | 代码 | 小 |
| 🟢 低 | D6 视频封面透传 | 代码 | 小 |
| 🟢 低 | D7 失败任务重试 | 代码 | 小 |
| ⚪ 周期 | O1 关键词调优 | 运营 | 每周 |
| ⚪ 周期 | O2 人设调优 | 运营 | 每月 |
| ⚪ 周期 | O3 提示词调优 | 运营 | 每月 |
| ⚪ 长期 | A1 常驻机迁移 | 架构 | 视稳定后 |
| ⚪ 长期 | A2 Web Dashboard | 架构 | P2 启动 |
| ⚪ 长期 | A3 企业微信双轨 | 架构 | P3 视情况 |
