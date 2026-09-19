# ClawBot 对接验证清单（微信渠道 · bot_weixin.py 前置验证）

- 版本：v1.0 · 日期：2026-09-19
- 目的：在写一行 `bot_weixin.py` 代码之前，先在本地把微信 ClawBot 通道完整跑通并测出能力边界；验证结果直接回填设计文档 3.7 节「待实测」项
- **执行环境：你的本地电脑**（Windows + WSL2 Ubuntu）。微信扫码授权无法在沙箱内代做，故出本清单
- 知识来源：`MyAgent/knowledge_base/02-platform-integrations/02-wechat-clawbot.md`（ClawBot 对接教程）、`MyAgent/knowledge_base/01-hermes-agent/03-hermes-agent-installation.md`（Hermes 安装与踩坑实录）

## 0. 验收总标准

以下 5 条全部达成，才启动 `bot_weixin.py` 开发：

1. 网关以 systemd user service 常驻，重启电脑后自动恢复
2. 文本 / 图片 / 视频 / 文件四类消息收发正常
3. 20MB 视频（POC 成片量级）能直发
4. 第二个微信号经 DM pairing 配对后可用（多用户最小验证）
5. 连续 7 天无不可恢复故障（掉线自动重连，或一条命令可恢复）

任何一步卡住，把终端完整报错贴回来，我来判断是环境问题还是通道问题。

## 一、环境准备（WSL2 内）

- [ ] Windows 已装 WSL2 + Ubuntu 20.04+（PowerShell 管理员执行 `wsl --install` 后重启）
- [ ] Python ≥ 3.11：`python3 --version`
- [ ] 安装 uv：`curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.bashrc`
- [ ] 准备一个 LLM API Key（国内推荐智谱 GLM：[open.bigmodel.cn](https://open.bigmodel.cn/) 注册创建）

验收：前三条命令均有正常版本输出。

## 二、安装 Hermes Agent

```bash
cd ~
git clone --depth 1 https://github.com/NousResearch/hermes-agent.git   # 必须 --depth 1
cd hermes-agent
uv venv venv && source venv/bin/activate
uv pip install -e ".[messaging,cron,cli,pty,mcp,dev]" \
  -i https://pypi.tuna.tsinghua.edu.cn/simple/ \
  --trusted-host pypi.tuna.tsinghua.edu.cn
```

踩坑预警（来自知识库实测记录）：

- 完整克隆仓库 >80MB，国内极慢 → **必须 `--depth 1`**
- PyPI 直连超时 → 用清华源；**不要用阿里源**（版本不全，依赖解析报 `No solution found`）

验证：

- [ ] `hermes doctor` 无红色报错

## 三、配置模型与微信网关

### 3.1 配置 LLM

```bash
# 项目根目录 .env
echo 'GLM_API_KEY=你的智谱Key' >> .env
```

```yaml
# ~/.hermes/config.yaml
model:
  default: glm-5-turbo      # 或 glm-4-plus / glm-4-flash
  provider: zai
```

### 3.2 启动网关配置

```bash
hermes gateway setup
```

交互选项按以下选择（与 ClawBot 教程一致）：

| 提示 | 选择 |
|---|---|
| 平台选择列表 | **Weixin/WeChat（微信）** |
| `Start QR Login now? [Y/n]` | **Y**，复制终端二维码地址到浏览器打开，手机微信扫一扫 |
| 私聊消息权限 | **Use DM pairing approval**（配对批准，多用户安全） |
| 群聊权限 | **Disable group chats**（内测期禁群聊，降低风控面） |
| `Use your Weixin user ID as the home channel? [Y/n]` | **Y** |
| `Install the gateway as a systemd service? [Y/n]` | **Y** |
| 运行方式 | **User service**（无需 sudo，本地电脑适用） |

### 3.3 完成配对

1. 微信里打开机器人窗口，发送任意消息 → 自动生成**一键配对命令**
2. 回 Ubuntu 终端，粘贴执行 → 提示**对接成功**

### 3.4 验收

- [ ] `~/.hermes/.env` 已写入账号 ID / Token
- [ ] `hermes gateway status` 显示 Weixin 已连接
- [ ] 微信发「你好」能收到 AI 回复（收发链路全通）

## 四、功能验证（逐项打勾并记录）

| # | 项目 | 操作 | 预期 | 结果 |
|---|---|---|---|---|
| 4.1 | 文本收发 | 发一段约 200 字文案（小红书笔记长度） | 原文完整送达，回复正常 | ☐ |
| 4.2 | 图片收发 | 发 1 张 1728×2304 竖图（POC 图文规格） | 可发送/接收，记录是否被压缩 | ☐ |
| 4.3 | 视频收发 | 发 POC 成片 video_001.mp4（20MB · 1080×1440 · 50s） | 能直发且接收方可播放 | ☐ |
| 4.4 | 大视频边界 | 依次发 50MB / 100MB 左右视频 | 记录失败阈值 → 决定超限走网盘链接的阈值 | ☐ |
| 4.5 | 文件收发 | 发一个 zip 包（模拟图文笔记交付包） | 能收能发 | ☐ |
| 4.6 | 连发压力 | 1 分钟内连发 10 条消息 | 记录是否限流 / 丢消息 / 触发风控提示 | ☐ |

> 4.3 的视频可从 preview.html 播放页下载，或本地任取 ≈20MB 竖版视频替代。

## 五、多用户验证（对应设计文档 3.6 节）

- [ ] 用家人/朋友的第二个微信号私聊机器人 → 触发 DM pairing → 终端执行配对命令 → 第二个用户可正常对话
- [ ] 两个用户会话互不串扰（各自上下文独立）
- [ ] 未配对的陌生人发消息被拒（白名单机制生效）

## 六、扩展点探查（决定 bot_weixin.py 的实现形态）

目的：确认本项目流水线如何挂接到 hermes。逐项记录证据（命令输出 / 配置样例）：

- [ ] `hermes skills` 的输出：技能目录结构、如何添加自定义 skill
- [ ] 微信消息进来后，能否路由到自定义脚本 / HTTP 服务（查 `~/.hermes/config.yaml` 可配项、MCP、cron）
- [ ] 记录 `~/.hermes/` 目录结构
- [ ] **结论**：`bot_weixin.py` 走哪种形态——
  - A：包装成 hermes skill（流水线跑在 hermes 内）
  - B：hermes 仅做消息通道，转发给自建 Router（流水线独立进程）
  - C：其他（记录发现）

## 七、一周稳定性压测（对应验收标准 5）

每日固定时间检查：

| 天 | 检查项 | 结果 |
|---|---|---|
| Day 1 | 重启电脑 → `hermes gateway status` 确认 user service 自动拉起 | ☐ |
| Day 1-7 | 每日发 1 条测试消息，记录送达延迟 | ☐ |
| 全程 | 日志有无断线重连记录；掉线后是否需要重新扫码 | ☐ |
| Day 7 | 汇总：故障次数 / 恢复方式 / 能否无人值守 | ☐ |

日志查看：

```bash
journalctl --user -u hermes-gateway -f   # 服务名以安装时实际输出为准
```

## 八、结果回填模板

验证完成后填此表发回，我按结论定 `bot_weixin.py` 实现细节，并回填设计文档 3.7 节。

| 项 | 结果 |
|---|---|
| 四类消息收发 | 通过 / 失败（失败项：__） |
| 视频直发上限 | __ MB |
| 连发限流 | 无 / 约每分钟 __ 条 |
| 多用户 pairing | 通过 / 失败 |
| 扩展点结论 | A（hermes skill）/ B（通道转发）/ 其他：__ |
| 7 天故障 | __ 次，恢复方式 __ |
| 总体结论 | 可开发 / 需观望 / 放弃微信渠道（飞书单渠道） |

## 九、风险与备注

- ClawBot 2026.3 才开放，接口可能变动：`bot_weixin.py` 保持薄适配层（设计 3.3 接口已隔离），通道层大改不影响内核
- 个人微信挂机器人存在风控可能：内测期建议用小号、低频使用、禁群聊（配置已选 Disable）
- 若想改权限或重新登录，重跑 `hermes gateway setup` 即可
- 若二~三步即卡死：把报错贴回，先判断环境问题；确属通道不可用则降级为「飞书先行」，微信渠道挂起观望
