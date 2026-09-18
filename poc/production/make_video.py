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
SPEAKER = "zh_female_qingxinnvsheng_uranus_bigtts"  # 清新女声
BGM = os.path.join(BASE, "assets", "bgm_travel.mp3")  # Carefree · Kevin MacLeod (CC-BY)
BGM_VOL = 0.16       # BGM 音量（旁白优先）
XFADE = 0.5          # 镜头叠化时长
FPS = 25
OUT_W, OUT_H = 1080, 1440  # 3:4 竖版

# 分镜脚本：图文严格同源——每段旁白只讲对应图片上承载的内容
STORYBOARD = [
    # 图1 封面：标题大字 + Day1/2/3 预告 → 开场钩子
    ("images/cover_photo_ref.jpg", "国庆带娃去南京，怕人多又想看秋色？这份三天的赏秋路线，直接抄作业。"),
    # 图2 三日总览卡：Day1 钟山赏秋线 / Day2 红山动物园 / Day3 博物院收尾 → 三天概览 + 预约提醒
    ("images/itinerary.jpg", "三天这样排：第一天钟山赏秋线，第二天红山动物园，第三天南京博物院收尾，博物院记得提前七天预约。"),
    # 图3 红山实景（小熊猫）→ Day2 重点展开
    ("assets/spot_zoo.jpg", "重点说红山动物园，开园就进，动物上午最活跃，娃能开心一上午。"),
    # 图4 点位卡：石象路 8-10 点 / 梧桐大道下午 4 点后 / 红山开园即入 → 只讲图上三个时段
    ("images/spots.jpg", "出片时段记好：石象路早上八点到十点，光线好游客少；梧桐大道下午四点后，斜阳穿树最美。"),
    # 图5 住宿三原则（动线/床/退改）+ 评论区钩子 → 同步收尾
    ("images/stay_hook.jpg", "带娃住宿记住三点：动线顺、床舒服、能免费退改。你家情况不一样？评论区报人数天数，我帮你重排一版。"),
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

    # 2. xfade 叠化拼接（视频 + 音频同步过渡）
    n = len(clips)
    inputs = []
    for c, _ in clips:
        inputs += ["-i", c]

    vf_chain, prev = [], "[0:v]"
    acc = 0.0
    for i in range(1, n):
        acc += clips[i - 1][1]
        offset = acc - i * XFADE
        out = f"[v{i}]" if i < n - 1 else "[vout]"
        vf_chain.append(f"{prev}[{i}:v]xfade=transition=fade:duration={XFADE}:offset={offset:.3f}{out}")
        prev = out
    af_chain, prev = [], "[0:a]"
    for i in range(1, n):
        out = f"[a{i}]" if i < n - 1 else "[avox]"
        af_chain.append(f"{prev}[{i}:a]acrossfade=d={XFADE}{out}")
        prev = out

    total = sum(d for _, d in clips) - (n - 1) * XFADE
    joined = os.path.join(VDIR, "joined.mp4")
    # BGM：循环铺满 + 音量压低 + 淡入淡出，与旁白混音（旁白优先）
    bg_fade_out = max(total - 2.5, 0)
    subprocess.run(
        ["ffmpeg", "-y", *inputs, "-stream_loop", "-1", "-i", BGM,
         "-filter_complex",
         ";".join(vf_chain + af_chain) +
         f";[{n}:a]volume={BGM_VOL},afade=t=in:d=1.5,"
         f"afade=t=out:st={bg_fade_out:.2f}:d=2.2[bgm];"
         f"[avox][bgm]amix=inputs=2:duration=first:normalize=0[aout]",
         "-map", "[vout]", "-map", "[aout]",
         "-t", f"{total:.3f}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-c:a", "aac", "-b:a", "160k", joined],
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
