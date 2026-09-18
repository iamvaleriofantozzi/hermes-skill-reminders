#!/usr/bin/env python3
"""setup.py — installazione e diagnostica della skill reminders.

Rende la skill autosufficiente su qualsiasi profilo Hermes:

  python3 setup.py            # installa (idempotente): DB + wrapper + istruzioni cron
  python3 setup.py --register # installa E registra il cron job via `hermes cron`
  python3 setup.py --check    # diagnostica: dice cosa manca e come rimediare

Non tocca nulla fuori dal profilo che ospita la skill.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rs  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
# <profilo>/skills/<categoria>/reminders → parents[0]=reminders, [1]=<categoria>, [2]=skills, [3]=<profilo>
PROFILE_DIR = SKILL_DIR.parents[2]
WRAPPER_NAME = "reminders_tick.sh"
JOB_NAME = "Reminders — delivery"
SCHEDULE = "1m"
ACTIONS_WRAPPER_NAME = "reminders_actions.sh"
ACTIONS_JOB_NAME = "Reminders — actions"
# Giro di sicurezza giornaliero: il job si sveglia soprattutto su richiesta
# (tick.py lo attiva quando accoda un'azione), ma con uno schedule reale resta
# un percorso di recupero se una sveglia va persa. A vuoto risponde [SILENT].
ACTIONS_SCHEDULE = "0 4 * * *"


def actions_prompt() -> str:
    """Prompt del job agente. Deve essere autosufficiente: il cron non ha contesto."""
    rem_py = SCRIPTS_DIR / "rem.py"
    return (
        "You execute reminder actions queued by the reminders skill.\n"
        "The script output above lists what is pending: one line per action,\n"
        "formatted `#<id> skill=<name> :: <instruction>` (the skill part is optional).\n"
        "\n"
        "For each action listed:\n"
        "1. If it names a skill, load that skill first and follow it.\n"
        "2. Carry out the instruction. Unless the instruction itself asks for a\n"
        "   change, treat it as read-only: gather and report, do not send, delete,\n"
        "   or modify anything outside this machine.\n"
        f"3. Mark it done when finished, passing the outcome that should reach the user:\n"
        f"   python3 \"{rem_py}\" actions done <id> --result \"<one short paragraph>\"\n"
        "   Start with the outcome itself: do not restate the action's title.\n"
        f"   If it cannot be done, record why: python3 \"{rem_py}\" actions fail <id> \"<reason>\"\n"
        "   Always mark every action you touch — an unmarked action is retried.\n"
        "   The result is delivered by the reminder channel: do NOT repeat it in your\n"
        "   own reply as well.\n"
        "\n"
        "Then reply with exactly [SILENT] and nothing else — the outcome travels\n"
        "through the result you stored, so your own reply must stay empty.\n"
        "If the list above is empty, also reply with exactly [SILENT]."
    )


def profile_name() -> str | None:
    """Nome del profilo, se la skill vive dentro profiles/<nome>/."""
    parts = PROFILE_DIR.parts
    if "profiles" in parts:
        i = parts.index("profiles")
        if i + 1 < len(parts):
            return parts[i + 1]
    return None


def wrapper_path() -> Path:
    return PROFILE_DIR / "scripts" / WRAPPER_NAME


def actions_wrapper_path() -> Path:
    return PROFILE_DIR / "scripts" / ACTIONS_WRAPPER_NAME


def find_job_by_script(script: str) -> str | None:
    """Id del job che esegue questo script — l'identita' vera, non il nome.

    Il nome di un job si puo' rinominare a mano (il job di consegna di questo
    profilo si chiama diversamente da quello che questa skill crea): cercare
    per nome ne farebbe nascere un secondo esemplare a ogni `--register`.
    """
    jobs_file = PROFILE_DIR / "cron" / "jobs.json"
    try:
        data = json.loads(jobs_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for job in (data.get("jobs") or []):
        if job.get("script") == script:
            return str(job.get("id") or "")
    return None


def profile_scripts_dir() -> Path:
    return PROFILE_DIR / "scripts"


def do_install(register: bool, deliver: str) -> int:
    ok = True
    conn = rs.connect()            # creates the database if missing
    lc = rs.get_setting(conn, "language", "en")

    def L(key: str, **kw) -> str:
        return rs.tl(conn, key, **kw)

    print(L("setup_installing"))
    print(f"  {L('setup_skill'):<10}{SKILL_DIR}")
    print(f"  {L('setup_profile'):<10}{PROFILE_DIR}")
    print(f"  {L('setup_name'):<10}{profile_name() or L('setup_default_profile')}")

    if sys.version_info < (3, 10):
        print(f"  \u2717 {L('setup_python_needs')}")
        conn.close()
        return 1
    print(f"  python:   {sys.version.split()[0]} \u2713")

    n = conn.execute("SELECT COUNT(*) c FROM lists").fetchone()["c"]
    print(f"  {L('setup_db'):<10}{rs.DB_PATH} \u2713 ({n})")

    # cron wrapper (the scheduler only accepts scripts inside <profile>/scripts/)
    profile_scripts_dir().mkdir(parents=True, exist_ok=True)
    tick = SCRIPTS_DIR / "tick.py"
    wp = wrapper_path()
    wp.write_text(
        "#!/usr/bin/env bash\n"
        "# Generated by reminders/setup.py — delivers due reminders.\n"
        f'exec python3 "{tick}"\n',
        encoding="utf-8",
    )
    wp.chmod(wp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"  {L('setup_wrapper'):<10}{wp} \u2713")

    # Wrapper del job azioni: elenca la coda, che finisce nel prompt dell'agente.
    aw = actions_wrapper_path()
    rempy = SCRIPTS_DIR / "rem.py"
    aw.write_text(
        "#!/usr/bin/env bash\n"
        "# Generated by reminders/setup.py — lists queued reminder actions for the agent.\n"
        f'exec python3 "{rempy}" actions list\n',
        encoding="utf-8",
    )
    aw.chmod(aw.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"  {L('setup_actions_wrapper'):<10}{aw} \u2713")

    pn = profile_name()
    base = ["hermes"] + (["-p", pn] if pn else [])
    cmd = base + ["cron", "create", SCHEDULE, "--name", JOB_NAME,
                  "--script", WRAPPER_NAME, "--no-agent", "--deliver", deliver]
    acmd = base + ["cron", "create", ACTIONS_SCHEDULE, actions_prompt(),
                   "--name", ACTIONS_JOB_NAME, "--script", ACTIONS_WRAPPER_NAME,
                   "--deliver", deliver]
    if register:
        if not shutil.which("hermes"):
            print(f"  \u2717 {L('setup_no_hermes')}")
            ok = False
        else:
            # Idempotenza: non ricreare un job che esiste gia'. Si cerca per
            # script, non per nome: il nome si puo' rinominare a mano.
            if find_job_by_script(WRAPPER_NAME):
                print(f"  {L('setup_cron'):<10}{L('setup_cron_present')} \u2713")
            else:
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode == 0:
                    print(f"  {L('setup_cron'):<10}{L('setup_cron_registered')} \u2713")
                else:
                    print(f"  \u2717 {L('setup_cron_failed')}:\n{(res.stderr or res.stdout).strip()[:400]}")
                    ok = False
            # Job agente delle azioni: dormiente finche' tick.py non lo sveglia.
            jid = find_job_by_script(ACTIONS_WRAPPER_NAME)
            if jid:
                rs.set_setting(conn, "action_job_id", jid, commit=True)
                print(f"  {L('setup_actions_job'):<10}"
                      f"{L('setup_actions_registered', id=jid)} \u2713")
            else:
                ares = subprocess.run(acmd, capture_output=True, text=True)
                if ares.returncode == 0:
                    jid = find_job_by_script(ACTIONS_WRAPPER_NAME)
                    if jid:
                        rs.set_setting(conn, "action_job_id", jid, commit=True)
                    print(f"  {L('setup_actions_job'):<10}"
                          f"{L('setup_actions_registered', id=jid or '?')} \u2713")
                else:
                    print(f"  \u2717 {L('setup_actions_failed')}:\n"
                          f"{(ares.stderr or ares.stdout).strip()[:400]}")
                    ok = False

    if not register or not ok:
        print(f"\n  {L('setup_register_with')}:")
        print(f"    {' '.join(cmd)}")
        print(f"  {L('setup_or_from_chat')}:")
        print(f"    cronjob_manage(action='create', schedule='{SCHEDULE}', name='{JOB_NAME}',")
        print(f"                    script='{WRAPPER_NAME}', no_agent=True, deliver='{deliver}')")

    print(f"\n  {L('setup_delivery_note')}")
    conn.close()
    return 0 if ok else 1


def do_check() -> int:
    conn = rs.connect()
    lc = rs.get_setting(conn, "language", "en")

    def L(key: str, **kw) -> str:
        return rs.tl(conn, key, **kw)

    print(L("setup_checking"))
    problems = []

    print(f"  python:   {sys.version.split()[0]}")
    print(f"  {L('setup_skill'):<10}{SKILL_DIR}")
    print(f"  {L('setup_db'):<10}{rs.DB_PATH}")
    if not os.path.exists(rs.DB_PATH):
        problems.append(L("setup_db_missing"))
    else:
        try:
            stats = conn.execute(
                "SELECT (SELECT COUNT(*) FROM reminders) r, (SELECT COUNT(*) FROM alarms) a,"
                " (SELECT COUNT(*) FROM lists) l").fetchone()
            pending = conn.execute(
                "SELECT COUNT(*) c FROM alarms a JOIN reminders r ON r.id=a.reminder_id "
                "WHERE a.sent_at IS NULL AND r.completed_at IS NULL").fetchone()["c"]
            print("            " + L("setup_reminders_n", r=stats["r"], a=stats["a"],
                                     l=stats["l"], p=pending))
        except Exception as exc:
            problems.append(L("setup_db_unreadable", err=exc))

    wp = wrapper_path()
    if wp.exists():
        print(f"  {L('setup_wrapper'):<10}{wp} \u2713")
    else:
        problems.append(L("setup_wrapper_missing"))

    pn = profile_name()
    base = ["hermes"] + (["-p", pn] if pn else [])
    if shutil.which("hermes"):
        res = subprocess.run(base + ["cron", "list"], capture_output=True, text=True)
        out = (res.stdout or "") + (res.stderr or "")
        if JOB_NAME in out or WRAPPER_NAME in out:
            print(f"  {L('setup_cron'):<10}{L('setup_cron_present')} \u2713")
        else:
            problems.append(L("setup_cron_absent"))
    else:
        print(f"  {L('setup_cron'):<10}{L('setup_cron_unverifiable')}")

    if problems:
        print(f"\n  {L('setup_problems')}:")
        for p in problems:
            print(f"    \u2717 {p}")
        conn.close()
        return 1
    print(f"\n  {L('setup_all_good')}")
    conn.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Installa/diagnostica la skill reminders")
    ap.add_argument("--register", action="store_true", help="registra anche il cron job")
    ap.add_argument("--check", action="store_true", help="solo diagnostica")
    ap.add_argument("--deliver", default="origin",
                    help="destinazione del cron: origin, local, telegram, platform:chat_id… "
                         "(da shell conviene una piattaforma, es. 'telegram')")
    args = ap.parse_args()
    if args.check:
        return do_check()
    return do_install(args.register, args.deliver)


if __name__ == "__main__":
    raise SystemExit(main())
