# ClawBot 对接验证清单（微信渠道 · bot_weixin.py 前置验证）

- 版本：v2.0 · 日期：2026-09-19（v2.0：沙箱已完成源码级预验证，本清单收敛为「本地必做项」）
- 目的：在写一行 `bot_weixin.py` 代码之前，把微信 ClawBot 通道验通并测出服务端能力边界；结果回填设计文档 3.7 节
- 执行环境：**你的本地电脑**（Windows + WSL2 Ubuntu）。微信扫码授权需真人 + 手机，无法在沙箱代做
- 知识来源：`MyAgent/knowledge_base/02-platform-integrations/02-wechat-clawbot.md`（对接教程）、`01-hermes-agent/03-hermes-agent-installation.md`（安装与踩坑）

---

## 一、沙箱预验证结论（2026-09-19 已完成，无需本地重复）

沙箱已克隆 hermes-agent 仓库并完成 `gateway/platforms/weixin.py`（1258 行）+ `gateway/pairing.py` 源码级分析。以下结论已确认：

| # | 验证项 | 结论 | 源码证据 |
|---|---|---|---|
| 1.1 | 协议 | iLink Bot API，`https://ilinkai.weixin.qq.com`，35s 长轮询 getupdates 驱动收消息；媒体经 AES-128-ECB 加密 CDN（novac2c.cdn.weixin.qq.com） | weixin.py L48-58 |
| 1.2 | 消息能力 | **五类双向**：文本 / 图片 / 视频 / 文件 / 语音（出站语音降级为文件附件播放）；入站另支持引用消息 | L58-59, L650-687 |
| 1.3 | 文本规格 | 单条上限 2000 字符，1800 阈值自动分片，分片间隔默认 1.5s；支持代码块、markdown 表格 | L694-696 |
| 1.4 | 媒体大小 | **客户端无硬限制**（rawsize 直传 getuploadurl），真实阈值在 iLink 服务端 → 仍需本地实测（见 3.2） | L1144-1147 |
| 1.5 | 限流机制 | -2 频率限制自动退避（重试 4 次 ×3 倍增）+ 30s 熔断器；**入站 3s 防抖**：快速连发多条合并为一个处理回合，连发不会打挂 | L56, L712-730 |
| 1.6 | 会话过期 | errcode -14 → 自动暂停 10 分钟后恢复（不崩溃，但会静默 10 分钟——压测关注项） | L826-830 |
| 1.7 | **cron 主动推送** | **可行**：tokenless 降级发送路径专为 cron 保留；极端情况（用户长期未发消息）推送失败需「下次对话补发」兜底——已写入设计文档 3.4 | L974-978, L1224 |
| 1.8 | **群聊** | **不可用**：QR 登录的 iLink bot 身份（…@im.bot）无法被拉进普通微信群，源码明确警告。方案按纯私聊 DM 设计 | L777-783 |
| 1.9 | DM pairing | 陌生人首条消息 → bot 回复一次性配对码 → 管理员终端 `hermes pairing approve` 批准；数据存 `~/.hermes/platforms/pairing/`（0600） | pairing.py |
| 1.10 | 配置方式 | **可完全非交互**：`config.yaml` 写 `platforms.weixin`（token / extra.account_id / dm_policy / group_policy / allow_from）+ `WEIXIN_*` 环境变量即可，唯一交互环节是扫码 | L698-734 |
| 1.11 | QR 登录 | 480s 超时；二维码过期自动刷新（最多 3 次）；扫码确认后 token 持久化 `~/.hermes/weixin/accounts/*.json`，重启无需重扫 | L592-647 |
| 1.12 | 许可证 | **MIT** —— WeixinAdapter 代码可合法移植/裁剪进我们的 bot_weixin.py | LICENSE |
| 1.13 | 已踩平的坑 | 图片 aes_key 必须 base64(hex)（否则灰图）；出站语音气泡未验证、降级附件；CDN 上传必须 POST（PUT 404）；代理环境 keepalive 泄漏已修复 | L1157, L1127, L1150, L90-107 |

**原清单第六节「扩展点探查」的答案**（无需再探查）：

| 形态 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| A · hermes skill | 流水线做成 skill，微信消息 → hermes agent → skill 执行 | 部署最简单 | 每条消息过 hermes 的 LLM（要配 key），时延与成本高，Router/指令集被 hermes 接管 |
| B · hermes 通道 + 自建 Router | hermes gateway 只跑 weixin adapter，经 hook/plugin 转发到我们的 Router | 协议升级有人维护 | hermes 是全家桶（仍需 LLM key 才能跑通消息流），部署重 |
| C · 移植 WeixinAdapter（推荐） | bot_weixin.py 按 MIT 源码裁剪移植（仅依赖 aiohttp + cryptography），挂自己的 Router | 最贴合设计文档 3.3 TriggerAdapter；部署最轻（无需 hermes 主程序、无需额外 LLM key） | iLink 协议演进需自己跟（channel_version 2.2.0） |

**推荐路径**：本地验证期仍按本清单用 hermes gateway 快速验通道（它是最现成的扫码+收发工具）；`bot_weixin.py` 开发期走形态 C 移植。两者不冲突：验证结论（服务端阈值、稳定性）直接适用于 C。

---

## 二、验收总标准（v2.0 收敛后）

以下 4 条全部达成即启动 `bot_weixin.py` 开发（1/2/3 已由源码预验证覆盖，不再列入）：

1. 网关以 systemd user service 常驻，重启电脑后自动恢复
2. 20MB 视频（POC 成片量级）能直发，并测出服务端失败阈值
3. 第二个微信号经 DM pairing 配对后可用（多用户最小验证）
4. 连续 7 天无不可恢复故障（掉线自动重连，或一条命令可恢复）

任何一步卡住，把终端完整报错贴回来。

## 三、本地必做项

### 3.1 环境与安装（WSL2 内）

- [ ] Windows 已装 WSL2 + Ubuntu 20.04+（PowerShell 管理员执行 `wsl --install` 后重启）
- [ ] `python3 --version` ≥ 3.11
- [ ] 安装 uv：`curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.bashrc`
- [ ] LLM API Key 一个（国内推荐智谱 GLM：[open.bigmodel.cn](https://open.bigmodel.cn/)；hermes 回复消息需要）

```bash
cd ~
git clone --depth 1 https://github.com/NousResearch/hermes-agent.git   # 必须 --depth 1（完整克隆 >80MB 极慢）
cd hermes-agent
uv venv venv && source venv/bin/activate
uv pip install -e ".[messaging,cron,cli,pty,mcp,dev]" \
  -i https://pypi.tuna.tsinghua.edu.cn/simple/ \
  --trusted-host pypi.tuna.tsinghua.edu.cn
```

踩坑预警（知识库实测）：PyPI 直连超时 → 清华源；**不要用阿里源**（版本不全，依赖解析报 `No solution found`）。

验证：`hermes doctor` 无红色报错。

### 3.2 配置与扫码

```bash
hermes gateway setup
```

| 提示 | 选择 |
|---|---|
| 平台列表 | **Weixin/WeChat（微信）** |
| `Start QR Login now? [Y/n]` | **Y** → 复制终端二维码 URL 到浏览器 → 手机微信扫码 |
| 私聊权限 | **Use DM pairing approval** |
| 群聊权限 | **Disable group chats**（群聊本就不可用，见 1.8） |
| `home channel? [Y/n]` | **Y** |
| systemd service | **Y** → **User service**（无需 sudo） |

配对：微信里给机器人发任意消息 → 得到一键配对命令 → 回终端执行 → 对接成功。

验收：
- [ ] `~/.hermes/.env` 已写入账号 ID / Token
- [ ] `hermes gateway status` 显示 Weixin 已连接
- [ ] 微信发「你好」能收到 AI 回复

### 3.3 功能实测（聚焦源码看不到的服务端阈值）

| # | 项目 | 操作 | 预期 | 结果 |
|---|---|---|---|---|
| 3.3.1 | 视频 20MB | 发 POC 成片 video_001.mp4（20MB · 1080×1440 · 50s，可从 preview.html 下载） | 直发成功且可播放 | ☐ |
| 3.3.2 | 视频阈值 | 依次发 50MB / 100MB 视频 | 记录服务端失败阈值 → 定「超限转网盘链接」的阈值 | ☐ |
| 3.3.3 | 连发限流 | 1 分钟连发 10 条文本 | 预期被 3s 防抖合并（源码 1.5）；记录有无服务端限流提示 | ☐ |
| 3.3.4 | 长文案 | 发 200 字笔记文案 | 完整送达不截断（源码 1.3 已确认，快速过） | ☐ |
| 3.3.5 | 图片 1728×2304 | 发 POC 规格竖图 | 接收方清晰无灰图（源码已修灰图坑，快速过） | ☐ |
| 3.3.6 | cron 推送 | `hermes cron` 建一条定时任务，向自己推送一条文本（隔夜后触发一次） | 验证 tokenless 降级推送（源码 1.7）；记录隔夜首推是否成功 | ☐ |

### 3.4 多用户验证（对应设计 3.6 节）

- [ ] 第二个微信号私聊机器人 → 触发 pairing → 终端 `hermes pairing approve` → 第二个用户可正常对话
- [ ] 两用户会话互不串扰
- [ ] 未配对陌生人发消息被拒（白名单生效）

### 3.5 一周稳定性压测

| 天 | 检查项 | 结果 |
|---|---|---|
| Day 1 | 重启电脑 → `hermes gateway status` 确认自动拉起 | ☐ |
| Day 1-7 | 每日 1 条测试消息，记录送达延迟 | ☐ |
| 全程 | 有无 -14 会话过期（表现为静默 10 分钟，源码 1.6）；掉线是否需重扫 | ☐ |
| Day 7 | 汇总：故障次数 / 恢复方式 / 能否无人值守 | ☐ |

日志：`journalctl --user -u hermes-gateway -f`（服务名以安装输出为准）

## 四、结果回填模板

| 项 | 结果 |
|---|---|
| 20MB 视频直发 | 通过 / 失败 |
| 服务端视频阈值 | __ MB |
| 连发限流 | 无 / 有（现象：__） |
| cron 隔夜推送 | 成功 / 失败（需补发兜底） |
| 多用户 pairing | 通过 / 失败 |
| 7 天故障 | __ 次，恢复方式 __（重点：-14 静默出现次数） |
| 总体结论 | 可开发（形态 C 移植）/ 需观望 / 放弃微信渠道 |

## 五、风险与备注

- ClawBot 2026.3 开放，iLink 协议可能变动：`bot_weixin.py` 保持薄适配层（设计 3.3 已隔离），协议层大改不影响内核；channel_version 2.2.0 记录在案
- 个人微信挂机器人存在风控可能：内测期用小号、低频、禁群聊（本就不可用）
- -14 会话过期会静默 10 分钟：压测重点观察，若高频出现需在 bot_weixin.py 加主动重连逻辑
- 改权限 / 重新登录：重跑 `hermes gateway setup` 即可
- 若 3.1/3.2 即卡死：贴报错回来先判断环境问题；确属通道不可用则降级「飞书先行」
