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
from freshbooks_mcp.freshbooks_client import (  # noqa: E402
    FreshBooksClient,
    FreshBooksError,
)
from freshbooks_mcp.server import (  # noqa: E402
    handle_check_timesheet,
    handle_list_clients,
    handle_list_projects,
    handle_list_services,
    handle_list_time_entries,
    handle_update_time_entry,
)


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="anchor date YYYY-MM-DD (default: today)")
    parser.add_argument(
        "--write-test",
        action="store_true",
        help="also run a REVERSIBLE write round-trip (flip an entry's billable "
        "flag and flip it back) to verify update_time_entry live",
    )
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
            print(
                f"  business    : {biz.get('name')} "
                f"(account_id={biz.get('account_id')})"
            )

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

        _section("4. list_time_entries (this week)")
        entries = handle_list_time_entries(
            client, config, "week", date_str=args.date
        )["entries"]
        print(f"  {len(entries)} entry(ies)")
        for e in entries[:10]:
            print(
                f"  id {e['id']}  {e['date']}  {e['hours']}h  "
                f"project {e['project_id']}"
            )

        # Per-tool scope checks — a missing scope only 403s on its own endpoint,
        # so every read tool must be exercised individually (this is the gap that
        # let list_clients/list_services scope errors slip through before).
        scope_gaps = []

        _section("5. list_clients")
        try:
            clients = handle_list_clients(client)["clients"]
            print(f"  {len(clients)} client(s)")
        except FreshBooksError as exc:
            if exc.status != 403:
                raise
            scope_gaps.append(("list_clients", "user:clients:read"))
            print("  ⚠ 403 — token is missing scope `user:clients:read`")

        _section("6. list_services")
        try:
            services = handle_list_services(client)["services"]
            print(f"  {len(services)} service(s)")
        except FreshBooksError as exc:
            if exc.status != 403:
                raise
            scope_gaps.append(("list_services", "user:billable_items:read"))
            print("  ⚠ 403 — token is missing scope `user:billable_items:read`")

        if args.write_test:
            _section("7. update_time_entry (REVERSIBLE write round-trip)")
            entries = handle_list_time_entries(
                client, config, "week", date_str=args.date
            )["entries"]
            if not entries:
                print("  (no entries this week to test against — skipped)")
            else:
                e = entries[0]
                eid, original = e["id"], bool(e["billable"])
                print(f"  target id {eid} (billable={original})")
                # flip it
                flipped = handle_update_time_entry(
                    client, config, eid, billable=not original,
                    client_id=e.get("client_id"),
                )["updated"]["billable"]
                print(f"  flipped -> billable={flipped}")
                # restore it
                restored = handle_update_time_entry(
                    client, config, eid, billable=original,
                    client_id=e.get("client_id"),
                )["updated"]["billable"]
                print(f"  restored -> billable={restored}")
                if flipped != (not original) or restored != original:
                    print("  ✗ write round-trip did not behave as expected")
                    return 6
                print("  ✓ update_time_entry write verified and reverted")

    except AuthError as exc:
        print(f"\n✗ Auth error: {exc}")
        return 3
    except FreshBooksError as exc:
        print(f"\n✗ API error (status={exc.status}): {exc}")
        return 4

    if scope_gaps:
        _section("Result")
        print("✗ Some read tools are missing OAuth scopes:")
        for tool, scope in scope_gaps:
            print(f"   - {tool} needs `{scope}`")
        print("\nAdd the scope(s) to your FreshBooks app, then RE-AUTHORIZE —")
        print("scopes don't apply to existing tokens (see GETTING_STARTED Step 1).")
        return 5

    if args.write_test:
        print("\n✅ Smoke test passed — all tools work; write round-trip reverted.")
    else:
        print("\n✅ Smoke test passed — all read tools work. No data written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
