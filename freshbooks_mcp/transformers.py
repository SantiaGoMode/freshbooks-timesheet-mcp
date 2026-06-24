"""Pure data-formatting helpers: date math, unit conversion, report building.

No I/O, no API calls — everything here is deterministic and unit-tested.
All boundary math happens in the configured timezone; conversion to UTC only
occurs at the API edge (``utc_bounds`` / ``local_datetime``).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .models import TimeEntry

VALID_PERIODS = ("day", "week", "month")


def hours_to_seconds(hours: float) -> int:
    return int(round(hours * 3600))


def seconds_to_hours(seconds: int) -> float:
    return round(seconds / 3600, 2)


def resolve_range(period: str, anchor: date) -> tuple[date, date]:
    """Return the inclusive (start, end) calendar range for a period.

    - day:   (anchor, anchor)
    - week:  Monday..Sunday of the anchor's week
    - month: first..last day of the anchor's month
    """
    if period not in VALID_PERIODS:
        raise ValueError(f"period must be one of {VALID_PERIODS}, got {period!r}")
    if period == "day":
        return anchor, anchor
    if period == "week":
        monday = anchor - timedelta(days=anchor.weekday())
        return monday, monday + timedelta(days=6)
    # month
    last = calendar.monthrange(anchor.year, anchor.month)[1]
    return anchor.replace(day=1), anchor.replace(day=last)


def business_days(start: date, end: date) -> list[date]:
    """All Monday–Friday dates in the inclusive range."""
    days = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:  # 0=Mon .. 4=Fri
            days.append(cur)
        cur += timedelta(days=1)
    return days


def entries_by_day(
    entries: list[TimeEntry], tz: str | None = None
) -> dict[date, int]:
    """Sum logged seconds per local date.

    When ``tz`` is given, timezone-aware entries are converted to that zone
    before the date is taken — so an entry logged late at night (e.g. 10pm EDT
    = 2am UTC the next day) is attributed to the correct local day. Naive
    datetimes are used as-is.
    """
    zone = ZoneInfo(tz) if tz else None
    totals: dict[date, int] = {}
    for e in entries:
        dt = e.started_at
        if zone is not None and dt.tzinfo is not None:
            d = dt.astimezone(zone).date()
        else:
            d = dt.date()
        totals[d] = totals.get(d, 0) + e.duration_seconds
    return totals


@dataclass
class DayStatus:
    date: date
    weekday: str
    hours_logged: float
    status: str  # "logged" | "under" | "missing" | "future"

    def to_dict(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "weekday": self.weekday,
            "hours_logged": self.hours_logged,
            "status": self.status,
        }


def build_timesheet_report(
    start: date,
    end: date,
    entries: list[TimeEntry],
    expected_hours: float,
    today: date,
    tz: str | None = None,
) -> dict:
    """Build a structured timesheet report over the M–F days in [start, end].

    Future weekdays (> today) are reported as ``future`` and never counted as
    missing or under-logged. ``tz`` controls day-bucketing (see entries_by_day).
    """
    totals = entries_by_day(entries, tz)
    days: list[DayStatus] = []
    missing: list[str] = []
    under: list[str] = []
    total_seconds = 0

    for d in business_days(start, end):
        secs = totals.get(d, 0)
        total_seconds += secs
        hours = seconds_to_hours(secs)
        weekday = d.strftime("%A")
        if d > today:
            status = "future"
        elif secs == 0:
            status = "missing"
            missing.append(d.isoformat())
        elif hours < expected_hours:
            status = "under"
            under.append(d.isoformat())
        else:
            status = "logged"
        days.append(DayStatus(d, weekday, hours, status))

    return {
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "expected_hours_per_day": expected_hours,
        "days": [d.to_dict() for d in days],
        "missing_days": missing,
        "under_logged_days": under,
        "total_hours": seconds_to_hours(total_seconds),
    }


def _zone(tz: str) -> ZoneInfo:
    return ZoneInfo(tz)


def local_datetime(d: date, start_time: str, tz: str) -> datetime:
    """Combine a date + 'HH:MM' local start time into a tz-aware datetime."""
    hh, mm = (int(x) for x in start_time.split(":"))
    return datetime.combine(d, time(hh, mm), tzinfo=_zone(tz))


def _to_utc_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def utc_bounds(start: date, end: date, tz: str) -> tuple[str, str]:
    """UTC 'started_from'/'started_to' covering local [start 00:00, end 23:59:59]."""
    z = _zone(tz)
    start_local = datetime.combine(start, time(0, 0, 0), tzinfo=z)
    end_local = datetime.combine(end, time(23, 59, 59), tzinfo=z)
    return _to_utc_z(start_local), _to_utc_z(end_local)
