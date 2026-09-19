---
name: xhs-note-fetch
description: >
  小红书笔记详情抓取 Skill（拉模式核心）。当用户粘贴小红书分享链接时调用，
  调红狐 get_work 拉取完整笔记详情（标题/正文/图集/数据/作者），归一化为
  内部 topic dict 供 xhs-rewrite 消费。支持图文笔记和视频笔记：
  - 图文笔记：直接抓详情进流水线
  - 视频笔记：抓详情 + 调 transcript_submit/result 提取口播文案 → 进流水线
  完成拉模式闭环（推模式由雷达 cron 负责，本 Skill 负责用户主动输入）。
version: 1.0.0
author: xhs-travel-planner
tags:
  - xiaohongshu
  - fetch
  - redfox
  - pull-mode
  - video-transcript
---

# xhs-note-fetch Skill

## 触发条件

| 触发源 | 触发动作 |
|---|---|
| Router `_dispatch` | 用户消息匹配 `xhslink.com` 或 `xiaohongshu.com` 域名正则 |
| Router `_dispatch` | 纯 note_id（24 位字母数字，Phase 1.5+ 兜底） |

## 职责

1. **链接解析**：从用户消息提取小红书链接 / note_id
2. **详情抓取**：调红狐 `get_work(work_id 或 work_link)` 拿笔记完整字段
3. **归一化**：红狐返回结构 → 内部 topic dict（与 fetch_redfox 同 schema）
4. **视频文案提取**：视频笔记调红狐 `transcript_submit/result` 异步任务提取口播文案
5. **拼装 description**：视频笔记把口播文案拼到 description 末尾，让 xhs-rewrite LLM 拿到完整内容

## 执行步骤

### Step 1: 链接解析（parse_share_link）

```python
from pipeline.note_fetch import parse_share_link
parsed = parse_share_link(text)
link, note_id = parsed["link"], parsed["note_id"]
```

支持格式：
- 短链：`https://xhslink.com/abc123XYZ`（仅能传 link 给红狐）
- 长链：`https://www.xiaohongshu.com/explore/{note_id}` / `discovery/item/{id}` / `note/{id}`
- 纯 note_id：24 位字母数字（兜底，红狐 get_work(work_id=...)）

### Step 2: 详情抓取（fetch_note_detail）

```python
raw = fetch_note_detail(work_id=note_id, work_link=link, api_key=api_key)
```

- 红狐接口：`POST /story/api/xhsUser/queryWorkDetail`
- 入参：`workId` 或 `workLink`（至少一个）
- 鉴权：HTTP Header `Authorization: Bearer {api_key}`

### Step 3: 视频文案提取（fetch_video_transcript，仅视频笔记）

```python
if topic["type"] == "video" and topic["video_url"]:
    transcript = fetch_video_transcript(topic["video_url"], api_key=api_key)
    topic["video_transcript"] = transcript
    if transcript:
        topic["description"] += "\n\n[视频口播文案]\n" + transcript
```

- 红狐接口：`POST /story/api/parseWork/audioTextExtract/submit/xhs` → 返回 taskId
- 查询接口：`POST /story/api/parseWork/audioTextExtract/result/xhs`
- 轮询参数：间隔 5s / 超时 180s
- 失败降级：返回空字符串，不阻塞流水线（仿写只拿到 description 简介走）

### Step 4: 归一化（normalize）

```python
topic = normalize(raw)
```

字段映射（基于红狐 SDK 文档；防御性取值兼容多字段名变体）：

| 内部字段 | 红狐字段 |
|---|---|
| note_id | workId / id |
| url | workUrl / url / shareInfoLink |
| type | workType / type（"video" → "video"，其他 → "image"） |
| title | workTitle / title |
| description | workDesc / desc / description |
| cover_image | coverUrl / cover |
| likes | workLikedCount / likedCount |
| collects | workCollectedCount / collectedCount |
| comments | workCommentsCount / commentsCount |
| shares | workSharedCount / sharedCount |
| published_at | workPublishTime / createTime |
| author.nickname | accountNickname / authorNickname |
| author.fans | authorFans |
| images | images / imageList |
| video_url | videoUrl / video |
| video_transcript | （由本 Skill 在视频笔记场景补） |
| data_source | "redfox_detail" |

## 输出 Schema

```json
{
  "note_id": "abc123def456XYZ",
  "url": "https://www.xiaohongshu.com/explore/abc123def456XYZ",
  "type": "video",
  "title": "...",
  "description": "...简介\n\n[视频口播文案]\n...完整口播稿...",
  "cover_image": "https://sns-img.xxx.com/...",
  "likes": 8500,
  "collects": 12000,
  "comments": 320,
  "shares": 200,
  "published_at": "2026-09-15",
  "author": {"nickname": "...", "fans": 50000},
  "images": [],
  "video_url": "https://sns-video.xxx.com/....mp4",
  "video_transcript": "...完整口播稿...",
  "data_source": "redfox_detail"
}
```

## 依赖工具

- `redfox-python-sdk`：`pip install redfox-python-sdk`
- `REDFOX_API_KEY` 环境变量 / `config.radar.redfox_api_key`（与雷达搜索共用）
- `pipeline/note_fetch.py`：核心执行

## 异常处理

| 场景 | 处理方式 |
|---|---|
| 未识别到合法链接 | 抛 ValueError，提示用户检查链接 |
| 未配置 REDFOX_API_KEY | 抛 RuntimeError，提示用户填 config.radar.redfox_api_key 或环境变量 |
| 红狐接口 401/403 | 抛异常，提示用户检查 key 是否过期 |
| 视频提文案超时（180s） | 返回空字符串，不阻塞流水线（仿写走 description 简介兜底） |
| 视频提文案任务失败 | 返回空字符串，记 warning log |
| 字段名不匹配（红狐返回结构变化） | 防御性取值兜底（多字段名变体 try） |

## 与其他 Skill 的协作

```
用户粘贴 xhslink.com/xxx
        ↓
xhs-note-fetch（本 Skill）→ topic dict
        ↓
  ┌──────────────────────────────────┐
  │ topic 存入 tasks 表（status=queued） │
  └──────────────────────────────────┘
        ↓
router._run_task（异步触发）
        ↓
xhs-rewrite（topic → rewrite.json）
        ↓
xhs-imagepack（rewrite.json → 4 卡 + video_frames）
        ↓
xhs-video（video_frames + narrations → video.mp4）
        ↓
deliver
```

## 图文 vs 视频笔记处理差异

| 笔记类型 | description 内容 | images | video_url | 后续流程 |
|---|---|---|---|---|
| 图文笔记 | 原文正文 | 图集 URL 列表 | 空 | 直接仿写 → 图文卡 + 视频 |
| 视频笔记 | 简介 + 口播文案（拼接） | 空（或封面缩略） | 视频直链 | 拿口播文案作正文 → 仿写 → 图文卡 + 视频 |

## 示例

### 输入 1：图文笔记链接
```
看下这篇 https://www.xiaohongshu.com/explore/abc123def456
```

### 输出 1
```json
{
  "type": "image",
  "title": "国庆带娃去南京，3天赏秋路线直接抄",
  "description": "...原文正文...",
  "images": ["https://...", "https://...", "https://...", "https://..."],
  "video_url": "",
  "video_transcript": ""
}
```

### 输入 2：视频笔记链接
```
仿写这个视频 https://www.xiaohongshu.com/explore/xyz789abc
```

### 输出 2
```json
{
  "type": "video",
  "title": "...",
  "description": "...简介\n\n[视频口播文案]\n大家好今天分享...",
  "video_url": "https://sns-video.xxx.com/....mp4",
  "video_transcript": "大家好今天分享..."
}
```

实测耗时（红狐）：
- 详情抓取：1-3 秒
- 视频提文案：5-60 秒（视红狐异步任务排队情况）
