"""Floating pill widget + hover dashboard (tkinter + tkdnd)."""
from __future__ import annotations

import os
import tkinter as tk

from . import theme as T
from .screen import clamp_xy, virtual_screen
from .theme import rounded_rect
from .tooltip import Tooltip

try:
    from tkinterdnd2 import COPY, DND_FILES
except Exception:  # pragma: no cover - exercised only without tkdnd installed
    COPY, DND_FILES = "copy", "DND_FILES"

PILL_SIZE = 74
DASH_W = 430
DASH_H = 580
MARGIN = 10  # inset of content inside the rounded panel


def _task_sig(task) -> tuple:
    """Everything a row renders — a row whose signature didn't change is reused."""
    return (task.title, task.detail, task.source, task.due, task.due_at,
            task.customer, task.order_date, task.amount, task.priority,
            task.mode, task.progress)


def _parse_drop_data(widget, data: str) -> list[str]:
    """Split a tkdnd drop payload into a python list (handles {paths with spaces})."""
    try:
        return list(widget.tk.splitlist(data))
    except tk.TclError:
        return data.split()


class ScrollFrame(tk.Frame):
    """Canvas-backed scrolling container."""

    def __init__(self, parent, bg):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.scrollbar = tk.Scrollbar(self, orient="vertical", width=10,
                                      command=self.canvas.yview,
                                      troughcolor=T.CARD, bg=T.STROKE,
                                      activebackground=T.TEXT_DIM,
                                      highlightthickness=0, bd=0)
        self.body = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _on_body_configure(self, _e=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)

    def _bind_wheel(self, _e=None):
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _e=None):
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


class TaskRow(tk.Frame):
    """One task row, rendered according to ``task.mode``:

    ``check`` — binary: 圆圈 + 待办/已完成，不出现百分比。
    ``pct``   — quantifiable goal: 百分比进度条(左键 +25 / 右键 −25)。
    行内 ``✓`` / ``％`` 按钮可随时切换这两者。
    """

    def __init__(self, parent, task, cb):
        super().__init__(parent, bg=T.CARD_ALT, highlightthickness=0)
        self.task = task
        self.cb = cb  # dict of callables
        self._hover = False
        self.quantified = getattr(task, "mode", "check") == "pct"

        self.check = tk.Canvas(self, width=22, height=22, bg=T.CARD_ALT,
                               highlightthickness=0, bd=0)
        self.check.grid(row=0, column=0, rowspan=2, padx=(10, 8), pady=9)
        self.check.bind("<Button-1>", lambda e: cb["toggle"](task.id))
        self._draw_check()

        title_fg = T.TEXT_DIM if task.done else T.TEXT
        self.title = tk.Label(self, text=task.title, font=T.font(10, task.done),
                              fg=title_fg, bg=T.CARD_ALT, anchor="w",
                              wraplength=240, justify="left")
        self.title.grid(row=0, column=1, sticky="ew", pady=(8, 0))

        meta_bits = []
        if getattr(task, "customer", ""):
            meta_bits.append(f"客户 {task.customer}")
        if getattr(task, "order_date", ""):
            meta_bits.append(f"订单日 {task.order_date}")
        if getattr(task, "amount", ""):
            meta_bits.append(f"金额 {task.amount}")
        if task.due:
            meta_bits.append(f"截止 {task.due}")
        if task.source:
            meta_bits.append(task.source)
        prio_txt = {"high": "高", "med": "中", "low": "低"}.get(task.priority, "中")
        meta_bits.append(f"优先级 {prio_txt}")
        self.meta = tk.Label(self, text=" · ".join(meta_bits), font=T.font(8),
                             fg=T.TEXT_DIM, bg=T.CARD_ALT, anchor="w",
                             wraplength=240, justify="left")
        self.meta.grid(row=1, column=1, sticky="ew", pady=(1, 3))

        # detail carries the specifics (产品清单 / 地址 / 联系人) — show one line
        detail_txt = (task.detail or "").strip()
        if detail_txt and detail_txt != task.title.strip():
            self.detail = tk.Label(self, text=detail_txt, font=T.font(8),
                                   fg=T.TEXT_SUB, bg=T.CARD_ALT, anchor="w",
                                   wraplength=240, justify="left")
            self.detail.grid(row=2, column=1, sticky="ew", pady=(0, 8))
        else:
            self.detail = None
            self.meta.grid_configure(pady=(1, 8))

        if self.quantified:
            # quantifiable goal → percentage bar
            self.prog = tk.Canvas(self, width=96, height=18, bg=T.CARD_ALT,
                                  highlightthickness=0, bd=0)
            self.prog.grid(row=0, column=2, rowspan=3, padx=(8, 4))
            self.prog.bind("<Button-1>",
                           lambda e: cb["progress"](task.id, min(100, task.progress + 25)))
            self.prog.bind("<Button-3>",
                           lambda e: cb["progress"](task.id, max(0, task.progress - 25)))
            self.side = self.prog
            self._draw_progress()
            Tooltip(self.prog, "左键 +25%;右键 −25%;点右侧「✓」改回完成勾选")
        else:
            # one-off item → binary state only, no percentages
            self.state_lbl = tk.Label(
                self, text="已完成" if task.done else "待办",
                font=T.font(8, True),
                fg=T.OK if task.done else T.TEXT_DIM,
                bg=T.CARD_ALT, cursor="hand2", padx=6)
            self.state_lbl.grid(row=0, column=2, rowspan=3, padx=(8, 4))
            self.state_lbl.bind("<Button-1>", lambda e: cb["toggle"](task.id))
            self.side = self.state_lbl
            Tooltip(self.state_lbl, "点我切换 完成/未完成;点右侧「％」改为百分比进度")

        # per-task mode switch: ✓ → 完成勾选 / ％ → 百分比进度
        self.mode_btn = tk.Label(self, text="✓" if self.quantified else "％",
                                 font=T.font(9), fg=T.TEXT_DIM, bg=T.CARD_ALT,
                                 cursor="hand2", padx=5)
        self.mode_btn.grid(row=0, column=3, rowspan=3)
        self.mode_btn.bind("<Button-1>", lambda e: cb["mode"](
            task.id, "check" if self.quantified else "pct"))

        self.del_btn = tk.Label(self, text="✕", font=T.font(10), fg=T.TEXT_DIM,
                                bg=T.CARD_ALT, cursor="hand2", padx=6)
        self.del_btn.grid(row=0, column=4, rowspan=3, padx=(2, 8))
        self.del_btn.bind("<Button-1>", lambda e: cb["delete"](task.id))

        Tooltip(self.check, "点击切换完成 / 未完成")
        Tooltip(self.mode_btn, "改为「完成勾选」,不显示百分比" if self.quantified
                else "改为「百分比进度」,可记 0-100%")
        Tooltip(self.del_btn, "删除此任务")

        for w in (self, self.title, self.meta, self.side, self.mode_btn, self.del_btn):
            w.bind("<Enter>", lambda e: self._set_hover(True))
            w.bind("<Leave>", lambda e: self._set_hover(False))
        if self.detail is not None:
            self.detail.bind("<Enter>", lambda e: self._set_hover(True))
            self.detail.bind("<Leave>", lambda e: self._set_hover(False))
        self.columnconfigure(1, weight=1)

    def _draw_check(self):
        c = self.check
        c.delete("all")
        bg = T.CARD_ALT if not self._hover else T.CARD_HOVER
        c.configure(bg=bg)
        r = 8
        cx, cy = 11, 11
        if self.task.done:
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=T.OK, outline=T.OK)
            c.create_line(cx - 4, cy, cx - 1, cy + 3, cx + 4, cy - 3,
                          fill=T.CARD, width=2, smooth=True)
        else:
            ring = T.ACCENT if self.task.progress > 0 else T.STROKE
            c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=ring, width=2)

    def _draw_progress(self):
        if not self.quantified:
            return  # binary rows have no percentage surface
        c = self.prog
        c.delete("all")
        bg = T.CARD_ALT if not self._hover else T.CARD_HOVER
        c.configure(bg=bg)
        p = self.task.progress
        rounded_rect(c, 0, 2, 96, 16, 6, fill=T.CARD, outline=T.STROKE)
        if p > 0:
            w = max(12, round(96 * p / 100))
            fill = T.OK if p >= 100 else T.ACCENT
            rounded_rect(c, 0, 2, w, 16, 6, fill=fill, outline=fill)
        txt_color = T.CARD if p >= 55 else T.TEXT_SUB
        c.create_text(48, 9, text=f"{p}%", font=T.font(8, True), fill=txt_color)

    def _set_hover(self, on: bool):
        self._hover = on
        bg = T.CARD_HOVER if on else T.CARD_ALT
        widgets = [self, self.title, self.meta, self.side, self.mode_btn, self.del_btn]
        if self.detail is not None:
            widgets.append(self.detail)
        for w in widgets:
            w.configure(bg=bg)
        self._draw_check()
        self._draw_progress()


class Dashboard(tk.Toplevel):
    """Hover panel: add via text/drop, todo list with progress, stats."""

    def __init__(self, root, cb):
        super().__init__(root)
        self.cb = cb
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.configure(bg=T.TRANSPARENT)
            self.attributes("-transparentcolor", T.TRANSPARENT)
        except tk.TclError:
            self.configure(bg=T.CARD)
        self.geometry(f"{DASH_W}x{DASH_H}")
        self.withdraw()

        self.canvas = tk.Canvas(self, bg=T.TRANSPARENT, highlightthickness=0, bd=0)
        self.canvas.place(x=0, y=0, width=DASH_W, height=DASH_H)

        self.body = tk.Frame(self, bg=T.CARD)
        self.body.place(x=MARGIN, y=MARGIN, width=DASH_W - 2 * MARGIN,
                        height=DASH_H - 2 * MARGIN)
        self.resize(DASH_W, DASH_H)

        # Incremental list state: one row widget per task id, reused across
        # refreshes.  Rebuilding every row on every click cost 183 ms at 15
        # tasks and >1 s at 80 (measured) — a click must not repaint the list.
        # Declared BEFORE the sections: the overall-bar <Configure> binding
        # fires during first layout and reads these.
        self._rows: dict[str, TaskRow] = {}
        self._row_order: list[str] = []
        self._empty: tk.Label | None = None
        self._done_header: tk.Frame | None = None
        self._done_label: tk.Label | None = None
        self._done_open = False
        self._tasks: list = []
        self._stats: dict = {}

        self._build_header()
        self._build_add_row()
        self._build_drop_zone()
        self._build_list()
        self._build_footer()

        self.bind("<Enter>", lambda e: cb["hover_panel"](True))
        # 面板里任何一次左键点击 = 用户要它留着(与 ◎ 固定同义)
        self.bind("<Button-1>", lambda e: cb.get("pin_keep") and cb["pin_keep"]())
        self.bind("<Leave>", lambda e: cb["hover_panel"](False))

    def resize(self, w: int, h: int):
        """Adaptive panel size; on short screens the list area shrinks."""
        self._size = (w, h)  # never name attrs _w/_h: tkinter Widget owns _w
        self.geometry(f"{w}x{h}")
        self.canvas.place(x=0, y=0, width=w, height=h)
        self.canvas.delete("all")
        rounded_rect(self.canvas, 0, 0, w - 1, h - 1, 18,
                     fill=T.CARD, outline=T.STROKE)
        self.body.place(x=MARGIN, y=MARGIN, width=w - 2 * MARGIN,
                        height=h - 2 * MARGIN)

    # ------------------------------------------------------------- sections
    def _build_header(self):
        head = tk.Frame(self.body, bg=T.CARD)
        head.pack(fill="x", padx=12, pady=(12, 6))
        tk.Label(head, text="AgentTodo", font=T.font(13, True), fg=T.TEXT,
                 bg=T.CARD).pack(side="left")
        tk.Label(head, text="agent 悬浮待办", font=T.font(8), fg=T.TEXT_DIM,
                 bg=T.CARD).pack(side="left", padx=(8, 0))

        self._obsidian_on = True
        self.obsidian_btn = tk.Label(head, text="✎ 笔记", font=T.font(8),
                                     fg=T.OK, bg=T.CARD, cursor="hand2", padx=4)
        self.obsidian_btn.pack(side="left", padx=(10, 0))
        self.obsidian_btn.bind("<Button-1>", lambda e: self._toggle_obsidian())
        Tooltip(self.obsidian_btn,
                "完成的任务自动移出列表,并写入当天 Obsidian 每日笔记的「今日完成」;"
                "点击开关")

        self.pin_btn = tk.Label(head, text="◎", font=T.font(11), fg=T.TEXT_SUB,
                                bg=T.CARD, cursor="hand2", padx=6)
        self.pin_btn.pack(side="right")
        self.pin_btn.bind("<Button-1>", lambda e: self.cb["pin"]())
        self.close_btn = tk.Label(head, text="✕", font=T.font(11), fg=T.TEXT_SUB,
                                  bg=T.CARD, cursor="hand2", padx=6)
        self.close_btn.pack(side="right")
        self.close_btn.bind("<Button-1>", lambda e: self.cb["hide"]())
        Tooltip(self.pin_btn, "固定面板:移开鼠标不自动收起(再次点击取消)")
        Tooltip(self.close_btn, "隐藏面板(悬浮球和右键菜单可再打开)")

        stats_row = tk.Frame(self.body, bg=T.CARD)
        stats_row.pack(fill="x", padx=12, pady=(0, 4))
        # Pack the counter BEFORE the bar: with side="left" + expand the bar
        # otherwise eats the whole cavity and the label collapses to zero width
        # (it was invisible in every screenshot before this).
        self.stats_label = tk.Label(stats_row, text="0/0", font=T.font(9, True),
                                    fg=T.TEXT_SUB, bg=T.CARD)
        self.stats_label.pack(side="right")
        self.overall_bar = tk.Canvas(stats_row, height=10, bg=T.CARD,
                                     highlightthickness=0, bd=0)
        self.overall_bar.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.overall_bar.bind("<Configure>",
                              lambda e: self._draw_overall(self._stats))

    def _build_add_row(self):
        row = tk.Frame(self.body, bg=T.CARD)
        row.pack(fill="x", padx=12, pady=(6, 4))
        self.entry = tk.Entry(row, font=T.font(10), bg=T.CARD_ALT, fg=T.TEXT,
                              insertbackground=T.TEXT, relief="flat",
                              highlightthickness=1, highlightcolor=T.ACCENT,
                              highlightbackground=T.STROKE)
        self.entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        self.entry.bind("<Return>", lambda e: self._submit(True))
        self.ai_btn = tk.Button(row, text="AI 分析", font=T.font(9, True),
                                bg=T.ACCENT, fg=T.ON_ACCENT, activebackground=T.ACCENT,
                                activeforeground=T.ON_ACCENT, relief="flat",
                                cursor="hand2", padx=10, pady=4,
                                command=lambda: self._submit(True))
        self.ai_btn.pack(side="left")
        self.quick_btn = tk.Button(row, text="+", font=T.font(11, True),
                                   bg=T.CARD_ALT, fg=T.TEXT, activebackground=T.CARD_HOVER,
                                   activeforeground=T.TEXT, relief="flat",
                                   cursor="hand2", padx=8, pady=4,
                                   command=lambda: self._submit(False))
        self.quick_btn.pack(side="left", padx=(6, 0))
        Tooltip(self.entry, "输入文字:回车 = AI 分析;「+」= 按行快速添加")
        Tooltip(self.ai_btn, "AI 分析:由 agent 从输入内容提取任务(标题 / 截止 / 优先级)")
        Tooltip(self.quick_btn, "快速添加:不走 AI,每一行直接作为一条任务")

    def _build_drop_zone(self):
        self.drop_frame = tk.Frame(self.body, bg=T.CARD)
        self.drop_frame.pack(fill="x", padx=12, pady=(6, 4))
        self.drop_zone = tk.Label(
            self.drop_frame,
            text="拖入 图片 / 文件 到这里 — agent 自动分析生成任务",
            font=T.font(9), fg=T.TEXT_SUB, bg=T.CARD_ALT, pady=14,
            relief="flat", cursor="hand2",
        )
        self.drop_zone.pack(fill="x")
        for w in (self.drop_zone, self.drop_frame):
            try:
                w.drop_target_register(DND_FILES, "DND_TEXT", "DND_HTML")
                w.dnd_bind("<<DropEnter>>", self._on_drop_enter)
                w.dnd_bind("<<DropLeave>>", self._on_drop_leave)
                w.dnd_bind("<<Drop>>", self._on_drop)
            except (tk.TclError, AttributeError):
                pass  # tkdnd unavailable — typing still works
        Tooltip(self.drop_zone,
                "拖入即分析:图片由 AI 看图;txt/md/csv/json/代码直接读取;"
                "docx 抽取正文;pdf/xls 等交给 agent 读取")

    def _build_list(self):
        self.list_frame = ScrollFrame(self.body, T.CARD)
        self.list_frame.pack(fill="both", expand=True, padx=8, pady=(6, 2))

    def _build_footer(self):
        foot = tk.Frame(self.body, bg=T.CARD)
        foot.pack(fill="x", padx=12, pady=(2, 10))
        self.agent_var = tk.BooleanVar(value=True)
        self.agent_chk = tk.Checkbutton(
            foot, text="AI 分析", font=T.font(8), variable=self.agent_var,
            fg=T.TEXT_SUB, bg=T.CARD, activebackground=T.CARD,
            activeforeground=T.TEXT, selectcolor=T.CARD_ALT,
            highlightthickness=0, bd=0,
            command=lambda: self.cb["agent_toggle"](self.agent_var.get()))
        self.agent_chk.pack(side="right")
        Tooltip(self.agent_chk,
                "AI 分析开关:关闭后使用内置规则解析(速度最快,离线可用)")
        self.status = tk.Label(foot, text="就绪 · 拖入文件或输入文字",
                               font=T.font(8), fg=T.TEXT_DIM, bg=T.CARD,
                               anchor="w")
        self.status.pack(side="left", fill="x", expand=True)

    # ------------------------------------------------------------- behavior
    def _submit(self, use_agent: bool):
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self.cb["submit"](text, use_agent and self.agent_var.get())

    def _on_drop_enter(self, event):
        self.drop_zone.configure(bg=T.ACCENT_SOFT, fg=T.TEXT)
        return COPY

    def _on_drop_leave(self, event):
        self._reset_drop_zone()
        return COPY

    def _reset_drop_zone(self):
        self.drop_zone.configure(bg=T.CARD_ALT, fg=T.TEXT_SUB)

    def _on_drop(self, event):
        self._reset_drop_zone()
        items = [p for p in _parse_drop_data(self.drop_zone, event.data or "") if p]
        paths = [p for p in items if os.path.exists(p)]
        stray = [p for p in items if not os.path.exists(p)]
        text = " ".join(stray).strip() if stray else None
        self.cb["drop"](paths, text)
        return COPY

    # ------------------------------------------------------------- updates
    def refresh(self, tasks, stats):
        self._tasks = list(tasks)
        self._stats = stats
        self._render()

    def _render(self):
        """Rebuild only what changed, then fix the order if it moved.

        The scroll position survives because the canvas body is never emptied
        (measured: yview stays put across a refresh).
        """
        body = self.list_frame.body
        active = [t for t in self._tasks if not t.done]
        done_all = [t for t in self._tasks if t.done]
        done = done_all if self._done_open else []

        for t in active + done:
            sig = _task_sig(t)
            row = self._rows.get(t.id)
            if row is not None and getattr(row, "_sig", "") == sig:
                continue
            if row is not None:
                row.destroy()
            row = TaskRow(body, t, self.cb)
            row._sig = sig
            self._rows[t.id] = row

        keep = {t.id for t in active + done}
        for tid in [k for k in self._rows if k not in keep]:
            self._rows.pop(tid).destroy()

        self._sync_done_header(len(done_all))
        self._sync_empty(bool(self._tasks))

        order = [self._rows[t.id] for t in active]
        if self._done_header is not None:
            order.append(self._done_header)
        order += [self._rows[t.id] for t in done]
        if self._empty is not None:
            order.append(self._empty)
        if body.pack_slaves() != order:
            for w in order:
                w.pack_forget()
            for w in order:
                w.pack(fill="x", pady=0 if w is self._empty else (0, 4))

        self._row_order = [t.id for t in active + done]
        self._draw_overall(self._stats)
        total = int(self._stats.get("total", 0) or 0)
        done_today = int(self._stats.get("done_today", 0) or 0)
        if done_today:
            # Completed rows leave the list, so "done/total" would only ever
            # read 0/N — today's archive count is the number that means something.
            text = f"{total} 待办 · 今日完成 {done_today}"
        elif total:
            text = f"{self._stats.get('done', 0)}/{total} 完成"
        else:
            text = "0 待办"
        self.stats_label.configure(text=text)

    def _sync_done_header(self, n_done: int):
        if n_done == 0:
            if self._done_header is not None:
                self._done_header.destroy()
                self._done_header = None
                self._done_label = None
            self._done_open = False
            return
        if self._done_header is None:
            frame = tk.Frame(self.list_frame.body, bg=T.CARD)
            label = tk.Label(frame, text="", font=T.font(8), fg=T.TEXT_DIM,
                             bg=T.CARD, anchor="w", cursor="hand2", pady=6)
            label.pack(side="left", fill="x", expand=True)
            label.bind("<Button-1>", lambda e: self._toggle_done())
            self._done_header = frame
            self._done_label = label
        self._done_label.configure(
            text=(f"▾ 已完成 {n_done} 条 · 点击收起" if self._done_open
                  else f"▸ 已完成 {n_done} 条 · 点击展开"))

    def _toggle_done(self):
        self._done_open = not self._done_open
        self._render()

    def _sync_empty(self, has_tasks: bool):
        if has_tasks:
            if self._empty is not None:
                self._empty.destroy()
                self._empty = None
            return
        if self._empty is None:
            self._empty = tk.Label(
                self.list_frame.body,
                text="暂无任务 — 拖入文件、图片,或在上方输入文字",
                font=T.font(9), fg=T.TEXT_DIM, bg=T.CARD, pady=30)

    def _draw_overall(self, stats):
        stats = stats or {}
        c = self.overall_bar
        c.delete("all")
        # Before the first layout pass the canvas reports width 1 — fall back to
        # the row's own budget so the very first paint isn't a 120 px stub.
        w = c.winfo_width()
        if w <= 1:
            w = max(120, (self._size[0] if getattr(self, "_size", None) else DASH_W)
                    - 2 * MARGIN - 12 - 64)
        p = stats.get("overall", 0)
        rounded_rect(c, 0, 1, w, 9, 4, fill=T.CARD_ALT, outline=T.STROKE)
        if p > 0:
            fill = T.OK if p >= 100 else T.ACCENT
            rounded_rect(c, 0, 1, max(8, round(w * p / 100)), 9, 4,
                         fill=fill, outline=fill)

    def set_status(self, text: str):
        self.status.configure(text=text)

    def set_pinned(self, pinned: bool):
        self.pin_btn.configure(fg=T.ACCENT if pinned else T.TEXT_SUB,
                               text="●" if pinned else "◎")

    def set_agent_enabled(self, on: bool):
        self.agent_var.set(on)

    def set_obsidian_enabled(self, on: bool):
        self._obsidian_on = bool(on)
        self.obsidian_btn.configure(
            fg=T.OK if on else T.TEXT_DIM,
            text="✎ 笔记" if on else "✎ 笔记 关")

    def _toggle_obsidian(self):
        fn = self.cb.get("obsidian_toggle")
        if fn is not None:
            fn(not self._obsidian_on)

    def show_at(self, x: int, y: int):
        self.geometry(f"+{x}+{y}")
        self.deiconify()
        self.lift()
        self._draw_overall(self._stats)


class PillWindow:
    """The floating pill (root window): progress ring, drag, hover → dashboard."""

    def __init__(self, root, cb):
        self.root = root
        self.cb = cb
        self._drag = None
        self._moved = False

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.configure(bg=T.TRANSPARENT)
            root.attributes("-transparentcolor", T.TRANSPARENT)
        except tk.TclError:
            root.configure(bg=T.CARD)
        root.geometry(f"{PILL_SIZE}x{PILL_SIZE}")

        self.canvas = tk.Canvas(root, bg=T.TRANSPARENT, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._draw(overall=0, total=0, done=0)

        root.bind("<Button-1>", self._on_press)
        root.bind("<B1-Motion>", self._on_drag)
        root.bind("<ButtonRelease-1>", self._on_release)
        root.bind("<Enter>", lambda e: cb["hover_pill"](True))
        root.bind("<Leave>", lambda e: cb["hover_pill"](False))
        root.bind("<Button-3>", self._on_menu)
        root.bind("<Double-Button-1>", lambda e: cb["pin"]())
        Tooltip(root,
                "AgentTodo 悬浮球:单击 展开面板 · 双击 固定面板\n"
                "拖动 移动位置 · 右键 打开菜单", above=True, delay=250)

    def _draw(self, overall: int, total: int, done: int,
              overdue: int = 0, due_today: int = 0):
        c = self.canvas
        c.delete("all")
        rounded_rect(c, 2, 2, PILL_SIZE - 2, PILL_SIZE - 2, 22,
                     fill=T.CARD, outline=T.STROKE)
        pad = 8
        if total > 0:
            extent = -3.6 * overall
            c.create_arc(pad, pad, PILL_SIZE - pad, PILL_SIZE - pad,
                         start=90, extent=extent, style="arc",
                         outline=T.ACCENT if overall < 100 else T.OK, width=4)
        c.create_text(PILL_SIZE / 2, PILL_SIZE / 2 - 6, text=f"{overall}%",
                      font=T.font(13, True), fill=T.TEXT)
        c.create_text(PILL_SIZE / 2, PILL_SIZE / 2 + 13, text=f"{done}/{total}",
                      font=T.font(8), fill=T.TEXT_DIM)
        # Urgency badge: a red count means overdue, amber means due today.
        badge = overdue or due_today
        if badge:
            cx, cy, r = PILL_SIZE - 15, 15, 9
            fill = T.DANGER if overdue else T.WARN
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=fill,
                          outline=T.CARD, width=2)
            c.create_text(cx, cy, text="9+" if badge > 9 else str(badge),
                          font=T.font(7, True), fill=T.ON_ACCENT)

    def update_progress(self, stats: dict):
        self._draw(stats.get("overall", 0), stats.get("total", 0),
                   stats.get("done", 0), stats.get("overdue", 0),
                   stats.get("due_today", 0))

    def move_to(self, x: int, y: int):
        self.root.geometry(f"+{x}+{y}")

    def position(self):
        return self.root.winfo_x(), self.root.winfo_y()

    # ------------------------------------------------------------- gestures
    def _on_press(self, event):
        self._drag = (event.x_root, event.y_root)
        self._moved = False

    def _on_drag(self, event):
        if not self._drag:
            return
        dx = event.x_root - self._drag[0]
        dy = event.y_root - self._drag[1]
        if abs(dx) > 3 or abs(dy) > 3:
            self._moved = True
        x = self.root.winfo_x() + dx
        y = self.root.winfo_y() + dy
        x, y = clamp_xy(x, y, PILL_SIZE, PILL_SIZE, virtual_screen(self.root))
        self.root.geometry(f"+{x}+{y}")
        self._drag = (event.x_root, event.y_root)

    def _on_release(self, _event):
        self._drag = None
        if self._moved:
            self.cb["moved"]()
        else:
            self.cb["click"]()

    def _build_menu(self):
        menu = tk.Menu(self.root, tearoff=0, bg=T.CARD_ALT, fg=T.TEXT,
                       activebackground=T.ACCENT_SOFT, activeforeground=T.TEXT,
                       font=T.font(9), relief="flat", bd=0)
        menu.add_command(label="显示 / 隐藏面板", command=self.cb["click"])
        menu.add_command(label="固定面板", command=self.cb["pin"])
        menu.add_separator()
        menu.add_command(label="撤销最近完成(恢复任务)",
                         command=self.cb.get("undo_done"))
        on = bool(self.cb.get("obsidian_on") and self.cb["obsidian_on"]())
        toggle = self.cb.get("obsidian_toggle")
        cmd = (lambda: toggle(not on)) if toggle is not None else (lambda: None)
        menu.add_command(
            label=("☑ " if on else "☐ ") + "记入 Obsidian 每日笔记", command=cmd)
        menu.add_separator()
        menu.add_command(label="清空已完成任务", command=self.cb["clear_done"])
        menu.add_command(label="重置悬浮球位置", command=self.cb["reset_position"])
        menu.add_separator()
        menu.add_command(label="退出", command=self.cb["quit"])
        return menu

    def _on_menu(self, event):
        menu = self._build_menu()
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
