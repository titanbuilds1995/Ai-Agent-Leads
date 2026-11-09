"""The no-cap blast (Rodrigo, 2026-11-09: "the rest of the agents that do not show a cap rate, I want us to email them asking them if it
fits an 8 cap, once sold and re assessed ... we need to send those emails out also today", and "let's not put anything else on the
Asana board"). One email per broker covering all of their in-box listings without a cap rate, queued in data/commercial_queue/<date>.json
(so the Mac, whose database never saw the cloud's pull, can send it), sent by the approvals rounds from Charles@ at the commercial pace,
no Asana card until the broker answers (poll_om_replies then makes one in the column the answer earns)."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import db
from ..util import iso, now_utc
from .om_request import cap_question, validate_om_request

log = logging.getLogger(__name__)
QUEUE_DIR = "data/commercial_queue"


HEAVY = ("description", "text", "highlights", "brokers", "price_history", "documents", "hotel_metrics", "tenant_names")


def _listing(row: dict[str, Any]) -> dict[str, Any]:
    """The stored listing dict, minus the heavy text, so the queue file can carry it to the Mac (whose database never saw this pull)."""
    L = {}
    try:
        L = {k: v for k, v in json.loads(row.get("data") or "{}").items() if k not in HEAVY}
    except json.JSONDecodeError:
        L = {}
    for k in ("listing_id", "street", "city", "broker_name", "broker_email", "broker_phone", "brokerage", "asset_class", "price", "score"):
        if row.get(k) not in (None, ""):
            L[k] = row[k]
    return L


def build_queue(conn, cfg: Any, run_date: str | None = None) -> list[dict[str, Any]]:
    """Every in-box, active listing with no stated cap and a broker email, grouped by broker → one entry per broker, written to the
    queue file. Brokers already emailed (any commercial thread) and bounced addresses are left out."""
    run_date = run_date or now_utc().strftime("%Y-%m-%d")
    rows = [db.row_to_dict(r) for r in conn.execute(
        "SELECT * FROM commercial_listings WHERE in_box=1 AND stated_cap IS NULL AND COALESCE(status,'active')='active' "
        "AND COALESCE(broker_email,'')<>'' ORDER BY score DESC").fetchall()]
    emailed = {r[0] for r in conn.execute("SELECT LOWER(broker_email) FROM commercial_threads WHERE broker_email IS NOT NULL").fetchall()}
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        em = r["broker_email"].strip().lower()
        if em in emailed:
            continue
        by.setdefault(em, []).append(_listing(r))
    out = []
    for em, Ls in by.items():
        Ls.sort(key=lambda L: -(L.get("score") or 0))
        try:
            msg = cap_question(cfg, Ls)
        except ValueError:
            continue
        problems = validate_om_request(msg["subject"], msg["body"])
        if problems:
            log.warning("blast: %s skipped (%s)", em, "; ".join(problems))
            continue
        ids = [str(L["listing_id"]) for L in Ls]
        out.append({"id": hashlib.sha1((em + "|" + ",".join(sorted(ids))).encode()).hexdigest()[:12], "broker_email": em,
                    "broker_name": Ls[0].get("broker_name"), "brokerage": Ls[0].get("brokerage"), "listing_ids": ids,
                    "streets": [f"{L.get('street')}, {L.get('city')}" for L in Ls], "score": max(L.get("score") or 0 for L in Ls),
                    "subject": msg["subject"], "body": msg["body"], "listings": Ls})
    out.sort(key=lambda e: -e["score"])
    folder = cfg.root / cfg.get("commercial.outreach.queue_dir", QUEUE_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{run_date}.json").write_text(json.dumps({"built_at": iso(now_utc()), "entries": out}, ensure_ascii=False, indent=1))
    return out


def pending(conn, cfg: Any) -> list[dict[str, Any]]:
    """Queue entries not yet sent: every broker email in every queue file minus the ones with a commercial thread."""
    folder = cfg.root / cfg.get("commercial.outreach.queue_dir", QUEUE_DIR)
    if not folder.exists():
        return []
    emailed = {r[0] for r in conn.execute("SELECT LOWER(broker_email) FROM commercial_threads WHERE broker_email IS NOT NULL").fetchall()}
    seen, out = set(), []
    for f in sorted(folder.glob("*.json")):
        try:
            entries = json.loads(f.read_text()).get("entries") or []
        except (json.JSONDecodeError, OSError):
            continue
        for e in entries:
            em = (e.get("broker_email") or "").lower()
            if not em or em in emailed or em in seen:
                continue
            seen.add(em)
            out.append(e)
    out.sort(key=lambda e: -(e.get("score") or 0))
    return out


def send_queue(conn, cfg: Any, gmail, room: int, now: datetime | None = None) -> dict[str, Any]:
    """Send up to `room` queued emails from Charles@. Each one is logged like an OM request (outreach kind om_request + a
    commercial_threads row on the first listing, the others in listing_ids) so replies, the response clock and the books flow the same way."""
    now = now or now_utc()
    res: dict[str, Any] = {"sent": 0, "errors": 0, "waiting": 0}
    todo = pending(conn, cfg)
    res["waiting"] = len(todo)
    if gmail is None or room <= 0:
        return res
    run_date = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
    for e in todo[:room]:
        try:
            sent = gmail.send(e["broker_email"], e["subject"], e["body"])
        except Exception as ex:  # noqa: BLE001
            res["errors"] += 1
            log.error("blast send failed for %s: %s", e["broker_email"], ex)
            continue
        ts = iso(now)
        ids = [str(i) for i in e.get("listing_ids") or []]
        for L in e.get("listings") or []:          # a database that never saw this pull (the Mac) gets the listing from the queue file
            if L.get("listing_id") and not conn.execute("SELECT 1 FROM commercial_listings WHERE listing_id=?", (str(L["listing_id"]),)).fetchone():
                from .run import store
                try:
                    store(conn, L, run_date)
                except Exception as ex:  # noqa: BLE001
                    log.warning("blast: could not store listing %s: %s", L.get("listing_id"), ex)
        cur = conn.execute("INSERT INTO outreach (agent_id, property_ids, channel, kind, status, subject, body, to_address, gmail_thread_id, "
                           "gmail_message_id, run_date, created_at, sent_at) VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (json.dumps([f"loopnet:{i}" for i in ids]), "email", "om_request", "sent", e["subject"], e["body"], e["broker_email"],
                            sent.get("thread_id"), sent.get("message_id"), run_date, ts, ts))
        conn.execute("INSERT INTO commercial_threads (listing_id, listing_ids, broker_email, broker_name, gmail_thread_id, gmail_message_id, outreach_id, "
                     "subject, sent_at, state, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (ids[0] if ids else "", json.dumps(ids), e["broker_email"], e.get("broker_name"), sent.get("thread_id"), sent.get("message_id"),
                      cur.lastrowid, e["subject"], ts, "SENT", ts, ts))
        for i in ids:
            conn.execute("UPDATE commercial_listings SET board_column=COALESCE(board_column,'In touch with the agent') WHERE listing_id=?", (i,))
        res["sent"] += 1
    res["waiting"] = max(0, len(todo) - res["sent"])
    return res
