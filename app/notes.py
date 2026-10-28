"""Append completed tasks to the day's Obsidian note.

A completed todo is archived (``completed.json``) and, when enabled, written
into ``<vault>/<daily_dir>/<YYYY-MM-DD>.md`` under the ``## ✅ 今日完成``
section as a ``- [x] HH:MM 标题`` line.

Rules that keep this safe on a vault the user also edits by hand:

* the note is read, edited in place, and written back atomically
  (``.tmp`` + ``os.replace``) — never truncated-then-written;
* the existing line endings are preserved (Obsidian may write CRLF);
* a line that is already present is not added twice (``log()`` is idempotent);
* every failure returns ``{"ok": False, "error": ...}`` instead of raising:
  a missing vault must never take the todo app down.
"""
from __future__ import annotations

import os
import re
import time
from datetime import date

DEFAULT_VAULT = r"E:\Harry 知识库\Harry 知识库"
DEFAULT_DAILY_DIR = "Daily Notes"
DEFAULT_SECTION = "✅ 今日完成"

_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _plain(text: str) -> str:
    """Heading text without decoration: ``✅ 今日完成`` → ``今日完成``."""
    return re.sub(r"^[^\w\u4e00-\u9fff]+", "", str(text or "").strip())


def _read(path: str) -> tuple[str, str]:
    """Return (text, dominant newline). ``newline=''`` keeps CRLF intact."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    return text, ("\r\n" if "\r\n" in text else "\n")


def _write(path: str, text: str) -> None:
    tmp = path + ".agenttodo.tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def build_line(title: str, when: float, customer: str = "", due: str = "",
               source: str = "") -> str:
    """One markdown task line. The title is flattened — a newline in it would
    split the entry into a stray paragraph."""
    title = " ".join(str(title or "").split())
    stamp = time.strftime("%H:%M", time.localtime(when))
    bits = []
    if customer:
        bits.append(f"客户 {customer}")
    if due:
        bits.append(f"截止 {due}")
    if source:
        bits.append(f"来源 {source}")
    line = f"- [x] {stamp} {title}"
    return line + (" — " + " · ".join(bits) if bits else "")


class DailyNotes:
    """Minimal Obsidian daily-note writer (one section, append-only)."""

    def __init__(self, vault: str = DEFAULT_VAULT,
                 daily_dir: str = DEFAULT_DAILY_DIR,
                 section: str = DEFAULT_SECTION):
        self.vault = str(vault or "")
        self.daily_dir = str(daily_dir or "")
        self.section = str(section or DEFAULT_SECTION)

    # ------------------------------------------------------------- paths
    def dir_path(self) -> str:
        return os.path.join(self.vault, self.daily_dir) if self.daily_dir else self.vault

    def note_path(self, day: date) -> str:
        return os.path.join(self.dir_path(), f"{day.isoformat()}.md")

    def available(self) -> bool:
        return bool(self.vault) and os.path.isdir(self.vault)

    # ------------------------------------------------------------- sections
    def _find_section(self, lines: list[str]) -> int | None:
        want = _plain(self.section)
        for i, line in enumerate(lines):
            s = line.strip()
            if s.startswith("## ") and _plain(s[3:]) == want:
                return i
        return None

    @staticmethod
    def _block_end(lines: list[str], heading: int) -> int:
        """Index just past a section's body (next heading or ``---`` rule)."""
        end = len(lines)
        for j in range(heading + 1, len(lines)):
            s = lines[j].strip()
            if s.startswith("#") or s.startswith("---"):
                end = j
                break
        while end > heading + 1 and not lines[end - 1].strip():
            end -= 1      # keep the blank line that separates blocks
        return end

    def _insert(self, lines: list[str], line: str) -> list[str]:
        idx = self._find_section(lines)
        if idx is not None:
            at = self._block_end(lines, idx)
            lines.insert(at, line)
            return lines
        # Section missing: put it after 今日工作 when that block exists, so the
        # note keeps the usual 今日工作 → 今日完成 order.
        pos = None
        for i, ln in enumerate(lines):
            s = ln.strip()
            if s.startswith("## ") and _plain(s[3:]) == "今日工作":
                pos = self._block_end(lines, i)
                break
        if pos is None:
            pos = len(lines)
            while pos > 0 and not lines[pos - 1].strip():
                pos -= 1
        block = []
        if pos > 0 and lines[pos - 1].strip():
            block.append("")
        block += [f"## {self.section}", line]
        if pos < len(lines):
            block.append("")
        lines[pos:pos] = block
        return lines

    # ------------------------------------------------------------- actions
    def log(self, title: str, when: float | None = None, customer: str = "",
            due: str = "", source: str = "") -> dict:
        """Append one completion line. Never raises."""
        when = time.time() if when is None else float(when)
        title = " ".join(str(title or "").split())
        if not title:
            return {"ok": False, "path": "", "line": "", "error": "空标题"}
        if not self.available():
            return {"ok": False, "path": "", "line": "",
                    "error": f"Obsidian vault 不存在: {self.vault or '(未配置)'}"}
        day = date.fromtimestamp(when)
        path = self.note_path(day)
        line = build_line(title, when, customer, due, source)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if os.path.exists(path):
                text, nl = _read(path)
                lines = text.splitlines()
                if line in lines:
                    return {"ok": True, "path": path, "line": line, "dup": True}
            else:
                nl = "\n"
                header = f"# {day.isoformat()} ({_WEEKDAYS[day.weekday()]})\n"
                lines = header.splitlines()
            lines = self._insert(lines, line)
            out = nl.join(lines)
            if not out.endswith(nl):
                out += nl
            _write(path, out)
        except OSError as e:
            return {"ok": False, "path": path, "line": line, "error": f"写入失败: {e}"}
        return {"ok": True, "path": path, "line": line}

    def remove_line(self, path: str, line: str) -> bool:
        """Undo helper: drop one exact line (no-op when file/line is gone)."""
        if not path or not line or not os.path.exists(path):
            return False
        try:
            text, nl = _read(path)
            lines = text.splitlines()
            kept = [ln for ln in lines if ln != line]
            if len(kept) == len(lines):
                return False
            out = nl.join(kept)
            if text.endswith(("\n", "\r")):
                out += nl
            _write(path, out)
            return True
        except OSError:
            return False
