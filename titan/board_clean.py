"""Daily Fresh Leads clean-up (Rodrigo, 2026-11-12: "make sure that we're cleaning our board daily for leads that already went
under contract or are no longer available that are on fresh leads").

With new listings paused (ingest.paused) the stored listing status never refreshes, so a house can go pending, sell or be
pulled while its first email still waits in Fresh Leads. Every morning (run-daily step `board_clean`, by hand
`make clean-board`) each house on a Fresh Leads card is looked up on realtor.com through HomeHarvest (free, one address at
a time; realtor.com blocks cloud IPs, so this runs on the Mac). When every house on a card is pending / contingent / under
contract / sold / off the market, the card goes to Skip with one comment, its email is marked skipped (it never sends), and
the house is marked EXCLUDED so no new email is written for it. A card with two houses where only one is gone keeps its place
and gets one comment. A lookup that fails or finds nothing changes nothing. These moves are not Rodrigo's votes on the email
(learning.OFF_MARKET), so they never count against the trust ladder."""
from __future__ import annotations

import json
import time
from typing import Any, Callable

from . import db
from .util import log, today_et

ACTIVE, PENDING, SOLD, OFF_MARKET = "active", "pending", "sold", "off_market"
GONE = (PENDING, SOLD, OFF_MARKET)
WORDS = {PENDING: "under contract (pending)", SOLD: "sold", OFF_MARKET: "off the market (withdrawn / expired)"}
# MLS words that mean the house is no longer available, as realtor.com / the MLS feeds spell them
_PENDING_WORDS = ("pending", "contingent", "under contract", "under_contract", "backup", "back up", "active under")
_OFF_WORDS = ("off market", "off_market", "withdrawn", "expired", "cancel", "temporarily off", "inactive")

Lookup = Callable[[dict[str, Any]], "str | None"]


def classify(rec: dict[str, Any] | None) -> str | None:
    """realtor.com home record → active / pending / sold / off_market, or None when it can't tell."""
    if not isinstance(rec, dict):
        return None
    status = str(rec.get("status") or "").lower()
    mls = str(rec.get("mls_status") or "").lower()
    flags = rec.get("flags") or {}
    if status == "sold" or mls in ("sold", "closed") or mls.startswith("closed"):
        return SOLD
    if flags.get("is_pending") or flags.get("is_contingent") or status in ("pending", "contingent") \
            or any(w in mls for w in _PENDING_WORDS):
        return PENDING
    if status == "off_market" or any(w in mls for w in _OFF_WORDS):
        return OFF_MARKET
    if status in ("for_sale", "active", "coming_soon", "ready_to_build", "new_community") or mls.startswith("active"):
        return ACTIVE
    return None


def stored_status(p: dict[str, Any]) -> str | None:
    """What the database already says (a feed that did catch the change). Only a clear 'gone' counts."""
    s = str(p.get("listing_status") or "").lower()
    if not s:
        return None
    st = classify({"status": s, "mls_status": s})
    return st if st in GONE else None


def homeharvest_lookup(cfg: Any, scrape=None) -> Lookup:
    """One realtor.com single-address search per house (HomeHarvest's address path returns that home with its status)."""
    def fn(p: dict[str, Any]) -> str | None:
        from .ingest.homeharvest_src import _scraper
        loc = ", ".join(x for x in (p.get("street"), p.get("city"), f"{p.get('state') or 'FL'} {p.get('zip') or ''}".strip()) if x)
        items = (scrape or _scraper())(loc, return_type="raw", extra_property_data=False,
                                       proxy=cfg.get("ingest.homeharvest.proxy") or None) or []
        recs = [r for r in items if isinstance(r, dict)]
        if not recs:
            return None
        sid = str(p.get("source_id") or "")
        same = [r for r in recs if sid and sid in (str(r.get("property_id")), str(r.get("listing_id")))]
        return classify((same or recs)[0])
    return fn


def _props(conn, row: dict[str, Any]) -> list[dict[str, Any]]:
    pids = json.loads(row.get("property_ids") or "[]")
    return [db.row_to_dict(r) for r in conn.execute(
        f"SELECT * FROM properties WHERE id IN ({','.join('?' * len(pids)) or 'NULL'})", pids).fetchall()]


def _comment(asana, gid: str, text: str) -> None:
    try:
        asana.comment(gid, text)
    except Exception as e:  # noqa: BLE001
        log.warning("board clean: comment on %s: %s", gid, e)


def run(conn, cfg: Any, asana, lookup: Lookup | None = None, dry_run: bool = False, sleep_s: float | None = None) -> dict[str, Any]:
    """Look up every house on a Fresh Leads card; move the cards whose houses are all gone to Skip. Never raises."""
    from .approvals import BOT, parse_card
    from .learning import OFF_MARKET as DECISION, record_decision
    conf = cfg.get("outreach.board_clean", {}) or {}
    n: dict[str, Any] = {"cards": 0, "checked": 0, "to_skip": 0, "partly_gone": 0, "still_active": 0, "unknown": 0,
                         "errors": 0, "gone": []}
    a = cfg.get("outreach.asana", {}) or {}
    if not conf.get("enabled", True):
        return {"skipped": "outreach.board_clean.enabled is off"}
    if not (asana and a.get("to_review") and a.get("skip")):
        return {"skipped": "no Asana board"}
    lookup = lookup or homeharvest_lookup(cfg)
    max_checks = int(conf.get("max_checks", 300))
    max_errors = int(conf.get("stop_after_errors", 5))
    sleep_s = float(conf.get("pause_seconds", 1.0)) if sleep_s is None else sleep_s
    today = today_et()
    try:
        cards = asana.section_tasks(a["to_review"])
    except Exception as e:  # noqa: BLE001
        log.warning("board clean: Fresh Leads unreadable: %s", e)
        return {**n, "error": str(e)[:200]}
    seen: dict[int, str | None] = {}
    for t in cards:
        card = parse_card(t.get("notes") or "")
        if not card or card.get("reply_to"):
            continue
        row = db.row_to_dict(conn.execute("SELECT * FROM outreach WHERE id=?", (card["outreach_id"],)).fetchone())
        if not row or row.get("status") != "pending_approval":
            continue
        props = _props(conn, row)
        if not props:
            continue
        n["cards"] += 1
        states: list[tuple[dict[str, Any], str | None]] = []
        for p in props:
            if p["id"] not in seen:
                st = stored_status(p)
                if st is None and n["checked"] < max_checks and n["errors"] < max_errors:
                    try:
                        if n["checked"] and sleep_s:
                            time.sleep(sleep_s)                     # polite: one address at a time
                        n["checked"] += 1
                        st = lookup(p)
                    except Exception as e:  # noqa: BLE001 — one failed lookup changes nothing
                        n["errors"] += 1
                        log.warning("board clean: lookup %s failed: %s", p.get("street"), e)
                        st = None
                seen[p["id"]] = st
            states.append((p, seen[p["id"]]))
        gone = [(p, s) for p, s in states if s in GONE]
        if not gone:
            n["still_active" if all(s == ACTIVE for _, s in states) else "unknown"] += 1
            continue
        lines = "; ".join(f"{p.get('street')}: {WORDS[s]}" for p, s in gone)
        n["gone"].append({"card": t["gid"], "outreach_id": row["id"], "agent": row.get("to_address"), "houses": lines,
                          "all_gone": len(gone) == len(states)})
        if dry_run:
            n["to_skip" if len(gone) == len(states) else "partly_gone"] += 1
            continue
        for p, s in gone:
            conn.execute("UPDATE properties SET listing_status=?, lead_status='EXCLUDED', lead_status_at=?, lead_note=? WHERE id=?",
                         (s, db.iso(), f"off market: {WORDS[s]} (realtor.com, checked {today})", p["id"]))
        if len(gone) < len(states):
            note = f"{BOT} Board clean-up:"
            try:
                said = any((c.get("text") or "").startswith(note) and lines in (c.get("text") or "") for c in asana.comments(t["gid"]))
            except Exception:  # noqa: BLE001
                said = False
            if not said:
                _comment(asana, t["gid"], f"Board clean-up: {lines} (realtor.com, checked {today}). The other house on this "
                                          "email is still for sale, so the card stays; edit the email if you still want to send it.")
            n["partly_gone"] += 1
            continue
        try:
            comments = asana.comments(t["gid"])
        except Exception:  # noqa: BLE001
            comments = []
        record_decision(conn, t["gid"], row["id"], "first_touch", DECISION, comments)   # not his vote on the email
        conn.execute("UPDATE outreach SET status='skipped' WHERE id=? AND status='pending_approval'", (row["id"],))
        try:
            asana.move(t["gid"], a["skip"])
        except Exception as e:  # noqa: BLE001
            log.warning("board clean: move %s to Skip: %s", t["gid"], e)
            n["errors"] += 1
            continue
        _comment(asana, t["gid"], f"Board clean-up: {lines} (realtor.com, checked {today}). Moved to Skip; the email was never sent "
                                  "and won't be. Drag it back to Fresh Leads if that's wrong.")
        n["to_skip"] += 1
    if n["errors"] and n["checked"] <= n["errors"]:
        n["warning"] = "every realtor.com lookup failed (blocked? it only works from the Mac)"
    return n


def plain(n: dict[str, Any]) -> str:
    if n.get("skipped"):
        return f"Board clean-up skipped: {n['skipped']}"
    out = [f"Fresh Leads clean-up: {n.get('cards', 0)} cards, {n.get('checked', 0)} houses looked up on realtor.com",
           f"  moved to Skip (no longer for sale): {n.get('to_skip', 0)} · one house of two gone: {n.get('partly_gone', 0)} · "
           f"still for sale: {n.get('still_active', 0)} · couldn't tell: {n.get('unknown', 0)}"]
    for g in n.get("gone", [])[:40]:
        out.append(f"  {'SKIP ' if g['all_gone'] else 'NOTE '} {g['agent']}: {g['houses']}")
    if n.get("warning"):
        out.append(f"  WARNING: {n['warning']}")
    return "\n".join(out)
