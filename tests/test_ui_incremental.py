"""Incremental list rendering: a click must not repaint (or re-create) the list.

Measured before this: ``Dashboard.refresh`` destroyed and rebuilt every row —
183 ms at 15 tasks, 700 ms at 40, 1053 ms at 80, on every toggle.  These tests
pin the behaviour (row reuse + collapse), not the widget internals.
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.store import Task  # noqa: E402
from app.ui import Dashboard  # noqa: E402

CB_NAMES = ("hover_panel", "pin", "hide", "pin_keep", "submit", "drop", "toggle",
            "progress", "mode", "delete", "agent_toggle", "obsidian_toggle",
            "undo_done")


def _cb(stats=None, obsidian_on=True, seen=None) -> dict:
    cb: dict = {name: (lambda *a, **k: None) for name in CB_NAMES}
    cb["stats"] = stats or (lambda: {"total": 0, "done": 0, "overall": 0})
    cb["obsidian_on"] = lambda: obsidian_on
    if seen is not None:
        cb["obsidian_toggle"] = lambda on: seen.append(on)
    return cb


def _stats(tasks):
    total = len(tasks)
    done = sum(1 for t in tasks if t.progress >= 100)
    return {"total": total, "done": done, "done_today": 0,
            "overall": round(sum(t.progress for t in tasks) / total) if total else 0,
            "overdue": 0, "due_today": 0}


def _task(i, **kw):
    return Task(id=f"t{i}", title=f"任务 {i}", **kw)


class TestIncrementalList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk
            cls.root = tk.Tk()
            cls.root.withdraw()
        except Exception as e:  # no display / no Tk
            raise unittest.SkipTest(f"Tk unavailable: {e}")

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.dash = Dashboard(self.root, _cb())
        self.dash.withdraw()

    def tearDown(self):
        self.dash.destroy()

    def test_unchanged_rows_are_reused(self):
        tasks = [_task(1), _task(2)]
        self.dash.refresh(tasks, _stats(tasks))
        first = self.dash._rows["t1"]
        second = self.dash._rows["t2"]
        self.dash.refresh(list(tasks), _stats(tasks))
        self.assertIs(first, self.dash._rows["t1"])
        self.assertIs(second, self.dash._rows["t2"])
        # exactly one task changed → exactly one row is rebuilt
        tasks[1].title = "改过的标题"
        self.dash.refresh(tasks, _stats(tasks))
        self.assertIs(first, self.dash._rows["t1"])
        self.assertIsNot(second, self.dash._rows["t2"])
        self.assertEqual(self.dash._rows["t2"].title.cget("text"), "改过的标题")

    def test_progress_change_rebuilds_only_that_row(self):
        a, b = _task(1), _task(2)
        self.dash.refresh([a, b], _stats([a, b]))
        row_a = self.dash._rows["t1"]
        row_b = self.dash._rows["t2"]
        a.progress = 60
        a.mode = "pct"
        self.dash.refresh([a, b], _stats([a, b]))
        self.assertIsNot(row_a, self.dash._rows["t1"])
        self.assertIs(row_b, self.dash._rows["t2"])

    def test_removed_task_drops_its_row(self):
        a, b = _task(1), _task(2)
        self.dash.refresh([a, b], _stats([a, b]))
        row_b = self.dash._rows["t2"]
        self.dash.refresh([a], _stats([a]))
        self.assertNotIn("t2", self.dash._rows)
        self.assertFalse(row_b.winfo_exists())

    def test_done_group_collapsed_by_default(self):
        open_task, done_task = _task(1), _task(2, progress=100)
        tasks = [open_task, done_task]
        self.dash.refresh(tasks, _stats(tasks))
        self.assertIn("t1", self.dash._rows)
        self.assertNotIn("t2", self.dash._rows)          # hidden under the header
        self.assertIsNotNone(self.dash._done_header)
        self.assertIn("已完成 1 条", self.dash._done_label.cget("text"))

        self.dash._toggle_done()
        self.assertIn("t2", self.dash._rows)
        self.assertIn("收起", self.dash._done_label.cget("text"))

        self.dash._toggle_done()
        self.assertNotIn("t2", self.dash._rows)

    def test_empty_state_and_back(self):
        self.dash.refresh([], {"total": 0, "done": 0, "overall": 0})
        self.assertIsNotNone(self.dash._empty)
        tasks = [_task(1)]
        self.dash.refresh(tasks, _stats(tasks))
        self.assertIsNone(self.dash._empty)
        self.assertEqual(len(self.dash._rows), 1)

    def test_pack_order_follows_task_order(self):
        tasks = [_task(i) for i in range(4)]
        self.dash.refresh(tasks, _stats(tasks))
        order = [w for w in self.dash.list_frame.body.pack_slaves()]
        self.assertEqual(order, [self.dash._rows[t.id] for t in tasks])
        # reorder the input → widgets are re-packed, not rebuilt
        before = dict(self.dash._rows)
        tasks.reverse()
        self.dash.refresh(tasks, _stats(tasks))
        order = [w for w in self.dash.list_frame.body.pack_slaves()]
        self.assertEqual(order, [self.dash._rows[t.id] for t in tasks])
        for tid, row in before.items():
            self.assertIs(row, self.dash._rows[tid])

    def test_no_change_refresh_is_cheap(self):
        tasks = [_task(i, detail="核对产品清单与数量", amount="USD 12,345.00")
                 for i in range(40)]
        self.dash.refresh(tasks, _stats(tasks))
        start = time.perf_counter()
        self.dash.refresh(tasks, _stats(tasks))
        elapsed_ms = (time.perf_counter() - start) * 1000
        # a full rebuild of 40 rows measured 700 ms; reuse must be far below
        self.assertLess(elapsed_ms, 60, f"refresh took {elapsed_ms:.0f} ms")

    def test_obsidian_badge_reflects_state_and_toggles(self):
        seen: list = []
        dash = Dashboard(self.root, _cb(obsidian_on=True, seen=seen))
        try:
            self.assertEqual(dash.obsidian_btn.cget("text"), "✎ 笔记")
            dash.set_obsidian_enabled(False)
            self.assertIn("关", dash.obsidian_btn.cget("text"))
            dash._toggle_obsidian()          # state is off → asks for ON
            self.assertEqual(seen, [True])
        finally:
            dash.destroy()

    def test_stats_label_reports_today_archived(self):
        tasks = [_task(1), _task(2)]
        stats = dict(_stats(tasks), done_today=3)
        self.dash.refresh(tasks, stats)
        self.assertEqual(self.dash.stats_label.cget("text"), "2 待办 · 今日完成 3")


if __name__ == "__main__":
    unittest.main(verbosity=2)
