"""Broker answers that did not come back as a reply in Charles's OM thread (Rodrigo, 2026-11-11: "we got the trailing twelve from
772 Cortaro Dr but I do not see it on my board … it probably has not picked up a lot of them, let's fix it").

`poll_om_replies` opens each OM thread by its Gmail id. A broker who writes a NEW email (new subject, forwards the package, writes
from an assistant's or a colleague's address) lands in another thread, so the card never heard of it. This sweep looks at Charles's
inbox once per round and hands every such message to the same handler a reply goes through (`outreach.take_message`): mirrored on
the card, attachments filed and read, regraded, card moved, 🧠 READ FOR YOU at the next books review.

Which thread a stray message belongs to:
  1. sent from the broker's own address → that broker's thread (the one whose street the email names, else the oldest);
  2. otherwise the email names the street of one of our listings ("772 Cortaro") AND is addressed to Charles personally AND is not
     an automated mailer (LoopNet / Crexi alerts name streets too) → that listing's thread.
Messages already in a known conversation (any thread the engine sent in, residential or commercial) are skipped, and every message
looked at is remembered in `commercial_stray_seen`, so nothing is fetched or posted twice.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Callable

from .. import db
from ..util import iso, log, parse_iso

AUTOMATED_RE = re.compile(r"no-?reply|do-?not-?reply|notifications?@|alerts?@|mailer|newsletter|marketing@|@(?:\w+\.)*"
                          r"(?:loopnet|crexi|costar|showcase|homes|zillow|realtor|apartments|ten-x|biproxi)\.com", re.I)
ADDR_RE = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")


def addr(s: str | None) -> str:
    m = ADDR_RE.search(s or "")
    return m.group(0).lower() if m else ""


def street_key(street: str | None) -> re.Pattern | None:
    """'772 Cortaro Dr' → a pattern for '772 Cortaro' (number + first word of the street name), the part every broker writes."""
    m = re.match(r"\s*(\d+[A-Za-z]?)\s+(?:(?:N|S|E|W|NE|NW|SE|SW|North|South|East|West)\.?\s+)?([A-Za-z0-9]+)", street or "")
    if not m or len(m.group(2)) < 3:
        return None
    return re.compile(rf"\b{re.escape(m.group(1))}\s+(?:\w+\.?\s+)?{re.escape(m.group(2))}\b", re.I)


def _threads(conn) -> list[dict[str, Any]]:
    out = []
    for r in conn.execute("SELECT * FROM commercial_threads WHERE gmail_thread_id IS NOT NULL ORDER BY sent_at, id").fetchall():
        t = db.row_to_dict(r)
        try:
            ids = json.loads(t.get("listing_ids") or "[]") or [t["listing_id"]]
        except json.JSONDecodeError:
            ids = [t["listing_id"]]
        keys = []
        for lid in ids:
            row = conn.execute("SELECT data FROM commercial_listings WHERE listing_id=?", (lid,)).fetchone()
            try:
                street = (json.loads(row[0]) or {}).get("street") if row and row[0] else None
            except json.JSONDecodeError:
                street = None
            k = street_key(street)
            if k:
                keys.append(k)
        t["_keys"] = keys
        out.append(t)
    return out


def match(threads: list[dict[str, Any]], m: dict[str, Any], me: str) -> dict[str, Any] | None:
    sender = addr(m.get("from"))
    if not sender or AUTOMATED_RE.search(m.get("from") or ""):
        return None
    text = f"{m.get('subject') or ''}\n{(m.get('body') or '')[:20000]}"
    named = [t for t in threads if any(k.search(text) for k in t["_keys"])]
    own = [t for t in threads if (t.get("broker_email") or "").lower() == sender]
    if own:
        both = [t for t in own if t in named]
        return (both or own)[0]
    if named and me and me in (m.get("to") or "").lower() + " " + (m.get("cc") or "").lower():
        dom = sender.split("@")[-1]
        same = [t for t in named if (t.get("broker_email") or "").lower().endswith("@" + dom)]
        return (same or named)[0]
    return None


def new_refs(conn, cfg: Any, gmail, now: datetime) -> list[dict[str, str]]:
    """ONE cheap Gmail search: every inbound message since the last look that the engine has not handled yet. `poll_om_replies` opens
    only the OM threads that appear here (2026-11-11: opening all ~400 threads every round ran into Gmail's per-minute quota and the
    threads after the cut were never read), and `sweep` looks at the rest for stray broker answers."""
    kv = conn.execute("SELECT value FROM commercial_kv WHERE key='stray_since'").fetchone()
    first = conn.execute("SELECT MIN(sent_at) FROM commercial_threads").fetchone()[0]
    since = (parse_iso(kv[0]) if kv else None) or parse_iso(first) or now
    q = f"after:{(since - timedelta(days=1)):%Y/%m/%d} -in:sent -in:drafts -in:chats"
    seen = {r[0] for r in conn.execute("SELECT gmail_message_id FROM commercial_stray_seen")}
    seen |= {r[0] for r in conn.execute("SELECT gmail_message_id FROM commercial_messages")}
    return [r for r in gmail.search_refs(q, max_results=3000) if r["id"] not in seen]


def mark_seen(conn, ids: list[str], thread_id: int | None, now: datetime) -> None:
    for i in ids:
        conn.execute("INSERT OR IGNORE INTO commercial_stray_seen (gmail_message_id, thread_id, created_at) VALUES (?,?,?)", (i, thread_id, iso(now)))


def sweep(conn, cfg: Any, gmail, handle: Callable[[dict[str, Any], dict[str, Any]], None], now: datetime,
          refs: list[dict[str, str]] | None = None, clean: bool = True) -> dict[str, Any]:
    """Look at new inbox messages outside the known threads; hand each stray broker answer to `handle(thread, message)`.
    `clean` False (a thread could not be opened this round) keeps the look-back date where it is, so nothing is skipped."""
    res: dict[str, Any] = {"looked": 0, "found": 0, "who": []}
    if gmail is None or not hasattr(gmail, "search_refs"):
        return res
    threads = _threads(conn)
    if not threads:
        return res
    me = (cfg.secret("GMAIL_SENDER") or "").lower()
    dom = (cfg.get("operator.sender_email") or me).split("@")[-1].lower()
    per_round = int(cfg.get("commercial.strays.per_round", 150) or 150)
    known = {r[0] for r in conn.execute("SELECT gmail_thread_id FROM commercial_threads WHERE gmail_thread_id IS NOT NULL")}
    known |= {r[0] for r in conn.execute("SELECT gmail_thread_id FROM outreach WHERE gmail_thread_id IS NOT NULL")}
    if refs is None:
        refs = new_refs(conn, cfg, gmail, now)
    refs = [r for r in refs if r.get("thread_id") not in known]
    capped = len(refs) > per_round
    msgs = []
    for r in refs[:per_round]:
        try:
            msgs.append(gmail.message(r["id"]))
        except Exception as e:  # noqa: BLE001
            log.warning("stray sweep: message %s: %s", r["id"], e)
            capped = True
            break                                       # Gmail is pushing back: stop, the rest waits for the next round
    for m in sorted(msgs, key=lambda x: x.get("internal_ts", 0)):
        res["looked"] += 1
        t = None if (dom and f"@{dom}" in (m.get("from") or "").lower()) else match(threads, m, me)     # our own mail is never a broker answer
        sent_at = parse_iso(t["sent_at"]) if t else None
        if t and sent_at and (m.get("internal_ts") or 0) < sent_at.timestamp():
            t = None                                    # written before Charles ever emailed: not an answer to us
        mark_seen(conn, [m["id"]], t["id"] if t else None, now)
        if not t:
            continue
        log.info("stray broker answer %s → thread %s (%s)", m["id"], t["id"], t.get("broker_email"))
        res["found"] += 1
        res["who"].append({"broker": t.get("broker_name") or t.get("broker_email"), "listing_id": t["listing_id"],
                           "from": m.get("from"), "subject": m.get("subject"), "files": len(m.get("attachments") or [])})
        handle(t, m)
    if clean and not capped:
        conn.execute("INSERT INTO commercial_kv (key, value) VALUES ('stray_since', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (iso(now),))
    res["waiting"] = max(0, len(refs) - per_round)
    return res
