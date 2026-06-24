import time

import httpx
import pytest

from freshbooks_mcp.config import Config
from freshbooks_mcp.freshbooks_client import FreshBooksClient, FreshBooksError
from freshbooks_mcp.models import TokenSet


def make_config(**over):
    base = dict(
        client_id="cid",
        client_secret="csecret",
        redirect_uri="https://localhost/callback",
        business_id=None,
        identity_id=None,
        token_backend="file",
        token_path=None,
        token_key=None,
        timezone="UTC",
        default_daily_hours=8,
        default_start_time="09:00",
        max_log_days=31,
    )
    base.update(over)
    return Config(**base)


class FakeAuth:
    def __init__(self):
        self.refreshes = 0

    def get_access_token(self):
        return "tok"

    def force_refresh(self):
        self.refreshes += 1
        return "tok2"


def make_client(handler, **cfg):
    auth = FakeAuth()
    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = FreshBooksClient(make_config(**cfg), auth, http)
    return client, auth


ME_RESPONSE = {
    "response": {
        "id": 555,
        "business_memberships": [
            {"business": {"id": 999, "account_id": "abc123"}}
        ],
    }
}


def test_me_auto_discovery():
    def handler(request):
        assert request.url.path.endswith("/users/me")
        return httpx.Response(200, json=ME_RESPONSE)

    client, _ = make_client(handler)
    assert client.business_id == 999
    assert client.identity_id == 555


def test_401_triggers_single_refresh_and_retry():
    state = {"calls": 0}

    def handler(request):
        if request.url.path.endswith("/users/me"):
            return httpx.Response(200, json=ME_RESPONSE)
        state["calls"] += 1
        if state["calls"] == 1:
            assert request.headers["Authorization"] == "Bearer tok"
            return httpx.Response(401, json={"error": "unauthenticated"})
        assert request.headers["Authorization"] == "Bearer tok2"
        return httpx.Response(200, json={"time_entries": [], "meta": {"pages": 1}})

    client, auth = make_client(handler, business_id=999, identity_id=555)
    entries = client.list_time_entries("2026-06-01T00:00:00.000Z",
                                       "2026-06-02T00:00:00.000Z")
    assert entries == []
    assert auth.refreshes == 1


def test_list_time_entries_omits_identity_by_default():
    # Regression: FreshBooks returns 422 ("team must be true ...") if identity_id
    # is sent without team=true. The endpoint already scopes to the authed user,
    # so the default call must NOT send identity_id or team.
    captured = {}

    def handler(request):
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"time_entries": [], "meta": {"pages": 1}})

    client, _ = make_client(handler, business_id=999, identity_id=555)
    client.list_time_entries("a", "b")
    assert "identity_id" not in captured["params"]
    assert "team" not in captured["params"]


def test_list_time_entries_team_mode_when_identity_given():
    # When an explicit identity_id is passed (admin viewing a team member),
    # both identity_id AND team=true must be sent.
    captured = {}

    def handler(request):
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"time_entries": [], "meta": {"pages": 1}})

    client, _ = make_client(handler, business_id=999, identity_id=555)
    client.list_time_entries("a", "b", identity_id=777)
    assert captured["params"]["identity_id"] == "777"
    assert captured["params"]["team"] == "true"


def test_list_time_entries_pagination():
    def handler(request):
        page = int(request.url.params.get("page", "1"))
        if page == 1:
            return httpx.Response(200, json={
                "time_entries": [
                    {"id": 1, "started_at": "2026-06-01T13:00:00.000Z",
                     "duration": 3600, "identity_id": 555}
                ],
                "meta": {"page": 1, "pages": 2},
            })
        return httpx.Response(200, json={
            "time_entries": [
                {"id": 2, "started_at": "2026-06-02T13:00:00.000Z",
                 "duration": 7200, "identity_id": 555}
            ],
            "meta": {"page": 2, "pages": 2},
        })

    client, _ = make_client(handler, business_id=999, identity_id=555)
    entries = client.list_time_entries("a", "b")
    assert [e.id for e in entries] == [1, 2]
    assert entries[1].hours == 2.0


def test_create_time_entry_body():
    from datetime import datetime, timezone

    captured = {}

    def handler(request):
        import json as _json
        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"time_entry": {
            "id": 42, "started_at": "2026-06-01T13:00:00.000Z",
            "duration": 28800, "identity_id": 555, "project_id": 7,
        }})

    client, _ = make_client(handler, business_id=999, identity_id=555)
    started = datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)
    entry = client.create_time_entry(started, 28800, project_id=7, note="work")

    te = captured["body"]["time_entry"]
    assert te["duration"] == 28800
    assert te["project_id"] == 7
    assert te["is_logged"] is True
    assert te["identity_id"] == 555
    assert te["started_at"] == "2026-06-01T13:00:00.000Z"
    assert entry.id == 42


def test_list_projects_filters_inactive():
    def handler(request):
        return httpx.Response(200, json={
            "projects": [
                {"id": 1, "title": "Active", "active": True},
                {"id": 2, "title": "Old", "active": False},
            ],
            "meta": {"page": 1, "pages": 1},
        })

    client, _ = make_client(handler, business_id=999, identity_id=555)
    projects = client.list_projects(active_only=True)
    assert [p.id for p in projects] == [1]


def test_error_raises_freshbooks_error():
    def handler(request):
        return httpx.Response(422, json={"message": "bad input"})

    client, _ = make_client(handler, business_id=999, identity_id=555)
    with pytest.raises(FreshBooksError) as exc:
        client.create_time_entry(__import__("datetime").datetime(2026, 6, 1),
                                 3600, project_id=7)
    assert exc.value.status == 422


def test_rate_limit_raises_429():
    def handler(request):
        return httpx.Response(429, json={})

    client, _ = make_client(handler, business_id=999, identity_id=555)
    with pytest.raises(FreshBooksError) as exc:
        client.list_time_entries("a", "b")
    assert exc.value.status == 429
