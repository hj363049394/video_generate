#!/usr/bin/env python3
"""视频笔记合成：分镜图 + TTS 旁白（Agent Plan）+ Ken Burns 动效 + 字幕 → MP4

流程：5 镜头（封面/路线/红山/点位/住宿钩子）→ 每镜头 TTS 合成旁白 mp3
     → zoompan 缓推/拉 + 底部双行字幕 → concat 拼接 + 首尾淡入淡出
输出：video/video_001.mp4（1080x1440, 25fps）

用法：export ARK_API_KEY=专属key && python3 make_video.py
"""
import base64
import json
import os
import subprocess
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
VDIR = os.path.join(BASE, "video")
ADIR = os.path.join(VDIR, "audio")
CDIR = os.path.join(VDIR, "clips")
FONT = os.path.join(BASE, "assets", "fonts", "NotoSansSC-Bold.otf")
TTS_URL = "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional"
SPEAKER = "zh_female_vv_uranus_bigtts"  # 文档示例音色，可按音色库更换
FPS = 25
OUT_W, OUT_H = 1080, 1440  # 3:4 竖版

STORYBOARD = [
    ("images/cover_photo_ref.jpg", "国庆带娃去南京，怕人多又想看秋色？这份三天的赏秋路线，直接抄作业。"),
    ("images/itinerary.jpg", "第一天钟山赏秋线，石象路上午去，光线好还出片，下午梧桐大道加灵谷寺。"),
    ("assets/spot_zoo.jpg", "第二天红山动物园，开园就进，动物上午最活跃，娃能开心一上午。"),
    ("images/spots.jpg", "第三天南京博物院收尾，记得提前七天预约。石象路早上八点最出片，梧桐大道下午四点后最美。"),
    ("images/stay_hook.jpg", "住宿就选夫子庙老门东片区，动线最顺。你家情况不一样？评论区报人数天数，我帮你重排。"),
]


def tts(text: str, out: str):
    """Agent Plan HTTP TTS：chunked JSON 行流，data 为 base64 mp3"""
    payload = {
        "req_params": {
            "text": text,
            "speaker": SPEAKER,
            "audio_params": {"format": "mp3", "sample_rate": 24000},
        }
    }
    req = urllib.request.Request(
        TTS_URL,
        data=json.dumps(payload).encode(),
        headers={
            "X-Api-Key": os.environ["ARK_API_KEY"],
            "X-Api-Resource-Id": "seed-tts-2.0",
            "Content-Type": "application/json",
        },
        method="POST",
    )
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
    with open(out, "wb") as f:
        f.write(audio)


def probe(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def split_lines(text: str, width: int = 15) -> list:
    """按标点优先断行为最多 3 行，每行约 width 字"""
    if len(text) <= width:
        return [text]
    # 优先在标点处断
    for cut in range(min(width + 2, len(text)), width // 2, -1):
        if text[cut - 1] in "，。？！、；：" and cut <= width + 2:
            return [text[:cut]] + split_lines(text[cut:].lstrip("，。？！、；：") or "", width) \
                if text[cut:].strip("，。？！、；：") else [text[:cut]]
    return [text[:width]] + split_lines(text[width:], width)


def make_clip(idx: int, img: str, mp3: str, dur: float, out: str):
    frames = int(round(dur * FPS))
    # 奇偶镜头交替：缓推 / 缓拉
    if idx % 2 == 0:
        z = "min(zoom+0.0007,1.18)"
    else:
        z = "max(1.18-0.0008*on,1.001)"

    lines = split_lines(STORYBOARD[idx][1])
    draws = []
    ys = [f"h-{300 - i * 78}" for i in range(len(lines))]
    for ln, y in zip(lines, ys):
        safe = ln.replace(":", "").replace("'", "")
        draws.append(
            f"drawtext=fontfile={FONT}:text='{safe}':fontcolor=white:"
            f"borderw=5:bordercolor=black@0.75:fontsize=56:x=(w-text_w)/2:y={y}"
        )

    vf = (
        f"scale=2160:-2,crop=2160:2880,"
        f"zoompan=z='{z}':x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':"
        f"d={frames}:s={OUT_W}x{OUT_H}:fps={FPS},format=yuv420p,"
        + ",".join(draws)
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loop", "1", "-i", img, "-i", mp3,
         "-filter_complex", f"[0:v]{vf}[v];[1:a]apad=whole_dur={dur:.3f}[a]",
         "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-c:a", "aac", "-b:a", "128k", "-ar", "24000", out],
        check=True, capture_output=True,
    )


def main():
    os.makedirs(ADIR, exist_ok=True)
    os.makedirs(CDIR, exist_ok=True)

    # 1. TTS 逐镜头合成
    clips, total = [], 0.0
    for i, (img, text) in enumerate(STORYBOARD):
        mp3 = os.path.join(ADIR, f"vo_{i}.mp3")
        if not os.path.exists(mp3):
            print(f"[TTS] 镜头{i+1}: {text[:18]}…")
            tts(text, mp3)
        ad = probe(mp3)
        dur = round(ad + 0.9, 3)
        clip = os.path.join(CDIR, f"clip_{i}.mp4")
        print(f"[CLIP] 镜头{i+1}: 旁白 {ad:.1f}s → 片段 {dur:.1f}s")
        make_clip(i, img, mp3, dur, clip)
        clips.append((clip, dur))
        total += dur

    # 2. concat 拼接
    lst = os.path.join(VDIR, "concat.txt")
    with open(lst, "w") as f:
        for c, _ in clips:
            f.write(f"file '{os.path.abspath(c)}'\n")
    joined = os.path.join(VDIR, "joined.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", joined],
        check=True, capture_output=True,
    )

    # 3. 首尾淡入淡出 → 成片
    final = os.path.join(VDIR, "video_001.mp4")
    fade_out = max(total - 0.6, 0)
    subprocess.run(
        ["ffmpeg", "-y", "-i", joined,
         "-vf", f"fade=t=in:st=0:d=0.5,fade=t=out:st={fade_out:.2f}:d=0.6",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-c:a", "copy", final],
        check=True, capture_output=True,
    )
    print(f"\n成片: {final} | 总时长 {total:.1f}s | {os.path.getsize(final)//1024//1024} MB")


if __name__ == "__main__":
    main()
