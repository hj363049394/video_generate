#!/usr/bin/env python3
"""图文笔记卡片生成（PIL 排版，无 AI 生图依赖）

产出 4 张 1242x1656（3:4）小红书图：
  1. cover.jpg      大字报封面
  2. itinerary.jpg  3 日路线总表
  3. spots.jpg      重点点位卡片
  4. stay_hook.jpg  住宿三原则 + 服务钩子

文案数据源：rewrite_001.json（POC 阶段卡片化文案内嵌于此，后续可改为程序化拆解）
用法：python3 gen_images.py
"""
import os

from PIL import Image, ImageDraw, ImageFont

BASE = os.path.dirname(os.path.abspath(__file__))
FONT_R = os.path.join(BASE, "assets", "fonts", "NotoSansSC-Regular.otf")
FONT_B = os.path.join(BASE, "assets", "fonts", "NotoSansSC-Bold.otf")
OUT = os.path.join(BASE, "images")

W, H = 1242, 1656
MARGIN = 66

# 暖秋干货风配色
BG = (251, 246, 238)
CARD = (255, 255, 255)
AMBER = (180, 83, 9)
MAPLE = (194, 65, 12)
GOLD = (217, 119, 6)
DARK = (63, 58, 51)
GRAY = (122, 113, 105)
LEAF = (77, 124, 15)
LINE = (233, 222, 206)


def F(size, bold=False):
    return ImageFont.truetype(FONT_B if bold else FONT_R, size)


def new_canvas():
    img = Image.new("RGB", (W, H), BG)
    return img, ImageDraw.Draw(img)


def pill(draw, cx_or_x, y, text, font, fg, bg, centered=True, padx=34, h=None):
    """圆角胶囊标签，返回 (x, y, w, h)"""
    tw = font.getlength(text)
    h = h or font.size + 28
    w = tw + padx * 2
    x = cx_or_x - w / 2 if centered else cx_or_x
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h / 2, fill=bg)
    draw.text((x + padx, y + (h - font.size) / 2 - font.size * 0.12), text, font=font, fill=fg)
    return x, y, w, h


def wrap(draw, x, y, text, font, fill, max_w, line_gap=12, max_lines=99):
    """中文逐字换行绘制，返回结束 y"""
    line, lines = "", []
    for ch in text:
        if font.getlength(line + ch) > max_w and line:
            lines.append(line)
            line = ch
        else:
            line += ch
    lines.append(line)
    for i, ln in enumerate(lines[:max_lines]):
        draw.text((x, y + i * (font.size + line_gap)), ln, font=font, fill=fill)
    return y + min(len(lines), max_lines) * (font.size + line_gap)


def deco_dots(draw):
    """角落装饰圆点（落叶色系）"""
    spots = [(W - 120, 90, 26, GOLD), (W - 78, 140, 14, MAPLE), (96, H - 96, 22, GOLD), (146, H - 66, 12, LEAF)]
    for x, y, r, c in spots:
        draw.ellipse([x - r, y - r, x + r, y + r], fill=c)


# ─────────────────────────── 1. 封面 ───────────────────────────
def gen_cover():
    img, d = new_canvas()
    deco_dots(d)
    pill(d, W / 2, 84, "国庆中秋 · 带娃亲子游", F(42, True), (255, 255, 255), AMBER)
    d.text((W / 2, 220), "南京 3 日", font=F(158, True), fill=DARK, anchor="ma")
    d.text((W / 2, 412), "赏秋路线", font=F(158, True), fill=MAPLE, anchor="ma")
    # 分隔圆点
    for i in range(3):
        x = W / 2 + (i - 1) * 46
        d.ellipse([x - 7, 632 - 7, x + 7, 632 + 7], fill=GOLD)
    d.text((W / 2, 676), "避开人从众 · 带娃直接抄作业", font=F(56), fill=DARK, anchor="ma")

    days = [
        ("Day 1", "钟山赏秋线", "石象路 · 梧桐大道 · 灵谷寺"),
        ("Day 2", "红山动物园", "开园即进 · 娃的南京天花板"),
        ("Day 3", "博物馆收尾", "南京博物院 · 台城城墙"),
    ]
    y = 800
    for tag, title, sub in days:
        d.rounded_rectangle([MARGIN, y, W - MARGIN, y + 168], radius=28, fill=CARD, outline=LINE, width=2)
        pill(d, MARGIN + 44, y + 36, tag, F(40, True), (255, 255, 255), MAPLE, centered=False)
        d.text((MARGIN + 48, y + 104), title, font=F(58, True), fill=DARK)
        d.text((W - MARGIN - 48, y + 116), sub, font=F(40), fill=GRAY, anchor="ra")
        y += 168 + 28
    # 底部条
    d.rounded_rectangle([MARGIN, H - 148, W - MARGIN, H - 76], radius=24, fill=(252, 240, 220))
    d.text((W / 2, H - 112), "@ 行程规划旅行家 · 排程不踩坑", font=F(42, True), fill=AMBER, anchor="mm")
    img.save(os.path.join(OUT, "cover.jpg"), quality=92)


# ─────────────────────────── 2. 路线总表 ───────────────────────────
def gen_itinerary():
    img, d = new_canvas()
    deco_dots(d)
    d.rounded_rectangle([MARGIN, 76, W - MARGIN, 190], radius=28, fill=AMBER)
    d.text((W / 2, 133), "3 日路线总表 · 照着走", font=F(60, True), fill=(255, 255, 255), anchor="mm")

    days = [
        ("Day 1", "钟山赏秋线", [
            ("上午", "明孝陵石象路：秋天最美的 600 米，光线好出片"),
            ("下午", "陵园路梧桐大道（漫步/骑行）→ 灵谷寺，人少桂花香"),
        ], "交通：地铁 2 号线苜蓿园站，景区内观光车串联"),
        ("Day 2", "红山动物园日", [
            ("上午", "开园就进（动物上午最活跃），全程不无聊"),
            ("下午", "回酒店午睡充电 → 傍晚玄武湖遛弯"),
        ], "交通：地铁 1 号线红山动物园站，出站即达"),
        ("Day 3", "博物馆收尾", [
            ("上午", "南京博物院（提前 7 天预约！民国馆娃会看呆）"),
            ("下午", "鸡鸣寺 - 台城城墙（秋色 + 视野）→ 返程"),
        ], "交通：博物院在地铁 2 号线明故宫站附近，返程顺路"),
    ]
    y = 244
    for tag, theme, rows, traffic in days:
        ch = 148 + len(rows) * 96
        d.rounded_rectangle([MARGIN, y, W - MARGIN, y + ch], radius=28, fill=CARD, outline=LINE, width=2)
        pill(d, MARGIN + 40, y + 36, tag, F(40, True), (255, 255, 255), MAPLE, centered=False)
        d.text((MARGIN + 210, y + 42), theme, font=F(56, True), fill=DARK)
        ry = y + 128
        for label, content in rows:
            pill(d, MARGIN + 40, ry, label, F(34, True), AMBER, (253, 236, 213), centered=False, padx=24)
            ry = wrap(d, MARGIN + 190, ry - 2, content, F(44), DARK, W - MARGIN * 2 - 200, max_lines=2) + 22
        d.text((MARGIN + 40, y + ch - 58), traffic, font=F(36), fill=GRAY)
        y += ch + 30
    d.rounded_rectangle([MARGIN, H - 130, W - MARGIN, H - 62], radius=22, fill=(240, 247, 228))
    d.text((W / 2, H - 96), "节奏原则：上午一个重点 · 下午一个重点，不赶路", font=F(42, True), fill=LEAF, anchor="mm")
    img.save(os.path.join(OUT, "itinerary.jpg"), quality=92)


# ─────────────────────────── 3. 点位卡片 ───────────────────────────
def gen_spots():
    img, d = new_canvas()
    deco_dots(d)
    d.rounded_rectangle([MARGIN, 76, W - MARGIN, 190], radius=28, fill=AMBER)
    d.text((W / 2, 133), "重点点位 · 最佳时段", font=F(60, True), fill=(255, 255, 255), anchor="mm")

    spots = [
        ("石象路", "出片天花板", [
            ("最佳时段", "上午 8:00-10:00，光线柔和游客少"),
            ("拍照点", "石象长廊中段、满地落叶的路面"),
            ("提醒", "属明孝陵景区，需门票，以官方渠道为准"),
        ]),
        ("陵园路梧桐大道", "免费慢行", [
            ("怎么玩", "骑行或漫步，梧桐隧道随手出片"),
            ("最佳时段", "下午 4 点后，斜阳穿过树隙最有氛围"),
        ]),
        ("红山动物园", "娃的天花板", [
            ("最佳时段", "开园即入（约 9:00），动物上午最活跃"),
            ("体验", "场馆设计沉浸不无聊，遮阴好不怕晒"),
            ("提醒", "需提前预约，避开上午 10 点后人流高峰"),
        ]),
    ]
    y = 244
    for name, tag, rows in spots:
        ch = 150 + len(rows) * 92
        d.rounded_rectangle([MARGIN, y, W - MARGIN, y + ch], radius=28, fill=CARD, outline=LINE, width=2)
        d.text((MARGIN + 40, y + 34), name, font=F(58, True), fill=DARK)
        tw = F(38, True).getlength(tag)
        d.rounded_rectangle(
            [W - MARGIN - 46 - tw - 56, y + 40, W - MARGIN - 46, y + 40 + 66], radius=33, fill=(253, 236, 213)
        )
        d.text((W - MARGIN - 46 - 28 - tw / 2, y + 73), tag, font=F(38, True), fill=AMBER, anchor="mm")
        ry = y + 136
        for label, content in rows:
            d.text((MARGIN + 40, ry), label, font=F(38, True), fill=GOLD)
            ry = wrap(d, MARGIN + 210, ry - 4, content, F(42), DARK, W - MARGIN * 2 - 220, max_lines=2) + 18
        y += ch + 30
    d.rounded_rectangle([MARGIN, H - 130, W - MARGIN, H - 62], radius=22, fill=(253, 236, 213))
    d.text((W / 2, H - 96), "门票与预约信息以各官方渠道实时为准", font=F(40), fill=AMBER, anchor="mm")
    img.save(os.path.join(OUT, "spots.jpg"), quality=92)


# ─────────────────────────── 4. 住宿 + 钩子 ───────────────────────────
def gen_stay_hook():
    img, d = new_canvas()
    deco_dots(d)
    d.rounded_rectangle([MARGIN, 76, W - MARGIN, 190], radius=28, fill=AMBER)
    d.text((W / 2, 133), "带娃住宿 · 规划师只看 3 点", font=F(58, True), fill=(255, 255, 255), anchor="mm")

    rules = [
        ("①", "动线", "住夫子庙-老门东片区：去钟山、博物院地铁直达，少折腾就是少踩坑"),
        ("②", "床", "亲子房或能加床优先：娃睡得好，第二天大人孩子才都不崩"),
        ("③", "退改", "国庆计划最容易变：选免费退改的房源，怎么改都踏实"),
    ]
    y = 244
    for num, name, desc in rules:
        ch = 210
        d.rounded_rectangle([MARGIN, y, W - MARGIN, y + ch], radius=28, fill=CARD, outline=LINE, width=2)
        d.ellipse([MARGIN + 40, y + 52, MARGIN + 40 + 106, y + 52 + 106], fill=(253, 236, 213))
        d.text((MARGIN + 40 + 53, y + 105), num, font=F(52, True), fill=AMBER, anchor="mm")
        d.text((MARGIN + 190, y + 44), name, font=F(56, True), fill=DARK)
        wrap(d, MARGIN + 190, y + 126, desc, F(42), GRAY, W - MARGIN * 2 - 230)
        y += ch + 26
    # 服务钩子大色块
    d.rounded_rectangle([MARGIN, y + 26, W - MARGIN, y + 316], radius=32, fill=MAPLE)
    d.text((W / 2, y + 96), "这份是 2 大 1 小的默认节奏", font=F(44), fill=(255, 226, 214), anchor="mm")
    d.text((W / 2, y + 168), "评论区报：人数 / 天数 / 娃年龄", font=F(54, True), fill=(255, 255, 255), anchor="mm")
    d.text((W / 2, y + 240), "帮你重新排一版不赶路的行程", font=F(48), fill=(255, 255, 255), anchor="mm")
    d.text((W / 2, H - 66), "@ 行程规划旅行家 · 关注不迷路", font=F(40, True), fill=AMBER, anchor="mm")
    img.save(os.path.join(OUT, "stay_hook.jpg"), quality=92)


def main():
    os.makedirs(OUT, exist_ok=True)
    gen_cover()
    gen_itinerary()
    gen_spots()
    gen_stay_hook()
    for f in sorted(os.listdir(OUT)):
        p = os.path.join(OUT, f)
        print(f"{f}: {Image.open(p).size}, {os.path.getsize(p) // 1024} KB")


if __name__ == "__main__":
    main()
