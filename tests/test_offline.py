"""Offline tests for AgentTodo: store, content extraction, analyzers (no GUI)."""
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.analyzer import (AgentAnalyzer, Analyzer, build_query, clean_agent_output,
                          extract_json, heuristic_tasks, normalize_tasks)
from app.content import build_drop, read_docx, read_text_file
from app.store import TodoStore, normalize_task


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agenttodo_test_")
        self.path = os.path.join(self.tmp, "todos.json")
        self.store = TodoStore(self.path)

    def test_add_and_persist_roundtrip(self):
        t = self.store.add({"title": "写周报", "priority": "高", "progress": "30"})
        self.assertEqual(t.priority, "high")
        self.assertEqual(t.progress, 30)
        again = TodoStore(self.path)
        self.assertEqual(len(again.tasks), 1)
        self.assertEqual(again.tasks[0].title, "写周报")
        self.assertEqual(again.tasks[0].progress, 30)

    def test_progress_clamp_and_toggle(self):
        t = self.store.add({"title": "x", "progress": 250})
        self.assertEqual(t.progress, 100)
        self.store.set_progress(t.id, -5)
        self.assertEqual(self.store.get(t.id).progress, 0)
        self.store.toggle_done(t.id)
        self.assertTrue(self.store.get(t.id).done)
        self.store.toggle_done(t.id)
        self.assertFalse(self.store.get(t.id).done)

    def test_clear_done_and_stats(self):
        a = self.store.add({"title": "a", "progress": 100})
        self.store.add({"title": "b", "progress": 50})
        self.store.add({"title": "c", "progress": 0})
        s = self.store.stats()
        self.assertEqual((s["total"], s["done"], s["in_progress"], s["pending"]),
                         (3, 1, 1, 1))
        self.assertEqual(s["overall"], 50)
        self.assertEqual(self.store.clear_done(), 1)
        self.assertEqual(self.store.stats()["total"], 2)

    def test_normalize_task_defaults(self):
        n = normalize_task({"title": "  多余   空格  ", "priority": "p1",
                            "progress": "abc"})
        self.assertEqual(n["title"], "多余 空格")
        self.assertEqual(n["priority"], "high")
        self.assertEqual(n["progress"], 0)
        n2 = normalize_task({})
        self.assertTrue(n2["title"])  # never empty

    def test_order_fields(self):
        t = self.store.add({"title": "跟进 ROVSEA 订单 1610R 交付", "customer": "ROVSEA",
                            "order_date": "2026-10-16", "amount": "USD 12,345.00"})
        self.assertEqual((t.customer, t.order_date, t.amount),
                         ("ROVSEA", "2026-10-16", "USD 12,345.00"))
        # aliases the agent may emit
        n = normalize_task({"title": "x", "client": "Chasing", "total": "EUR 900"})
        self.assertEqual((n["customer"], n["amount"]), ("Chasing", "EUR 900"))
        # editing another field must not drop them, and they survive a reload
        self.store.update(t.id, priority="high")
        self.assertEqual(self.store.get(t.id).amount, "USD 12,345.00")
        self.assertEqual(TodoStore(self.path).get(t.id).customer, "ROVSEA")

    def test_mode_check_vs_pct(self):
        # no mode in the payload → 0/100 = binary, a mid value = quantified goal
        a = self.store.add({"title": "回复邮件", "progress": 0})
        b = self.store.add({"title": "拜访30家客户", "progress": 40})
        c = self.store.add({"title": "提交报价单", "progress": 0, "mode": "pct"})
        self.assertEqual((a.mode, b.mode, c.mode), ("check", "pct", "pct"))
        # setting a mid value on a binary task promotes it to percentage tracking
        self.store.set_progress(a.id, 50)
        self.assertEqual(self.store.get(a.id).mode, "pct")
        # switching back to check keeps the value (no silent data loss)
        self.store.set_mode(a.id, "check")
        self.assertEqual(self.store.get(a.id).mode, "check")
        self.assertEqual(self.store.get(a.id).progress, 50)
        # editing other fields must not lose the mode
        self.store.update(c.id, title="提交ROVSEA报价单")
        self.assertEqual(self.store.get(c.id).mode, "pct")
        # mode survives a reload from disk
        self.assertEqual(TodoStore(self.path).get(c.id).mode, "pct")
        # unknown mode string falls back to the progress-based inference
        self.assertEqual(normalize_task({"title": "x", "mode": "???"})["mode"], "check")
        self.assertEqual(normalize_task({"title": "x", "mode": "百分比"})["mode"], "pct")


    def test_due_parsing_and_ordered_list(self):
        from datetime import date, timedelta
        today = date.today()
        past = (today - timedelta(days=2)).isoformat()
        soon = (today + timedelta(days=5)).isoformat()
        overdue = self.store.add({"title": "逾期", "due": past})
        due_now = self.store.add({"title": "今天到期", "due": "今天"})
        later = self.store.add({"title": "以后", "due": soon})
        undated = self.store.add({"title": "无截止"})
        done = self.store.add({"title": "已完成", "progress": 100, "due": past})

        order = [t.id for t in self.store.ordered(today=today)]
        self.assertEqual(order, [overdue.id, due_now.id, later.id,
                                 undated.id, done.id])
        # raw text preserved, parsed date alongside it
        self.assertEqual(self.store.get(overdue.id).due, past)
        self.assertEqual(self.store.get(overdue.id).due_at, past)
        self.assertEqual(self.store.get(due_now.id).due, "今天")
        self.assertEqual(self.store.get(due_now.id).due_at, today.isoformat())
        self.assertEqual(self.store.get(undated.id).due_at, "")

        stats = self.store.stats()
        self.assertEqual((stats["overdue"], stats["due_today"]), (1, 1))

        # editing another field keeps the parsed date …
        self.store.update(later.id, priority="high")
        self.assertEqual(self.store.get(later.id).due_at, soon)
        # … editing the due text re-derives it
        self.store.update(later.id, due="明天")
        self.assertEqual(self.store.get(later.id).due_at,
                         (today + timedelta(days=1)).isoformat())


class TestJsonExtraction(unittest.TestCase):
    def test_fenced_json(self):
        raw = '好的,提取如下:\n```json\n{"tasks": [{"title": "提交报告", "due": "周五"}]}\n```\n完成。'
        obj = extract_json(raw)
        self.assertEqual(obj["tasks"][0]["title"], "提交报告")

    def test_bare_json_with_noise(self):
        raw = '说明文字 {"tasks": [{"title": "买菜", "detail": "含 {括号} 内容"}]} 尾巴'
        obj = extract_json(raw)
        self.assertEqual(obj["tasks"][0]["title"], "买菜")

    def test_no_json(self):
        self.assertIsNone(extract_json("完全没有结构化内容"))
        self.assertIsNone(extract_json(""))

    def test_alias_and_normalize(self):
        tasks = normalize_tasks({"todos": [{"title": "A", "priority": "urgent"}]},
                                source="x.txt")
        self.assertEqual(tasks[0]["priority"], "high")
        self.assertEqual(tasks[0]["source"], "x.txt")
        self.assertEqual(normalize_tasks(None), [])

    def test_clean_agent_output(self):
        raw = "cli.startup.warning\n这里是结果\n\nsession_id: 20261016_014854_d348f8"
        self.assertEqual(clean_agent_output(raw), "这里是结果")


class TestHeuristic(unittest.TestCase):
    def test_bullets_and_keywords(self):
        dc = build_drop(None, "- 10月20日前完成设计评审\n需要联系供应商确认报价\n"
                              "明天预约牙医\n今天天气不错")
        tasks = heuristic_tasks(dc)
        titles = " | ".join(t["title"] for t in tasks)
        self.assertIn("设计评审", titles)
        self.assertIn("供应商", titles)
        self.assertIn("预约牙医", titles)
        dues = [t["due"] for t in tasks]
        self.assertTrue(any("10月20" in d for d in dues))
        self.assertTrue(any("明天" in d for d in dues))

    def test_priority_flags(self):
        dc = build_drop(None, "- 紧急修复登录崩溃\n- 有空整理书架")
        tasks = heuristic_tasks(dc)
        self.assertEqual(tasks[0]["priority"], "high")
        self.assertEqual(tasks[1]["priority"], "low")

    def test_fallback_lines(self):
        dc = build_drop(None, "只是一些普通句子\n另一句话")
        tasks = heuristic_tasks(dc)
        self.assertGreaterEqual(len(tasks), 1)


class TestContent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agenttodo_ct_")

    def _write(self, name, data, enc="utf-8", binary=False):
        p = os.path.join(self.tmp, name)
        with open(p, "wb" if binary else "w", encoding=None if binary else enc) as f:
            f.write(data)
        return p

    def test_text_encodings(self):
        p = self._write("a.txt", "待办: 交房租", enc="gbk")
        self.assertIn("交房租", read_text_file(p))
        p2 = self._write("b.txt", "hello 待办", enc="utf-8")
        self.assertIn("待办", read_text_file(p2))

    def test_docx_extraction(self):
        p = os.path.join(self.tmp, "c.docx")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("word/document.xml",
                       '<w:document><w:body><w:p><w:r><w:t>准备周会材料'
                       '</w:t></w:r></w:p></w:body></w:document>')
        self.assertIn("准备周会材料", read_docx(p))

    def test_build_drop_classifies(self):
        txt = self._write("纪要.txt", "- 完成方案评审")
        img = self._write("s.png", b"\x89PNG\r\n\x1a\nxxxx", binary=True)
        pdf = self._write("d.pdf", b"%PDF-1.4 binary-ish \x00\x01", binary=True)
        dc = build_drop([txt, img, pdf], "直接输入的文字")
        self.assertEqual(len(dc.image_paths), 1)
        self.assertEqual(len(dc.text_blocks), 2)  # file text + typed text
        self.assertEqual(len(dc.other_paths), 1)  # pdf goes to agent by path
        self.assertIn("方案评审", dc.text_for_prompt())
        self.assertIn("直接输入的文字", dc.text_for_prompt())


class TestAgentPath(unittest.TestCase):
    def test_build_query_includes_paths(self):
        dc = build_drop(None, "文本内容", label="输入文字")
        dc.image_paths = [r"C:\tmp\截图.png"]
        dc.other_paths = [r"C:\tmp\报告.pdf"]
        q = build_query(dc)
        self.assertIn("截图.png", q)
        self.assertIn("报告.pdf", q)
        self.assertIn("文本内容", q)
        self.assertIn('"tasks"', q)

    def test_cmd_shape(self):
        a = AgentAnalyzer(exe=r"C:\x\hermes.cmd")
        dc = build_drop(None, "x", label="y")
        dc.image_paths = [r"C:\img.png"]
        cmd = a._cmd(r"C:\q.txt", dc)
        self.assertEqual(cmd[0], r"C:\x\hermes.cmd")
        self.assertIn("--query-file", cmd)
        self.assertIn("--image", cmd)
        self.assertIn("-Q", cmd)

    def test_facade_falls_back_without_agent(self):
        a = Analyzer(agent_enabled=False,
                     agent=AgentAnalyzer(exe=None))
        tasks, note = a.analyze(build_drop(None, "- 今天交作业"))
        self.assertGreaterEqual(len(tasks), 1)
        self.assertIn("规则", note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
