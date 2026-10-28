"""AgentTodo switch backend — process control + live state for the desktop chip.

Mounted (hot, on the first request) at /api/plugins/agenttodo-switch/ by
``_PluginApiHotMount`` in hermes_cli.web_server_dashboard, so no server restart
is needed.  Trust gates: user source + name must be listed in config
``plugins.enabled`` (GHSA-mcfc-hp25-cjv7).

State comes from ``status.json``, which the app itself rewrites atomically on
every change plus a 5 s heartbeat (``E:\\AgentTodo\\app\\status.py``).  That
replaced a per-poll WMI scan — ``Get-CimInstance`` cost ~700 ms per call and the
chip polls every 8 s, i.e. ~450 powershell.exe launches per hour just to answer
"is it running".  Liveness is now the heartbeat's age, with the PID in
``lock.pid`` as the secondary signal for the window before the first beat.

Everything blocking runs in ``asyncio.to_thread`` because ``plugins.isolation``
defaults to in_process, sharing the serve event loop.
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

APP_DIR = Path(r"E:\AgentTodo")
PYW = APP_DIR / ".venv" / "Scripts" / "pythonw.exe"

# Keep in sync with app/status.py: the app beats every 5 s.
STALE_AFTER = 20.0


def data_dir() -> Path:
    """%LOCALAPPDATA%\\AgentTodoFloat — the app's const.data_dir() logic."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "AgentTodoFloat"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _pid_alive(pid: int) -> bool:
    """OpenProcess + GetExitCodeProcess: os.kill(pid, 0) TERMINATES on Windows."""
    if pid <= 0:
        return False
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, 0, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    except Exception:
        return False


def _lock_pid(d: Path) -> int:
    try:
        return int((d / "lock.pid").read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return 0


def _as_progress(value) -> int:
    """A hand-edited todos.json must never take /status down with a 500."""
    try:
        return max(0, min(100, int(float(value or 0))))
    except (TypeError, ValueError):
        return 0


def _totals_from_store(d: Path) -> dict:
    """Fallback totals straight from todos.json (app not running / no heartbeat)."""
    tasks = _read_json(d / "todos.json", [])
    if not isinstance(tasks, list):
        tasks = []
    total = done = summed = 0
    overdue = due_today = 0
    today = date.today()
    for task in tasks:
        if not isinstance(task, dict):
            continue
        total += 1
        p = _as_progress(task.get("progress"))
        summed += p
        if p >= 100:
            done += 1
            continue
        raw = str(task.get("due_at") or "")[:10]
        if not raw:
            continue
        try:
            got = date.fromisoformat(raw)
        except ValueError:
            continue
        delta = (got - today).days
        if delta < 0:
            overdue += 1
        elif delta == 0:
            due_today += 1
    return {
        "total": total,
        "done": done,
        "overall": round(summed / total) if total else 0,
        "overdue": overdue,
        "due_today": due_today,
        "done_today": _completed_today(d, today),
    }


def _completed_today(d: Path, today: date | None = None) -> int:
    """Completed tasks stamped today — the app writes ``completed.json``."""
    data = _read_json(d / "completed.json", [])
    if not isinstance(data, list):
        return 0
    want = today or date.today()
    n = 0
    for rec in data:
        if not isinstance(rec, dict):
            continue
        try:
            if date.fromtimestamp(float(rec.get("completed_at") or 0)) == want:
                n += 1
        except (TypeError, ValueError, OSError):
            continue
    return n


def _status_sync() -> dict:
    d = data_dir()
    st = _read_json(d / "status.json", {})
    if not isinstance(st, dict):
        st = {}
    try:
        age = max(0.0, time.time() - float(st.get("updated_at") or 0))
    except (TypeError, ValueError):
        age = None
    fresh = age is not None and st.get("updated_at") is not None and age <= STALE_AFTER

    pid = _lock_pid(d) or int(st.get("pid") or 0)
    alive = _pid_alive(pid)
    # A clean quit writes running=false with a fresh timestamp; a crash stops
    # the heartbeat.  Either way a live PID alone cannot claim "running".
    running = bool(st.get("running")) if fresh else alive

    totals = ({k: int(st.get(k) or 0)
               for k in ("total", "done", "done_today", "overall", "overdue", "due_today")}
              if fresh else _totals_from_store(d))
    cfg = _read_json(d / "config.json", {})
    ai = bool(st.get("ai")) if fresh and "ai" in st else (
        bool(cfg.get("agent_enabled", True)) if isinstance(cfg, dict) else None)
    return {
        "running": running,
        "pids": [pid] if alive and pid else [],
        "total": totals["total"],
        "done": totals["done"],
        "done_today": totals["done_today"],
        "progress": totals["overall"],   # kept for older chips
        "overall": totals["overall"],
        "overdue": totals["overdue"],
        "due_today": totals["due_today"],
        "ai": ai,
        "analyzing": bool(st.get("analyzing")) if fresh else False,
        "queue": int(st.get("queue") or 0) if fresh else 0,
        "last_note": str(st.get("last_note") or "") if fresh else "",
        "last_error": str(st.get("last_error") or "") if fresh else "",
        "stale": not fresh,
        "app_dir": str(APP_DIR),
        "data_dir": str(d),
    }


def _write_status(d: Path, payload: dict) -> None:
    """Only used to record a stop — the app is the writer while it lives."""
    payload = {**payload, "updated_at": time.time()}
    tmp = d / "status.json.tmp"
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, d / "status.json")
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def _start_sync() -> str | None:
    if _status_sync()["running"]:
        return None  # already running — status will say so
    if not PYW.exists():
        return f"找不到 {PYW}"
    if not (APP_DIR / "main.py").exists():
        return f"找不到 {APP_DIR}\\main.py"
    subprocess.Popen(
        [str(PYW), "main.py"],
        cwd=str(APP_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    return None


def _stop_sync() -> str | None:
    d = data_dir()
    st = _read_json(d / "status.json", {})
    st = st if isinstance(st, dict) else {}
    candidates = {_lock_pid(d), int(st.get("pid") or 0)}
    for pid in [p for p in candidates if p and _pid_alive(p)]:
        try:
            # /T also reaps the parent/child pair; a dead pid's error is fine.
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=20)
        except Exception:
            pass
    # Flip the chip immediately: a stale "running" heartbeat would otherwise
    # keep the ball green for up to STALE_AFTER seconds after a manual stop.
    totals = _totals_from_store(d)
    ai = st.get("ai")
    _write_status(d, {"running": False, "pid": 0, "analyzing": False, "queue": 0,
                      "ai": bool(ai) if ai is not None else True,
                      "last_note": "已停止", "last_error": "", **totals})
    return None  # stale lock.pid is tolerated by the app (pid-liveness check)


@router.get("/status")
async def status() -> dict:
    return await asyncio.to_thread(_status_sync)


@router.post("/start")
async def start() -> dict:
    err = await asyncio.to_thread(_start_sync)
    await asyncio.sleep(0.8)  # let the app write its first heartbeat
    out = await asyncio.to_thread(_status_sync)
    return {**out, "action": "start", "error": err}


@router.post("/stop")
async def stop() -> dict:
    err = await asyncio.to_thread(_stop_sync)
    out = await asyncio.to_thread(_status_sync)
    return {**out, "action": "stop", "error": err}
