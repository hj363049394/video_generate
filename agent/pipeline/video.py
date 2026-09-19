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


def _make_clip(idx: int, img: str, mp3: str, dur: float, narration: str,
               font: str, out: str) -> None:
    frames = int(round(dur * FPS))
    z = "min(zoom+0.0007,1.18)" if idx % 2 == 0 else "max(1.18-0.0008*on,1.001)"  # 奇偶交替推/拉
    draws = []
    for i, ln in enumerate(_split_lines(narration)):
        safe = ln.replace(":", "").replace("'", "")
        draws.append(
            f"drawtext=fontfile={font}:text='{safe}':fontcolor=white:"
            f"borderw=5:bordercolor=black@0.75:fontsize=56:x=(w-text_w)/2:y=h-{300 - i * 78}")
    vf = (f"scale=2160:-2,crop=2160:2880,"
          f"zoompan=z='{z}':x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':"
          f"d={frames}:s={OUT_W}x{OUT_H}:fps={FPS},format=yuv420p," + ",".join(draws))
    subprocess.run(
        ["ffmpeg", "-y", "-loop", "1", "-i", img, "-i", mp3,
         "-filter_complex", f"[0:v]{vf}[v];[1:a]apad=whole_dur={dur:.3f}[a]",
         "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-c:a", "aac", "-b:a", "128k", "-ar", "24000", out],
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
         "-c:a", "aac", "-b:a", "160k", joined],
        check=True, capture_output=True)

    # 3. 首尾淡入淡出 → 成片
    fade_out = max(total - 0.6, 0)
    subprocess.run(
        ["ffmpeg", "-y", "-i", joined,
         "-vf", f"fade=t=in:st=0:d=0.5,fade=t=out:st={fade_out:.2f}:d=0.6",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-c:a", "copy", out_path],
        check=True, capture_output=True)
    return out_path


def shutil_which_none(cmd: str) -> bool:
    """ffmpeg/ffprobe 是否缺失"""
    return any(subprocess.run(["which", c], capture_output=True).returncode != 0
               for c in (cmd, "ffprobe"))
