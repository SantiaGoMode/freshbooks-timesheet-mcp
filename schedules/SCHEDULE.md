# FreshBooks Timesheet Update Workflow

## How this is wired up (architecture)

This workflow has two halves that run on different schedules:

- **Reminder sender** — a **cloud routine** (claude.ai, Slack-only) posts the
  *Weekly Freshbooks Time Reminder* to the channel below every **Friday ~9 AM
  `<YOUR_TIMEZONE>`**. It needs only Slack, so it runs in the cloud and fires
  even when my laptop is off.
- **Processor (this workflow)** — runs **locally** via a `launchd` agent
  (`<LAUNCHD_AGENT_LABEL>`, Friday 5 PM `<YOUR_TIMEZONE>`) because the
  FreshBooks tools are a **local-only MCP server** (`mcp__freshbooks__*`) that
  does not exist in the cloud. It launches Claude Code headlessly from this
  project directory via `run-timesheet-workflow.sh`, reads my Slack reply, and
  logs the time. If the Mac was asleep at 5 PM, `launchd` runs the missed job on
  the next wake.

## Key facts

- **Channel:** `<#YOUR_CHANNEL_NAME>` (ID `<YOUR_CHANNEL_ID>`) — the reminder is
  posted here and all replies/follow-ups stay in that thread.
- **My identity:** `<YOUR_EMAIL>` (Slack user `<YOUR_SLACK_USER_ID>`).
- **Expectations:** 8 hours/day, Monday–Friday, unless I say otherwise.
- **Services:** `list_services` currently fails — the FreshBooks token is
  missing the `user:billable_items:read` scope. A billing service is optional,
  so log without it; entries succeed regardless.

## Trigger

Run this workflow after the **Weekly Freshbooks Time Reminder** has fired and I
have had a chance to reply in Slack. The goal is to take my reply and update my
FreshBooks timesheet, or to chase me down if I haven't replied yet.

## Steps

1. **Find today's reminder.** Locate the most recent **Weekly Freshbooks Time
   Reminder** message posted today in `<#YOUR_CHANNEL_NAME>`
   (`<YOUR_CHANNEL_ID>`). Work in that message's thread.

2. **Check for my response.** Look for a reply from me
   (`<YOUR_EMAIL>`, Slack `<YOUR_SLACK_USER_ID>`) in that thread that
   came *after* today's reminder.

3. **If I have responded:**
   - Parse my reply for the hours, dates, and client/project.
   - Before logging, confirm the current state with `check_timesheet`, and use
     `list_projects` to resolve project names to IDs. (`list_services` is
     unavailable — see Key facts; a service is optional.)
   - **If I didn't name a project**, don't guess — list the active projects in
     the thread and ask me to pick before logging.
   - Log the time with `log_time`.
   - **Never log time for any date in the future** — even if I say "the full
     week" or "the full month," cap entries at today's date. (See memory:
     no-future-time-entries.)
   - Reply in the Slack thread summarizing what was logged (dates, hours,
     project) so I can confirm it's correct.

4. **If I have NOT responded:**
   - Send a follow-up message in the same Slack thread reminding me to send my
     hours so the timesheet can be updated. Do not log anything until I respond.
   - There is **no self-rescheduling in a headless run** — the next scheduled
     run (or a manual run) re-checks the thread and picks up my reply then. Do
     not assume a separate 24-hour timer will fire.

## Notes

- Only act on input I have actually given — never invent or estimate hours.
- Keep follow-ups in the existing reminder thread so the conversation stays in
  one place.
