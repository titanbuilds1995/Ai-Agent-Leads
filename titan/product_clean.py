"""55+ / townhouse / land clean-up of the house board (Rodrigo, 2026-11-18: "I want us to not look for properties in 55 and
older communities, and no townhouses ... clean out all of the properties I have on the board and take out all of those
properties"; 2026-11-24: "we need to make sure that we are not pulling land leads on the MLS scraper").

The rule itself lives in ingest/normalize.py (flags `age_restricted` / `townhouse` / `land`, in `market.exclude_flags`): exclude_flagged()
marks matching stored houses EXCLUDED, so they are never scored, valued or emailed again and the follow-up sequence stops.
This module takes their cards off the Email Approvals board:
  - a card whose houses are all 55+ / townhouse / land and whose agent never really answered (Fresh Leads, Needs Me, Needs Revision,
    Approved, Sent · no answer yet) → Skip, one "Rule check" comment, an unsent email is marked skipped, a drafted NEXT EMAIL dropped;
  - a lead the agent answered stays where it is with one comment (a live conversation is his call);
  - he drags a skipped card back onto the board → that house is kept for good (`product_keeps`).
Not his vote on the email (learning.OFF_MARKET). Never raises per card.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from . import db

log = logging.getLogger(__name__)

RULES = {"age_restricted": "55+ / age-restricted community", "townhouse": "townhouse", "land": "vacant land / lot"}
NOTE = "Rule check:"
WHY = "We don't go after 55+ communities, townhouses or land any more"


def _hits(p: dict[str, Any]) -> list[str]:
    note = p.get("lead_note") or ""
    if p.get("lead_status") != "EXCLUDED" or not note.startswith("excluded:"):
        return []
    return [k for k in RULES if k in note]


def _said(asana, gid: str) -> bool:
    try:
        return any(NOTE in (c.get("text") or "") for c in asana.comments(gid))
    except Exception:  # noqa: BLE001
        return False


def run(conn, cfg: Any, asana, dry_run: bool = False) -> dict[str, Any]:
    """dry_run: the database work happens inside a transaction that is rolled back, so the list is real and nothing changes."""
    if not dry_run:
        return _run(conn, cfg, asana, False)
    conn.execute("BEGIN")
    try:
        return _run(conn, cfg, asana, True)
    finally:
        conn.execute("ROLLBACK")


def _run(conn, cfg: Any, asana, dry_run: bool) -> dict[str, Any]:
    from .approvals import needs_me_section, needs_revision_section, parse_card
    from .conversations import board_sections, where_we_stand
    from .ingest.normalize import exclude_flagged
    from .learning import OFF_MARKET as DECISION, record_decision
    n: dict[str, Any] = {"excluded_now": 0, "cards": 0, "to_skip": 0, "answered_kept": 0, "partly": 0, "put_back_by_you": 0,
                         "errors": 0, "moved": []}
    n["excluded_now"] = exclude_flagged(conn, cfg)      # rolled back on a dry run
    a = cfg.get("outreach.asana", {}) or {}
    if not (asana and a.get("to_review") and a.get("skip")):
        return {**n, "skipped": "no Asana board"}
    sections = [("Fresh Leads", a["to_review"])]
    for name, fn in (("Needs Me", lambda: needs_me_section(cfg, asana)), ("Needs Revision", lambda: needs_revision_section(cfg, asana)),
                     ("Approved", lambda: a.get("approved")), ("Sent · no answer yet", lambda: board_sections(cfg, asana)[0]),
                     ("Answered", lambda: board_sections(cfg, asana)[1])):
        try:
            gid = fn()
            if gid and gid not in {g for _, g in sections}:
                sections.append((name, gid))
        except Exception as e:  # noqa: BLE001
            log.warning("product clean: %s unreadable: %s", name, e)
    for col, sec in sections:
        try:
            cards = asana.section_tasks(sec)
        except Exception as e:  # noqa: BLE001
            log.warning("product clean: %s unreadable: %s", col, e)
            n["errors"] += 1
            continue
        for t in cards:
            try:
                _card(conn, cfg, asana, t, col, n, dry_run, parse_card, where_we_stand, record_decision, DECISION)
            except Exception as e:  # noqa: BLE001 — one card failing changes nothing else
                log.warning("product clean: card %s: %s", t.get("gid"), e)
                n["errors"] += 1
    return n


def _card(conn, cfg, asana, t, col, n, dry_run, parse_card, where_we_stand, record_decision, DECISION) -> None:
    a = cfg.get("outreach.asana", {}) or {}
    card = parse_card(t.get("notes") or "")
    if not card or card.get("reply_to"):
        return
    row = db.row_to_dict(conn.execute("SELECT * FROM outreach WHERE id=?", (card["outreach_id"],)).fetchone())
    if not row:
        return
    pids = json.loads(row.get("property_ids") or "[]")
    props = [db.row_to_dict(r) for r in conn.execute(
        f"SELECT * FROM properties WHERE id IN ({','.join('?' * len(pids)) or 'NULL'})", pids).fetchall()]
    if not props:
        return
    hits = {p["id"]: _hits(p) for p in props}
    if row.get("status") == "skipped" and any(hits.values()) and _said(asana, t["gid"]):
        # we moved it to Skip and he dragged it back onto the board: he wants this house
        if not dry_run:
            for p in props:
                if hits[p["id"]]:
                    conn.execute("INSERT OR REPLACE INTO product_keeps(property_id, kept_at) VALUES (?,?)", (p["id"], db.iso()))
                    conn.execute("UPDATE properties SET lead_status='NONE', lead_status_at=?, lead_note=NULL WHERE id=?", (db.iso(), p["id"]))
            conn.execute("UPDATE outreach SET status='pending_approval' WHERE id=? AND sent_at IS NULL", (row["id"],))
            conn.execute("DELETE FROM card_decisions WHERE card_gid=? AND decision=?", (t["gid"], DECISION))
        n["put_back_by_you"] += 1
        return
    if not any(hits.values()):
        return
    n["cards"] += 1
    what = "; ".join(f"{p.get('street')}: {', '.join(RULES[h] for h in hits[p['id']])}" for p in props if hits[p["id"]])
    entry = {"column": col, "agent": row.get("to_address"), "houses": what}
    if not all(hits.values()):
        n["partly"] += 1
        n["moved"].append({**entry, "action": "note (other house on the email is fine)"})
        if not dry_run and not _said(asana, t["gid"]):
            asana.comment(t["gid"], f"{NOTE} {what}. {WHY}; the other house on "
                                    "this email is fine, so the card stays. Edit the email if you still want to send it.")
        return
    answered = col == "Answered" or (row.get("agent_id") and where_we_stand(conn, row["agent_id"]) is not None)
    if answered:
        n["answered_kept"] += 1
        n["moved"].append({**entry, "action": "kept (the agent answered)"})
        if not dry_run and not _said(asana, t["gid"]):
            asana.comment(t["gid"], f"{NOTE} {what}. {WHY}, but this agent "
                                    "answered, so I left the card where it is and stopped the follow-ups. Drag it to Skip if you're done with it.")
        return
    n["to_skip"] += 1
    n["moved"].append({**entry, "action": "to Skip"})
    if dry_run:
        return
    try:
        comments = asana.comments(t["gid"])
    except Exception:  # noqa: BLE001
        comments = []
    record_decision(conn, t["gid"], row["id"], "first_touch", DECISION, comments)   # not his vote on the email
    conn.execute("UPDATE outreach SET status='skipped' WHERE id=? AND status='pending_approval'", (row["id"],))
    if row.get("answer_draft"):
        conn.execute("UPDATE outreach SET answer_draft=NULL, answer_note=NULL, answer_state=NULL WHERE id=?", (row["id"],))
    asana.move(t["gid"], a["skip"])
    sent = row.get("status") == "sent" or row.get("sent_at")
    asana.comment(t["gid"], f"{NOTE} {what}. {WHY} (your rules, Nov 18 and Nov 24), so it "
                            f"moved to Skip; {'no more follow-ups go to this agent' if sent else 'the email was never sent and won’t be'}. "
                            "Drag it back onto the board if that's wrong and I'll keep the house.")


def plain(n: dict[str, Any]) -> str:
    out = [f"55+ / townhouse / land clean-up: {n.get('excluded_now', 0)} more houses ruled out in the database · {n.get('cards', 0)} cards on the board",
           f"  moved to Skip: {n.get('to_skip', 0)} · agent answered, card left with a note: {n.get('answered_kept', 0)} · "
           f"one house of two: {n.get('partly', 0)} · put back by you (kept): {n.get('put_back_by_you', 0)}"]
    if n.get("skipped"):
        out.append(f"  board skipped: {n['skipped']}")
    for m in n.get("moved", [])[:80]:
        out.append(f"  {m['action']:<40} [{m['column']}] {m['agent']}: {m['houses']}")
    return "\n".join(out)
