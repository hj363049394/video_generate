#!/usr/bin/env python3
"""图文笔记卡片生成（PIL 排版 + Seedream 实景图混合）

统一视觉风格：每张内页 = 实景图区（Seedream 生成）+ 白色信息卡层。
产出 1242x1656（3:4）小红书图：
  itinerary.jpg  顶部梧桐横幅 + 3 日路线总表
  spots.jpg      3 个点位行（实景小图 + 文字）
  stay_hook.jpg  顶部老门东横幅 + 住宿三原则 + 服务钩子

素材（assets/，由 gen_assets_ark.py / Agent Plan 生图产出）：
  banner_wutong.jpg / spot_zoo.jpg / banner_laomendong.jpg
  cover_gen_ref.jpg（石象路，复用为点位图）
用法：python3 gen_images.py
"""
import os

from PIL import Image, ImageDraw, ImageFont

BASE = os.path.dirname(os.path.abspath(__file__))
FONT_R = os.path.join(BASE, "assets", "fonts", "NotoSansSC-Regular.otf")
FONT_B = os.path.join(BASE, "assets", "fonts", "NotoSansSC-Bold.otf")
ASSETS = os.path.join(BASE, "assets")
OUT = os.path.join(BASE, "images")

W, H = 1242, 1656
MARGIN = 66

BG = (251, 246, 238)
CARD = (255, 255, 255)
AMBER = (180, 83, 9)
MAPLE = (194, 65, 12)
GOLD = (217, 119, 6)
DARK = (63, 58, 51)
GRAY = (122, 113, 105)
LEAF = (77, 124, 15)
LINE = (233, 222, 206)
CREAM = (255, 238, 214)


def F(size, bold=True):
    return ImageFont.truetype(FONT_B if bold else FONT_R, size)


def load_crop(name: str, w: int, h: int) -> Image.Image:
    """加载素材并按目标比例中心裁切、缩放"""
    im = Image.open(os.path.join(ASSETS, name)).convert("RGB")
    sw, sh = im.size
    scale = max(w / sw, h / sh)
    nw, nh = int(sw * scale + 0.5), int(sh * scale + 0.5)
    im = im.resize((nw, nh), Image.LANCZOS)
    x, y = (nw - w) // 2, (nh - h) // 2
    return im.crop((x, y, x + w, y + h))


def paste_rounded(canvas: Image.Image, im: Image.Image, xy, radius=20):
    """以圆角形式贴图"""
    mask = Image.new("L", im.size, 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, im.size[0] - 1, im.size[1] - 1], radius=radius, fill=255)
    canvas.paste(im, xy, mask)


def paste_banner(canvas: Image.Image, name: str, height: int):
    """全宽顶幅：贴图 + 底部渐变过渡到背景色"""
    im = load_crop(name, W, height)
    canvas.paste(im, (0, 0))
    d = ImageDraw.Draw(canvas, "RGBA")
    y0 = int(height * 0.62)
    for y in range(y0, height):
        t = (y - y0) / (height - y0)
        a = int(235 * (t ** 1.1))
        d.line([(0, y), (W, y)], fill=BG + (a,))
    # 顶部轻压暗（文字可读）
    for y in range(240):
        a = int(130 * (1 - y / 240) ** 1.4)
        d.line([(0, y), (W, y)], fill=(12, 9, 6, a))


def banner_text(d: ImageDraw.ImageDraw, xy, text, font, fill=CREAM, anchor="la", shadow=(0, 0, 0, 170)):
    x, y = xy
    d.text((x + 4, y + 4), text, font=font, fill=shadow, anchor=anchor, stroke_width=2, stroke_fill=shadow)
    d.text((x, y), text, font=font, fill=fill, anchor=anchor, stroke_width=1, stroke_fill=(0, 0, 0, 80))


def pill(d, x, y, text, font, fg, bg, padx=26, h=None):
    tw = font.getlength(text)
    h = h or font.size + 24
    w = tw + padx * 2
    d.rounded_rectangle([x, y, x + w, y + h], radius=h / 2, fill=bg)
    d.text((x + padx, y + (h - font.size) / 2 - font.size * 0.12), text, font=font, fill=fg)
    return w


def wrap(d, x, y, text, font, fill, max_w, line_gap=10, max_lines=99):
    line, lines = "", []
    for ch in text:
        if font.getlength(line + ch) > max_w and line:
            lines.append(line)
            line = ch
        else:
            line += ch
    lines.append(line)
    for i, ln in enumerate(lines[:max_lines]):
        d.text((x, y + i * (font.size + line_gap)), ln, font=font, fill=fill)
    return y + min(len(lines), max_lines) * (font.size + line_gap)


# ─────────────────────── 2. 路线总表（梧桐横幅） ───────────────────────
def gen_itinerary():
    img = Image.new("RGB", (W, H), BG)
    paste_banner(img, "banner_wutong.jpg", 580)
    d = ImageDraw.Draw(img, "RGBA")
    banner_text(d, (MARGIN, 96), "3 日路线总表", F(66))
    banner_text(d, (MARGIN, 186), "照着走 · 上午一个重点，下午一个重点", F(42, False))

    days = [
        ("Day 1", "钟山赏秋线", "上午 明孝陵石象路（秋天最美的 600 米）· 下午 梧桐大道 → 灵谷寺",
         "地铁 2 号线苜蓿园站，景区观光车串联"),
        ("Day 2", "红山动物园", "上午 开园就进（动物最活跃）· 下午 回酒店午睡，傍晚玄武湖遛弯",
         "地铁 1 号线红山动物园站，出站即达"),
        ("Day 3", "博物馆收尾", "上午 南京博物院（提前 7 天预约）· 下午 鸡鸣寺 → 台城城墙 → 返程",
         "地铁 2 号线明故宫站附近，返程顺路"),
    ]
    y = 640
    for tag, theme, content, traffic in days:
        d.rounded_rectangle([MARGIN, y, W - MARGIN, y + 264], radius=26, fill=CARD, outline=LINE, width=2)
        pill(d, MARGIN + 34, y + 30, tag, F(38, True), (255, 255, 255), MAPLE)
        d.text((MARGIN + 200, y + 34), theme, font=F(52), fill=DARK)
        wrap(d, MARGIN + 34, y + 122, content, F(42, False), DARK, W - MARGIN * 2 - 68, max_lines=2)
        d.text((MARGIN + 34, y + 208), "🚇 " if False else "· ", font=F(36), fill=GOLD)
        d.text((MARGIN + 58, y + 206), traffic, font=F(36), fill=GRAY)
        y += 286

    d.rounded_rectangle([MARGIN, 1520, W - MARGIN, 1600], radius=22, fill=(240, 247, 228))
    d.text((W / 2, 1560), "节奏原则：上午一个重点 · 下午一个重点，不赶路", font=F(42), fill=LEAF, anchor="mm")
    img.save(os.path.join(OUT, "itinerary.jpg"), quality=92)


# ─────────────────────── 3. 点位卡（实景小图 + 文字） ───────────────────────
def gen_spots():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle([MARGIN, 66, W - MARGIN, 170], radius=26, fill=AMBER)
    d.text((W / 2, 118), "重点点位 · 最佳时段", font=F(56), fill=(255, 255, 255), anchor="mm")

    spots = [
        ("cover_gen_ref.jpg", "石象路", "出片天花板",
         [("最佳时段", "上午 8:00-10:00，光线柔和游客少"), ("拍照点", "石象长廊中段、满地落叶的路面")]),
        ("banner_wutong.jpg", "梧桐大道", "免费慢行",
         [("怎么玩", "陵园路骑行或漫步，随手出片"), ("最佳时段", "下午 4 点后斜阳穿树隙最有氛围")]),
        ("spot_zoo.jpg", "红山动物园", "娃的天花板",
         [("最佳时段", "开园即入（约 9:00），动物最活跃"), ("提醒", "需提前预约，避开 10 点后人流高峰")]),
    ]
    y = 204
    for img_name, name, tag, rows in spots:
        d.rounded_rectangle([MARGIN, y, W - MARGIN, y + 430], radius=26, fill=CARD, outline=LINE, width=2)
        paste_rounded(img, load_crop(img_name, 382, 382), (MARGIN + 34, y + 24), radius=20)
        tx = MARGIN + 456
        d.text((tx, y + 40), name, font=F(52), fill=DARK)
        pill(d, tx + F(52).getlength(name) + 22, y + 44, tag, F(34, True), AMBER, (253, 236, 213))
        ry = y + 136
        for label, content in rows:
            d.text((tx, ry), label, font=F(36), fill=GOLD)
            ry = wrap(d, tx, ry + 52, content, F(40, False), DARK, W - MARGIN - 34 - tx, max_lines=2) + 14
        d.text((tx, y + 388), "门票/预约以官方渠道为准", font=F(32, False), fill=GRAY)
        y += 450

    d.rounded_rectangle([MARGIN, 1566, W - MARGIN, 1636], radius=20, fill=(253, 236, 213))
    d.text((W / 2, 1601), "以上为规划参考，开放时间以各官方渠道实时为准", font=F(36), fill=AMBER, anchor="mm")
    img.save(os.path.join(OUT, "spots.jpg"), quality=92)


# ─────────────────────── 4. 住宿 + 钩子（老门东横幅） ───────────────────────
def gen_stay_hook():
    img = Image.new("RGB", (W, H), BG)
    paste_banner(img, "banner_laomendong.jpg", 560)
    d = ImageDraw.Draw(img, "RGBA")
    banner_text(d, (MARGIN, 92), "带娃住宿 · 规划师只看 3 点", F(62))
    banner_text(d, (MARGIN, 180), "住夫子庙-老门东片区，动线最顺", F(42, False))

    rules = [
        ("①", "动线", "去钟山、博物院地铁直达，少折腾就是少踩坑"),
        ("②", "床", "亲子房或能加床优先，娃睡好第二天才不崩"),
        ("③", "退改", "国庆计划容易变，免费退改的房源最踏实"),
    ]
    cw = (W - MARGIN * 2 - 48) // 3
    y = 620
    for i, (num, name, desc) in enumerate(rules):
        x = MARGIN + i * (cw + 24)
        d.rounded_rectangle([x, y, x + cw, y + 460], radius=26, fill=CARD, outline=LINE, width=2)
        d.ellipse([x + cw / 2 - 44, y + 36, x + cw / 2 + 44, y + 124], fill=(253, 236, 213))
        d.text((x + cw / 2, y + 80), num, font=F(46), fill=AMBER, anchor="mm")
        d.text((x + cw / 2, y + 152), name, font=F(48), fill=DARK, anchor="mm")
        wrap(d, x + 34, y + 232, desc, F(36, False), GRAY, cw - 68)
        y_line = y  # noqa

    d.rounded_rectangle([MARGIN, 1120, W - MARGIN, 1500], radius=32, fill=MAPLE)
    d.text((W / 2, 1190), "这份是 2 大 1 小的默认节奏", font=F(42, False), fill=(255, 226, 214), anchor="mm")
    d.text((W / 2, 1268), "评论区报：人数 / 天数 / 娃年龄", font=F(52), fill=(255, 255, 255), anchor="mm")
    d.text((W / 2, 1340), "帮你重新排一版不赶路的行程", font=F(46, False), fill=(255, 255, 255), anchor="mm")
    d.rounded_rectangle([W / 2 - 190, 1390, W / 2 + 190, 1450], radius=30, fill=(255, 255, 255, 40))
    d.text((W / 2, 1420), "关注 @ 行程规划旅行家", font=F(38), fill=(255, 255, 255), anchor="mm")
    d.text((W / 2, 1580), "· 排程不踩坑 ·", font=F(36), fill=AMBER, anchor="mm")
    img.save(os.path.join(OUT, "stay_hook.jpg"), quality=92)


def main():
    os.makedirs(OUT, exist_ok=True)
    gen_itinerary()
    gen_spots()
    gen_stay_hook()
    for f in sorted(os.listdir(OUT)):
        p = os.path.join(OUT, f)
        print(f"{f}: {Image.open(p).size}, {os.path.getsize(p) // 1024} KB")


if __name__ == "__main__":
    main()
