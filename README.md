# Reminders — an Apple Reminders replica for Hermes Agent

A **self-contained reminder system** that lives inside your Hermes profile. It does
not touch Apple's Reminders app: it keeps its own SQLite database and delivers
notifications to your chat through a cron job.

## Why

Agents are good at *doing*, not at *remembering at the right time*. An in-session
todo list dies with the session, and system monitors are agent alerts, not your
reminders. This skill fills that gap: real reminders with due dates, advance
warnings, recurrences and nagging — delivered on their own, even when nobody is
talking to the agent.

## Features

**Structure**
- **No lists are created for you**: as in Apple Reminders, the built-in views
  (Today, Scheduled, All, Flagged, Urgent, Anytime, Completed) are *computed*,
  not lists. You create your own lists, and a reminder may have **no list at all**
- Lists with colour, icon, folder, pinned, muted, default
- Sections inside a list, plus a kanban **column view** grouped by section
- Grocery lists with automatic category grouping
- **Smart lists**: saved views filtered by tags, flag, urgent, priority, list,
  due-within-N-days, has/no date — combined with AND or ANY
- **Templates**: save a reminder as a template, apply it with a new date

**The reminder itself**
- Notes, URL, attachment reference, tags, priority (none/low/medium/high), flag
- **Urgent** — surfaced as `URGENT` in the notification
- **Subtasks** — completing a parent completes its children
- Location metadata (arriving / leaving)

**Timing**
- Due with a time, or all-day (notifies at a configurable hour)
- **Early reminder** — an advance warning (minutes to months before)
- **Multiple alarms per reminder** — offsets or absolute times
- **Recurrences**: hourly, daily, weekly (multi-day), monthly (fixed day of month
  or patterns like *last Friday*), yearly (multi-month), with arbitrary intervals,
  an end date, and *from completion* counting
- **Nagging**: overdue items re-notify with a backoff until you complete them

**Views**: today · scheduled · all · flagged · urgent · anytime · overdue ·
completed · week · nextweek · rolling 7 days. Weeks always start on Monday.

**Localisation**: English (default) and Italian, switchable at runtime, including
12/24-hour time, all-day notification time, and overdue rendering for all-day items.

## Install

```bash
# 1. copy the skill into your profile
mkdir -p ~/.hermes/profiles/<profile>/skills/productivity
cp -R skills/reminders ~/.hermes/profiles/<profile>/skills/productivity/

# 2. set it up (creates the database and the cron wrapper)
python3 ~/.hermes/profiles/<profile>/skills/productivity/reminders/scripts/setup.py

# 3. register the delivery job
python3 .../scripts/setup.py --register --deliver telegram
#    or, from a chat with the agent:
#    cronjob_manage(action="create", schedule="1m", name="Reminders — delivery",
#                    script="reminders_tick.sh", no_agent=True, deliver="origin")

# 4. verify
python3 .../scripts/setup.py --check
```

No API keys, no external services, no LLM calls on the notification path:
`no_agent` runs a script every minute and posts its stdout.

## Requirements

- Python 3.10+
- Hermes Agent with the cron scheduler running
- On a **satellite profile** (one that does not own the platform credentials), a
  target-exact `profile_route` carrying `chat_id` in `~/.hermes/config.yaml` is
  required, otherwise delivery fails closed with
  `platform '<name>' not configured/enabled`. See the Installation section of
  `skills/reminders/SKILL.md`.

## Usage

```bash
rem.py add "Call Marco" --list Work --due "tomorrow 9:00" --early 30m
rem.py add "Report" --due 2026-10-01 --repeat monthly:last:fri --repeat-until 2027-12-31
rem.py today | week | scheduled | urgent | overdue
rem.py done 12 | snooze 12 +15m | edit 12 --due "friday 10:00"
rem.py lists | lists add "Travel" --icon airplane | smart "Urgent" --urgent
rem.py alarms 12 add 1d --label "a day before"
rem.py settings set language it
```

`--json` on every command for programmatic use. Full command reference, date
formats, recurrence syntax and operating rules are in
[`skills/reminders/SKILL.md`](skills/reminders/SKILL.md).

## Not included

No UI, no sharing with other people, no sync with Apple Reminders or any external
task app, and no real geofencing for location reminders (location is metadata).
This is the agent's own reminder system.

## License

MIT
