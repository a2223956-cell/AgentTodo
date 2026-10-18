"""Regression tests for the compatibility fixes (no GUI required)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import lock  # noqa: E402
from app.analyzer import AgentAnalyzer, _cmd_quote, _kill_tree  # noqa: E402
from app.screen import clamp_xy  # noqa: E402
from app.store import TodoStore  # noqa: E402


class TestCorruptStore(unittest.TestCase):
    """Fix 6: a corrupt todos.json is backed up, not silently overwritten."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agenttodo_test_")
        self.path = os.path.join(self.tmp, "todos.json")

    def test_corrupt_file_backed_up_before_overwrite(self):
        garbage = "{ not valid json"
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(garbage)
        store = TodoStore(self.path)
        self.assertEqual(store.tasks, [])
        bak = self.path + ".bak"
        self.assertTrue(os.path.exists(bak))
        with open(bak, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), garbage)
        store.add({"title": "x"})
        with open(bak, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), garbage)  # still preserved after save
        with open(self.path, "r", encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)), 1)


class TestScreenClamp(unittest.TestCase):
    """Fix 1: positioning math keeps windows inside the virtual screen."""

    def test_clamp_inside(self):
        self.assertEqual(clamp_xy(100, 100, 200, 100, (0, 0, 1000, 800)),
                         (100, 100))

    def test_clamp_beyond_edges(self):
        self.assertEqual(clamp_xy(1500, 100, 200, 100, (0, 0, 1000, 800)),
                         (800, 100))
        self.assertEqual(clamp_xy(-50, -50, 200, 100, (0, 0, 1000, 800)),
                         (0, 0))

    def test_clamp_negative_origin_monitor(self):
        # a monitor left of the primary has negative coordinates
        self.assertEqual(clamp_xy(-5000, -5000, 200, 100, (-1920, 0, 0, 1080)),
                         (-1920, 0))

    def test_clamp_oversized_window_pins_to_origin(self):
        self.assertEqual(clamp_xy(0, 0, 2000, 900, (0, 0, 1000, 800)), (0, 0))


class TestSingleInstance(unittest.TestCase):
    """Fix 3: a second live instance is refused; stale locks are reclaimed."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agenttodo_test_")
        self.lockfile = os.path.join(self.tmp, "lock.pid")

    def test_acquire_release_roundtrip(self):
        self.assertTrue(lock.acquire(self.tmp))
        self.assertTrue(os.path.exists(self.lockfile))
        lock.release()
        self.assertFalse(os.path.exists(self.lockfile))

    def test_refuses_when_other_instance_alive(self):
        proc = subprocess.Popen(["cmd", "/c", "ping", "-n", "30", "127.0.0.1"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        try:
            with open(self.lockfile, "w", encoding="utf-8") as f:
                f.write(str(proc.pid))
            self.assertFalse(lock.acquire(self.tmp))
        finally:
            _kill_tree(proc)

    def test_stale_lock_is_reclaimed(self):
        proc = subprocess.Popen(["cmd", "/c", "exit"])
        proc.wait(timeout=10)   # pid is now dead
        with open(self.lockfile, "w", encoding="utf-8") as f:
            f.write(str(proc.pid))
        self.assertTrue(lock.acquire(self.tmp))
        lock.release()


@unittest.skipUnless(sys.platform == "win32", "cmd.exe semantics")
class TestCmdMetacharSafety(unittest.TestCase):
    """Fix 4/5: args survive cmd.exe metacharacters; timeouts kill the tree."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agenttodo_test_")
        self.bat = os.path.join(self.tmp, "echo_args.bat")
        with open(self.bat, "w", encoding="utf-8") as f:
            f.write("@echo off\r\necho GOT:%*\r\n")

    def test_cmd_quote(self):
        self.assertEqual(_cmd_quote("a&b"), '"a&b"')
        self.assertEqual(_cmd_quote('say "hi"'), '"say ""hi"""')

    def test_direct_exe_keeps_metachar_args(self):
        proc = AgentAnalyzer(exe=sys.executable, timeout=30)._exec(
            [sys.executable, "-c", "import sys; print(sys.argv[1])", "a&b"])
        self.assertIn("a&b", proc.stdout)

    def test_batch_shim_keeps_metachar_args(self):
        proc = AgentAnalyzer(exe=self.bat, timeout=30)._run(
            [self.bat, "a&b", "x y"])
        self.assertIsNotNone(proc)
        self.assertIn("a&b", proc.stdout)
        self.assertIn("x y", proc.stdout)

    def test_fallback_quoted_command_line(self):
        cmdline = " ".join(_cmd_quote(a) for a in [self.bat, "a&b", "x y"])
        proc = AgentAnalyzer(exe=self.bat, timeout=30)._exec_shell(cmdline)
        self.assertIn("a&b", proc.stdout)
        self.assertIn("x y", proc.stdout)

    def test_timeout_kills_process_tree(self):
        a = AgentAnalyzer(exe="cmd", timeout=2)
        with self.assertRaises(subprocess.TimeoutExpired):
            a._exec(["cmd", "/c", "ping", "-n", "60", "127.0.0.1"])
        left = subprocess.run(["tasklist", "/FI", "IMAGENAME eq PING.EXE", "/NH"],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace").stdout
        self.assertNotIn("PING.EXE", left)


if __name__ == "__main__":
    unittest.main()
