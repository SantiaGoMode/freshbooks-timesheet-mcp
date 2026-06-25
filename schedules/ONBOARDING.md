# FreshBooks Timesheet Automation — Schedule Config

This project automates a weekly FreshBooks timesheet update driven by a Slack
reminder. The automation has **two halves on two different schedulers** because
of where each half's tools live.

| Half | What it does | Runs where | Schedule |
|---|---|---|---|
| **Reminder sender** | Posts the *Weekly Freshbooks Time Reminder* to Slack | **Cloud routine** (claude.ai) — Slack only | Fri **9:00 AM** `<YOUR_TIMEZONE>` |
| **Processor** | Reads the Slack reply, logs time in FreshBooks, confirms in-thread | **Local** `launchd` agent — needs the local FreshBooks MCP | Fri **5:00 PM** `<YOUR_TIMEZONE>` |

The workflow logic itself lives in [`CLAUDE.md`](./CLAUDE.md).

The weekly loop:
**Fri 9 AM** reminder posted → you reply in-thread → **Fri 5 PM** processor reads
the reply and logs to FreshBooks (never future-dated) → confirmation posted in
the thread.

---

## Prerequisites

- **Claude Code** installed locally (`claude` CLI on PATH).
- **FreshBooks MCP server** configured locally for this project (tools:
  `check_timesheet`, `list_projects`, `list_services`, `log_time`). This is a
  **local-only** server — it does not exist in the cloud, which is why the
  processor must run locally.
  - Token scope note: `list_services` needs `user:billable_items:read`, which
    the current token lacks. A billing service is optional, so logging still
    works without it.
- **Slack connector** connected in claude.ai (for the cloud reminder routine).
- **Slack channel:** `<#YOUR_CHANNEL_NAME>` (ID `<YOUR_CHANNEL_ID>`) — reminder
  is posted here; all replies/follow-ups stay in that thread.

---

## Part 1 — Reminder sender (cloud routine)

A claude.ai scheduled routine that posts the reminder. Slack-only, so it runs in
the cloud and fires even if the laptop is off.

- **Name:** `Weekly FreshBooks Time Reminder`
- **Routine ID:** `<YOUR_ROUTINE_ID>`
- **Schedule (cron, UTC):** `0 15 * * 5` = Friday 9 AM `<YOUR_TIMEZONE>` (MDT)
- **Model:** `claude-sonnet-4-6`
- **MCP:** Slack connector only
- **Manage:** https://claude.ai/code/routines/`<YOUR_ROUTINE_ID>`

> **DST caveat:** the cron is fixed in UTC. `0 15 UTC` = 9 AM during MDT
> (summer) but **8 AM** during MST (winter). Re-pin seasonally if exact time
> matters.

To create your own equivalent, use the `/schedule` skill (or the claude.ai
routines UI) with a Slack-only routine that posts the reminder text to your
channel and then stops.

---

## Part 2 — Processor (local `launchd` agent)

Launches Claude Code headlessly from the project directory so both `CLAUDE.md`
and the local FreshBooks MCP server load, then runs the workflow.

### Files

- **`run-timesheet-workflow.sh`** — wrapper invoked by the scheduler. Pins the
  node/nvm path (schedulers run with a bare environment), `cd`s into the project
  dir, and runs `claude -p "<run the workflow>" --dangerously-skip-permissions`,
  appending to a timestamped log under `logs/`.
- **`~/Library/LaunchAgents/<LAUNCHD_AGENT_LABEL>.plist`** — the
  `launchd` agent. Fires Friday 5 PM local time; if the Mac was asleep, runs the
  missed job on next wake.

### Install / manage the launchd agent

```sh
# Load (enable)
launchctl load ~/Library/LaunchAgents/<LAUNCHD_AGENT_LABEL>.plist

# Confirm it's registered
launchctl list | grep freshbooks

# Run it right now (manual test)
launchctl start <LAUNCHD_AGENT_LABEL>

# Unload (disable)
launchctl unload ~/Library/LaunchAgents/<LAUNCHD_AGENT_LABEL>.plist
```

Logs: `logs/timesheet-workflow_*.log` (per-run) and
`logs/launchd.out.log` / `logs/launchd.err.log` (launchd stdio).

### Simpler alternative: cron

If you don't need the wake-from-sleep behavior, a cron entry works (but only
fires if the Mac is awake at that moment):

```
0 17 * * 5 "/Users/<you>/Freshbooks MCP/run-timesheet-workflow.sh"
```

> **Heads-up:** `--dangerously-skip-permissions` is used so the unattended run
> isn't blocked on tool-permission prompts. Restrict to an explicit
> `--allowedTools` allowlist (FreshBooks + Slack tools) if you want tighter
> scope.

---

## Safety rules (enforced by the workflow)

- **Never log time for a future date**, even if asked for "the full week/month."
  Entries are capped at today.
- **Only log input that was actually given** — never invent or estimate hours.
- **If no project is named**, ask which one before logging (don't guess).
- Keep all follow-ups in the existing reminder thread.

---

## Adjusting the schedule

- **Reminder time:** edit the routine's cron at the claude.ai URL above, or via
  the `/schedule` skill.
- **Processor time:** edit `StartCalendarInterval` in the plist, then
  `launchctl unload` + `launchctl load` to apply.
