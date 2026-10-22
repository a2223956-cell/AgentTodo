"""``status.json`` heartbeat — how outside readers (the Hermes desktop chip)
learn the app's live state without spawning PowerShell/WMI.

Contract: the app is the only writer.  It rewrites the file atomically on every
state change plus a slow heartbeat, so a reader only needs one ``open()`` and
never a process scan.  Liveness is decided from the heartbeat's age, with the
PID from ``lock.pid`` as a secondary signal (a crashed app stops beating).
"""
from __future__ import annotations

import json
import os
import time

NAME = "status.json"
VERSION = 1
# A reader treats the heartbeat as live within this window.  The app beats
# every 5 s, so 20 s tolerates a suspended/hung UI thread without lying forever.
STALE_AFTER = 20.0


def path_for(data_dir: str) -> str:
    return os.path.join(data_dir, NAME)


def write(data_dir: str, payload: dict) -> None:
    """Atomically replace status.json.  Never raises: status is best-effort."""
    data = dict(payload)
    data["version"] = VERSION
    data["updated_at"] = time.time()
    path = path_for(data_dir)
    tmp = path + ".tmp"
    try:
        os.makedirs(data_dir, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def read(data_dir: str) -> dict:
    """The last status payload, or {} when missing/unreadable."""
    try:
        with open(path_for(data_dir), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def age(data: dict) -> float | None:
    try:
        return max(0.0, time.time() - float(data.get("updated_at") or 0))
    except (TypeError, ValueError):
        return None


def is_fresh(data: dict, stale_after: float = STALE_AFTER) -> bool:
    got = age(data)
    return got is not None and data.get("updated_at") is not None and got <= stale_after
