#!/usr/bin/env python3
"""Collaudo completo della skill reminders: esercita ogni feature e stampa un
report pass/fail. Gira su un profilo temporaneo, non tocca dati reali.

    python3 selftest.py        # esce 0 se tutto passa
"""
import os, shutil, subprocess, sys, sqlite3
from datetime import datetime, timedelta

import tempfile
SK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(tempfile.gettempdir(), "reminders-selftest")
PROF = os.path.join(ROOT, "profiles", "tester")
SCRIPTS = os.path.join(PROF, "skills", "productivity", "reminders", "scripts")
DB = os.path.join(PROF, "reminders", "reminders.db")

RESULTS = []
CTX = {"id": {}}


def sh(*args, lang=None):
    r = subprocess.run([sys.executable, "rem.py", *args], cwd=SCRIPTS,
                       capture_output=True, text=True, timeout=60)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def t(group, name, args, want=None, wantnot=None, code=0):
    rc, out = sh(*args)
    ok = rc == code
    if ok and want and want not in out:
        ok = False
    if ok and wantnot and wantnot in out:
        ok = False
    RESULTS.append((group, name, ok, "" if ok else f"rc={rc} out={out.strip()[:150]}"))
    return out


def _due_of(i):
    """Scadenza attuale di un promemoria, letta dal DB."""
    c = sqlite3.connect(DB)
    r = c.execute("SELECT due_at FROM reminders WHERE id=?", (i,)).fetchone()
    c.close()
    return r[0] if r else None


def add(title, *extra, **kw):
    """Aggiunge un promemoria e ne memorizza l'id."""
    out = t(kw.get("group", "add"), f'add "{title}"', ["add", title, *extra])
    for line in out.splitlines():
        if line.startswith("#"):
            CTX["id"][title] = line.split()[0][1:]
            break
    return CTX["id"].get(title)


def setup():
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(os.path.join(PROF, "skills", "productivity"), exist_ok=True)
    shutil.copytree(SK, os.path.join(PROF, "skills", "productivity", "reminders"))
    for dp, dn, fn in os.walk(ROOT):
        for d in list(dn):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(dp, d), ignore_errors=True)
    subprocess.run([sys.executable, "setup.py"], cwd=SCRIPTS, capture_output=True, text=True)


# ─── A. installazione ────────────────────────────────────────────────────────
def test_setup():
    g = "A · install"
    c = sqlite3.connect(DB)
    rc, out = sh("settings")
    RESULTS.append((g, "settings leggibili", rc == 0, out[:80] if rc else ""))
    cols = [r[1] for r in c.execute("PRAGMA table_info(reminders)")]
    for col in ("estimate_minutes", "urgent", "nag_at", "repeat_rule", "tags", "parent_id"):
        RESULTS.append((g, f"colonna {col}", col in cols, ""))
    RESULTS.append((g, "list_id nullable",
                    not [r for r in c.execute("PRAGMA table_info(reminders)") if r[1] == "list_id"][0][3], ""))
    RESULTS.append((g, "nessun elenco pre-creato",
                    c.execute("SELECT COUNT(*) FROM lists").fetchone()[0] == 0, ""))
    RESULTS.append((g, "integrity ok",
                    c.execute("PRAGMA integrity_check").fetchone()[0] == "ok", ""))
    c.close()


# ─── B. elenchi ──────────────────────────────────────────────────────────────
def test_lists():
    g = "B · elenchi"
    t(g, "crea Work con colore/icona/cartella/pin",
      ["lists", "add", "Work", "--color", "blue", "--icon", "briefcase", "--folder", "Ufficio", "--pinned"])
    t(g, "crea Groceries (grocery)", ["lists", "add", "Groceries", "--grocery"])
    t(g, "crea Personal", ["lists", "add", "Personal"])
    t(g, "elenco mostra 3", ["lists"], want="Work")
    t(g, "rename", ["lists", "rename", "Personal", "Casa"])
    t(g, "folder", ["lists", "folder", "Casa", "Privato"])
    t(g, "mute", ["lists", "mute", "Casa"])
    t(g, "unmute", ["lists", "unmute", "Casa"])
    t(g, "default", ["lists", "default", "Work"])
    t(g, "pin/unpin", ["lists", "pin", "Casa"])
    t(g, "grocery flag", ["lists"], want="Groceries")
    c = sqlite3.connect(DB)
    gc = c.execute("SELECT is_grocery FROM lists WHERE name='Groceries'").fetchone()[0]
    RESULTS.append((g, "flag grocery salvato", gc == 1, ""))
    c.close()


# ─── C. sezioni + kanban ─────────────────────────────────────────────────────
def test_sections():
    g = "C · sezioni"
    t(g, "crea sezione", ["section", "add", "Work", "In corso"])
    t(g, "lista sezioni", ["section", "list", "Work"], want="In corso")
    add("Con sezione", "--list", "Work", "--section", "In corso", group=g)
    t(g, "columns (kanban)", ["columns", "Work"], want="In corso")
    t(g, "rimuovi sezione", ["section", "rm", "Work", "In corso"])


# ─── D. promemoria: campi ────────────────────────────────────────────────────
def test_fields():
    g = "D · campi"
    add("Senza elenco", group=g)
    add("Completo", "--list", "Work", "--notes", "dettaglio segreto",
        "--url", "https://example.com", "--tags", "alfa,beta",
        "--priority", "high", "--flag", "--urgent", group=g)
    i = CTX["id"]["Completo"]
    out = t(g, "show mostra priorità", ["show", i], want="high")
    t(g, "show mostra flag", ["show", i], want="flag")
    t(g, "show mostra urgente", ["show", i], want="URGENT")
    t(g, "show mostra note", ["show", i], want="dettaglio segreto")
    t(g, "show mostra url", ["show", i], want="example.com")
    t(g, "tag filtra", ["tag", "alfa"], want="Completo")
    t(g, "tags elenca", ["tags"], want="alfa")
    t(g, "ricerca full-text", ["search", "segreto"], want="Completo")
    t(g, "ricerca senza risultati", ["search", "zzzznope"], wantnot="Completo")
    t(g, "senza elenco compare in all", ["all"], want="Senza elenco")


# ─── E. date ─────────────────────────────────────────────────────────────────
def test_dates():
    g = "E · date"
    for label, due in [("domani", "tomorrow 9:00"), ("lunedi", "monday 9:30"),
                       ("assoluta", "2026-10-12 10:30"), ("oggi", "today 18:30"),
                       ("offset", "+30m"), ("giorno", "friday 10:00")]:
        add(f"Data {label}", "--due", due, group=g)
    add("All-day", "--due", "2026-12-25", group=g)
    c = sqlite3.connect(DB)
    r = c.execute("SELECT due_has_time FROM reminders WHERE title='All-day'").fetchone()[0]
    RESULTS.append((g, "all-day registrato senza ora", r == 0, ""))
    r = c.execute("SELECT COUNT(*) FROM reminders WHERE title LIKE 'Data %' AND due_at IS NOT NULL").fetchone()[0]
    RESULTS.append((g, "tutte le 6 date parsate", r == 6, f"trovate {r}"))
    c.close()


# ─── F. early + allarmi ──────────────────────────────────────────────────────
def test_alarms():
    g = "F · allarmi"
    add("Con early", "--due", "tomorrow 12:00", "--early", "2h", group=g)
    i = CTX["id"]["Con early"]
    t(g, "early mostrato", ["show", i], want="2h")
    add("Multi allarme", "--due", "tomorrow 15:00", "--alarm", "3h", "--alarm", "17:30", group=g)
    j = CTX["id"]["Multi allarme"]
    out = t(g, "tre allarmi", ["alarms", j])
    RESULTS.append((g, "3 allarmi per il reminder", out.count("·") >= 3, out[:120]))
    t(g, "aggiungi allarme", ["alarms", j, "add", "1d", "--label", "un giorno prima"])
    t(g, "etichetta allarme", ["alarms", j], want="un giorno prima")
    c = sqlite3.connect(DB)
    aid = c.execute("SELECT id FROM alarms WHERE reminder_id=? ORDER BY id DESC LIMIT 1", (j,)).fetchone()[0]
    c.close()
    t(g, "rimuovi allarme", ["alarms", j, "rm", str(aid)])
    add("Senza allarmi", "--due", "tomorrow 16:00", "--no-alarm", group=g)
    k = CTX["id"]["Senza allarmi"]
    out = t(g, "no-alarm: nessun allarme", ["alarms", k])
    RESULTS.append((g, "nessun allarme creato", "·" not in out.replace("Alarms", ""), out[:100]))


# ─── G. ricorrenze ───────────────────────────────────────────────────────────
def test_recurrence():
    g = "G · ricorrenze"
    cases = [("daily", "daily"), ("daily:3", "daily"), ("hourly", "hourly"),
             ("weekly", "weekly"), ("weekly:mon,wed", "weekly"),
             ("monthly:15", "monthly"), ("monthly:last:fri", "monthly"),
             ("monthly:2:15", "monthly"), ("yearly", "yearly"), ("yearly:mar,jun", "yearly")]
    for spec, want in cases:
        add(f"Ric {spec}", "--due", "tomorrow 8:00", "--repeat", spec, group=g)
        i = CTX["id"][f"Ric {spec}"]
        out = t(g, f"ricorrenza {spec}", ["show", i], want=want)
    c = sqlite3.connect(DB)
    r = c.execute("SELECT repeat_rule FROM reminders WHERE title='Ric monthly:15'").fetchone()[0]
    RESULTS.append((g, "monthly:15 = giorno 15, non ogni 15 mesi", "bymonthday" in r and "15" in r, r))
    c.close()
    add("Ric until", "--due", "tomorrow 8:00", "--repeat", "daily", "--repeat-until", "2027-12-31", group=g)
    i = CTX["id"]["Ric until"]
    t(g, "repeat-until mostrato", ["show", i], want="2027")
    add("Ric from completion", "--due", "tomorrow 8:00", "--repeat", "daily:3", "--repeat-from", "completion", group=g)
    i = CTX["id"]["Ric from completion"]
    t(g, "from completion mostrato", ["show", i], want="completion")

    # Accenti: in italiano la forma corretta è accentata e deve funzionare.
    for acc in ("lunedì 9:00", "martedì 9:00", "mercoledì 10:00", "giovedì", "venerdì"):
        add(f"Acc {acc}", "--due", acc, group=g)
        i = CTX["id"][f"Acc {acc}"]
        out = t(g, f"data accentata «{acc}»", ["show", i])
        RESULTS.append((g, f"«{acc}» riconosciuta", "due:" in out, out[:100]))

    # Serie infinita: ogni lunedì, per sempre.
    add("Sempre lunedi", "--due", "monday 9:00", "--repeat", "weekly:mon", group=g)
    i = CTX["id"]["Sempre lunedi"]
    d0 = _due_of(i)
    sh("done", i)
    d1 = _due_of(i)
    RESULTS.append((g, "serie infinita: avanza di una settimana",
                    bool(d0 and d1 and d0 != d1), f"{d0} → {d1}"))
    sh("done", i)
    d2 = _due_of(i)
    RESULTS.append((g, "serie infinita: continua ad avanzare",
                    bool(d1 and d2 and d1 != d2), f"{d1} → {d2}"))

    # Serie a numero chiuso: ogni martedì per 5 settimane.
    add("Cinque martedi", "--due", "tuesday 10:00", "--repeat", "weekly:tue",
        "--repeat-count", "5", group=g)
    j = CTX["id"]["Cinque martedi"]
    t(g, "mostra l'occorrenza corrente", ["show", j], want="1 of 5")
    seen, last = [], ""
    for _ in range(5):
        seen.append(_due_of(j))
        last = sh("done", j)[1]
    RESULTS.append((g, "5 occorrenze distinte, poi stop", len(set(seen)) == 5, str(seen)))
    RESULTS.append((g, "la quinta chiusura segnala serie completata",
                    "series complete" in last, last[:80]))
    c = sqlite3.connect(DB)
    done_state = c.execute("SELECT completed_at FROM reminders WHERE id=?", (j,)).fetchone()[0]
    c.close()
    RESULTS.append((g, "a serie finita il promemoria resta chiuso", bool(done_state), str(done_state)))

    # Ritorno a serie illimitata.
    add("Torna infinita", "--due", "wednesday 8:00", "--repeat", "weekly:wed",
        "--repeat-count", "3", group=g)
    k = CTX["id"]["Torna infinita"]
    sh("edit", k, "--repeat-count", "0")
    out = t(g, "illimitata: contatore rimosso", ["show", k])
    RESULTS.append((g, "contatore rimosso con --repeat-count 0", "of 3" not in out, out[:100]))

    # Una ricorrenza rimasta ferma a lungo non deve far smaltire a mano tutte le
    # occorrenze perse: si riparte dalla prossima futura.
    import datetime as _dt
    add("Ferma da settimane", "--due", "2026-08-24 09:00", "--repeat", "weekly:mon", group=g)
    i = CTX["id"]["Ferma da settimane"]
    sh("done", i)
    nxt = _due_of(i)
    RESULTS.append((g, "in ritardo: riparte dalla prossima occorrenza futura",
                    bool(nxt) and _dt.datetime.fromisoformat(nxt) > _dt.datetime.now(),
                    f"→ {nxt}"))

    # Chiudere in anticipo non deve far saltare un giro.
    add("In anticipo", "--due", "monday 9:00", "--repeat", "weekly:mon", group=g)
    j2 = CTX["id"]["In anticipo"]
    before = _due_of(j2)
    sh("done", j2)
    after = _due_of(j2)
    delta = (_dt.datetime.fromisoformat(after) - _dt.datetime.fromisoformat(before)).days
    RESULTS.append((g, "in anticipo: avanza di 7 giorni esatti", delta == 7,
                    f"{before} → {after} ({delta}g)"))


# ─── H. viste ────────────────────────────────────────────────────────────────
def test_views():
    g = "H · viste"
    for v in ("today", "scheduled", "all", "flagged", "urgent", "anytime",
              "overdue", "completed", "week", "nextweek", "days7"):
        t(g, f"view {v}", [v])
    add("Scaduto", "--due", "yesterday 9:00", group=g)
    t(g, "overdue trova lo scaduto", ["overdue"], want="Scaduto")
    t(g, "flagged trova il flaggato", ["flagged"], want="Completo")
    t(g, "urgent trova l'urgente", ["urgent"], want="Completo")
    t(g, "anytime trova i senza data", ["anytime"], want="Senza elenco")


# ─── I. smart list ───────────────────────────────────────────────────────────
def test_smart():
    g = "I · smart list"
    t(g, "smart urgente", ["smart", "Urgenti", "--urgent"])
    t(g, "smart flaggati", ["smart", "Flaggati", "--flag"])
    t(g, "smart priorità", ["smart", "Alta", "--priority", "high"])
    t(g, "smart entro 3 giorni", ["smart", "Entro3", "--due-within", "3"])
    t(g, "smart senza data", ["smart", "SenzaData", "--no-date"])
    t(g, "smart da elenco", ["smart", "DaWork", "--from-list", "Work"])
    t(g, "smart per tag", ["smart", "Alfa", "--tags", "alfa"])
    t(g, "smart OR", ["smart", "OrView", "--urgent", "--flag", "--any"])
    t(g, "smart listate negli elenchi", ["lists"], want="Urgenti")
    out = t(g, "smart urgente filtra bene", ["list", "Urgenti"], want="Completo")
    RESULTS.append((g, "smart non include i non-urgenti", "Senza elenco" not in out, out[:120]))
    t(g, "smart delete", ["smart", "delete", "OrView"])


# ─── J. template ─────────────────────────────────────────────────────────────
def test_templates():
    g = "J · template"
    i = CTX["id"]["Completo"]
    out = t(g, "salva template", ["template", "save", "Deliverable", "--from", i])
    t(g, "template elencato", ["template", "list"], want="Deliverable")
    before = sh("all")[1].count("Completo")
    t(g, "applica template", ["template", "apply", "Deliverable", "--list", "Work"])
    after = sh("all")[1].count("Completo")
    RESULTS.append((g, "il template ha creato un nuovo promemoria", after == before + 1, f"{before} → {after}"))


# ─── K. stime ────────────────────────────────────────────────────────────────
def test_estimates():
    g = "K · stime"
    add("Stima 45", "--est", "45m", "--due", "tomorrow 9:00", group=g)
    i = CTX["id"]["Stima 45"]
    t(g, "stima in show", ["show", i], want="45m")
    t(g, "stima in all", ["all"], want="[45m]")
    t(g, "stima 1h30", ["add", "Stima 90", "--est", "1h30"], want="1h30")
    CTX["id"]["Stima 90"] = [l for l in sh("all")[1].splitlines() if "Stima 90" in l][0].split()[0][1:]
    t(g, "stima 1.5h", ["add", "Stima float", "--est", "1.5h"], want="1h30")
    t(g, "stima in minuti nudi", ["add", "Stima nude", "--est", "120"], want="2h")
    t(g, "stima non valida rifiutata", ["add", "Stima brutta", "--est", "boh"], want="duration", code=0)
    t(g, "fits base", ["fits", "1h"], want="Fits in 1h")
    t(g, "fits niente", ["fits", "1m"], want="nothing fits")
    add("Ord scaduto", "--est", "10m", "--due", "yesterday 8:00", group=g)
    add("Ord grande", "--est", "1h", group=g)
    add("Ord piccolo", "--est", "5m", group=g)
    out = t(g, "fits ordina", ["fits", "2h"])
    lines = [l for l in out.splitlines() if "·" in l]
    idx = {k: next((n for n, l in enumerate(lines) if k in l), -1)
           for k in ("Ord scaduto", "Ord grande", "Ord piccolo")}
    RESULTS.append((g, "fits: lo scaduto viene prima",
                    idx["Ord scaduto"] == 0, str(idx)))
    RESULTS.append((g, "fits: a parità, il più grande che ci sta",
                    0 <= idx["Ord grande"] < idx["Ord piccolo"], str(idx)))
    t(g, "fits --count", ["fits", "2h", "--count", "1"])
    out = t(g, "fits --count rispettato", ["fits", "2h", "--count", "1"])
    RESULTS.append((g, "conta 1 riga", sum(1 for l in out.splitlines() if "·" in l) == 1, out[:100]))
    t(g, "fits --pack", ["fits", "2h", "--pack"], want="slot")
    t(g, "fits --include-unknown", ["fits", "1h", "--include-unknown"], want="without")
    add("Padre rollup", group=g)
    p = CTX["id"]["Padre rollup"]
    add("Figlio A", "--parent", p, "--est", "20m", group=g)
    add("Figlio B", "--parent", p, "--est", "25m", group=g)
    t(g, "rollup somma i figli", ["show", p], want="45m")
    c = sqlite3.connect(DB)
    own = c.execute("SELECT estimate_minutes FROM reminders WHERE id=?", (p,)).fetchone()[0]
    c.close()
    RESULTS.append((g, "il padre resta senza stima propria", own is None, str(own)))
    t(g, "edit --est none azzera", ["edit", i, "--est", "none"])
    out = t(g, "stima rimossa", ["show", i])
    RESULTS.append((g, "nessuna stima dopo none", "stimate" not in out.lower(), out[:100]))
    t(g, "edit ripristina stima", ["edit", i, "--est", "30m"])
    t(g, "totali nella vista", ["all"], want="with an estimate")


# ─── L. sottopromemoria ──────────────────────────────────────────────────────
def test_subtasks():
    g = "L · sottopromemoria"
    add("Genitore", group=g)
    p = CTX["id"]["Genitore"]
    add("Figlio 1", "--parent", p, group=g)
    add("Figlio 2", "--parent", p, group=g)
    t(g, "show elenca figli", ["show", p], want="subtasks")
    t(g, "completare il padre completa i figli", ["done", p])
    c = sqlite3.connect(DB)
    n = c.execute("SELECT COUNT(*) FROM reminders WHERE parent_id=? AND completed_at IS NOT NULL", (p,)).fetchone()[0]
    c.close()
    RESULTS.append((g, "entrambi i figli chiusi", n == 2, f"chiusi {n}"))
    t(g, "riapertura padre", ["done", p])


# ─── M. spesa ────────────────────────────────────────────────────────────────
def test_grocery():
    g = "M · spesa"
    for x in ("apples", "milk", "bread", "dish soap", "detersivo", "latte"):
        t(g, f"aggiungi {x}", ["add", x, "--list", "Groceries"])
    out = t(g, "categorie EN", ["list", "Groceries"], want="Produce")
    t(g, "EN: Dairy & eggs", ["list", "Groceries"], want="Dairy & eggs")
    t(g, "EN: Household & hygiene", ["list", "Groceries"], want="Household & hygiene")
    RESULTS.append((g, "EN: 'milk' fuori da Other", "Other" not in out or "milk" not in out.split("Other")[-1], ""))
    sh("settings", "set", "language", "it")
    out2 = t(g, "categorie IT", ["list", "Groceries"], want="Ortofrutta")
    t(g, "IT: Latticini", ["list", "Groceries"], want="Latticini e uova")
    t(g, "IT: latte e milk stessa categoria", ["list", "Groceries"], want="Latticini e uova")
    sh("settings", "set", "language", "en")


# ─── N. consegna ─────────────────────────────────────────────────────────────
def test_delivery():
    g = "N · consegna"
    c = sqlite3.connect(DB)
    # fire_at unico per riga: il vincolo UNIQUE(reminder_id, fire_at, kind) non
    # permette di collassare tutti gli allarmi sullo stesso istante.
    c.execute("UPDATE alarms SET sent_at=NULL, "
              "fire_at='2020-01-01T10:' || printf('%02d', id % 60) || ':' || printf('%02d', (id / 60) % 60)")
    c.commit()
    c.close()
    r = subprocess.run([sys.executable, "tick.py", "--dry-run"], cwd=SCRIPTS, capture_output=True, text=True)
    RESULTS.append((g, "tick --dry-run elenca gli allarmi scaduti", r.returncode == 0 and "·" in r.stdout, r.stdout[:120]))
    r = subprocess.run([sys.executable, "tick.py"], cwd=SCRIPTS, capture_output=True, text=True)
    fired = r.stdout.strip()
    RESULTS.append((g, "tick reale emette", bool(fired), fired[:120]))
    r = subprocess.run([sys.executable, "tick.py"], cwd=SCRIPTS, capture_output=True, text=True)
    RESULTS.append((g, "secondo tick non ripete", r.stdout.strip() == "", r.stdout[:80]))
    t(g, "tick marca sent_at", ["show", CTX["id"]["Con early"]], want="sent")

    # ── Un promemoria SENZA elenco deve essere consegnato ──────────────────
    # Con list_id NULL una INNER JOIN su lists lo faceva sparire del tutto:
    # nessuna notifica, mai, in silenzio.
    add("Senza elenco in consegna", "--due", "2020-01-01 10:00", group=g)
    i = CTX["id"]["Senza elenco in consegna"]
    c = sqlite3.connect(DB)
    c.execute("UPDATE reminders SET list_id=NULL WHERE id=?", (i,))
    c.commit()
    c.close()
    out = subprocess.run([sys.executable, "tick.py", "--dry-run"], cwd=SCRIPTS,
                         capture_output=True, text=True).stdout
    RESULTS.append((g, "promemoria senza elenco consegnato",
                    "Senza elenco in consegna" in out, out[:120]))

    # ── Recupero dopo un'assenza ───────────────────────────────────────────
    # Un avviso "in anticipo" la cui scadenza è già passata non è un
    # promemoria, è rumore; e lo stesso promemoria non deve comparire due volte.
    add("Recupero", "--due", "2026-09-15 09:00", "--early", "1h", group=g)
    r = CTX["id"]["Recupero"]
    out = subprocess.run([sys.executable, "tick.py", "--dry-run"], cwd=SCRIPTS,
                         capture_output=True, text=True).stdout
    rec = [l for l in out.splitlines() if "Recupero" in l]
    RESULTS.append((g, "recupero: una sola riga per promemoria", len(rec) == 1, str(rec)))
    RESULTS.append((g, "recupero: avviso 'in anticipo' tardivo soppresso",
                    not any("upcoming" in l for l in rec), str(rec)))


def run_tick(*extra):
    """Esegue tick.py e restituisce stdout."""
    r = subprocess.run([sys.executable, "tick.py", *extra], cwd=SCRIPTS,
                       capture_output=True, text=True, timeout=120)
    return r.stdout


# ─── Q. azioni ───────────────────────────────────────────────────────────────
def test_actions():
    g = "Q · azioni"
    sh("settings", "set", "language", "en")

    gate0 = subprocess.run([sys.executable, "rem.py", "actions", "gate"], cwd=SCRIPTS,
                           capture_output=True, text=True).stdout
    RESULTS.append((g, "gate vuoto a riposo", gate0.strip() == "", repr(gate0)))

    # La scadenza accoda; l'avviso in anticipo no.
    add("Azione base", "--due", "2026-01-01 09:00", "--early", "2h",
        "--action", "Riporta qualcosa di utile", group=g)
    i = CTX["id"]["Azione base"]
    c = sqlite3.connect(DB)
    # La scadenza e' nel passato: la metto da parte, altrimenti scatterebbe
    # insieme all'avviso e accoderebbe l'azione, falsando il test dell'early.
    c.execute("UPDATE alarms SET sent_at='2020-01-01T00:00:00' "
              "WHERE reminder_id=? AND kind='due'", (i,))
    c.execute("UPDATE alarms SET fire_at='2020-01-01T07:00:00', sent_at=NULL "
              "WHERE reminder_id=? AND kind='early'", (i,))
    c.commit()
    c.close()
    run_tick()
    c = sqlite3.connect(DB)
    n = c.execute("SELECT COUNT(*) FROM action_queue").fetchone()[0]
    c.close()
    RESULTS.append((g, "l'avviso in anticipo NON accoda", n == 0, f"righe={n}"))

    c = sqlite3.connect(DB)
    c.execute("UPDATE alarms SET fire_at='2020-01-01T09:00:00', sent_at=NULL WHERE reminder_id=? AND kind='due'", (i,))
    c.commit()
    c.close()
    run_tick()
    c = sqlite3.connect(DB)
    rows = c.execute("SELECT id FROM action_queue WHERE reminder_id=?", (i,)).fetchall()
    c.close()
    RESULTS.append((g, "la scadenza accoda l'azione", len(rows) == 1, f"righe={len(rows)}"))
    qid = rows[0][0] if rows else 0

    # La coda sopravvive al completamento del promemoria (rebuild degli allarmi).
    sh("done", i)
    c = sqlite3.connect(DB)
    n = c.execute("SELECT COUNT(*) FROM action_queue WHERE id=?", (qid,)).fetchone()[0]
    c.close()
    RESULTS.append((g, "l'azione sopravvive al completamento", n == 1, f"righe={n}"))

    # Il gate cambia quando c'e' qualcosa, ed e' deterministico.
    g1 = subprocess.run([sys.executable, "rem.py", "actions", "gate"], cwd=SCRIPTS,
                        capture_output=True, text=True).stdout
    g2 = subprocess.run([sys.executable, "rem.py", "actions", "gate"], cwd=SCRIPTS,
                        capture_output=True, text=True).stdout
    RESULTS.append((g, "gate non vuoto con un'azione", g1.strip() != "", repr(g1[:60])))
    RESULTS.append((g, "gate deterministico fra due letture", g1 == g2, repr(g1[:60])))

    # L'agente deposita il risultato; il tick lo consegna una volta sola.
    sh("actions", "done", str(qid), "--result", "Esito di prova")
    out = run_tick("--dry-run")
    RESULTS.append((g, "il tick mostra il risultato", "Esito di prova" in out, out[:120]))
    run_tick()
    c = sqlite3.connect(DB)
    d1 = c.execute("SELECT delivered_at FROM action_queue WHERE id=?", (qid,)).fetchone()[0]
    res = c.execute("SELECT result FROM action_queue WHERE id=?", (qid,)).fetchone()[0]
    c.close()
    RESULTS.append((g, "consegna marcata", bool(d1), str(d1)))
    RESULTS.append((g, "il risultato resta agli atti", res == "Esito di prova", str(res)))
    out2 = run_tick("--dry-run")
    RESULTS.append((g, "non si consegna due volte", "Esito di prova" not in out2, out2[:100]))

    # Fallimento e ritentativo.
    add("Azione che fallisce", "--due", "2026-01-02 09:00", "--action", "Non fattibile", group=g)
    j = CTX["id"]["Azione che fallisce"]
    c = sqlite3.connect(DB)
    c.execute("UPDATE alarms SET fire_at='2020-01-02T09:00:00', sent_at=NULL WHERE reminder_id=? AND kind='due'", (j,))
    c.commit()
    c.close()
    run_tick()
    c = sqlite3.connect(DB)
    row2 = c.execute("SELECT id FROM action_queue WHERE reminder_id=?", (j,)).fetchone()
    c.close()
    RESULTS.append((g, "seconda azione accodata", row2 is not None, str(row2)))
    q2 = row2[0] if row2 else 0
    sh("actions", "fail", str(q2), "motivo di prova")
    c = sqlite3.connect(DB)
    err = c.execute("SELECT error FROM action_queue WHERE id=?", (q2,)).fetchone()[0]
    c.close()
    RESULTS.append((g, "il fallimento registra il motivo", err == "motivo di prova", str(err)))
    sh("actions", "retry", str(q2))
    c = sqlite3.connect(DB)
    att = c.execute("SELECT attempted_at FROM action_queue WHERE id=?", (q2,)).fetchone()[0]
    c.close()
    RESULTS.append((g, "retry riapre l'azione", att is None, str(att)))

def test_i18n():
    g = "O · i18n"
    out_en = sh("week")[1]
    sh("settings", "set", "language", "it")
    out_it = sh("week")[1]
    RESULTS.append((g, "le due lingue differiscono", out_en != out_it, ""))
    RESULTS.append((g, "IT usa 'Questa settimana'", "Questa settimana" in out_it, out_it[:80]))
    RESULTS.append((g, "EN usa 'This week'", "This week" in out_en, out_en[:80]))
    t(g, "IT: SCADUTO", ["overdue"], want="SCADUTO")
    sh("settings", "set", "language", "en")
    out = t(g, "EN: OVERDUE", ["overdue"], want="OVERDUE")
    RESULTS.append((g, "EN non mostra SCADUTO", "SCADUTO" not in out, out[:80]))
    sh("settings", "set", "time_format", "12")
    t(g, "formato 12h", ["show", CTX["id"]["Completo"]])
    sh("settings", "set", "time_format", "24")
    t(g, "impostazioni listate", ["settings"], want="language")


# ─── P. manutenzione ─────────────────────────────────────────────────────────
def test_maint():
    g = "P · manutenzione"
    add("Da chiudere", group=g)
    i = CTX["id"]["Da chiudere"]
    t(g, "done", ["done", i])
    t(g, "completed la trova", ["completed"], want="Da chiudere")
    t(g, "reopen", ["done", i])
    t(g, "snooze", ["snooze", i, "+15m"])
    t(g, "edit due", ["edit", i, "--due", "friday 11:00"])
    t(g, "edit priorità", ["edit", i, "--priority", "low"])
    t(g, "edit no-due", ["edit", i, "--no-due"])
    t(g, "delete", ["delete", i])
    t(g, "statistiche", ["stats"], want="Reminders")
    c = sqlite3.connect(DB)
    RESULTS.append((g, "integrity finale", c.execute("PRAGMA integrity_check").fetchone()[0] == "ok", ""))
    n = c.execute("SELECT COUNT(*) FROM reminders").fetchone()[0]
    a = c.execute("SELECT COUNT(*) FROM alarms").fetchone()[0]
    l = c.execute("SELECT COUNT(*) FROM lists").fetchone()[0]
    c.close()
    print(f"\n  stato finale: {n} promemoria · {a} allarmi · {l} elenchi")


def main():
    setup()
    for fn in (test_setup, test_lists, test_sections, test_fields, test_dates, test_alarms,
               test_recurrence, test_views, test_smart, test_templates, test_estimates,
               test_subtasks, test_grocery, test_delivery, test_i18n, test_maint,
               test_actions):
        try:
            fn()
        except Exception as e:
            RESULTS.append((fn.__name__, f"CRASH: {type(e).__name__}", False, str(e)[:200]))
    print("\n" + "═" * 62)
    groups = {}
    for grp, name, ok, err in RESULTS:
        groups.setdefault(grp, []).append((name, ok, err))
    fails = 0
    for grp in groups:
        items = groups[grp]
        bad = [x for x in items if not x[1]]
        fails += len(bad)
        mark = "✓" if not bad else "✗"
        print(f"{mark} {grp:<22} {len(items) - len(bad)}/{len(items)}")
        for name, ok, err in bad:
            print(f"    ✗ {name}")
            if err:
                print(f"      {err}")
    total = sum(len(v) for v in groups.values())
    print("═" * 62)
    print(f"{total - fails}/{total} test passati · {fails} falliti")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
