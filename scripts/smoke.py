#!/usr/bin/env python3
"""Read-only live smoke test for the FreshBooks MCP.

Validates auth + API shapes against a real account WITHOUT writing anything:
  1. /me identity + business/account discovery
  2. list_projects
  3. check_timesheet for the current week

Prereqs:
  - `.env` filled with FRESHBOOKS_CLIENT_ID / FRESHBOOKS_CLIENT_SECRET
  - one-time auth done: `freshbooks-mcp-auth`

Run:  python scripts/smoke.py        (or with --date YYYY-MM-DD)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the package importable when run directly from the repo.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from freshbooks_mcp.auth_manager import AuthError, AuthManager  # noqa: E402
from freshbooks_mcp.config import Config  # noqa: E402
from freshbooks_mcp.freshbooks_client import FreshBooksClient, FreshBooksError  # noqa: E402
from freshbooks_mcp.server import handle_check_timesheet, handle_list_projects  # noqa: E402


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="anchor date YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    config = Config.load()
    try:
        config.require_oauth()
    except ValueError as exc:
        print(f"✗ {exc}\n  Fill in .env (see .env.example).")
        return 2

    auth = AuthManager(config)
    client = FreshBooksClient(config, auth)

    try:
        _section("1. Identity (/me)")
        me = client.me()
        print(f"  identity_id : {client.identity_id}")
        print(f"  business_id : {client.business_id}")
        memberships = me.get("business_memberships") or []
        if memberships:
            biz = memberships[0].get("business", {})
            print(f"  business    : {biz.get('name')} (account_id={biz.get('account_id')})")

        _section("2. Projects")
        projects = handle_list_projects(client)["projects"]
        if not projects:
            print("  (no active projects found)")
        for p in projects[:20]:
            print(f"  [{p['project_id']}] {p['title']}  (client_id={p['client_id']})")
        if len(projects) > 20:
            print(f"  … and {len(projects) - 20} more")

        _section("3. check_timesheet (this week)")
        report = handle_check_timesheet(client, config, "week", date_str=args.date)
        print("  " + report["summary"])
        print(json.dumps(report["days"], indent=2))

    except AuthError as exc:
        print(f"\n✗ Auth error: {exc}")
        return 3
    except FreshBooksError as exc:
        print(f"\n✗ API error (status={exc.status}): {exc}")
        return 4

    print("\n✅ Smoke test passed — auth and read endpoints work. No data written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
