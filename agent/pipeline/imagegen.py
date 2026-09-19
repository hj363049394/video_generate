"""生图双通道：火山 Agent Plan（主）+ 红狐 GPT-Image-2（备·支持参考图）+ 红狐豆包 Lite（第三备）

统一接口 ImageGenProvider.generate(prompt, out_path, orientation, ref_images)。
业务侧只说 orientation（portrait 竖版 3:4 / landscape 横幅 16:9），
各 Provider 内部翻译为自己的参数格式（改造清单 3.1-3.5）。

通道能力对照：
  ark          doubao-seedream-5.0-lite 直连，同步返回 url；纯文生图（参考图为 TODO）
  redfox_gpt   GPT-Image-2，异步 submit+轮询；支持 ≤2 张参考图 URL（图生图/要素调整）
  redfox_doubao Seedream Lite 红狐转发，异步 submit+轮询；支持图生图；Agent Plan 额度备源
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from abc import ABC, abstractmethod
from typing import List, Optional

ORIENTATIONS = ("portrait", "landscape")


class ImageGenError(RuntimeError):
    """生图失败（含轮询超时/服务端 failed）"""


def _download(url: str, out_path: str, timeout: int = 120) -> str:
    parent = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(parent, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(out_path, "wb") as f:
        f.write(resp.read())
    return out_path


class ImageGenProvider(ABC):
    name: str = "base"

    @abstractmethod
    def generate(
        self,
        prompt: str,
        out_path: str,
        orientation: str = "portrait",
        ref_images: Optional[List[str]] = None,
    ) -> str:
        """生成图片并落盘到 out_path，返回 out_path。失败抛 ImageGenError。"""


# ─── 通道 A · 火山 Agent Plan（主，POC 验证过的直连路径） ───────────────

class ArkImageGen(ImageGenProvider):
    """doubao-seedream-5.0-lite · https://ark.cn-beijing.volces.com/api/plan/v3/images/generations

    尺寸（POC 实测满足 Agent Plan ≥3686400 像素要求）：
      portrait  1728x2304（3:4 竖版）
      landscape 2304x1728（16:9 近似横幅，实际 4:3——Agent Plan 端点支持的横向档）
    """

    name = "ark"
    API = "https://ark.cn-beijing.volces.com/api/plan/v3/images/generations"
    MODEL = "doubao-seedream-5.0-lite"
    SIZES = {"portrait": "1728x2304", "landscape": "2304x1728"}

    def __init__(self, api_key: str = "", timeout: int = 300):
        self.api_key = api_key or os.environ.get("ARK_API_KEY", "")
        self.timeout = timeout

    def generate(self, prompt, out_path, orientation="portrait", ref_images=None):
        if ref_images:
            raise ImageGenError("ark 通道暂不支持参考图（请用 redfox_gpt 通道做图生图）")
        if orientation not in self.SIZES:
            raise ImageGenError(f"未知 orientation: {orientation}")
        if not self.api_key:
            raise ImageGenError("未配置 ARK_API_KEY")
        body = json.dumps({
            "model": self.MODEL,
            "prompt": prompt,
            "size": self.SIZES[orientation],
            "response_format": "url",
            "watermark": False,
        }).encode()
        req = urllib.request.Request(
            self.API, data=body, method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
            url = data["data"][0]["url"]
        except Exception as exc:
            raise ImageGenError(f"ark 生成失败: {exc}") from exc
        return _download(url, out_path)


# ─── 通道 B · 红狐 GPT-Image-2（支持参考图，对标图要素调整用） ──────────

class RedfoxGptImageGen(ImageGenProvider):
    """gpt_image.submit / result · resolution 1k/2k/4k + 宽高比；最多 2 张参考图 URL"""

    name = "redfox_gpt"
    TERMINAL = ("completed", "failed")

    def __init__(self, api_key: str = "", resolution: str = "2k",
                 poll_interval: float = 3.0, poll_timeout: float = 300.0):
        # key 延迟到 generate 时才建 client：无 key 环境下路由构建/导入不崩
        self._api_key = api_key or os.environ.get("REDFOX_API_KEY", "")
        self.resolution = resolution
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        # 竖版 3:4 / 横幅 16:9（红狐走比例制，非像素制）
        self.sizes = {"portrait": "3:4", "landscape": "16:9"}

    def _client(self):
        from redfox import RedFoxClient  # 延迟导入，保持无 SDK 环境可加载本模块
        if not self._api_key:
            raise ImageGenError("未配置 REDFOX_API_KEY（redfox.hk 控制台获取）")
        return RedFoxClient(api_key=self._api_key)

    def generate(self, prompt, out_path, orientation="portrait", ref_images=None):
        if ref_images and len(ref_images) > 2:
            raise ImageGenError("redfox_gpt 参考图最多 2 张")
        size = self.sizes.get(orientation)
        if not size:
            raise ImageGenError(f"未知 orientation: {orientation}")
        client = self._client()
        submit = client.gpt_image.submit(
            prompt=prompt, resolution=self.resolution, size=size,
            n=1, reference_images=ref_images or None)
        task_id = submit.get("taskId") or submit.get("data", {}).get("taskId")
        if not task_id:
            raise ImageGenError(f"redfox_gpt submit 未返回 taskId: {submit}")
        deadline = time.monotonic() + self.poll_timeout
        while time.monotonic() < deadline:
            r = client.gpt_image.result(task_id)
            status = str(r.get("status") or "")
            if status == "completed":
                urls = r.get("imageUrls") or []
                if not urls:
                    raise ImageGenError(f"redfox_gpt completed 但无 imageUrls: {r}")
                return _download(urls[0], out_path)
            if status == "failed":
                raise ImageGenError(f"redfox_gpt 服务端失败: {r.get('failReason') or r}")
            time.sleep(self.poll_interval)
        raise ImageGenError(f"redfox_gpt 轮询超时（{self.poll_timeout:.0f}s）taskId={task_id}")


# ─── 通道 C · 红狐豆包 Seedream Lite 转发（Agent Plan 额度备源） ────────

class RedfoxDoubaoLiteImageGen(ImageGenProvider):
    """doubao_image.lite_submit / result · 像素尺寸直传，支持图生图"""

    name = "redfox_doubao"

    def __init__(self, api_key: str = "", poll_interval: float = 3.0, poll_timeout: float = 300.0):
        self._api_key = api_key or os.environ.get("REDFOX_API_KEY", "")
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.sizes = {"portrait": "1728x2304", "landscape": "2304x1728"}

    def _client(self):
        from redfox import RedFoxClient
        if not self._api_key:
            raise ImageGenError("未配置 REDFOX_API_KEY（redfox.hk 控制台获取）")
        return RedFoxClient(api_key=self._api_key)

    def generate(self, prompt, out_path, orientation="portrait", ref_images=None):
        size = self.sizes.get(orientation)
        if not size:
            raise ImageGenError(f"未知 orientation: {orientation}")
        kwargs = {}
        if ref_images:
            if len(ref_images) > 1:
                raise ImageGenError("redfox_doubao 图生图仅支持 1 张参考图")
            kwargs["image"] = ref_images[0]
        client = self._client()
        submit = client.doubao_image.lite_submit(
            prompt=prompt, size=size, output_format="jpeg",
            response_format="url", watermark=False, **kwargs)
        task_id = submit.get("taskId") or submit.get("data", {}).get("taskId")
        if not task_id:
            raise ImageGenError(f"redfox_doubao submit 未返回 taskId: {submit}")
        deadline = time.monotonic() + self.poll_timeout
        while time.monotonic() < deadline:
            r = client.doubao_image.lite_result(task_id)
            status = str(r.get("status") or "")
            if status == "completed":
                # 豆包转发结果字段兼容 imageUrls / data[0].url 两种形态
                urls = r.get("imageUrls") or (r.get("data") or [{}])[0].get("url", "")
                if not urls:
                    raise ImageGenError(f"redfox_doubao completed 但无图片 url: {r}")
                return _download(urls[0] if isinstance(urls, list) else urls, out_path)
            if status == "failed":
                raise ImageGenError(f"redfox_doubao 服务端失败: {r.get('failReason') or r}")
            time.sleep(self.poll_interval)
        raise ImageGenError(f"redfox_doubao 轮询超时（{self.poll_timeout:.0f}s）taskId={task_id}")


# ─── 路由：主备 fallback + 运行时切换（/生图通道 指令） ─────────────────

class ImageGenRouter:
    """按优先级尝试各通道；失败自动 fallback 到下一通道（改造清单 3.5）。"""

    def __init__(self, providers: List[ImageGenProvider], primary: Optional[str] = None):
        self.providers = {p.name: p for p in providers}
        if not self.providers:
            raise ValueError("至少需要一个 ImageGenProvider")
        self._order = list(self.providers)
        if primary:
            self.set_primary(primary)

    def set_primary(self, name: str) -> None:
        if name not in self.providers:
            raise ValueError(f"未知生图通道: {name}（可用: {', '.join(self.providers)}）")
        self._order.remove(name)
        self._order.insert(0, name)

    @property
    def primary(self) -> str:
        return self._order[0]

    def generate(self, prompt, out_path, orientation="portrait", ref_images=None,
                 provider: Optional[str] = None) -> tuple:
        """生成图片。返回 (out_path, 实际使用的通道名)。指定 provider 时不 fallback。"""
        order = [provider] if provider else self._order
        errors = []
        for name in order:
            p = self.providers.get(name)
            if not p:
                errors.append(f"{name}: 通道不存在")
                continue
            try:
                path = p.generate(prompt, out_path, orientation, ref_images)
                return path, name
            except Exception as exc:  # noqa: BLE001 —— fallback 需要吞掉各通道异常
                errors.append(f"{name}: {exc}")
        raise ImageGenError("所有生图通道失败 -> " + " | ".join(errors))


def build_router(config: dict) -> ImageGenRouter:
    """从 config.yaml 的 imagegen 段构建（改造清单 5.1）。"""
    providers: List[ImageGenProvider] = []
    for name, spec in (config.get("providers") or {}).items():
        if not spec.get("enabled", True):
            continue
        if name == "ark":
            providers.append(ArkImageGen(api_key=spec.get("api_key", "")))
        elif name == "redfox_gpt":
            providers.append(RedfoxGptImageGen(api_key=spec.get("api_key", ""),
                                               resolution=spec.get("resolution", "2k")))
        elif name == "redfox_doubao":
            providers.append(RedfoxDoubaoLiteImageGen(api_key=spec.get("api_key", "")))
    if not providers:
        # 无 config 时兜底：环境变量构建默认双通道
        providers = [ArkImageGen(), RedfoxGptImageGen()]
    return ImageGenRouter(providers, primary=config.get("primary"))
