"""Completed tasks: leave the list, land in completed.json + the day's note."""
import json
import logging
import os
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import notes  # noqa: E402
from app.app import App  # noqa: E402
from app.store import TodoStore  # noqa: E402


def _today_ts(hour: int = 10) -> float:
    now = time.localtime()
    return time.mktime((now.tm_year, now.tm_mon, now.tm_mday, hour, 0, 0, 0, 0, -1))


class TestArchive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agenttodo_arch_")
        self.path = os.path.join(self.tmp, "todos.json")
        self.store = TodoStore(self.path)

    def test_completion_leaves_list_and_is_archived(self):
        t = self.store.add({"title": "回复 ROVSEA 报价", "customer": "ROVSEA"})
        self.store.set_progress(t.id, 100)
        done = [x for x in self.store.all() if x.done]
        recs = self.store.archive_tasks(done, prev_progress={t.id: 0})

        self.assertEqual(len(recs), 1)
        self.assertIsNone(self.store.get(t.id))            # gone from the list
        self.assertEqual(self.store.stats()["total"], 0)
        with open(self.path, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), [])             # persisted
        with open(os.path.join(self.tmp, "completed.json"), "r",
                  encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved[0]["title"], "回复 ROVSEA 报价")
        self.assertIn("completed_at", saved[0])
        self.assertEqual(saved[0]["prev_progress"], 0)

        again = TodoStore(self.path)                       # survives a restart
        self.assertEqual(again.tasks, [])
        self.assertEqual(len(again.archive), 1)
        self.assertEqual(again.completed_today(), 1)
        self.assertEqual(again.stats()["done_today"], 1)

    def test_undo_restores_previous_progress(self):
        t = self.store.add({"title": "拜访 30 家客户", "progress": 40})
        self.store.set_progress(t.id, 100)
        done = self.store.get(t.id)
        if done is None:
            self.fail("task disappeared before archiving")
        self.store.archive_tasks([done], prev_progress={t.id: 75})

        rec = self.store.pop_archive()
        if rec is None:
            self.fail("archive is empty")
        self.assertEqual(self.store.archive, [])
        task = self.store.restore(rec)
        self.assertEqual(task.id, t.id)
        self.assertEqual(task.progress, 75)
        self.assertFalse(task.done)
        self.assertIsNotNone(self.store.get(t.id))
        self.assertIsNone(self.store.pop_archive())

    def test_completed_today_ignores_older_records(self):
        t = self.store.add({"title": "旧任务"})
        old = time.time() - 10 * 86400
        self.store.archive_tasks([t], completed_at=old)
        self.assertEqual(self.store.completed_today(), 0)
        self.assertEqual(self.store.completed_today(date.fromtimestamp(old)), 1)

    def test_archive_survives_corrupt_archive_file(self):
        with open(os.path.join(self.tmp, "completed.json"), "w",
                  encoding="utf-8") as f:
            f.write("{ not json")
        store = TodoStore(self.path)
        self.assertEqual(store.archive, [])
        t = store.add({"title": "x"})
        store.archive_tasks([t])
        self.assertEqual(len(TodoStore(self.path).archive), 1)


class TestDailyNotes(unittest.TestCase):
    def setUp(self):
        self.vault = tempfile.mkdtemp(prefix="agenttodo_vault_")
        self.writer = notes.DailyNotes(self.vault, "Daily Notes", "✅ 今日完成")
        self.day = date.today()
        self.path = self.writer.note_path(self.day)

    def _write(self, text):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def _read(self):
        with open(self.path, "r", encoding="utf-8", newline="") as f:
            return f.read()

    def test_creates_note_and_section(self):
        out = self.writer.log("跟进 1610R 订单", _today_ts(9),
                              customer="ROVSEA", due="10月30日", source="PO.pdf")
        self.assertTrue(out["ok"], out)
        text = self._read()
        self.assertIn(f"# {self.day.isoformat()}", text)
        self.assertIn("## ✅ 今日完成", text)
        self.assertIn("- [x] 09:00 跟进 1610R 订单", text)
        self.assertIn("客户 ROVSEA", text)
        self.assertIn("截止 10月30日", text)
        self.assertIn("来源 PO.pdf", text)

    def test_appends_inside_existing_section_only(self):
        self._write(
            f"# {self.day.isoformat()}\n\n"
            "## 今日工作\n- 已有内容\n\n"
            "## ✅ 今日完成\n- [x] 08:00 昨天的事\n\n"
            "## 📋 今日待办\n- [ ] 未完成的事\n\n---\n"
            "*最后更新: x*\n")
        self.writer.log("打电话给 Wolfgang", _today_ts(11))
        text = self._read()
        self.assertIn("- [x] 08:00 昨天的事", text)
        self.assertIn("- [x] 11:00 打电话给 Wolfgang", text)
        # the new line sits inside the completed block, not at the file end
        self.assertLess(text.index("打电话给 Wolfgang"),
                        text.index("## 📋 今日待办"))
        self.assertIn("- [ ] 未完成的事", text)
        self.assertIn("*最后更新: x*", text)

    def test_same_line_is_not_added_twice(self):
        a = self.writer.log("回复邮件", _today_ts(9))
        b = self.writer.log("回复邮件", _today_ts(9))
        self.assertTrue(a["ok"] and b["ok"])
        self.assertTrue(b.get("dup"))
        self.assertEqual(self._read().count("回复邮件"), 1)

    def test_missing_section_is_created_after_today_work(self):
        self._write(f"# {self.day.isoformat()}\n\n## 今日工作\n- 干活\n\n"
                    "## 📋 今日待办\n- [ ] x\n")
        self.writer.log("完成一件事", _today_ts(14))
        text = self._read()
        self.assertIn("## ✅ 今日完成", text)
        self.assertLess(text.index("## 今日工作"), text.index("## ✅ 今日完成"))
        self.assertLess(text.index("## ✅ 今日完成"), text.index("## 📋 今日待办"))

    def test_crlf_file_stays_crlf(self):
        self._write("## ✅ 今日完成\r\n- [x] 08:00 老记录\r\n")
        self.writer.log("新记录", _today_ts(12))
        raw = self._read()
        self.assertIn("\r\n", raw)
        self.assertNotIn("\n", raw.replace("\r\n", ""))   # no bare LF introduced
        self.assertIn("- [x] 12:00 新记录", raw.splitlines())

    def test_remove_line(self):
        out = self.writer.log("会被撤销", _today_ts(15))
        self.assertTrue(os.path.exists(out["path"]))
        self.assertTrue(self.writer.remove_line(out["path"], out["line"]))
        self.assertNotIn("会被撤销", self._read())
        self.assertFalse(self.writer.remove_line(out["path"], out["line"]))

    def test_missing_vault_is_reported_not_raised(self):
        writer = notes.DailyNotes(os.path.join(self.vault, "nope"), "Daily Notes")
        out = writer.log("x", _today_ts(9))
        self.assertFalse(out["ok"])
        self.assertIn("不存在", out["error"])
        self.assertFalse(writer.available())

    def test_write_leaves_no_tmp_file(self):
        self.writer.log("x", _today_ts(9))
        self.writer.log("y", _today_ts(10))
        names = os.listdir(os.path.dirname(self.path))
        self.assertFalse([n for n in names if n.endswith(".tmp")], names)


class TestAppCompletion(unittest.TestCase):
    """The controller wiring, without booting the GUI."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agenttodo_app_")
        vault = tempfile.mkdtemp(prefix="agenttodo_vault_")
        self.app = App.__new__(App)
        self.app.dir = self.tmp
        self.app.store = TodoStore(os.path.join(self.tmp, "todos.json"))
        self.app.config = {"archive_done": True, "obsidian_enabled": True}
        self.app.notes = notes.DailyNotes(vault, "Daily Notes", "✅ 今日完成")
        self.app.log = logging.getLogger("agenttodo.test")
        self.app._last_notes_written = 0

    def test_flush_done_archives_and_writes_note(self):
        t = self.app.store.add({"title": "确认报价单", "customer": "Alphageo"})
        self.app.store.set_progress(t.id, 100)
        archived, written = self.app._flush_done({t.id: 0})
        self.assertEqual((archived, written), (1, 1))
        self.assertIsNone(self.app.store.get(t.id))
        note = self.app.notes.note_path(date.today())
        with open(note, "r", encoding="utf-8") as f:
            body = f.read()
        self.assertIn("确认报价单", body)
        self.assertEqual(self.app.store.archive[-1]["note_path"], note)

    def test_migration_notes_only_tasks_completed_today(self):
        old = self.app.store.add({"title": "上周就完成的事"})
        self.app.store.set_progress(old.id, 100)
        old_task = self.app.store.get(old.id)
        if old_task is None:
            self.fail("task disappeared")
        old_task.updated_at = time.time() - 10 * 86400
        fresh = self.app.store.add({"title": "今天完成的事"})
        self.app.store.set_progress(fresh.id, 100)

        archived, written = self.app._migrate_done()
        self.assertEqual(archived, 2)
        self.assertEqual(written, 1)          # only today's task is dated into a note
        note = self.app.notes.note_path(date.today())
        with open(note, "r", encoding="utf-8") as f:
            body = f.read()
        self.assertIn("今天完成的事", body)
        self.assertNotIn("上周就完成的事", body)
        self.assertEqual(self.app.store.tasks, [])

    def test_obsidian_disabled_keeps_archive(self):
        self.app.config["obsidian_enabled"] = False
        t = self.app.store.add({"title": "无笔记模式"})
        self.app.store.set_progress(t.id, 100)
        archived, written = self.app._flush_done({t.id: 0})
        self.assertEqual((archived, written), (1, 0))
        self.assertEqual(self.app.store.archive[0]["title"], "无笔记模式")
        self.assertEqual(self.app.store.archive[0].get("note_path"), None)


class TestAppEndToEnd(unittest.TestCase):
    """Boot the real controller on a temp data dir + temp vault (window hidden)."""

    def setUp(self):
        try:
            import tkinter as tk  # noqa: F401
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"Tk unavailable: {e}")
        self.data = tempfile.mkdtemp(prefix="agenttodo_e2e_")
        self.vault = tempfile.mkdtemp(prefix="agenttodo_e2e_vault_")
        with open(os.path.join(self.data, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"obsidian_enabled": True, "obsidian_vault": self.vault,
                       "obsidian_daily_dir": "Daily Notes",
                       "obsidian_section": "✅ 今日完成"}, f, ensure_ascii=False)
        self.app = App(data_dir=self.data, agent_enabled=False)
        self.app.root.withdraw()

    def tearDown(self):
        try:
            self.app.root.destroy()
        except Exception:
            pass

    def test_toggle_writes_note_then_undo_reverts_it(self):
        t = self.app.store.add({"title": "端到端完成测试", "customer": "CSignum"})
        self.app.toggle_task(t.id)

        self.assertIsNone(self.app.store.get(t.id))
        note = self.app.notes.note_path(date.today())
        with open(note, "r", encoding="utf-8") as f:
            body = f.read()
        self.assertIn("端到端完成测试", body)
        self.assertIn("## ✅ 今日完成", body)
        self.assertEqual(self.app.store.stats()["done_today"], 1)

        self.app.undo_done()
        self.assertIsNotNone(self.app.store.get(t.id))
        with open(note, "r", encoding="utf-8") as f:
            self.assertNotIn("端到端完成测试", f.read())


    def test_menu_has_undo_and_obsidian_toggle(self):
        menu = self.app.pill._build_menu()
        try:
            labels = [menu.entrycget(i, "label")
                      for i in range(int(menu.index("end") or 0) + 1)
                      if menu.type(i) != "separator"]
        finally:
            menu.destroy()
        self.assertIn("撤销最近完成(恢复任务)", labels)
        self.assertIn("☑ 记入 Obsidian 每日笔记", labels)

        self.app.set_obsidian_enabled(False)
        menu = self.app.pill._build_menu()
        try:
            labels = [menu.entrycget(i, "label")
                      for i in range(int(menu.index("end") or 0) + 1)
                      if menu.type(i) != "separator"]
        finally:
            menu.destroy()
        self.assertIn("☐ 记入 Obsidian 每日笔记", labels)
        self.assertFalse(self.app.config["obsidian_enabled"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
