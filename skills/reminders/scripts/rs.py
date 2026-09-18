"""rs.py — modello dati e motore dei promemoria.

Replica la semantica di Apple Reminders:
  liste (+ cartelle, colore, icona, preferite, mute), sezioni, sottopromemoria,
  tag, priorita, flag, urgent, note, URL, allegati, localita',
  scadenza (con/senza orario), early reminder, PIU allarmi, ricorrenze
  (oraria/giornaliera/settimanale/mensile/annuale con pattern e fine),
  ricorrenza da scadenza o da completamento, template, liste smart, spesa,
  viste (Oggi, Programmato, Tutti, Flaggati, Urgent, Anytime, Completati),
  localizzazione (lingua, 12/24h, orario all-day, all-day scaduti o no).

Il DB vive accanto alla skill, dentro il profilo Hermes che la ospita
(<profilo>/reminders/reminders.db): nessun percorso cablato.
"""
from __future__ import annotations

import calendar as _cal
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import i18n

def data_dir() -> Path:
    """Cartella dati del profilo che ospita questa skill.

    Ordine: risalita dal percorso della skill (piu affidabile, funziona per
    qualsiasi profilo) → $HERMES_HOME → ~/.hermes.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if parent.name == "skills":
            return parent.parent / "reminders"
    env = os.environ.get("HERMES_HOME")
    base = Path(env).expanduser() if env else Path.home() / ".hermes"
    return base / "reminders"


DB_DIR = str(data_dir())
DB_PATH = os.path.join(DB_DIR, "reminders.db")

PRIORITY = {"none": 0, "low": 9, "medium": 5, "high": 1}
PRIORITY_LABEL = {0: "none", 9: "low", 5: "medium", 1: "high"}

WEEKDAYS_IT = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
WEEKDAYS_EN = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_ALIAS = {
    "lun": 0, "lunedi": 0, "mon": 0, "monday": 0,
    "mar": 1, "martedi": 1, "tue": 1, "tuesday": 1,
    "mer": 2, "mercoledi": 2, "wed": 2, "wednesday": 2,
    "gio": 3, "giovedi": 3, "thu": 3, "thursday": 3,
    "ven": 4, "venerdi": 4, "fri": 4, "friday": 4,
    "sab": 5, "sabato": 5, "sat": 5, "saturday": 5,
    "dom": 6, "domenica": 6, "sun": 6, "sunday": 6,
}
# Apple: "On the First/Second/Third/Fourth/Last <weekday>"
SETPOS = {"first": 1, "primo": 1, "second": 2, "secondo": 2, "third": 3, "terzo": 3,
          "fourth": 4, "quarto": 4, "last": -1, "ultimo": -1, "ultima": -1}

DEFAULTS = {
    "language": "en",   # English is the shipped default; 'it' switches the whole CLI
    "time_format": "24",
    "date_format": "dd/mm/yyyy",
    "all_day_notify_time": "09:00",
    "show_all_day_overdue": "0",
    "nag_minutes": "10",
    "nag_max": "6",
    "default_list_id": "",
}

# La settimana comincia SEMPRE di lunedi: non e' una preferenza configurabile.
WEEK_START = 0

# Categorie della spesa: chiave interna → etichetta per lingua → parole chiave
# (entrambe le lingue, così "milk" e "latte" finiscono nella stessa categoria).
GROCERY = [
    ("produce", {"it": "Ortofrutta", "en": "Produce"},
     ["mela", "mele", "banana", "banane", "pomodoro", "pomodori", "insalata", "patate", "carote",
      "cipolla", "cipolle", "limone", "limoni", "arancia", "arance", "zucchine", "melanzane",
      "frutta", "verdura", "spinaci", "broccoli", "avocado", "uva", "pera", "pere", "funghi",
      "apple", "apples", "bananas", "tomato", "tomatoes", "salad", "potato", "potatoes", "carrot",
      "carrots", "onion", "onions", "lemon", "lemons", "orange", "oranges", "courgette",
      "aubergine", "eggplant", "fruit", "vegetables", "spinach", "grapes", "pear", "pears"]),
    ("meat_fish", {"it": "Carne e pesce", "en": "Meat & fish"},
     ["pollo", "manzo", "maiale", "salsiccia", "prosciutto", "salame", "tonno", "salmone", "pesce",
      "gamberi", "carne", "bistecca", "mortadella", "bresaola",
      "chicken", "beef", "pork", "sausage", "ham", "salami", "tuna", "salmon", "fish", "prawns",
      "shrimp", "meat", "steak", "bacon"]),
    ("dairy", {"it": "Latticini e uova", "en": "Dairy & eggs"},
     ["latte", "formaggio", "uova", "uovo", "burro", "yogurt", "ricotta", "mozzarella", "parmigiano",
      "panna", "stracchino",
      "milk", "cheese", "egg", "eggs", "butter", "yoghurt", "cream", "parmesan"]),
    ("bakery", {"it": "Pane e cereali", "en": "Bread & grains"},
     ["pane", "pasta", "riso", "farina", "biscotti", "cracker", "grissini", "cereali", "polenta",
      "orzo", "farro", "pangrattato",
      "bread", "rice", "flour", "biscuits", "cookies", "cereal", "barley", "breadcrumbs"]),
    ("pantry", {"it": "Dispensa", "en": "Pantry"},
     ["olio", "sale", "zucchero", "caffe", "aceto", "passata", "legumi", "fagioli", "ceci",
      "lenticchie", "miele", "marmellata", "maionese", "spezie", "pepe", "sugo", "pelati",
      "cioccolato", "snack", "te", "tonno in scatola",
      "oil", "salt", "sugar", "coffee", "vinegar", "beans", "chickpeas", "lentils", "honey", "jam",
      "mayonnaise", "spices", "pepper", "sauce", "chocolate", "tea", "canned"]),
    ("frozen", {"it": "Surgelati", "en": "Frozen"},
     ["surgelat", "gelato", "piselli", "bastoncini", "frozen", "ice cream", "peas"]),
    ("drinks", {"it": "Bevande", "en": "Drinks"},
     ["acqua", "vino", "birra", "succo", "aranciata", "cola", "spumante", "bibita",
      "water", "wine", "beer", "juice", "soda", "lemonade"]),
    ("household", {"it": "Igiene e casa", "en": "Household & hygiene"},
     ["detersivo", "sapone", "shampoo", "dentifricio", "carta igienica", "spugna", "ammorbidente",
      "candeggina", "sacchi", "scottex", "deodorante", "rasoio", "assorbenti", "crema",
      "spazzolino", "dish soap", "soap", "toothpaste", "toilet paper", "sponge", "bleach",
      "bin bags", "deodorant", "razor", "toothbrush"],
    ),
    ("other", {"it": "Altro", "en": "Other"}, []),
]

GROCERY_LABELS = {key: labels for key, labels, _ in GROCERY}
GROCERY_CATEGORIES = {key: words for key, _, words in GROCERY}


def grocery_label(conn, key: str) -> str:
    labels = GROCERY_LABELS.get(key, {})
    return labels.get(lang(conn)) or labels.get("en") or key

# Elenchi: nessuno pre-creato (le viste integrate sono calcolate). Un promemoria
# puo' anche non appartenere ad alcun elenco.
REM_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_rem_due ON reminders(due_at, completed_at);
CREATE INDEX IF NOT EXISTS idx_rem_list ON reminders(list_id, completed_at);
"""

REM_TRIGGERS_SQL = """
CREATE TRIGGER IF NOT EXISTS rem_ai AFTER INSERT ON reminders BEGIN
  INSERT INTO reminders_fts(rowid, title, notes, tags) VALUES (new.id, new.title, new.notes, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS rem_ad AFTER DELETE ON reminders BEGIN
  INSERT INTO reminders_fts(reminders_fts, rowid, title, notes, tags)
  VALUES ('delete', old.id, old.title, old.notes, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS rem_au AFTER UPDATE ON reminders BEGIN
  INSERT INTO reminders_fts(reminders_fts, rowid, title, notes, tags)
  VALUES ('delete', old.id, old.title, old.notes, old.tags);
  INSERT INTO reminders_fts(rowid, title, notes, tags) VALUES (new.id, new.title, new.notes, new.tags);
END;
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS lists (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  color TEXT, icon TEXT,
  position INTEGER DEFAULT 0,
  muted INTEGER DEFAULT 0,
  pinned INTEGER DEFAULT 0,
  folder TEXT,
  is_smart INTEGER DEFAULT 0,
  smart_rules TEXT,
  smart_match_all INTEGER DEFAULT 1,
  is_grocery INTEGER DEFAULT 0,
  is_default INTEGER DEFAULT 0,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS sections (
  id INTEGER PRIMARY KEY,
  list_id INTEGER NOT NULL REFERENCES lists(id),
  name TEXT NOT NULL,
  position INTEGER DEFAULT 0,
  UNIQUE(list_id, name)
);

CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY,
  list_id INTEGER REFERENCES lists(id),
  section_id INTEGER REFERENCES sections(id),
  title TEXT NOT NULL,
  notes TEXT, url TEXT, attachments TEXT,
  due_at TEXT, due_has_time INTEGER DEFAULT 0,
  priority INTEGER DEFAULT 0,
  flagged INTEGER DEFAULT 0,
  urgent INTEGER DEFAULT 0,
  completed_at TEXT,
  repeat_rule TEXT,
  early_minutes INTEGER,
  location_name TEXT, location_trigger TEXT, location_radius INTEGER,
  parent_id INTEGER REFERENCES reminders(id),
  tags TEXT,
  sort_order INTEGER DEFAULT 0,
  nag_at TEXT,
  notify_count INTEGER DEFAULT 0,
  created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS alarms (
  id INTEGER PRIMARY KEY,
  reminder_id INTEGER NOT NULL REFERENCES reminders(id) ON DELETE CASCADE,
  fire_at TEXT NOT NULL,
  offset_minutes INTEGER,
  kind TEXT DEFAULT 'custom',
  label TEXT,
  sent_at TEXT,
  created_at TEXT,
  UNIQUE(reminder_id, fire_at, kind)
);
CREATE INDEX IF NOT EXISTS idx_alarms_fire ON alarms(fire_at, sent_at);

CREATE TABLE IF NOT EXISTS templates (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  items TEXT,
  created_at TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS reminders_fts USING fts5(
  title, notes, tags, content='reminders', content_rowid='id'
);
""" + REM_INDEXES_SQL + REM_TRIGGERS_SQL

# Nessun elenco viene creato automaticamente. Come in Apple Reminders, le viste
# integrate (Oggi, Programmato, Tutti, Flaggati, Urgenti, Anytime, Completati)
# sono CALCOLATE, non elenchi: gli elenchi li crea l'utente. L'unica eccezione
# e' l'elenco predefinito, creato su richiesta al primo promemoria senza --list.
DEFAULT_LIST_NAMES = {"en": "Reminders", "it": "Promemoria"}


def default_list_name(conn) -> str:
    return DEFAULT_LIST_NAMES.get(lang(conn), DEFAULT_LIST_NAMES["en"])


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def from_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


# ---------------------------------------------------------------------- setup

def connect(path: str = DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    for k, v in DEFAULTS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?,?)", (k, v))
    # Nessun elenco pre-creato: al massimo si marca il primo come predefinito.
    if not get_setting(conn, "default_list_id"):
        row = conn.execute("SELECT id FROM lists ORDER BY position, id LIMIT 1").fetchone()
        if row:
            conn.execute("UPDATE lists SET is_default=1 WHERE id=?", (row["id"],))
            conn.execute("UPDATE settings SET value=? WHERE key='default_list_id'", (str(row["id"]),))
    conn.commit()
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Aggiunge colonne mancanti e converte le ricorrenze della v1 in JSON."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(reminders)")}
    for name, decl in {
        "section_id": "INTEGER", "urgent": "INTEGER DEFAULT 0", "attachments": "TEXT",
        "location_name": "TEXT", "location_trigger": "TEXT", "location_radius": "INTEGER",
        "sort_order": "INTEGER DEFAULT 0", "nag_at": "TEXT", "early_minutes": "INTEGER",
        "notify_count": "INTEGER DEFAULT 0",
    }.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE reminders ADD COLUMN {name} {decl}")
    lcols = {r["name"] for r in conn.execute("PRAGMA table_info(lists)")}
    for name, decl in {"icon": "TEXT", "pinned": "INTEGER DEFAULT 0", "folder": "TEXT",
                       "is_smart": "INTEGER DEFAULT 0", "smart_rules": "TEXT",
                       "smart_match_all": "INTEGER DEFAULT 1", "is_grocery": "INTEGER DEFAULT 0",
                       "is_default": "INTEGER DEFAULT 0"}.items():
        if name not in lcols:
            conn.execute(f"ALTER TABLE lists ADD COLUMN {name} {decl}")
    # Un promemoria puo' non avere elenco: i database creati prima avevano
    # list_id NOT NULL e vanno ricostruiti (SQLite non lo toglie con ALTER).
    info = conn.execute("PRAGMA table_info(reminders)").fetchall()
    lid = next((c for c in info if c["name"] == "list_id"), None)
    if lid is not None and lid["notnull"]:
        _relax_list_id(conn)

    # v1: repeat_rule era 'daily'|'weekly'|… con repeat_every separato.
    # Su un database nuovo queste colonne non esistono: la migrazione gira solo
    # se il database arriva davvero dalla v1.
    if "repeat_every" in cols and "repeat_rule" in cols:
        old = conn.execute("SELECT id, repeat_rule FROM reminders "
                           "WHERE repeat_rule IS NOT NULL AND repeat_rule NOT LIKE '{%'").fetchall()
        for r in old:
            rule = {"freq": r["repeat_rule"], "interval": 1}
            conn.execute("UPDATE reminders SET repeat_rule=? WHERE id=?", (json.dumps(rule), r["id"]))


def _relax_list_id(conn: sqlite3.Connection) -> None:
    """Ricostruisce `reminders` rendendo list_id nullable, preservando i dati."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='reminders'").fetchone()
    if not row or not row["sql"]:
        return
    new_sql = row["sql"].replace("list_id INTEGER NOT NULL REFERENCES lists(id)",
                                 "list_id INTEGER REFERENCES lists(id)")
    if new_sql == row["sql"]:
        return
    cols = ", ".join(c["name"] for c in conn.execute("PRAGMA table_info(reminders)"))
    conn.execute("PRAGMA foreign_keys=OFF")
    # Senza legacy_alter_table SQLite riscrive i riferimenti esterni alla tabella
    # rinominata (le FK di `alarms` finirebbero sul nome temporaneo).
    conn.execute("PRAGMA legacy_alter_table=ON")
    conn.execute("ALTER TABLE reminders RENAME TO reminders_pre_nullable")
    conn.execute(new_sql)
    conn.execute(f"INSERT INTO reminders ({cols}) SELECT {cols} FROM reminders_pre_nullable")
    conn.execute("DROP TABLE reminders_pre_nullable")
    conn.execute("PRAGMA legacy_alter_table=OFF")
    # executescript esegue un commit implicito: senza questo la ricostruzione
    # resterebbe a metà transazione e il DB si corrompe.
    conn.commit()
    conn.executescript(REM_INDEXES_SQL)
    conn.executescript(REM_TRIGGERS_SQL)
    conn.commit()
    # L'indice FTS a contenuto esterno va riallineato alla tabella ricostruita.
    conn.execute("INSERT INTO reminders_fts(reminders_fts) VALUES('rebuild')")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()


def get_setting(conn, key: str, default: str | None = None) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row and row["value"] not in (None, ""):
        return row["value"]
    return default if default is not None else DEFAULTS.get(key, "")


def set_setting(conn, key: str, value: str, commit: bool = True) -> None:
    conn.execute("INSERT INTO settings(key,value) VALUES (?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    if commit:
        conn.commit()


def lang(conn) -> str:
    return get_setting(conn, "language", "en")


def tl(conn, msg: str, **kw) -> str:
    """Translated message for the active locale (falls back to English).

    NOTE: the key parameter is named `msg` on purpose — catalogs contain a
    message that itself takes `{key}`, so `key=` must stay free for callers.
    """
    return i18n.t(lang(conn), msg, **kw)


def weekday_names(conn) -> list[str]:
    return WEEKDAYS_IT if lang(conn) == "it" else WEEKDAYS_EN


def week_bounds(ref: datetime, offset: int = 0) -> tuple[datetime, datetime]:
    """Lunedi 00:00 → lunedi successivo 00:00. La settimana comincia sempre di lunedi."""
    start = (ref - timedelta(days=(ref.weekday() - WEEK_START) % 7)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    start += timedelta(weeks=offset)
    return start, start + timedelta(days=7)


# ---------------------------------------------------------------- date parsing

_OFFSET_RE = re.compile(r"^\+(\d+)([mhdw])$")
_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ t](\d{1,2}):(\d{2}))?$")


def parse_when(text: str, base: datetime | None = None,
               all_day_time: str = "09:00") -> tuple[datetime | None, bool]:
    """(datetime, has_time). Forme: +30m +2h +3d +1w | oggi/domani/dopodomani/ieri |
    lunedi [10:00] | 2026-10-12 [10:30] | 18:30"""
    if not text:
        return None, False
    base = base or now()
    t = text.strip().lower()
    hh, mm = (int(x) for x in all_day_time.split(":"))

    m = _OFFSET_RE.match(t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n),
                 "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]
        return base + delta, unit in ("m", "h")

    day_words = {"oggi": 0, "today": 0, "domani": 1, "tomorrow": 1, "dopodomani": 2,
                 "ieri": -1, "yesterday": -1}
    parts = t.replace(",", " ").split()
    if parts and parts[0] in day_words:
        d = base + timedelta(days=day_words[parts[0]])
        if len(parts) > 1:
            hm = _parse_clock(parts[1])
            if hm:
                return d.replace(hour=hm[0], minute=hm[1], second=0), True
        return d.replace(hour=hh, minute=mm, second=0), False

    if parts and parts[0] in WEEKDAY_ALIAS:
        target = WEEKDAY_ALIAS[parts[0]]
        delta = (target - base.weekday()) % 7 or 7
        d = base + timedelta(days=delta)
        if len(parts) > 1:
            hm = _parse_clock(parts[1])
            if hm:
                return d.replace(hour=hm[0], minute=hm[1], second=0), True
        return d.replace(hour=hh, minute=mm, second=0), False

    m = _ISO_RE.match(t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if m.group(4) is not None:
            return datetime(y, mo, d, int(m.group(4)), int(m.group(5))), True
        return datetime(y, mo, d, hh, mm), False

    hm = _parse_clock(t)
    if hm:
        cand = base.replace(hour=hm[0], minute=hm[1], second=0)
        if cand < base:
            cand += timedelta(days=1)
        return cand, True
    return None, False


def _parse_clock(token: str) -> tuple[int, int] | None:
    m = re.match(r"^(\d{1,2})[:.](\d{2})$", token.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return (h, mi) if 0 <= h <= 23 and 0 <= mi <= 59 else None


def parse_offset(text: str) -> int | None:
    """'30m' '2h' '3d' '1w' '1M' (mesi) → minuti. None se non parsabile."""
    m = re.match(r"^(\d+)\s*([mhdwM])$", text.strip())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return n * {"m": 1, "h": 60, "d": 1440, "w": 10080, "M": 43200}[unit]


def parse_alarm(text: str, due_at: str | None, base: datetime | None = None) -> str | None:
    """'30m' = 30 min prima della scadenza; altrimenti orario/data assoluta."""
    if not text:
        return None
    off = parse_offset(text.lstrip("-"))
    if off is not None and due_at:
        return iso(from_iso(due_at) - timedelta(minutes=off))
    dt, _ = parse_when(text, base)
    return iso(dt) if dt else None


# ------------------------------------------------------------------ ricorrenze

def parse_repeat(spec: str) -> dict | None:
    """Forme compatte:
    hourly|daily|weekly|monthly|yearly  con  :N  per l'intervallo
    weekly:mon,wed   weekly:2:mon     monthly:15    monthly:2:15
    monthly:last:fri  monthly:second:mar   yearly:mar,giu   yearly:2:mar
    """
    if not spec:
        return None
    parts = spec.strip().lower().split(":")
    freq = parts[0]
    if freq not in ("hourly", "daily", "weekly", "monthly", "yearly"):
        return None
    rule: dict = {"freq": freq, "interval": 1}
    rest = [p for p in parts[1:] if p != ""]
    # Il numero iniziale e' l'intervallo, tranne monthly:15 (che e' il giorno del mese)
    if rest and rest[0].isdigit() and not (freq == "monthly" and len(rest) == 1):
        rule["interval"] = max(1, int(rest[0]))
        rest = rest[1:]
    if rest:
        arg = rest[0]
        if freq == "weekly":
            days = sorted({WEEKDAY_ALIAS[p] for p in arg.split(",") if p in WEEKDAY_ALIAS})
            if days:
                rule["byweekday"] = days
        elif freq == "monthly":
            if arg.isdigit():
                rule["bymonthday"] = [int(arg)]
            elif arg in SETPOS and len(rest) > 1:
                wd = WEEKDAY_ALIAS.get(rest[1])
                if wd is not None:
                    rule["bysetpos"] = SETPOS[arg]
                    rule["byweekday"] = [wd]
            elif arg in SETPOS:
                rule["bysetpos"] = SETPOS[arg]
        elif freq == "yearly":
            months = [m for m in (_month_num(p) for p in arg.split(",")) if m]
            rule["bymonth"] = sorted(set(months))
    return rule


def _month_num(token: str) -> int | None:
    t = token.strip().lower()
    if t.isdigit():
        n = int(t)
        return n if 1 <= n <= 12 else None
    names = {"gen": 1, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "may": 5, "giu": 6,
             "jun": 6, "lug": 7, "jul": 7, "ago": 8, "aug": 8, "set": 9, "sep": 9,
             "ott": 10, "oct": 10, "nov": 11, "dic": 12, "dec": 12}
    return names.get(t[:3])


def next_occurrence(rule: dict | None, dt: datetime) -> datetime | None:
    if not rule:
        return None
    freq = rule.get("freq")
    n = max(1, int(rule.get("interval", 1)))
    if freq == "hourly":
        return dt + timedelta(hours=n)
    if freq == "daily":
        return dt + timedelta(days=n)
    if freq == "weekly":
        days = rule.get("byweekday")
        if not days:
            return dt + timedelta(weeks=n)
        cur = dt
        for _ in range(1, 7 * n + 1):
            cur += timedelta(days=1)
            if cur.weekday() in days:
                return cur
        return None
    if freq == "monthly":
        return _next_monthly(rule, dt, n)
    if freq == "yearly":
        months = rule.get("bymonth") or []
        y, mo = dt.year, dt.month
        for _ in range(1, 13 * n + 2):
            mo += 1
            if mo > 12:
                mo, y = 1, y + 1
            if months and mo not in months:
                continue
            day = min(dt.day, _cal.monthrange(y, mo)[1])
            cand = dt.replace(year=y, month=mo, day=day)
            if cand > dt:
                return cand
        return None
    return None


def _next_monthly(rule: dict, dt: datetime, n: int) -> datetime | None:
    pos = rule.get("bysetpos")
    wd = (rule.get("byweekday") or [None])[0]
    day = (rule.get("bymonthday") or [None])[0]

    def nth_weekday(year: int, month: int) -> int | None:
        last = _cal.monthrange(year, month)[1]
        if wd is None:
            return None
        if pos == -1:
            d = datetime(year, month, last)
            while d.weekday() != wd:
                d -= timedelta(days=1)
            return d.day
        first = datetime(year, month, 1)
        daynum = 1 + ((wd - first.weekday()) % 7) + (abs(pos) - 1) * 7
        return daynum if daynum <= last else None

    y, mo = dt.year, dt.month
    for _ in range(1, 27):
        mo += n
        while mo > 12:
            mo -= 12
            y += 1
        if pos is not None:
            target = nth_weekday(y, mo)
        elif day is not None:
            target = day if day <= _cal.monthrange(y, mo)[1] else None
        else:
            target = min(dt.day, _cal.monthrange(y, mo)[1])
        if target is None:
            continue
        cand = dt.replace(year=y, month=mo, day=target)
        if cand > dt:
            return cand
    return None


def advance(conn, rem, from_dt: datetime | None = None) -> str | None:
    """Prossima scadenza di un ricorrente. Se la regola ha from_completion, conta da adesso."""
    rule = json.loads(rem["repeat_rule"]) if rem["repeat_rule"] else None
    if not rule or not rem["due_at"]:
        return None
    base = now() if rule.get("from_completion") else (from_dt or from_iso(rem["due_at"]))
    nxt = next_occurrence(rule, base)
    if nxt is None:
        return None
    end = rule.get("end_date")
    if end:
        limit = from_iso(end if "T" in str(end) else str(end) + "T23:59:59")
        if nxt > limit:
            return None
    return iso(nxt)


# --------------------------------------------------------------------- allarmi

def rebuild_alarms(conn, rem_id: int, commit: bool = True) -> int:
    """Rigenera gli allarmi derivati (early + scadenza); conserva i custom."""
    rem = conn.execute("SELECT * FROM reminders WHERE id=?", (rem_id,)).fetchone()
    if not rem:
        return 0
    conn.execute("DELETE FROM alarms WHERE reminder_id=? AND kind != 'custom'", (rem_id,))
    n = 0
    if rem["due_at"] and not rem["completed_at"]:
        due = from_iso(rem["due_at"])
        em = int(rem["early_minutes"]) if rem["early_minutes"] else 0
        if em:
            conn.execute(
                "INSERT OR IGNORE INTO alarms(reminder_id, fire_at, offset_minutes, kind, created_at) "
                "VALUES (?,?,?,'early',?)",
                (rem_id, iso(due - timedelta(minutes=em)), -em, iso(now())))
            n += 1
        conn.execute(
            "INSERT OR IGNORE INTO alarms(reminder_id, fire_at, offset_minutes, kind, created_at) "
            "VALUES (?,?,0,'due',?)", (rem_id, rem["due_at"], iso(now())))
        n += 1
    n += conn.execute("SELECT COUNT(*) c FROM alarms WHERE reminder_id=? AND kind='custom'",
                      (rem_id,)).fetchone()["c"]
    if commit:
        conn.commit()
    return n


def alarms_of(conn, rem_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM alarms WHERE reminder_id=? ORDER BY fire_at", (rem_id,)).fetchall()


def add_alarm(conn, rem_id: int, when: str, label: str | None = None,
              due_at: str | None = None) -> int | None:
    """Aggiunge un allarme esplicito: offset ('30m') relativo alla scadenza o orario assoluto."""
    off = parse_offset(when.lstrip("-"))
    fire = None
    offset = None
    if off is not None:
        if not due_at:
            return None
        fire = iso(from_iso(due_at) - timedelta(minutes=off))
        offset = -off
    else:
        dt, _ = parse_when(when)
        if dt is None:
            return None
        fire = iso(dt)
    cur = conn.execute(
        "INSERT OR IGNORE INTO alarms(reminder_id, fire_at, offset_minutes, kind, label, created_at) "
        "VALUES (?,?,?,'custom',?,?)", (rem_id, fire, offset, label, iso(now())))
    conn.commit()
    return cur.lastrowid


# --------------------------------------------------------------- formattazione

def fmt_dt(conn, dt: datetime, with_time: bool = True) -> str:
    if not with_time:
        return dt.strftime("%d/%m/%Y" if lang(conn) == "it" else "%Y-%m-%d")
    if get_setting(conn, "time_format", "24") == "12":
        return dt.strftime("%d/%m/%Y %I:%M %p")
    return dt.strftime("%d/%m/%Y %H:%M")


def relative_day(conn, dt: datetime, ref: datetime | None = None) -> str:
    ref = ref or now()
    delta = (dt.date() - ref.date()).days
    if delta == 0:
        return tl(conn, "day_today")
    if delta == 1:
        return tl(conn, "day_tomorrow")
    if delta == -1:
        return tl(conn, "day_yesterday")
    if 1 < delta < 7:
        return weekday_names(conn)[dt.weekday()]
    return dt.strftime("%d/%m/%Y")


MONTHS = {
    "en": ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
    "it": ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"],
}


def describe_repeat(conn, rule: dict | None) -> str:
    if not rule:
        return ""
    f, n = rule.get("freq"), max(1, int(rule.get("interval", 1)))
    days = rule.get("byweekday") or []
    dn = weekday_names(conn)
    if f == "hourly":
        return tl(conn, "rep_hourly", n=n) if n > 1 else tl(conn, "rep_hourly_one")
    if f == "daily":
        return tl(conn, "rep_daily", n=n) if n > 1 else tl(conn, "rep_daily_one")
    if f == "weekly":
        base = tl(conn, "rep_weekly", n=n) if n > 1 else tl(conn, "rep_weekly_one")
        if not days:
            return base
        return base + tl(conn, "rep_on_weekdays", days=", ".join(dn[d] for d in days))
    if f == "monthly":
        base = tl(conn, "rep_monthly", n=n) if n > 1 else tl(conn, "rep_monthly_one")
        pos = rule.get("bysetpos")
        if pos is not None and days:
            key = {1: "pos_first", 2: "pos_second", 3: "pos_third", 4: "pos_fourth", -1: "pos_last"}.get(
                pos, "pos_first")
            return base + tl(conn, "rep_on_nth", pos=tl(conn, key), weekday=dn[days[0]])
        if rule.get("bymonthday"):
            return base + tl(conn, "rep_on_day", day=rule["bymonthday"][0])
        return base
    if f == "yearly":
        base = tl(conn, "rep_yearly", n=n) if n > 1 else tl(conn, "rep_yearly_one")
        months = rule.get("bymonth") or []
        if not months:
            return base
        mn = MONTHS.get(lang(conn), MONTHS["en"])
        return base + tl(conn, "rep_in_months", months=", ".join(mn[m - 1] for m in months))
    return ""


def human(conn, rem, ref: datetime | None = None) -> str:
    """Riga in stile Apple Reminders."""
    ref = ref or now()
    mark = "x" if rem["completed_at"] else "·"
    pri = {1: " !!!", 5: " !!", 9: " !"}.get(rem["priority"] or 0, "")
    flag = " ⚑" if rem["flagged"] else ""
    urg = " " + tl(conn, "flag_urgent") if rem["urgent"] else ""
    when = ""
    if rem["due_at"]:
        d = from_iso(rem["due_at"])
        clock = ""
        if rem["due_has_time"]:
            clock = " " + (d.strftime("%I:%M %p") if get_setting(conn, "time_format", "24") == "12"
                           else d.strftime("%H:%M"))
        when = f" [{relative_day(conn, d, ref)}{clock}]"
    late = ""
    if rem["due_at"] and not rem["completed_at"] and from_iso(rem["due_at"]) < ref:
        if rem["due_has_time"] or get_setting(conn, "show_all_day_overdue", "0") == "1":
            late = " " + tl(conn, "flag_overdue")
    rep = ""
    if rem["repeat_rule"]:
        desc = describe_repeat(conn, json.loads(rem["repeat_rule"]))
        rep = f" ({desc})" if desc else ""
    loc = f" @{rem['location_name']}" if rem["location_name"] else ""
    tags = rem["tags"] if "tags" in rem.keys() else None
    tg = " " + " ".join(f"#{t.strip()}" for t in tags.split(",") if t.strip()) if tags else ""
    return f"{mark} #{rem['id']} {rem['title']}{pri}{flag}{urg}{when}{late}{rep}{loc}{tg}"


def tags_of(rem) -> list[str]:
    if not rem["tags"]:
        return []
    return [t.strip().lstrip("#") for t in rem["tags"].split(",") if t.strip()]


def grocery_category(title: str) -> str:
    """Chiave della categoria per un articolo (fallback: 'other')."""
    t = title.lower()
    for key, words in GROCERY_CATEGORIES.items():
        for w in words:
            if w in t:
                return key
    return "other"


def smart_ids(conn, include_completed: bool = False) -> dict[int, set[int]]:
    """{list_id: set(reminder_id)} per ogni smart list salvata."""
    out: dict[int, set[int]] = {}
    lists = conn.execute("SELECT id, smart_rules, smart_match_all FROM lists WHERE is_smart=1").fetchall()
    for lst in lists:
        try:
            rules = json.loads(lst["smart_rules"] or "{}")
        except Exception:
            continue
        conds, params = [], []
        tags = rules.get("tags") or []
        for t in tags:
            conds.append("r.tags LIKE ?")
            params.append(f"%{t}%")
        if rules.get("flagged"):
            conds.append("r.flagged=1")
        if rules.get("urgent"):
            conds.append("r.urgent=1")
        if rules.get("priority"):
            conds.append("r.priority=?")
            params.append(PRIORITY.get(rules["priority"], 0))
        if rules.get("list_id"):
            conds.append("r.list_id=?")
            params.append(int(rules["list_id"]))
        if rules.get("due_within_days") is not None:
            conds.append("r.due_at IS NOT NULL AND r.due_at <= ?")
            params.append(iso(now() + timedelta(days=int(rules["due_within_days"]))))
        if rules.get("has_date"):
            conds.append("r.due_at IS NOT NULL")
        if rules.get("no_date"):
            conds.append("r.due_at IS NULL")
        if not include_completed:
            conds.append("r.completed_at IS NULL")
        if not conds:
            out[lst["id"]] = set()
            continue
        joiner = " AND " if lst["smart_match_all"] else " OR "
        sql = f"SELECT r.id FROM reminders r WHERE {joiner.join(conds)}"
        out[lst["id"]] = {r["id"] for r in conn.execute(sql, params)}
    return out
