"""Natural-language date references, resolved against a fixed "today".

Two jobs, one engine:

- ``anchor_dates(text, today)`` reads a NOTE and returns the absolute calendar
  dates it mentions ("meeting David tomorrow" -> [2026-06-10]). Used at capture
  and edit time to freeze what a relative phrase meant *then*, so it never drifts
  if you reopen the note in another timezone next week.
- ``query_ranges(text, today)`` reads a SEARCH and returns date ranges to match
  notes against ("next week" -> Mon..Sun). A point reference is a one-day range.

Design rules (see docs/DECISIONS.md and the private idea book):

- Resolve once, against the local "today" passed in, and return absolute dates.
  We never store a draggable instant, so the timezone question never arises.
- Precision over recall: only clear, unambiguous phrases are recognised. A missed
  date is invisible and harmless; a wrong one erodes trust. Month names must carry
  a day number ("Jun 15", not a bare "may"), numeric slash dates are skipped
  (1/6 is hopelessly US-vs-EU ambiguous), and times of day are left alone.
- Pure: no I/O, no clock reads. The caller supplies ``today`` so behaviour is
  deterministic and testable. Production callers pass ``date.today()``.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_WD = "|".join(_WEEKDAYS)
_MO = "|".join(sorted(_MONTHS, key=len, reverse=True))  # longest first so "june" beats "jun"


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - timedelta(days=1)).day


def _add_months(d: date, n: int) -> date:
    total = d.month - 1 + n
    year = d.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(d.day, _days_in_month(year, month)))


# Each handler takes (regex match, today) and returns a (start, end) date range.

def _h_today(m, t):
    return (t, t)


def _h_tomorrow(m, t):
    return (t + timedelta(days=1),) * 2


def _h_yesterday(m, t):
    return (t - timedelta(days=1),) * 2


def _h_day_after(m, t):
    return (t + timedelta(days=2),) * 2


def _h_day_before(m, t):
    return (t - timedelta(days=2),) * 2


def _h_weekday(m, t):
    """'monday' / 'next monday' / 'this monday' / 'last monday'."""
    qualifier = (m.group("q") or "").strip().lower()
    target = _WEEKDAYS[m.group("wd").lower()]
    this_week = _monday_of(t) + timedelta(days=target)
    if qualifier == "this":
        d = this_week
    elif qualifier == "next":
        d = this_week + timedelta(days=7)
    elif qualifier == "last":
        d = this_week - timedelta(days=7)
    else:
        # Bare weekday: the next occurrence on or after today.
        ahead = (target - t.weekday()) % 7
        d = t + timedelta(days=ahead)
    return (d, d)


def _h_week(m, t):
    monday = _monday_of(t)
    shift = {"this": 0, "next": 7, "last": -7}[m.group("q").lower()]
    start = monday + timedelta(days=shift)
    return (start, start + timedelta(days=6))


def _h_weekend(m, t):
    qualifier = (m.group("q") or "").strip().lower()
    saturday = _monday_of(t) + timedelta(days=5)
    shift = {"": 0, "this": 0, "next": 7, "last": -7}[qualifier]
    start = saturday + timedelta(days=shift)
    return (start, start + timedelta(days=1))


def _h_month(m, t):
    shift = {"this": 0, "next": 1, "last": -1}[m.group("q").lower()]
    anchor = _add_months(t.replace(day=1), shift)
    return (anchor, anchor.replace(day=_days_in_month(anchor.year, anchor.month)))


def _h_nth_of_month(m, t):
    """'14th of this month' / 'the 2nd of next month' -> that exact day."""
    shift = {"this": 0, "next": 1, "last": -1}[m.group("q").lower()]
    first = _add_months(t.replace(day=1), shift)
    day = int(m.group("d"))
    if not 1 <= day <= _days_in_month(first.year, first.month):
        return None
    d = first.replace(day=day)
    return (d, d)


def _h_numeric(m, t, order):
    """Slash/dash date with a year: 14/2/2024, 14-12-26, 6/4/26.

    Disambiguation, in order:
      1. If one component is > 12 it can only be the day (14/2 is the 14th).
      2. Otherwise it's genuinely ambiguous (6/4), so we honour the user's chosen
         ``order`` ("DMY" day-first or "MDY" month-first; set in Settings).
    Requiring a year (2- or 4-digit) keeps fractions and refs like '3/4' or '1/6'
    from being mistaken for dates.
    """
    a, b, year = int(m.group("a")), int(m.group("b")), int(m.group("y"))
    if year < 100:
        year += 2000
    if a > 12 and b <= 12:
        day, month = a, b
    elif b > 12 and a <= 12:
        day, month = b, a
    elif a <= 12 and b <= 12:
        day, month = (a, b) if order == "DMY" else (b, a)
    else:
        return None  # both > 12: not a date
    try:
        return (date(year, month, day),) * 2
    except ValueError:
        return None


_UNIT_DAYS = {"day": 1, "week": 7}


def _h_in(m, t):
    n = int(m.group("n"))
    unit = m.group("unit").lower().rstrip("s")
    d = _add_months(t, n) if unit == "month" else t + timedelta(days=n * _UNIT_DAYS[unit])
    return (d, d)


def _h_ago(m, t):
    n = int(m.group("n"))
    unit = m.group("unit").lower().rstrip("s")
    d = _add_months(t, -n) if unit == "month" else t - timedelta(days=n * _UNIT_DAYS[unit])
    return (d, d)


def _h_iso(m, t):
    try:
        d = date(int(m.group("y")), int(m.group("mo")), int(m.group("d")))
    except ValueError:
        return None
    return (d, d)


def _resolve_md(month: int, day: int, year_grp: str | None, t: date):
    if year_grp:
        year = int(year_grp)
    else:
        # No year given: assume the nearest upcoming occurrence (this year, or
        # next if it already passed). Matches how people read "see you Jun 15".
        year = t.year
    try:
        d = date(year, month, day)
    except ValueError:
        return None
    if not year_grp and d < t:
        try:
            d = date(year + 1, month, day)
        except ValueError:
            return None
    return (d, d)


def _h_month_day(m, t):
    return _resolve_md(_MONTHS[m.group("mo").lower()], int(m.group("d")), m.group("y"), t)


def _h_day_month(m, t):
    return _resolve_md(_MONTHS[m.group("mo").lower()], int(m.group("d")), m.group("y"), t)


# Ordered most-specific first. Greedy span-claiming (below) lets a longer match
# ("day after tomorrow") win over a shorter one ("tomorrow") it contains.
_PATTERNS: list[tuple[re.Pattern, object]] = [
    (re.compile(r"\b(?:the\s+)?day\s+after\s+tomorrow\b", re.I), _h_day_after),
    (re.compile(r"\b(?:the\s+)?day\s+before\s+yesterday\b", re.I), _h_day_before),
    (re.compile(r"\b(?:the\s+)?(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+of\s+(?P<q>this|next|last)\s+month\b", re.I), _h_nth_of_month),
    (re.compile(r"\b(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})\b"), _h_iso),
    (re.compile(r"\b(?P<a>\d{1,2})[/-](?P<b>\d{1,2})[/-](?P<y>\d{4}|\d{2})\b"), _h_numeric),
    (re.compile(rf"\b(?P<mo>{_MO})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<y>\d{{4}}))?\b", re.I), _h_month_day),
    (re.compile(rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mo>{_MO})\.?(?:,?\s+(?P<y>\d{{4}}))?\b", re.I), _h_day_month),
    (re.compile(r"\bin\s+(?P<n>\d{1,3})\s+(?P<unit>days?|weeks?|months?)\b", re.I), _h_in),
    (re.compile(r"\b(?P<n>\d{1,3})\s+(?P<unit>days?|weeks?|months?)\s+ago\b", re.I), _h_ago),
    (re.compile(r"\b(?:(?P<q>this|next|last)\s+)?(?P<wd>" + _WD + r")\b", re.I), _h_weekday),
    (re.compile(r"\b(?:(?P<q>this|next|last)\s+)?weekend\b", re.I), _h_weekend),
    (re.compile(r"\b(?P<q>this|next|last)\s+week\b", re.I), _h_week),
    (re.compile(r"\b(?P<q>this|next|last)\s+month\b", re.I), _h_month),
    (re.compile(r"\btoday\b", re.I), _h_today),
    (re.compile(r"\btonight\b", re.I), _h_today),
    (re.compile(r"\b(?:tomorrow|tmrw|tmr)\b", re.I), _h_tomorrow),
    (re.compile(r"\byesterday\b", re.I), _h_yesterday),
]


def _find_ranges(text: str, today: date, order: str) -> list[tuple[date, date]]:
    """All date ranges referenced in ``text``, resolved against ``today``.

    Overlapping matches are resolved greedily by (earliest start, longest span),
    so a phrase is claimed by its most specific pattern and never double-counted.
    ``order`` ("DMY"/"MDY") only matters to all-numeric dates; every other handler
    ignores it, so it is passed solely to the one that needs it.
    """
    candidates = []
    for regex, handler in _PATTERNS:
        for m in regex.finditer(text):
            candidates.append((m.start(), m.end(), handler, m))
    candidates.sort(key=lambda c: (c[0], -(c[1] - c[0])))

    claimed: list[tuple[int, int]] = []
    ranges: list[tuple[date, date]] = []
    for start, end, handler, m in candidates:
        if any(not (end <= cs or start >= ce) for cs, ce in claimed):
            continue  # overlaps an already-claimed span
        rng = handler(m, today, order) if handler is _h_numeric else handler(m, today)
        if rng is not None:
            ranges.append(rng)
            claimed.append((start, end))
    return ranges


def anchor_dates(text: str, today: date, order: str = "DMY") -> list[str]:
    """Absolute single days a note refers to, as sorted unique ISO strings.

    Only genuine point references stamp a note ("tomorrow", "Jun 15", "14th of
    this month"). Vague spans ("this month", "next week") are deliberately NOT
    anchored: pinning them to a single day (the 1st, the Monday) would show a
    misleading chip. They still widen a *search* via query_ranges below.
    """
    days = {s.isoformat() for s, e in _find_ranges(text, today, order) if s == e}
    return sorted(days)


def query_ranges(text: str, today: date, order: str = "DMY") -> list[tuple[str, str]]:
    """Date ranges a search refers to, as (start_iso, end_iso) pairs."""
    return [(s.isoformat(), e.isoformat()) for s, e in _find_ranges(text, today, order)]
