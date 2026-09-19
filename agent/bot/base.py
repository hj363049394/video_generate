"""触发层抽象：TriggerAdapter + Intent（设计文档 v1.1 · 3.3 节）

渠道适配器只实现本接口；Router 与 pipeline 不 import 任何渠道 SDK。
新增渠道（如后续 bot_feishu.py）只需实现本抽象，内核零改动。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass
class Intent:
    """统一入站意图：渠道差异在 Adapter 内抹平（设计 3.3）"""

    user_id: str                 # 唯一标识 "{channel}:{open_id}"
    channel: str                 # "weixin" | "feishu" | ...
    text: str                    # 文本内容
    media: List[str] = field(default_factory=list)  # 入站媒体本地缓存路径
    ts: float = 0.0              # 消息时间戳（unix 秒）


# Router 注入给 Adapter 的回调：on_intent(intent) -> None
OnIntent = Callable[[Intent], None]


class TriggerAdapter(ABC):
    """渠道适配器抽象基类。

    实现方义务：
      - start(on_intent) 内启动监听，每条白名单内消息构造 Intent 后回调
      - send_* 家族完成出站投递，失败抛异常（由 Router 决定降级/补发）
      - bot_id 属性标识本适配器所属 Bot 实例（多 Bot 隔离用，单 Bot 默认 "default"）
    """

    name: str = "base"
    bot_id: str = "default"  # 多 Bot 实例隔离标识（v1.1：支持多进程多 Bot）

    @abstractmethod
    async def start(self, on_intent: OnIntent) -> None:
        """启动监听（长连接 / 轮询 / 网关回调）。阻塞运行至 stop()。"""

    @abstractmethod
    async def send_text(self, uid: str, text: str) -> None:
        """发送文本（超长由实现方自行分片）。"""

    @abstractmethod
    async def send_image(self, uid: str, path: str, caption: str = "") -> None:
        """发送本地图片文件。"""

    @abstractmethod
    async def send_video(self, uid: str, path: str, caption: str = "") -> None:
        """发送本地视频文件。"""

    @abstractmethod
    async def send_file(self, uid: str, path: str, caption: str = "") -> None:
        """发送任意文件（zip 包等兜底通道）。"""

    def capabilities(self) -> set:
        """渠道能力声明，如 {"text", "image", "video", "file"}；Deliver 按此降级。"""
        return {"text", "image", "video", "file"}

    async def stop(self) -> None:
        """优雅停止（默认空实现）。"""
        return None

    # ---- 推送（bot 主动发起，区别于 send 的回复语义）----
    async def push_text(self, uid: str, text: str) -> None:
        """主动推送文本（雷达 cron 用）。默认走 send_text；
        会话制渠道（如 iLink）可覆盖此方法实现 tokenless 降级路径。"""
        await self.send_text(uid, text)
