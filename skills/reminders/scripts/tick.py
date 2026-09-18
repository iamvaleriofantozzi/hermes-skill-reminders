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
    """Allarmi da sparare adesso + promemoria da sollecitare.

    LEFT JOIN su `lists`: un promemoria puo' non avere elenco (list_id NULL) e
    sparirebbe del tutto dalla consegna. `COALESCE(l.muted,0)` perche' con il
    LEFT JOIN il muted di un promemoria senza elenco e' NULL, e NULL = 0 non e'
    mai vero: anche quel confronto lo escluderebbe.

    Gli allarmi `early` con scadenza gia' passata sono esclusi: se Hermes e'
    rimasto spento, avvisare in anticipo di qualcosa che e' gia' successo non e'
    un promemoria, e' rumore.
    """
    fired = conn.execute(
        """SELECT a.id AS alarm_id, a.kind, a.label, a.fire_at, r.*, l.name AS list_name,
                  COALESCE(l.muted, 0) AS muted
           FROM alarms a
           JOIN reminders r ON r.id = a.reminder_id
           LEFT JOIN lists l ON l.id = r.list_id
           WHERE a.sent_at IS NULL AND a.fire_at <= ?
             AND r.completed_at IS NULL AND COALESCE(l.muted, 0) = 0
             AND NOT (a.kind = 'early' AND r.due_at IS NOT NULL AND r.due_at <= ?)
           ORDER BY a.fire_at""", (rs.iso(ref), rs.iso(ref))).fetchall()
    nags = conn.execute(
        """SELECT r.*, l.name AS list_name, COALESCE(l.muted, 0) AS muted
           FROM reminders r LEFT JOIN lists l ON l.id = r.list_id
           WHERE r.completed_at IS NULL AND COALESCE(l.muted, 0) = 0
             AND r.nag_at IS NOT NULL AND r.nag_at <= ?
           ORDER BY r.due_at""", (rs.iso(ref),)).fetchall()
    return fired, nags


def drop_stale_early(conn, ref) -> int:
    """Segna come inviati gli avvisi `early` ormai superati, cosi' non restano in coda per sempre."""
    cur = conn.execute(
        """UPDATE alarms SET sent_at=?
           WHERE sent_at IS NULL AND kind='early' AND fire_at <= ?
             AND reminder_id IN (SELECT id FROM reminders
                                 WHERE due_at IS NOT NULL AND due_at <= ?)""",
        (rs.iso(ref), rs.iso(ref), rs.iso(ref)))
    return cur.rowcount or 0


def render(conn, fired, nags, ref):
    """Una riga per promemoria: piu' allarmi dello stesso non devono ripeterlo."""
    lines = []
    seen = set()
    for a in fired:
        if a["id"] in seen:
            continue
        seen.add(a["id"])
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
        if r["id"] in seen:
            continue
        seen.add(r["id"])
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


def pump_actions(conn) -> None:
    """Sveglia il job agente se c'e' un'azione mai tentata.

    Il job resta dormiente (nessun costo) finche' la coda non ha una riga nuova.
    Se il job non e' configurato resta tutto silenzioso: non e' un guasto.
    """
    if not rs.action_job_id(conn) or not rs.pending_actions(conn, only_fresh=True):
        return
    ok, detail = rs.trigger_action_job(conn)
    if ok:
        rs.mark_actions_attempted(conn)
    else:
        # Non e' fatale: la riga resta fresca e il prossimo tick riprova.
        print(rs.tl(conn, "action_trigger_err", err=detail), file=sys.stderr)


def main() -> int:
    dry = "--dry-run" in sys.argv
    conn = rs.connect()
    ref = rs.now()
    # Gli avvisi in anticipo ormai superati vanno archiviati anche quando non
    # c'e' altro da consegnare, altrimenti restano in coda per sempre.
    stale = 0 if dry else drop_stale_early(conn, ref)
    fired, nags = gather(conn, ref)
    # Risultati delle azioni concluse: viaggiano sul canale dei promemoria, che
    # e' l'unico che consegna in modo affidabile anche da un profilo satellite.
    results = rs.undelivered_results(conn)

    if not fired and not nags and not results:
        if stale:
            conn.commit()
        # Anche senza nulla da consegnare: una sveglia puo' essere andata persa
        # in un tick precedente, e questo e' l'unico posto che la ritenta.
        if not dry:
            pump_actions(conn)
        conn.close()
        return 0

    lines = render(conn, fired, nags, ref)
    lines += [f"{a['title']}: {a['result']}" for a in results]
    header = rs.tl(conn, "tick_title", n=len(lines))

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
        rs.enqueue_action(conn, a)
        count = int(a["notify_count"] or 0) + 1
        if a["kind"] == "due":
            reschedule(conn, a["id"], count, ref, nag_min, nag_max)
        else:
            conn.execute("UPDATE reminders SET notify_count=?, updated_at=? WHERE id=?",
                         (count, rs.iso(ref), a["id"]))

    for r in nags:
        reschedule(conn, r["id"], int(r["notify_count"] or 0) + 1, ref, nag_min, nag_max)

    for a in results:
        rs.mark_result_delivered(conn, a["id"])

    conn.commit()
    pump_actions(conn)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
