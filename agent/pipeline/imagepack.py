"""xhs-imagepack · 图文卡片生成 SKILL（Phase 1.5）

拆解驱动卡片 DSL（2026-09-21 v1.2：版式逐项对标爆款，替代固定模板）：
  1. plan_layout(llm_call, rewrite_result, analysis) -> layout dict
     输入仿写稿 + 拆解引擎的结构规格（note_analyze 产出），LLM 生成卡片 DSL：
     第 N 张卡对标爆款第 N 张图卡的 kind/文字排版/风格——版式跟拆解走，不套模板
  2. generate_pack(layout, gen, out_dir, assets_dir) -> [图片路径]
     底图走 imagegen 双通道；渲染器只实现通用块类型（list/rows/lines/cta）
     + 两种图片模式（full 整页底图 / banner 顶部横幅），任意结构自由组合

产出 3-6 张 1242x1656（3:4）卡片 + 等长分镜 narrations（视频图文同源）。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont

from pipeline.imagegen import ImageGenRouter
from pipeline.promptkit import load_prompt

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

BLOCK_TYPES = ("list", "rows", "lines", "cta")
IMAGE_MODES = ("full", "banner")
# 人设服务钩子（最后一张卡缺 cta 块时自动补，保持旅行家人设）
DEFAULT_CTA = {"line1": "每次出发都值得认真规划",
               "line2": "评论区报：人数 / 天数 / 预算",
               "line3": "帮你出定制行程",
               "account": "关注 @ 行程规划旅行家"}


# ─── 第一步：LLM 卡片 DSL 编排（拆解驱动） ──────────────────────────────

# 提示词外置（v1.2.1）：agent/prompts/layout.md——调提示词改文件，不改代码
LAYOUT_PROMPT = load_prompt("layout")


def _analysis_section(analysis: Optional[dict]) -> str:
    """拆解结果 → prompt 的对标基准段。无拆解数据时给降级说明。"""
    if not analysis:
        return ("（拆解数据缺失）无对标基准——从仿写稿正文分段推断图卡结构："
                "正文每个自然段/emoji 分段 ≈ 一张卡；叙事型段落用 full+lines，"
                "清单型段落用 banner+list/rows。")
    lines = []
    for i, im in enumerate(analysis.get("image_structure") or []):
        lines.append(
            f"图 {im.get('idx', i + 1)}：kind={im.get('kind', '')}｜role={im.get('role', '')}｜"
            f"{im.get('desc', '')}｜文字排版：{im.get('text_layout', '')}｜风格：{im.get('style', '')}")
    lines.append(f"整体调性：{analysis.get('style_summary', '')}")
    kind_map = ("kind → DSL 映射：full_photo_cover→full+title/subtitle/pill；"
                "list_card→banner+list；rows_card→banner+rows；"
                "lines_quote→full+lines；mixed→按内容择优组合")
    return "\n".join(lines) + "\n" + kind_map


def plan_layout(llm_call: Callable[[str], str], rewrite_result: dict,
                analysis: Optional[dict] = None) -> dict:
    """LLM 把仿写稿 + 拆解结构编排成卡片 DSL。返回 layout dict。

    校验：cards 3-6 张、image_mode/blocks 类型合法、narrations 等长、
    末卡含 cta（缺则自动补人设钩子）。
    """
    prompt = LAYOUT_PROMPT.format(
        title=rewrite_result.get("title", ""),
        content=rewrite_result.get("content", ""),
        tags=" ".join(rewrite_result.get("tags", [])),
        analysis_section=_analysis_section(analysis))
    raw = llm_call(prompt)
    m = re.search(r"```json\s*(\{.*?\})\s*```", raw, re.S) or re.search(r"(\{.*\})", raw, re.S)
    if not m:
        raise ValueError(f"版式编排输出无 JSON：{raw[:200]}")
    layout = json.loads(m.group(1))

    cards = layout.get("cards") or []
    if not 3 <= len(cards) <= 6:
        raise ValueError(f"cards 必须为 3-6 张，实际 {len(cards)}")
    names = set()
    for c in cards:
        name = str(c.get("name") or "").strip()
        if not name or name in names:
            raise ValueError(f"卡 name 非法或重复：{name!r}")
        names.add(name)
        if not c.get("image_prompt"):
            raise ValueError(f"卡 {name} 缺 image_prompt")
        if c.get("image_mode") not in IMAGE_MODES:
            raise ValueError(f"卡 {name} image_mode 非法：{c.get('image_mode')}")
        for b in c.get("blocks") or []:
            if b.get("type") not in BLOCK_TYPES:
                raise ValueError(f"卡 {name} 块类型非法：{b.get('type')}")
    narrations = layout.get("narrations") or []
    if len(narrations) != len(cards):
        raise ValueError(f"narrations 必须与 cards 等长（{len(cards)}），实际 {len(narrations)}")
    # 末卡缺 cta → 自动补人设钩子（软校验，不因 LLM 漏输出而失败）
    last = cards[-1]
    if not any(b.get("type") == "cta" for b in last.get("blocks") or []):
        last.setdefault("blocks", []).append({"type": "cta", **DEFAULT_CTA})
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

    # ── 卡片 DSL 渲染（通用：full / banner × list / rows / lines / cta） ──

    def _dip(self, d, y0, y1, a0, a1):
        """纵向渐变压暗带（y0→y1，透明度 a0→a1），整页底图上保文字可读。"""
        span = max(y1 - y0, 1)
        for y in range(y0, y1):
            t = (y - y0) / span
            d.line([(0, y), (W, y)], fill=(15, 10, 6, int(a0 + (a1 - a0) * t)))

    def render_card(self, spec: dict, bg_path: str,
                    row_imgs: List[Optional[str]], out: str) -> None:
        """渲染一张卡片 DSL。

        row_imgs：rows 块行级小图路径（按行序展平，无图的行传 None）。
        full 模式只消费 lines/cta 块；banner 模式消费全部块类型。
        """
        if spec.get("image_mode") == "full":
            self._render_full(spec, bg_path, out)
        else:
            self._render_banner(spec, bg_path, row_imgs, out)

    def _render_full(self, spec: dict, bg_path: str, out: str) -> None:
        """整页底图卡：pill 角标 + 顶部 title/subtitle + 中部 cta 色块 + 底部 lines。"""
        img = Image.new("RGB", (W, H), BG)
        self.paste_banner(img, bg_path, H)
        d = ImageDraw.Draw(img, "RGBA")
        blocks = spec.get("blocks") or []
        cta = next((b for b in blocks if b.get("type") == "cta"), None)

        self._dip(d, 0, 560, 175, 0)  # 顶部压暗（标题区）
        if spec.get("pill"):
            self.pill(d, MARGIN, 88, spec["pill"], self.F(38), (255, 255, 255), MAPLE)
        ty = 160
        if spec.get("title"):
            self.banner_text(d, (MARGIN, ty), spec["title"], self.F(78))
            ty += 134
        if spec.get("subtitle"):
            self.banner_text(d, (MARGIN, ty), spec["subtitle"], self.F(46, False))

        if cta:  # 中部服务钩子色块（原 gen_ending 样式）
            self._dip(d, 700, 1190, 0, 120)
            y0 = 760
            d.rounded_rectangle([MARGIN, y0, W - MARGIN, y0 + 420], radius=32, fill=MAPLE)
            d.text((W / 2, y0 + 70), cta.get("line1", ""), font=self.F(42, False),
                   fill=(255, 226, 214), anchor="mm")
            d.text((W / 2, y0 + 148), cta.get("line2", ""), font=self.F(52),
                   fill=(255, 255, 255), anchor="mm")
            d.text((W / 2, y0 + 220), cta.get("line3", ""), font=self.F(46, False),
                   fill=(255, 255, 255), anchor="mm")
            d.rounded_rectangle([W / 2 - 190, y0 + 270, W / 2 + 190, y0 + 330],
                                radius=30, fill=(255, 255, 255, 40))
            d.text((W / 2, y0 + 300), cta.get("account", ""), font=self.F(38),
                   fill=(255, 255, 255), anchor="mm")

        ly = 1240 if cta else 1030  # 底部叙事行区
        lines_items = [ln for b in blocks if b.get("type") == "lines"
                       for ln in (b.get("items") or [])][:3]
        if lines_items:
            self._dip(d, max(ly - 70, 0), H, 0, 165)
            for ln in lines_items:
                self.banner_text(d, (MARGIN, ly), ln, self.F(44, False))
                ly += 92
        img.save(out, quality=92)

    def _render_banner(self, spec: dict, bg_path: str,
                       row_imgs: List[Optional[str]], out: str) -> None:
        """横幅卡：顶部 560 底图横幅 + title/subtitle + 内容块垂直排布 + 底部 cta。"""
        img = Image.new("RGB", (W, H), BG)
        self.paste_banner(img, bg_path, 560)
        d = ImageDraw.Draw(img, "RGBA")
        self.banner_text(d, (MARGIN, 92), spec.get("title", ""), self.F(62))
        if spec.get("subtitle"):
            self.banner_text(d, (MARGIN, 180), spec["subtitle"], self.F(42, False))

        y, ri = 620, 0  # ri：row_imgs 游标
        for b in spec.get("blocks") or []:
            t = b.get("type")
            if t == "list":
                y = self._render_list(d, b, y)
            elif t == "rows":
                n_img = sum(1 for it in (b.get("items") or []) if it.get("image_prompt"))
                imgs = row_imgs[ri:ri + n_img] if n_img else []
                ri += n_img
                y = self._render_rows(img, d, b, y, imgs)
            elif t == "lines":
                y = self._render_lines(d, b, y)
            y += 24

        cta = next((b for b in spec.get("blocks") or [] if b.get("type") == "cta"), None)
        if cta:  # 底部服务钩子（原 gen_stay_hook 样式），跟在内容后但不低于 1080
            yc = min(max(y, 1080), 1140)
            d.rounded_rectangle([MARGIN, yc, W - MARGIN, yc + 380], radius=32, fill=MAPLE)
            d.text((W / 2, yc + 70), cta.get("line1", ""), font=self.F(42, False),
                   fill=(255, 226, 214), anchor="mm")
            d.text((W / 2, yc + 148), cta.get("line2", ""), font=self.F(52),
                   fill=(255, 255, 255), anchor="mm")
            d.text((W / 2, yc + 220), cta.get("line3", ""), font=self.F(46, False),
                   fill=(255, 255, 255), anchor="mm")
            d.rounded_rectangle([W / 2 - 190, yc + 270, W / 2 + 190, yc + 330],
                                radius=30, fill=(255, 255, 255, 40))
            d.text((W / 2, yc + 300), cta.get("account", ""), font=self.F(38),
                   fill=(255, 255, 255), anchor="mm")
        d.text((W / 2, 1608), "· 出发这件事，永远不亏 ·", font=self.F(34),
               fill=AMBER, anchor="mm")
        img.save(out, quality=92)

    def _render_list(self, d, block: dict, y: int) -> int:
        """条目清单块：白卡 + (tag pill + label + text) × N。返回下一块起始 y。"""
        items = [it for it in (block.get("items") or []) if it][:4]
        if not items:
            return y
        ch = 30 + len(items) * 190 + 16
        d.rounded_rectangle([MARGIN, y, W - MARGIN, y + ch], radius=26,
                            fill=CARD, outline=LINE, width=2)
        iy = y + 24
        for it in items:
            pw = self.pill(d, MARGIN + 34, iy, str(it.get("tag", "·")), self.F(36),
                           (255, 255, 255), MAPLE)
            d.text((MARGIN + 34 + pw + 24, iy + 2), it.get("label", ""),
                   font=self.F(50), fill=DARK)
            self.wrap(d, MARGIN + 34, iy + 84, it.get("text", ""), self.F(40, False),
                     DARK, W - MARGIN * 2 - 68, max_lines=2)
            iy += 190
        return y + ch + 20

    def _render_rows(self, canvas, d, block: dict, y: int,
                     imgs: List[Optional[str]]) -> int:
        """信息行块：白卡；有小图的行渲染 382px 图文行，无图渲染纯文字行。"""
        items = [it for it in (block.get("items") or []) if it][:4]
        if not items:
            return y
        heights = [464 if (imgs and k < len(imgs) and imgs[k]) else 140
                   for k in range(len(items))]
        ch = 30 + sum(heights) + 16
        d.rounded_rectangle([MARGIN, y, W - MARGIN, y + ch], radius=26,
                            fill=CARD, outline=LINE, width=2)
        iy = y + 24
        for k, it in enumerate(items):
            img_path = imgs[k] if (imgs and k < len(imgs)) else None
            if img_path:  # 图文行（原 gen_spots 样式）
                self.paste_rounded(canvas, self.load_crop(img_path, 382, 382),
                                   (MARGIN + 34, iy + 40), radius=20)
                tx = MARGIN + 456
                d.text((tx, iy + 36), it.get("label", ""), font=self.F(48), fill=DARK)
                self.wrap(d, tx, iy + 108, it.get("text", ""), self.F(38, False),
                          DARK, W - MARGIN - 34 - tx, max_lines=3)
            else:  # 纯文字行
                d.text((MARGIN + 34, iy), it.get("label", ""), font=self.F(38), fill=GOLD)
                self.wrap(d, MARGIN + 34, iy + 52, it.get("text", ""), self.F(40, False),
                          DARK, W - MARGIN * 2 - 68, max_lines=2)
            iy += heights[k]
        return y + ch + 20

    def _render_lines(self, d, block: dict, y: int) -> int:
        """金句/叙事行块：无卡纯文字（banner 模式下）。"""
        for ln in (block.get("items") or [])[:3]:
            d.text((MARGIN, y), ln, font=self.F(44), fill=DARK)
            y += 92
        return y


# ─── 第二步：底图生成 + 渲染 ────────────────────────────────────────────

def generate_pack(layout: dict, gen: ImageGenRouter, out_dir: str,
                  assets_dir: str, base_dir: str = "") -> Dict[str, List[str]]:
    """按卡片 DSL 生成 3-6 张图文卡片。

    返回 {"cards": [卡路径], "video_frames": [帧路径]}——cards 即 video_frames
    （narrations 与 cards 等长，由 plan_layout 校验保证，视频图文同源）。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    raw = out / "raw"
    raw.mkdir(exist_ok=True)
    r = Renderer(assets_dir)
    outs: List[str] = []

    for i, spec in enumerate(layout["cards"]):
        name = spec["name"]
        # ① 卡级底图：full 竖版 / banner 横幅
        orient = "portrait" if spec["image_mode"] == "full" else "landscape"
        bg, _ = gen.generate(spec["image_prompt"], str(raw / f"{name}.jpg"), orient)
        # ② rows 块行级小图（图文行）
        row_imgs: List[Optional[str]] = []
        for b in spec.get("blocks") or []:
            if b.get("type") != "rows":
                continue
            for it in b.get("items") or []:
                ip = it.get("image_prompt")
                if ip:
                    p, _ = gen.generate(ip, str(raw / f"{name}_row{len(row_imgs)}.jpg"),
                                        "portrait")
                    row_imgs.append(p)
                else:
                    row_imgs.append(None)
        # ③ 渲染（文件名 {序号}_{name}.jpg，序号保证图集顺序）
        dst = str(out / f"{i + 1}_{name}.jpg")
        r.render_card(spec, bg, row_imgs, dst)
        outs.append(dst)
    return {"cards": outs, "video_frames": outs}
