"""Task model + JSON-backed todo store with progress tracking."""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field

VALID_PRIORITY = ("high", "med", "low")

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
    due: str = ""
    priority: str = "med"
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
    return {
        "title": title,
        "detail": detail[:500],
        "source": str(raw.get("source") or "")[:120],
        "due": str(raw.get("due") or "").strip()[:40],
        "priority": priority,
        "progress": _clamp_progress(raw.get("progress", 0)),
    }


class TodoStore:
    def __init__(self, path: str):
        self.path = path
        self.tasks: list[Task] = []
        self.load()

    # ---------- persistence ----------
    def load(self) -> None:
        self.tasks = []
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            self._backup_corrupt()
            return
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
        norm = normalize_task({**{"title": task.title, "detail": task.detail,
                                  "source": task.source, "due": task.due,
                                  "priority": task.priority,
                                  "progress": task.progress}, **fields})
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
        return self.update(task_id, progress=_clamp_progress(progress))

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

    def find_by_title(self, title: str) -> Task | None:
        key = title.strip().lower()
        for t in self.tasks:
            if t.title.strip().lower() == key:
                return t
        return None

    def stats(self) -> dict:
        total = len(self.tasks)
        done = sum(1 for t in self.tasks if t.done)
        if total:
            overall = round(sum(t.progress for t in self.tasks) / total)
        else:
            overall = 0
        return {
            "total": total,
            "done": done,
            "in_progress": sum(1 for t in self.tasks if 0 < t.progress < 100),
            "pending": total - done - sum(1 for t in self.tasks if 0 < t.progress < 100),
            "overall": overall,
        }
