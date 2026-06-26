"""Thin HTTP wrapper over the FreshBooks REST API.

One method per API call. Auth is delegated to ``AuthManager``; a 401 triggers a
single forced refresh + retry. Business/identity/account ids are auto-discovered
from ``/auth/api/v1/users/me`` when not configured.
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from .auth_manager import AuthManager
from .config import API_BASE, ME_URL, Config
from .models import Project, TimeEntry
from .transformers import to_utc_z

logger = logging.getLogger(__name__)


class FreshBooksError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class FreshBooksClient:
    def __init__(
        self,
        config: Config,
        auth: AuthManager,
        client: httpx.Client | None = None,
    ) -> None:
        self._config = config
        self._auth = auth
        self._http = client or httpx.Client(timeout=30.0)
        self._business_id: int | None = config.business_id
        self._identity_id: int | None = config.identity_id
        self._account_id: str | None = None

    # -- core request ---------------------------------------------------------

    def _request(self, method: str, url: str, *, params=None, json=None) -> dict:
        headers = {
            "Authorization": f"Bearer {self._auth.get_access_token()}",
            "Api-Version": "alpha",
            "Accept": "application/json",
        }
        if json is not None:
            headers["Content-Type"] = "application/json"

        resp = self._http.request(
            method, url, params=params, json=json, headers=headers
        )

        if resp.status_code == 401:
            # token may have been revoked/expired early — refresh once and retry
            headers["Authorization"] = f"Bearer {self._auth.force_refresh()}"
            resp = self._http.request(
                method, url, params=params, json=json, headers=headers
            )

        if resp.status_code == 429:
            raise FreshBooksError("Rate limited by FreshBooks (429).", 429)
        if resp.status_code >= 400:
            raise FreshBooksError(_error_message(resp), resp.status_code)

        if not resp.content:
            return {}
        return resp.json()

    # -- identity / discovery -------------------------------------------------

    def me(self) -> dict:
        data = self._request("GET", ME_URL)
        return data.get("response", data)

    def _ensure_ids(self, need_account: bool = False) -> None:
        """Lazily fill business_id / identity_id (+ account_id) from /me."""
        have_core = self._business_id and self._identity_id
        if have_core and (self._account_id or not need_account):
            return
        me = self.me()
        if self._identity_id is None:
            self._identity_id = me.get("id")
        memberships = me.get("business_memberships") or []
        if memberships:
            business = memberships[0].get("business", {})
            if self._business_id is None:
                self._business_id = business.get("id")
            if self._account_id is None:
                self._account_id = business.get("account_id")
        if self._business_id is None or self._identity_id is None:
            raise FreshBooksError(
                "Could not determine business_id/identity_id from /me. "
                "Set FRESHBOOKS_BUSINESS_ID and FRESHBOOKS_IDENTITY_ID."
            )

    @property
    def business_id(self) -> int:
        self._ensure_ids()
        return self._business_id  # type: ignore[return-value]

    @property
    def identity_id(self) -> int:
        self._ensure_ids()
        return self._identity_id  # type: ignore[return-value]

    # -- time entries ---------------------------------------------------------

    def list_time_entries(
        self,
        started_from: str,
        started_to: str,
        identity_id: int | None = None,
    ) -> list[TimeEntry]:
        base = (
            f"{API_BASE}/timetracking/business/{self.business_id}/time_entries"
        )
        params = {
            "started_from": started_from,
            "started_to": started_to,
            "per_page": 100,
            "page": 1,
        }
        # The endpoint is already scoped to the authenticated user. The
        # identity_id filter is only valid with team=true (admin viewing other
        # team members), so only send it in that explicit case.
        if identity_id is not None:
            params["team"] = "true"
            params["identity_id"] = identity_id
        entries: list[TimeEntry] = []
        while True:
            data = self._request("GET", base, params=params)
            page_entries = data.get("time_entries", [])
            entries.extend(TimeEntry.from_api(e) for e in page_entries)
            meta = data.get("meta", {})
            page = meta.get("page", params["page"])
            pages = meta.get("pages", 1)
            if not page_entries or page >= pages:
                break
            params["page"] = page + 1
        return entries

    def create_time_entry(
        self,
        started_at: datetime,
        duration_seconds: int,
        *,
        project_id: int,
        note: str = "Logged via MCP",
        client_id: int | None = None,
        service_id: int | None = None,
        billable: bool = False,
        identity_id: int | None = None,
    ) -> TimeEntry:
        url = f"{API_BASE}/timetracking/business/{self.business_id}/time_entries"
        entry: dict = {
            "is_logged": True,
            "duration": int(duration_seconds),
            "note": note,
            "started_at": to_utc_z(started_at),
            "identity_id": identity_id or self.identity_id,
            "project_id": project_id,
            "billable": billable,
        }
        if client_id is not None:
            entry["client_id"] = client_id
        if service_id is not None:
            entry["service_id"] = service_id

        data = self._request("POST", url, json={"time_entry": entry})
        return TimeEntry.from_api(data.get("time_entry", data))

    # -- projects / clients / services ---------------------------------------

    def list_projects(self, active_only: bool = True) -> list[Project]:
        url = f"{API_BASE}/projects/business/{self.business_id}/projects"
        params = {"per_page": 100, "page": 1}
        projects: list[Project] = []
        while True:
            data = self._request("GET", url, params=params)
            page_items = data.get("projects", [])
            projects.extend(Project.from_api(p) for p in page_items)
            meta = data.get("meta", {})
            page = meta.get("page", params["page"])
            pages = meta.get("pages", 1)
            if not page_items or page >= pages:
                break
            params["page"] = page + 1
        if active_only:
            projects = [p for p in projects if p.active]
        return projects

    def list_clients(self) -> list[dict]:
        self._ensure_ids(need_account=True)
        if not self._account_id:
            raise FreshBooksError("No account_id available for clients lookup.")
        url = (
            f"{API_BASE}/accounting/account/{self._account_id}/users/clients"
        )
        data = self._request("GET", url, params={"per_page": 100})
        result = data.get("response", {}).get("result", {})
        return result.get("clients", [])

    def list_services(self) -> list[dict]:
        url = f"{API_BASE}/comments/business/{self.business_id}/services"
        data = self._request("GET", url, params={"per_page": 100})
        return data.get("services", [])


def _error_message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except Exception:
        return f"HTTP {resp.status_code}"
    # FreshBooks errors come in a few shapes
    if isinstance(data, dict):
        if "message" in data:
            return f"{resp.status_code}: {data['message']}"
        errors = data.get("response", {}).get("errors") or data.get("errors")
        if errors:
            return f"{resp.status_code}: {errors}"
    return f"HTTP {resp.status_code}"
