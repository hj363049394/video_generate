"""小红书笔记抓取 · 红狐适配器（拉模式核心）

职责：
  1. parse_share_link(text) - 从用户消息解析出笔记链接 / note_id
  2. fetch_note_detail(work_id|work_link, api_key) - 调红狐 get_work 拿详情
  3. fetch_video_transcript(url, api_key) - 视频笔记提文案（异步任务，轮询结果）
  4. normalize(raw) - 把红狐返回结构统一为内部 topic dict（与 fetch_redfox 同 schema）

输出 topic dict（与 router._run_task 期望一致）：
  {
    "note_id": str, "url": str, "type": "image"|"video",
    "title": str, "description": str, "cover_image": str,
    "likes": int, "collects": int, "comments": int, "shares": int,
    "published_at": str, "author": {"nickname": str, "fans": int},
    "images": [str],          # 图集 URL 列表（图文笔记才有）
    "video_url": str,         # 视频直链（视频笔记才有）
    "video_transcript": str,  # 视频文案（视频笔记才有，从红狐 transcript 提取）
    "data_source": "redfox_detail"
  }

用法：
  from pipeline.note_fetch import parse_share_link, fetch_topic_from_user_text
  link_or_id = parse_share_link(text)
  topic = fetch_topic_from_user_text(text, api_key=os.environ["REDFOX_API_KEY"])
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger("pipeline.note_fetch")

# 红狐 transcript 异步任务轮询参数
_TRANSCRIPT_POLL_INTERVAL = 5     # 秒
_TRANSCRIPT_POLL_TIMEOUT = 180    # 3 分钟超时


# ─── 链接解析 ─────────────────────────────────────────────────

# 匹配 xhslink.com/xxx（短链）或 xiaohongshu.com/explore/{note_id}（长链）
# xiaohongshu.com/discovery/item/{note_id} / xiaohongshu.com/note/{note_id} 等变体也覆盖
_LINK_RE = re.compile(
    r"(https?://(?:www\.)?(?:xhslink\.com|xiaohongshu\.com)/[A-Za-z0-9/?=_\-]+)"
)
_NOTE_ID_RE = re.compile(
    r"xiaohongshu\.com/(?:explore|discovery/item|note)/([A-Za-z0-9]+)"
)


def parse_share_link(text: str) -> dict:
    """从用户粘贴消息解析出笔记链接 / note_id。

    返回：
      {"link": str|None, "note_id": str|None}
      两者都为 None 表示未匹配到合法链接
    """
    if not text:
        return {"link": None, "note_id": None}

    link_match = _LINK_RE.search(text)
    link = link_match.group(1) if link_match else None

    note_id = None
    if link:
        nid = _NOTE_ID_RE.search(link)
        if nid:
            note_id = nid.group(1)
    # 用户可能只粘了 note_id 没带链接（24 位字符）
    if not note_id:
        nid2 = re.search(r"\b([A-Za-z0-9]{24})\b", text)
        if nid2:
            note_id = nid2.group(1)

    return {"link": link, "note_id": note_id}


# ─── 红狐 client 延迟构造 ─────────────────────────────────────

_client_cache: dict = {}


def _get_client(api_key: str):
    """延迟创建 RedFoxClient，避免无 key 时整个路由崩溃（imagegen 同款模式）"""
    if not api_key:
        raise RuntimeError("未配置 REDFOX_API_KEY（红狐详情抓取必需）")
    if api_key in _client_cache:
        return _client_cache[api_key]
    from redfox import RedFoxClient
    client = RedFoxClient(api_key=api_key)
    _client_cache[api_key] = client
    return client


# ─── 详情抓取 ─────────────────────────────────────────────────

def fetch_note_detail(work_id: str = "", work_link: str = "", api_key: str = "") -> dict:
    """调红狐 get_work 拿笔记详情（原始返回）

    work_id 和 work_link 至少传一个，红狐 SDK 会自动选择。
    """
    client = _get_client(api_key)
    return client.xiaohongshu.get_work(work_id=work_id or None, work_link=work_link or None)


def fetch_video_transcript(video_url: str, api_key: str = "") -> str:
    """红狐视频提文案（异步任务，轮询结果）。

    接口：transcript_submit → transcript_result
    返回视频文案文本（视频笔记的口播稿）。
    超时或失败返回空字符串。
    """
    client = _get_client(api_key)
    try:
        submit = client.xiaohongshu.transcript_submit(url=video_url)
        task_id = submit.get("taskId") or submit.get("data", {}).get("taskId")
        if not task_id:
            logger.warning("视频提文案未返回 taskId: %s", submit)
            return ""
        deadline = time.time() + _TRANSCRIPT_POLL_TIMEOUT
        while time.time() < deadline:
            r = client.xiaohongshu.transcript_result(task_id=task_id)
            # 红狐异步任务通常返回 status / state 字段表示是否完成
            status = r.get("status") or r.get("state") or r.get("data", {}).get("status", "")
            text = r.get("text") or r.get("content") or r.get("data", {}).get("text", "")
            if text:
                return text
            if status in ("failed", "error", "FAIL"):
                logger.warning("视频提文案任务失败: %s", r)
                return ""
            time.sleep(_TRANSCRIPT_POLL_INTERVAL)
        logger.warning("视频提文案超时（%ds） url=%s", _TRANSCRIPT_POLL_TIMEOUT, video_url)
        return ""
    except Exception:
        logger.exception("视频提文案异常 url=%s", video_url)
        return ""


# ─── 归一化（红狐详情 → 内部 topic dict）─────────────────────

def normalize(raw: dict) -> dict:
    """红狐 get_work 返回 → 内部 topic dict（与 fetch_redfox.normalize_search 同 schema）

    字段映射（基于红狐文档；实际返回字段名以 SDK 实测为准，这里做防御性取值）：
      workId/workType/workTitle/workDesc/coverUrl/
      workLikedCount/workCollectedCount/workCommentsCount/workSharedCount/
      workPublishTime/accountNickname/authorFans
      images / videoUrl
    """
    d = raw.get("data", raw) if isinstance(raw, dict) else {}
    note_type_raw = d.get("workType") or d.get("type") or ""
    is_video = note_type_raw in ("video", " VIDEO", 1, "1", True)
    return {
        "note_id": d.get("workId") or d.get("id") or "",
        "url": d.get("workUrl") or d.get("url") or d.get("shareInfoLink") or "",
        "type": "video" if is_video else "image",
        "title": d.get("workTitle") or d.get("title") or "",
        "description": d.get("workDesc") or d.get("desc") or d.get("description") or "",
        "cover_image": d.get("coverUrl") or d.get("cover") or "",
        "likes": int(d.get("workLikedCount") or d.get("likedCount") or 0),
        "collects": int(d.get("workCollectedCount") or d.get("collectedCount") or 0),
        "comments": int(d.get("workCommentsCount") or d.get("commentsCount") or 0),
        "shares": int(d.get("workSharedCount") or d.get("sharedCount") or 0),
        "published_at": d.get("workPublishTime") or d.get("createTime") or "",
        "author": {
            "nickname": d.get("accountNickname") or d.get("authorNickname") or "",
            "fans": int(d.get("authorFans") or 0),
        },
        "images": d.get("images") or d.get("imageList") or [],
        "video_url": d.get("videoUrl") or d.get("video") or "",
        "video_transcript": "",  # 由 fetch_topic_from_user_text 在视频笔记场景下补
        "data_source": "redfox_detail",
    }


# ─── 主入口（router 调用）─────────────────────────────────────

def fetch_topic_from_user_text(text: str, api_key: str = "") -> dict:
    """从用户粘贴消息拉取完整笔记详情。

    步骤：
      1. parse_share_link 解析 link / note_id
      2. 调红狐 get_work（work_id 或 work_link）
      3. normalize 归一化
      4. 如果是视频笔记：调 transcript_submit/result 提取视频口播文案
    """
    parsed = parse_share_link(text)
    link, note_id = parsed["link"], parsed["note_id"]
    if not link and not note_id:
        raise ValueError(f"未识别到合法小红书链接：{text[:60]}")

    api_key = api_key or os.environ.get("REDFOX_API_KEY", "")
    raw = fetch_note_detail(work_id=note_id, work_link=link, api_key=api_key)
    topic = normalize(raw)

    # 视频笔记：补口播文案（用作仿写输入的核心 description 补充）
    if topic["type"] == "video" and topic.get("video_url"):
        logger.info("检测到视频笔记，提取口播文案 url=%s", topic["video_url"][:60])
        transcript = fetch_video_transcript(topic["video_url"], api_key=api_key)
        topic["video_transcript"] = transcript
        # 视频笔记的 description 通常只是简介，把口播文案拼到 description 末尾
        # 让仿写 LLM 拿到完整内容（rewrite.py 用 description 字段作为对标正文）
        if transcript and transcript not in (topic["description"] or ""):
            topic["description"] = (topic["description"] or "") + "\n\n[视频口播文案]\n" + transcript
    return topic
