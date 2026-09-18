#!/usr/bin/env python3
"""补充实景素材生成（Agent Plan · Seedream 5.0-lite）

为内页生成 3 张实景图，风格锚点与封面一致（旅行摄影/暖秋色调/无人物无文字）：
  banner_wutong.jpg   梧桐大道横幅（图2 顶部）
  spot_zoo.jpg        小熊猫秋日动物园（图3 点位图）
  banner_laomendong.jpg 老门东秋色横幅（图4 顶部）

用法：export ARK_API_KEY=... && python3 gen_assets_ark.py
"""
import json
import os
import time
import urllib.request

API = "https://ark.cn-beijing.volces.com/api/plan/v3/images/generations"

JOBS = [
    {
        "out": "banner_wutong.jpg",
        "prompt": "南京陵园路梧桐大道，金色法国梧桐成荫形成的绿色隧道，秋日午后阳光透过树叶洒下斑驳光影，道路延伸向远方，旅行摄影风格，横版构图，无人物，无文字",
        "size": "2304x1728",
    },
    {
        "out": "spot_zoo.jpg",
        "prompt": "秋日动物园里一只可爱的小熊猫趴在树枝上，红棕色毛发蓬松，背景是金黄秋叶的自然栖息环境，温暖午后光线，自然摄影风格，主体居中，无文字",
        "size": "1728x2304",
    },
    {
        "out": "banner_laomendong.jpg",
        "prompt": "南京老门东历史街区，青砖黛瓦的传统建筑与庭院里金黄的银杏树，红灯笼点缀，傍晚温暖光线，宁静氛围，旅行摄影风格，横版构图，无人物，无文字",
        "size": "2304x1728",
    },
]


def main():
    key = os.environ.get("ARK_API_KEY", "")
    if not key:
        raise SystemExit("未设置 ARK_API_KEY")
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

    for job in JOBS:
        body = json.dumps({
            "model": "doubao-seedream-5.0-lite",
            "prompt": job["prompt"],
            "size": job["size"],
            "response_format": "url",
            "watermark": False,
        }).encode()
        req = urllib.request.Request(
            API, data=body, method="POST",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
        url = data["data"][0]["url"]
        out = os.path.join(out_dir, job["out"])
        urllib.request.urlretrieve(url, out)
        print(f"{job['out']}: 生成并下载完成")
        time.sleep(2)


if __name__ == "__main__":
    main()
