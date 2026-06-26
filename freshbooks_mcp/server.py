"""MCP interface — tool definitions and dispatch.

Tool *logic* lives in plain ``handle_*`` functions that take an explicit client,
so they are unit-testable without the MCP runtime. The FastMCP layer is a thin
wrapper registered in ``build_server``.

Security note (§11): all validation happens here, server-side — never trust the
calling agent. ``identity_id`` is always the authenticated user; write tools
require an explicit ``project_id`` and clamp/limit inputs.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import transformers as T
from .config import Config
from .freshbooks_client import FreshBooksClient

logger = logging.getLogger(__name__)

MAX_HOURS_PER_DAY = 24


def _today(config: Config) -> date:
    return datetime.now(ZoneInfo(config.timezone)).date()


def _parse_date(value: str | None, config: Config) -> date:
    if not value:
        return _today(config)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid date {value!r}; use YYYY-MM-DD.") from exc


# -- auth handlers ------------------------------------------------------------

def handle_start_auth(auth, config: Config) -> dict:
    """Return the authorize URL to begin the one-time OAuth flow."""
    config.require_oauth()
    url, state = auth.authorize_url()
    return {
        "authorize_url": url,
        "state": state,
        "instructions": (
            "Open authorize_url in a browser and approve the app. You'll be "
            "redirected to the redirect URI with ?code=...&state=... (the page "
            "won't load — that's fine). Copy the `code` value and call "
            "finish_auth with it. The code expires within minutes."
        ),
    }


def handle_finish_auth(auth, config: Config, code: str) -> dict:
    """Exchange the authorization `code` for tokens and store them."""
    code = (code or "").strip()
    if not code:
        raise ValueError("code is required — the `code` value from the redirect URL.")
    auth.bootstrap_from_code(code)
    return {
        "status": "ok",
        "message": "Authorized. Tokens stored securely — you can now check or log time.",
    }


def handle_auth_debug(auth, config: Config) -> dict:
    """Fingerprint the loaded credentials for troubleshooting (no secrets)."""

    def fp(v: str) -> str:
        return hashlib.sha256(v.encode()).hexdigest()[:12] if v else "<empty>"

    return {
        "client_id_len": len(config.client_id),
        "client_id_fp": fp(config.client_id),
        "client_secret_len": len(config.client_secret),
        "client_secret_fp": fp(config.client_secret),
        "redirect_uri": config.redirect_uri,
        "token_backend": config.token_backend,
        "token_stored": auth.has_stored_tokens(),
    }


# -- handlers -----------------------------------------------------------------

def handle_check_timesheet(
    client: FreshBooksClient,
    config: Config,
    period: str,
    date_str: str | None = None,
    expected_hours: float | None = None,
    today: date | None = None,
) -> dict:
    anchor = _parse_date(date_str, config)
    today = today or _today(config)
    expected = (
        expected_hours
        if expected_hours is not None
        else config.default_daily_hours
    )
    start, end = T.resolve_range(period, anchor)
    frm, to = T.utc_bounds(start, end, config.timezone)
    entries = client.list_time_entries(frm, to)
    report = T.build_timesheet_report(
        start, end, entries, expected, today, config.timezone
    )
    report["summary"] = _summarize_report(period, report)
    return report


def handle_log_time(
    client: FreshBooksClient,
    config: Config,
    period: str,
    hours: float,
    project_id: int,
    date_str: str | None = None,
    off_days: list[str] | None = None,
    note: str = "Logged via MCP",
    billable: bool = False,
    client_id: int | None = None,
    service_id: int | None = None,
    skip_existing: bool = True,
    dry_run: bool = False,
    today: date | None = None,
) -> dict:
    # --- validation (server-side, never trust the agent) ---
    if project_id is None:
        raise ValueError(
            "project_id is required. Call list_projects and ask the user "
            "which project to log against."
        )
    if hours <= 0 or hours > MAX_HOURS_PER_DAY:
        raise ValueError(f"hours must be between 0 and {MAX_HOURS_PER_DAY}.")
    if billable and client_id is None:
        raise ValueError("billable=true requires a client_id.")

    anchor = _parse_date(date_str, config)
    start, end = T.resolve_range(period, anchor)

    off = {_parse_date(d, config) for d in (off_days or [])}
    targets = [d for d in T.business_days(start, end) if d not in off]

    if len(targets) > config.max_log_days:
        raise ValueError(
            f"Refusing to log {len(targets)} days in one call "
            f"(MAX_LOG_DAYS={config.max_log_days})."
        )

    skipped: list[str] = []
    if skip_existing and targets:
        frm, to = T.utc_bounds(targets[0], targets[-1], config.timezone)
        existing = T.entries_by_day(
            client.list_time_entries(frm, to), config.timezone
        )
        kept = []
        for d in targets:
            if existing.get(d, 0) > 0:
                skipped.append(d.isoformat())
            else:
                kept.append(d)
        targets = kept

    plan = [d.isoformat() for d in targets]
    if dry_run:
        return {
            "dry_run": True,
            "would_log": plan,
            "hours_each": hours,
            "skipped_existing": skipped,
            "off_days": sorted(d.isoformat() for d in off),
            "project_id": project_id,
        }

    duration = T.hours_to_seconds(hours)
    created: list[dict] = []
    errors: list[dict] = []
    for d in targets:
        started = T.local_datetime(d, config.default_start_time, config.timezone)
        try:
            entry = client.create_time_entry(
                started,
                duration,
                project_id=project_id,
                note=note,
                client_id=client_id,
                service_id=service_id,
                billable=billable,
            )
            created.append({"date": d.isoformat(), "hours": hours, "id": entry.id})
        except Exception as exc:  # surface partial failure clearly
            errors.append({"date": d.isoformat(), "error": str(exc)})

    return {
        "dry_run": False,
        "created": created,
        "skipped_existing": skipped,
        "off_days": sorted(d.isoformat() for d in off),
        "errors": errors,
        "summary": _summarize_log(created, skipped, errors, hours),
    }


def handle_list_time_entries(
    client: FreshBooksClient,
    config: Config,
    period: str,
    date_str: str | None = None,
) -> dict:
    """List individual entries (with ids) for a day/week/month, for editing."""
    anchor = _parse_date(date_str, config)
    start, end = T.resolve_range(period, anchor)
    frm, to = T.utc_bounds(start, end, config.timezone)
    zone = ZoneInfo(config.timezone)
    items = []
    for e in client.list_time_entries(frm, to):
        dt = e.started_at
        local = dt.astimezone(zone).date() if dt.tzinfo else dt.date()
        items.append({
            "id": e.id,
            "date": local.isoformat(),
            "hours": e.hours,
            "project_id": e.project_id,
            "note": e.note,
            "billable": e.billable,
        })
    items.sort(key=lambda x: (x["date"], x["id"] or 0))
    return {"entries": items}


def handle_update_time_entry(
    client: FreshBooksClient,
    config: Config,
    entry_id: int,
    project_id: int | None = None,
    note: str | None = None,
    hours: float | None = None,
) -> dict:
    """Edit an existing time entry (project, note, and/or hours)."""
    if entry_id is None:
        raise ValueError(
            "entry_id is required. Call list_time_entries to find the entry."
        )
    duration = None
    if hours is not None:
        if hours <= 0 or hours > MAX_HOURS_PER_DAY:
            raise ValueError(f"hours must be between 0 and {MAX_HOURS_PER_DAY}.")
        duration = T.hours_to_seconds(hours)
    if project_id is None and note is None and duration is None:
        raise ValueError(
            "Provide at least one field to change (project_id, note, or hours)."
        )

    entry = client.update_time_entry(
        entry_id, project_id=project_id, note=note, duration_seconds=duration
    )
    changed = [
        name for name, val in
        (("project_id", project_id), ("note", note), ("hours", hours))
        if val is not None
    ]
    return {
        "updated": {
            "id": entry.id,
            "date": entry.local_date.isoformat(),
            "hours": entry.hours,
            "project_id": entry.project_id,
            "note": entry.note,
        },
        "summary": f"Updated entry {entry_id} ({', '.join(changed)}).",
    }


def handle_list_projects(
    client: FreshBooksClient, active_only: bool = True, query: str | None = None
) -> dict:
    projects = client.list_projects(active_only=active_only)
    items = [
        {"project_id": p.id, "title": p.title, "client_id": p.client_id,
         "active": p.active}
        for p in projects
    ]
    if query:
        q = query.lower()
        items = [p for p in items if q in (p["title"] or "").lower()]
    return {"projects": items}


def handle_list_clients(client: FreshBooksClient) -> dict:
    return {"clients": client.list_clients()}


def handle_list_services(client: FreshBooksClient) -> dict:
    return {"services": client.list_services()}


# -- summaries ----------------------------------------------------------------

def _summarize_report(period: str, report: dict) -> str:
    missing = report["missing_days"]
    under = report["under_logged_days"]
    parts = [
        f"{period.capitalize()} {report['range']['start']}–"
        f"{report['range']['end']}: {report['total_hours']}h logged."
    ]
    if missing:
        parts.append(f"Missing: {', '.join(missing)}.")
    if under:
        parts.append(f"Under-logged: {', '.join(under)}.")
    if not missing and not under:
        parts.append("All weekdays accounted for.")
    return " ".join(parts)


def _summarize_log(created, skipped, errors, hours) -> str:
    parts = [f"Logged {hours}h on {len(created)} day(s)."]
    if skipped:
        parts.append(f"Skipped {len(skipped)} already-logged day(s).")
    if errors:
        parts.append(f"{len(errors)} failed.")
    return " ".join(parts)


# -- MCP wiring ---------------------------------------------------------------

def build_server(config: Config, client: FreshBooksClient, auth):
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("freshbooks-timesheet")

    @mcp.tool()
    def start_auth() -> dict:
        """Begin FreshBooks authorization (one-time, run before any other tool).

        Returns an `authorize_url` the user must open and approve. FreshBooks
        then redirects to the configured redirect URI with a `code` query
        parameter — the page itself won't load, that's expected. The user copies
        that `code` value and you pass it to `finish_auth`. The code is
        single-use and expires within minutes, so call finish_auth promptly.
        """
        return handle_start_auth(auth, config)

    @mcp.tool()
    def finish_auth(code: str) -> dict:
        """Complete authorization with the `code` from the start_auth redirect.

        `code` is the value of the `code` query parameter in the redirect URL.
        On success the tokens are stored securely (OS keychain) and the time
        tools become usable. If it fails with an expired/invalid code, call
        start_auth again for a fresh URL.
        """
        return handle_finish_auth(auth, config, code)

    @mcp.tool()
    def auth_debug() -> dict:
        """Diagnostic: fingerprint the credentials the server actually loaded.

        Returns lengths and short SHA-256 prefixes (never the values) of the
        client_id / client_secret in effect, plus the redirect_uri, token
        backend, and whether a token is stored. Use this to confirm the
        connector passed the right credentials when authorization fails with
        invalid_client — compare the fingerprints to the known-good ones.
        """
        return handle_auth_debug(auth, config)

    @mcp.tool()
    def check_timesheet(
        period: str,
        date: str | None = None,
        expected_hours: float | None = None,
    ) -> dict:
        """Report logged/missing time for a day, week, or month (M–F).

        period: "day" | "week" | "month". date: anchor YYYY-MM-DD (default today).
        Lists days missing an entry and days logged under expected_hours.
        """
        return handle_check_timesheet(client, config, period, date, expected_hours)

    @mcp.tool()
    def log_time(
        period: str,
        hours: float,
        project_id: int,
        date: str | None = None,
        off_days: list[str] | None = None,
        note: str = "Logged via MCP",
        billable: bool = False,
        client_id: int | None = None,
        service_id: int | None = None,
        skip_existing: bool = True,
        dry_run: bool = False,
    ) -> dict:
        """Log `hours` per weekday (M–F) for a period against a project.

        project_id is REQUIRED — if the user hasn't named a project, call
        list_projects first and ask which to use. Use off_days (YYYY-MM-DD list)
        for PTO/holidays. Set dry_run=true to preview without writing. Skips days
        that already have entries unless skip_existing=false. Logs as the
        authenticated user only.
        """
        return handle_log_time(
            client, config, period, hours, project_id, date, off_days, note,
            billable, client_id, service_id, skip_existing, dry_run,
        )

    @mcp.tool()
    def list_time_entries(period: str, date: str | None = None) -> dict:
        """List individual time entries with their ids for a day/week/month.

        period: "day" | "week" | "month". date: anchor YYYY-MM-DD (default today).
        Use this to find the `id` of an entry before editing it with
        update_time_entry (e.g. to move it to a different project).
        """
        return handle_list_time_entries(client, config, period, date)

    @mcp.tool()
    def update_time_entry(
        entry_id: int,
        project_id: int | None = None,
        note: str | None = None,
        hours: float | None = None,
    ) -> dict:
        """Edit an existing time entry — e.g. move it to a different project.

        Find `entry_id` via list_time_entries. Provide at least one of
        project_id (reassign the project), note, or hours. Only the fields you
        pass are changed. If reassigning the project, confirm the target with
        list_projects first.
        """
        return handle_update_time_entry(client, config, entry_id, project_id, note, hours)

    @mcp.tool()
    def list_projects(active_only: bool = True, query: str | None = None) -> dict:
        """List the user's projects (id + title) to choose for logging time."""
        return handle_list_projects(client, active_only, query)

    @mcp.tool()
    def list_clients() -> dict:
        """List clients (needed when logging billable time)."""
        return handle_list_clients(client)

    @mcp.tool()
    def list_services() -> dict:
        """List services (optional; sets the billing rate on an entry)."""
        return handle_list_services(client)

    return mcp


def main() -> None:  # pragma: no cover - process entrypoint
    logging.basicConfig(level=logging.INFO)
    from .auth_manager import AuthManager

    config = Config.load()
    auth = AuthManager(config)
    client = FreshBooksClient(config, auth)
    server = build_server(config, client, auth)
    server.run()


if __name__ == "__main__":  # pragma: no cover
    main()
