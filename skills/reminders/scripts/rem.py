#!/usr/bin/env python3
"""rem.py — command line for the reminders skill (an Apple Reminders replica).

  rem.py add "Call Marco" --list Work --due "tomorrow 9:00" --early 30m --alarm 2h
  rem.py add "Report" --due 2026-10-01 --repeat monthly:last:fri --repeat-until 2027-12-31
  rem.py today | scheduled | all | flagged | urgent | anytime | completed | overdue
  rem.py week | nextweek | days7
  rem.py tag work | tags | smart "Urgent" --urgent | columns Work | list Groceries
  rem.py show 12 | done 12 | edit 12 … | snooze 12 +15m | delete 12
  rem.py alarms 12 | alarms 12 add 2h --label "nudge" | alarms 12 rm 3
  rem.py section add Work "To do" | template save "Setup" --from 12 | template apply "Setup"
  rem.py settings | settings set language it | stats

Output language follows the `language` setting (default: en).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import rs  # noqa: E402


def L(conn, msg: str, **kw) -> str:
    """Shorthand for a translated message (`msg` is the catalog key)."""
    return rs.tl(conn, msg, **kw)


# --------------------------------------------------------------------- helpers

def resolve_list(conn, name: str | None) -> int | None:
    """List id for `name` (created when missing). None means "no list"."""
    if not name:
        d = rs.get_setting(conn, "default_list_id")
        if d:
            row = conn.execute("SELECT id FROM lists WHERE id=?", (int(d),)).fetchone()
            if row:
                return row["id"]
        return None
    row = conn.execute("SELECT id FROM lists WHERE lower(name)=lower(?)", (name,)).fetchone()
    if row:
        return row["id"]
    grocery = 1 if name.strip().lower() in ("groceries", "grocery", "shopping", "spesa") else 0
    cur = conn.execute("INSERT INTO lists(name, position, is_grocery, created_at) VALUES (?, 99, ?, ?)",
                       (name, grocery, rs.iso(rs.now())))
    conn.commit()
    return cur.lastrowid


def resolve_list_id(conn, name: str | None) -> int | None:
    if not name:
        return None
    row = conn.execute("SELECT id FROM lists WHERE lower(name)=lower(?)", (name,)).fetchone()
    return row["id"] if row else None


def fetch(conn, where: str, params: tuple = (), order: str = "r.due_at IS NULL, r.due_at"):
    sql = (f"SELECT r.*, l.name AS list_name, s.name AS section_name, "
           f"(SELECT SUM(c.estimate_minutes) FROM reminders c "
           f" WHERE c.parent_id = r.id AND c.estimate_minutes IS NOT NULL) AS est_effective "
           f"FROM reminders r "
           f"LEFT JOIN lists l ON l.id = r.list_id "
           f"LEFT JOIN sections s ON s.id = r.section_id "
           f"WHERE {where} ORDER BY {order}")
    return conn.execute(sql, params).fetchall()


def totals_line(conn, rows) -> str:
    """Riga di riepilogo delle stime: '4 con stima (~2h15) · 3 senza'."""
    timed = [r for r in rows if rs.effective_estimate(r)]
    if not timed:
        return ""
    total = sum(int(rs.effective_estimate(r) or 0) for r in timed)
    missing = len(rows) - len(timed)
    key = "totals_some" if missing else "totals_all"
    return L(conn, key, n=len(timed), total=rs.fmt_estimate(total), m=missing)


def emit(conn, rows, title, args):
    if getattr(args, "json", False):
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
        return
    if not rows:
        print(f"{title}: {L(conn, 'no_reminders')}")
        return
    print(f"{title} ({len(rows)})")
    ref = rs.now()
    for r in rows:
        line = rs.human(conn, r, ref)
        if r["list_name"]:
            line += f"  @{r['list_name']}"
        if r["parent_id"]:
            line = "    \u21b3 " + line[2:]
        print("  " + line)
    totals = totals_line(conn, rows)
    if totals:
        print("  " + totals)


def vtitle(conn, key: str) -> str:
    return L(conn, f"view_{key}")


def show_one(conn, rem_id: int, as_json: bool = False) -> None:
    row = conn.execute(
        "SELECT r.*, l.name AS list_name, s.name AS section_name FROM reminders r "
        "LEFT JOIN lists l ON l.id=r.list_id LEFT JOIN sections s ON s.id=r.section_id WHERE r.id=?",
        (rem_id,)).fetchone()
    if not row:
        print(L(conn, "not_found", id=rem_id))
        return
    if as_json:
        d = dict(row)
        d["alarms"] = [dict(a) for a in rs.alarms_of(conn, rem_id)]
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return
    print(f"#{row['id']}  {row['title']}")
    sec = f" \u203a {row['section_name']}" if row["section_name"] else ""
    print(f"  {L(conn, 'detail_list') + ':':<12}{row['list_name'] or L(conn, 'no_list')}{sec}")
    status = L(conn, "completed_on", ts=row["completed_at"]) if row["completed_at"] else L(conn, "open")
    print(f"  {L(conn, 'detail_status') + ':':<12}{status}")
    if row["due_at"]:
        d = rs.from_iso(row["due_at"])
        kind = L(conn, "detail_timed") if row["due_has_time"] else L(conn, "detail_allday")
        print(f"  {L(conn, 'detail_due') + ':':<12}{rs.fmt_dt(conn, d, bool(row['due_has_time']))}  ({kind})")
    if row["early_minutes"]:
        m = int(row["early_minutes"])
        lab = f"{m}m" if m < 60 else (f"{m // 60}h" if m < 1440 else f"{m // 1440}d")
        print(f"  {L(conn, 'detail_early') + ':':<12}{L(conn, 'detail_before', value=lab)}")
    est_min = rs.row_get(row, "estimate_minutes")
    if est_min:
        print(f"  {L(conn, 'detail_estimate') + ':':<12}{rs.fmt_estimate(est_min)}")
    else:
        subs = conn.execute("SELECT estimate_minutes FROM reminders WHERE parent_id=? "
                            "AND estimate_minutes IS NOT NULL", (rem_id,)).fetchall()
        if subs:
            tot = sum(int(s["estimate_minutes"]) for s in subs)
            print(f"  {L(conn, 'detail_estimate') + ':':<12}{rs.fmt_estimate(tot)}"
                  f"  ({len(subs)} \u00d7 {L(conn, 'detail_subtasks').lower()})")
    al = rs.alarms_of(conn, rem_id)
    if al:
        print(f"  {L(conn, 'detail_alarms') + ':'}")
        for a in al:
            lab = f" [{a['label']}]" if a["label"] else ""
            sent = f" ({L(conn, 'detail_sent')})" if a["sent_at"] else ""
            print(f"    \u00b7 {rs.fmt_dt(conn, rs.from_iso(a['fire_at']))}{lab}  {a['kind']}{sent}")
    if row["repeat_rule"]:
        rule = json.loads(row["repeat_rule"])
        extra = []
        if rule.get("end_date"):
            extra.append(L(conn, "detail_until", date=str(rule["end_date"])[:10]))
        if rule.get("from_completion"):
            extra.append(L(conn, "detail_from_completion"))
        desc = rs.describe_repeat(conn, rule)
        print(f"  {L(conn, 'detail_recurrence') + ':':<12}{desc}" + (f" ({', '.join(extra)})" if extra else ""))
    prio = L(conn, f"prio_{rs.PRIORITY_LABEL.get(row['priority'] or 0, 'none')}")
    flags = []
    if row["flagged"]:
        flags.append(L(conn, "flag_flagged"))
    if row["urgent"]:
        flags.append(L(conn, "flag_urgent"))
    print(f"  {L(conn, 'detail_priority') + ':':<12}{prio}" + (f"  [{', '.join(flags)}]" if flags else ""))
    if row["location_name"]:
        trig = {"arriving": L(conn, "loc_arriving"), "leaving": L(conn, "loc_leaving")}.get(
            row["location_trigger"] or "", "")
        print(f"  {L(conn, 'detail_location') + ':':<12}{row['location_name']} {trig}".rstrip())
    if row["tags"]:
        print(f"  {L(conn, 'detail_tags') + ':':<12}{' '.join('#' + t for t in rs.tags_of(row))}")
    if row["url"]:
        print(f"  {L(conn, 'detail_url') + ':':<12}{row['url']}")
    if row["attachments"]:
        print(f"  {L(conn, 'detail_attachments') + ':':<12}{row['attachments']}")
    if row["notes"]:
        print(f"  {L(conn, 'detail_notes') + ':':<12}{row['notes']}")
    subs = conn.execute("SELECT * FROM reminders WHERE parent_id=?", (rem_id,)).fetchall()
    if subs:
        print(f"  {L(conn, 'detail_subtasks')} ({len(subs)}):")
        for s in subs:
            mark = "x" if s["completed_at"] else "\u00b7"
            print(f"    {mark} #{s['id']} {s['title']}")


# --------------------------------------------------------------------- comandi

def cmd_lists(conn, args):
    a = args.action
    if a in ("add", "delete", "mute", "unmute", "pin", "unpin", "default", "rename", "folder"):
        if not args.name:
            print(L(conn, "needs_name"))
            return
        lid = resolve_list_id(conn, args.name)
        if a == "add":
            if lid:
                print(L(conn, "list_exists", name=args.name))
                return
            cur = conn.execute(
                "INSERT INTO lists(name, color, icon, folder, position, pinned, is_grocery, created_at) "
                "VALUES (?,?,?,?,99,?,?,?)",
                (args.name, args.color, args.icon, args.folder, 1 if args.pinned else 0,
                 1 if args.grocery else 0, rs.iso(rs.now())))
            if args.default:
                conn.execute("UPDATE lists SET is_default=0")
                conn.execute("UPDATE lists SET is_default=1 WHERE id=?", (cur.lastrowid,))
                rs.set_setting(conn, "default_list_id", str(cur.lastrowid), commit=False)
            conn.commit()
            print(L(conn, "list_created", name=args.name))
            return
        if not lid:
            print(L(conn, "list_not_found", name=args.name))
            return
        if a == "delete":
            n = conn.execute("SELECT COUNT(*) c FROM reminders WHERE list_id=?", (lid,)).fetchone()["c"]
            conn.execute("DELETE FROM reminders WHERE list_id=?", (lid,))
            conn.execute("DELETE FROM sections WHERE list_id=?", (lid,))
            conn.execute("DELETE FROM lists WHERE id=?", (lid,))
            conn.commit()
            print(L(conn, "list_deleted", name=args.name, n=n))
        elif a == "mute":
            conn.execute("UPDATE lists SET muted=1 WHERE id=?", (lid,))
            conn.commit()
            print(L(conn, "list_muted", name=args.name))
        elif a == "unmute":
            conn.execute("UPDATE lists SET muted=0 WHERE id=?", (lid,))
            conn.commit()
            print(L(conn, "list_unmuted", name=args.name))
        elif a == "pin":
            conn.execute("UPDATE lists SET pinned=1 WHERE id=?", (lid,))
            conn.commit()
            print(L(conn, "list_pinned", name=args.name))
        elif a == "unpin":
            conn.execute("UPDATE lists SET pinned=0 WHERE id=?", (lid,))
            conn.commit()
            print(L(conn, "list_unpinned", name=args.name))
        elif a == "default":
            conn.execute("UPDATE lists SET is_default=0")
            conn.execute("UPDATE lists SET is_default=1 WHERE id=?", (lid,))
            rs.set_setting(conn, "default_list_id", str(lid), commit=False)
            conn.commit()
            print(L(conn, "list_default", name=args.name))
        elif a == "rename":
            conn.execute("UPDATE lists SET name=? WHERE id=?", (args.to, lid))
            conn.commit()
            print(L(conn, "list_renamed", old=args.name, new=args.to))
        elif a == "folder":
            conn.execute("UPDATE lists SET folder=? WHERE id=?", (args.to, lid))
            conn.commit()
            print(L(conn, "list_folder", name=args.name, folder=args.to or L(conn, "no_folder")))
        return

    rows = conn.execute(
        """SELECT l.*, (SELECT COUNT(*) FROM reminders r WHERE r.list_id=l.id AND r.completed_at IS NULL) AS open_n
           FROM lists l ORDER BY l.pinned DESC, l.position, l.name""").fetchall()
    if getattr(args, "json", False):
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
        return
    smart = rs.smart_ids(conn)
    if not rows:
        print(L(conn, "no_lists"))
        return
    print(f"{L(conn, 'lists_title')} ({len(rows)})")
    cur_folder = object()
    for r in rows:
        if r["folder"] != cur_folder:
            cur_folder = r["folder"]
            if cur_folder:
                print(f"  \u250c {cur_folder}")
        n = len(smart.get(r["id"], [])) if r["is_smart"] else r["open_n"]
        marks = []
        if r["pinned"]:
            marks.append(L(conn, "mark_pinned"))
        if r["muted"]:
            marks.append(L(conn, "mark_muted"))
        if r["is_smart"]:
            marks.append(L(conn, "mark_smart"))
        if r["is_grocery"]:
            marks.append(L(conn, "mark_grocery"))
        if r["is_default"]:
            marks.append(L(conn, "mark_default"))
        suffix = f"  [{', '.join(marks)}]" if marks else ""
        print(f"  {r['name']:<18} {L(conn, 'tag_open', n=n):>10}{suffix}")


def cmd_add(conn, args):
    list_id = resolve_list(conn, args.list)
    adt = rs.get_setting(conn, "all_day_notify_time", "09:00")
    due, has_time = (None, False)
    if args.due:
        due, has_time = rs.parse_when(args.due, all_day_time=adt)
        if due is None:
            print(L(conn, "bad_date", value=args.due))
            return
    due_at = rs.iso(due) if due else None
    early = None
    if args.early:
        early = rs.parse_offset(args.early)
        if early is None:
            print(L(conn, "bad_early", value=args.early))
            return
    section_id = None
    if args.section:
        row = conn.execute("SELECT id FROM sections WHERE list_id=? AND lower(name)=lower(?)",
                           (list_id, args.section)).fetchone()
        if row:
            section_id = row["id"]
        else:
            section_id = conn.execute("INSERT INTO sections(list_id, name, position) VALUES (?,?,99)",
                                      (list_id, args.section)).lastrowid
    rule = rs.parse_repeat(args.repeat) if args.repeat else None
    if args.repeat and rule is None:
        print(L(conn, "bad_recurrence", value=args.repeat))
        return
    if rule:
        if args.repeat_until:
            dt, _ = rs.parse_when(args.repeat_until, all_day_time=adt)
            if dt:
                rule["end_date"] = rs.iso(dt)
        if args.repeat_from == "completion":
            rule["from_completion"] = True
        if not due_at:
            d0, _ht = rs.parse_when("today", all_day_time=adt)
            if d0 is not None:
                due, has_time = d0, False
                due_at = rs.iso(d0)
    ts = rs.iso(rs.now())
    est_min = None
    if args.est:
        est_min = rs.parse_estimate(args.est)
        if est_min is None:
            print(L(conn, "bad_duration", value=args.est))
            return
    cur = conn.execute(
        """INSERT INTO reminders(list_id, section_id, title, notes, url, attachments, due_at, due_has_time,
                                 early_minutes, priority, flagged, urgent, estimate_minutes, repeat_rule,
                                 tags, parent_id, location_name, location_trigger, location_radius,
                                 sort_order, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (list_id, section_id, args.title, args.notes, args.url, args.attach, due_at,
         1 if has_time else 0, early, rs.PRIORITY.get(args.priority, 0), 1 if args.flag else 0,
         1 if args.urgent else 0, est_min, json.dumps(rule) if rule else None, args.tags, args.parent,
         args.location, args.location_trigger, args.location_radius, 0, ts, ts))
    rem_id = cur.lastrowid
    if not args.no_alarm:
        for extra in (args.alarm or []):
            if rs.add_alarm(conn, rem_id, extra, "nudge", due_at) is None:
                print("  " + L(conn, "bad_alarm", value=extra))
        # Dentro il guard: rebuild_alarms() rigenera l'allarme di scadenza, quindi
        # chiamarla con --no-alarm annullava l'opzione.
        rs.rebuild_alarms(conn, rem_id)
    conn.commit()
    show_one(conn, rem_id, getattr(args, "json", False))


def cmd_view(conn, args):
    ref = rs.now()
    end = rs.iso(ref.replace(hour=0, minute=0, second=0) + timedelta(days=1))
    v = args.view
    if v == "today":
        rows = fetch(conn, "r.completed_at IS NULL AND r.due_at IS NOT NULL AND r.due_at < ?", (end,))
    elif v == "overdue":
        rows = fetch(conn, "r.completed_at IS NULL AND r.due_at IS NOT NULL AND r.due_at < ?",
                     (rs.iso(ref),))
    elif v == "scheduled":
        rows = fetch(conn, "r.completed_at IS NULL AND r.due_at IS NOT NULL AND r.due_at >= ?",
                     (end,), order="r.due_at")
    elif v == "all":
        rows = fetch(conn, "r.completed_at IS NULL")
    elif v == "flagged":
        rows = fetch(conn, "r.completed_at IS NULL AND r.flagged=1")
    elif v == "urgent":
        rows = fetch(conn, "r.completed_at IS NULL AND r.urgent=1")
    elif v == "anytime":
        rows = fetch(conn, "r.completed_at IS NULL AND r.due_at IS NULL")
    elif v == "completed":
        rows = fetch(conn, "r.completed_at IS NOT NULL", order="r.completed_at DESC")
    elif v in ("week", "nextweek"):
        start_w, end_w = rs.week_bounds(ref, 1 if v == "nextweek" else 0)
        rows = fetch(conn, "r.completed_at IS NULL AND r.due_at IS NOT NULL AND r.due_at >= ? "
                           "AND r.due_at < ?",
                     (rs.iso(start_w), rs.iso(end_w)), order="r.due_at")
    elif v == "days7":
        rows = fetch(conn, "r.completed_at IS NULL AND r.due_at IS NOT NULL AND r.due_at < ?",
                     (rs.iso(ref + timedelta(days=7)),), order="r.due_at")
    else:
        rows = []
    emit(conn, rows, vtitle(conn, v), args)


def cmd_list_view(conn, args):
    lid = resolve_list_id(conn, args.name)
    if not lid:
        print(L(conn, "list_not_found", name=args.name))
        return
    r = conn.execute("SELECT * FROM lists WHERE id=?", (lid,)).fetchone()
    if r["is_smart"]:
        ids = rs.smart_ids(conn).get(lid, set())
        title = f"{r['name']} ({L(conn, 'mark_smart')})"
        if not ids:
            emit(conn, [], title, args)
            return
        ph = ",".join("?" * len(ids))
        rows = fetch(conn, f"r.completed_at IS NULL AND r.id IN ({ph})", tuple(ids))
        emit(conn, rows, title, args)
        return
    rows = fetch(conn, "r.completed_at IS NULL AND r.list_id=?", (lid,))
    if r["is_grocery"]:
        groups: dict[str, list] = {}
        for x in rows:
            groups.setdefault(rs.grocery_category(x["title"]), []).append(x)
        if getattr(args, "json", False):
            print(json.dumps({k: [dict(v) for v in vs] for k, vs in groups.items()},
                             ensure_ascii=False, indent=2))
            return
        print(L(conn, "groceries_title", name=r["name"], n=len(rows)))
        for key in rs.GROCERY_CATEGORIES:
            if groups.get(key):
                print(f"  {rs.grocery_label(conn, key)}")
                for x in groups[key]:
                    print("    " + rs.human(conn, x))
        return
    emit(conn, rows, r["name"], args)


def cmd_columns(conn, args):
    lid = resolve_list_id(conn, args.name)
    if not lid:
        print(L(conn, "list_not_found", name=args.name))
        return
    rows = fetch(conn, "r.completed_at IS NULL AND r.list_id=?", (lid,))
    groups: dict[str, list] = {}
    for x in rows:
        groups.setdefault(x["section_name"] or L(conn, "no_section"), []).append(x)
    if getattr(args, "json", False):
        print(json.dumps({k: [dict(v) for v in vs] for k, vs in groups.items()},
                         ensure_ascii=False, indent=2))
        return
    print(L(conn, "columns_title", name=args.name, n=len(rows)))
    for sec, items in groups.items():
        print(f"\n  \u2500\u2500 {sec} ({len(items)})")
        for x in items:
            print("    " + rs.human(conn, x))


def cmd_tag(conn, args):
    t = args.tag.lstrip("#")
    rows = fetch(conn, "r.completed_at IS NULL AND r.tags LIKE ?", (f"%{t}%",))
    emit(conn, rows, f"#{t}", args)


def cmd_tags(conn, args):
    counter: dict[str, int] = {}
    for r in conn.execute("SELECT tags FROM reminders WHERE completed_at IS NULL AND tags IS NOT NULL"):
        for t in rs.tags_of(r):
            counter[t] = counter.get(t, 0) + 1
    if getattr(args, "json", False):
        print(json.dumps(counter, ensure_ascii=False, indent=2))
        return
    if not counter:
        print(L(conn, "no_tags"))
        return
    print(f"{L(conn, 'tags_title')} ({len(counter)})")
    for t, n in sorted(counter.items(), key=lambda x: -x[1]):
        print(f"  #{t:<18} {n}")


def cmd_show(conn, args):
    show_one(conn, args.id, getattr(args, "json", False))


def cmd_search(conn, args):
    q = args.query
    match = " OR ".join(q) if len(q) > 1 else q[0]
    rows = fetch(conn, "r.id IN (SELECT rowid FROM reminders_fts WHERE reminders_fts MATCH ?)", (match,))
    emit(conn, rows, L(conn, "search_title", query=" ".join(q)), args)


def cmd_done(conn, args):
    row = conn.execute("SELECT * FROM reminders WHERE id=?", (args.id,)).fetchone()
    if not row:
        print(L(conn, "not_found", id=args.id))
        return
    ts = rs.iso(rs.now())
    if row["completed_at"]:
        conn.execute("UPDATE reminders SET completed_at=NULL, updated_at=? WHERE id=?", (ts, args.id))
        rs.rebuild_alarms(conn, args.id)
        conn.commit()
        print(L(conn, "reopened", id=args.id, title=row["title"]))
        return
    nxt = rs.advance(conn, row)
    conn.execute("UPDATE reminders SET completed_at=?, nag_at=NULL, updated_at=? WHERE id=?",
                 (ts, ts, args.id))
    if nxt:
        conn.execute("UPDATE reminders SET due_at=?, completed_at=NULL, nag_at=NULL, updated_at=? WHERE id=?",
                     (nxt, ts, args.id))
        rs.rebuild_alarms(conn, args.id, commit=False)
        conn.commit()
        print(L(conn, "done_next", title=row["title"],
                when=rs.fmt_dt(conn, rs.from_iso(nxt), bool(row["due_has_time"]))))
        return
    conn.execute("DELETE FROM alarms WHERE reminder_id=? AND sent_at IS NULL", (args.id,))
    for s in conn.execute("SELECT id FROM reminders WHERE parent_id=?", (args.id,)):
        conn.execute("UPDATE reminders SET completed_at=?, nag_at=NULL WHERE id=? AND completed_at IS NULL",
                     (ts, s["id"]))
    conn.commit()
    print(L(conn, "done", title=row["title"]))


def cmd_edit(conn, args):
    row = conn.execute("SELECT * FROM reminders WHERE id=?", (args.id,)).fetchone()
    if not row:
        print(L(conn, "not_found", id=args.id))
        return
    adt = rs.get_setting(conn, "all_day_notify_time", "09:00")
    sets, vals = [], []

    def put(col, val):
        sets.append(f"{col}=?")
        vals.append(val)

    if args.title:
        put("title", args.title)
    if args.notes is not None:
        put("notes", args.notes)
    if args.url is not None:
        put("url", args.url)
    if args.attach is not None:
        put("attachments", args.attach)
    if args.due:
        d, ht = rs.parse_when(args.due, all_day_time=adt)
        if d is None:
            print(L(conn, "bad_date", value=args.due))
            return
        put("due_at", rs.iso(d))
        put("due_has_time", 1 if ht else 0)
    if args.no_due:
        put("due_at", None)
        put("due_has_time", 0)
        conn.execute("DELETE FROM alarms WHERE reminder_id=?", (args.id,))
    if args.early is not None:
        if args.early.lower() in ("none", "no", "0"):
            put("early_minutes", None)
        else:
            m = rs.parse_offset(args.early)
            if m is None:
                print(L(conn, "bad_early", value=args.early))
                return
            put("early_minutes", m)
    if args.priority:
        put("priority", rs.PRIORITY.get(args.priority, 0))
    if args.est is not None:
        if args.est.lower() in ("none", "no", ""):
            put("estimate_minutes", None)
        else:
            e = rs.parse_estimate(args.est)
            if e is None:
                print(L(conn, "bad_duration", value=args.est))
                return
            put("estimate_minutes", e)
    if args.flag is not None:
        put("flagged", 1 if args.flag else 0)
    if args.urgent is not None:
        put("urgent", 1 if args.urgent else 0)
    if args.repeat is not None:
        if args.repeat.lower() in ("none", "no", ""):
            put("repeat_rule", None)
        else:
            rule = rs.parse_repeat(args.repeat)
            if rule is None:
                print(L(conn, "bad_recurrence", value=args.repeat))
                return
            if args.repeat_until:
                dt, _ = rs.parse_when(args.repeat_until, all_day_time=adt)
                if dt:
                    rule["end_date"] = rs.iso(dt)
            if args.repeat_from == "completion":
                rule["from_completion"] = True
            put("repeat_rule", json.dumps(rule))
    if args.list:
        put("list_id", resolve_list(conn, args.list))
    if args.section is not None:
        lid = row["list_id"]
        if args.section == "":
            put("section_id", None)
        else:
            srow = conn.execute("SELECT id FROM sections WHERE list_id=? AND lower(name)=lower(?)",
                                (lid, args.section)).fetchone()
            if srow:
                put("section_id", srow["id"])
            else:
                put("section_id", conn.execute(
                    "INSERT INTO sections(list_id, name, position) VALUES (?,?,99)",
                    (lid, args.section)).lastrowid)
    if args.tags is not None:
        put("tags", args.tags)
    if args.location is not None:
        put("location_name", args.location or None)
    if args.location_trigger:
        put("location_trigger", args.location_trigger)
    if not sets:
        print(L(conn, "nothing_to_change"))
        return
    put("updated_at", rs.iso(rs.now()))
    vals.append(args.id)
    conn.execute(f"UPDATE reminders SET {', '.join(sets)} WHERE id=?", vals)
    rs.rebuild_alarms(conn, args.id, commit=False)
    conn.commit()
    show_one(conn, args.id)


def cmd_delete(conn, args):
    row = conn.execute("SELECT title FROM reminders WHERE id=?", (args.id,)).fetchone()
    if not row:
        print(L(conn, "not_found", id=args.id))
        return
    conn.execute("DELETE FROM reminders WHERE parent_id=?", (args.id,))
    conn.execute("DELETE FROM reminders WHERE id=?", (args.id,))
    conn.commit()
    print(L(conn, "deleted", id=args.id, title=row["title"]))


def cmd_snooze(conn, args):
    row = conn.execute("SELECT * FROM reminders WHERE id=?", (args.id,)).fetchone()
    if not row:
        print(L(conn, "not_found", id=args.id))
        return
    dt, _ = rs.parse_when(args.when)
    if dt is None:
        print(L(conn, "bad_duration", value=args.when))
        return
    conn.execute("DELETE FROM alarms WHERE reminder_id=? AND sent_at IS NULL AND kind='due'", (args.id,))
    conn.execute("INSERT OR REPLACE INTO alarms(reminder_id, fire_at, offset_minutes, kind, label, created_at) "
                 "VALUES (?,?,0,'due',?,?)", (args.id, rs.iso(dt), L(conn, "snooze_label"), rs.iso(rs.now())))
    conn.execute("UPDATE reminders SET nag_at=NULL, updated_at=? WHERE id=?", (rs.iso(rs.now()), args.id))
    conn.commit()
    print(L(conn, "snoozed", id=args.id, title=row["title"], when=rs.fmt_dt(conn, dt)))


def cmd_alarms(conn, args):
    row = conn.execute("SELECT * FROM reminders WHERE id=?", (args.id,)).fetchone()
    if not row:
        print(L(conn, "not_found", id=args.id))
        return
    if args.action == "add":
        aid = rs.add_alarm(conn, args.id, args.when, args.label, row["due_at"])
        if aid is None:
            print(L(conn, "alarm_needs_due", value=args.when))
            return
        show_one(conn, args.id)
        return
    if args.action == "rm":
        aid = int(args.when)
        conn.execute("DELETE FROM alarms WHERE id=? AND reminder_id=?", (aid, args.id))
        conn.commit()
        print(L(conn, "alarm_removed", id=aid))
        show_one(conn, args.id)
        return
    al = rs.alarms_of(conn, args.id)
    if getattr(args, "json", False):
        print(json.dumps([dict(a) for a in al], ensure_ascii=False, indent=2))
        return
    print(L(conn, "alarms_of", id=args.id, title=row["title"], n=len(al)))
    for a in al:
        lab = f" [{a['label']}]" if a["label"] else ""
        sent = f"  {L(conn, 'detail_sent')}" if a["sent_at"] else ""
        print(f"  \u00b7 id={a['id']}  {rs.fmt_dt(conn, rs.from_iso(a['fire_at']))}{lab}  {a['kind']}{sent}")


def cmd_section(conn, args):
    lid = resolve_list_id(conn, args.list)
    if not lid:
        print(L(conn, "list_not_found", name=args.list))
        return
    if args.action == "add":
        conn.execute("INSERT OR IGNORE INTO sections(list_id, name, position) VALUES (?,?,99)",
                     (lid, args.name))
        conn.commit()
        print(L(conn, "section_created", name=args.name, list=args.list))
    elif args.action == "rm":
        srow = conn.execute("SELECT id FROM sections WHERE list_id=? AND lower(name)=lower(?)",
                            (lid, args.name)).fetchone()
        if not srow:
            print(L(conn, "section_not_found"))
            return
        conn.execute("UPDATE reminders SET section_id=NULL WHERE section_id=?", (srow["id"],))
        conn.execute("DELETE FROM sections WHERE id=?", (srow["id"],))
        conn.commit()
        print(L(conn, "section_removed", name=args.name))
    else:
        rows = conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM reminders r WHERE r.section_id=s.id "
            "AND r.completed_at IS NULL) n FROM sections s WHERE s.list_id=? "
            "ORDER BY s.position, s.name", (lid,)).fetchall()
        print(f"{L(conn, 'sections_title', list=args.list)} ({len(rows)})")
        for r in rows:
            print(f"  {r['id']:>3}  {r['name']:<20} {L(conn, 'tag_open', n=r['n'])}")


def cmd_smart(conn, args):
    if args.action == "delete":
        lid = resolve_list_id(conn, args.name)
        if not lid:
            print(L(conn, "smart_not_found"))
            return
        conn.execute("DELETE FROM lists WHERE id=?", (lid,))
        conn.commit()
        print(L(conn, "smart_deleted", name=args.name))
        return
    rules: dict = {}
    if args.tags:
        rules["tags"] = [t.strip().lstrip("#") for t in args.tags.split(",")]
    if args.flag:
        rules["flagged"] = True
    if args.urgent:
        rules["urgent"] = True
    if args.priority:
        rules["priority"] = args.priority
    if args.from_list:
        rules["list_id"] = resolve_list_id(conn, args.from_list)
    if args.due_within is not None:
        rules["due_within_days"] = args.due_within
    if args.has_date:
        rules["has_date"] = True
    if args.no_date:
        rules["no_date"] = True
    if not rules:
        print(L(conn, "smart_needs_filter"))
        return
    cur = conn.execute(
        "INSERT INTO lists(name, icon, position, is_smart, smart_rules, smart_match_all, created_at) "
        "VALUES (?,?,99,1,?,?,?)",
        (args.name, args.icon, json.dumps(rules), 0 if args.any else 1, rs.iso(rs.now())))
    conn.commit()
    n = len(rs.smart_ids(conn).get(cur.lastrowid, set()))
    print(L(conn, "smart_created", name=args.name, n=n))


def cmd_template(conn, args):
    if args.action == "save":
        src = conn.execute("SELECT * FROM reminders WHERE id=?", (args.from_id,)).fetchone()
        if not src:
            print(L(conn, "not_found", id=args.from_id))
            return
        items = {"title": src["title"], "notes": src["notes"], "url": src["url"],
                 "priority": src["priority"], "tags": src["tags"],
                 "repeat_rule": src["repeat_rule"], "early_minutes": src["early_minutes"]}
        conn.execute("INSERT OR REPLACE INTO templates(name, items, created_at) VALUES (?,?,?)",
                     (args.name, json.dumps(items), rs.iso(rs.now())))
        conn.commit()
        print(L(conn, "template_saved", name=args.name))
        return
    if args.action == "delete":
        conn.execute("DELETE FROM templates WHERE name=?", (args.name,))
        conn.commit()
        print(L(conn, "template_deleted", name=args.name))
        return
    if args.action == "apply":
        t = conn.execute("SELECT * FROM templates WHERE lower(name)=lower(?)", (args.name,)).fetchone()
        if not t:
            print(L(conn, "template_not_found", name=args.name))
            return
        items = json.loads(t["items"])
        list_id = resolve_list(conn, args.list)
        due = None
        if args.due:
            d, ht = rs.parse_when(args.due)
            if d:
                due = (rs.iso(d), 1 if ht else 0)
        ts = rs.iso(rs.now())
        cur = conn.execute(
            """INSERT INTO reminders(list_id, title, notes, url, due_at, due_has_time, early_minutes,
                                     priority, repeat_rule, tags, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (list_id, items.get("title"), items.get("notes"), items.get("url"),
             due[0] if due else None, due[1] if due else 0, items.get("early_minutes"),
             items.get("priority") or 0, items.get("repeat_rule"), items.get("tags"), ts, ts))
        rs.rebuild_alarms(conn, cur.lastrowid)
        conn.commit()
        show_one(conn, cur.lastrowid)
        return
    rows = conn.execute("SELECT name FROM templates ORDER BY name").fetchall()
    if not rows:
        print(L(conn, "no_templates"))
        return
    print(f"{L(conn, 'templates_title')} ({len(rows)})")
    for r in rows:
        print(f"  {r['name']}")


def cmd_settings(conn, args):
    if args.action == "set":
        if args.key not in rs.DEFAULTS:
            print(L(conn, "unknown_key", key=args.key, keys=", ".join(rs.DEFAULTS)))
            return
        rs.set_setting(conn, args.key, args.value)
        print(L(conn, "set_ok", key=args.key, value=args.value))
        return
    if args.action == "get":
        print(L(conn, "set_ok", key=args.key, value=rs.get_setting(conn, args.key)))
        return
    rows = conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
    if getattr(args, "json", False):
        print(json.dumps({r["key"]: r["value"] for r in rows}, ensure_ascii=False, indent=2))
        return
    print(f"{L(conn, 'settings_title')} ({len(rows)})")
    for r in rows:
        print(f"  {r['key']:<22} {r['value']}")


def cmd_fits(conn, args):
    """Cosa chiudo in N minuti: il comando che rende utile la stima."""
    window = rs.parse_estimate(args.window) or rs.parse_offset(args.window.lstrip("+"))
    if not window:
        print(L(conn, "bad_duration", value=args.window))
        return
    ref = rs.now()
    today_end = rs.iso(ref.replace(hour=0, minute=0, second=0) + timedelta(days=1))
    rows = fetch(conn, "r.completed_at IS NULL AND r.parent_id IS NULL")

    cands: list[tuple] = []
    for r in rows:
        est = rs.effective_estimate(r)
        if est is None:
            if args.include_unknown:
                cands.append((r, None))
            continue
        if est <= window:
            cands.append((r, est))

    def sort_key(item):
        r, est = item
        overdue = 0 if (r["due_at"] and rs.from_iso(r["due_at"]) < ref) else 1
        today = 0 if (r["due_at"] and r["due_at"] < today_end) else 1
        prio = r["priority"] or 0
        prio = 10 if prio == 0 else prio          # senza priorità in fondo
        return (overdue, today, prio, -(est or 0))  # il più grande che ci sta

    cands.sort(key=sort_key)
    if args.count:
        cands = cands[:args.count]

    packed = False
    if args.pack:
        picked, used = [], 0
        for r, est in cands:
            if est is not None and used + est <= window:
                picked.append((r, est))
                used += est
        cands, packed = picked, True

    when = rs.fmt_estimate(window)
    if getattr(args, "json", False):
        print(json.dumps([dict(r) for r, _ in cands], ensure_ascii=False, indent=2))
        return
    if not cands:
        print(L(conn, "fits_none", when=when))
        return
    title = L(conn, "fits_title", when=when)
    print(f"{title} ({len(cands)})")
    total = 0
    for r, est in cands:
        line = rs.human(conn, r, ref)
        if r["list_name"]:
            line += f"  @{r['list_name']}"
        print("  " + line)
        total += est or 0
    if total:
        key = "totals_all" if not args.include_unknown else "totals_some"
        print("  " + L(conn, key, n=len(cands), total=rs.fmt_estimate(total),
                       m=sum(1 for _, e in cands if e is None)))
    if packed:
        print("  " + L(conn, "fits_packed", when=when))


def cmd_stats(conn, args):
    ref = rs.now()

    def q(where, params=()):
        return conn.execute(f"SELECT COUNT(*) c FROM reminders WHERE {where}", params).fetchone()["c"]

    end = rs.iso(ref.replace(hour=0, minute=0, second=0) + timedelta(days=1))
    data = {
        "total": q("1=1"), "open": q("completed_at IS NULL"),
        "today": q("completed_at IS NULL AND due_at IS NOT NULL AND due_at < ?", (end,)),
        "overdue": q("completed_at IS NULL AND due_at IS NOT NULL AND due_at < ?", (rs.iso(ref),)),
        "urgent": q("completed_at IS NULL AND urgent=1"),
        "flagged": q("completed_at IS NULL AND flagged=1"),
        "nodate": q("completed_at IS NULL AND due_at IS NULL"),
        "completed": q("completed_at IS NOT NULL"),
        "recurring": q("completed_at IS NULL AND repeat_rule IS NOT NULL"),
        "lists": conn.execute("SELECT COUNT(*) c FROM lists").fetchone()["c"],
        "pending": conn.execute("SELECT COUNT(*) c FROM alarms a JOIN reminders r ON r.id=a.reminder_id "
                                "WHERE a.sent_at IS NULL AND r.completed_at IS NULL").fetchone()["c"],
    }
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(L(conn, "stats_line1", **data))
    print(L(conn, "stats_line2", **data))


VIEW_NAMES = ("today", "scheduled", "all", "flagged", "urgent", "anytime", "overdue",
              "completed", "week", "nextweek", "days7")


def main() -> int:
    p = argparse.ArgumentParser(prog="rem", description="Reminders — a local Apple Reminders replica")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("lists", help="lists")
    pl.add_argument("action", nargs="?",
                    choices=["add", "delete", "mute", "unmute", "pin", "unpin", "default",
                             "rename", "folder"])
    pl.add_argument("name", nargs="?")
    pl.add_argument("to", nargs="?")
    pl.add_argument("--color", default="#3478F6")
    pl.add_argument("--icon")
    pl.add_argument("--folder")
    pl.add_argument("--pinned", action="store_true")
    pl.add_argument("--grocery", action="store_true")
    pl.add_argument("--default", action="store_true")
    pl.set_defaults(func=cmd_lists)

    psec = sub.add_parser("section", help="sections inside a list")
    psec.add_argument("action", nargs="?", choices=["add", "rm", "list"], default="list")
    psec.add_argument("list")
    psec.add_argument("name", nargs="?")
    psec.set_defaults(func=cmd_section)

    psm = sub.add_parser("smart", help="saved filtered views")
    psm.add_argument("action", nargs="?", choices=["add", "delete"], default="add")
    psm.add_argument("name")
    psm.add_argument("--tags")
    psm.add_argument("--flag", action="store_true")
    psm.add_argument("--urgent", action="store_true")
    psm.add_argument("--priority", choices=list(rs.PRIORITY))
    psm.add_argument("--from-list")
    psm.add_argument("--due-within", type=int)
    psm.add_argument("--has-date", action="store_true")
    psm.add_argument("--no-date", action="store_true")
    psm.add_argument("--any", action="store_true", help="match ANY filter instead of all")
    psm.add_argument("--icon", default="line.3.horizontal.decrease.circle")
    psm.set_defaults(func=cmd_smart)

    pa = sub.add_parser("add", help="create a reminder")
    pa.add_argument("title")
    pa.add_argument("--list")
    pa.add_argument("--due")
    pa.add_argument("--early", help="advance warning: 30m, 2h, 1d, 1w")
    pa.add_argument("--est", help="estimated time to complete: 45m, 2h, 1h30")
    pa.add_argument("--alarm", action="append", help="extra alarm (repeatable): 2h, 1d, 18:00")
    pa.add_argument("--no-alarm", action="store_true")
    pa.add_argument("--priority", choices=list(rs.PRIORITY), default="none")
    pa.add_argument("--flag", action="store_true")
    pa.add_argument("--urgent", action="store_true")
    pa.add_argument("--notes")
    pa.add_argument("--url")
    pa.add_argument("--attach", help="file path or attachment reference")
    pa.add_argument("--repeat", help="daily|weekly|monthly|yearly|hourly[:N] [pattern]")
    pa.add_argument("--repeat-until", help="end date for the recurrence")
    pa.add_argument("--repeat-from", choices=["due", "completion"], default="due")
    pa.add_argument("--tags", help="comma-separated tags")
    pa.add_argument("--section")
    pa.add_argument("--parent", type=int, help="parent reminder id (subtask)")
    pa.add_argument("--location")
    pa.add_argument("--location-trigger", choices=["arriving", "leaving"])
    pa.add_argument("--location-radius", type=int)
    pa.set_defaults(func=cmd_add)

    pv = sub.add_parser("view", help="views")
    pv.add_argument("view", choices=list(VIEW_NAMES))
    pv.set_defaults(func=cmd_view)
    for name in VIEW_NAMES:
        sp = sub.add_parser(name, help=f"view: {name}")
        sp.set_defaults(func=cmd_view, view=name)

    pn = sub.add_parser("list", help="show one list")
    pn.add_argument("name")
    pn.set_defaults(func=cmd_list_view)

    pcol = sub.add_parser("columns", help="column (kanban) view of a list")
    pcol.add_argument("name")
    pcol.set_defaults(func=cmd_columns)

    pf = sub.add_parser("fits", help="what fits in a given amount of time")
    pf.add_argument("window", help="available time: 30m, 1h, 1h30")
    pf.add_argument("--count", type=int, help="at most N reminders")
    pf.add_argument("--pack", action="store_true",
                    help="pick a subset whose estimates sum to within the window")
    pf.add_argument("--include-unknown", action="store_true",
                    help="also list reminders with no estimate")
    pf.set_defaults(func=cmd_fits)

    pt = sub.add_parser("tag", help="reminders carrying a tag")
    pt.add_argument("tag")
    pt.set_defaults(func=cmd_tag)

    pts = sub.add_parser("tags", help="all tags")
    pts.set_defaults(func=cmd_tags)

    ps = sub.add_parser("show", help="details")
    ps.add_argument("id", type=int)
    ps.set_defaults(func=cmd_show)

    pc = sub.add_parser("search", help="full-text search")
    pc.add_argument("query", nargs="+")
    pc.set_defaults(func=cmd_search)

    pd = sub.add_parser("done", help="complete / reopen")
    pd.add_argument("id", type=int)
    pd.set_defaults(func=cmd_done)

    pe = sub.add_parser("edit", help="modify")
    pe.add_argument("id", type=int)
    pe.add_argument("--title")
    pe.add_argument("--notes")
    pe.add_argument("--url")
    pe.add_argument("--attach")
    pe.add_argument("--due")
    pe.add_argument("--no-due", action="store_true")
    pe.add_argument("--early")
    pe.add_argument("--est", help="estimated time to complete: 45m, 2h, 1h30, none")
    pe.add_argument("--priority", choices=list(rs.PRIORITY))
    pe.add_argument("--flag", dest="flag", action="store_true", default=None)
    pe.add_argument("--no-flag", dest="flag", action="store_false")
    pe.add_argument("--urgent", dest="urgent", action="store_true", default=None)
    pe.add_argument("--no-urgent", dest="urgent", action="store_false")
    pe.add_argument("--repeat")
    pe.add_argument("--repeat-until")
    pe.add_argument("--repeat-from", choices=["due", "completion"], default="due")
    pe.add_argument("--list")
    pe.add_argument("--section")
    pe.add_argument("--tags")
    pe.add_argument("--location")
    pe.add_argument("--location-trigger", choices=["arriving", "leaving"])
    pe.set_defaults(func=cmd_edit)

    pdel = sub.add_parser("delete", help="delete")
    pdel.add_argument("id", type=int)
    pdel.set_defaults(func=cmd_delete)

    pz = sub.add_parser("snooze", help="snooze")
    pz.add_argument("id", type=int)
    pz.add_argument("when")
    pz.set_defaults(func=cmd_snooze)

    pal = sub.add_parser("alarms", help="alarms of a reminder")
    pal.add_argument("id", type=int)
    pal.add_argument("action", nargs="?", choices=["add", "rm"], default=None)
    pal.add_argument("when", nargs="?")
    pal.add_argument("--label")
    pal.set_defaults(func=cmd_alarms)

    ptp = sub.add_parser("template", help="list templates")
    ptp.add_argument("action", nargs="?", choices=["save", "apply", "list", "delete"], default="list")
    ptp.add_argument("name", nargs="?")
    ptp.add_argument("--from", dest="from_id", type=int)
    ptp.add_argument("--list")
    ptp.add_argument("--due")
    ptp.set_defaults(func=cmd_template)

    pst = sub.add_parser("settings", help="settings")
    pst.add_argument("action", nargs="?", choices=["list", "get", "set"], default="list")
    pst.add_argument("key", nargs="?")
    pst.add_argument("value", nargs="?")
    pst.set_defaults(func=cmd_settings)

    pstats = sub.add_parser("stats", help="summary")
    pstats.set_defaults(func=cmd_stats)

    args = p.parse_args()
    conn = rs.connect()
    try:
        args.func(conn, args)
    except BrokenPipeError:
        pass
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
