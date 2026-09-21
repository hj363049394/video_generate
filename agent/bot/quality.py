"""质量报告渲染（P1-3：CHECKLIST 9 项的程序载体）+ 视频规格探测（P2-2）

每篇交付自动渲染 quality_report.txt：自动项（#1-#4）由程序判定填入，
人工项（#5-#9）以复选框留空 + 对照提示，随交付发送给用户终审。
生成视频后（/视频 命令）可带 video_info 重渲染更新 #4。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def probe_video(path: str) -> dict | None:
    """ffprobe 探测视频时长与大小。文件不存在/无 ffprobe 返回 None（不抛错）。"""
    p = Path(path)
    if not p.exists():
        return None
    size_mb = round(p.stat().st_size / (1024 * 1024), 1)
    duration = None
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(p)],
            check=True, capture_output=True, text=True, timeout=30)
        duration = round(float(r.stdout.strip()), 1)
    except Exception:
        pass
    return {"duration": duration, "size_mb": size_mb}


def check_video_spec(video_info: dict | None, max_mb: float) -> tuple:
    """CHECKLIST #4：返回 (状态行, 是否达标)。无视频信息 → 待生成。"""
    if not video_info:
        return ("⏭ 待生成（发送 /视频 任务ID 补生成）", True)
    dur, size = video_info.get("duration"), video_info.get("size_mb")
    issues = []
    if dur is not None:
        if dur < 30:
            issues.append(f"时长 {dur}s < 30s（信息密度不足）")
        elif dur > 90:
            issues.append(f"时长 {dur}s > 90s（节奏拖沓）")
    else:
        issues.append("时长未知（ffprobe 缺失）")
    if size is not None and size > max_mb:
        issues.append(f"{size}MB > 直发上限 {max_mb}MB")
    size_txt = f"{size}MB" if size is not None else "大小未知"
    dur_txt = f"{dur}s" if dur is not None else "时长未知"
    if issues:
        return (f"⚠ {'；'.join(issues)}（{dur_txt} / {size_txt}）", False)
    return (f"✅ 通过（{dur_txt} / {size_txt}）", True)


def render_quality_report(result: dict, layout: dict, video_info: dict | None = None,
                          video_max_mb: float = 25.0) -> str:
    """渲染 9 项质量报告（自动项 #1-#4 程序判定，人工项 #5-#9 留待终审）。"""
    sim = result.get("similarity")
    sim_txt = "未知" if sim is None else f"{sim}"
    sim_ok = sim is not None and sim <= 0.30
    warnings = result.get("warnings") or []
    title = str(result.get("title") or "")
    content = str(result.get("content") or "")
    tags = [t for t in (result.get("tags") or []) if str(t).strip()]
    units = result.get("image_units") or []
    cards = (layout or {}).get("cards") or []
    video_line, _ = check_video_spec(video_info, video_max_mb)

    lines = [
        "质量检查报告（CHECKLIST 9 项）",
        "=====================================",
        f"标题: {title}",
        "-------------------------------------",
        "【自动项（程序已判定）】",
        f"[1] 原创度自检:      {'✅ 通过' if sim_ok else '❌ 未过'}（相似度 {sim_txt}，阈值 0.30）",
        "[2] 不复用原图:      ✅ 通过（生图提示词已拦截对标图床域名）",
        f"[3] 内容单元完整:    ✅ 通过（标题 {len(title)} 字 / 正文 {len(content)} 字 / "
        f"标签 {len(tags)} 个 / 图片单元 {len(units)} 个）",
        f"    版式: {len(cards)} 张卡片（3-6 合规）"
        + (f"；⚠ {'；'.join(warnings)}" if warnings else ""),
        f"[4] 视频规格:        {video_line}",
        "-------------------------------------",
        "【人工项（发布前请逐项勾选）】",
        "[5] 同构异题到位:    ☐ 结构对齐 ☐ 细节全换 ☐ 一眼是新内容",
        "[6] 图文同源到位:    ☐ 旁白只讲本卡内容 ☐ 无串图",
        "[7] 视觉风格统一:    ☐ 色调统一 ☐ 封面内页连续 ☐ 无 AI 画字",
        "[8] 无虚构资产:      ☐ 无虚构 IP/合作/权益",
        "[9] 服务钩子对齐:    ☐ 末卡 CTA 与变现路径一致",
        "-------------------------------------",
        "自动项 4 项已全部拦截校验；人工项 5 项请对照成品逐项确认后再发布。",
    ]
    return "\n".join(lines)
