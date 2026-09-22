"""xhs-video · 视频合成 SKILL（Phase 1.5）

移植 POC make_video.py 合成引擎，分镜数据驱动：
  make_video(images, narrations, out_path) —— 图文同源分镜 + TTS + Ken Burns
  + xfade 叠化 + BGM ducking 混音 + 首尾淡入淡出

依赖：ffmpeg/ffprobe、ARK_API_KEY（Agent Plan TTS）、字体与 BGM 资产目录。
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import List, Tuple

TTS_URL = "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional"
SPEAKER = "zh_female_qingxinnvsheng_uranus_bigtts"  # 清新女声（uranus 2.0 系，POC 定稿）
BGM_VOL = 0.16       # BGM 音量（旁白优先，ducking）
XFADE = 0.5          # 镜头叠化时长
FPS = 25
OUT_W, OUT_H = 1080, 1440  # 3:4 竖版
# 音频统一标准参数（2026-09-22 v1.3.5）：TTS 源为 24kHz 单声道 mp3，直编 AAC
# 会继承 24kHz/mono 并经 -c:a copy 透传进成品——实测 Win11 媒体播放器报
# 0x80004005"不受支持的编码设置"（MF 文档名义支持但实测兼容性差），且微信 CDN
# 转码对非标准参数同样敏感。统一重采样 44.1kHz 立体声（通用解码器最大交集）。
AUD_AR, AUD_CH = 44100, 2


def tts(text: str, out: str, api_key: str = "") -> None:
    """Agent Plan HTTP TTS：chunked JSON 行流，data 为 base64 mp3（POC 验证路径）"""
    key = api_key or os.environ.get("ARK_API_KEY", "")
    if not key:
        raise RuntimeError("未配置 ARK_API_KEY（TTS 用）")
    payload = {"req_params": {"text": text, "speaker": SPEAKER,
                              "audio_params": {"format": "mp3", "sample_rate": 24000}}}
    req = urllib.request.Request(
        TTS_URL, data=json.dumps(payload).encode(),
        headers={"X-Api-Key": key, "X-Api-Resource-Id": "seed-tts-2.0",
                 "Content-Type": "application/json"}, method="POST")
    audio = bytearray()
    with urllib.request.urlopen(req, timeout=300) as resp:
        for raw in resp:
            line = raw.strip()
            if not line:
                continue
            d = json.loads(line)
            code = d.get("code", 0)
            if code == 0 and d.get("data"):
                audio.extend(base64.b64decode(d["data"]))
            elif code == 20000000:
                break
            elif code > 0:
                raise RuntimeError(f"TTS 错误: {d}")
    if not audio:
        raise RuntimeError("TTS 未返回音频")
    Path(out).write_bytes(audio)


def _probe(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def _split_lines(text: str, width: int = 15) -> List[str]:
    """按标点优先断行为最多 3 行，每行约 width 字（POC 定稿字幕断行）"""
    if len(text) <= width:
        return [text]
    for cut in range(min(width + 2, len(text)), width // 2, -1):
        if text[cut - 1] in "，。？！、；：" and cut <= width + 2:
            rest = text[cut:].strip("，。？！、；：")
            return [text[:cut]] + (_split_lines(rest, width) if rest else [])
    return [text[:width]] + _split_lines(text[width:], width)


def ff_filter_path(p: str) -> str:
    """路径 → filter_complex 内可安全解析的形式（修复 Windows 路径，2026-09-21）。

    Windows 路径 D:\\My Project\\x.otf 直拼会被 ffmpeg 两层解析吃掉：
      \\ 是转义符（分隔符被吞）、D: 的冒号是选项分隔符（值截断成 D）、空格破坏解析。
    实测（ffmpeg 6.1，盘符+空格路径字面模拟）：须转为 D\\:/My Project/x.otf
    并单引号包裹（引号同时保护空格/逗号等），字形渲染验证通过。"""
    p = p.replace("\\", "/").replace("'", "")
    p = p.replace(":", "\\:")
    return f"'{p}'"


def _make_clip(idx: int, img: str, mp3: str, dur: float, narration: str,
               font: str, out: str) -> None:
    frames = int(round(dur * FPS))
    z = "min(zoom+0.0007,1.18)" if idx % 2 == 0 else "max(1.18-0.0008*on,1.001)"  # 奇偶交替推/拉
    sub_dir = Path(out).parent
    draws = []
    for i, ln in enumerate(_split_lines(narration)):
        # Windows 兼容（2026-09-22）：text= 直传中文在中文系统上可能被按 ANSI
        # codepage（GBK）解析成乱码；textfile= 强制按 UTF-8 读取，跨平台稳定
        tf = str(sub_dir / f"sub_{idx}_{i}.txt")
        Path(tf).write_text(ln, encoding="utf-8")
        draws.append(
            f"drawtext=fontfile={ff_filter_path(font)}:textfile={ff_filter_path(tf)}:"
            f"fontcolor=white:expansion=none:"
            f"borderw=5:bordercolor=black@0.75:fontsize=56:x=(w-text_w)/2:y=h-{300 - i * 78}")
    # v1.3.6 色彩范围修复：format=yuv420p 只转格式标签、不转范围元数据——
    # ffmpeg 7+（实测 9.0.1）JPEG 解码输出 yuv420p+pc（full range）直通 libx264，
    # 成片被 ffprobe 判为 yuvj420p（自检门拦截）。scale=out_range=limited 在
    # 新版本实际压缩数据 255→235 并标 tv；旧版本（6.1.1 format 已转 tv）为无操作
    vf = (f"scale=2160:-2,crop=2160:2880,"
          f"zoompan=z='{z}':x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':"
          f"d={frames}:s={OUT_W}x{OUT_H}:fps={FPS},format=yuv420p,scale=out_range=limited,"
          + ",".join(draws))
    subprocess.run(
        ["ffmpeg", "-y", "-loop", "1", "-i", img, "-i", mp3,
         "-filter_complex", f"[0:v]{vf}[v];[1:a]apad=whole_dur={dur:.3f}[a]",
         "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-c:a", "aac", "-b:a", "128k", "-ar", str(AUD_AR), "-ac", str(AUD_CH), out],
        check=True, capture_output=True)


def make_video(images: List[str], narrations: List[str], out_path: str,
               assets_dir: str, work_dir: str = "") -> str:
    """图文同源分镜合成成片。images 与 narrations 等长（一图一段旁白）。"""
    if len(images) != len(narrations) or not images:
        raise ValueError("images 与 narrations 必须等长且非空")
    if shutil_which_none("ffmpeg"):
        raise RuntimeError("缺少 ffmpeg/ffprobe（apt install ffmpeg）")
    font = str(Path(assets_dir) / "fonts" / "NotoSansSC-Bold.otf")
    bgm = str(Path(assets_dir) / "bgm_travel.mp3")
    work = Path(work_dir or (Path(out_path).parent / "_video_work"))
    adir, cdir = work / "audio", work / "clips"
    adir.mkdir(parents=True, exist_ok=True)
    cdir.mkdir(parents=True, exist_ok=True)

    # 1. TTS 逐镜头 + 片段渲染
    clips: List[Tuple[str, float]] = []
    for i, (img, text) in enumerate(zip(images, narrations)):
        mp3 = str(adir / f"vo_{i}.mp3")
        if not os.path.exists(mp3):
            tts(text, mp3)
        dur = round(_probe(mp3) + 0.9, 3)
        clip = str(cdir / f"clip_{i}.mp4")
        _make_clip(i, img, mp3, dur, text, font, clip)
        clips.append((clip, dur))

    # 2. xfade 叠化拼接（视频 + 音频同步过渡）
    n = len(clips)
    inputs: List[str] = []
    for c, _ in clips:
        inputs += ["-i", c]
    vf_chain, prev, acc = [], "[0:v]", 0.0
    for i in range(1, n):
        acc += clips[i - 1][1]
        out = f"[v{i}]" if i < n - 1 else "[vout]"
        vf_chain.append(f"{prev}[{i}:v]xfade=transition=fade:duration={XFADE}:offset={acc - i * XFADE:.3f}{out}")
        prev = out
    af_chain, prev = [], "[0:a]"
    for i in range(1, n):
        out = f"[a{i}]" if i < n - 1 else "[avox]"
        af_chain.append(f"{prev}[{i}:a]acrossfade=d={XFADE}{out}")
        prev = out

    total = sum(d for _, d in clips) - (n - 1) * XFADE
    joined = str(work / "joined.mp4")
    bg_fade_out = max(total - 2.5, 0)
    subprocess.run(
        ["ffmpeg", "-y", *inputs, "-stream_loop", "-1", "-i", bgm,
         "-filter_complex",
         ";".join(vf_chain + af_chain) +
         f";[{n}:a]volume={BGM_VOL},afade=t=in:d=1.5,afade=t=out:st={bg_fade_out:.2f}:d=2.2[bgm];"
         f"[avox][bgm]amix=inputs=2:duration=first:normalize=0[aout]",
         "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-ar", str(AUD_AR), "-ac", str(AUD_CH),
         "-movflags", "+faststart", joined],
        check=True, capture_output=True)

    # 3. 首尾淡入淡出 → 成片
    fade_out = max(total - 0.6, 0)
    subprocess.run(
        ["ffmpeg", "-y", "-i", joined,
         "-vf", f"fade=t=in:st=0:d=0.5,fade=t=out:st={fade_out:.2f}:d=0.6",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-pix_fmt", "yuv420p",
         "-c:a", "copy",
         "-movflags", "+faststart", out_path],
        check=True, capture_output=True)
    # 4. 兼容性自检（2026-09-22）：流参数必须落在通用解码器交集内，
    #    非标准参数（如 24kHz 单声道 AAC）在 Windows 媒体播放器/微信端
    #    会被直接拒绝，报错拦截优于静默交付坏文件。
    _verify_compat(out_path)
    # 5. 黑场自检（2026-09-22）：拦截"合成正常但内容全黑"的静默交付。
    #    微信流式播放对 mp4 索引/封装敏感（黑屏有声），自检失败即报错并提示诊断。
    black = _detect_black(out_path)
    if black > total * 0.8:
        raise RuntimeError(
            f"成片黑场自检未通过：黑场 {black:.1f}s / 总时长 {total:.1f}s。"
            f"多半是本机 ffmpeg 与 zoompan/drawtext 的兼容问题，"
            f"请把 ffmpeg -version 输出反馈给开发者")
    return out_path


def _verify_compat(path: str) -> None:
    """成片流参数自检：音频须 AAC 且 ≥32kHz/≤2 声道，视频须 H.264/yuv420p。
    （AAC 24kHz 单声道等非标准参数在 Win11 媒体播放器报 0x80004005，
    微信端转圈；此自检防本机 ffmpeg 行为漂移静默产出坏文件。）"""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,pix_fmt,sample_rate,channels",
         "-of", "json", path], capture_output=True, text=True, check=True)
    errs = []
    for s in json.loads(r.stdout).get("streams", []):
        if s.get("codec_type") == "video" and (
                s.get("codec_name") != "h264" or s.get("pix_fmt") != "yuv420p"):
            errs.append(f"视频流 {s.get('codec_name')}/{s.get('pix_fmt')} 非 H.264/yuv420p")
        elif s.get("codec_type") == "audio":
            if s.get("codec_name") != "aac":
                errs.append(f"音频流 {s.get('codec_name')} 非 AAC")
            elif int(s.get("sample_rate", 0)) < 32000 or int(s.get("channels", 0)) > 2:
                errs.append(f"音频流 {s.get('sample_rate')}Hz/{s.get('channels')}ch "
                            f"低于通用兼容下限（需 ≥32kHz 且 ≤2ch）")
    if errs:
        raise RuntimeError("成片编码兼容性自检未通过：" + "；".join(errs))


def _detect_black(path: str) -> float:
    """blackdetect 统计黑场总时长（秒）。阈值：像素亮度 ≤10% 且占比 ≥98% 判黑。"""
    r = subprocess.run(
        ["ffmpeg", "-i", path, "-vf", "blackdetect=d=0.5:pix_th=0.10",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True)
    total = 0.0
    for ln in r.stderr.splitlines():
        m = re.search(r"black_duration:([0-9.]+)", ln)
        if m:
            total += float(m.group(1))
    return total


def shutil_which_none(cmd: str) -> bool:
    """ffmpeg/ffprobe 是否缺失（跨平台：shutil.which 在 Windows/macOS/Linux 均可查 exe）"""
    return any(shutil.which(c) is None for c in (cmd, "ffprobe"))
