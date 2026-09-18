#!/usr/bin/env python3
"""音色试听批量生成：同一段口播文案 × 6 个候选音色 → 预览页试听

用法：export ARK_API_KEY=专属key && python3 gen_voice_samples.py
"""
import sys

from make_video import ADIR, TTS_URL, tts  # 复用 TTS 实现
import json
import os
import urllib.request

SAMPLE_TEXT = "国庆带娃去南京，怕人多又想看秋色？这份三天的赏秋路线，直接抄作业。第一天钟山赏秋，石象路上午去，光线好还出片。"

CANDIDATES = [
    # (voice_type, 名字, 风格说明)
    ("zh_female_kailangjiejie_moon_bigtts", "开朗姐姐", "女·分享感强，语气上扬，最贴近口播博主"),
    ("zh_female_linjianvhai_uranus_bigtts", "邻家女孩", "女·亲切自然，像朋友聊天，不端着"),
    ("zh_female_shuangkuaisisi_uranus_bigtts", "爽快思思", "女·干脆利落，攻略信息感强"),
    ("zh_female_qingxinnvsheng_uranus_bigtts", "清新女声", "女·文艺清新，慢节奏旅行 vlog 风"),
    ("zh_male_yangguangqingnian_uranus_bigtts", "阳光青年", "男·活力阳光，年轻旅行博主感"),
    ("zh_male_yuanboxiaoshu_uranus_bigtts", "渊博小叔", "男·有阅历的沉稳感，带娃旅行博主气质"),
]


def main():
    key = os.environ.get("ARK_API_KEY")
    if not key:
        sys.exit("未设置 ARK_API_KEY")

    import importlib
    import make_video
    results = []
    for voice, name, desc in CANDIDATES:
        out = os.path.join(ADIR, f"sample_{name}.mp3")
        # 临时替换音色调用
        make_video.SPEAKER = voice
        try:
            tts(SAMPLE_TEXT, out)
            print(f"✓ {name}: {out}")
            results.append((name, voice, desc))
        except Exception as e:
            print(f"✗ {name}: {e}")

    # 输出映射供预览页使用
    print("\n--- 试听清单 ---")
    for name, voice, desc in results:
        print(f"{name} | {voice} | {desc}")


if __name__ == "__main__":
    main()
