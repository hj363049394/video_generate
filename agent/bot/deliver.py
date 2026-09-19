"""交付降级：按渠道能力交付图文 / 视频（设计文档 v1.1 §3.7 微信列）

微信渠道策略：
  图文 = 4 图逐张直发 + 文案整段文本
  视频 = ≤ video_max_mb 直发；超限提示人工取件（网盘链接属 Phase 2）
"""
from __future__ import annotations

import os
from typing import List, Optional

from bot.base import TriggerAdapter

MB = 1024 * 1024


async def deliver_note(adapter: TriggerAdapter, uid: str, title: str, content: str,
                       image_paths: List[str], video_path: Optional[str] = None,
                       video_max_mb: float = 25.0) -> None:
    """交付一篇完整笔记：文案 → 逐图 → 视频（可选）。"""
    caps = adapter.capabilities()
    await adapter.send_text(uid, f"【{title}】\n\n{content}")
    if "image" in caps:
        for p in image_paths:
            await adapter.send_image(uid, p)
    elif "file" in caps:  # 渠道不支持图片消息时降级为文件
        for p in image_paths:
            await adapter.send_file(uid, p)
    if video_path:
        await deliver_video(adapter, uid, video_path, video_max_mb)


async def deliver_video(adapter: TriggerAdapter, uid: str, path: str,
                        video_max_mb: float = 25.0) -> None:
    size_mb = os.path.getsize(path) / MB
    if size_mb > video_max_mb:
        await adapter.send_text(
            uid, f"视频 {size_mb:.0f}MB 超过直发上限 {video_max_mb:.0f}MB，"
                 f"请从产出目录人工取件：{path}（网盘自动转链属 Phase 2）")
        return
    if "video" in adapter.capabilities():
        await adapter.send_video(uid, path)
    elif "file" in adapter.capabilities():
        await adapter.send_file(uid, path)
    else:
        await adapter.send_text(uid, f"当前渠道不支持视频，文件位于：{path}")
