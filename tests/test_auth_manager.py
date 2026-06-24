import time

import httpx
import pytest

from freshbooks_mcp.auth_manager import AuthError, AuthManager
from freshbooks_mcp.config import Config
from freshbooks_mcp.models import TokenSet


def make_config():
    return Config(
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


class MemoryStore:
    def __init__(self, tokens=None):
        self.tokens = tokens
        self.saves = []

    def load(self):
        return self.tokens

    def save(self, tokens):
        self.tokens = tokens
        self.saves.append(tokens)

    def clear(self):
        self.tokens = None


def client_returning(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_get_access_token_returns_cached_when_valid():
    store = MemoryStore(TokenSet("valid", "ref", time.time() + 9999))

    def handler(request):  # should never be called
        raise AssertionError("no token request expected")

    auth = AuthManager(make_config(), store, client_returning(handler))
    assert auth.get_access_token() == "valid"


def test_refresh_on_expired_rotates_and_persists():
    store = MemoryStore(TokenSet("old", "oldref", time.time() - 10))
    calls = {}

    def handler(request):
        body = request.content.decode()
        calls["grant"] = "refresh_token" in body
        calls["used_refresh"] = "oldref" in body
        return httpx.Response(
            200,
            json={
                "access_token": "newacc",
                "refresh_token": "newref",
                "expires_in": 43200,
                "created_at": time.time(),
            },
        )

    auth = AuthManager(make_config(), store, client_returning(handler))
    token = auth.get_access_token()

    assert token == "newacc"
    assert calls["grant"] and calls["used_refresh"]
    # rotated refresh token persisted
    assert store.tokens.refresh_token == "newref"
    assert len(store.saves) == 1


def test_invalid_grant_raises_autherror():
    store = MemoryStore(TokenSet("old", "deadref", time.time() - 10))

    def handler(request):
        return httpx.Response(400, json={"error": "invalid_grant"})

    auth = AuthManager(make_config(), store, client_returning(handler))
    with pytest.raises(AuthError) as exc:
        auth.get_access_token()
    assert "bootstrap" in str(exc.value).lower()


def test_no_tokens_raises():
    store = MemoryStore(None)
    auth = AuthManager(make_config(), store, client_returning(lambda r: None))
    with pytest.raises(AuthError):
        auth.get_access_token()


def test_bootstrap_from_code_stores_tokens():
    store = MemoryStore(None)

    def handler(request):
        assert "authorization_code" in request.content.decode()
        return httpx.Response(
            200,
            json={
                "access_token": "a",
                "refresh_token": "r",
                "expires_in": 43200,
                "created_at": time.time(),
            },
        )

    auth = AuthManager(make_config(), store, client_returning(handler))
    ts = auth.bootstrap_from_code("thecode")
    assert ts.access_token == "a"
    assert store.tokens.refresh_token == "r"


def test_authorize_url_includes_state():
    auth = AuthManager(make_config(), MemoryStore(None), httpx.Client())
    url, state = auth.authorize_url()
    assert "response_type=code" in url
    assert f"state={state}" in url
    assert len(state) > 10


def test_force_refresh_used_for_401_path():
    store = MemoryStore(TokenSet("acc", "ref", time.time() + 9999))

    def handler(request):
        return httpx.Response(
            200,
            json={
                "access_token": "acc2",
                "refresh_token": "ref2",
                "expires_in": 43200,
                "created_at": time.time(),
            },
        )

    auth = AuthManager(make_config(), store, client_returning(handler))
    assert auth.force_refresh() == "acc2"
    assert store.tokens.refresh_token == "ref2"
