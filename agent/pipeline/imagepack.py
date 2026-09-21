"""xhs-imagepack · 图文卡片生成 SKILL（Phase 1.5）

两步式（数据驱动，任意选题可复用）：
  1. plan_layout(llm_call, rewrite_result) -> layout dict
     LLM 先判断内容类型（itinerary 行程攻略 / narrative 叙事情绪），再编排版式
     —— 版式跟随正文结构，禁止把叙事型硬套进行程表（2026-09-21 修复）
  2. generate_pack(layout, gen, out_dir, assets_dir) -> [图片路径]
     底图走 imagegen 双通道；排版用 PIL 版式引擎（移植自 POC gen_images.py，参数化）

产出 4 张 1242x1656（3:4）：
  itinerary 型：cover / itinerary / spots / stay_hook（+ 5 段分镜）
  narrative 型：cover / story_1 / story_2 / ending（+ 4 段分镜，整页底图金句杂志风）
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Callable, Dict, List

from PIL import Image, ImageDraw, ImageFont

from pipeline.imagegen import ImageGenRouter

# ─── 版式常量（继承 POC 定稿视觉规范） ───────────────────────────────────
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


# ─── 第一步：LLM 版式编排 ───────────────────────────────────────────────

LAYOUT_PROMPT = """你是小红书图文排版师。先判断这篇仿写稿的内容类型，再排版成对应版式。
版式必须跟随正文实际结构——叙事/观点型内容禁止硬套行程表（这是铁律）。

## 类型判断（note_type 二选一）
- itinerary：正文按天推进，含交通/住宿/预算/时段等行程规划信息
- narrative：正文是叙事、感悟、情绪、观点、对比反思（无按天结构，无实用排程信息）

## 铁律
- 图文同源：第 N 段旁白只讲第 N 张图上承载的内容，不得串图
- 排版密度参照小红书干货卡：每行不超 22 字，内容行最多 2 行
- 分镜旁白为口播文案：去书面化、短句、每段 30-60 字
- 人设是旅行家：ending/收尾卡保留服务钩子（评论区报人数/天数/预算）

## 仿写稿
标题：{title}
正文：{content}
标签：{tags}

## 输出（严格 JSON，无其他文字；按判断的类型输出对应结构）

itinerary 型：
```json
{{
  "note_type": "itinerary",
  "cover": {{"image_prompt": "封面底图提示词（本篇目的地标志性场景，旅行摄影，无人物无文字）",
             "title": "封面大标题（18字内，含emoji）", "subtitle": "副标题（15字内）"}},
  "itinerary": {{"image_prompt": "路线卡顶部横幅提示词（本篇核心景观大道/街区，无人物无文字）",
                 "title": "3 日路线总表", "subtitle": "排法一句话（15字内）",
                 "days": [
                   {{"tag": "Day 1", "theme": "当日主题（6字内）", "content": "上午…·下午…（40字内）", "traffic": "地铁/交通一句话（20字内）"}},
                   {{"tag": "Day 2", "theme": "", "content": "", "traffic": ""}},
                   {{"tag": "Day 3", "theme": "", "content": "", "traffic": ""}}
                 ],
                 "footer": "节奏原则一句话（18字内）"}},
  "spots": {{"title": "重点点位 · 最佳时段",
             "spots": [
               {{"image_prompt": "点位1实景提示词", "name": "点位名（6字内）", "tag": "一句话标签（6字内）",
                 "rows": [{{"label": "最佳时段", "content": "（22字内）"}}, {{"label": "提醒", "content": "（22字内）"}}]}},
               {{"image_prompt": "点位2实景提示词", "name": "", "tag": "", "rows": [{{"label": "怎么玩", "content": ""}}, {{"label": "最佳时段", "content": ""}}]}},
               {{"image_prompt": "点位3实景提示词", "name": "", "tag": "", "rows": [{{"label": "最佳时段", "content": ""}}, {{"label": "提醒", "content": ""}}]}}
             ]}},
  "stay_hook": {{"image_prompt": "住宿卡顶部横幅提示词（本篇推荐住宿片区实景，无人物无文字）",
                 "title": "带娃住宿 · 规划师只看 3 点", "subtitle": "住哪片区一句话（15字内）",
                 "rules": [
                   {{"num": "①", "name": "动线", "desc": "为什么重要+怎么选（20字内）"}},
                   {{"num": "②", "name": "床", "desc": ""}},
                   {{"num": "③", "name": "退改", "desc": ""}}
                 ],
                 "cta": {{"line1": "这份是默认家庭节奏的说明（16字内）",
                          "line2": "评论区报：人数 / 天数 / 娃年龄",
                          "line3": "帮你重排一版（14字内）", "account": "关注 @ 行程规划旅行家"}}}},
  "narrations": ["镜头1旁白（对应封面：钩子开场，30-50字）",
                  "镜头2旁白（对应路线总表：三天概览，30-60字）",
                  "镜头3旁白（对应重点点位之一：展开讲透，30-60字）",
                  "镜头4旁白（对应点位卡：只讲图上时段要点，30-60字）",
                  "镜头5旁白（对应住宿钩子卡：三原则+服务钩子收尾，40-60字）"]
}}
```

narrative 型（整页底图金句杂志风，4 张卡 + 4 段分镜）：
```json
{{
  "note_type": "narrative",
  "cover": {{"image_prompt": "封面底图提示词（与正文情绪强相关的目的地场景，旅行摄影，无人物无文字）",
             "title": "封面大标题（18字内，含emoji，保留正文核心观点）", "subtitle": "副标题（15字内）",
             "pill": "角标短语（10字内，如「我的真实感受」）"}},
  "story_1": {{"image_prompt": "场景1底图提示词（正文第一个场景/论点对应画面，无人物无文字）",
               "title": "场景金句（16字内，从正文提炼）",
               "lines": ["叙事行1（22字内，正文原句或同义改写）", "叙事行2（22字内）", "叙事行3（22字内）"]}},
  "story_2": {{"image_prompt": "场景2底图提示词（正文第二个场景/论点对应画面，无人物无文字）",
               "title": "场景金句（16字内）",
               "lines": ["叙事行1（22字内）", "叙事行2（22字内）", "叙事行3（22字内）"]}},
  "ending": {{"image_prompt": "收尾底图提示词（开阔/有回味的场景，无人物无文字）",
              "title": "收尾观点（14字内）",
              "lines": ["升华行1（22字内）", "升华行2（22字内）"],
              "cta": {{"line1": "过渡句（16字内）", "line2": "评论区报：人数 / 天数 / 预算",
                       "line3": "帮你出定制行程（12字内）", "account": "关注 @ 行程规划旅行家"}}}},
  "narrations": ["镜头1旁白（对应封面：钩子开场，30-50字）",
                  "镜头2旁白（对应场景页1：讲透第一个场景，30-60字）",
                  "镜头3旁白（对应场景页2：讲透第二个场景，30-60字）",
                  "镜头4旁白（对应收尾卡：观点升华+服务钩子，40-60字）"]
}}
```"""


def plan_layout(llm_call: Callable[[str], str], rewrite_result: dict) -> dict:
    """LLM 把仿写稿编排成版式数据（先判类型，按类型校验）。返回 layout dict。"""
    prompt = LAYOUT_PROMPT.format(
        title=rewrite_result.get("title", ""),
        content=rewrite_result.get("content", ""),
        tags=" ".join(rewrite_result.get("tags", [])))
    raw = llm_call(prompt)
    m = re.search(r"```json\s*(\{.*?\})\s*```", raw, re.S) or re.search(r"(\{.*\})", raw, re.S)
    if not m:
        raise ValueError(f"版式编排输出无 JSON：{raw[:200]}")
    layout = json.loads(m.group(1))
    note_type = layout.get("note_type") or "itinerary"  # 旧版式数据无类型 → 默认行程
    if note_type == "narrative":
        for key in ("cover", "story_1", "story_2", "ending"):
            if key not in layout:
                raise ValueError(f"版式数据（narrative）缺 {key} 段")
        if len(layout.get("narrations") or []) != 4:
            raise ValueError("narrative 版式 narrations 必须为 4 段（图文同源分镜）")
    else:
        for key in ("cover", "itinerary", "spots", "stay_hook"):
            if key not in layout:
                raise ValueError(f"版式数据（itinerary）缺 {key} 段")
        if len(layout.get("narrations") or []) != 5:
            raise ValueError("itinerary 版式 narrations 必须为 5 段（图文同源分镜）")
    return layout


# ─── PIL 排版引擎（移植自 POC gen_images.py，通用部分） ─────────────────

class Renderer:
    def __init__(self, assets_dir: str):
        self.assets = Path(assets_dir)
        fonts = self.assets / "fonts"
        self.font_b = str(fonts / "NotoSansSC-Bold.otf")
        self.font_r = str(fonts / "NotoSansSC-Regular.otf")

    def F(self, size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(self.font_b if bold else self.font_r, size)

    def load_crop(self, path: str, w: int, h: int) -> Image.Image:
        im = Image.open(path).convert("RGB")
        sw, sh = im.size
        scale = max(w / sw, h / sh)
        im = im.resize((int(sw * scale + .5), int(sh * scale + .5)), Image.LANCZOS)
        x, y = (im.size[0] - w) // 2, (im.size[1] - h) // 2
        return im.crop((x, y, x + w, y + h))

    def paste_rounded(self, canvas, im, xy, radius=20):
        mask = Image.new("L", im.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, im.size[0] - 1, im.size[1] - 1], radius=radius, fill=255)
        canvas.paste(im, xy, mask)

    def paste_banner(self, canvas, path: str, height: int):
        canvas.paste(self.load_crop(path, W, height), (0, 0))
        d = ImageDraw.Draw(canvas, "RGBA")
        y0 = int(height * 0.62)
        for y in range(y0, height):
            t = (y - y0) / (height - y0)
            d.line([(0, y), (W, y)], fill=BG + (int(235 * t ** 1.1),))
        for y in range(240):
            a = int(130 * (1 - y / 240) ** 1.4)
            d.line([(0, y), (W, y)], fill=(12, 9, 6, a))

    def banner_text(self, d, xy, text, font, fill=CREAM):
        x, y = xy
        d.text((x + 4, y + 4), text, font=font, fill=(0, 0, 0, 170), anchor="la",
               stroke_width=2, stroke_fill=(0, 0, 0, 170))
        d.text((x, y), text, font=font, fill=fill, anchor="la",
               stroke_width=1, stroke_fill=(0, 0, 0, 80))

    def pill(self, d, x, y, text, font, fg, bg, padx=26):
        w = font.getlength(text) + padx * 2
        h = font.size + 24
        d.rounded_rectangle([x, y, x + w, y + h], radius=h / 2, fill=bg)
        d.text((x + padx, y + (h - font.size) / 2 - font.size * .12), text, font=font, fill=fg)
        return w

    def wrap(self, d, x, y, text, font, fill, max_w, line_gap=10, max_lines=99):
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

    # ── 4 张卡的版式（数据驱动） ──

    def gen_cover(self, layout: dict, bg_path: str, out: str):
        c = layout["cover"]
        img = Image.new("RGB", (W, H), BG)
        self.paste_banner(img, bg_path, 1656)  # 全幅底图
        d = ImageDraw.Draw(img, "RGBA")
        for y in range(1656):  # 下半部渐变压暗保可读
            t = max(0.0, (y - 700) / 956)
            d.line([(0, y), (W, y)], fill=(20, 14, 8, int(150 * t)))
        self.banner_text(d, (MARGIN, 1080), c.get("title", ""), self.F(88))
        self.banner_text(d, (MARGIN, 1230), c.get("subtitle", ""), self.F(50, False))
        # 角标/署名数据驱动（narrative 型可配「我的真实感受」等，缺省沿用攻略号文案）
        self.pill(d, MARGIN, 1360, c.get("pill", "保姆级 · 可直接抄"),
                  self.F(40, True), (255, 255, 255), MAPLE)
        d.text((W - MARGIN, 1392), c.get("account", "行程规划旅行家"), font=self.F(38, False),
               fill=CREAM, anchor="rm")
        img.save(out, quality=92)

    def _dip(self, d, y0, y1, a0, a1):
        """纵向渐变压暗带（y0→y1，透明度 a0→a1），整页底图上保文字可读。"""
        span = max(y1 - y0, 1)
        for y in range(y0, y1):
            t = (y - y0) / span
            d.line([(0, y), (W, y)], fill=(15, 10, 6, int(a0 + (a1 - a0) * t)))

    def gen_story(self, page: dict, bg_path: str, out: str):
        """叙事场景页（narrative）：整页底图 + 金句标题 + 叙事行，杂志风。"""
        img = Image.new("RGB", (W, H), BG)
        self.paste_banner(img, bg_path, 1656)
        d = ImageDraw.Draw(img, "RGBA")
        self._dip(d, 0, 520, 175, 0)        # 顶部压暗（金句区）
        self._dip(d, 960, 1656, 0, 165)     # 底部压暗（叙事行区）
        self.banner_text(d, (MARGIN, 150), page.get("title", ""), self.F(76))
        y = 1030
        for ln in page.get("lines", [])[:3]:
            self.banner_text(d, (MARGIN, y), ln, self.F(46, False))
            y += 96
        img.save(out, quality=92)

    def gen_ending(self, page: dict, bg_path: str, out: str):
        """收尾卡（narrative）：整页底图 + 升华观点 + CTA 服务钩子。"""
        img = Image.new("RGB", (W, H), BG)
        self.paste_banner(img, bg_path, 1656)
        d = ImageDraw.Draw(img, "RGBA")
        self._dip(d, 0, 700, 165, 0)
        self.banner_text(d, (MARGIN, 130), page.get("title", ""), self.F(72))
        y = 330
        for ln in page.get("lines", [])[:2]:
            self.banner_text(d, (MARGIN, y), ln, self.F(48, False))
            y += 92
        cta = page.get("cta", {})
        d.rounded_rectangle([MARGIN, 760, W - MARGIN, 1180], radius=32, fill=MAPLE)
        d.text((W / 2, 830), cta.get("line1", "每次出发都值得认真规划"), font=self.F(42, False),
               fill=(255, 226, 214), anchor="mm")
        d.text((W / 2, 910), cta.get("line2", "评论区报：人数 / 天数 / 预算"),
               font=self.F(52), fill=(255, 255, 255), anchor="mm")
        d.text((W / 2, 982), cta.get("line3", "帮你出定制行程"), font=self.F(46, False),
               fill=(255, 255, 255), anchor="mm")
        d.rounded_rectangle([W / 2 - 190, 1030, W / 2 + 190, 1090], radius=30, fill=(255, 255, 255, 40))
        d.text((W / 2, 1060), cta.get("account", "关注 @ 行程规划旅行家"),
               font=self.F(38), fill=(255, 255, 255), anchor="mm")
        d.text((W / 2, 1580), "· 出发这件事，永远不亏 ·", font=self.F(36), fill=AMBER, anchor="mm")
        img.save(out, quality=92)

    def gen_itinerary(self, layout: dict, banner_path: str, out: str):
        it = layout["itinerary"]
        img = Image.new("RGB", (W, H), BG)
        self.paste_banner(img, banner_path, 580)
        d = ImageDraw.Draw(img, "RGBA")
        self.banner_text(d, (MARGIN, 96), it.get("title", "3 日路线总表"), self.F(66))
        self.banner_text(d, (MARGIN, 186), it.get("subtitle", ""), self.F(42, False))
        y = 616
        for day in it.get("days", [])[:3]:
            d.rounded_rectangle([MARGIN, y, W - MARGIN, y + 306], radius=26,
                                fill=CARD, outline=LINE, width=2)
            self.pill(d, MARGIN + 34, y + 28, day.get("tag", ""), self.F(38), (255, 255, 255), MAPLE)
            d.text((MARGIN + 200, y + 32), day.get("theme", ""), font=self.F(52), fill=DARK)
            self.wrap(d, MARGIN + 34, y + 118, day.get("content", ""), self.F(42, False),
                      DARK, W - MARGIN * 2 - 68, max_lines=2)
            d.text((MARGIN + 34, y + 248), "· ", font=self.F(36), fill=GOLD)
            d.text((MARGIN + 58, y + 246), day.get("traffic", ""), font=self.F(36), fill=GRAY)
            y += 324
        d.rounded_rectangle([MARGIN, 1590, W - MARGIN, 1650], radius=20, fill=(240, 247, 228))
        d.text((W / 2, 1620), it.get("footer", ""), font=self.F(40), fill=LEAF, anchor="mm")
        img.save(out, quality=92)

    def gen_spots(self, layout: dict, img_paths: List[str], out: str):
        sp = layout["spots"]
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img, "RGBA")
        d.rounded_rectangle([MARGIN, 66, W - MARGIN, 170], radius=26, fill=AMBER)
        d.text((W / 2, 118), sp.get("title", "重点点位 · 最佳时段"),
               font=self.F(56), fill=(255, 255, 255), anchor="mm")
        y = 204
        for i, spot in enumerate(sp.get("spots", [])[:3]):
            d.rounded_rectangle([MARGIN, y, W - MARGIN, y + 464], radius=26,
                                fill=CARD, outline=LINE, width=2)
            if i < len(img_paths):
                self.paste_rounded(img, self.load_crop(img_paths[i], 382, 382),
                                   (MARGIN + 34, y + 41), radius=20)
            tx = MARGIN + 456
            name = spot.get("name", "")
            d.text((tx, y + 38), name, font=self.F(52), fill=DARK)
            self.pill(d, tx + self.F(52).getlength(name) + 22, y + 42,
                      spot.get("tag", ""), self.F(34, True), AMBER, (253, 236, 213))
            ry = y + 130
            for row in spot.get("rows", [])[:2]:
                d.text((tx, ry), row.get("label", ""), font=self.F(36), fill=GOLD)
                ry = self.wrap(d, tx, ry + 48, row.get("content", ""), self.F(40, False),
                               DARK, W - MARGIN - 34 - tx, max_lines=2) + 20
            y += 478
        img.save(out, quality=92)

    def gen_stay_hook(self, layout: dict, banner_path: str, out: str):
        sh = layout["stay_hook"]
        img = Image.new("RGB", (W, H), BG)
        self.paste_banner(img, banner_path, 560)
        d = ImageDraw.Draw(img, "RGBA")
        self.banner_text(d, (MARGIN, 92), sh.get("title", ""), self.F(62))
        self.banner_text(d, (MARGIN, 180), sh.get("subtitle", ""), self.F(42, False))
        cw = (W - MARGIN * 2 - 48) // 3
        y = 620
        for i, rule in enumerate(sh.get("rules", [])[:3]):
            x = MARGIN + i * (cw + 24)
            d.rounded_rectangle([x, y, x + cw, y + 460], radius=26,
                                fill=CARD, outline=LINE, width=2)
            d.ellipse([x + cw / 2 - 44, y + 36, x + cw / 2 + 44, y + 124], fill=(253, 236, 213))
            d.text((x + cw / 2, y + 80), rule.get("num", ""), font=self.F(46), fill=AMBER, anchor="mm")
            d.text((x + cw / 2, y + 152), rule.get("name", ""), font=self.F(48), fill=DARK, anchor="mm")
            self.wrap(d, x + 34, y + 232, rule.get("desc", ""), self.F(36, False), GRAY, cw - 68)
        cta = sh.get("cta", {})
        d.rounded_rectangle([MARGIN, 1120, W - MARGIN, 1500], radius=32, fill=MAPLE)
        d.text((W / 2, 1190), cta.get("line1", ""), font=self.F(42, False), fill=(255, 226, 214), anchor="mm")
        d.text((W / 2, 1268), cta.get("line2", "评论区报：人数 / 天数 / 娃年龄"),
               font=self.F(52), fill=(255, 255, 255), anchor="mm")
        d.text((W / 2, 1340), cta.get("line3", ""), font=self.F(46, False), fill=(255, 255, 255), anchor="mm")
        d.rounded_rectangle([W / 2 - 190, 1390, W / 2 + 190, 1450], radius=30, fill=(255, 255, 255, 40))
        d.text((W / 2, 1420), cta.get("account", "关注 @ 行程规划旅行家"),
               font=self.F(38), fill=(255, 255, 255), anchor="mm")
        d.text((W / 2, 1580), "· 排程不踩坑 ·", font=self.F(36), fill=AMBER, anchor="mm")
        img.save(out, quality=92)


# ─── 第二步：底图生成 + 渲染 ────────────────────────────────────────────

def generate_pack(layout: dict, gen: ImageGenRouter, out_dir: str,
                  assets_dir: str, base_dir: str = "") -> Dict[str, List[str]]:
    """生成 4 张图文卡片。返回 {"cards": [4 卡路径], "video_frames": [帧路径]}。

    video_frames 与 narrations 严格等长：
      itinerary 型 5 帧：封面卡 / 路线卡 / 点位底图 / 点位卡 / 住宿卡（继承 POC 分镜）
      narrative 型 4 帧：封面卡 / 场景1 / 场景2 / 收尾卡（4 卡即 4 镜头）"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    raw = out / "raw"
    raw.mkdir(exist_ok=True)
    r = Renderer(assets_dir)
    note_type = layout.get("note_type") or "itinerary"  # 旧数据默认行程（向后兼容）

    if note_type == "narrative":
        jobs = {name: (layout[name].get("image_prompt", ""), "portrait")
                for name in ("cover", "story_1", "story_2", "ending")}
        paths = {}
        for name, (prompt, orient) in jobs.items():
            if not prompt:
                raise ValueError(f"版式数据缺底图提示词: {name}")
            paths[name], _ = gen.generate(prompt, str(raw / f"{name}.jpg"), orient)
        r.gen_cover(layout, paths["cover"], str(out / "1_cover.jpg"))
        r.gen_story(layout["story_1"], paths["story_1"], str(out / "2_story.jpg"))
        r.gen_story(layout["story_2"], paths["story_2"], str(out / "3_story.jpg"))
        r.gen_ending(layout["ending"], paths["ending"], str(out / "4_ending.jpg"))
        cards = [str(out / n) for n in
                 ("1_cover.jpg", "2_story.jpg", "3_story.jpg", "4_ending.jpg")]
        return {"cards": cards, "video_frames": cards}

    # ── itinerary 型（原版式） ──
    jobs = {
        "cover_bg": (layout["cover"].get("image_prompt", ""), "portrait"),
        "it_banner": (layout["itinerary"].get("image_prompt", ""), "landscape"),
        "st_banner": (layout["stay_hook"].get("image_prompt", ""), "landscape"),
    }
    for i, spot in enumerate(layout["spots"].get("spots", [])[:3]):
        jobs[f"spot_{i}"] = (spot.get("image_prompt", ""), "portrait")
    paths = {}
    for name, (prompt, orient) in jobs.items():
        if not prompt:
            raise ValueError(f"版式数据缺底图提示词: {name}")
        paths[name], _ = gen.generate(prompt, str(raw / f"{name}.jpg"), orient)

    r.gen_cover(layout, paths["cover_bg"], str(out / "1_cover.jpg"))
    r.gen_itinerary(layout, paths["it_banner"], str(out / "2_itinerary.jpg"))
    r.gen_spots(layout, [paths[f"spot_{i}"] for i in range(3)], str(out / "3_spots.jpg"))
    r.gen_stay_hook(layout, paths["st_banner"], str(out / "4_stay_hook.jpg"))
    cards = [str(out / n) for n in ("1_cover.jpg", "2_itinerary.jpg", "3_spots.jpg", "4_stay_hook.jpg")]
    video_frames = [cards[0], cards[1], paths["spot_0"], cards[2], cards[3]]
    return {"cards": cards, "video_frames": video_frames}
