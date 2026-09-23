# PowerShell 运维命令手册（Windows 部署机）

> 适用机器：`D:\My Project\github_project\video_generate`（Windows + Python 3.12 直装 + ffmpeg 9.0.1）
> 每次更新代码后的标准动作 = **第 2 节三条命令**；其他按需查阅。

---

## 1. 路径与版本速查

| 项 | 值 |
|---|---|
| 项目根目录 | `D:\My Project\github_project\video_generate` |
| 启动入口 | `agent\main.py`（必须在该目录下运行） |
| 配置文件 | `agent\config\config.yaml`（多实例：`config.{profile}.yaml`） |
| API 密钥 | 项目根 `.env`（ARK_API_KEY / REDFOX_API_KEY / LLM_API_KEY 等） |
| 微信 token / 任务库 | `agent\state\`（重扫码才动它，平时勿删） |
| 产物目录 | `agent\workspace\{bot_id}\`（图文、视频成片在这里） |
| Python | `python`（3.12；无效时试 `py -3`） |

---

## 2. 日常更新部署（最高频，照抄三步）

```powershell
cd "D:\My Project\github_project\video_generate"
git pull
python agent\main.py
```

> 正在运行的 bot 先 Ctrl+C 停掉再 pull，避免运行中文件被覆盖。

---

## 3. 启动 Bot

**方式 A：前台窗口运行（日常推荐，日志直接可见，Ctrl+C 停止）**

```powershell
cd "D:\My Project\github_project\video_generate\agent"
python main.py
```

**方式 B：后台常驻（关窗口不退出，日志写文件）**

```powershell
cd "D:\My Project\github_project\video_generate\agent"
Start-Process -WindowStyle Hidden python `
  -ArgumentList "main.py" `
  -RedirectStandardOutput "bot.log" -RedirectStandardError "bot.err"
```

实时查看后台日志（Ctrl+C 退出查看、不影响 bot）：

```powershell
Get-Content "D:\My Project\github_project\video_generate\agent\bot.log" -Wait -Tail 50
```

**多 Bot 实例**（每个 profile 独立配置/状态，可同机挂多个微信号）：

```powershell
python main.py --profile bot2        # 加载 config\config.bot2.yaml
```

---

## 4. 停止 Bot

前台运行：窗口内按 `Ctrl+C`。

后台运行——**按命令行精确匹配**（不会误杀其他 python 进程）：

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'main\.py' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

确认是否还有残留进程：

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'main\.py' } |
  Select-Object ProcessId, CommandLine
```

---

## 5. 首次部署 / 换机（一次性）

```powershell
# 1) 取代码
cd "D:\My Project\github_project"
git clone https://github.com/hj363049394/video_generate
cd video_generate
git checkout trae/agent-F784ti

# 2) 装依赖
python -m pip install -r agent\requirements.txt

# 3) 验证 ffmpeg（合成视频必需）
ffmpeg -version

# 4) 配置文件（首次从模板复制，之后不覆盖）
Copy-Item agent\config\config.example.yaml agent\config\config.yaml

# 5) 密钥（PowerShell 写 .env 注意加 -NoNewline，值含特殊字符用单引号包裹）
Set-Content -Path .env -Value "ARK_API_KEY=你的火山key" -Encoding ASCII
Add-Content -Path .env -Value "REDFOX_API_KEY=你的红狐key" -Encoding ASCII

# 6) 微信扫码登录（token 落盘 agent\state\weixin\，之后免扫码）
cd agent
python main.py --qr-login

# 7) 正式启动
python main.py
```

> `.env` 用 `Set-Content -Encoding UTF8` 会写 BOM 头——代码已兼容（按 utf-8-sig 读），但若发现 ARK_API_KEY 解析失败优先怀疑 BOM/引号问题。

---

## 6. 立即跑雷达抓取（不出清单时手动补）

```powershell
cd "D:\My Project\github_project\video_generate\agent"
python main.py --radar-now
```

清单落盘 `agent\workspace\default\topic_list_YYYY-MM-DD.json`，bot 内 `/选题` 查看。

---

## 7. Git 常用（本仓库）

```powershell
cd "D:\My Project\github_project\video_generate"
git pull                                   # 拉最新代码
git status                                 # 看本地改动
git log --oneline -5                       # 最近 5 次提交
git checkout trae/agent-F784ti             # 切到开发分支
git config user.name "hj363049394"         # 换机后重建提交身份
git config user.email "hj363049394@users.noreply.github.com"
```

---

## 8. Bot 对话指令速查（微信里发，非 PowerShell）

```
/定位                       查看/设置赛道·人设·关键词（长期生效，一次配置）
/主题 词1, 词2              按临时主题立即抓爆款（1-3 分钟自动推送清单，仅本次生效）
/选题                       查看当日选题清单（Top 5）
/选题 抓取                  按 /定位 默认关键词抓取
/选题 列表                  历史清单日期；/选题 MM-DD 查指定日期
/确认 N 或 仿写第N条        按选题生成图文笔记
/换角度 N 描述              调整第 N 条选题的仿写角度
/视频 任务ID                图文交付后生成视频笔记（失败重发同 ID 直接重传）
/仿写 内容                  直发主题或爆款文字仿写（首行=标题，其余=正文）
粘贴小红书链接              抓取该笔记并仿写
/状态                       查生产进度
/生图通道 ark|gpt|doubao    切换生图通道
/生图 描述                  直接生一张竖图
/help                       帮助
```

典型链路：`/主题 避寒路线` → 收到清单 → `/确认 2` → 收图文 → `/视频 任务ID` → 收视频。

---

## 9. 常见问题

| 现象 | 处理 |
|---|---|
| `python : 无法将…识别` | 用 `py -3` 替代，或把 Python312 加入 PATH |
| 脚本无法运行（.ps1） | 管理员执行 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| ARK_API_KEY 解析失败 | 检查 `.env` BOM/引号；重写：`Set-Content .env "KEY=value" -Encoding ASCII` |
| CDN 上传失败 / 500 | v1.3.7 起自动重试 3 次；仍失败看日志 `CDN 上传` 关键字，稍后重发同指令即可（成片保留、直接重传） |
| 视频黑屏无封面 | v1.3.8 起自动抽帧上传封面；若仍黑屏，日志搜 `封面` 三类 warning 定位降级原因 |
| 端口/token 异常登录掉线 | 重新 `python main.py --qr-login` 扫码（agent\state\ 保留即可续用任务库） |
| 改了代码不生效 | 重启 bot（第 2 节），Python 不热加载 |
