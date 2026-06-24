import time

from freshbooks_mcp.models import TimeEntry, TokenSet


def test_tokenset_repr_masks_secrets():
    ts = TokenSet("supersecretaccess", "supersecretrefresh", time.time() + 100)
    r = repr(ts)
    assert "supersecretaccess" not in r
    assert "supersecretrefresh" not in r
    assert "…" in r  # masked preview present


def test_tokenset_expiry_with_skew():
    now = 1_000_000.0
    # expires in 30s but skew is 60s -> already considered expired
    ts = TokenSet("a", "b", now + 30)
    assert ts.is_expired(now=now)
    # expires well beyond skew
    ts2 = TokenSet("a", "b", now + 600)
    assert not ts2.is_expired(now=now)


def test_tokenset_roundtrip_dict():
    ts = TokenSet("a", "b", 123.0)
    assert TokenSet.from_dict(ts.to_dict()) == ts


def test_tokenset_from_token_response_uses_created_at():
    payload = {
        "access_token": "a",
        "refresh_token": "b",
        "expires_in": 100,
        "created_at": 1000,
    }
    ts = TokenSet.from_token_response(payload)
    assert ts.expires_at == 1100


def test_time_entry_hours_and_date():
    entry = TimeEntry.from_api(
        {
            "id": 1,
            "started_at": "2026-06-01T13:00:00.000Z",
            "duration": 3600,
            "note": "x",
            "identity_id": 7,
        }
    )
    assert entry.hours == 1.0
    assert entry.local_date.isoformat() == "2026-06-01"
    assert entry.duration_seconds == 3600
