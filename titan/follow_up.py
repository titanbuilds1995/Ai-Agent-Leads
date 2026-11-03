"""SECOND TOUCH (Rodrigo, 2026-11-02: "send a good morning email to all of the agents that never responded back to us").

For every first email that went out and got no real answer (bounces and auto-replies don't count), Charles writes a short
"Good morning" follow-up in the SAME Gmail thread: one line that we emailed about the house, one easy question, no price.
Each follow-up is a reply card in Needs Me (his to approve, like every other email); `--approve` puts them straight into
Approved when Rodrigo has said in so many words to send them. One follow-up per first email, never to a bounced or
do-not-contact agent, never while an answer card for that agent is already on the board, and never to a lead Rodrigo parked
("too fresh, wait 45 days").
"""
from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any

from . import db
from .util import first_name, iso, log, now_utc

PARKED_RE = re.compile(r"\btoo fresh\b|\b(wait|give it|hold off|park|follow up in)\b[^.]{0,40}\b\d+\s*(days?|weeks?|months?)", re.I)

FOLLOW_UP_SCHEMA = {"type": "object", "properties": {"body": {"type": "string"}}, "required": ["body"], "additionalProperties": False}

FOLLOW_UP_SYSTEM = """You are {sender}, Junior Acquisitions Agent at {company}. You emailed a listing agent about their listing and
got no answer. Write the short follow-up that goes in the same email thread. Plain text, no subject line.

Rules (Rodrigo's, all hard):
- Line 1 is exactly "Good morning {first}," then a blank line.
- One short sentence that you emailed about the house (name the street) and are checking back. Never repeat the first email,
  never summarize it, never say "just following up" or "bumping this".
- Then ONE easy question that moves things forward (is the seller still open to a conversation, is there any room from asking,
  what's a good time for a quick call). One question only. If the first email already asked it, ask a different one.
- NO price, number, range, percentage, "offer" or dollar figure. Never say we are the buyer or investors ("we buy", "we're
  investors", "serious buyers", "genuinely interested"). Never tell them what doesn't scare or bother us; no "curious", "honestly",
  "potentially", "the bigger question is"; never mention the electrical panel.
- With 4 or more photos never ask how much work the house needs. "I" only for what {sender} does; "we" / "our side" for criteria.
- 25 to 60 words before the signature. No dashes of any kind (no em dash, en dash, " - " or "--"). No emojis, no links.
- End with the signature block from the input, unchanged.

=== VOICE PROTOCOL ===
{voice}
=== END VOICE PROTOCOL ===
{learned}
Return JSON: body (the follow-up, signature included)."""

TEMPLATES = [
    "Good morning {first},\n\nCharles with Titan Realty Investments. I emailed you last week about {street} and wanted to check back.\n\n"
    "Is the seller still open to a conversation on it, and would a quick call this week work for you?",
    "Good morning {first},\n\nCharles with Titan Realty Investments, checking back on {street}.\n\n"
    "Is there any room from asking on it, or is the seller holding firm for now?",
    "Good morning {first},\n\nCharles with Titan Realty Investments. I reached out about {street} a few days ago.\n\n"
    "Is it still available, and what's a good time to reach you for a quick call?",
]


def _real_reply(conn, agent_id: int, since: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM messages m JOIN threads t ON t.id=m.thread_id WHERE t.agent_id=? AND m.direction='in' "
        "AND COALESCE(m.classification,'') NOT IN ('BOUNCE','OUT_OF_OFFICE') AND m.created_at>=? LIMIT 1", (agent_id, since)).fetchone())


def _later_outbound(conn, agent_id: int, since: str) -> bool:
    """Anything we sent this agent after the first email (an answer, an offer, an earlier follow-up)."""
    return bool(conn.execute("SELECT 1 FROM outreach WHERE agent_id=? AND status='sent' AND kind!='first_touch' AND sent_at>? LIMIT 1",
                             (agent_id, since)).fetchone())


def _parked(conn, agent_id: int) -> bool:
    from .card_chat import lead_notes
    return any(PARKED_RE.search(n or "") for n in lead_notes(conn, agent_id))


def _pending_agents(conn, cfg: Any, asana) -> set[int]:
    """Agents with an answer or follow-up already waiting (on their lead card, or a legacy reply card still on the board)."""
    from .answers import pending_agents
    from .approvals import _cfg, needs_me_section, needs_revision_section, parse_card
    a = _cfg(cfg)
    oids = set()
    for sec in (a.get("to_review"), a.get("approved"), needs_revision_section(cfg, asana), needs_me_section(cfg, asana)):
        for t in asana.section_tasks(sec):
            pc = parse_card(t.get("notes") or "")
            if pc and pc.get("reply_to"):
                oids.add(pc["outreach_id"])
    return pending_agents(conn) | {r[0] for o in oids for r in conn.execute("SELECT agent_id FROM outreach WHERE id=?", (o,)).fetchall()}


def candidates(conn, cfg: Any, asana, after_days: int | None = None) -> list[dict[str, Any]]:
    """First emails sent at least `after_days` ago whose agent never really answered and has heard nothing since."""
    fu = cfg.get("outreach.follow_up") or {}
    days = int(fu.get("after_days", 3) if after_days is None else after_days)
    cutoff = iso(now_utc() - timedelta(days=days))
    pending = _pending_agents(conn, cfg, asana)
    done = {r[0] for r in conn.execute("SELECT outreach_id FROM follow_ups").fetchall()}
    out, seen_agents = [], set()
    rows = conn.execute("SELECT * FROM outreach WHERE channel='email' AND kind='first_touch' AND status='sent' AND sent_at<=? "
                        "ORDER BY sent_at", (cutoff,)).fetchall()
    for r in rows:
        row = db.row_to_dict(r)
        aid = row["agent_id"]
        if row["id"] in done or aid in pending or aid in seen_agents:
            continue
        agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (aid,)).fetchone()) or {}
        if not agent.get("email") or (agent.get("email_confidence") or "") == "bounced" or db.is_dnc(conn, agent_id=aid, email=agent.get("email")):
            continue
        if _real_reply(conn, aid, row["sent_at"]) or _later_outbound(conn, aid, row["sent_at"]) or _parked(conn, aid):
            continue
        seen_agents.add(aid)
        out.append({"outreach": row, "agent": agent})
    return out


def _props(conn, row: dict[str, Any]) -> list[dict[str, Any]]:
    pids = json.loads(row.get("property_ids") or "[]")
    return [db.row_to_dict(r) for r in conn.execute(
        f"SELECT street, city, list_price, days_on_market, price_cuts, photos_count FROM properties WHERE id IN ({','.join('?' * len(pids)) or 'NULL'})",
        pids).fetchall()]


def validate_follow_up(body: str, props: list[dict[str, Any]]) -> list[str]:
    from .replies.writer import validate_reply
    problems = validate_reply(body, props)
    if not re.match(r"^Good morning [^\n,]{1,40},\n\n", body):
        problems.append("must open with 'Good morning <name>,' on its own line")
    if re.search(r"\b(just )?(following up|bumping|circling back|touching base)\b", body, re.I):
        problems.append("filler ('following up', 'bumping', 'circling back')")
    return problems


def write_follow_up(llm, cfg: Any, agent: dict[str, Any], props: list[dict[str, Any]], first_email: str, seed: int = 0) -> tuple[str, str]:
    """(body, source). Claude when reachable, else a template; both must pass the validator."""
    from .outreach.compose import format_paragraphs, may_ask_how_much_work, signature, voice_text
    first = first_name(agent.get("name")) or "there"
    street = ", ".join(p["street"] for p in props) or "your listing"
    sig = signature(cfg)
    if llm is not None and getattr(llm, "available", False):
        from .learning import learned_block
        op = cfg["operator"]
        system = FOLLOW_UP_SYSTEM.format(sender=op["sender_name"].split()[0], company=op["company"], first=first,
                                         voice=voice_text(cfg), learned=learned_block(cfg))
        user = json.dumps({"agent_first_name": first, "first_email_we_sent": first_email, "properties": props,
                           "may_ask_how_much_work": may_ask_how_much_work(props), "signature_block": sig}, ensure_ascii=False, default=str)
        for _ in range(2):
            out = llm.structured("follow_up", system, user, FOLLOW_UP_SCHEMA, max_tokens=600)
            if not out:
                break
            body = format_paragraphs(str(out.get("body", "")).strip())
            if not validate_follow_up(body, props):
                return body, "claude"
    body = format_paragraphs(TEMPLATES[seed % len(TEMPLATES)].format(first=first, street=street) + "\n\n" + sig)
    return body, "template"


def post_follow_ups(conn, cfg: Any, asana, llm, after_days: int | None = None, limit: int = 25, approve: bool = False) -> dict[str, Any]:
    """One follow-up per unanswered first email, as the NEXT EMAIL on the lead's own card (one card per lead, Rodrigo 2026-11-03),
    in Needs Me (or Approved with approve=True: Rodrigo's go-ahead in words)."""
    from .answers import set_answer
    n: dict[str, Any] = {"posted": 0, "skipped": 0, "cards": []}
    for i, c in enumerate(candidates(conn, cfg, asana, after_days)[:limit]):
        row, agent = c["outreach"], c["agent"]
        props = _props(conn, row)
        body, source = write_follow_up(llm, cfg, agent, props, row.get("body") or "", seed=row["id"])
        if validate_follow_up(body, props):
            n["skipped"] += 1
            continue
        sent = (row.get("sent_at") or "")[:10]
        note = (f"FOLLOW-UP · the first email went out {sent} and {first_name(agent.get('name'))} never answered. This goes in the same "
                f"thread. Written by {'Charles (Claude)' if source == 'claude' else 'template'}."
                + ("" if approve else " Drag to Approved to send it."))
        gid = set_answer(conn, cfg, asana, row["id"], body, note=note, approved=approve)
        if gid:
            conn.execute("INSERT OR REPLACE INTO follow_ups(outreach_id, card_gid, body, created_at) VALUES(?,?,?,?)", (row["id"], gid, body, iso()))
            conn.commit()
            n["posted"] += 1
            n["cards"].append({"agent": agent.get("name"), "card": gid, "to": agent.get("email")})
    log.info("follow-ups: %s", n)
    return n
