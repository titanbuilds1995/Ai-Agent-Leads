"""Offer log (Rodrigo, 2026-10-26): every written offer that actually went out, so we know what this market looks like
(how many offers, how far under ask, how many get countered / accepted) and can copy them onto the main results board.

One code path: `sync_offers()` scans sent emails (reply cards and approve-offer emails) for an offer figure and logs the ones
not logged yet. It runs after every approvals tick and when stats are computed, so nothing has to remember to call it."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from . import db
from .util import iso, log

STATUSES = ("SENT", "COUNTERED", "ACCEPTED", "UNDER_CONTRACT", "CLOSED", "REJECTED", "DEAD")
OPEN_STATUSES = ("SENT", "COUNTERED")
MIN_OFFER = 30_000          # smaller figures in an offer email are the deposit, holdback or repair numbers, not the price
_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d{5,7})(?!\s*(?:/|per)\s*(?:mo|month))", re.I)


def offer_price(body: str, list_price: int | None = None) -> int | None:
    """The first dollar figure that reads as a purchase price (not the $5,000 deposit, not the asking price)."""
    for m in _MONEY.finditer(body or ""):
        n = int(m.group(1).replace(",", ""))
        if n >= MIN_OFFER and n != (list_price or 0):
            return n
    return None


def sync_offers(conn, cfg: Any = None) -> int:
    """Log every sent offer email that is not in the offer log yet. Returns how many were added."""
    rows = conn.execute("SELECT o.* FROM outreach o LEFT JOIN offers f ON f.outreach_id=o.id WHERE f.id IS NULL AND o.channel='email' "
                        "AND o.status='sent' AND (o.kind LIKE 'reply%' OR o.kind LIKE 'offer%')").fetchall()
    added = 0
    for r in rows:
        r = db.row_to_dict(r)
        pids = json.loads(r["property_ids"] or "[]")
        p = db.row_to_dict(conn.execute("SELECT * FROM properties WHERE id=?", (pids[0],)).fetchone()) if pids else None
        price = offer_price(r["body"], (p or {}).get("list_price"))
        if not price or not p:
            continue
        rng = conn.execute("SELECT internal_low, internal_high FROM price_decisions WHERE property_id=? ORDER BY id DESC LIMIT 1",
                           (p["id"],)).fetchone()
        conn.execute("INSERT OR IGNORE INTO offers(property_id,agent_id,outreach_id,price,list_price,street,city,zip,county,internal_low,"
                     "internal_high,status,sent_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (p["id"], r["agent_id"], r["id"], price, p.get("list_price"), p.get("street"), p.get("city"), p.get("zip"),
                      p.get("county"), rng[0] if rng else None, rng[1] if rng else None, "SENT", r.get("sent_at") or iso(), iso()))
        db.set_lead_status(conn, p["id"], "OFFER_SENT", f"offer ${price:,} sent")
        added += 1
        log.info("offer log: ${:,} on {} (outreach #{})".format(price, p.get("street"), r["id"]))
    return added


def set_status(conn, offer_id: int, status: str, note: str | None = None) -> dict[str, Any]:
    status = status.upper().replace(" ", "_")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    if not conn.execute("SELECT 1 FROM offers WHERE id=?", (offer_id,)).fetchone():
        raise ValueError(f"no offer #{offer_id}")
    conn.execute("UPDATE offers SET status=?, note=COALESCE(?, note), updated_at=? WHERE id=?", (status, note, iso(), offer_id))
    return db.row_to_dict(conn.execute("SELECT * FROM offers WHERE id=?", (offer_id,)).fetchone())


def all_offers(conn) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT f.*, a.name AS agent_name, a.email AS agent_email, a.phone AS agent_phone FROM offers f "
                        "LEFT JOIN agents a ON a.id=f.agent_id ORDER BY f.sent_at").fetchall()
    out = []
    for r in rows:
        d = db.row_to_dict(r)
        d["pct_of_list"] = round(100 * d["price"] / d["list_price"], 1) if d.get("list_price") else None
        out.append(d)
    return out


def offer_stats(conn) -> dict[str, Any]:
    """The market picture: how many offers, how far under ask, and what happened to them (overall and per city)."""
    offers = all_offers(conn)
    pcts = sorted(o["pct_of_list"] for o in offers if o["pct_of_list"] is not None)

    def summary(group: list[dict[str, Any]]) -> dict[str, Any]:
        ps = sorted(o["pct_of_list"] for o in group if o["pct_of_list"] is not None)
        by = {s: sum(1 for o in group if o["status"] == s) for s in STATUSES}
        decided = len(group) - by["SENT"]
        won = by["ACCEPTED"] + by["UNDER_CONTRACT"] + by["CLOSED"]
        return {"offers": len(group), "by_status": by, "median_pct_of_list": ps[len(ps) // 2] if ps else None,
                "response_rate_pct": round(100 * decided / len(group), 1) if group else None,
                "accept_rate_pct": round(100 * won / len(group), 1) if group else None,
                "total_offered": sum(o["price"] for o in group)}

    cities: dict[str, list[dict[str, Any]]] = {}
    for o in offers:
        cities.setdefault(o.get("city") or "?", []).append(o)
    return {**summary(offers), "min_pct_of_list": pcts[0] if pcts else None, "max_pct_of_list": pcts[-1] if pcts else None,
            "by_city": {c: summary(g) for c, g in sorted(cities.items(), key=lambda kv: -len(kv[1]))}}


COLUMNS = ("id", "sent_at", "street", "city", "zip", "county", "list_price", "price", "pct_of_list", "status", "note",
           "agent_name", "agent_email", "agent_phone", "internal_low", "internal_high", "property_id", "outreach_id")


def export_csv(conn, path: Path) -> Path:
    """reports/offers.csv: one row per offer, ready to paste onto the main results board."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for o in all_offers(conn):
            w.writerow({k: (o.get(k) or "")[:10] if k == "sent_at" else o.get(k) for k in COLUMNS})
    return path
