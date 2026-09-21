---
name: xhs-video
description: >
  小红书视频合成 Skill。把 xhs-imagepack 产出的图文帧 + 分镜旁白合成完整竖版视频。
  流程：TTS 旁白（Agent Plan）→ Ken Burns 动效（zoompan 推/拉交替）→ crossfade 叠化
  → BGM ducking 混音 → 首尾淡入淡出 → MP4 成片。
  当 xhs-imagepack 完成后由 router 顺序调用，失败不阻塞图文交付。
version: 1.0.0
author: xhs-travel-planner
tags:
  - xiaohongshu
  - video
  - tts
  - ffmpeg
  - ken-burns
---

# xhs-video Skill

## 触发条件

| 触发源 | 触发动作 |
|---|---|
| Router `_run_task` | xhs-imagepack 完成后顺序调用，传 video_frames + narrations |

## 职责

1. **TTS 旁白合成**：每段 narrations 调 Agent Plan TTS 出 mp3
2. **Ken Burns 动效**：奇偶镜头交替缓推/缓拉（zoompan）
3. **字幕渲染**：底部双行字幕（按 15 字断行）
4. **镜头拼接**：crossfade 0.5s 叠化（含音频 acrossfade 同步）
5. **BGM ducking**：BGM 16% 音量混音 + 首尾 1.5s/2.2s 淡入淡出
6. **首尾淡入淡出**：整体 0.5s 淡入淡出

## 执行步骤

### Step 1: TTS 合成

调 `pipeline/video.py` 的 `tts(text, out, api_key="")`：

```python
tts(narration, str(work_dir / f"audio_{i}.mp3"))
```

- 端点：`https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional`
- 鉴权：`X-Api-Key` + `X-Api-Resource-Id: seed-tts-2.0`
- 响应：chunked JSON 行，data 为 base64 mp3
- 音色：`zh_female_qingxinnvsheng_uranus_bigtts`（清新女声，旅行赛道验证款）

### Step 2: 单镜头合成

调 `_make_clip(idx, img, mp3, dur, narration, font, out)`：

- 用 `ffprobe` 获取音频时长
- frames = dur × FPS（25）
- Ken Burns：奇偶镜头交替 `zoom+0.0007`（推）/ `1.18-0.0008*on`（拉）
- 字幕：`_split_lines(text, width=15)` 按标点断行，底部双行
- 输出：单镜头 mp4

### Step 3: 镜头拼接

- xfade 0.5s 叠化（视频）
- acrossfade 0.5s（音频同步）
- concat 拼接全部镜头（3-6 个，随卡片 DSL）

### Step 4: BGM ducking 混音

- BGM 音量 16%（旁白优先）
- 首尾淡入 1.5s / 淡出 2.2s
- 整体首尾 0.5s 淡入淡出

### Step 5: 输出

```python
video_mod.make_video(
    pack["video_frames"], layout["narrations"],
    str(work_dir / "video.mp4"), self.assets_dir)
```

产出：`workspace/{bot_id}/{uid}/{task_id}/video.mp4`（1080×1440, 25fps）

## 视频规格

| 维度 | 规格 |
|---|---|
| 分辨率 | 1080×1440（3:4 竖版） |
| 帧率 | 25 fps |
| 镜头数 | 3-6（随卡片 DSL，与 narrations 等长） |
| 音色 | zh_female_qingxinnvsheng_uranus_bigtts（清新女声） |
| Ken Burns | 奇偶交替缓推(1.0→1.18) / 缓拉(1.18→1.0) |
| 转场 | xfade 0.5s + acrossfade 0.5s |
| BGM 音量 | 16%（ducking） |
| BGM 首尾 | 淡入 1.5s / 淡出 2.2s |
| 整体首尾 | 淡入淡出 0.5s |
| 字幕 | 底部双行（按 15 字断行） |

## 已验证音色清单（doubao-seed-tts-2.0 / seed-tts-2.0）

| 音色 | voice_type | 适用 |
|---|---|---|
| 清新女声 ✅当前默认 | zh_female_qingxinnvsheng_uranus_bigtts | 文艺旅行 vlog |
| 邻家女孩 | zh_female_linjianvhai_uranus_bigtts | 亲切分享 |
| 爽快思思 | zh_female_shuangkuaisisi_uranus_bigtts | 攻略节奏感 |
| 知性灿灿 | zh_female_cancan_uranus_bigtts | 知性规划师 |
| 阳光青年 | zh_male_yangguangqingnian_uranus_bigtts | 活力男声 |
| 渊博小叔 | zh_male_yuanboxiaoshu_uranus_bigtts | 带娃旅行博主 |

**兼容规则**：仅 `*_uranus_bigtts`（2.0 系）音色可用于 seed-tts-2.0 资源；`*_moon_bigtts`（1.0 系）会报 55000000。

## 依赖工具

- `pipeline/video.py`：核心执行（make_video + tts）
- `ffmpeg` / `ffprobe`：视频合成与时长探测
- `ARK_API_KEY` 环境变量：TTS 鉴权（与生图共用 Agent Plan key）
- `assets/bgm_travel.mp3`：BGM 资产（Carefree · Kevin MacLeod, CC-BY）
- `assets/fonts/NotoSansSC-Bold.otf`：字幕字体

## API 接入速查

| 能力 | 端点 | 关键点 |
|---|---|---|
| TTS | `https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional` | X-Api-Key + X-Api-Resource-Id: seed-tts-2.0；响应为 chunked JSON 行，data 为 base64 mp3 |
| 生图（视频帧底图来源） | `https://ark.cn-beijing.volces.com/api/plan/v3/images/generations` | Bearer 鉴权；模型名必须 `doubao-seedream-5.0-lite`；size ≥3686400 像素（3:4 用 1728x2304） |

## 异常处理

| 场景 | 处理方式 |
|---|---|
| 未配置 ARK_API_KEY | 抛 RuntimeError，提示配置环境变量 |
| TTS 返回错误码 55000000 | 音色不兼容，提示换 `*_uranus_bigtts` 系列 |
| ffmpeg/ffprobe 未安装 | 抛 RuntimeError，提示 `apt install ffmpeg` |
| ffprobe 检查 `ffmpegprobe`（拼错） | 已修：检查 `ffprobe`（POC 踩平的坑） |
| TTS 端点 404 | 用正确端点 `openspeech.bytedance.com/api/v3/plan/tts/unidirectional`（不是 ark 域名） |
| BGM 403 防盗链 | 用 Incompetech 直链，不用 Pixabay CDN |
| 视频超 video_max_mb | deliver 提示人工取件，不阻塞交付 |

## 与其他 Skill 的协作

```
xhs-imagepack → video_frames + narrations
     ↓
xhs-video（本 Skill）→ video.mp4
     ↓
deliver_video（按 video_max_mb 阈值降级）
```

## 关键铁律

1. **图文同源**：每段旁白只讲对应图片上承载的内容，不得串图（编排阶段已保证）
2. **音色兼容**：仅用 `*_uranus_bigtts`（2.0 系）
3. **BGM ducking**：旁白优先，BGM 16% 音量
4. **失败不阻塞**：视频合成失败不影响图文交付

## 示例

**输入**：
- 5 张视频帧 frame_0.jpg ~ frame_4.jpg
- 5 段旁白 narrations

**输出**：
- video.mp4（1080×1440, 25fps, ~74s, ~30MB）

实测耗时（端到端，沙箱）：TTS 5 段 + ffmpeg 合成 = **约 5 分钟**。
注意：30MB 超过默认 video_max_mb=25，微信交付时触发超限降级（人工取件或调码率/V4 实测后调阈值）。
