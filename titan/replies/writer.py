"""STAGE 3 OF THE TRUST LADDER (Rodrigo, 2026-10-26): Charles writes his own answer to every agent who replied, as a reply card
on the approvals board. Rodrigo edits it like a first email (comment → rewrite, or edit the text, drag to Approved), every decision
is recorded and his comments become lessons, so the writer learns how to answer people from real conversations.

Conversation first still holds: no price, range, percentage or offer language ever. When the agent asks for an offer or gives a
number, the card is a holding answer (one easy question, no number) and the WHERE WE STAND block tells Rodrigo it needs his number.
Nothing sends on its own: the card waits in To Review."""
from __future__ import annotations

import json
import re
from typing import Any

from .. import db
from ..util import first_name, log

ANSWERABLE = ("INTERESTED", "QUESTION", "ASK_FOR_OFFER", "COUNTER_WITH_NUMBER")
REPLY_SCHEMA = {"type": "object", "properties": {"read": {"type": "string"}, "body": {"type": "string"}},
                "required": ["read", "body"], "additionalProperties": False}

REPLY_SYSTEM = """You are {sender}, Junior Acquisitions Agent at {company}. A listing agent answered your email. Write {sender}'s NEXT reply
in the same email thread. Plain text, no subject line.

Rules (Rodrigo's, all hard):
- Answer what they said first, briefly and like a person ("Thanks Lise, that helps."). Use what they told you; never re-ask anything
  they already answered anywhere in the thread.
- Then ONE easy question that moves the deal forward (seller's timeline or flexibility, occupancy, condition of the big-ticket items,
  why they are selling). One question only.
- NO price, number, range, percentage, "offer", discount or dollar figure, ever. If they asked for our offer or named a number, say
  you're putting the numbers together on your side and will come back to them shortly, then ask the one question that most affects
  the number. Never promise a date or an amount.
- Terms you may state if asked: as-is, cash, {terms}. Always keep the inspection period; never offer to waive it.
- Never say we are the buyer or investors ("we buy", "we're investors", "serious buyers", "genuinely interested"). Say "our side",
  "what we look for", "something we'd pursue".
- "I" only for what {sender} did or will do; "we"/"our side" for criteria. Never powerless ("ask my boss"), never overcommitting.
- 25 to 90 words before the signature. No dashes of any kind (no em dash, en dash, " - " or "--"). No emojis, no links.
- End with the signature block from the input, unchanged.

=== VOICE PROTOCOL ===
{voice}
=== END VOICE PROTOCOL ===
{learned}
Return JSON: read (one line: what the agent told us and what we still need to learn) and body (the reply, signature included)."""


def validate_reply(body: str) -> list[str]:
    from ..outreach.compose import BUYER_RE, DASH_RE, HTML_RE, NO_NUMBERS_RE, PERSONAL_RE, URL_RE
    problems = []
    text = body.split("\n\nBest")[0]
    words = len(text.split())
    if words < 15 or words > 140:
        problems.append(f"length {words}")
    if re.search(r"\$\s?\d", body) or NO_NUMBERS_RE.search(text) or re.search(r"\b(our|an|my) offer\b|\boffer (of|at)\b", text, re.I):
        problems.append("price or offer language")
    if BUYER_RE.search(body):
        problems.append("says we are the buyer / investors")
    if PERSONAL_RE.search(body):
        problems.append("personal framing of criteria")
    if DASH_RE.search(text):
        problems.append("dash")
    if HTML_RE.search(body) or URL_RE.search(text):
        problems.append("html or link")
    if text.count("?") > 2:
        problems.append("more than one question")
    return problems


def _thread_text(conn, agent_id: int) -> list[dict[str, str]]:
    rows = conn.execute("SELECT m.direction, m.body, m.created_at, m.classification FROM messages m JOIN threads t ON t.id=m.thread_id "
                        "WHERE t.agent_id=? AND COALESCE(m.classification,'') NOT IN ('BOUNCE','OUT_OF_OFFICE') ORDER BY m.created_at, m.id",
                        (agent_id,)).fetchall()
    return [{"from": "agent" if r["direction"] == "in" else "Charles", "when": r["created_at"], "text": (r["body"] or "")[:2500]} for r in rows]


def waiting_threads(conn) -> list[dict[str, Any]]:
    """Agents whose last real message is theirs (we owe an answer) and whose last message got no reply card yet."""
    out = []
    for t in conn.execute("SELECT DISTINCT agent_id FROM threads WHERE agent_id IS NOT NULL").fetchall():
        last = conn.execute("SELECT m.* FROM messages m JOIN threads t ON t.id=m.thread_id WHERE t.agent_id=? "
                            "AND COALESCE(m.classification,'') NOT IN ('BOUNCE','OUT_OF_OFFICE') ORDER BY m.created_at DESC, m.id DESC LIMIT 1",
                            (t["agent_id"],)).fetchone()
        if last and last["direction"] == "in" and (last["classification"] or "") in ANSWERABLE and not last["reply_card"]:
            out.append({"agent_id": t["agent_id"], "message": db.row_to_dict(last)})
    return out


def draft_reply_cards(conn, cfg: Any, asana, llm, limit: int = 5) -> dict[str, Any]:
    """Write Charles's answer for each agent waiting on us and put it on the board as a reply card (To Review). Never raises."""
    from ..approvals import _cfg, needs_revision_section, parse_card, post_reply_card
    from ..learning import learned_block
    from ..outreach.compose import signature, voice_text
    from ..util import inspection_phrase
    n = {"drafted": 0, "skipped_pending_card": 0, "failed": 0}
    if llm is None or not getattr(llm, "available", False):
        return {**n, "note": "Claude not available; no reply cards written this tick"}
    try:
        a = _cfg(cfg)
        pending_oids = set()
        for sec in (a.get("to_review"), a.get("approved"), needs_revision_section(cfg, asana)):
            for t in asana.section_tasks(sec):
                pc = parse_card(t.get("notes") or "")
                if pc and pc.get("reply_to"):
                    pending_oids.add(pc["outreach_id"])
        pending_agents = {r[0] for o in pending_oids for r in conn.execute("SELECT agent_id FROM outreach WHERE id=?", (o,)).fetchall()}
        op, f, terms = cfg["operator"], cfg["formula"], cfg["outreach"]["terms"]
        system = REPLY_SYSTEM.format(sender=op["sender_name"].split()[0], company=op["company"], voice=voice_text(cfg), learned=learned_block(cfg),
                                     terms=f"{f['close_days']}-day close, {inspection_phrase(terms)}, ${f['emd']:,.0f} EMD")
        for w in waiting_threads(conn)[:limit]:
            msg = w["message"]
            if w["agent_id"] in pending_agents:          # an answer is already on the board for this agent
                n["skipped_pending_card"] += 1
                continue
            row = conn.execute("SELECT id, property_ids FROM outreach WHERE agent_id=? AND kind='first_touch' AND status='sent' "
                               "ORDER BY sent_at DESC, id DESC LIMIT 1", (w["agent_id"],)).fetchone()
            if not row:
                continue
            agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (w["agent_id"],)).fetchone()) or {}
            pids = json.loads(row["property_ids"] or "[]")
            props = [db.row_to_dict(r) for r in conn.execute(
                f"SELECT street, city, list_price, days_on_market, price_cuts, beds, baths, sqft, year_built, property_type, remarks "
                f"FROM properties WHERE id IN ({','.join('?' * len(pids)) or 'NULL'})", pids).fetchall()]
            user = json.dumps({"agent_first_name": first_name(agent.get("name")), "their_last_message_class": msg.get("classification"),
                               "thread": _thread_text(conn, w["agent_id"]), "properties": props, "signature_block": signature(cfg)},
                              ensure_ascii=False, default=str)
            body, problems = None, ["no answer from Claude"]
            for _ in range(2):
                out = llm.structured("draft_reply", system, user, REPLY_SCHEMA, max_tokens=900)
                if not out:
                    break
                from ..outreach.compose import format_paragraphs
                body = format_paragraphs(str(out.get("body", "")).strip())
                problems = validate_reply(body)
                if not problems:
                    break
            if problems:
                log.warning("reply card for agent %s not written: %s", w["agent_id"], problems)
                n["failed"] += 1
                continue
            needs_number = msg.get("classification") in ("ASK_FOR_OFFER", "COUNTER_WITH_NUMBER")
            note = (f"Engine's read: {out.get('read', '')}\n" +
                    ("They asked for a number: this is a holding answer only. Comment \"offer N\" on the agent's card when you decide.\n" if needs_number else "") +
                    "Charles wrote this himself (trust ladder stage 3). Comment to change it, edit it, or drag to Approved to send.")
            gid = post_reply_card(conn, cfg, asana, row["id"], body, note=note)
            if gid:
                conn.execute("UPDATE messages SET reply_card=?, reply_draft=? WHERE id=?", (gid, body, msg["id"]))
                pending_agents.add(w["agent_id"])
                n["drafted"] += 1
    except Exception as e:  # noqa: BLE001 — never break the tick
        log.warning("reply writer: %s", e)
        n["error"] = str(e)[:200]
    return n
