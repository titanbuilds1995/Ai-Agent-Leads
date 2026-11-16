"""THE FOLLOW-UP SEQUENCE (Rodrigo, 2026-11-02: "send a good morning email to all of the agents that never responded back to us";
2026-11-16: "anybody that doesn't answer should be getting emails from us. 3 to 4 consecutive emails within a time frame of like
15 days ... if they're still active ... stress test the email ... double check that the logic is fine, and then send it").

Day 0 the first email, then up to three follow-ups in the same Gmail thread: step 1 three days after the first email ("I emailed
you about X the other day, is the seller still open to a conversation"), step 2 five days after step 1 (a different easy question),
step 3 six days after step 2 (the last note: door left open). Four emails inside about 15 days. Before every step the house is looked
up on realtor.com (Mac only): still for sale → the email is written; under contract → the backup lane takes it and the sequence stops;
sold / pulled → the house is excluded and the sequence stops; can't tell → it waits for the next run. Every step is read by the
second reader (second_read.run) against the emails already sent; only a SEND goes to Approved, anything else waits in Needs Me.
The sequence stops for good the moment the agent really answers, or we write them anything else.

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
- This is follow-up {step} of 3 to an agent who has not answered anything yet. {step_note}
- Say when the first email went exactly as "first_email_went_out" puts it (never "last week" for an older one).
- Never repeat or summarize an earlier email, never say "just following up", "bumping this" or "circling back".
- ONE easy question only, and never one already asked in the first email or an earlier follow-up (they are in the input).
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

STEP_NOTES = {
    1: 'One short sentence: Charles with Titan Realty Investments, you emailed about the house the other day (name the street) and '
       'wanted to check back. Then the question: is the seller still open to a conversation on it (or another easy one).',
    2: 'Charles again, about the house (name the street). A different easy question from every earlier email, about '
       'something the photos cannot show (is the seller living there or is it vacant, is there a reason it has sat), never a call '
       'if an earlier email already asked for one.',
    3: 'The last note (say it is the last one from you on this house, plainly, no guilt). Leave the door open: if the seller\'s plans '
       'change we would still like a look, and you can mention we look at other listings of theirs in the area too. One easy question '
       'that is simple to answer yes or no (is it worth checking back in a few weeks).',
}

SEQ_TEMPLATES = {
    2: ["Good morning {first},\n\nCharles again with Titan Realty Investments, about {street}.\n\n"
        "Is the seller still living in the house, or is it vacant right now?"],
    3: ["Good morning {first},\n\nThis is my last note on {street}. If the seller's plans change, we would still like a look, "
        "and the same goes for anything else you have listed in {city}.\n\nIs it worth me checking back with you in a few weeks?"],
}

TEMPLATES = [
    "Good morning {first},\n\nCharles with Titan Realty Investments. I emailed you {when} about {street} and wanted to check back.\n\n"
    "Is the seller still open to a conversation on it?",
    "Good morning {first},\n\nCharles with Titan Realty Investments. I emailed you {when} about {street} and wanted to check back.\n\n"
    "Is there any room from asking on it, or is the seller holding firm for now?",
    "Good morning {first},\n\nCharles with Titan Realty Investments. I reached out about {street} {when}.\n\n"
    "Is there a good time this week for a quick call about it?",
]


def when_phrase(sent_at: str | None) -> str:
    """How long ago the first email went, in words that stay true ("last week" was wrong for a first email sent a month ago)."""
    from datetime import datetime
    try:
        days = (now_utc() - datetime.fromisoformat(str(sent_at).replace("Z", "+00:00"))).days
    except Exception:  # noqa: BLE001
        return "a little while ago"
    return "the other day" if days <= 7 else "last week" if days <= 13 else "a few weeks ago"


def _real_reply(conn, agent_id: int, since: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM messages m JOIN threads t ON t.id=m.thread_id WHERE t.agent_id=? AND m.direction='in' "
        "AND COALESCE(m.classification,'') NOT IN ('BOUNCE','OUT_OF_OFFICE') AND m.created_at>=? LIMIT 1", (agent_id, since)).fetchone())


def _later_outbound(conn, agent_id: int, since: str) -> list[dict[str, Any]]:
    """Everything we sent this agent after the first email (an answer, an offer, our own follow-ups), oldest first."""
    return [db.row_to_dict(r) for r in conn.execute(
        "SELECT id, kind, sent_at, body FROM outreach WHERE agent_id=? AND status='sent' AND kind!='first_touch' AND sent_at>? ORDER BY sent_at",
        (agent_id, since)).fetchall()]


def _steps(conn, outreach_id: int) -> list[dict[str, Any]]:
    conn.execute("CREATE TABLE IF NOT EXISTS follow_up_steps (outreach_id INTEGER NOT NULL, step INTEGER NOT NULL, card_gid TEXT, "
                 "body TEXT, verdict TEXT, created_at TEXT NOT NULL, PRIMARY KEY(outreach_id, step))")
    # the single follow-ups sent before the sequence existed count as step 1
    conn.execute("INSERT OR IGNORE INTO follow_up_steps(outreach_id, step, card_gid, body, created_at) "
                 "SELECT outreach_id, 1, card_gid, body, created_at FROM follow_ups WHERE outreach_id=?", (outreach_id,))
    return [db.row_to_dict(r) for r in conn.execute("SELECT * FROM follow_up_steps WHERE outreach_id=? ORDER BY step", (outreach_id,)).fetchall()]


def _seq(cfg: Any) -> tuple[int, list[int], int]:
    fu = cfg.get("outreach.follow_up") or {}
    gaps = [int(g) for g in (fu.get("sequence_gaps") or [5, 6])]
    return int(fu.get("after_days", 3)), gaps, int(fu.get("max_steps", len(gaps) + 1))


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


def _gone(conn, row: dict[str, Any]) -> bool:
    """Every house on the email is already out (excluded, under contract in the backup lane, or marked sold / pending)."""
    from .board_clean import stored_status
    pids = json.loads(row.get("property_ids") or "[]")
    ps = [db.row_to_dict(r) for r in conn.execute(
        f"SELECT lead_status, listing_status FROM properties WHERE id IN ({','.join('?' * len(pids)) or 'NULL'})", pids).fetchall()]
    return bool(ps) and all((p.get("lead_status") or "") in ("EXCLUDED", "BACKUP") or stored_status(p) for p in ps)


def candidates(conn, cfg: Any, asana, after_days: int | None = None) -> list[dict[str, Any]]:
    """First emails whose agent never really answered and whose next follow-up is due: step 1 `after_days` after the first email,
    each later step `sequence_gaps[k]` days after the previous follow-up went out. Nothing we sent them besides our own follow-ups."""
    first_gap, gaps, max_steps = _seq(cfg)
    if after_days is not None:
        first_gap = int(after_days)
    now = now_utc()
    pending = _pending_agents(conn, cfg, asana)
    out, seen_agents = [], set()
    rows = conn.execute("SELECT * FROM outreach WHERE channel='email' AND kind='first_touch' AND status='sent' AND sent_at IS NOT NULL "
                        "ORDER BY sent_at").fetchall()
    for r in rows:
        row = db.row_to_dict(r)
        aid = row["agent_id"]
        if aid in pending or aid in seen_agents:
            continue
        steps = _steps(conn, row["id"])
        if len(steps) >= max_steps:
            continue
        later = _later_outbound(conn, aid, row["sent_at"])
        if len(later) != len(steps):
            continue            # more: we are in a conversation with them; fewer: the last follow-up has not gone out yet
        last = later[-1]["sent_at"] if later else row["sent_at"]
        gap = first_gap if not steps else gaps[min(len(steps) - 1, len(gaps) - 1)]
        if last > iso(now - timedelta(days=gap)):
            continue
        agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (aid,)).fetchone()) or {}
        if not agent.get("email") or (agent.get("email_confidence") or "") == "bounced" or db.is_dnc(conn, agent_id=aid, email=agent.get("email")):
            continue
        if _real_reply(conn, aid, row["sent_at"]) or _parked(conn, aid) or _gone(conn, row):
            continue
        seen_agents.add(aid)
        out.append({"outreach": row, "agent": agent, "step": len(steps) + 1, "earlier": [x.get("body") or "" for x in later]})
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


def write_follow_up(llm, cfg: Any, agent: dict[str, Any], props: list[dict[str, Any]], first_email: str, seed: int = 0,
                    step: int = 1, earlier: list[str] | None = None, pushback: list[str] | None = None,
                    sent_at: str | None = None) -> tuple[str, str]:
    """(body, source). Claude when reachable, else a template; both must pass the validator."""
    from .outreach.compose import format_paragraphs, may_ask_how_much_work, signature, voice_text
    first = first_name(agent.get("name")) or "there"
    street = ", ".join(p["street"] for p in props) or "your listing"
    sig = signature(cfg)
    if llm is not None and getattr(llm, "available", False):
        from .learning import learned_block
        op = cfg["operator"]
        system = FOLLOW_UP_SYSTEM.format(sender=op["sender_name"].split()[0], company=op["company"], first=first, step=step,
                                         step_note=STEP_NOTES.get(step, STEP_NOTES[1]), voice=voice_text(cfg), learned=learned_block(cfg))
        payload = {"agent_first_name": first, "first_email_we_sent": first_email,
                   "first_email_went_out": when_phrase(sent_at) + (f" ({sent_at[:10]})" if sent_at else ""), "earlier_follow_ups_we_sent": earlier or [],
                   "properties": props, "may_ask_how_much_work": may_ask_how_much_work(props), "signature_block": sig}
        if pushback:
            payload["second_reader_pushback"] = pushback
        user = json.dumps(payload, ensure_ascii=False, default=str)
        for _ in range(2):
            out = llm.structured("follow_up", system, user, FOLLOW_UP_SCHEMA, max_tokens=600)
            if not out:
                break
            body = format_paragraphs(str(out.get("body", "")).strip())
            if not validate_follow_up(body, props):
                return body, "claude"
    if pushback:
        return "", "template"           # a fixed template cannot answer the second reader's pushback
    pool = SEQ_TEMPLATES.get(step) or TEMPLATES
    city = next((p.get("city") for p in props if p.get("city")), "the area")
    body = format_paragraphs(pool[seed % len(pool)].format(first=first, street=street, city=city, when=when_phrase(sent_at)) + "\n\n" + sig)
    return body, "template"


def _case(agent: dict[str, Any], props: list[dict[str, Any]], row: dict[str, Any], earlier: list[str], step: int, status: str) -> dict[str, Any]:
    thread = [{"from": "Charles (us)", "when": (row.get("sent_at") or "")[:10], "text": row.get("body") or ""}]
    thread += [{"from": "Charles (us)", "when": "later", "text": b} for b in earlier]
    return {"who": f"listing agent {agent.get('name')} about {', '.join(p.get('street') or '' for p in props)}",
            "thread": thread,
            "facts": {"agent_answered": "never (no reply to any email above)", "listing_status": status,
                      "this_email": f"follow-up {step} of 3 in the same thread", "properties": props},
            "instruction": ("Rodrigo asked for a short follow-up sequence (three emails over about two weeks) to listing agents who never "
                            "answered, only while the house is still for sale. Check that this follow-up does not repeat a question already "
                            "asked above, does not claim anything we cannot know, and fits where the thread stands.")}


def _check_status(conn, props_full: list[dict[str, Any]], lookup) -> tuple[str, list[tuple[dict[str, Any], str]]]:
    """('active' | 'pending' | 'gone' | 'unknown', per-house states). No lookup (cloud, tests) = what the database says."""
    from .board_clean import ACTIVE, PENDING, stored_status
    states = []
    for p in props_full:
        s = stored_status(p)
        if s is None and lookup is not None:
            try:
                s = lookup(p)
            except Exception as e:  # noqa: BLE001
                log.warning("follow-up status lookup %s: %s", p.get("street"), e)
                s = None
        states.append((p, s if s is not None else (None if lookup is not None else ACTIVE)))
    if any(s == ACTIVE for _, s in states):
        return "active", states
    if any(s is None for _, s in states):
        return "unknown", states
    return ("pending" if any(s == PENDING for _, s in states) else "gone"), states


def post_follow_ups(conn, cfg: Any, asana, llm, after_days: int | None = None, limit: int = 25, approve: bool = False,
                    lookup=None, dry_run: bool = False) -> dict[str, Any]:
    """The next due follow-up per silent agent, as the NEXT EMAIL on the lead's own card (one card per lead, Rodrigo 2026-11-03).
    The house is checked first (`lookup`), the email is second-read, and only a cleared email goes to Approved (approve=True:
    Rodrigo's go-ahead in words); anything else lands in Needs Me."""
    from . import second_read
    from .answers import set_answer
    from .board_clean import PENDING, WORDS
    n: dict[str, Any] = {"posted": 0, "approved": 0, "needs_me": 0, "skipped": 0, "pending": 0, "gone": 0, "unknown": 0,
                         "by_step": {}, "cards": []}
    for c in candidates(conn, cfg, asana, after_days):
        if n["posted"] >= limit:
            break
        row, agent, step = c["outreach"], c["agent"], c["step"]
        pids = json.loads(row.get("property_ids") or "[]")
        full = [db.row_to_dict(r) for r in conn.execute(
            f"SELECT * FROM properties WHERE id IN ({','.join('?' * len(pids)) or 'NULL'})", pids).fetchall()]
        status, states = _check_status(conn, full, lookup)
        if status != "active":
            n[status] += 1
            if dry_run or status == "unknown":
                continue
            from .backup_lane import _enter
            today = iso()[:10]
            for p, s in states:
                if s == PENDING:
                    _enter(conn, p["id"], row["agent_id"], "follow-up check", None, note="found under contract before a follow-up")
                elif s:
                    conn.execute("UPDATE properties SET listing_status=?, lead_status='EXCLUDED', lead_status_at=?, lead_note=? WHERE id=?",
                                 (s, iso(), f"off market: {WORDS.get(s, s)} (realtor.com, checked {today})", p["id"]))
            conn.commit()
            if row.get("asana_task_id"):
                what = "under contract, so it moved to the backup lane" if status == "pending" else "no longer for sale (sold or pulled)"
                try:
                    asana.comment(row["asana_task_id"], f"Follow-ups stopped: realtor.com shows the house is {what} (checked {today}). "
                                                         "No more emails to this agent about it.")
                except Exception as e:  # noqa: BLE001
                    log.warning("follow-up stop comment: %s", e)
            continue
        props = _props(conn, row)
        body, source = write_follow_up(llm, cfg, agent, props, row.get("body") or "", seed=row["id"], step=step, earlier=c["earlier"],
                                       sent_at=row.get("sent_at"))
        if validate_follow_up(body, props):
            n["skipped"] += 1
            continue
        rewrite = (lambda pb, a=agent, pr=props, r=row, st=step, e=c["earlier"]:
                   write_follow_up(llm, cfg, a, pr, r.get("body") or "", seed=r["id"], step=st, earlier=e, pushback=pb,
                                   sent_at=r.get("sent_at"))[0] or None) \
            if source == "claude" else None
        body, review = second_read.run(llm, cfg, _case(agent, props, row, c["earlier"], step, "still for sale (checked today)"), body, rewrite)
        if validate_follow_up(body, props):
            n["skipped"] += 1
            continue
        cleared = second_read.passed(review)
        go = approve and cleared
        if dry_run:
            n["posted"] += 1
            n["cards"].append({"agent": agent.get("name"), "step": step, "body": body, "cleared": cleared, "why": (review or {}).get("why")})
            continue
        sent = (row.get("sent_at") or "")[:10]
        note = (f"FOLLOW-UP {step} of 3 · the first email went out {sent} and {first_name(agent.get('name'))} never answered; the house "
                f"is still for sale. Same thread. Written by {'Charles (Claude)' if source == 'claude' else 'template'}. "
                + second_read.line(review)
                + ("" if go else " Drag to Approved to send it."))
        gid = set_answer(conn, cfg, asana, row["id"], body, note=note.strip(), approved=go)
        if gid:
            _steps(conn, row["id"])
            conn.execute("INSERT OR REPLACE INTO follow_up_steps(outreach_id, step, card_gid, body, verdict, created_at) VALUES(?,?,?,?,?,?)",
                         (row["id"], step, gid, body, (review or {}).get("verdict") or ("off" if review is None else "unread"), iso()))
            if step == 1:
                conn.execute("INSERT OR REPLACE INTO follow_ups(outreach_id, card_gid, body, created_at) VALUES(?,?,?,?)", (row["id"], gid, body, iso()))
            conn.commit()
            n["posted"] += 1
            n["approved" if go else "needs_me"] += 1
            n["by_step"][step] = n["by_step"].get(step, 0) + 1
            n["cards"].append({"agent": agent.get("name"), "step": step, "card": gid, "to": agent.get("email")})
    log.info("follow-ups: %s", {k: v for k, v in n.items() if k != "cards"})
    return n
