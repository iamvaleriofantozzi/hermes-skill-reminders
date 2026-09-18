#!/usr/bin/env python3
"""tick.py — motore di consegna dei promemoria (stile Apple Reminders).

Gira da cron Hermes (no_agent=True). Ad ogni tick:
  1. spara gli allarmi scaduti (early, scadenza, extra) non ancora inviati;
  2. fa insistenza: se un promemoria e' scaduto e non completato, rinotifica
     ogni `nag_minutes` (fino a `nag_max` volte), come fa Apple finche' non lo
     completi o scegli Ignore;
  3. salta tutto cio' che sta in un elenco mutato.

Se non c'e' nulla da notificare NON stampa niente: nessun messaggio inviato.

  tick.py            # produzione
  tick.py --dry-run  # mostra cosa notificherebbe senza scrivere sul DB
"""
from __future__ import annotations

import sys
from datetime import timedelta

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import rs  # noqa: E402


def gather(conn, ref):
    """Allarmi da sparare adesso + promemoria da sollecitare."""
    fired = conn.execute(
        """SELECT a.id AS alarm_id, a.kind, a.label, a.fire_at, r.*, l.name AS list_name, l.muted
           FROM alarms a
           JOIN reminders r ON r.id = a.reminder_id
           JOIN lists l ON l.id = r.list_id
           WHERE a.sent_at IS NULL AND a.fire_at <= ?
             AND r.completed_at IS NULL AND l.muted = 0
           ORDER BY a.fire_at""", (rs.iso(ref),)).fetchall()
    nags = conn.execute(
        """SELECT r.*, l.name AS list_name, l.muted
           FROM reminders r JOIN lists l ON l.id = r.list_id
           WHERE r.completed_at IS NULL AND l.muted = 0
             AND r.nag_at IS NOT NULL AND r.nag_at <= ?
           ORDER BY r.due_at""", (rs.iso(ref),)).fetchall()
    return fired, nags


def render(conn, fired, nags, ref):
    lines = []
    for a in fired:
        label = {"early": rs.tl(conn, "tick_early"), "due": rs.tl(conn, "tick_due"),
                 "custom": a["label"] or rs.tl(conn, "tick_custom")}.get(
                     a["kind"], rs.tl(conn, "tick_custom"))
        head = f"{label}: " if label else ""
        line = f"{head}{rs.human(conn, a, ref)}"
        if a["notes"]:
            line += f"\n    {a['notes']}"
        if a["url"]:
            line += f"\n    {a['url']}"
        lines.append(line)
    for r in nags:
        line = f"{rs.tl(conn, 'tick_nag')}: {rs.human(conn, r, ref)}"
        if r["notes"]:
            line += f"\n    {r['notes']}"
        lines.append(line)
    return lines


def reschedule(conn, rem_id: int, count: int, ref, nag_min: int, nag_max: int) -> None:
    """Prossimo sollecito con backoff (10m, 20m, 40m... max 1 giorno)."""
    step = min(nag_min * (2 ** (count - 1)), 24 * 60)
    nxt = ref + timedelta(minutes=step) if count < nag_max else None
    conn.execute("UPDATE reminders SET notify_count=?, nag_at=?, updated_at=? WHERE id=?",
                 (count, rs.iso(nxt) if nxt else None, rs.iso(ref), rem_id))


def main() -> int:
    dry = "--dry-run" in sys.argv
    conn = rs.connect()
    ref = rs.now()
    fired, nags = gather(conn, ref)

    if not fired and not nags:
        conn.close()
        return 0

    lines = render(conn, fired, nags, ref)
    header = rs.tl(conn, "tick_title", n=len(fired) + len(nags))

    if dry:
        print("[dry-run] " + header)
        for line in lines:
            print(line)
        conn.close()
        return 0

    print(header)
    for line in lines:
        print(line)

    nag_min = int(rs.get_setting(conn, "nag_minutes", "10") or 10)
    nag_max = int(rs.get_setting(conn, "nag_max", "6") or 6)

    for a in fired:
        conn.execute("UPDATE alarms SET sent_at=? WHERE id=?", (rs.iso(ref), a["alarm_id"]))
        count = int(a["notify_count"] or 0) + 1
        if a["kind"] == "due":
            reschedule(conn, a["id"], count, ref, nag_min, nag_max)
        else:
            conn.execute("UPDATE reminders SET notify_count=?, updated_at=? WHERE id=?",
                         (count, rs.iso(ref), a["id"]))

    for r in nags:
        reschedule(conn, r["id"], int(r["notify_count"] or 0) + 1, ref, nag_min, nag_max)

    conn.commit()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
