#!/usr/bin/env python3
"""封面底图生成（红狐 GPT 图片生成，异步任务）

生成小红书封面用无字底图（文字由排版层叠加，规避 AI 中文渲染错误）。
下载结果到 assets/cover_base.jpg

用法：
  export REDFOX_API_KEY=ak_xxx
  python3 gen_cover_base.py
"""
import json
import os
import sys
import time
import urllib.request

from redfox import RedFoxClient

PROMPT = (
    "南京明孝陵石象路秋景，金黄色梧桐树长廊，古石像生分列道路两侧，"
    "地面铺满落叶，温暖午后光线，旅行摄影风格，竖版构图，画面干净，无人物，无文字"
)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "cover_base.jpg")


def main():
    key = os.environ.get("REDFOX_API_KEY", "")
    if not key:
        print("未设置 REDFOX_API_KEY", file=sys.stderr)
        sys.exit(1)
    client = RedFoxClient(api_key=key)

    task = client.gpt_image.submit(prompt=PROMPT, resolution="1k", size="3:4", n=1)
    task_id = task["taskId"]
    print(f"任务已提交: {task_id}")

    for i in range(40):  # 最多等 10 分钟
        time.sleep(15)
        r = client.gpt_image.result(task_id=task_id)
        status = r.get("status")
        print(f"[{i+1}] status={status}")
        if status in ("completed", "failed"):
            if status == "failed":
                print("生成失败:", json.dumps(r, ensure_ascii=False)[:500])
                sys.exit(1)
            urls = r.get("imageUrls") or []
            if not urls:
                print("完成但无图片 URL:", json.dumps(r, ensure_ascii=False)[:500])
                sys.exit(1)
            os.makedirs(os.path.dirname(OUT), exist_ok=True)
            urllib.request.urlretrieve(urls[0], OUT)
            print(f"封面底图已下载 -> {OUT}")
            return
    print("超时未完成", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
