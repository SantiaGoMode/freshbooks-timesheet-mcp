# Claude Desktop Extension (`.mcpb`)

This packages the FreshBooks Timesheet MCP server as a one-click **Claude
Desktop extension** — an [`.mcpb` bundle](https://github.com/modelcontextprotocol/mcpb)
(the format formerly called `.dxt`). Users drag it into Claude Desktop, fill in
their FreshBooks credentials, and authorize from the chat — no terminal, no
config files.

## How it works

- **Server type `uv`.** Dependencies are *not* vendored; `uv` resolves and
  installs them on the host at first launch from the bundled `pyproject.toml`.
  One bundle works on macOS/Windows/Linux.
- **Credentials** (`client_id` / `client_secret`) are `user_config` fields
  marked `sensitive`, so Claude Desktop stores them in the OS keychain and
  injects them as env vars — they never live in a file.
- **Authorization happens in the chat** via the `start_auth` / `finish_auth`
  tools: `start_auth` returns a URL to approve, you copy the `code` from the
  redirect URL, and `finish_auth` exchanges it. The code is single-use, expires
  in minutes, and is useless without the client secret. (FreshBooks requires
  HTTPS redirect URIs, so an automatic localhost-loopback capture isn't an
  option here.) The rotating OAuth refresh token is stored in the OS keychain
  (`FRESHBOOKS_TOKEN_BACKEND=keyring`) and survives restarts.

## Prerequisites

- **[`uv`](https://docs.astral.sh/uv/)** installed on the host (`brew install uv`,
  or `curl -LsSf https://astral.sh/uv/install.sh | sh`). uv provisions a
  Python 3.11+ interpreter itself, so a separate Python install isn't required.
- A FreshBooks OAuth app — see the repo's
  [GETTING_STARTED.md](../GETTING_STARTED.md) Step 1 for `client_id` / `secret`.
  Its registered redirect URI must match the connector's Redirect URI config
  (default `https://localhost/callback`).

## Build

```bash
./extension/build.sh
```

Produces `extension/freshbooks-timesheet-<version>.mcpb`. (Needs Node/`npx` for
the `mcpb` CLI; the script fetches it via `npx -y @anthropic-ai/mcpb`.)

## Install & use

1. Open **Claude Desktop → Settings → Extensions** and drag the `.mcpb` in
   (or double-click the file).
2. When prompted, paste your **Client ID** and **Client Secret**, confirm the
   redirect URI and timezone.
3. In a chat: **"Authorize FreshBooks."** The agent calls `start_auth` and gives
   you a URL — open it and approve. You'll be redirected to the redirect URI
   (the page won't load, that's expected); copy the `code` value from the address
   bar and paste it back. The agent calls `finish_auth` to store the tokens.
4. Then talk to it: *"Which days am I missing this week?"* /
   *"Log 8 hours Mon–Fri last week to the Acme project."*

## Notes

- The managed virtualenv is created at
  `~/.freshbooks-timesheet-mcp/venv-<version>` (`UV_PROJECT_ENVIRONMENT`), so it
  persists across launches, doesn't depend on the extracted bundle being
  writable, **and a version bump forces a fresh environment** — this is what
  guarantees a reinstalled bundle actually runs the new code rather than reusing
  a stale venv.
- Bump `version` in **`extension/manifest.json`** (including the
  `venv-<version>` path), the root `pyproject.toml`, and rebuild, for every
  release. Keeping the version in the venv path is required for the point above.
- To give the extension an icon, drop a square PNG at `extension/icon.png` and
  add `"icon": "icon.png"` back to the manifest — `build.sh` copies it if present.
- To re-authorize after a revoke, just run `start_auth` / `finish_auth` again.
