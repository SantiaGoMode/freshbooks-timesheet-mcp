from datetime import date, datetime, timezone

import pytest

from freshbooks_mcp.models import TimeEntry
from freshbooks_mcp.transformers import (
    build_timesheet_report,
    business_days,
    entries_by_day,
    hours_to_seconds,
    local_datetime,
    resolve_range,
    seconds_to_hours,
    utc_bounds,
)


def test_unit_conversions():
    assert hours_to_seconds(8) == 28800
    assert hours_to_seconds(1.5) == 5400
    assert seconds_to_hours(3600) == 1.0
    assert seconds_to_hours(5400) == 1.5


def test_resolve_range_day():
    d = date(2026, 6, 24)  # Wednesday
    assert resolve_range("day", d) == (d, d)


def test_resolve_range_week_is_mon_to_sun():
    d = date(2026, 6, 24)  # Wednesday
    start, end = resolve_range("week", d)
    assert start == date(2026, 6, 22)  # Monday
    assert end == date(2026, 6, 28)  # Sunday


def test_resolve_range_month():
    d = date(2026, 2, 15)  # February (non-leap 2026)
    start, end = resolve_range("month", d)
    assert start == date(2026, 2, 1)
    assert end == date(2026, 2, 28)


def test_resolve_range_invalid():
    with pytest.raises(ValueError):
        resolve_range("year", date(2026, 1, 1))


def test_business_days_excludes_weekend():
    start, end = date(2026, 6, 22), date(2026, 6, 28)  # Mon..Sun
    days = business_days(start, end)
    assert days == [date(2026, 6, d) for d in (22, 23, 24, 25, 26)]


def test_entries_by_day_sums():
    entries = [
        TimeEntry(1, datetime(2026, 6, 1, 9), 3600, None, 1),
        TimeEntry(2, datetime(2026, 6, 1, 14), 7200, None, 1),
        TimeEntry(3, datetime(2026, 6, 2, 9), 3600, None, 1),
    ]
    totals = entries_by_day(entries)
    assert totals[date(2026, 6, 1)] == 10800
    assert totals[date(2026, 6, 2)] == 3600


def test_entries_by_day_buckets_in_local_timezone():
    # 02:00 UTC on Jun 24 == 22:00 EDT on Jun 23 in Toronto.
    late = TimeEntry.from_api({
        "started_at": "2026-06-24T02:00:00.000Z", "duration": 3600,
        "identity_id": 1,
    })
    # Without tz: bucketed by UTC date (Jun 24)
    assert date(2026, 6, 24) in entries_by_day([late])
    # With tz: correctly bucketed to the local day (Jun 23)
    toronto = entries_by_day([late], "America/Toronto")
    assert date(2026, 6, 23) in toronto
    assert date(2026, 6, 24) not in toronto


def _entry(d: date, hours: float):
    return TimeEntry(None, datetime(d.year, d.month, d.day, 9), int(hours * 3600),
                     None, 1)


def test_report_flags_missing_under_and_logged():
    start, end = date(2026, 6, 22), date(2026, 6, 28)  # Mon..Sun
    today = date(2026, 6, 28)  # treat whole week as past
    entries = [
        _entry(date(2026, 6, 22), 8),    # Mon: logged
        _entry(date(2026, 6, 23), 4),    # Tue: under
        # Wed (24): missing
        _entry(date(2026, 6, 25), 8),    # Thu: logged
        # Fri (26): missing
    ]
    report = build_timesheet_report(start, end, entries, 8, today)

    assert report["missing_days"] == ["2026-06-24", "2026-06-26"]
    assert report["under_logged_days"] == ["2026-06-23"]
    assert report["total_hours"] == 20.0
    # only the 5 weekdays present
    assert len(report["days"]) == 5
    statuses = {d["date"]: d["status"] for d in report["days"]}
    assert statuses["2026-06-22"] == "logged"
    assert statuses["2026-06-23"] == "under"


def test_report_future_days_not_missing():
    start, end = date(2026, 6, 22), date(2026, 6, 28)
    today = date(2026, 6, 23)  # Tuesday; Wed-Fri are future
    entries = [_entry(date(2026, 6, 22), 8)]  # Mon logged, Tue empty (past)
    report = build_timesheet_report(start, end, entries, 8, today)

    assert report["missing_days"] == ["2026-06-23"]  # only Tuesday
    statuses = {d["date"]: d["status"] for d in report["days"]}
    assert statuses["2026-06-24"] == "future"
    assert statuses["2026-06-25"] == "future"


def test_local_datetime_and_utc_bounds():
    dt = local_datetime(date(2026, 6, 24), "09:00", "America/Toronto")
    # Toronto is UTC-4 in June (EDT) -> 09:00 local == 13:00 UTC
    assert dt.astimezone(timezone.utc).hour == 13

    frm, to = utc_bounds(date(2026, 6, 24), date(2026, 6, 24), "America/Toronto")
    assert frm == "2026-06-24T04:00:00.000Z"   # 00:00 EDT
    assert to == "2026-06-25T03:59:59.000Z"    # 23:59:59 EDT
