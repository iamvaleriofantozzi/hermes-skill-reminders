# ⏰ Reminders

### An Apple Reminders replica for Hermes Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org)
[![Platform: macOS | Linux](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg)](#-requirements)
[![For Hermes Agent](https://img.shields.io/badge/for-Hermes%20Agent-8A2BE2.svg)](https://github.com/NousResearch/hermes-agent)
![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen.svg)

> Agents are brilliant at *doing*. They're terrible at *remembering at the right moment*.
>
> This fixes that. 🔔

A self-contained reminder system that lives inside your Hermes profile — lists,
due dates, early warnings, recurrences and gentle (then not-so-gentle) nagging,
delivered straight to your chat even when nobody is talking to the agent.

No API keys. No external services. No LLM calls on the notification path.
Just a SQLite file and a cron job that minds its own business. 🧘

---

## 🤔 Why this exists

Every agent has the same two tools, and neither one actually reminds you:

| | |
|---|---|
| 📝 **In-session todo list** | Dies the moment the session ends. Great for "where was I?" — useless for "don't forget Thursday". |
| 🔍 **System monitors** | Built for the agent's own alerts ("the site changed"), not for *your* life. |

So here's the missing third thing: **reminders that survive the conversation**.
Set them and walk away. Your chat pings you when it matters. 💬

---

## ✨ What you get

### 🗂️ Structure
- **Lists** with colour, icon, folder, pinned, muted, and a default
- **No lists are forced on you** — as in Apple Reminders, the built-in views
  (Today, Scheduled, All, Flagged, Urgent, Anytime, Completed) are *computed*,
  not lists. Create yours, or don't. A reminder can even have **no list at all** 👌
- **Sections** inside a list, plus a kanban **column view** grouped by section
- 🛒 **Grocery lists** with automatic category grouping
- 💾 **Templates** — save a reminder as a template, stamp it out with a new date
- 🎯 **Smart lists** — saved views filtered by tag, flag, urgent, priority, list,
  due-within-N-days, has/no date… combined with ALL or ANY

### 📌 The reminder itself
- Notes, URL, attachment reference, tags, priority, flag
- 🔥 **Urgent** — shows up as `URGENT` when it lands
- 🪆 **Subtasks** — finish the parent, the children close themselves
- 📍 Location metadata (arriving / leaving)

### ⏳ Estimates — the bit Apple doesn't have
- ⏱️ **`--est 45m`** — how long the task actually takes. Optional, never nags you
- 🎯 **`rem.py fits 30m`** — *"I've got half an hour, what can I close?"*
- 🧩 **`--pack`** — picks a subset that adds up to *within* the slot
- 🪆 **Rolls up from subtasks** — three 30m children make the parent `[1h30]`
- ➗ **Totals in every view** — `5 with an estimate (~3h15) · 1 without`

Ordered **overdue → due today → priority → largest that still fits**, because a
plain "shorter than 30 minutes" filter hands you five trivial items and calls it
progress.

### ⏱️ Timing
- Due with a time, or **all-day** (pings at an hour you choose)
- ⏳ **Early reminder** — an advance nudge, from 5 minutes to a month before
- 🔔 **Multiple alarms per reminder** — offsets (`3h`) or absolute times (`18:00`)
- 🔁 **Recurrences** — hourly, daily, weekly (multi-day), monthly (fixed day *or*
  patterns like *last Friday*), yearly (multi-month), with intervals, an end date,
  and *count-from-completion* mode
- 😤 **Nagging** — overdue items re-notify with a backoff until you actually close
  them. Like Apple does. It will not let go. 💪

### 👀 Views
`today` · `scheduled` · `all` · `flagged` · `urgent` · `anytime` · `overdue` ·
`completed` · `week` · `nextweek` · rolling `days7`

🗓️ **Weeks always start on Monday.** Not configurable — it's a fact, not a preference.

### 🌍 Languages
English and Italian, switchable at runtime, including 12/24-hour time, the
all-day notification hour, and how overdue all-day items are rendered.

---

## 🚀 Quick start

```bash
# 1️⃣  drop the skill into your profile
mkdir -p ~/.hermes/profiles/<profile>/skills/productivity
cp -R skills/reminders ~/.hermes/profiles/<profile>/skills/productivity/

# 2️⃣  set it up — creates the database and the cron wrapper
python3 ~/.hermes/profiles/<profile>/skills/productivity/reminders/scripts/setup.py

# 3️⃣  register the delivery job
python3 .../scripts/setup.py --register --deliver telegram
#     or just ask your agent:
#     cronjob_manage(action="create", schedule="1m", name="Reminders — delivery",
#                     script="reminders_tick.sh", no_agent=True, deliver="origin")

# 4️⃣  make sure everything's wired up
python3 .../scripts/setup.py --check
```

`setup.py` figures out which profile holds the skill by itself. It's idempotent —
run it twice, nothing breaks. 🙌

---

## 💬 What it looks like

```console
$ rem.py add "Send the report" --list Work --due "tuesday 15:00" --early 2h --est 2h --urgent
#11  Send the report
  list:       Work
  status:     open
  due:        22/09/2026 15:00  (timed)
  early:      2h before
  estimate:   2h
  alarms:
    · 22/09/2026 13:00  early
    · 22/09/2026 15:00  due
  priority:   none  [URGENT]

$ rem.py week
This week (Mon–Sun) (3)
  · #10 Review contract !!! [45m] [yesterday]  @Work
  · #6 Buy milk [10m] [today 18:30] OVERDUE  @Groceries
  · #7 Call Marco [15m] [tomorrow 10:00]  @Work
  3 with an estimate (~1h10)

$ rem.py fits 30m
Fits in 30m (4)
  · #6 Buy milk [10m] [today 18:30] OVERDUE  @Groceries
  · #9 Update the slides !! [25m]  @Work
  · #7 Call Marco [15m] [tomorrow 10:00]  @Work
  · #8 Reply to Anna [5m]  @Work
  4 with an estimate (~55m)

$ rem.py fits 2h --pack
Fits in 2h (5)
  · #10 Review contract !!! [45m] [yesterday]  @Work
  · #6 Buy milk [10m] [today 18:30] OVERDUE  @Groceries
  · #9 Update the slides !! [25m]  @Work
  · #7 Call Marco [15m] [tomorrow 10:00]  @Work
  · #8 Reply to Anna [5m]  @Work
  5 with an estimate (~1h40)
  selected for a 2h slot

$ rem.py list Groceries
Groceries — groceries (6)
  Produce
    · #1 apples
    · #2 lemons
  Dairy & eggs
    · #6 Buy milk [10m] [today 18:30] OVERDUE
    · #3 milk
  Bread & grains
    · #4 bread
  Household & hygiene
    · #5 dish soap
```

And in your chat, without anyone asking:

```
⏰ Reminders (2)
upcoming: · #10 Send the report URGENT [tue 15:00]
· #8 Call Marco [tomorrow 10:00]
```

---

## 🧩 How it works

```
   you ──── "remind me to…" ────►  the agent
                                     │  rem.py add …
                                     ▼
                              ┌──────────────┐
                              │ reminders.db │  SQLite, one file, no server
                              └──────┬───────┘
                                     │  every minute
                                     ▼
    💬 your chat  ◄──────────  tick.py  ──►  fires due alarms, nags the
                                              ones you keep ignoring 😅
```

`tick.py` runs through the scheduler with `no_agent`, which means **the whole
notification path is a single Python script** — fast, free, and quiet when there's
nothing to say. Empty output = no message sent. 🤫

---

## 🧰 Requirements

- 🐍 Python 3.10+
- 🤖 Hermes Agent with the cron scheduler running
- 🛰️ On a **satellite profile** (one that doesn't own the platform credentials):
  a target-exact `profile_route` carrying `chat_id` in `~/.hermes/config.yaml`,
  otherwise delivery fails closed with `platform '<name>' not configured/enabled`.
  Details in the [Installation section](skills/reminders/SKILL.md). 🧭

---

## 🛠️ Handy commands

```bash
rem.py add "Call Marco" --list Work --due "tomorrow 9:00" --early 30m
rem.py add "Report" --due 2026-10-01 --repeat monthly:last:fri --repeat-until 2027-12-31
rem.py today | week | scheduled | urgent | overdue
rem.py fits 30m | fits 1h --pack          # what closes in the time I have
rem.py done 12 | snooze 12 +15m | edit 12 --due "friday 10:00"
rem.py lists add "Travel" --icon airplane | smart "Urgent" --urgent
rem.py alarms 12 add 1d --label "a day before"
rem.py settings set language it   # or back to en
```

Add `--json` to any command for programmatic use. The full reference — date
formats, recurrence syntax, operating rules — is in
**[`skills/reminders/SKILL.md`](skills/reminders/SKILL.md)** 📖

Want to check your install actually works? 🧪

```bash
python3 skills/reminders/scripts/selftest.py
```

Runs ~190 checks over every feature on a throwaway profile — your data is never
touched — and exits non-zero if anything is off.

---

## 🚫 What it deliberately doesn't do

- No graphical UI — it's a CLI and a chat notification. That's the point. ✨
- No sharing lists with other people
- No sync with Apple Reminders or any third-party task app
- No real geofencing for locations — the place is metadata, not a trigger
- **No time tracking.** You can say a task takes 45 minutes, but nothing watches
  the clock while you do it — so there's no "your estimates run 40% short" report.
  Deliberate: measuring real time needs a start event, and half-built timekeeping
  is worse than none. 🔭

This is the *agent's own* reminder system, standing on its own two feet. 🦶

---

## 👋 Author

**Valerio Fantozzi** — built this because agents forget, and reminders shouldn't.

- 📧 <iamvaleriofantozzi@gmail.com>
- 💼 [linkedin.com/in/valeriofantozzi](https://www.linkedin.com/in/valeriofantozzi/)
- 🐙 [@iamvaleriofantozzi](https://github.com/iamvaleriofantozzi)

Found a bug? Have an idea? Open an issue — I'd genuinely like to hear it. 🗣️
If this saved you from missing something important, **a star helps other people
find it**. ⭐

---

## 📄 License

MIT — go wild. 🎉
