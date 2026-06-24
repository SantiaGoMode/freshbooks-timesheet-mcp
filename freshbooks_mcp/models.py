"""Typed data structures used across the server.

`TokenSet` deliberately masks its secret fields in ``repr`` so tokens never leak
into logs, tracebacks, or MCP tool output.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime


def _mask(value: str | None) -> str:
    """Return a redacted preview of a secret for safe logging."""
    if not value:
        return "<none>"
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}…{value[-2:]}"


@dataclass
class TokenSet:
    """An OAuth2 token set. ``expires_at`` is a unix epoch second."""

    access_token: str
    refresh_token: str
    expires_at: float

    # Skew buffer (seconds) applied when deciding if a token is still valid.
    SKEW: int = field(default=60, repr=False)

    @classmethod
    def from_token_response(cls, payload: dict) -> "TokenSet":
        """Build a TokenSet from a FreshBooks /auth/oauth/token response."""
        created = payload.get("created_at")
        expires_in = int(payload.get("expires_in", 43200))
        # FreshBooks returns created_at as epoch seconds; fall back to now.
        base = float(created) if created is not None else time.time()
        return cls(
            access_token=payload["access_token"],
            refresh_token=payload["refresh_token"],
            expires_at=base + expires_in,
        )

    def is_expired(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return now >= (self.expires_at - self.SKEW)

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TokenSet":
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=float(data["expires_at"]),
        )

    def __repr__(self) -> str:  # never expose raw tokens
        return (
            f"TokenSet(access_token={_mask(self.access_token)}, "
            f"refresh_token={_mask(self.refresh_token)}, "
            f"expires_at={self.expires_at})"
        )


@dataclass
class TimeEntry:
    """A FreshBooks time entry, normalized."""

    id: int | None
    started_at: datetime
    duration_seconds: int
    note: str | None
    identity_id: int | None
    client_id: int | None = None
    project_id: int | None = None
    service_id: int | None = None
    billable: bool = False
    is_logged: bool = True

    @property
    def hours(self) -> float:
        return round(self.duration_seconds / 3600, 4)

    @property
    def local_date(self) -> date:
        return self.started_at.date()

    @classmethod
    def from_api(cls, data: dict) -> "TimeEntry":
        started = data.get("started_at")
        started_dt = (
            datetime.fromisoformat(started.replace("Z", "+00:00"))
            if started
            else datetime.now()
        )
        return cls(
            id=data.get("id"),
            started_at=started_dt,
            duration_seconds=int(data.get("duration", 0)),
            note=data.get("note"),
            identity_id=data.get("identity_id"),
            client_id=data.get("client_id"),
            project_id=data.get("project_id"),
            service_id=data.get("service_id"),
            billable=bool(data.get("billable", False)),
            is_logged=bool(data.get("is_logged", True)),
        )


@dataclass
class Project:
    id: int
    title: str
    active: bool = True
    client_id: int | None = None

    @classmethod
    def from_api(cls, data: dict) -> "Project":
        return cls(
            id=data["id"],
            title=data.get("title", ""),
            active=bool(data.get("active", True)),
            client_id=data.get("client_id"),
        )
