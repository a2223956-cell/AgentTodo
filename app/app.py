"""App controller: wires store + analyzer + UI, handles hover/queue/config."""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import tkinter as tk

from . import const
from .analyzer import Analyzer, AgentAnalyzer
from .content import DropContent, build_drop
from .screen import clamp_xy, virtual_screen
from .store import Task, TodoStore
from .ui import DASH_H, DASH_W, PILL_SIZE, Dashboard, PillWindow


class App:
    def __init__(self, data_dir: str | None = None, agent_enabled: bool | None = None,
                 smoke: bool = False):
        self.dir = const.data_dir(data_dir)
        self.store = TodoStore(const.store_path(self.dir))
        self.config = self._load_config()
        if agent_enabled is not None:
            self.config["agent_enabled"] = agent_enabled
        self.analyzer = Analyzer(
            agent_enabled=bool(self.config.get("agent_enabled", True)),
            agent=AgentAnalyzer())

        self._queue: queue.Queue = queue.Queue()
        self._pending: list[DropContent] = []
        self._busy = False
        self._pinned = bool(self.config.get("pinned", False))
        self._hover = False
        self._hover_job = None
        self._open_job = None

        try:
            from tkinterdnd2 import TkinterDnD
            self.root = TkinterDnD.Tk()
        except Exception:
            self.root = tk.Tk()

        cbs = {
            "hover_pill": self._on_hover_pill,
            "hover_panel": self._on_hover_panel,
            "click": self.toggle_dashboard,
            "pin": self.toggle_pin,
            "hide": self.hide_dashboard,
            "moved": self._save_config,
            "reset_position": self.reset_position,
            "clear_done": self.clear_done,
            "quit": self.quit,
            "submit": self.submit_text,
            "drop": self.on_drop,
            "toggle": self.toggle_task,
            "progress": self.set_progress,
            "delete": self.delete_task,
            "agent_toggle": self.set_agent_enabled,
            "stats": self.store.stats,
        }
        self.pill = PillWindow(self.root, cbs)
        self.dashboard = Dashboard(self.root, cbs)
        self.dashboard.set_pinned(self._pinned)
        self.dashboard.set_agent_enabled(bool(self.config.get("agent_enabled", True)))

        x, y = self.config.get("pos") or self._default_pos()
        self.pill.move_to(int(x), int(y))
        self.refresh()
        self.root.after(120, self._poll)
        if smoke:
            self.root.after(300, lambda: self.smoke_demo())

    # ------------------------------------------------------------- config
    def _load_config(self) -> dict:
        try:
            with open(const.config_path(self.dir), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_config(self):
        self.config["pos"] = list(self.pill.position())
        self.config["pinned"] = self._pinned
        try:
            with open(const.config_path(self.dir), "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def _default_pos(self):
        x1, y1, x2, y2 = virtual_screen(self.root)
        return x2 - PILL_SIZE - 40, y1 + (y2 - y1) // 2 - PILL_SIZE

    def reset_position(self):
        self.pill.move_to(*self._default_pos())
        self._save_config()
        self.dashboard.set_status("悬浮球位置已重置")

    # ------------------------------------------------------------- hover
    def _on_hover_pill(self, entered: bool):
        self._hover = entered
        if entered:
            self._open_job = self.root.after(120, self.show_dashboard)

    def _on_hover_panel(self, entered: bool):
        self._hover = entered
        if entered and self._open_job:
            self.root.after_cancel(self._open_job)
            self._open_job = None
        if not entered:
            self._schedule_close_check()

    def _schedule_close_check(self):
        if self._hover_job:
            self.root.after_cancel(self._hover_job)
        self._hover_job = self.root.after(450, self._close_check)

    def _close_check(self):
        if self._pinned:
            return
        try:
            px, py = self.root.winfo_pointerxy()
            w = self.root.winfo_containing(px, py)
        except tk.TclError:
            w = None
        if w is not None:
            top = w.winfo_toplevel()
            if top in (self.root, self.dashboard):
                return
        self.hide_dashboard()

    def show_dashboard(self):
        if self._open_job:
            self.root.after_cancel(self._open_job)
            self._open_job = None
        px, py = self.pill.position()
        x1, y1, x2, y2 = virtual_screen(self.root)
        w = min(DASH_W, x2 - x1 - 16)
        h = min(DASH_H, y2 - y1 - 16)   # short screens shrink the list area
        self.dashboard.resize(w, h)
        x = px + PILL_SIZE + 10
        if x + w > x2 - 12:
            x = px - w - 10
        x, y = clamp_xy(x, py, w, h, (x1 + 8, y1 + 8, x2 - 8, y2 - 8))
        self.dashboard.show_at(x, y)

    def hide_dashboard(self):
        self.dashboard.withdraw()

    def toggle_dashboard(self):
        if self.dashboard.state() == "withdrawn":
            self.show_dashboard()
        else:
            self.hide_dashboard()

    def toggle_pin(self):
        self._pinned = not self._pinned
        self.dashboard.set_pinned(self._pinned)
        self._save_config()
        if self._pinned:
            self.show_dashboard()

    # ------------------------------------------------------------- actions
    def submit_text(self, text: str, use_agent: bool):
        text = (text or "").strip()
        if not text:
            return
        if not use_agent:
            tasks = [{"title": line.strip()[:80], "source": "输入文字", "progress": 0}
                     for line in text.splitlines() if line.strip()][:10]
            self.add_tasks(tasks, "直接添加")
            return
        self.enqueue(build_drop(None, text, label="输入文字"))

    def on_drop(self, paths: list[str], text: str | None):
        if not paths and not (text or "").strip():
            self.dashboard.set_status("未识别到可分析的内容")
            return
        label = ""
        if paths:
            label = os.path.basename(paths[0]) + (f" 等 {len(paths)} 项" if len(paths) > 1 else "")
        self.enqueue(build_drop(paths, text, label=label))

    def enqueue(self, dc: DropContent):
        self._pending.append(dc)
        self._kick_worker()

    def _kick_worker(self):
        if self._busy or not self._pending:
            return
        self._busy = True
        dc = self._pending.pop(0)
        n = len(self._pending)
        self.dashboard.set_status(
            "正在分析内容…" if n == 0 else f"正在分析内容… (队列中 {n} 项)")
        threading.Thread(target=self._worker, args=(dc,), daemon=True).start()

    def _worker(self, dc: DropContent):
        try:
            tasks, note = self.analyzer.analyze(dc)
        except Exception as e:  # never let a worker die silently
            tasks, note = [], f"分析出错: {e}"
        self._queue.put((tasks, note))

    def _poll(self):
        try:
            while True:
                tasks, note = self._queue.get_nowait()
                added = self.add_tasks(tasks, note, quiet=True)
                self.dashboard.set_status(f"已添加 {added} 条任务 · {note}")
                self._busy = False
                self._kick_worker()
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def add_tasks(self, tasks: list[dict], note: str = "", quiet: bool = False) -> int:
        added = 0
        for raw in tasks:
            title = (raw.get("title") or "").strip()
            if title and self.store.find_by_title(title):
                continue  # de-dupe repeats of the same drop
            self.store.add(raw)
            added += 1
        self.refresh()
        if not quiet:
            self.dashboard.set_status(f"已添加 {added} 条任务 · {note}" if note
                                      else f"已添加 {added} 条任务")
        return added

    def toggle_task(self, task_id: str):
        self.store.toggle_done(task_id)
        self.refresh()

    def set_progress(self, task_id: str, progress: int):
        self.store.set_progress(task_id, progress)
        self.refresh()

    def delete_task(self, task_id: str):
        self.store.delete(task_id)
        self.refresh()

    def clear_done(self):
        removed = self.store.clear_done()
        self.refresh()
        self.dashboard.set_status(f"已清理 {removed} 条完成任务")

    def set_agent_enabled(self, on: bool):
        self.analyzer.agent_enabled = bool(on)
        self.config["agent_enabled"] = bool(on)
        self._save_config()
        self.dashboard.set_status("AI 分析已开启" if on else "AI 分析已关闭 · 使用规则解析")

    # ------------------------------------------------------------- render
    def refresh(self):
        tasks = self.store.all()
        stats = self.store.stats()
        self.dashboard.refresh(tasks, stats)
        self.pill.update_progress(stats)

    def quit(self):
        self._save_config()
        self.root.destroy()

    def run(self):
        self.root.mainloop()

    # ------------------------------------------------------------- smoke
    def smoke_demo(self):
        """Self-test path used by `main.py --smoke`: exercises drop + text + progress."""
        self._smoke_results = []

        # 1. typed text through the real submit path (direct add)
        self.submit_text("周五前提交季度报告\n给张工回复邮件确认接口", use_agent=False)
        self._smoke_results.append(
            ("submit_text adds tasks", self.store.stats()["total"] >= 2))

        # 2. file + image drop through the real drop handler (async analyze)
        sample_dir = const.tmp_dir(self.dir)
        txt = os.path.join(sample_dir, "会议纪要.txt")
        with open(txt, "w", encoding="utf-8") as f:
            f.write("- 10月20日前完成设计评审\n- 需要联系供应商确认报价\n明天预约牙医")
        img = os.path.join(sample_dir, "截图.png")
        try:
            from PIL import Image, ImageDraw
            im = Image.new("RGB", (320, 180), (30, 34, 51))
            d = ImageDraw.Draw(im)
            d.text((12, 12), "TODO: prepare slides", fill=(230, 235, 250))
            im.save(img)
        except Exception:
            pass
        self._smoke_before = self.store.stats()["total"]
        self.dashboard._on_drop(type("E", (), {
            "data": " ".join("{%s}" % p for p in (txt, img) if os.path.exists(p))}))

        self.show_dashboard()
        self._smoke_deadline = time.time() + (60 if self.analyzer.agent_enabled else 4)
        self._smoke_wait()

    def _smoke_wait(self):
        if (self.store.stats()["total"] > self._smoke_before
                or time.time() > self._smoke_deadline):
            self.root.after(400, self._smoke_phase2)
        else:
            self.root.after(500, self._smoke_wait)

    def _smoke_phase2(self):
        added_by_drop = self.store.stats()["total"] - self._smoke_before
        self._smoke_results.append(("drop adds tasks", added_by_drop >= 2))
        self._smoke_results.append(("drop tasks carry source", any(
            ("会议纪要" in (t.source or "")) or ("截图" in (t.source or ""))
            or ("截图" in t.title) or (t.source == "图片")
            for t in self.store.all())))

        first = self.store.all()[0]
        self.set_progress(first.id, 75)
        self._smoke_results.append(
            ("progress update", self.store.get(first.id).progress == 75))
        self.toggle_task(first.id)
        self._smoke_results.append(("toggle done", self.store.get(first.id).done))
        self._smoke_results.append(("stats consistent",
                                    self.store.stats()["done"] == sum(
                                        1 for t in self.store.all() if t.done)))

        for name, ok in self._smoke_results:
            print(f"[smoke] {'PASS' if ok else 'FAIL'}: {name}")
        stats = self.store.stats()
        print(f"[smoke] tasks total={stats['total']} overall={stats['overall']}%")
        self.dashboard.set_status(
            "smoke 完成 · " + ("全部通过" if all(r[1] for r in self._smoke_results)
                              else "存在失败"))
        self.root.after(1200, lambda: (self._smoke_shot(),
                                       self.root.after(600, self.quit)))

    def _smoke_shot(self):
        path = os.environ.get("AGENTTODO_SMOKE_SHOT")
        if not path:
            return
        try:
            from PIL import ImageGrab
            ImageGrab.grab().save(path)
            print(f"[smoke] screenshot saved: {path}")
        except Exception as e:
            print(f"[smoke] screenshot failed: {e}")
