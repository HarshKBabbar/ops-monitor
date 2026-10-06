"""SQLite storage, NOTAM lifecycle (active -> archived -> purged) and status colours."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .config import CFG, DATA
from .notam_parser import Notam, compute_tags, critical_tags

DB_PATH = DATA / "ops_monitor.db"
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS notams (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    key           TEXT UNIQUE NOT NULL,         -- FIR|A1234/26
    notam_id      TEXT NOT NULL,
    fir           TEXT, series TEXT, number INTEGER, year INTEGER,
    notam_type    TEXT, ref_id TEXT,
    location      TEXT, airport_name TEXT,
    valid_from    TEXT, valid_to TEXT, valid_to_text TEXT,
    schedule      TEXT, text TEXT, lower_lim TEXT, upper_lim TEXT, q_code TEXT, raw TEXT,
    tags          TEXT DEFAULT '[]',
    critical      INTEGER DEFAULT 0,
    sources       TEXT DEFAULT '',
    first_seen    TEXT, last_seen TEXT,
    action_needed TEXT,                         -- NULL = not reviewed, 'YES', 'NO', 'EXT' (extending)
    action_taken  TEXT DEFAULT '',
    prev_notam    TEXT DEFAULT '',              -- for 'EXT': the NOTAM being extended
    action_by     TEXT, action_at TEXT,
    state         TEXT DEFAULT 'ACTIVE',        -- ACTIVE / ARCHIVED
    archive_reason TEXT, archived_at TEXT,
    deleted       INTEGER DEFAULT 0, deleted_by TEXT, deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_state ON notams(state, deleted);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notam_key TEXT, at TEXT, by TEXT, what TEXT
);
CREATE TABLE IF NOT EXISTS source_files (
    url TEXT PRIMARY KEY, sha TEXT, processed_at TEXT, notams INTEGER
);
CREATE TABLE IF NOT EXISTS source_status (
    name TEXT PRIMARY KEY, last_run TEXT, ok INTEGER, message TEXT
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS airports (
    icao TEXT PRIMARY KEY, name TEXT DEFAULT '', added_by TEXT, added_at TEXT
);
"""


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


@contextmanager
def conn():
    with _lock:
        c = sqlite3.connect(DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()


def init():
    DATA.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(notams)")}
        if "prev_notam" not in cols:                       # database from the first draft
            c.execute("ALTER TABLE notams ADD COLUMN prev_notam TEXT DEFAULT ''")
        # re-apply the current tag / performance-critical rules to everything stored
        for r in c.execute("SELECT id, schedule, text, q_code FROM notams").fetchall():
            tags = compute_tags(r["schedule"] or "", r["text"] or "", r["q_code"] or "")
            c.execute("UPDATE notams SET tags=?, critical=? WHERE id=?",
                      (json.dumps(tags), int(bool(critical_tags(tags))), r["id"]))
        bump(c)


def bump(c):
    """Increase change counter - browsers poll it to refresh live."""
    c.execute("INSERT INTO meta(k,v) VALUES('version','1') "
              "ON CONFLICT(k) DO UPDATE SET v = CAST(v AS INTEGER) + 1")


def version() -> int:
    with conn() as c:
        r = c.execute("SELECT v FROM meta WHERE k='version'").fetchone()
        return int(r["v"]) if r else 0


def log(c, key, by, what):
    c.execute("INSERT INTO history(notam_key, at, by, what) VALUES(?,?,?,?)", (key, iso(now()), by, what))


# ------------------------------------------------------------------ ingest
def ingest(notams: list[Notam], source: str) -> list[dict]:
    """Insert / update NOTAMs. Returns the newly added (active) ones."""
    t = iso(now())
    added: list[dict] = []
    with conn() as c:
        changed = False
        for n in notams:
            # replacements / cancellations act on the referenced NOTAM
            if n.notam_type in ("R", "C") and n.ref_id:
                ref_key = f"{n.fir or '----'}|{n.ref_id}"
                reason = "REPLACED" if n.notam_type == "R" else "CANCELLED"
                cur = c.execute("UPDATE notams SET state='ARCHIVED', archive_reason=?, archived_at=? "
                                "WHERE key=? AND state='ACTIVE'", (f"{reason} by {n.notam_id}", t, ref_key))
                if cur.rowcount:
                    log(c, ref_key, "system", f"{reason} by {n.notam_id} ({source})")
                    changed = True
            row =c.execute("SELECT id, sources, deleted FROM notams WHERE key=?", (n.key,)).fetchone()
            if row:
                srcs = set(filter(None, row["sources"].split(",")))
                if source not in srcs:
                    srcs.add(source)
                    c.execute("UPDATE notams SET sources=?, last_seen=? WHERE id=?", (",".join(sorted(srcs)), t, row["id"]))
                    changed = True
                else:
                    c.execute("UPDATE notams SET last_seen=? WHERE id=?", (t, row["id"]))
                continue

            state, reason, arch_at = "ACTIVE", None, None
            if n.notam_type == "C":
                state, reason, arch_at = "ARCHIVED", f"CANCEL NOTICE for {n.ref_id}", t
            elif n.valid_to and n.valid_to < t:
                state, reason, arch_at = "ARCHIVED", "EXPIRED", t
            critical = int(bool(critical_tags(n.tags)))
            c.execute(
                """INSERT INTO notams(key, notam_id, fir, series, number, year, notam_type, ref_id, location,
                   airport_name, valid_from, valid_to, valid_to_text, schedule, text, lower_lim, upper_lim, q_code,
                   raw, tags, critical, sources, first_seen, last_seen, state, archive_reason, archived_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (n.key, n.notam_id, n.fir, n.series, n.number, n.year, n.notam_type, n.ref_id, n.location,
                 n.airport_name, n.valid_from, n.valid_to, n.valid_to_text, n.schedule, n.text, n.lower, n.upper,
                 n.q_code, n.raw, json.dumps(n.tags), critical, source, t, t, state, reason, arch_at))
            changed = True
            if state == "ACTIVE":
                added.append({"notam_id": n.notam_id, "location": n.location, "airport": n.airport_name,
                              "text": n.text, "tags": n.tags, "critical": critical,
                              "valid_from": n.valid_from, "valid_to": n.valid_to or n.valid_to_text})
        if changed:
            bump(c)
    return added


def reconcile_aai_summary(fir: str, series: str, checklist: dict[int, set[int]], summary_date: datetime) -> int:
    """AAI summary = every NOTAM still valid on its date. Active NOTAMs of that FIR/series that are
    older than the summary but missing from its checklist have been cancelled / replaced."""
    if not checklist:
        return 0
    t = iso(now())
    moved = 0
    with conn() as c:
        rows = c.execute("SELECT id, key, year, number, valid_from FROM notams WHERE state='ACTIVE' AND fir=? AND series=?",
                         (fir, series)).fetchall()
        for r in rows:
            nums = checklist.get(r["year"])
            if nums is None or r["number"] in nums or r["number"] > max(nums):
                continue
            if r["valid_from"] and r["valid_from"] >= iso(summary_date):
                continue
            c.execute("UPDATE notams SET state='ARCHIVED', archive_reason=?, archived_at=? WHERE id=?",
                      (f"CANCELLED (not in AAI summary {summary_date:%b %Y})", t, r["id"]))
            log(c, r["key"], "system", "Not in latest AAI summary - moved to archive")
            moved += 1
        if moved:
            bump(c)
    return moved


def housekeep() -> None:
    """Expired -> archive; archive / deleted rows older than retention -> removed for good."""
    t = now()
    keep = timedelta(days=int(CFG["notam"].get("archive_retention_days", 365)))
    with conn() as c:
        a = c.execute("UPDATE notams SET state='ARCHIVED', archive_reason='EXPIRED', archived_at=? "
                      "WHERE state='ACTIVE' AND valid_to IS NOT NULL AND valid_to < ?", (iso(t), iso(t))).rowcount
        cutoff = iso(t - keep)
        b = c.execute("DELETE FROM notams WHERE (state='ARCHIVED' AND archived_at < ?) OR (deleted=1 AND deleted_at < ?)",
                      (cutoff, cutoff)).rowcount
        c.execute("DELETE FROM history WHERE at < ?", (cutoff,))
        if a or b:
            bump(c)


# ------------------------------------------------------------------ read / update
def status_of(r: dict, t: datetime) -> str:
    if r["state"] == "ARCHIVED":
        return "archived"
    if r["valid_to"]:
        exp = datetime.fromisoformat(r["valid_to"])
        if t <= exp <= t + timedelta(days=float(CFG["notam"].get("expiring_days", 7))):
            return "expiring"
    need = r["action_needed"]
    if (need == "NO" or (need == "YES" and (r["action_taken"] or "").strip())
            or (need == "EXT" and (r["prev_notam"] or "").strip())):
        return "done"
    if need in ("YES", "EXT"):
        return "pending"
    return "new"


def _decorate(d: dict, t: datetime) -> dict:
    d["tags"] = json.loads(d["tags"] or "[]")
    d["crit_tags"] = critical_tags(d["tags"])
    d["status"] = status_of(d, t)
    return d


def set_baseline() -> None:
    """Called after the very first import: those NOTAMs already existed, so they don't get a NEW badge."""
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('baseline', ?)", (iso(now()),))
        bump(c)


def list_notams(state: str) -> list[dict]:
    """NOTAMs of the given state, limited to the monitored airports (all, if none are set)."""
    t = now()
    new_h = float(CFG["notam"].get("new_badge_hours", 24))
    order = "first_seen DESC, valid_from DESC" if state == "ACTIVE" else "archived_at DESC"
    with conn() as c:
        only = " AND location IN (SELECT icao FROM airports)" if c.execute("SELECT 1 FROM airports LIMIT 1").fetchone() else ""
        rows = c.execute(f"SELECT * FROM notams WHERE state=? AND deleted=0{only} ORDER BY {order}", (state,)).fetchall()
        b = c.execute("SELECT v FROM meta WHERE k='baseline'").fetchone()
    baseline = b["v"] if b else ""
    out = []
    for r in rows:
        d = _decorate(dict(r), t)
        d.pop("raw", None)
        d["is_new"] = bool(d["first_seen"] and d["first_seen"] > baseline
                           and (t - datetime.fromisoformat(d["first_seen"])) < timedelta(hours=new_h))
        out.append(d)
    return out


def get(nid: int) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM notams WHERE id=?", (nid,)).fetchone()
        if not r:
            return None
        d = _decorate(dict(r), now())
        d["history"] = [dict(h) for h in c.execute(
            "SELECT at, by, what FROM history WHERE notam_key=? ORDER BY id DESC", (d["key"],))]
        return d


NEED_LABEL = {"YES": "Yes", "NO": "No action needed", "EXT": "Extending"}


def set_action(nid: int, action_needed: str | None, action_taken: str | None, user: str,
               prev_notam: str | None = None) -> bool:
    with conn() as c:
        r = c.execute("SELECT key, action_needed, action_taken, prev_notam FROM notams WHERE id=?", (nid,)).fetchone()
        if not r:
            return False
        an = r["action_needed"] if action_needed is None else (action_needed or None)
        at = (r["action_taken"] or "") if action_taken is None else action_taken.strip()
        pn = (r["prev_notam"] or "") if prev_notam is None else prev_notam.strip().upper()
        c.execute("UPDATE notams SET action_needed=?, action_taken=?, prev_notam=?, action_by=?, action_at=? WHERE id=?",
                  (an, at, pn, user, iso(now()), nid))
        parts = []
        if an != r["action_needed"]:
            parts.append(f"Action needed: {NEED_LABEL.get(an, '-')}")
        if pn != (r["prev_notam"] or ""):
            parts.append(f"Previous NOTAM: {pn or '-'}")
        if at != (r["action_taken"] or ""):
            parts.append(f"Action taken: {at or '-'}")
        if parts:
            log(c, r["key"], user, "; ".join(parts))
        bump(c)
        return True


def delete(nid: int, user: str) -> bool:
    with conn() as c:
        r = c.execute("SELECT key FROM notams WHERE id=?", (nid,)).fetchone()
        if not r:
            return False
        c.execute("UPDATE notams SET deleted=1, deleted_by=?, deleted_at=? WHERE id=?", (user, iso(now()), nid))
        log(c, r["key"], user, "Deleted from sheet")
        bump(c)
        return True


def set_source_status(name: str, ok: bool, message: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO source_status(name, last_run, ok, message) VALUES(?,?,?,?) "
                  "ON CONFLICT(name) DO UPDATE SET last_run=excluded.last_run, ok=excluded.ok, message=excluded.message",
                  (name, iso(now()), int(ok), message))
        bump(c)


def source_statuses() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM source_status ORDER BY name")]


def file_seen(url: str, sha: str) -> bool:
    with conn() as c:
        r = c.execute("SELECT sha FROM source_files WHERE url=?", (url,)).fetchone()
        return bool(r and r["sha"] == sha)


def mark_file(url: str, sha: str, count: int) -> None:
    with conn() as c:
        c.execute("INSERT INTO source_files(url, sha, processed_at, notams) VALUES(?,?,?,?) "
                  "ON CONFLICT(url) DO UPDATE SET sha=excluded.sha, processed_at=excluded.processed_at, notams=excluded.notams",
                  (url, sha, iso(now()), count))


# ------------------------------------------------------------------ monitored airports
def list_airports() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute(
            """SELECT a.icao, a.name, a.added_by, a.added_at,
                      (SELECT COUNT(*) FROM notams n WHERE n.location=a.icao AND n.state='ACTIVE' AND n.deleted=0) AS active
               FROM airports a ORDER BY a.icao""")]


def add_airports(items: list[dict], user: str) -> int:
    added = 0
    with conn() as c:
        for it in items:
            icao = (it.get("icao") or "").strip().upper()
            if len(icao) != 4 or not icao.isalpha():
                continue
            cur = c.execute("INSERT OR IGNORE INTO airports(icao, name, added_by, added_at) VALUES(?,?,?,?)",
                            (icao, (it.get("name") or "").strip()[:80], user, iso(now())))
            added += cur.rowcount
        if added:
            bump(c)
    return added


def remove_airport(icao: str) -> bool:
    with conn() as c:
        n = c.execute("DELETE FROM airports WHERE icao=?", (icao.upper(),)).rowcount
        if n:
            bump(c)
        return bool(n)


def clear_airports() -> None:
    with conn() as c:
        c.execute("DELETE FROM airports")
        bump(c)


def airport_set() -> set[str]:
    with conn() as c:
        return {r["icao"] for r in c.execute("SELECT icao FROM airports")}


def known_locations() -> dict[str, str]:
    """Locations seen in NOTAMs -> aerodrome name (helps when detecting airports from Excel)."""
    with conn() as c:
        return {r["location"]: r["airport_name"] or "" for r in c.execute(
            "SELECT location, MAX(airport_name) AS airport_name FROM notams WHERE location != '' GROUP BY location")}


def is_empty() -> bool:
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM notams").fetchone()[0] == 0
