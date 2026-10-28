"""status.json contract: the app writes it, the desktop chip reads it.

These tests are the cross-process contract — if a key or the freshness rule
changes here, ``plugins/agenttodo-switch/dashboard/plugin_api.py`` must follow.
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import status  # noqa: E402

TMP_ROOT = tempfile.mkdtemp(prefix="agenttodo_status_")


def _tmpdir() -> str:
    return tempfile.mkdtemp(dir=TMP_ROOT)


class TestStatusFile(unittest.TestCase):
    def test_write_read_roundtrip(self):
        d = _tmpdir()
        status.write(d, {"running": True, "pid": 4321, "total": 3, "analyzing": False})
        got = status.read(d)
        self.assertTrue(got["running"])
        self.assertEqual(got["pid"], 4321)
        self.assertEqual(got["total"], 3)
        self.assertEqual(got["version"], status.VERSION)
        self.assertIn("updated_at", got)
        self.assertTrue(status.is_fresh(got))

    def test_missing_or_broken_file(self):
        d = _tmpdir()
        self.assertEqual(status.read(d), {})
        with open(status.path_for(d), "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(status.read(d), {})
        self.assertFalse(status.is_fresh({}))

    def test_freshness_window(self):
        d = _tmpdir()
        status.write(d, {"running": True})
        got = status.read(d)
        got["updated_at"] = time.time() - (status.STALE_AFTER + 1)
        self.assertFalse(status.is_fresh(got))
        # a payload without a timestamp can never be fresh
        self.assertFalse(status.is_fresh({"running": True}))

    def test_write_is_atomic_no_tmp_left(self):
        d = _tmpdir()
        status.write(d, {"running": True})
        status.write(d, {"running": False})
        self.assertEqual(sorted(os.listdir(d)), [status.NAME])
        self.assertFalse(status.read(d)["running"])


class TestAppPayload(unittest.TestCase):
    """The payload keys the chip relies on, without booting the GUI."""

    def test_payload_keys(self):
        from types import SimpleNamespace

        from app.app import App
        from app.analyzer import Analyzer, AgentAnalyzer
        from app.store import TodoStore

        d = _tmpdir()
        fake = SimpleNamespace(
            dir=d,
            store=TodoStore(os.path.join(d, "todos.json")),
            analyzer=Analyzer(agent_enabled=False, agent=AgentAnalyzer(exe=None)),
            _busy=True, _pending=["x"],
            _last_note="已添加 2 条任务", _last_error="")
        payload = App._status_payload(fake)  # type: ignore[arg-type]
        for key in ("running", "pid", "app_dir", "analyzing", "queue", "total",
                    "done", "done_today", "overall", "overdue", "due_today", "ai",
                    "last_note", "last_error"):
            self.assertIn(key, payload)
        self.assertTrue(payload["running"])
        self.assertTrue(payload["analyzing"])
        self.assertEqual(payload["queue"], 1)
        self.assertFalse(payload["ai"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
