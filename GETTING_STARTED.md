# Getting Started

Go from zero to logging time through the FreshBooks Timesheet MCP.

**Recommended:** the one-click Claude Desktop extension (`.mcpb`). **Docker** and
**local Python** are alternatives for Claude Code, other MCP clients, or headless
use. Claude Desktop is **macOS/Windows only** — on Linux, use an alternative.

Every path needs the FreshBooks app from **Step 1**.

---

## Step 1 — Create a FreshBooks app

The MCP talks to FreshBooks as an OAuth2 app. Create one once and copy its
credentials.

1. Log in at **https://my.freshbooks.com** and open the **Developer Portal**
   (profile menu → **Developers**, or **https://my.freshbooks.com/#/developer**).
2. **Create an App** — name it e.g. `Timesheet MCP`.
3. Set the **Redirect URI** to exactly `https://localhost/callback` (FreshBooks
   requires HTTPS; it must match character-for-character).
4. Add these **scopes**:
   ```
   user:profile:read         # identity / business discovery (/me)
   user:time_entries:read    # check_timesheet
   user:time_entries:write   # log_time
   user:projects:read        # list_projects
   user:billable_items:read  # list_clients / list_services
   ```
5. Save, then copy the **Client ID** and **Client Secret**.

> The credentials identify the *app*, not a person — a team can share one app's
> Client ID/Secret, and each member authorizes with their own login (Step 2e).

---

## Step 2 — Install the Claude Desktop extension (recommended)

### 2a. Install `uv`

Claude Desktop bundles Node.js, **not uv or Python** — so this `uv`-type
extension needs `uv` on the machine, installed system-wide so the GUI sees it on
PATH:

```bash
# macOS
brew install uv
# Windows
winget install --id=astral-sh.uv -e
```

> If install fails with **"incompatible with your device"**, Claude Desktop
> didn't find system Python. Install it too (macOS: `brew install python@3.12`,
> Windows: `winget install Python.Python.3.12`); uv still manages the real
> runtime ([mcpb#84](https://github.com/modelcontextprotocol/mcpb/issues/84)).

### 2b. Get the `.mcpb` bundle

Easiest: have a teammate share the built `.mcpb` — then you only need `uv`, no
clone or toolchain.

To build it yourself (needs Node/`npx` + `python3`; run on macOS/Linux or via
Git Bash/WSL on Windows):

```bash
git clone https://github.com/SantiaGoMode/freshbooks-timesheet-mcp.git
cd freshbooks-timesheet-mcp
./extension/build.sh          # → extension/freshbooks-timesheet-<version>.mcpb
```

### 2c. Install and configure

In **Claude Desktop → Settings → Extensions**, drag in the `.mcpb`. When
prompted, fill in:

| Field | Value |
|---|---|
| **Client ID** / **Client Secret** | from Step 1 (stored in your OS credential store) |
| **Redirect URI** | `https://localhost/callback` (must match Step 1) |
| **Timezone** | e.g. `America/Denver` |
| **Default daily hours** / **Max days** | defaults are fine (`8` / `31`) |

### 2d. Authorize (one-time)

Say **"Authorize FreshBooks."** The agent returns a URL — open it, approve, and
you'll be redirected to `https://localhost/callback?code=...` (**the page won't
load, that's expected**). Copy the `code` from the address bar and paste it back;
the agent stores the tokens in your OS keychain / Credential Manager.

> The code is single-use and expires in minutes. If auth fails with
> `invalid_client`, ask the agent to run **`auth_debug`** (see Troubleshooting).

### 2e. Use it

- *"Which days am I missing this week?"* → `check_timesheet`
- *"Log 8 hours Mon–Fri last week to the Acme project."* → the agent confirms the
  project via `list_projects`, then runs `log_time`.

See the [README](README.md) for the full tool reference and options.

---

## Alternative: Docker

For Claude Code / other clients or headless use. Credentials come from Docker
secrets; tokens persist in a named volume (the container can't reach the OS
keychain, so it uses the encrypted-file backend).

```bash
git clone https://github.com/SantiaGoMode/freshbooks-timesheet-mcp.git
cd freshbooks-timesheet-mcp
cp .env.example .env          # non-secret settings only

mkdir -p secrets
printf %s 'PASTE_CLIENT_ID'     > secrets/fb_client_id
printf %s 'PASTE_CLIENT_SECRET' > secrets/fb_client_secret
python3 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())" > secrets/fb_token_key

docker compose build
docker compose run --rm app freshbooks-mcp-auth        # one-time auth
docker compose run --rm app python scripts/smoke.py    # read-only check
```

> The Fernet key encrypts your tokens — keep `secrets/fb_token_key` stable
> (regenerating it means re-running auth).

Register with your MCP client:

```json
{
  "mcpServers": {
    "freshbooks": {
      "command": "docker",
      "args": ["compose", "-f", "/ABS/PATH/TO/docker-compose.yml", "run", "--rm", "-T", "app"]
    }
  }
}
```

---

## Alternative: local Python

Runs directly; tokens go in your OS keychain / Credential Manager.

```bash
git clone https://github.com/SantiaGoMode/freshbooks-timesheet-mcp.git
cd freshbooks-timesheet-mcp

# macOS/Linux
python -m venv .venv && source .venv/bin/activate
# Windows
python -m venv .venv; .venv\Scripts\activate

pip install ".[dev]"

# put FRESHBOOKS_CLIENT_ID / FRESHBOOKS_CLIENT_SECRET in .env, then:
freshbooks-mcp-auth           # prints URL, prompts for code
python scripts/smoke.py
```

Register with your MCP client (use the platform's script path):

```json
{
  "mcpServers": {
    "freshbooks": {
      "command": "/abs/path/.venv/bin/freshbooks-mcp",
      "env": { "TZ": "America/Denver" }
    }
  }
}
```

> Windows command path: `C:\abs\path\.venv\Scripts\freshbooks-mcp.exe`.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Extension **"incompatible with your device"** / won't install | Claude Desktop didn't find system Python. Install it (macOS `brew install python@3.12`, Windows `winget install Python.Python.3.12`) and retry. |
| **Extension won't start / `uv` not found** | Install `uv` system-wide (Step 2a — macOS `brew install uv`, Windows `winget install --id=astral-sh.uv -e`) and restart Claude Desktop. |
| `invalid_client` during auth | Client ID/Secret don't match a live app (or have stray whitespace). Ask the agent to run **`auth_debug`** — it fingerprints the loaded credentials (no secrets) so you can confirm them. Re-enter in the connector config if wrong. |
| `invalid_grant` during auth | The code expired or was reused. Run `start_auth` again and paste a fresh code quickly. |
| Redirect rejected / "redirect_uri mismatch" | The app's Redirect URI must exactly equal the connector's (`https://localhost/callback`). Fix it in the Developer Portal. |
| **Stale auth / want to reset** | Re-running `start_auth` → `finish_auth` overwrites the stored token. To fully clear it: macOS — Keychain Access, delete `freshbooks-timesheet-mcp`; Windows — Credential Manager → Windows Credentials → remove `freshbooks-timesheet-mcp` (or `cmdkey /delete:freshbooks-timesheet-mcp`). |
| `check_timesheet` shows everything missing | You haven't logged yet, or the timezone is wrong. Set **Timezone** (extension) or `TZ` (Docker/local). |
| **(Docker)** `Fernet key must be 32 url-safe base64-encoded bytes` | Regenerate a literal key into `secrets/fb_token_key` (`.env` doesn't run `$(...)`). |
| **(Docker)** `Missing required config: FRESHBOOKS_CLIENT_ID...` | `secrets/fb_client_id` / `fb_client_secret` are empty or not mounted. |
