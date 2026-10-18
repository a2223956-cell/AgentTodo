"""Single-instance guard: a PID lock file inside the data dir.

Two running instances both rewrite todos.json wholesale, so the last writer
silently erases the other's changes — refuse to start a second copy instead.
"""
from __future__ import annotations

import atexit
import ctypes
import os
import sys

LOCK_NAME = "lock.pid"
_stored_path: str | None = None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    try:
        # OpenProcess + GetExitCodeProcess: os.kill(pid, 0) would TERMINATE a
        # process on Windows, never use it for liveness.
        h = ctypes.windll.kernel32.OpenProcess(0x1000, 0, pid)  # QUERY_LIMITED
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    except Exception:
        return False


def acquire(data_dir: str) -> bool:
    """Take the lock for data_dir. False = another live instance holds it."""
    global _stored_path
    path = os.path.join(data_dir, LOCK_NAME)
    pid = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            pid = int(f.read().strip() or "0")
    except (OSError, ValueError):
        pid = 0
    if pid and pid != os.getpid() and _pid_alive(pid):
        return False
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError:
        return True  # unwritable data dir breaks persistence anyway; don't block
    _stored_path = path
    atexit.register(release)
    return True


def release() -> None:
    global _stored_path
    if not _stored_path:
        return
    path, _stored_path = _stored_path, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            mine = f.read().strip() == str(os.getpid())
    except OSError:
        return
    if mine:
        try:
            os.remove(path)  # outside the with: Windows cannot delete open files
        except OSError:
            pass


def notify_duplicate() -> None:
    """Tell the user why startup was refused (message box where possible)."""
    msg = ("AgentTodo 已经在运行了。\n\n"
           "如需打开面板,请把鼠标移到屏幕右侧的悬浮球上。\n"
           "(如果确认没有在运行,请删除数据目录下的 lock.pid 后重试)")
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(0, msg, "AgentTodo", 0x40)
            return
        except Exception:
            pass
    print(msg)
