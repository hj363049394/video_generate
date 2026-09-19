"""Router：指令解析 · 确认卡点 · 多用户会话 · 任务队列 · 推送补发

设计文档 v1.1 §3.5（指令集）、§3.6（多用户隔离）、§3.4（推送降级与补发）。
指令解析：正则优先；LLM 意图兜底属 Phase 1.5（TODO）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

from bot.base import Intent, TriggerAdapter
from bot.deliver import deliver_note, deliver_video
from pipeline import rewrite as rewrite_mod
from pipeline.imagegen import ImageGenRouter
from pipeline import radar as radar_mod
from pipeline import imagepack
from pipeline import video as video_mod

logger = logging.getLogger("bot.router")

# 用户友好通道名 → provider 注册名
GEN_ALIAS = {"ark": "ark", "火山": "ark", "gpt": "redfox_gpt", "红狐gpt": "redfox_gpt",
             "doubao": "redfox_doubao", "红狐豆包": "redfox_doubao"}

HELP_TEXT = """【小红书仿写助手 · 指令】
/选题 —— 查看当日选题清单（Top 5）
/确认 N 或 仿写第N条 —— 确认选题，触发生产线
/状态 —— 查询生产进度
/换角度 N 描述 —— 调整第 N 条的仿写角度
/生图通道 ark|gpt|doubao —— 切换生图通道（ark=火山Seedream，gpt=红狐GPT-Image-2，doubao=红狐豆包）
/生图 描述文字 —— 直接生一张竖图（通道实测用）
粘贴小红书笔记链接 —— 手动输入对标（绕过雷达）
/help —— 本帮助"""

TASK_STAGES = ["queued", "rewriting", "imaging", "delivered", "failed"]


class Router:
    def __init__(self, adapter: TriggerAdapter, gen: ImageGenRouter, config: dict, base_dir: str):
        self.adapter = adapter
        self.gen = gen
        self.config = config or {}
        self.base = Path(base_dir)
        # v1.1：多 Bot 隔离——bot_id 从 adapter.bot_id 取（单 Bot 默认 "default"）
        # 数据库 / 产物路径均按 bot_id 分层，为 P2 多进程多 Bot 铺路
        self.bot_id = getattr(adapter, "bot_id", "default")
        self.workspace = self.base / "workspace" / self.bot_id
        self.workspace.mkdir(parents=True, exist_ok=True)
        # 排版/TTS/视频的共享资产（字体/BGM），默认指向 POC 资产目录
        self.assets_dir = str(Path(self.config.get("pipeline", {}).get(
            "assets_dir") or (self.base.parent / "poc" / "production" / "assets")))
        self.video_max_mb = float((self.config.get("deliver") or {}).get("video_max_mb", 25))
        # SQLite 按 bot_id 分文件，物理隔离
        db_path = self.base / f"state.{self.bot_id}.db"
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute("""CREATE TABLE IF NOT EXISTS tasks(
            id TEXT PRIMARY KEY, uid TEXT, note_id TEXT, title TEXT,
            status TEXT, detail TEXT, created REAL, updated REAL)""")
        self._db.execute("""CREATE TABLE IF NOT EXISTS pending_push(
            id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, text TEXT, created REAL)""")
        self._db.commit()

    # ─── 主入口（Adapter 回调） ─────────────────────────────────────────

    async def handle(self, intent: Intent) -> None:
        try:
            await self._flush_pending(intent.user_id)
            await self._dispatch(intent)
        except Exception:
            logger.exception("处理消息失败 uid=%s", intent.user_id)
            await self._safe_send(intent.user_id, "处理出错，请稍后重试或 /help 查看用法。")

    async def _dispatch(self, intent: Intent) -> None:
        uid, text = intent.user_id, intent.text.strip()
        if not text:
            return
        if m := re.match(r"^/?选题$|^今天有什么选题|^/?雷达$", text):
            await self._cmd_topics(uid)
        elif m := re.match(r"^/?确认\s*(\d+)$|^仿写第\s*(\d+)\s*条", text):
            n = int(m.group(1) or m.group(2))
            await self._cmd_confirm(uid, n)
        elif re.match(r"^/?状态$", text):
            await self._cmd_status(uid)
        elif m := re.match(r"^/?换角度\s*(\d+)\s+(.+)", text):
            await self._safe_send(uid, f"已记录第 {m.group(1)} 条角度调整：{m.group(2)}（Phase 1.5 接入语义重评）")
        elif m := re.match(r"^/?生图通道\s*(\S+)", text):
            await self._cmd_switch_gen(uid, m.group(1))
        elif m := re.match(r"^/?生图\s+(.+)", text, re.S):
            await self._cmd_gen(uid, m.group(1).strip())
        elif re.search(r"xhslink\.com|xiaohongshu\.com", text):
            await self._cmd_manual_note(uid, text)
        elif re.match(r"^/?help$|^帮助$|^你好$|^hi$", text.lower()):
            await self._safe_send(uid, HELP_TEXT)
        else:
            # TODO(Phase 1.5)：LLM 意图解析兜底；当前给帮助
            await self._safe_send(uid, "暂时没听懂。试试这些指令：\n" + HELP_TEXT)

    # ─── 指令实现 ───────────────────────────────────────────────────────

    async def _cmd_topics(self, uid: str) -> None:
        path = radar_mod.latest_topic_list(self.workspace)
        if not path:
            await self._safe_send(
                uid, "还没有当日选题清单。管理员可运行：python3 agent/main.py --radar-now（立即抓取并推送）")
            return
        await self._safe_send(uid, radar_mod.format_topic_list(path, top=5))

    async def _cmd_confirm(self, uid: str, n: int) -> None:
        path = radar_mod.latest_topic_list(self.workspace)
        if not path:
            await self._safe_send(uid, "当日无选题清单，先 /选题 查看或等待雷达推送。")
            return
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        topics = data.get("topic_list") or data.get("passed") or []
        if n < 1 or n > len(topics):
            await self._safe_send(uid, f"序号超范围（1-{len(topics)}），/选题 重新查看。")
            return
        topic = topics[n - 1]
        task_id = uuid.uuid4().hex[:8]
        self._db.execute(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)",
            (task_id, uid, topic.get("note_id", ""), topic.get("title", ""),
             "queued", json.dumps(topic, ensure_ascii=False), time.time(), time.time()))
        self._db.commit()
        await self._safe_send(uid, f"已确认选题 #{n}：{topic.get('title', '')[:30]}\n"
                                   f"任务 {task_id} 已入队，/状态 查看进度。")
        asyncio.create_task(self._run_task(uid, task_id))

    async def _cmd_status(self, uid: str) -> None:
        rows = self._db.execute(
            "SELECT id, title, status FROM tasks WHERE uid=? ORDER BY created DESC LIMIT 5",
            (uid,)).fetchall()
        if not rows:
            await self._safe_send(uid, "暂无任务。/选题 选一条，或直接粘贴笔记链接。")
            return
        lines = [f"{r[0]} · {r[2]:<9} · {(r[1] or '')[:24]}" for r in rows]
        await self._safe_send(uid, "最近任务（ID · 状态 · 标题）：\n" + "\n".join(lines))

    async def _cmd_switch_gen(self, uid: str, name: str) -> None:
        provider = GEN_ALIAS.get(name.lower()) or GEN_ALIAS.get(name)
        if not provider:
            await self._safe_send(uid, f"未知通道 {name}。可选：ark / gpt / doubao")
            return
        self.gen.set_primary(provider)
        await self._safe_send(uid, f"生图主通道已切换为 {provider}（当前顺序：{' → '.join(self.gen._order)}）")

    async def _cmd_gen(self, uid: str, prompt: str) -> None:
        out_dir = self.workspace / "users" / uid.replace(":", "_")
        out_dir.mkdir(parents=True, exist_ok=True)  # v1.1：workspace 已含 bot_id 分层
        out = str(out_dir / f"{int(time.time())}.jpg")
        await self._safe_send(uid, f"生图中（通道 {self.gen.primary}）…")
        try:
            path, used = await asyncio.to_thread(
                self.gen.generate, prompt, out, "portrait", None)
            await self.adapter.send_image(uid, path)
            await self._safe_send(uid, f"完成（通道 {used}）。")
        except Exception as exc:
            await self._safe_send(uid, f"生图失败：{exc}")

    async def _cmd_manual_note(self, uid: str, text: str) -> None:
        task_id = uuid.uuid4().hex[:8]
        self._db.execute(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)",
            (task_id, uid, "", text[:60], "queued",
             json.dumps({"manual_input": text[:2000]}, ensure_ascii=False),
             time.time(), time.time()))
        self._db.commit()
        await self._safe_send(
            uid, f"已收到笔记链接，任务 {task_id} 入队。\n"
                 f"说明：链接详情抓取与全流程生产在 SKILL 化（Phase 1.5）后开放；"
                 f"当前可直接用 /生图 实测生图通道。")

    # ─── 任务执行（rewrite → 底图 → 交付） ──────────────────────────────

    async def _run_task(self, uid: str, task_id: str) -> None:
        def _update(status: str, detail: str = ""):
            self._db.execute("UPDATE tasks SET status=?, detail=?, updated=? WHERE id=?",
                             (status, detail, time.time(), task_id))
            self._db.commit()

        row = self._db.execute("SELECT detail FROM tasks WHERE id=?", (task_id,)).fetchone()
        topic = json.loads(row[0]) if row and row[0] else {}
        work_dir = self.workspace / "users" / uid.replace(":", "_") / task_id
        work_dir.mkdir(parents=True, exist_ok=True)  # v1.1：workspace 已含 bot_id 分层
        try:
            # ① 拆解仿写（需 config.llm；未配置则任务失败并给出指引）
            _update("rewriting")
            llm = rewrite_mod.llm_call_factory(self.config.get("llm") or {})
            result = await asyncio.to_thread(
                rewrite_mod.run_rewrite, llm, topic, self.config.get("persona") or {})
            sim = result.get("similarity", 1.0)
            if sim > 0.30:  # 原创度门槛（POC 定稿 0.30）
                raise RuntimeError(f"原创度自检未通过（相似度 {sim:.2f} > 0.30），需人工复写")
            (work_dir / "rewrite.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            # ② 版式编排 + 4 张图文卡片（LLM 编排 → 底图生图 → PIL 排版）
            _update("imaging")
            layout = await asyncio.to_thread(imagepack.plan_layout, llm, result)
            (work_dir / "layout.json").write_text(
                json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
            pack = await asyncio.to_thread(
                imagepack.generate_pack, layout, self.gen, str(work_dir / "images"), self.assets_dir)
            # ③ 交付：文案 + 4 图
            await deliver_note(
                self.adapter, uid, result.get("title", ""), result.get("content", ""),
                pack["cards"], video_path=None)
            # ④ 视频合成（图文同源 5 镜头：narrations 与 video_frames 对应；视频失败不影响图文交付）
            _update("composing")
            try:
                video = await asyncio.to_thread(
                    video_mod.make_video, pack["video_frames"], layout["narrations"],
                    str(work_dir / "video.mp4"), self.assets_dir)
                await deliver_video(self.adapter, uid, video, self.video_max_mb)
            except Exception as exc:
                logger.warning("视频合成失败（图文已交付）: %s", exc)
                await self._safe_send(uid, f"视频合成失败：{exc}\n（图文已交付，视频文件在 {work_dir}/video.mp4 重试）")
                _update("delivered", f"video_failed: {exc}")
                return
            _update("delivered")
            await self._safe_send(
                uid, f"交付完成 ✅ 相似度 {sim:.2f}｜任务目录 {work_dir}")
        except Exception as exc:
            _update("failed", str(exc))
            logger.exception("任务 %s 失败", task_id)
            await self._safe_send(uid, f"任务 {task_id} 失败：{exc}")

    # ─── 推送补发（设计 3.4：tokenless 失败 → 落盘 → 下次对话补发） ────

    def queue_push(self, uid: str, text: str) -> None:
        self._db.execute("INSERT INTO pending_push(uid, text, created) VALUES (?,?,?)",
                         (uid, text, time.time()))
        self._db.commit()
        logger.info("推送失败已入补发队列 uid=%s", uid)

    async def _flush_pending(self, uid: str) -> None:
        rows = self._db.execute(
            "SELECT id, text FROM pending_push WHERE uid=? ORDER BY id", (uid,)).fetchall()
        for pid, text in rows:
            try:
                await self.adapter.send_text(uid, f"[补发] {text}")
                self._db.execute("DELETE FROM pending_push WHERE id=?", (pid,))
                self._db.commit()
            except Exception:
                break  # 仍失败，留队列下次再试

    async def _safe_send(self, uid: str, text: str) -> None:
        try:
            await self.adapter.send_text(uid, text)
        except Exception:
            logger.exception("回复失败 uid=%s", uid)
