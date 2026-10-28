"""SQLite storage. Every write is an upsert so re-running any step is harmless."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .util import et_day_bounds, iso, now_utc

SCHEMA = """
CREATE TABLE IF NOT EXISTS properties (
  id INTEGER PRIMARY KEY,
  address_key TEXT UNIQUE NOT NULL,
  street TEXT, city TEXT, state TEXT, zip TEXT, county TEXT,
  lat REAL, lng REAL,
  list_price INTEGER, original_list_price INTEGER, price_history TEXT, price_cuts INTEGER DEFAULT 0,
  days_on_market INTEGER, beds REAL, baths REAL, sqft INTEGER, lot_sqft INTEGER, year_built INTEGER,
  property_type TEXT, units INTEGER, remarks TEXT, photos_count INTEGER,
  listing_url TEXT, source TEXT, source_id TEXT, listing_status TEXT, listed_at TEXT, last_price_change_at TEXT,
  estimate INTEGER, hoa INTEGER, status_flags TEXT, back_on_market INTEGER DEFAULT 0, vacant INTEGER DEFAULT 0,
  agent_id INTEGER REFERENCES agents(id),
  lead_status TEXT DEFAULT 'NONE', lead_status_at TEXT, lead_note TEXT,
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, raw TEXT
);
CREATE INDEX IF NOT EXISTS idx_properties_county ON properties(county);
CREATE INDEX IF NOT EXISTS idx_properties_lead ON properties(lead_status);

CREATE TABLE IF NOT EXISTS agents (
  id INTEGER PRIMARY KEY,
  name TEXT, name_key TEXT NOT NULL, brokerage TEXT, brokerage_key TEXT NOT NULL,
  phone TEXT, phone_raw TEXT, is_office_line INTEGER DEFAULT 0,
  email TEXT, email_confidence TEXT, email_source TEXT, enriched_at TEXT, enrich_attempts INTEGER DEFAULT 0,
  portal_urls TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(name_key, brokerage_key)
);
CREATE INDEX IF NOT EXISTS idx_agents_phone ON agents(phone);

CREATE TABLE IF NOT EXISTS scores (
  property_id INTEGER PRIMARY KEY REFERENCES properties(id),
  score INTEGER NOT NULL, cited TEXT, signals TEXT, negatives TEXT, summary TEXT,
  method TEXT, model TEXT, remarks_hash TEXT, scored_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS valuations (
  property_id INTEGER PRIMARY KEY REFERENCES properties(id),
  arv INTEGER, arv_method TEXT, comp_count INTEGER, ppsf REAL,
  repair_tier TEXT, repair_cost INTEGER, mao INTEGER, list_price INTEGER, spread_pct REAL,
  offer_price INTEGER, rating TEXT, comps TEXT, valued_at TEXT NOT NULL, lane TEXT, comp_grade TEXT, comp_notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_valuations_rating ON valuations(rating);

-- Comp quality loop (titan/comps_loop.py, 2026-10-28): daily back-test of the comp rules on real sales, what it changed,
-- and Claude's review of the comp set behind the houses Rodrigo is working.
CREATE TABLE IF NOT EXISTS comps_calibration (
  id INTEGER PRIMARY KEY, run_date TEXT NOT NULL, scope TEXT NOT NULL, params TEXT NOT NULL, n_tests INTEGER,
  mdape REAL, mape REAL, within_10_pct REAL, baseline_mdape REAL, adopted INTEGER DEFAULT 0, note TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS comps_changes (
  id INTEGER PRIMARY KEY, run_date TEXT NOT NULL, property_id INTEGER NOT NULL, old_arv INTEGER, new_arv INTEGER,
  old_grade TEXT, new_grade TEXT, why TEXT, posted INTEGER DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS comps_review (
  id INTEGER PRIMARY KEY, property_id INTEGER NOT NULL, run_date TEXT NOT NULL, exclude_keys TEXT, subject_flags TEXT,
  arv_low INTEGER, arv_high INTEGER, confidence TEXT, note TEXT, model TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comps_review_pid ON comps_review(property_id, id);

CREATE TABLE IF NOT EXISTS outreach (
  id INTEGER PRIMARY KEY,
  agent_id INTEGER REFERENCES agents(id), property_ids TEXT NOT NULL,
  channel TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'first_touch',
  status TEXT NOT NULL, subject TEXT, body TEXT, variant TEXT,
  to_address TEXT, gmail_draft_id TEXT, gmail_thread_id TEXT, gmail_message_id TEXT, twilio_sid TEXT,
  error TEXT, run_date TEXT NOT NULL, created_at TEXT NOT NULL, sent_at TEXT, asana_task_id TEXT, analysis TEXT
);
CREATE INDEX IF NOT EXISTS idx_outreach_agent ON outreach(agent_id, created_at);
CREATE INDEX IF NOT EXISTS idx_outreach_run ON outreach(run_date, channel, status);

CREATE TABLE IF NOT EXISTS threads (
  id INTEGER PRIMARY KEY,
  agent_id INTEGER REFERENCES agents(id), channel TEXT NOT NULL, external_id TEXT,
  property_ids TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'OPEN',
  last_inbound_at TEXT, last_outbound_at TEXT, unanswered_count INTEGER DEFAULT 0,
  last_class TEXT, bridge_sent INTEGER DEFAULT 0, awaiting_email INTEGER DEFAULT 0, awaiting_email_since TEXT,
  linked_thread_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(channel, external_id)
);
CREATE INDEX IF NOT EXISTS idx_threads_state ON threads(state);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  thread_id INTEGER NOT NULL REFERENCES threads(id), direction TEXT NOT NULL,
  body TEXT, external_id TEXT UNIQUE, classification TEXT, confidence REAL, action TEXT,
  created_at TEXT NOT NULL, mirrored_at TEXT
);

CREATE TABLE IF NOT EXISTS do_not_contact (
  id INTEGER PRIMARY KEY,
  agent_id INTEGER, phone TEXT, email TEXT, reason TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dnc_phone ON do_not_contact(phone);
CREATE INDEX IF NOT EXISTS idx_dnc_email ON do_not_contact(email);

CREATE TABLE IF NOT EXISTS daily_runs (
  id INTEGER PRIMARY KEY,
  run_date TEXT NOT NULL, step TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
  status TEXT NOT NULL, counts TEXT, notes TEXT
);

CREATE TABLE IF NOT EXISTS llm_usage (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL, model TEXT, purpose TEXT, input_tokens INTEGER, output_tokens INTEGER,
  cost_usd REAL, ok INTEGER
);

CREATE TABLE IF NOT EXISTS call_bookings (key TEXT PRIMARY KEY, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS comps_cache (
  key TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, comps TEXT NOT NULL
);

-- The trust ladder (titan/learning.py): Rodrigo's decisions on cards, his comments as lessons, his numbers vs our range.
CREATE TABLE IF NOT EXISTS card_decisions (
  id INTEGER PRIMARY KEY, card_gid TEXT UNIQUE, outreach_id INTEGER, kind TEXT NOT NULL,
  decision TEXT NOT NULL, rounds INTEGER DEFAULT 0, hand_edited INTEGER DEFAULT 0,
  feedback TEXT, draft_body TEXT, final_body TEXT, decided_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lessons (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, outreach_id INTEGER, card_gid TEXT, text TEXT NOT NULL,
  before_body TEXT, after_body TEXT, created_at TEXT NOT NULL, UNIQUE(card_gid, text)
);
-- Offer log (titan/offers.py): every written offer that went out, and what happened to it.
CREATE TABLE IF NOT EXISTS offers (
  id INTEGER PRIMARY KEY, property_id INTEGER, agent_id INTEGER, outreach_id INTEGER UNIQUE, price INTEGER NOT NULL,
  list_price INTEGER, street TEXT, city TEXT, zip TEXT, county TEXT, internal_low INTEGER, internal_high INTEGER,
  status TEXT NOT NULL DEFAULT 'SENT', note TEXT, sent_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
-- What Rodrigo told us about a lead in a card comment (titan/card_chat.py); goes into every later prompt for that agent.
CREATE TABLE IF NOT EXISTS lead_notes (
  id INTEGER PRIMARY KEY, agent_id INTEGER, outreach_id INTEGER, text TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(outreach_id, text)
);
CREATE TABLE IF NOT EXISTS price_decisions (
  id INTEGER PRIMARY KEY, property_id INTEGER, price INTEGER, internal_low INTEGER, internal_high INTEGER, decided_at TEXT NOT NULL
);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True) if str(path) != ":memory:" else None
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL") if str(path) != ":memory:" else None
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


MIGRATIONS = ["ALTER TABLE valuations ADD COLUMN lane TEXT", "ALTER TABLE outreach ADD COLUMN asana_task_id TEXT", "ALTER TABLE outreach ADD COLUMN analysis TEXT",
              "ALTER TABLE messages ADD COLUMN mirrored_at TEXT", "ALTER TABLE outreach ADD COLUMN draft_body TEXT",
              "ALTER TABLE messages ADD COLUMN reply_card TEXT", "ALTER TABLE messages ADD COLUMN reply_draft TEXT",
              "ALTER TABLE valuations ADD COLUMN comp_grade TEXT", "ALTER TABLE valuations ADD COLUMN comp_notes TEXT"]   # databases created before the column existed


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for stmt in MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass   # already applied


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def j(v: Any) -> str | None:
    return None if v is None else json.dumps(v, ensure_ascii=False, default=str)


def uj(s: str | None, default: Any = None) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return default


def row_to_dict(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return None if r is None else {k: r[k] for k in r.keys()}


# --- agents -------------------------------------------------------------------

def _key(s: str | None) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip() or "unknown"


def upsert_agent(conn: sqlite3.Connection, name: str | None, brokerage: str | None, phone_raw: str | None,
                 phone: str | None, email: str | None = None, portal_urls: dict | None = None) -> int:
    now = iso()
    nk, bk = _key(name), _key(brokerage)
    row = conn.execute("SELECT id, email, phone FROM agents WHERE name_key=? AND brokerage_key=?", (nk, bk)).fetchone()
    if row:
        sets, params = ["updated_at=?"], [now]
        if name: sets.append("name=?"); params.append(name)
        if brokerage: sets.append("brokerage=?"); params.append(brokerage)
        if phone and not row["phone"]: sets.append("phone=?"); params.append(phone)
        if phone_raw: sets.append("phone_raw=?"); params.append(phone_raw)
        if email and not row["email"]:
            sets += ["email=?", "email_confidence=?", "email_source=?"]; params += [email.lower(), "high", "listing_payload"]
        if portal_urls: sets.append("portal_urls=?"); params.append(j(portal_urls))
        params.append(row["id"])
        conn.execute(f"UPDATE agents SET {', '.join(sets)} WHERE id=?", params)
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO agents(name,name_key,brokerage,brokerage_key,phone,phone_raw,email,email_confidence,email_source,"
        "portal_urls,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, nk, brokerage, bk, phone, phone_raw, email.lower() if email else None,
         "high" if email else None, "listing_payload" if email else None, j(portal_urls), now, now))
    return int(cur.lastrowid)


def flag_office_lines(conn: sqlite3.Connection, min_agents: int) -> int:
    """Same phone shared by >= min_agents agents at one brokerage → office line."""
    rows = conn.execute(
        "SELECT phone, brokerage_key, COUNT(*) c FROM agents WHERE phone IS NOT NULL "
        "GROUP BY phone, brokerage_key HAVING c >= ?", (min_agents,)).fetchall()
    n = 0
    for r in rows:
        cur = conn.execute("UPDATE agents SET is_office_line=1 WHERE phone=? AND brokerage_key=? AND is_office_line=0",
                           (r["phone"], r["brokerage_key"]))
        n += cur.rowcount
    return n


# --- properties ---------------------------------------------------------------

PROPERTY_FIELDS = [
    "street", "city", "state", "zip", "county", "lat", "lng", "list_price", "original_list_price", "price_history",
    "price_cuts", "days_on_market", "beds", "baths", "sqft", "lot_sqft", "year_built", "property_type", "units",
    "remarks", "photos_count", "listing_url", "source", "source_id", "listing_status", "listed_at",
    "last_price_change_at", "estimate", "hoa", "status_flags", "back_on_market", "vacant", "agent_id", "raw",
]


def upsert_property(conn: sqlite3.Connection, address_key: str, fields: dict[str, Any]) -> tuple[int, bool]:
    """Insert or update by address_key. Returns (id, created)."""
    now = iso()
    data = {k: fields.get(k) for k in PROPERTY_FIELDS}
    for k in ("price_history", "status_flags", "raw"):
        if data[k] is not None and not isinstance(data[k], str):
            data[k] = j(data[k])
    row = conn.execute("SELECT id, first_seen, list_price FROM properties WHERE address_key=?", (address_key,)).fetchone()
    if row:
        sets = [f"{k}=?" for k, v in data.items() if v is not None]
        params = [v for v in data.values() if v is not None]
        sets.append("last_seen=?"); params.append(now)
        params.append(row["id"])
        conn.execute(f"UPDATE properties SET {', '.join(sets)} WHERE id=?", params)
        return int(row["id"]), False
    cols = ["address_key", *data.keys(), "first_seen", "last_seen"]
    vals = [address_key, *data.values(), now, now]
    cur = conn.execute(f"INSERT INTO properties({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)
    return int(cur.lastrowid), True


def set_lead_status(conn: sqlite3.Connection, property_id: int, status: str, note: str | None = None) -> None:
    conn.execute("UPDATE properties SET lead_status=?, lead_status_at=?, lead_note=COALESCE(?, lead_note) WHERE id=?",
                 (status, iso(), note, property_id))


# --- do not contact ---------------------------------------------------------------

def add_dnc(conn: sqlite3.Connection, agent_id: int | None, phone: str | None, email: str | None, reason: str) -> None:
    conn.execute("INSERT INTO do_not_contact(agent_id,phone,email,reason,created_at) VALUES(?,?,?,?,?)",
                 (agent_id, phone, (email or "").lower() or None, reason, iso()))


def is_dnc(conn: sqlite3.Connection, agent_id: int | None = None, phone: str | None = None,
           email: str | None = None) -> bool:
    q = ["(agent_id IS NOT NULL AND agent_id=?)", "(phone IS NOT NULL AND phone=?)", "(email IS NOT NULL AND email=?)"]
    row = conn.execute(f"SELECT 1 FROM do_not_contact WHERE {' OR '.join(q)} LIMIT 1",
                       (agent_id, phone, (email or "").lower() or None)).fetchone()
    return row is not None


# --- runs + usage -----------------------------------------------------------------

def start_run(conn: sqlite3.Connection, step: str, run_date: str) -> int:
    cur = conn.execute("INSERT INTO daily_runs(run_date,step,started_at,status) VALUES(?,?,?,?)",
                       (run_date, step, iso(), "running"))
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, counts: dict | None = None, notes: str | None = None) -> None:
    conn.execute("UPDATE daily_runs SET finished_at=?, status=?, counts=?, notes=? WHERE id=?",
                 (iso(), status, j(counts), notes, run_id))


def record_usage(conn: sqlite3.Connection, model: str, purpose: str, input_tokens: int, output_tokens: int,
                 cost_usd: float, ok: bool) -> None:
    conn.execute("INSERT INTO llm_usage(ts,model,purpose,input_tokens,output_tokens,cost_usd,ok) VALUES(?,?,?,?,?,?,?)",
                 (iso(), model, purpose, input_tokens, output_tokens, cost_usd, 1 if ok else 0))


def usage_summary(conn: sqlite3.Connection, since_iso: str | None = None) -> dict[str, Any]:
    where = "WHERE ts >= ?" if since_iso else ""
    params = (since_iso,) if since_iso else ()
    r = conn.execute(f"SELECT COUNT(*) n, COALESCE(SUM(input_tokens),0) i, COALESCE(SUM(output_tokens),0) o, "
                     f"COALESCE(SUM(cost_usd),0) c FROM llm_usage {where}", params).fetchone()
    return {"calls": r["n"], "input_tokens": r["i"], "output_tokens": r["o"], "cost_usd": round(r["c"], 4)}


def funnel(conn: sqlite3.Connection, run_date: str | None = None) -> dict[str, Any]:
    q = lambda sql, *p: conn.execute(sql, p).fetchone()[0]  # noqa: E731
    out = {
        "properties": q("SELECT COUNT(*) FROM properties"),
        "scored": q("SELECT COUNT(*) FROM scores"),
        "valued": q("SELECT COUNT(*) FROM valuations"),
        "HOT": q("SELECT COUNT(*) FROM valuations WHERE rating='HOT'"),
        "WARM": q("SELECT COUNT(*) FROM valuations WHERE rating='WARM'"),
        "COLD": q("SELECT COUNT(*) FROM valuations WHERE rating='COLD'"),
        "agents": q("SELECT COUNT(*) FROM agents"),
        "agents_with_email": q("SELECT COUNT(*) FROM agents WHERE email IS NOT NULL"),
        "emails_drafted": q("SELECT COUNT(*) FROM outreach WHERE channel='email' AND status='draft'"),
        "emails_sent": q("SELECT COUNT(*) FROM outreach WHERE channel='email' AND status='sent'"),
        "sms_sent": q("SELECT COUNT(*) FROM outreach WHERE channel='sms' AND status='sent'"),
        "sms_drafted": q("SELECT COUNT(*) FROM outreach WHERE channel='sms' AND status='draft'"),
        "replies": q("SELECT COUNT(*) FROM messages WHERE direction='in'"),
        "hot_leads": q("SELECT COUNT(*) FROM properties WHERE lead_status IN ('HOT_LEAD','NEEDS_CALL')"),
        "do_not_contact": q("SELECT COUNT(*) FROM do_not_contact"),
    }
    if run_date:
        lo, hi = et_day_bounds(run_date)
        out["today_properties_seen"] = q("SELECT COUNT(*) FROM properties WHERE last_seen>=? AND last_seen<?", lo, hi)
        out["today_new"] = q("SELECT COUNT(*) FROM properties WHERE first_seen>=? AND first_seen<?", lo, hi)
        out["today_emails"] = q("SELECT COUNT(*) FROM outreach WHERE run_date=? AND channel='email' AND status IN ('sent','draft')", run_date)
        out["today_sms"] = q("SELECT COUNT(*) FROM outreach WHERE run_date=? AND channel='sms' AND status IN ('sent','draft')", run_date)
    return out
