from datetime import date, datetime

import pytest

from freshbooks_mcp.config import Config
from freshbooks_mcp.models import Project, TimeEntry
from freshbooks_mcp.server import (
    build_server,
    handle_auth_debug,
    handle_check_timesheet,
    handle_finish_auth,
    handle_list_projects,
    handle_log_time,
    handle_start_auth,
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
        self.updated = []
        self.identity_id = 555

    def list_time_entries(self, frm, to, identity_id=None):
        return self._entries

    def create_time_entry(self, started, duration, **kw):
        e = TimeEntry(len(self.created) + 1, started, duration, kw.get("note"),
                      555, project_id=kw.get("project_id"))
        self.created.append((started, duration, kw))
        return e

    def update_time_entry(self, entry_id, **kw):
        self.updated.append((entry_id, kw))
        dur = kw.get("duration_seconds") or 28800
        return TimeEntry(entry_id, datetime(2026, 6, 22, 9, 0), int(dur),
                         kw.get("note"), 555, project_id=kw.get("project_id"),
                         billable=bool(kw.get("billable")))

    def list_projects(self, active_only=True):
        return self._projects


class FakeAuth:
    def __init__(self, stored=False):
        self._stored = stored
        self.bootstrapped = []

    def authorize_url(self):
        return (
            "https://auth.freshbooks.com/oauth/authorize?client_id=cid&state=ST",
            "ST",
        )

    def bootstrap_from_code(self, code):
        self.bootstrapped.append(code)

    def has_stored_tokens(self):
        return self._stored


def _entry(d: date, hours: float):
    return TimeEntry(None, datetime(d.year, d.month, d.day, 9), int(hours * 3600),
                     None, 555)


# --- auth tool layer ---------------------------------------------------------

def test_start_auth_returns_url():
    out = handle_start_auth(FakeAuth(), make_config())
    assert "auth.freshbooks.com" in out["authorize_url"]
    assert out["state"] == "ST"


def test_start_auth_requires_credentials():
    with pytest.raises(ValueError):
        handle_start_auth(FakeAuth(), make_config(client_id="", client_secret=""))


def test_finish_auth_strips_and_exchanges_code():
    auth = FakeAuth()
    out = handle_finish_auth(auth, make_config(), "  thecode\n")
    assert out["status"] == "ok"
    assert auth.bootstrapped == ["thecode"]  # whitespace stripped


def test_finish_auth_rejects_empty_code():
    with pytest.raises(ValueError):
        handle_finish_auth(FakeAuth(), make_config(), "   ")


def test_auth_debug_fingerprints_without_leaking_secrets():
    out = handle_auth_debug(FakeAuth(stored=True), make_config())
    assert out["client_id_len"] == 3
    assert out["client_secret_len"] == len("csecret")
    assert out["client_secret_fp"] not in ("csecret", "<empty>")
    assert out["token_stored"] is True
    assert "csecret" not in str(out)  # never echo the raw secret


def test_auth_debug_flags_empty_secret():
    out = handle_auth_debug(FakeAuth(), make_config(client_secret=""))
    assert out["client_secret_len"] == 0
    assert out["client_secret_fp"] == "<empty>"
    assert out["token_stored"] is False


async def test_build_server_registers_all_tools():
    mcp = build_server(make_config(), FakeClient(), FakeAuth())
    names = {t.name for t in await mcp.list_tools()}
    assert {
        "start_auth", "finish_auth", "auth_debug", "check_timesheet",
        "log_time", "list_time_entries", "update_time_entry",
        "list_projects", "list_clients", "list_services",
    } <= names


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


# --- list_time_entries / update_time_entry -----------------------------------

def test_list_time_entries_exposes_ids():
    from freshbooks_mcp.server import handle_list_time_entries
    entries = [
        TimeEntry(101, datetime(2026, 6, 22, 9), 28800, "a", 555, project_id=7),
        TimeEntry(102, datetime(2026, 6, 23, 9), 14400, "b", 555, project_id=8),
    ]
    client = FakeClient(entries=entries)
    result = handle_list_time_entries(client, make_config(), "week",
                                      date_str="2026-06-24")
    ids = [e["id"] for e in result["entries"]]
    assert ids == [101, 102]
    assert result["entries"][0]["project_id"] == 7
    assert result["entries"][1]["hours"] == 4.0


def test_update_time_entry_changes_project():
    from freshbooks_mcp.server import handle_update_time_entry
    client = FakeClient()
    result = handle_update_time_entry(client, make_config(), 101, project_id=99)
    entry_id, kw = client.updated[0]
    assert entry_id == 101
    assert kw["project_id"] == 99
    assert result["updated"]["project_id"] == 99
    assert "project_id" in result["summary"]


def test_update_time_entry_flips_billable():
    from freshbooks_mcp.server import handle_update_time_entry
    client = FakeClient()
    result = handle_update_time_entry(client, make_config(), 101, billable=True,
                                      client_id=543791)
    entry_id, kw = client.updated[0]
    assert kw["billable"] is True
    assert kw["client_id"] == 543791
    assert result["updated"]["billable"] is True
    assert "billable" in result["summary"]


def test_update_time_entry_requires_id_and_a_field():
    from freshbooks_mcp.server import handle_update_time_entry
    client = FakeClient()
    with pytest.raises(ValueError):  # no fields to change
        handle_update_time_entry(client, make_config(), 101)
    with pytest.raises(ValueError):  # bad hours
        handle_update_time_entry(client, make_config(), 101, hours=25)
    assert client.updated == []  # nothing written on validation failure


# --- list_projects -----------------------------------------------------------

def test_list_projects_query_filter():
    projects = [
        Project(1, "Internal Tools", True),
        Project(2, "Client Website", True),
    ]
    client = FakeClient(projects=projects)
    result = handle_list_projects(client, query="website")
    assert [p["project_id"] for p in result["projects"]] == [2]
