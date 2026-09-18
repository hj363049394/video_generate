#!/usr/bin/env python3
"""封面合成：AI 底图 + 渐变遮罩 + 中文大字层

输入：assets/cover_gen_text.jpg（纯文生图）、assets/cover_gen_ref.jpg（对标封面参考图生图）
输出：images/cover_photo_text.jpg、images/cover_photo_ref.jpg（1728x2304）
文字全部由排版层渲染（规避 AI 中文文字错误），底图照片提供氛围与质感。
用法：python3 gen_cover_final.py
"""
import os

from PIL import Image, ImageDraw, ImageFont

BASE = os.path.dirname(os.path.abspath(__file__))
FONT_B = os.path.join(BASE, "assets", "fonts", "NotoSansSC-Bold.otf")
FONT_R = os.path.join(BASE, "assets", "fonts", "NotoSansSC-Regular.otf")
ASSETS = os.path.join(BASE, "assets")
OUT = os.path.join(BASE, "images")

W, H = 1728, 2304

WHITE = (255, 255, 255, 255)
CREAM = (255, 238, 214, 255)
AMBER = (234, 138, 30, 255)
SHADOW = (0, 0, 0, 160)


def F(size, bold=True):
    return ImageFont.truetype(FONT_B if bold else FONT_R, size)


def add_gradient(img: Image.Image) -> Image.Image:
    """底部渐暗（文字可读）+ 顶部轻压暗"""
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    y0 = 1150
    for y in range(H):
        if y >= y0:
            t = (y - y0) / (H - y0)
            a = int(215 * (t ** 1.15))
            d.line([(0, y), (W, y)], fill=(12, 9, 6, a))
        elif y < 420:  # 顶部轻压暗
            t = 1 - y / 420
            a = int(120 * (t ** 1.5))
            d.line([(0, y), (W, y)], fill=(12, 9, 6, a))
    return Image.alpha_composite(img.convert("RGBA"), overlay)


def text_with_shadow(d, xy, text, font, fill, anchor="la", shadow_off=5):
    x, y = xy
    # 阴影层
    d.text((x + shadow_off, y + shadow_off), text, font=font, fill=SHADOW, anchor=anchor,
           stroke_width=2, stroke_fill=SHADOW)
    d.text((x, y), text, font=font, fill=fill, anchor=anchor,
           stroke_width=2, stroke_fill=(0, 0, 0, 90))


def compose(src_name: str, out_name: str):
    base = Image.open(os.path.join(ASSETS, src_name)).convert("RGBA").resize((W, H))
    img = add_gradient(base)
    d = ImageDraw.Draw(img)

    # 顶部标签胶囊
    tag_font = F(58)
    tag = "国庆中秋 · 带娃亲子游"
    tw = tag_font.getlength(tag)
    px, py = 84, 96
    d.rounded_rectangle([px, py, px + tw + 88, py + 104], radius=52, fill=(28, 22, 16, 200))
    d.text((px + 44, py + 52 - 8), tag, font=tag_font, fill=CREAM, anchor="lm")

    # 右上部署名
    text_with_shadow(d, (W - 84, 148), "@ 行程规划旅行家", F(52, False), CREAM, anchor="rm")

    # 底部主标题
    text_with_shadow(d, (W / 2, 1600), "南京 3 日", F(210), WHITE, anchor="mm", shadow_off=8)
    text_with_shadow(d, (W / 2, 1830), "赏秋路线", F(210), WHITE, anchor="mm", shadow_off=8)

    # 副题
    text_with_shadow(d, (W / 2, 2010), "避开人从众 · 带娃直接抄作业", F(72, False), CREAM, anchor="mm")

    # Day 一行
    days = "Day1 钟山赏秋   Day2 红山动物园   Day3 博物馆收尾"
    text_with_shadow(d, (W / 2, 2140), days, F(54, False), (255, 255, 255, 230), anchor="mm")

    img.convert("RGB").save(os.path.join(OUT, out_name), quality=92)
    print(f"{out_name}: 已生成")


def main():
    os.makedirs(OUT, exist_ok=True)
    compose("cover_gen_text.jpg", "cover_photo_text.jpg")
    compose("cover_gen_ref.jpg", "cover_photo_ref.jpg")


if __name__ == "__main__":
    main()
