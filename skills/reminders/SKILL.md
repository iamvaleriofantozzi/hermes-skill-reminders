---
name: reminders
description: "Use when the user asks for a reminder (\"remind me to...\", \"what's due today?\") or wants a task system. A replica of Apple Reminders inside the agent: lists, sections, tags, priorities, urgent, early reminders, multiple alarms, patterned recurrences, smart lists, templates, grocery lists — delivered on schedule to any chat platform."
version: 3.0.0
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [reminders, todo, tasks, productivity, notifications, scheduling]
prerequisites:
  commands: [python3]
---

# Reminders — an Apple Reminders replica inside the agent

A **self-contained** reminder system that lives next to this skill. It does not
touch Apple's Reminders app: it keeps its own SQLite database and delivers
notifications to a chat through a cron job.

**Why it exists.** Agents are good at *doing*, not at *remembering at the right
time*. An in-session todo list dies with the session; system monitors are agent
alerts, not the user's reminders. This skill fills the gap: real reminders with
due dates, advance warnings, recurrences and nagging, delivered on their own
even when nobody is talking to the agent.

## Where the scripts are

`skill_view("reminders")` returns `skill_dir`; the scripts live in
`<skill_dir>/scripts`. From a shell, without knowing the profile:

```bash
S="$(ls -d "$HOME"/.hermes/profiles/*/skills/*/reminders/scripts 2>/dev/null | head -1)"
[ -z "$S" ] && S="$(ls -d "$HOME"/.hermes/skills/*/reminders/scripts 2>/dev/null | head -1)"
```

Every example below uses `python3 "$S/rem.py" …`.

## Installation

```bash
python3 "$S/setup.py"                                # database + cron wrapper (idempotent)
python3 "$S/setup.py" --register --deliver telegram  # also register the cron job
python3 "$S/setup.py" --check                         # diagnostics: what is missing
```

`setup.py` detects the profile hosting the skill by itself and creates:

1. the database at `<profile>/reminders/reminders.db`;
2. the wrapper `<profile>/scripts/reminders_tick.sh` (the scheduler only accepts
   scripts inside the profile's `scripts/` directory);
3. the cron job, every minute, `no_agent`, which runs the wrapper and delivers
   its output to the chat.

### The cron job

If `--register` is not possible (e.g. `hermes` is not on PATH), create the job
from the chat instead — that is the most reliable route, because `deliver` picks
up the origin:

```
cronjob_manage(action="create", schedule="1m", name="Reminders — delivery",
                script="reminders_tick.sh", no_agent=True, deliver="origin")
```

No LLM is involved: `no_agent` runs the script and posts its stdout. Empty
stdout = nothing is sent.

### Delivery requirement on satellite profiles

A profile that does **not own** the platform credentials (a satellite profile
fronted by another profile's gateway) cannot deliver: the attempt fails with
`platform '<name>' not configured/enabled`. It needs a **target-exact**
`profile_route` in `~/.hermes/config.yaml`:

```yaml
profile_routes:
  - name: <profile>-<platform>-dm
    platform: telegram
    profile: <profile>
    chat_id: "<chat_id>"        # without chat_id the route is not target-exact
```

Quick diagnosis: `hermes cron list` shows the delivery error on the last run.

## When to use it

- "Remind me to …", "set a reminder for …", "don't let me forget …"
- "What's due today?", "what's overdue?", "what do I have this week?"
- Completing, moving, snoozing, tagging or organising reminders
- Grocery lists

## When NOT to use it

- **Agent alerts** ("notify me when this site changes") → `cronjob_manage` directly: those are system monitors, not user reminders.
- **Events with other people / invitations** → the calendar tool, not this.
- **A checklist for the task at hand** → the in-session todo tool (it lives in the session and does not notify).

Rule of thumb: if it must **notify over time, independently of the
conversation**, it is a reminder and belongs here. If it is about **a process
running right now**, it belongs in the session todo tool.

## Model (mirrors Apple Reminders)

**No lists are created for you.** As in Apple Reminders, the built-in views
(Today, Scheduled, All, Flagged, Urgent, Anytime, Completed) are *computed*, not
lists — you create your own lists with `lists add`. A reminder may also have
**no list at all**: it then shows up in the computed views and in no list.

- **List** — colour, icon, folder, pinned, muted, default (all optional)
- **Section** — groups items inside a list; `columns` is the kanban view by section
- **Reminder** — title, notes, URL, attachment, tags, priority, flag, urgent, subtasks
- **Due** — timed or all-day
- **Early reminder** — advance warning before the due time (5m → 1 month)
- **Alarms** — multiple per reminder: early + due + extras
- **Recurrence** — hourly/daily/weekly/monthly/yearly, with patterns, intervals, an end date, and "from completion"
- **Location** — a place metadata field (arriving/leaving): no real geofencing

### Views

`today` · `scheduled` · `all` · `flagged` · `urgent` · `anytime` (no date) ·
`overdue` · `completed` · `week` (this week) · `nextweek` · `days7` (rolling 7 days)

**Weeks always start on Monday** (Monday→Sunday). This is not a configurable
preference: `week` and `nextweek` are Monday-aligned by construction.

### Smart lists

Saved filtered views: `--tags --flag --urgent --priority --from-list
--due-within N --has-date --no-date`, combined with AND (default) or `--any`.

## Commands

```bash
# create
rem.py add "Call Marco" --list Work --due "tomorrow 9:00" --early 30m
rem.py add "Report" --due 2026-10-01 --repeat monthly:last:fri --repeat-until 2027-12-31
rem.py add "Standup" --due "monday 9:30" --repeat weekly:mon,wed
rem.py add "Check" --due "tomorrow 8:00" --repeat daily:3 --repeat-from completion
rem.py add "Task" --due "today 18:00" --alarm 3h --alarm 17:30 --urgent --priority high
rem.py add "Shopping" --notes "..." --url "https://..." --attach ~/file.pdf --tags "home,shopping"
rem.py add "Prepare review" --section "To do" --list Work

# read
rem.py today | scheduled | all | flagged | urgent | anytime | overdue | completed | week
rem.py list Work            # one list (grocery lists → grouped by category)
rem.py columns Work         # kanban by section
rem.py tag work | tags
rem.py search keyword
rem.py show 12              # details, including alarms and subtasks

# change
rem.py done 12              # complete (or reopen); recurring ones roll forward
rem.py edit 12 --due "friday 10:00" --priority high --early 1h --urgent
rem.py snooze 12 +15m
rem.py alarms 12            # list
rem.py alarms 12 add 1d --label "a day before"
rem.py alarms 12 rm 3
rem.py delete 12

# organise
rem.py lists                # all lists
rem.py lists add "Travel" --icon airplane --folder "Personal" --pinned
rem.py lists default Work
rem.py section add Work "In progress" | section Work | section rm Work "In progress"
rem.py smart "Urgent" --urgent | smart "Next 3 days" --due-within 3
rem.py template save "Deliverable" --from 12 | template apply "Deliverable" --list Work
rem.py settings | settings set language it | settings set nag_minutes 15
rem.py stats
```

`--json` on any command for programmatic use.

## Date formats (`--due`)

| Form | Example |
|---|---|
| relative | `+30m` `+2h` `+3d` `+1w` |
| day | `today` `tomorrow` `day after tomorrow` `yesterday` |
| day + time | `tomorrow 9:00` |
| weekday | `monday` `friday 10:00` (next occurrence) |
| absolute | `2026-10-12` `2026-10-12 10:30` |
| time only | `18:30` (today, or tomorrow if already past) |

A date without a time is an **all-day** reminder: it notifies at
`all_day_notify_time` (default 09:00). Italian forms (`domani`, `lunedì`, …) are
also accepted.

## Recurrences (`--repeat`)

```
daily  weekly  monthly  yearly  hourly          interval: daily:3, hourly:6
weekly:mon,wed        weekly:2:mon
monthly:15            monthly:2:15       (day of month)
monthly:last:fri      monthly:second:mar (first/second/third/fourth/last)
yearly:mar,jun        yearly:2:mar
```

`--repeat-until 2027-12-31` ends the series.
`--repeat-from completion` restarts the count from when you complete it, instead
of from the due date.

## Early reminders and alarms

- `--early 30m|2h|1d|1w|1M` = advance warning before the due time (as Apple does)
- `--alarm` is **repeatable**: several alarms per reminder. An offset (`3h`) or an absolute time (`18:00`)
- With neither `--early` nor `--alarm`, the only notification is at the due time
- `--no-alarm` for no notification at all
- `show <id>` / `alarms <id>` list every alarm with its exact fire time
- `alarms <id> add <when> [--label …]` adds an alarm later; `alarms <id> rm <alarm_id>` removes one

## Delivery and nagging

`tick.py`, run by the cron job every minute:

1. fires the alarms that are past and not yet sent (early, due, extras);
2. **nags**: if a reminder is overdue and not completed, it notifies again with
   a backoff (`nag_minutes` × 2^n) up to `nag_max` times — Apple's behaviour,
   which keeps reminding you until you close the item;
3. skips **muted** lists (`lists mute`) and completed reminders.

To stop the nagging: complete the reminder, or `settings set nag_max 0`.

## Localisation

```bash
rem.py settings set language it              # en (default) | it
rem.py settings set time_format 12           # 12 | 24
rem.py settings set all_day_notify_time 08:30
rem.py settings set show_all_day_overdue 1   # overdue all-day items show as SCADUTO/OVERDUE
rem.py settings set nag_minutes 10
rem.py settings set nag_max 6
```

The first day of the week is **not** configurable: it is always Monday.

## Operating rules

1. **Confirm before creating** when the request is vague ("remind me to call someone"): ask who and when.
2. **Ambiguous date → ask.** Never invent a time: a reminder at 09:00 when the user meant 18:00 is worse than no reminder.
3. **Actionable titles**: "Send the report to Marco", not "report".
4. **Fetch the ID from a view** before `done`/`edit`/`delete`: never guess it.
5. **Complete only when it is actually done**, never on intent.
6. After an `add`, report **what** was created and **when** it will notify.
7. A completed recurring reminder **is not archived**: it rolls to the next occurrence. To stop it, `edit --repeat none`.

## Pitfalls

- `--list` creates the list when missing: a typo produces a ghost list. Check with `lists`.
- `monthly:27` is *day 27 of each month*; for "every 2 months on the 27th" use `monthly:2:27`.
- Location is **metadata only**: no geofencing (the agent has no location access).
- No UI, no sharing with other people, no sync with external apps: this is the agent's own system.
- **Satellite profile**: without a `profile_route` carrying `chat_id`, delivery fails closed (`platform … not configured/enabled`). See *Installation*.
- The cron runs **every minute**: precision is within ~60s, not to the second.
- `done` on a **parent** reminder also completes its subtasks.
- `tick.py` is the only thing that marks alarms as sent: running it by hand fires the notifications for real (use `--dry-run` to look without sending).
- The database lives at `<profile>/reminders/reminders.db`: keep it out of version control if the profile is inside a repo.
- When adding a catalogue message, add it to **both** `en` and `it` in `i18n.py` — a missing key silently falls back to English.
