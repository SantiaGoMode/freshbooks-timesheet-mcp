# Getting Started

A step-by-step guide to go from zero to logging time through the FreshBooks
Timesheet MCP. Two tracks — **Docker** (recommended) and **local Python**.
Most people want Docker; it's the path shown first at each step.

**Time:** ~10 minutes. **You'll need:** a FreshBooks account, and Docker
Desktop (or Python 3.11+ for the local track).

---

## Step 1 — Create a FreshBooks app (get your client id & secret)

The MCP talks to FreshBooks as an OAuth2 app, so you create a private app once
and copy its credentials.

1. Log in to FreshBooks at **https://my.freshbooks.com**.
2. Open the **Developer Portal**: profile menu → **Developers**, or go directly
   to **https://my.freshbooks.com/#/developer**.
3. Click **Create an App** (a.k.a. "New Application"). Give it a name like
   `Timesheet MCP` and a short description.
4. Set the **Redirect URI** to exactly:
   ```
   https://localhost/callback
   ```
   This must match character-for-character or the login step will be rejected.
5. If prompted for scopes/permissions, grant access to **time tracking** and
   **your user/identity** (these cover everything the MCP does). user:profile:read, user:time_entries:read, user:time_entries:write, user:projects:read 
6. Save, then copy the generated **Client ID** and **Client Secret** — you'll
   paste them in Step 3.

> The portal's exact labels change occasionally; the pieces you need are always:
> a Client ID, a Client Secret, and a Redirect URI.

---

## Step 2 — Get the code

```bash
git clone https://github.com/SantiaGoMode/freshbooks-timesheet-mcp.git
cd freshbooks-timesheet-mcp
cp .env.example .env          # non-secret settings; safe to leave as defaults
```

`.env` holds **only non-secret config** (timezone, default hours). Your
credentials go in Docker secrets in the next step — never in `.env` or git.

---

## Step 3 — Create your secrets

The container reads three secrets from files under `secrets/` (this directory
is gitignored, so nothing sensitive is ever committed):

```bash
mkdir -p secrets

# paste the values from Step 1 (printf avoids a trailing newline):
printf %s 'PASTE_CLIENT_ID'     > secrets/fb_client_id
printf %s 'PASTE_CLIENT_SECRET' > secrets/fb_client_secret

# generate the token-encryption key (keep it stable — see note):
python3 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())" > secrets/fb_token_key
```

> ⚠️ The Fernet key encrypts your stored tokens. If you regenerate it later, the
> existing token can't be decrypted and you'll just re-run Step 5. Keep this
> file safe and stable.

No Python locally? Generate the key in a container instead:

```bash
docker run --rm python:3.13-slim sh -c \
  "pip -q install cryptography && python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())'" \
  > secrets/fb_token_key
```

---

## Step 4 — Build

```bash
docker compose build
```

---

## Step 5 — Authenticate (one-time)

```bash
docker compose run --rm app freshbooks-mcp-auth
```

This prints an authorization URL.

1. Open the URL in your browser and **approve** the app.
2. You're redirected to `https://localhost/callback?code=...&state=...`. **The
   page won't load — that's expected.** Copy the **`code`** value from the
   address bar.
3. Paste it at the `Paste the authorization code here:` prompt and press Enter.

You should see `✅ Tokens stored securely.` The encrypted token is now in the
`fb-tokens` Docker volume and survives restarts.

> The code is single-use and expires within minutes — paste it promptly. If it
> fails with `invalid_grant`, just run the command again for a fresh URL.

---

## Step 6 — Verify (read-only)

```bash
docker compose run --rm app python scripts/smoke.py
```

You should see your identity/business, your projects, and a `check_timesheet`
report for the current week. Nothing is written.

---

## Step 7 — Use it from your AI agent

Register the containerized server with an MCP client (e.g. Claude Desktop's
`claude_desktop_config.json` or Claude Code's `.mcp.json`):

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

Then talk to it naturally:

- *"Which days am I missing this week?"* → runs `check_timesheet`.
- *"Log 8 hours Monday–Friday last week to the Acme project."* → the agent calls
  `list_projects`, confirms the project, and runs `log_time`.

See the [README](README.md) for the full tool reference and options
(`dry_run`, `off_days` for PTO, billable entries, etc.).

---

## Local (no Docker) track

Prefer running it directly? Tokens go in your OS keychain instead of a volume.

```bash
python -m venv .venv && source .venv/bin/activate
pip install ".[dev]"

# put credentials in .env (keychain backend keeps tokens in the OS keychain):
#   FRESHBOOKS_CLIENT_ID=...
#   FRESHBOOKS_CLIENT_SECRET=...
freshbooks-mcp-auth        # interactive: prints URL, prompts for code
python scripts/smoke.py
```

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Fernet key must be 32 url-safe base64-encoded bytes` | The key isn't a real key. `.env` does **not** run `$(...)`; the key must be a literal 44-char value. Regenerate into `secrets/fb_token_key` (Step 3). |
| Authorize URL returns **not found** | Wrong host — it must be `https://auth.freshbooks.com/oauth/authorize`. Re-run `freshbooks-mcp-auth` to get the correct URL. |
| `invalid_grant` during auth | The code expired or was already used. Re-run `freshbooks-mcp-auth` for a fresh URL and paste the new code quickly. |
| Redirect rejected / "redirect_uri mismatch" | The app's Redirect URI must exactly equal `FRESHBOOKS_REDIRECT_URI` (default `https://localhost/callback`). Fix it in the FreshBooks Developer Portal. |
| `Missing required config: FRESHBOOKS_CLIENT_ID...` | Secret files are empty or not mounted. Check `secrets/fb_client_id` / `fb_client_secret` exist and contain the values. |
| Tokens "disappear" after restart | The token must persist — make sure you're using the `fb-tokens` volume (don't run with `--no-TTY` hacks that skip compose). It rotates on every refresh. |
| `check_timesheet` shows everything missing | You genuinely haven't logged yet, or `TZ` is wrong for your week boundaries. Set `TZ` in `.env`. |
