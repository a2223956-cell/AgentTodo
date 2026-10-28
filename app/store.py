"""Task model + JSON-backed todo store with progress tracking."""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date

from .dates import URGENCY_RANK, parse_due, urgency

VALID_PRIORITY = ("high", "med", "low")

# Completed tasks leave todos.json and land here instead — the list shows what
# is still open, the archive keeps the history (and feeds undo + the "今日完成"
# counter).  Kept next to todos.json, so a --data-dir keeps its own history.
ARCHIVE_NAME = "completed.json"
ARCHIVE_KEEP = 5000

# Per-task display mode:
#   "check" — binary: 只需展示/切换 完成·未完成(不出现百分比)
#   "pct"   — quantifiable goal: 百分比进度条(0-100)
VALID_MODE = ("check", "pct")
_MODE_ALIAS = {
    "check": "check", "binary": "check", "done": "check", "bool": "check",
    "todo": "check", "simple": "check", "oneoff": "check",
    "完成": "check", "勾选": "check", "二值": "check", "是否完成": "check",
    "pct": "pct", "percent": "pct", "percentage": "pct", "progress": "pct",
    "quant": "pct", "quantified": "pct", "numeric": "pct", "ratio": "pct",
    "百分比": "pct", "进度": "pct", "量化": "pct",
}

_PRIORITY_ALIAS = {
    "high": "high", "hi": "high", "urgent": "high", "p0": "high", "p1": "high",
    "高": "high", "紧急": "high", "重要": "high", "高优": "high",
    "med": "med", "medium": "med", "normal": "med", "mid": "med", "p2": "med",
    "中": "med", "普通": "med", "一般": "med",
    "low": "low", "lo": "low", "p3": "low",
    "低": "low", "不急": "low", "低优": "low",
}


@dataclass
class Task:
    id: str
    title: str
    detail: str = ""
    source: str = ""
    due: str = ""          # 原文, e.g. "明天发货（2026-10-22）"
    due_at: str = ""       # 解析后的 ISO 日期, "" = 解析不出
    customer: str = ""
    order_date: str = ""
    amount: str = ""
    priority: str = "med"
    mode: str = "check"
    progress: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def done(self) -> bool:
        return self.progress >= 100


def _clamp_progress(v) -> int:
    try:
        p = int(round(float(v)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, p))


def _norm_mode(value) -> str | None:
    """Canonical display mode, or None when absent/unrecognised (→ infer later)."""
    if value is None:
        return None
    key = str(value).strip().lower()
    if not key:
        return None
    return _MODE_ALIAS.get(key, _MODE_ALIAS.get(key[:5]))


def normalize_task(raw: dict) -> dict:
    """Coerce an agent- or user-produced dict into a valid task record."""
    title = str(raw.get("title") or "").strip()
    detail = str(raw.get("detail") or "").strip()
    if not title:
        title = detail[:40] or "未命名任务"
    title = re.sub(r"\s+", " ", title)[:80]
    priority = str(raw.get("priority") or "med").strip().lower()
    priority = _PRIORITY_ALIAS.get(priority, _PRIORITY_ALIAS.get(priority[:4], "med"))
    if priority not in VALID_PRIORITY:
        priority = "med"
    progress = _clamp_progress(raw.get("progress", 0))
    mode = _norm_mode(raw.get("mode"))
    if mode is None:
        # Legacy records carry no mode. A mid-progress value is by definition a
        # quantified goal; 0/100 is a plain done/not-done item.
        mode = "pct" if 0 < progress < 100 else "check"
    # Order-style documents (PO/PI/合同) get their key facts surfaced in the row.
    customer = str(raw.get("customer") or raw.get("client") or "").strip()[:40]
    order_date = str(raw.get("order_date") or raw.get("orderdate")
                     or raw.get("order_date_text") or "").strip()[:40]
    amount = str(raw.get("amount") or raw.get("total") or "").strip()[:40]
    # Keep the original wording for display, and a parsed ISO date for sorting
    # and reminders. An unparseable due stays text-only — never a failed write.
    due = str(raw.get("due") or "").strip()[:40]
    due_at = str(raw.get("due_at") or "").strip()[:10]
    if not due_at:
        parsed = parse_due(due)
        due_at = parsed.isoformat() if parsed else ""
    return {
        "title": title,
        "detail": detail[:500],
        "source": str(raw.get("source") or "")[:120],
        "due": due,
        "due_at": due_at,
        "customer": customer,
        "order_date": order_date,
        "amount": amount,
        "priority": priority,
        "mode": mode,
        "progress": progress,
    }


class TodoStore:
    def __init__(self, path: str, archive_path: str | None = None):
        self.path = path
        self.archive_path = archive_path or os.path.join(
            os.path.dirname(path) or ".", ARCHIVE_NAME)
        self.tasks: list[Task] = []
        self.archive: list[dict] = []
        self._load_archive()
        if self.load():
            self._snapshot()

    # ---------- persistence ----------
    def load(self) -> bool:
        """Read the file. False = it existed but was unreadable (backed up)."""
        self.tasks = []
        if not os.path.exists(self.path):
            return True
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            self._backup_corrupt()
            return False
        for raw in data if isinstance(data, list) else data.get("tasks", []):
            try:
                norm = normalize_task(raw)
                self.tasks.append(Task(
                    id=str(raw.get("id") or uuid.uuid4().hex[:8]),
                    created_at=float(raw.get("created_at") or time.time()),
                    updated_at=float(raw.get("updated_at") or time.time()),
                    **norm,
                ))
            except (TypeError, ValueError):
                continue
        return True

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([asdict(t) for t in self.tasks], f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def _backup_corrupt(self) -> None:
        """Keep the unreadable file: the next save() would otherwise destroy it."""
        try:
            shutil.copy2(self.path, self.path + ".bak")
        except OSError:
            pass

    def _snapshot(self) -> None:
        """One dated copy per day, newest 7 kept.

        ``.bak`` is a single overwritten slot, so it can only ever answer "what
        did the last corrupt file look like" — this is the actual history.
        """
        try:
            if not os.path.exists(self.path):
                return
            bdir = os.path.join(os.path.dirname(self.path) or ".", "backups")
            os.makedirs(bdir, exist_ok=True)
            dest = os.path.join(bdir, f"todos-{time.strftime('%Y%m%d')}.json")
            if not os.path.exists(dest):
                shutil.copy2(self.path, dest)
            names = sorted(n for n in os.listdir(bdir)
                           if n.startswith("todos-") and n.endswith(".json"))
            for stale in names[:-7]:
                os.remove(os.path.join(bdir, stale))
        except OSError:
            pass

    # ---------- archive (completed tasks) ----------
    def _load_archive(self) -> None:
        try:
            with open(self.archive_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            self.archive = []
            return
        if isinstance(data, dict):
            data = data.get("tasks", [])
        self.archive = [r for r in (data if isinstance(data, list) else [])
                        if isinstance(r, dict)][-ARCHIVE_KEEP:]

    def save_archive(self) -> None:
        """Only happens on completion/undo — never on a heartbeat."""
        try:
            os.makedirs(os.path.dirname(self.archive_path) or ".", exist_ok=True)
            tmp = self.archive_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.archive, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.archive_path)
        except OSError:
            pass

    def archive_tasks(self, tasks: list[Task], prev_progress: dict | None = None,
                      completed_at=None) -> list[dict]:
        """Move tasks out of the live list and into completed.json.

        ``completed_at`` may be one timestamp or a per-task-id mapping (legacy
        tasks were completed on the day they were last edited).  ``prev_progress``
        is remembered so undo can put the task back where it was.
        """
        prev_progress = prev_progress or {}
        records = []
        for t in tasks:
            when = (completed_at.get(t.id, t.updated_at)
                    if isinstance(completed_at, dict)
                    else (t.updated_at if completed_at is None else completed_at))
            rec = asdict(t)
            rec["completed_at"] = float(when or time.time())
            rec["prev_progress"] = int(prev_progress.get(t.id, 0))
            records.append(rec)
        if not records:
            return records
        ids = {r["id"] for r in records}
        self.tasks = [t for t in self.tasks if t.id not in ids]
        self.archive.extend(records)
        self.archive = self.archive[-ARCHIVE_KEEP:]
        self.save()
        self.save_archive()
        return records

    def pop_archive(self) -> dict | None:
        """Undo: take the newest completion record off the archive."""
        if not self.archive:
            return None
        rec = self.archive.pop()
        self.save_archive()
        return rec

    def restore(self, rec: dict) -> Task:
        """Re-add an archived record as an open task (undo)."""
        data = {k: rec.get(k) for k in (
            "title", "detail", "source", "due", "due_at", "customer",
            "order_date", "amount", "priority", "mode")}
        norm = normalize_task(data)
        norm["progress"] = _clamp_progress(rec.get("prev_progress", 0))
        task = Task(
            id=str(rec.get("id") or uuid.uuid4().hex[:8]),
            created_at=float(rec.get("created_at") or time.time()),
            updated_at=time.time(),
            **norm,
        )
        self.tasks.append(task)
        self.save()
        return task

    def completed_today(self, day: date | None = None) -> int:
        """How many tasks were completed on ``day`` (local time), archive only."""
        want = (day or date.today()).isoformat()
        n = 0
        for rec in self.archive:
            try:
                got = date.fromtimestamp(float(rec.get("completed_at") or 0)).isoformat()
            except (TypeError, ValueError, OSError):
                continue
            if got == want:
                n += 1
        return n

    # ---------- mutations ----------
    def add(self, raw: dict) -> Task:
        norm = normalize_task(raw)
        task = Task(id=uuid.uuid4().hex[:8], **norm)
        self.tasks.append(task)
        self.save()
        return task

    def add_many(self, raws: list[dict]) -> list[Task]:
        out = []
        for raw in raws:
            norm = normalize_task(raw)
            out.append(Task(id=uuid.uuid4().hex[:8], **norm))
        self.tasks.extend(out)
        self.save()
        return out

    def update(self, task_id: str, **fields) -> Task | None:
        task = self.get(task_id)
        if not task:
            return None
        current = {"title": task.title, "detail": task.detail,
                   "source": task.source, "due": task.due, "due_at": task.due_at,
                   "customer": task.customer, "order_date": task.order_date,
                   "amount": task.amount, "priority": task.priority,
                   "mode": task.mode, "progress": task.progress}
        current.update(fields)
        if "due" in fields:
            # Re-derive the parsed date from the NEW text instead of keeping
            # the stale one that travelled along in `current`.
            current.pop("due_at", None)
        norm = normalize_task(current)
        for k, v in norm.items():
            setattr(task, k, v)
        task.updated_at = time.time()
        self.save()
        return task

    def delete(self, task_id: str) -> bool:
        before = len(self.tasks)
        self.tasks = [t for t in self.tasks if t.id != task_id]
        if len(self.tasks) != before:
            self.save()
            return True
        return False

    def set_progress(self, task_id: str, progress: int) -> Task | None:
        """Set progress. A mid value implies a quantified goal → mode becomes pct."""
        task = self.get(task_id)
        if not task:
            return None
        p = _clamp_progress(progress)
        mode = "pct" if (task.mode == "pct" or 0 < p < 100) else "check"
        return self.update(task_id, progress=p, mode=mode)

    def set_mode(self, task_id: str, mode: str) -> Task | None:
        """Switch a task between binary (check) and percentage (pct) tracking.

        The stored value is never rewritten: snapping a 49% goal to 0/100 on a
        mode switch was silent data loss.  Mode only decides how the row is
        drawn and which control edits it.
        """
        task = self.get(task_id)
        if not task:
            return None
        return self.update(task_id, mode=_norm_mode(mode) or "check")

    def toggle_done(self, task_id: str) -> Task | None:
        task = self.get(task_id)
        if not task:
            return None
        return self.set_progress(task_id, 0 if task.done else 100)

    def clear_done(self) -> int:
        before = len(self.tasks)
        self.tasks = [t for t in self.tasks if not t.done]
        removed = before - len(self.tasks)
        if removed:
            self.save()
        return removed

    # ---------- queries ----------
    def get(self, task_id: str) -> Task | None:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def all(self) -> list[Task]:
        return list(self.tasks)

    def ordered(self, today=None) -> list[Task]:
        """Display order: active first, then urgency → due date → priority → age.

        Overdue and due-today rows float to the top because they are the only
        ones that carry a consequence today; undated rows fall back to
        priority, then insertion order (stable, so the list doesn't shuffle).
        """
        prio = {"high": 0, "med": 1, "low": 2}

        def key(t: Task):
            return (t.done,
                    URGENCY_RANK.get(urgency(t.due_at, today), 4),
                    t.due_at or "9999-12-31",
                    prio.get(t.priority, 1),
                    t.created_at)

        return sorted(self.tasks, key=key)

    def find_by_title(self, title: str) -> Task | None:
        key = title.strip().lower()
        for t in self.tasks:
            if t.title.strip().lower() == key:
                return t
        return None

    def stats(self) -> dict:
        total = len(self.tasks)
        done = sum(1 for t in self.tasks if t.done)
        in_progress = sum(1 for t in self.tasks if 0 < t.progress < 100)
        if total:
            overall = round(sum(t.progress for t in self.tasks) / total)
        else:
            overall = 0
        open_buckets = [urgency(t.due_at) for t in self.tasks if not t.done]
        return {
            "total": total,
            "done": done,
            "done_today": self.completed_today(),
            "in_progress": in_progress,
            "pending": total - done - in_progress,
            "overall": overall,
            "overdue": open_buckets.count("overdue"),
            "due_today": open_buckets.count("today"),
        }
