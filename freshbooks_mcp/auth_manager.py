"""OAuth2 token lifecycle: storage, refresh, and rotation.

FreshBooks access tokens expire after ~12h, and **refresh tokens rotate on every
use** — the old refresh token is invalidated the moment a new one is issued. So
the rotated token is persisted *before* the new access token is returned, under a
lock to prevent two concurrent refreshes from racing and bricking the chain.
"""

from __future__ import annotations

import logging
import secrets
import threading
from urllib.parse import urlencode

import httpx

from .config import AUTHORIZE_URL, TOKEN_URL, Config
from .models import TokenSet
from .token_store import TokenStore, build_token_store

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Raised when authentication cannot proceed without user re-auth."""


class AuthManager:
    def __init__(
        self,
        config: Config,
        store: TokenStore | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._config = config
        self._store = store or build_token_store(config)
        self._client = client or httpx.Client(timeout=30.0)
        self._lock = threading.Lock()
        self._cache: TokenSet | None = None

    # -- public API -----------------------------------------------------------

    def get_access_token(self) -> str:
        """Return a valid access token, refreshing (and rotating) if needed."""
        with self._lock:
            tokens = self._cache or self._store.load()
            if tokens is None:
                raise AuthError(
                    "No stored credentials. Run the auth bootstrap first: "
                    "`freshbooks-mcp-auth`."
                )
            if tokens.is_expired():
                tokens = self._refresh_locked(tokens)
            self._cache = tokens
            return tokens.access_token

    def force_refresh(self) -> str:
        """Refresh unconditionally (used on a 401 from the API)."""
        with self._lock:
            tokens = self._cache or self._store.load()
            if tokens is None:
                raise AuthError("No stored credentials to refresh.")
            tokens = self._refresh_locked(tokens)
            self._cache = tokens
            return tokens.access_token

    def revoke(self) -> None:
        """Forget stored tokens (logout / recovery)."""
        with self._lock:
            self._store.clear()
            self._cache = None

    def authorize_url(self) -> tuple[str, str]:
        """Return (url, state) to start the OAuth authorization-code flow."""
        state = secrets.token_urlsafe(24)
        params = {
            "client_id": self._config.client_id,
            "response_type": "code",
            "redirect_uri": self._config.redirect_uri,
            "state": state,
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}", state

    def bootstrap_from_code(self, auth_code: str) -> TokenSet:
        """Exchange the initial authorization code for the first token set."""
        self._config.require_oauth()
        payload = {
            "grant_type": "authorization_code",
            "client_id": self._config.client_id,
            "client_secret": self._config.client_secret,
            "code": auth_code,
            "redirect_uri": self._config.redirect_uri,
        }
        tokens = self._token_request(payload)
        with self._lock:
            self._store.save(tokens)
            self._cache = tokens
        logger.info("Bootstrapped new token set: %r", tokens)
        return tokens

    # -- internals ------------------------------------------------------------

    def _refresh_locked(self, tokens: TokenSet) -> TokenSet:
        """Refresh using the current token. Caller must hold the lock."""
        self._config.require_oauth()
        payload = {
            "grant_type": "refresh_token",
            "client_id": self._config.client_id,
            "client_secret": self._config.client_secret,
            "refresh_token": tokens.refresh_token,
            "redirect_uri": self._config.redirect_uri,
        }
        new_tokens = self._token_request(payload)
        # Persist the rotated refresh token BEFORE returning / using it.
        self._store.save(new_tokens)
        logger.info("Refreshed access token; rotated refresh token persisted.")
        return new_tokens

    def _token_request(self, payload: dict) -> TokenSet:
        try:
            resp = self._client.post(TOKEN_URL, json=payload)
        except httpx.HTTPError as exc:  # network-level
            raise AuthError(f"Token request failed: {exc}") from exc

        if resp.status_code >= 400:
            # Do not log the payload (contains secrets/refresh token).
            err = _safe_error(resp)
            if err in {"invalid_grant", "unauthorized"}:
                raise AuthError(
                    "Refresh token is no longer valid (rotated or revoked). "
                    "Re-run the auth bootstrap: `freshbooks-mcp-auth`."
                )
            raise AuthError(f"Token endpoint returned {resp.status_code}: {err}")

        return TokenSet.from_token_response(resp.json())


def _safe_error(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        return str(data.get("error") or data.get("message") or resp.status_code)
    except Exception:
        return str(resp.status_code)


def cli() -> None:  # pragma: no cover - interactive
    """One-time OAuth bootstrap.

    Two-step, non-interactive friendly:
      freshbooks-mcp-auth            -> prints the authorize URL
      freshbooks-mcp-auth <code>     -> exchanges the code for tokens

    If run in a real interactive terminal with no code argument, it falls back
    to prompting for the pasted code.
    """
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(prog="freshbooks-mcp-auth")
    parser.add_argument(
        "code", nargs="?", help="authorization code from the redirect URL"
    )
    args = parser.parse_args()

    config = Config.load()
    config.require_oauth()
    auth = AuthManager(config)

    code = args.code
    if not code and sys.stdin.isatty():
        url, state = auth.authorize_url()
        print("\n1) Open this URL and authorize the app:\n")
        print(f"   {url}\n")
        print(f"   (state={state})\n")
        print("2) Copy the `code` query param from the redirect URL.\n")
        code = input("Paste the authorization code here: ").strip()

    if not code:
        url, _ = auth.authorize_url()
        print("\n1) Open this URL and authorize the app:\n")
        print(f"   {url}\n")
        print(
            "2) You'll be redirected to https://localhost/callback?code=...&state=...\n"
            "   (the page won't load — that's fine). Copy the `code` value.\n"
        )
        print("3) Then run:\n")
        print("   freshbooks-mcp-auth <code>\n")
        return

    auth.bootstrap_from_code(code)
    print("\n✅ Tokens stored securely. You can now run the MCP server.")


if __name__ == "__main__":  # pragma: no cover
    cli()
