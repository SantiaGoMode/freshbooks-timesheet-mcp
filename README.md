# FreshBooks Timesheet MCP

An [MCP](https://modelcontextprotocol.io) server that lets an AI agent **check
timesheet status** and **log time** in FreshBooks, with secure OAuth2
refresh-token handling.

Ask your agent *"log 8 hours a day Monday–Thursday last week to the Acme
project, PTO Friday"* — it discovers your projects, confirms which one, and
writes the entries. Or *"which days am I missing this month?"* — it reports the
gaps, never flagging days that haven't happened yet.

> **👉 Setting it up? See [GETTING_STARTED.md](GETTING_STARTED.md)** — a
> ~10-minute guide. The recommended path is the one-click **Claude Desktop
> extension** (`.mcpb`); Docker and local Python are covered as alternatives.
> Build/dev details for the bundle live in [extension/](extension/README.md).
> This README is the reference for the app itself: tools, config, and internals.

---

## Features

- **`check_timesheet`** — report logged / missing / under-logged days for a
  day, week, or month (Mon–Fri). Future weekdays are never flagged as missing.
- **`log_time`** — log X hours per weekday against a project, with PTO/off-day
  exclusion, a dry-run preview, and automatic skipping of days already logged.
- **`list_time_entries` / `update_time_entry`** — list entries with their ids,
  then edit one (e.g. reassign it to a different project, or fix its note/hours).
- **`list_projects` / `list_clients` / `list_services`** — discovery so the
  agent can ask which project to log against before writing anything.
- **Secure auth** — OAuth2 with automatic access-token refresh and correct
  rotating-refresh-token handling. Tokens live in the OS keychain by default.
- **Timezone-correct** — all day boundaries are computed in your configured
  timezone, not UTC.

---

## Architecture

| Module | Responsibility |
|---|---|
| `server.py` | MCP interface — tool definitions + testable `handle_*` functions |
| `freshbooks_client.py` | HTTP wrapper: auth injection, 401→refresh→retry, pagination, `/me` auto-discovery |
| `auth_manager.py` | OAuth2 lifecycle: refresh, **rotation**, bootstrap CLI |
| `token_store.py` | Pluggable secure storage: OS keychain or encrypted file |
| `transformers.py` | Pure date math, unit conversion, report building |
| `config.py` / `models.py` | Config loading + typed data structures |

Tool *logic* lives in plain `handle_*` functions (with the client injected), so
it is fully unit-testable without the MCP runtime.

---

## Requirements

- **Docker Desktop** (recommended path), or **Python 3.11+** for a local install
- A FreshBooks account with a private OAuth2 app (client id + secret)

For the complete setup — creating the FreshBooks app, secrets, building, first
auth, and registering with your agent — follow **[GETTING_STARTED.md](GETTING_STARTED.md)**.

The short version of a local install:

```bash
python -m venv .venv && source .venv/bin/activate
pip install ".[dev]"          # installs the package, console scripts, and dev tools
cp .env.example .env          # then fill in your credentials (see Configuration)
```

> Install regular, **not** `-e`. (Editable installs are unreliable here — this
> Python's `site` module skips the trailing line of the build backend's `.pth`.)
> After changing source, re-run `pip install .` to refresh the console scripts.
> Tests always run against the source tree, so no reinstall is needed for `pytest`.

---

## Configuration

Set in `.env` (loaded automatically). Get the client credentials from
**my.freshbooks.com → Developer → your app**.

| Variable | Required | Notes |
|---|---|---|
| `FRESHBOOKS_CLIENT_ID` | ✅ | OAuth app client id |
| `FRESHBOOKS_CLIENT_SECRET` | ✅ | OAuth app secret |
| `FRESHBOOKS_REDIRECT_URI` | ✅ | Must **exactly** match the app's redirect URI (e.g. `https://localhost/callback`) |
| `FRESHBOOKS_BUSINESS_ID` | — | Auto-discovered via `/me` if blank |
| `FRESHBOOKS_IDENTITY_ID` | — | Auto-discovered via `/me` if blank |
| `FRESHBOOKS_TOKEN_BACKEND` | — | `keyring` (default) or `file` |
| `FRESHBOOKS_TOKEN_PATH` | — | Encrypted token file path (file backend only) |
| `FRESHBOOKS_TOKEN_KEY` | — | Fernet key for the file backend (see below) |
| `TZ` | — | Day/week/month boundary timezone (default `UTC`) |
| `DEFAULT_DAILY_HOURS` | — | Expected hours per day (default `8`) |
| `DEFAULT_START_TIME` | — | Local start time for logged entries (default `09:00`) |
| `MAX_LOG_DAYS` | — | Safety cap on days per `log_time` call (default `31`) |

For the encrypted-file token backend, generate a key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## Tools

### `check_timesheet(period, date?, expected_hours?)`
Report logged/missing days for `"day"`, `"week"`, or `"month"` (M–F).
`date` is the anchor (default today). Returns per-day status
(`logged` / `under` / `missing` / `future`), `missing_days`,
`under_logged_days`, totals, and a text summary.

### `log_time(period, hours, project_id, …)`
Log `hours` per weekday (M–F) for the period against a project.

| Param | Default | Notes |
|---|---|---|
| `project_id` | — | **Required.** No silent default — agent must confirm first. |
| `date` | today | Anchor date for the period |
| `off_days` | `[]` | List of `YYYY-MM-DD` to skip (PTO/holidays) |
| `note` | `"Logged via MCP"` | Entry note |
| `billable` | `false` | If true, **`client_id` is required** |
| `client_id` / `service_id` | — | For billable entries / billing rate |
| `skip_existing` | `true` | Skip days that already have entries |
| `dry_run` | `false` | Preview the plan without writing |

Weekends are always excluded. `hours` must be `0 < hours ≤ 24`. A single call
may not exceed `MAX_LOG_DAYS`.

### `list_time_entries(period, date?)`
List individual entries (with `id`, `date`, `hours`, `project_id`, `note`,
`billable`) for a `"day"` / `"week"` / `"month"`. Use it to find the `id` of an
entry to edit.

### `update_time_entry(entry_id, project_id?, note?, hours?, billable?, client_id?)`
Edit an existing entry — e.g. **move it to a different project** or **flip the
billable flag**. Look up `entry_id` via `list_time_entries`. Provide at least one
of `project_id`, `note`, `hours`, `billable`, or `client_id`; only the fields you
pass are changed (others are preserved). `hours` (if given) must be
`0 < hours ≤ 24`. Setting `billable=true` may require the entry to have a
`client_id` — pass one if FreshBooks rejects the change.

### `list_projects(active_only?, query?)` · `list_clients()` · `list_services()`
Discovery tools. The agent calls `list_projects` and asks which project to use
when one isn't specified, then passes the chosen `project_id` to `log_time`.

### Example output

![check_timesheet example](output_example.png)

---

## How it works (API notes)

Verified live against the FreshBooks API:

- **Two hosts.** Browser authorization is on `https://auth.freshbooks.com/oauth/authorize`;
  the token exchange and all data calls are on `https://api.freshbooks.com`.
- **Rotating refresh tokens.** Each refresh returns a *new* refresh token and
  invalidates the old one. The new token is persisted **before** use, under a
  lock, so concurrent refreshes can't break the chain.
- **Time entries use `businessId`** (not accountId) in the path. `duration` is
  in **seconds**; `started_at` is UTC with milliseconds + `Z`.
- **`identity_id` gotcha.** The list endpoint is already scoped to the
  authenticated user. Sending `identity_id` without `team=true` returns a 422
  (`"team must be true in order to use identity_id filter"`), so the client
  omits it for the self case and only sends `identity_id`+`team=true` for the
  admin-viewing-a-teammate case.
- **Billable requires a client.** `billable=true` needs `client_id` on accounts
  that bill per client.
- **Day-bucketing** for reports happens in your configured `TZ`, so a late-night
  entry lands on the correct local day.

---

## Security

- **Token storage** — OS keychain by default; encrypted-file fallback uses
  Fernet with the key supplied via env (never co-located with the ciphertext),
  `0600` permissions, and atomic writes.
- **No secret leakage** — `TokenSet` masks its values in `repr`; token request
  payloads and responses are never logged.
- **Server-side validation** — the calling agent is untrusted. Hours are
  clamped, dates validated, day counts capped, and `identity_id` is locked to
  the authenticated user (you can't log time as someone else).
- **Safe writes** — `log_time` requires an explicit `project_id`, defaults to
  `skip_existing=true`, supports `dry_run`, and never logs weekends.
- **Compromise recovery** — revoke the app authorization in the FreshBooks UI,
  then re-run `freshbooks-mcp-auth`.

---

## Development

```bash
pytest            # 48 tests; runs against the source tree

# or run the suite in Docker:
docker build --target test -t freshbooks-mcp:test . && docker run --rm freshbooks-mcp:test
```

```
freshbooks_mcp/   package (config, models, token_store, auth_manager,
                  freshbooks_client, transformers, server)
scripts/smoke.py  read-only live validation
tests/            unit tests for every module
```

---

## License

MIT © 2026 Cristino Santiago. See [LICENSE](LICENSE).
