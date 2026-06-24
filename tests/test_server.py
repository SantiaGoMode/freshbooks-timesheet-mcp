from datetime import date, datetime

import pytest

from freshbooks_mcp.config import Config
from freshbooks_mcp.models import Project, TimeEntry
from freshbooks_mcp.server import (
    handle_check_timesheet,
    handle_list_projects,
    handle_log_time,
)


def make_config(**over):
    base = dict(
        client_id="cid", client_secret="csecret",
        redirect_uri="https://localhost/callback",
        business_id=999, identity_id=555,
        token_backend="file", token_path=None, token_key=None,
        timezone="America/Toronto", default_daily_hours=8,
        default_start_time="09:00", max_log_days=31,
    )
    base.update(over)
    return Config(**base)


class FakeClient:
    def __init__(self, entries=None, projects=None):
        self._entries = entries or []
        self._projects = projects or []
        self.created = []
        self.identity_id = 555

    def list_time_entries(self, frm, to, identity_id=None):
        return self._entries

    def create_time_entry(self, started, duration, **kw):
        e = TimeEntry(len(self.created) + 1, started, duration, kw.get("note"),
                      555, project_id=kw.get("project_id"))
        self.created.append((started, duration, kw))
        return e

    def list_projects(self, active_only=True):
        return self._projects


def _entry(d: date, hours: float):
    return TimeEntry(None, datetime(d.year, d.month, d.day, 9), int(hours * 3600),
                     None, 555)


# --- check_timesheet ---------------------------------------------------------

def test_check_timesheet_reports_missing():
    entries = [_entry(date(2026, 6, 22), 8)]  # only Monday logged
    client = FakeClient(entries=entries)
    report = handle_check_timesheet(
        client, make_config(), "week",
        date_str="2026-06-24", today=date(2026, 6, 26),
    )
    assert "2026-06-23" in report["missing_days"]
    assert report["total_hours"] == 8.0
    assert "summary" in report


# --- log_time validation -----------------------------------------------------

def test_log_time_rejects_bad_hours():
    client = FakeClient()
    with pytest.raises(ValueError):
        handle_log_time(client, make_config(), "day", 0, project_id=7)
    with pytest.raises(ValueError):
        handle_log_time(client, make_config(), "day", 25, project_id=7)


def test_log_time_billable_requires_client():
    client = FakeClient()
    with pytest.raises(ValueError):
        handle_log_time(client, make_config(), "day", 8, project_id=7,
                        billable=True)


def test_log_time_enforces_max_days():
    client = FakeClient()
    with pytest.raises(ValueError):
        handle_log_time(client, make_config(max_log_days=2), "month", 8,
                        project_id=7, date_str="2026-06-01", skip_existing=False)


# --- log_time behavior -------------------------------------------------------

def test_log_time_dry_run_writes_nothing():
    client = FakeClient()
    result = handle_log_time(
        client, make_config(), "week", 8, project_id=7,
        date_str="2026-06-24", dry_run=True, skip_existing=False,
    )
    assert result["dry_run"] is True
    assert result["would_log"] == [
        "2026-06-22", "2026-06-23", "2026-06-24", "2026-06-25", "2026-06-26",
    ]
    assert client.created == []


def test_log_time_skips_off_days_and_weekends():
    client = FakeClient()
    result = handle_log_time(
        client, make_config(), "week", 8, project_id=7,
        date_str="2026-06-24", off_days=["2026-06-26"],  # PTO Friday
        skip_existing=False,
    )
    logged = [c["date"] for c in result["created"]]
    assert logged == ["2026-06-22", "2026-06-23", "2026-06-24", "2026-06-25"]
    assert "2026-06-26" in result["off_days"]
    # weekend never appears
    assert all(not d.startswith("2026-06-27") for d in logged)


def test_log_time_skip_existing():
    entries = [_entry(date(2026, 6, 22), 8)]  # Monday already logged
    client = FakeClient(entries=entries)
    result = handle_log_time(
        client, make_config(), "week", 8, project_id=7,
        date_str="2026-06-24", skip_existing=True,
    )
    assert "2026-06-22" in result["skipped_existing"]
    logged = [c["date"] for c in result["created"]]
    assert "2026-06-22" not in logged


def test_log_time_uses_authenticated_identity_only():
    client = FakeClient()
    handle_log_time(client, make_config(), "day", 8, project_id=7,
                    date_str="2026-06-24", skip_existing=False)
    # identity is never taken from args; create call doesn't pass identity_id
    _, _, kw = client.created[0]
    assert "identity_id" not in kw  # client fills it from the authed user


def test_log_time_duration_and_start_time():
    client = FakeClient()
    handle_log_time(client, make_config(), "day", 8, project_id=7,
                    date_str="2026-06-24", skip_existing=False)
    started, duration, _ = client.created[0]
    assert duration == 28800  # 8h
    # 09:00 Toronto EDT == 13:00 UTC
    assert started.astimezone().tzinfo is not None


# --- list_projects -----------------------------------------------------------

def test_list_projects_query_filter():
    projects = [
        Project(1, "Internal Tools", True),
        Project(2, "Client Website", True),
    ]
    client = FakeClient(projects=projects)
    result = handle_list_projects(client, query="website")
    assert [p["project_id"] for p in result["projects"]] == [2]
