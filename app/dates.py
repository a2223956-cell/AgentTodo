"""Parse the free-text ``due`` field into a real date, plus urgency buckets.

The agent (and the rule parser) emit due dates as human text — "明天",
"10月20日", "明天发货（2026-10-22）".  That string is fine to *show* but
useless to sort or remind on, so every task keeps both: ``due`` (原文) and
``due_at`` (ISO date).  Parsing is best-effort by design — an unparseable
due must never block a task from being stored.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

# Rank for display ordering: sooner/more urgent first, undated last.
URGENCY_RANK = {"overdue": 0, "today": 1, "soon": 2, "later": 3, "": 4}

_ISO_RE = re.compile(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?")
_MD_CN_RE = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?")
# 10/20, 10-20 — but NOT 10/2026 (an order number: the trailing \d guard wins).
_MD_SLASH_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[/\-]\s*(\d{1,2})(?!\d)")

# Longest first: 大后天 contains 后天, 后天 contains 天.
_REL_WORDS = (
    ("大后天", 3), ("后天", 2), ("明天", 1), ("明日", 1),
    ("今天", 0), ("今日", 0), ("昨天", -1), ("前天", -2),
)
_IN_N_DAYS_RE = re.compile(r"(\d{1,3})\s*天\s*(?:之?后|以后|之内|以内)")

_WEEKDAY_WORDS = {
    "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
    "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6,
}
_WEEK_RE = re.compile(
    r"(下下周|下个周|下周|本周|这周|这个周|周|星期|礼拜)\s*([一二三四五六日天1-7])")

_MONTH_END_RE = re.compile(r"月[底末]")
_NEXT_MONTH_RE = re.compile(r"下个?月")
_YEAR_END_RE = re.compile(r"年[底前]")

_EN_RE = {
    "today": 0, "tomorrow": 1, "day after tomorrow": 2, "yesterday": -1,
}


def _today(today: date | None = None) -> date:
    return today or date.today()


def _as_date(value) -> date | None:
    """Tolerant ISO-date reader (``2026-10-22`` only; anything else → None)."""
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _weekday_of(ch: str) -> int | None:
    return _WEEKDAY_WORDS.get(ch)


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _pick_year(month: int, day: int, today: date) -> date | None:
    """A due date without a year belongs to the nearest future occurrence."""
    got = _safe_date(today.year, month, day)
    if got is None:
        return None
    if (today - got).days > 180:      # e.g. "1月5日" said in November
        got = _safe_date(today.year + 1, month, day) or got
    return got


def parse_due(text: str, today: date | None = None) -> date | None:
    """Best-effort date out of a due string; None when nothing is recognizable."""
    raw = str(text or "").strip()
    if not raw:
        return None
    today = _today(today)

    m = _ISO_RE.search(raw)
    if m:
        got = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if got:
            return got

    m = _MD_CN_RE.search(raw)
    if m:
        got = _pick_year(int(m.group(1)), int(m.group(2)), today)
        if got:
            return got

    m = _MD_SLASH_RE.search(raw)
    if m:
        got = _pick_year(int(m.group(1)), int(m.group(2)), today)
        if got:
            return got

    for word, delta in _REL_WORDS:
        if word in raw:
            return today + timedelta(days=delta)

    m = _IN_N_DAYS_RE.search(raw)
    if m:
        return today + timedelta(days=int(m.group(1)))

    m = _WEEK_RE.search(raw)
    if m:
        prefix, ch = m.group(1), m.group(2)
        target = _weekday_of(ch)
        if target is not None:
            if prefix == "下下周":
                week_shift = 2
            elif prefix in ("下个周", "下周"):
                week_shift = 1
            else:
                week_shift = 0
            monday = today - timedelta(days=today.weekday())
            got = monday + timedelta(weeks=week_shift, days=target)
            # A bare "周三" means the NEXT Wednesday; an explicit 本周 may be past.
            if week_shift == 0 and prefix in ("周", "星期", "礼拜") and got < today:
                got += timedelta(days=7)
            return got

    if _MONTH_END_RE.search(raw):
        last = calendar.monthrange(today.year, today.month)[1]
        return date(today.year, today.month, last)

    if _NEXT_MONTH_RE.search(raw):
        year = today.year + (1 if today.month == 12 else 0)
        month = 1 if today.month == 12 else today.month + 1
        day = min(today.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)

    if _YEAR_END_RE.search(raw):
        return date(today.year, 12, 31)

    lowered = raw.lower()
    for word, delta in _EN_RE.items():
        if word in lowered:
            return today + timedelta(days=delta)
    if re.search(r"\bnext\s+week\b", lowered):
        return today + timedelta(days=7)

    return None


def urgency(due_at, today: date | None = None) -> str:
    """``overdue | today | soon (≤2 days) | later | ''`` for one due date."""
    got = _as_date(due_at)
    if got is None:
        return ""
    delta = (got - _today(today)).days
    if delta < 0:
        return "overdue"
    if delta == 0:
        return "today"
    if delta <= 2:
        return "soon"
    return "later"


def humanize(due_at, today: date | None = None) -> str:
    """Short label for the tooltip/badge: 逾期3天 / 今天 / 明天 / 10-28."""
    got = _as_date(due_at)
    if got is None:
        return ""
    delta = (got - _today(today)).days
    if delta < 0:
        return f"逾期{-delta}天"
    if delta == 0:
        return "今天"
    if delta == 1:
        return "明天"
    if delta == 2:
        return "后天"
    return got.strftime("%m-%d")
