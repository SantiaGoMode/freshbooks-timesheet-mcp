"""Configuration loading and constants."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- API constants -----------------------------------------------------------
API_BASE = "https://api.freshbooks.com"
TOKEN_URL = f"{API_BASE}/auth/oauth/token"
ME_URL = f"{API_BASE}/auth/api/v1/users/me"
# The browser authorization endpoint lives on a different host.
AUTHORIZE_URL = "https://auth.freshbooks.com/oauth/authorize"

# Keyring service name under which the token set is stored.
KEYRING_SERVICE = "freshbooks-timesheet-mcp"
KEYRING_USERNAME = "tokens"


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _get_opt_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    return int(raw)


def _file_or_env(name: str, default: str = "") -> str:
    """Resolve a secret, preferring a mounted file (Docker/K8s secret) over env.

    If ``<NAME>_FILE`` is set, its file contents are used (e.g.
    ``FRESHBOOKS_TOKEN_KEY_FILE=/run/secrets/fb_token_key``); otherwise
    ``<NAME>`` is read from the environment. The file form keeps the value out
    of the container env and ``docker inspect``.
    """
    path = os.getenv(f"{name}_FILE")
    if path:
        return Path(path).expanduser().read_text().strip()
    return os.getenv(name, default)


@dataclass
class Config:
    client_id: str
    client_secret: str
    redirect_uri: str
    business_id: int | None
    identity_id: int | None
    token_backend: str
    token_path: str | None
    token_key: str | None
    timezone: str
    default_daily_hours: float
    default_start_time: str
    max_log_days: int

    @classmethod
    def load(cls) -> "Config":
        return cls(
            client_id=_file_or_env("FRESHBOOKS_CLIENT_ID"),
            client_secret=_file_or_env("FRESHBOOKS_CLIENT_SECRET"),
            redirect_uri=os.getenv(
                "FRESHBOOKS_REDIRECT_URI", "https://localhost/callback"
            ),
            business_id=_get_opt_int("FRESHBOOKS_BUSINESS_ID"),
            identity_id=_get_opt_int("FRESHBOOKS_IDENTITY_ID"),
            token_backend=os.getenv("FRESHBOOKS_TOKEN_BACKEND", "keyring").lower(),
            token_path=os.getenv("FRESHBOOKS_TOKEN_PATH") or None,
            token_key=_file_or_env("FRESHBOOKS_TOKEN_KEY") or None,
            timezone=os.getenv("TZ", "UTC"),
            default_daily_hours=float(os.getenv("DEFAULT_DAILY_HOURS", "8")),
            default_start_time=os.getenv("DEFAULT_START_TIME", "09:00"),
            max_log_days=_get_int("MAX_LOG_DAYS", 31),
        )

    def require_oauth(self) -> None:
        """Raise if the credentials needed for the OAuth flow are missing."""
        missing = [
            n
            for n, v in (
                ("FRESHBOOKS_CLIENT_ID", self.client_id),
                ("FRESHBOOKS_CLIENT_SECRET", self.client_secret),
            )
            if not v
        ]
        if missing:
            raise ValueError(f"Missing required config: {', '.join(missing)}")
