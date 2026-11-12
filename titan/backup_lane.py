"""THE BACKUP LANE: pending houses (Rodrigo, 2026-11-12: "properties that match our criteria and are pending, maybe pending
for a while, we should email the agents and tell them that if anything falls through we are interested and have a follow up
sequence with those agents. Those are the hottest leads. When others have stopped reaching out and they are ripe").

Every morning (run-daily step `backup`, by hand `make backup-lane`; `make backup-list` only prints):

1. FIND houses that fit our criteria (the engine rated them HOT or WARM) and are now under contract:
   - houses the board clean-up or a feed already marked pending;
   - houses whose agent told us "it's under contract" in a reply;
   - the other HOT / WARM houses we never emailed, looked up on realtor.com a batch a day (free, Mac only). A house found
     sold or pulled is taken out of the pipeline on the way; a pending one enters this lane.
   A house in the lane has lead_status BACKUP: the normal first email is never written for it.
2. WAIT until it has been pending `min_pending_days` (the other buyers have stopped calling the agent), then write the
   BACKUP EMAIL: Charles, one short note that if the current deal does not close we would like a look, one easy question,
   never a price. It is a card in Fresh Leads like every first email; nothing sends without Rodrigo's drag to Approved.
3. FOLLOW UP in the same thread every `follow_up_days` while it is still pending and the agent has not answered, up to
   `max_follow_ups` times ("Good morning … is it still on track to close?"), each one the NEXT EMAIL on the lead's own card
   in Needs Me.
4. WATCH the status every `recheck_days`: SOLD → the sequence stops; BACK ON THE MARKET → 🔥 on the card, Needs Me, call the
   agent now (a house never emailed goes back into the normal lane flagged back-on-market).
Every email here is written by one Claude call and read by the second reader (titan/second_read.py) before it reaches a card."""
from __future__ import annotations

import json
import re
import time
from datetime import timedelta
from typing import Any

from . import db
from .board_clean import ACTIVE, OFF_MARKET, PENDING, SOLD, WORDS, Lookup, homeharvest_lookup, stored_status
from .util import first_name, iso, log, now_utc, parse_iso, today_et

WATCHING, CARD, ANSWERED, BACK, CLOSED, DROPPED = "WATCHING", "CARD", "ANSWERED", "BACK_ON_MARKET", "CLOSED", "DROPPED"
OPEN_STATES = (WATCHING, CARD)
LEAD_STATUS = "BACKUP"
AGENT_SAID_PENDING_RE = re.compile(r"\b(under contract|pending|contingent|accepted (an )?offer|in escrow|went (to|under) contract|has a contract)\b", re.I)
AGENT_SAID_SOLD_RE = re.compile(r"\b(sold|closed|has closed|already closed)\b", re.I)
NEVER_AGAIN = ("STOP", "NOT_INTERESTED")
INSTRUCTION = ("Rodrigo's standing instruction (2026-11-12): for houses that fit our criteria and are under contract, email the listing agent "
               "that if the current deal falls through we are interested, and keep following up while it is pending. No price, ever.")

SCHEMA = {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["subject", "body"],
          "additionalProperties": False}

BACKUP_SYSTEM = """You are {sender}, Junior Acquisitions Agent at {company}. A listing that fits what we look for is UNDER CONTRACT with
someone else. You write the listing agent one short email so that we are the first call if that deal does not close. Deals fall
through all the time (inspection, financing) and by now nobody else is writing this agent. Plain text.

Rules (Rodrigo's, all hard):
- Line 1 is the greeting alone ("Hey {first},"), then a blank line. Short paragraphs of 1 to 3 sentences.
- First paragraph is the intro: "{sender} with {company}. I saw your listing on <street> is under contract." (when the input says
  the agent told us so, say "You mentioned <street> went under contract." instead of "I saw").
- Then one or two plain sentences: if anything changes with the current deal, we would like a look at it and we are easy to reach.
- Then ONE easy question in its own paragraph (would the seller want a backup in place, can you keep us in mind if it comes
  back, is it fine if I check in with you again). One question only.
- NEVER a price, number, range, percentage, "offer" or "cash". Never say we are the buyer or investors ("we buy", "serious
  buyers", "genuinely interested"); "we're looking for properties in <city>" is how we say it.
- Never guess why it might fall through, never say anything about the other buyer, never wish the deal fails, never ask how much
  work the house needs. Never promise a date, a timeframe, proof of funds, a signature or an attachment.
- No "curious", "honestly", "potentially", "just", "I'll keep this short", "stood out", "caught my eye". Never recite the listing.
- 35 to 80 words before the signature. No dashes of any kind (no em dash, en dash, " - " or "--"). No emojis, no links.
- End with the signature block from the input, unchanged.
- subject: under 60 characters, plain, names the street (e.g. "NW 11th St, if anything changes").

=== VOICE PROTOCOL ===
{voice}
=== END VOICE PROTOCOL ===
{learned}
Return JSON: subject, body (signature included)."""

CHECKIN_SYSTEM = """You are {sender}, Junior Acquisitions Agent at {company}. A while ago you emailed a listing agent that we would like a
look at their listing if the deal it is under contract in does not close. The house is still pending and the agent has not answered.
Write the short check-in that goes in the same email thread. Plain text, no subject.

Rules (Rodrigo's, all hard):
- Line 1 is exactly "Good morning {first}," then a blank line.
- One short sentence that you are checking in on <street> (name the street). Never repeat or summarize an earlier email, never say
  "just following up", "bumping", "circling back" or "touching base".
- Then ONE easy question, different from the ones already asked in the thread (is it still on track to close, did the inspection
  period pass, has anything changed, would the seller want a backup in place). One question only.
- NEVER a price, number, range, percentage, "offer" or "cash". Never say we are the buyer or investors. Never guess why it might
  fall through, nothing about the other buyer. Never promise a date, a timeframe, proof of funds, a signature or an attachment.
- 20 to 50 words before the signature. No dashes of any kind. No emojis, no links.
- End with the signature block from the input, unchanged.

=== VOICE PROTOCOL ===
{voice}
=== END VOICE PROTOCOL ===
{learned}
Return JSON: subject (leave ""), body (signature included)."""

TEMPLATES = [
    ("{street}, if anything changes",
     "Hey {first},\n\nCharles with Titan Realty Investments. {saw} {street} {went} under contract.\n\n"
     "If anything changes with the current deal, we would like a look at it. We're looking for properties in {city} and we are easy to reach.\n\n"
     "Would the seller want a backup in place in case this one does not close?"),
    ("{street}, keep us in mind",
     "Hey {first},\n\nCharles with Titan Realty Investments. {saw} {street} {went} under contract.\n\n"
     "If it does not close for any reason, we would like to hear from you first. We're looking for properties in {city}.\n\n"
     "Can you keep us in mind if it comes back?"),
    ("Checking on {street}",
     "Hey {first},\n\nCharles with Titan Realty Investments. {saw} {street} {went} under contract.\n\n"
     "We're looking for properties in {city}, and if the current deal changes we would like a look at this one.\n\n"
     "Is it fine if I check in with you again while it is pending?"),
]
CHECKINS = [
    "Good morning {first},\n\nCharles with Titan Realty Investments, checking in on {street}.\n\nIs the sale still on track to close, or has anything changed?",
    "Good morning {first},\n\nCharles with Titan Realty Investments. I wanted to check in on {street}.\n\nDid it get through the inspection period, or is anything still open?",
    "Good morning {first},\n\nCharles with Titan Realty Investments, checking on {street} once more.\n\nHas it closed, or would the seller want a backup in place?",
]


def _c(cfg: Any) -> dict[str, Any]:
    return cfg.get("outreach.backup_lane", {}) or {}


def _ago(days: float, now=None) -> str:
    return iso((now or now_utc()) - timedelta(days=days))


def _prop(conn, pid: int) -> dict[str, Any] | None:
    return db.row_to_dict(conn.execute(
        "SELECT p.*, v.rating, v.lane, v.arv, v.mao, v.arv_method, v.comp_count, v.comp_grade, v.repair_tier, v.repair_cost, s.score "
        "FROM properties p LEFT JOIN valuations v ON v.property_id=p.id LEFT JOIN scores s ON s.property_id=p.id WHERE p.id=?", (pid,)).fetchone())


def _enter(conn, pid: int, agent_id: int | None, source: str, since: str | None, note: str = "") -> bool:
    if conn.execute("SELECT 1 FROM backup_leads WHERE property_id=?", (pid,)).fetchone():
        return False
    conn.execute("INSERT INTO backup_leads(property_id,agent_id,state,source,pending_since,last_status,last_check_at,note,created_at,updated_at) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?)", (pid, agent_id, WATCHING, source, since or iso(), PENDING, iso(), note, iso(), iso()))
    conn.execute("UPDATE properties SET listing_status='pending', lead_status=?, lead_status_at=?, lead_note=? WHERE id=?",
                 (LEAD_STATUS, iso(), f"backup lane: under contract, watching ({source})", pid))
    return True


def _set(conn, pid: int, **kw) -> None:
    kw["updated_at"] = iso()
    conn.execute(f"UPDATE backup_leads SET {', '.join(k + '=?' for k in kw)} WHERE property_id=?", (*kw.values(), pid))


# ---------- 1. find ----------

def discover(conn, cfg: Any, lookup: Lookup | None, budget: int, sleep_s: float = 0.0) -> dict[str, int]:
    n = {"from_board": 0, "from_agents": 0, "looked_up": 0, "found_pending": 0, "found_gone": 0, "lookup_errors": 0}
    ratings = tuple(_c(cfg).get("ratings") or ("HOT", "WARM"))
    q = ",".join("?" * len(ratings))
    # (a) already marked pending (the Fresh Leads clean-up, a feed)
    for r in conn.execute(f"SELECT p.* FROM properties p JOIN valuations v ON v.property_id=p.id WHERE v.rating IN ({q}) "
                          f"AND p.listing_status IS NOT NULL AND COALESCE(p.lead_note,'') NOT LIKE 'excluded:%' "
                          f"AND p.id NOT IN (SELECT property_id FROM backup_leads)", ratings).fetchall():
        p = dict(r)
        if stored_status(p) == PENDING and p.get("lead_status") in ("EXCLUDED", "NONE", "QUEUED", None):
            n["from_board"] += _enter(conn, p["id"], p.get("agent_id"), "board clean-up", p.get("lead_status_at"))
    # (b) the agent told us it went under contract
    for m in conn.execute("SELECT m.body, m.created_at, t.agent_id, t.property_ids FROM messages m JOIN threads t ON t.id=m.thread_id "
                          "WHERE m.direction='in' AND m.classification='SOLD_OR_PENDING' ORDER BY m.id").fetchall():
        body = m["body"] or ""
        if not AGENT_SAID_PENDING_RE.search(body) or AGENT_SAID_SOLD_RE.search(body):
            continue
        for pid in json.loads(m["property_ids"] or "[]"):
            p = _prop(conn, pid)
            if p and (p.get("lead_note") or "")[:9] != "excluded:":
                n["from_agents"] += _enter(conn, pid, m["agent_id"], "the agent told us", m["created_at"], note=body[:300])
    # (c) look up the HOT / WARM houses we never emailed, oldest check first
    if lookup is None or budget <= 0:
        return n
    emailed = set()
    for r in conn.execute("SELECT property_ids FROM outreach WHERE status IN ('sent','pending_approval','draft','queued')").fetchall():
        emailed.update(json.loads(r[0] or "[]"))
    rows = conn.execute(f"SELECT p.* FROM properties p JOIN valuations v ON v.property_id=p.id LEFT JOIN status_checks c ON c.property_id=p.id "
                        f"WHERE v.rating IN ({q}) AND COALESCE(p.lead_status,'NONE') IN ('NONE','QUEUED') "
                        f"AND (c.checked_at IS NULL OR c.checked_at<?) ORDER BY c.checked_at IS NOT NULL, c.checked_at, "
                        f"CASE v.rating WHEN 'HOT' THEN 0 ELSE 1 END LIMIT ?", (*ratings, _ago(float(_c(cfg).get("lookup_every_days", 7))), budget * 3)).fetchall()
    for r in rows:
        p = dict(r)
        if p["id"] in emailed or n["looked_up"] >= budget or n["lookup_errors"] >= 5:
            continue
        try:
            if n["looked_up"] and sleep_s:
                time.sleep(sleep_s)
            n["looked_up"] += 1
            st = lookup(p)
        except Exception as e:  # noqa: BLE001 — a failed lookup changes nothing
            n["lookup_errors"] += 1
            log.warning("backup lane: lookup %s failed: %s", p.get("street"), e)
            continue
        conn.execute("INSERT INTO status_checks(property_id,status,checked_at) VALUES(?,?,?) ON CONFLICT(property_id) DO UPDATE SET "
                     "status=excluded.status, checked_at=excluded.checked_at", (p["id"], st, iso()))
        if st == PENDING:
            n["found_pending"] += _enter(conn, p["id"], p.get("agent_id"), "realtor.com", None)
        elif st in (SOLD, OFF_MARKET):
            conn.execute("UPDATE properties SET listing_status=?, lead_status='EXCLUDED', lead_status_at=?, lead_note=? WHERE id=?",
                         (st, iso(), f"off market: {WORDS[st]} (realtor.com, checked {today_et()})", p["id"]))
            n["found_gone"] += 1
    return n


# ---------- 4. watch ----------

def _comment(asana, gid: str | None, text: str) -> None:
    if asana is None or not gid:
        return
    try:
        asana.comment(gid, text)
    except Exception as e:  # noqa: BLE001
        log.warning("backup lane: comment on %s: %s", gid, e)


def _card_of(conn, lead: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if not lead.get("outreach_id"):
        return None, None
    o = db.row_to_dict(conn.execute("SELECT * FROM outreach WHERE id=?", (lead["outreach_id"],)).fetchone())
    return o, (o or {}).get("asana_task_id")


def recheck(conn, cfg: Any, asana, lookup: Lookup | None, budget: int, sleep_s: float = 0.0) -> dict[str, Any]:
    """Is each house in the lane still pending? Sold → the sequence stops. Back on the market → the hottest lead on the board."""
    n: dict[str, Any] = {"checked": 0, "still_pending": 0, "closed": 0, "back_on_market": [], "errors": 0}
    if lookup is None:
        return n
    from .approvals import needs_me_section
    a = cfg.get("outreach.asana", {}) or {}
    due = _ago(float(_c(cfg).get("recheck_days", 2)))
    for r in conn.execute(f"SELECT * FROM backup_leads WHERE state IN ({','.join('?' * len(OPEN_STATES))}) AND COALESCE(last_check_at,'')<? "
                          "ORDER BY last_check_at LIMIT ?", (*OPEN_STATES, due, budget)).fetchall():
        lead = dict(r)
        p = _prop(conn, lead["property_id"])
        if not p or n["errors"] >= 5:
            continue
        try:
            if n["checked"] and sleep_s:
                time.sleep(sleep_s)
            n["checked"] += 1
            st = lookup(p)
        except Exception as e:  # noqa: BLE001
            n["errors"] += 1
            log.warning("backup lane: recheck %s failed: %s", p.get("street"), e)
            continue
        _set(conn, p["id"], last_check_at=iso(), last_status=st or lead.get("last_status"))
        o, gid = _card_of(conn, lead)
        sent = bool(o and o["status"] == "sent")
        if st in (SOLD, OFF_MARKET):
            _set(conn, p["id"], state=CLOSED, note=f"{WORDS[st]} ({today_et()})")
            conn.execute("UPDATE properties SET listing_status=?, lead_status='EXCLUDED', lead_status_at=?, lead_note=? WHERE id=?",
                         (st, iso(), f"off market: {WORDS[st]} (realtor.com, checked {today_et()})", p["id"]))
            if o and not sent and o["status"] == "pending_approval":
                conn.execute("UPDATE outreach SET status='skipped' WHERE id=?", (o["id"],))
                try:
                    if asana is not None and gid and a.get("skip"):
                        asana.move(gid, a["skip"])
                except Exception as e:  # noqa: BLE001
                    log.warning("backup lane: move %s to Skip: %s", gid, e)
            if o and o.get("answer_draft"):
                conn.execute("UPDATE outreach SET answer_draft=NULL, answer_note=NULL, answer_state=NULL WHERE id=?", (o["id"],))
            _comment(asana, gid, f"Backup lane: {p.get('street')} is {WORDS[st]} (realtor.com, checked {today_et()}). The deal closed, so the "
                                 "check-ins stop here. Nothing else goes to this agent about it.")
            n["closed"] += 1
        elif st == ACTIVE:
            _set(conn, p["id"], state=BACK, note=f"back on the market ({today_et()})")
            agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (lead.get("agent_id") or p.get("agent_id"),)).fetchone()) or {}
            if o and o["status"] in ("sent", "pending_approval"):
                conn.execute("UPDATE properties SET listing_status='active', back_on_market=1, lead_status='NEEDS_CALL', lead_status_at=?, lead_note=? WHERE id=?",
                             (iso(), "BACK ON THE MARKET after being under contract: call the agent", p["id"]))
                _comment(asana, gid, f"🔥 BACK ON THE MARKET: {p.get('street')} was under contract and is for sale again (realtor.com, checked "
                                     f"{today_et()}). " + ("The agent already has our email. " if sent else "Our email had not gone out yet. ")
                                     + f"Call {first_name(agent.get('name')) or 'the agent'} now: {agent.get('phone') or 'no phone on file'}.")
                try:
                    if asana is not None and gid:
                        asana.move(gid, needs_me_section(cfg, asana))
                except Exception as e:  # noqa: BLE001
                    log.warning("backup lane: move %s to Needs Me: %s", gid, e)
            else:       # never emailed: back into the normal lane, flagged back-on-market (the engine's "ideal" seller)
                conn.execute("UPDATE properties SET listing_status='active', back_on_market=1, lead_status='NONE', lead_status_at=?, lead_note=? WHERE id=?",
                             (iso(), "back on the market after being under contract", p["id"]))
            n["back_on_market"].append({"street": p.get("street"), "city": p.get("city"), "agent": agent.get("name"), "phone": agent.get("phone"),
                                        "emailed": sent})
        else:
            n["still_pending"] += 1
    return n


# ---------- 2. the backup email ----------

def validate_backup(subject: str, body: str, props: list[dict[str, Any]]) -> list[str]:
    from .outreach.compose import validate_email
    problems = [x for x in validate_email(subject, body, props) if "photos" not in x.lower()]
    if not re.search(r"\b(under contract|pending)\b", body, re.I):
        problems.append("does not say the house is under contract")
    if re.search(r"\b(falls? through|fall apart|other buyer|their buyer|financing fails|hope)\b", body, re.I):
        problems.append("guesses at / talks about the other deal failing")
    return problems


def _write(llm, cfg: Any, system_tpl: str, purpose: str, payload: dict[str, Any], first: str, check, pushback: list[str] | None = None):
    """One Claude draft that passes `check` (two tries), or None."""
    if llm is None or not getattr(llm, "available", False):
        return None
    from .learning import learned_block
    from .outreach.compose import format_paragraphs, voice_text
    op = cfg["operator"]
    system = system_tpl.format(sender=op["sender_name"].split()[0], company=op["company"], first=first, voice=voice_text(cfg),
                               learned=learned_block(cfg))
    if pushback:
        payload = {**payload, "second_reader_pushback_fix_this": pushback}
    user = json.dumps(payload, ensure_ascii=False, default=str)
    for _ in range(2):
        out = llm.structured(purpose, system, user, SCHEMA, max_tokens=700)
        if not isinstance(out, dict):
            return None
        subject, body = str(out.get("subject", "")).strip(), format_paragraphs(str(out.get("body", "")).strip())
        if not check(subject, body):
            return subject, body
    return None


def write_backup(llm, cfg: Any, agent: dict[str, Any], p: dict[str, Any], lead: dict[str, Any], seed: int = 0,
                 pushback: list[str] | None = None) -> tuple[str, str, str]:
    """(subject, body, source)."""
    from .outreach.compose import format_paragraphs, signature, street_name
    first = first_name(agent.get("name")) or "there"
    told = lead.get("source") == "the agent told us"
    payload = {"agent_first_name": first, "property": {k: p.get(k) for k in ("street", "city", "zip", "property_type", "beds", "baths", "sqft", "year_built")},
               "the_agent_told_us_it_is_under_contract": told, "what_the_agent_wrote": lead.get("note") if told else None,
               "signature_block": signature(cfg)}
    got = _write(llm, cfg, BACKUP_SYSTEM, "backup_touch", payload, first, lambda s, b: validate_backup(s, b, [p]), pushback)
    if got:
        return got[0], got[1], "claude"
    subj, body = TEMPLATES[seed % len(TEMPLATES)]
    ctx = {"first": first, "street": street_name(p.get("street")) or p.get("street"), "city": p.get("city") or "the area",
           "saw": "You mentioned" if told else "I saw your listing on", "went": "went" if told else "is"}
    return subj.format(**ctx), format_paragraphs(body.format(**ctx) + "\n\n" + signature(cfg)), "template"


def _case(p: dict[str, Any], agent: dict[str, Any], lead: dict[str, Any], thread: list[dict[str, Any]]) -> dict[str, Any]:
    return {"who": f"listing agent {agent.get('name')} about {p.get('street')}, {p.get('city')}", "thread": thread,
            "facts": {"listing_status": "under contract (pending)", "how_we_know": lead.get("source"), "pending_since_about": (lead.get("pending_since") or "")[:10],
                      "what_the_agent_wrote": lead.get("note") or None, "asking": p.get("list_price")},
            "instruction": INSTRUCTION}


def _emailable(conn, cfg: Any, agent: dict[str, Any]) -> bool:
    from .outreach.send import GENERIC_MAILBOX_RE
    email = agent.get("email") or ""
    if not email or GENERIC_MAILBOX_RE.match(email) or (agent.get("email_confidence") or "") in ("bounced", "low"):
        return False
    if db.is_dnc(conn, agent_id=agent["id"], phone=agent.get("phone"), email=email):
        return False
    return not conn.execute("SELECT 1 FROM messages m JOIN threads t ON t.id=m.thread_id WHERE t.agent_id=? AND m.direction='in' "
                            f"AND m.classification IN ({','.join('?' * len(NEVER_AGAIN))}) LIMIT 1", (agent["id"], *NEVER_AGAIN)).fetchone()


def draft_cards(conn, cfg: Any, asana, llm, dry_run: bool = False) -> dict[str, Any]:
    """The backup email for every house that has been pending long enough: a card in Fresh Leads (his to approve)."""
    from . import second_read
    from .outreach.send import _recent_contact
    c = _c(cfg)
    n: dict[str, Any] = {"ready": 0, "cards": 0, "waiting_days": 0, "no_email": 0, "agent_busy": 0, "held": 0, "list": []}
    left = int(c.get("max_cards_per_day", 10))
    seen_agents: set[int] = set()
    cutoff = _ago(float(c.get("min_pending_days", 7)))
    for r in conn.execute("SELECT * FROM backup_leads WHERE state=? ORDER BY pending_since", (WATCHING,)).fetchall():
        lead = dict(r)
        if (lead.get("pending_since") or "") > cutoff:
            n["waiting_days"] += 1
            continue
        if (lead.get("note") or "").startswith("second reader held") and (lead.get("last_touch_at") or "") > _ago(7):
            n["held"] += 1                              # the reader said no this week: not asked again every morning
            continue
        p = _prop(conn, lead["property_id"])
        aid = lead.get("agent_id") or (p or {}).get("agent_id")
        agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (aid,)).fetchone()) if aid else None
        if not p or not agent or not _emailable(conn, cfg, agent):
            n["no_email"] += 1
            continue
        if aid in seen_agents or _recent_contact(conn, aid, "email", int(cfg["outreach"].get("agent_cooldown_days", 7))):
            n["agent_busy"] += 1                       # one email per agent at a time; this house waits its turn
            continue
        n["ready"] += 1
        n["list"].append({"street": p.get("street"), "city": p.get("city"), "agent": agent.get("name"), "pending_since": (lead.get("pending_since") or "")[:10],
                          "source": lead.get("source")})
        if dry_run or left <= 0 or asana is None:
            continue
        seen_agents.add(aid)
        subject, body, source = write_backup(llm, cfg, agent, p, lead, seed=p["id"])
        if validate_backup(subject, body, [p]):
            n["held"] += 1
            continue
        state = {"subject": subject, "source": source}

        def rewrite(pushback, _state=state, _agent=agent, _p=p, _lead=lead):
            s, b, src = write_backup(llm, cfg, _agent, _p, _lead, pushback=pushback)
            if src != "claude":
                return None
            _state.update(subject=s, source=src)
            return b
        body, review = second_read.run(llm, cfg, _case(p, agent, lead, []), body, rewrite)
        if review and review.get("verdict") == "HOLD":
            n["held"] += 1                              # the second reader says no email should go: nothing reaches the board
            _set(conn, p["id"], note=f"second reader held the backup email: {review.get('why')}"[:400], last_touch_at=iso())
            continue
        with db.tx(conn):
            cur = conn.execute("INSERT INTO outreach(agent_id,property_ids,channel,kind,status,subject,body,variant,to_address,run_date,created_at,analysis) "
                               "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                               (aid, json.dumps([p["id"]]), "email", "backup_touch", "pending_approval", state["subject"], body, f"backup:{state['source']}",
                                agent["email"], today_et(), iso(),
                                json.dumps({"angle": "backup (under contract)", "hypothesis": f"Under contract since about {(lead.get('pending_since') or '')[:10]} "
                                            f"({lead.get('source')}). If it does not close we want to be the first call."})))
        try:
            from .approvals import post_card
            gid = post_card(conn, cfg, asana, agent, [p], {"subject": state["subject"], "body": body, "analysis": {
                "angle": "backup (under contract)", "hypothesis": "Under contract: if this deal does not close, we want to be the agent's first call."}}, cur.lastrowid)
        except Exception as e:  # noqa: BLE001
            conn.execute("UPDATE outreach SET status='failed', error=? WHERE id=?", (f"asana: {e}"[:400], cur.lastrowid))
            log.exception("backup lane: card for %s failed: %s", p.get("street"), e)
            continue
        try:
            if hasattr(asana, "rename"):
                from .house_card import VERSION, card_name
                asana.rename(gid, f"⏳ PENDING · backup · {card_name(agent, [p])}")
                conn.execute("INSERT OR REPLACE INTO card_format(gid, version, at) VALUES(?,?,?)", (gid, VERSION, iso()))   # keep the name
        except Exception as e:  # noqa: BLE001
            log.warning("backup lane: rename %s: %s", gid, e)
        _comment(asana, gid, f"Backup lane: {p.get('street')} is under contract ({lead.get('source')}, since about {(lead.get('pending_since') or '')[:10]}). "
                             f"This email asks to be the first call if that deal does not close. After it goes out, a check-in is drafted every "
                             f"{c.get('follow_up_days', 10)} days while it stays pending (up to {c.get('max_follow_ups', 3)}), and the card lights up if it "
                             f"comes back on the market. Written by {'Charles (Claude)' if state['source'] == 'claude' else 'template'}."
                             + (f"\n{second_read.line(review)}" if review is not None else ""))
        _set(conn, p["id"], state=CARD, outreach_id=cur.lastrowid, agent_id=aid, last_touch_at=iso())
        n["cards"] += 1
        left -= 1
    return n


# ---------- 3. the sequence ----------

def check_ins(conn, cfg: Any, asana, llm, dry_run: bool = False, now=None) -> dict[str, Any]:
    from . import second_read
    from .answers import pending as answer_pending, set_answer
    from .follow_up import validate_follow_up
    from .outreach.compose import format_paragraphs, signature, street_name
    c = _c(cfg)
    n: dict[str, Any] = {"answered": 0, "dropped": 0, "due": 0, "posted": 0, "done": 0, "held": 0}
    every, most = float(c.get("follow_up_days", 10)), int(c.get("max_follow_ups", 3))
    for r in conn.execute("SELECT * FROM backup_leads WHERE state=? ORDER BY last_touch_at", (CARD,)).fetchall():
        lead = dict(r)
        o, gid = _card_of(conn, lead)
        if not o or o["status"] in ("skipped", "failed"):
            _set(conn, lead["property_id"], state=DROPPED, note="the backup email was skipped")
            n["dropped"] += 1
            continue
        if o["status"] != "sent" or not o.get("sent_at"):
            continue                                              # still his to approve
        if conn.execute("SELECT 1 FROM messages m JOIN threads t ON t.id=m.thread_id WHERE t.agent_id=? AND m.direction='in' AND m.created_at>=? "
                        "AND COALESCE(m.classification,'') NOT IN ('BOUNCE','OUT_OF_OFFICE') LIMIT 1", (o["agent_id"], o["sent_at"])).fetchone():
            _set(conn, lead["property_id"], state=ANSWERED, note="the agent answered: the conversation continues on the lead card")
            n["answered"] += 1
            continue
        if (lead.get("follow_ups") or 0) >= most:
            n["done"] += 1
            continue
        last = max(o["sent_at"], lead.get("last_touch_at") or "")
        if last > _ago(every, now) or answer_pending(conn, o["id"]):
            continue
        n["due"] += 1
        if dry_run or asana is None:
            continue
        p = _prop(conn, lead["property_id"])
        agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (o["agent_id"],)).fetchone()) or {}
        if not p or not _emailable(conn, cfg, agent):
            continue
        first = first_name(agent.get("name")) or "there"
        thread = [{"from": "Charles (us)", "when": m["created_at"], "text": m["body"]} for m in conn.execute(
            "SELECT m.body, m.created_at FROM messages m JOIN threads t ON t.id=m.thread_id WHERE t.agent_id=? AND m.direction='out' AND m.created_at>=? "
            "ORDER BY m.id", (o["agent_id"], o["sent_at"])).fetchall()] or [{"from": "Charles (us)", "when": o["sent_at"], "text": o["body"]}]
        k = lead.get("follow_ups") or 0

        def write(pushback=None, _p=p, _first=first, _thread=thread, _k=k):
            got = _write(llm, cfg, CHECKIN_SYSTEM, "backup_check_in",
                         {"agent_first_name": _first, "street": _p.get("street"), "emails_we_already_sent": [t["text"] for t in _thread],
                          "check_in_number": _k + 1, "signature_block": signature(cfg)}, _first,
                         lambda s, b: validate_follow_up(b, [_p]), pushback)
            return got[1] if got else None
        body, source = write(), "claude"
        if not body:
            body = format_paragraphs(CHECKINS[k % len(CHECKINS)].format(first=first, street=street_name(p.get("street")) or p.get("street"))
                                     + "\n\n" + signature(cfg))
            source = "template"
        if validate_follow_up(body, [p]):
            n["held"] += 1
            continue
        body, review = second_read.run(llm, cfg, _case(p, agent, lead, thread), body, write)
        if review and review.get("verdict") == "HOLD":
            n["held"] += 1
            _set(conn, lead["property_id"], last_touch_at=iso(), note=f"second reader held check-in {k + 1}: {review.get('why')}"[:400])
            _comment(asana, gid, f"Backup lane: check-in {k + 1} was not drafted. {second_read.line(review)}")
            continue
        note = (f"BACKUP CHECK-IN {k + 1} of {most} · {p.get('street')} is still under contract and {first} has not answered. Same thread. "
                f"Written by {'Charles (Claude)' if source == 'claude' else 'template'}. Drag to Approved to send it."
                + (f" {second_read.line(review)}" if review is not None else ""))
        if set_answer(conn, cfg, asana, o["id"], body, note=note):
            _set(conn, lead["property_id"], follow_ups=k + 1, last_touch_at=iso())
            n["posted"] += 1
    return n


# ---------- the run ----------

def run(conn, cfg: Any, asana=None, llm=None, lookup: Lookup | None = None, dry_run: bool = False, lookups: bool = True) -> dict[str, Any]:
    """One morning of the lane. Never raises past a stage; a failed lookup changes nothing."""
    db.init_db(conn)
    c = _c(cfg)
    if not c.get("enabled", True):
        return {"skipped": "outreach.backup_lane.enabled is off"}
    if lookups and lookup is None:
        try:
            import homeharvest  # noqa: F401
            lookup = homeharvest_lookup(cfg)
        except ImportError:
            lookup = None                                # the cloud: no realtor.com; work from what the database already knows
    budget, pause = int(c.get("lookups_per_day", 150)), float(c.get("pause_seconds", 1.0))
    out: dict[str, Any] = {"lookups": lookup is not None}
    if dry_run:
        conn.execute("BEGIN")                            # --list: everything it works out is thrown away at the end
    stages = [("watch", lambda: recheck(conn, cfg, None if dry_run else asana, None if dry_run else lookup, budget, pause)),
              ("found", lambda: discover(conn, cfg, None if dry_run else lookup, max(0, budget - (out.get("watch") or {}).get("checked", 0)), pause)),
              ("emails", lambda: draft_cards(conn, cfg, asana, llm, dry_run)),
              ("check_ins", lambda: check_ins(conn, cfg, asana, llm, dry_run))]
    for name, fn in stages:
        try:
            out[name] = fn()
        except Exception as e:  # noqa: BLE001 — the lane must never stop the morning run
            log.exception("backup lane: %s failed: %s", name, e)
            out[name] = {"error": str(e)[:200]}
    out["lane"] = dict(conn.execute("SELECT state, COUNT(*) FROM backup_leads GROUP BY state").fetchall())
    if dry_run:
        conn.execute("ROLLBACK")
        return out
    try:
        conn.execute("INSERT INTO daily_runs(run_date,step,started_at,finished_at,status,counts) VALUES(?,?,?,?,?,?)",
                     (today_et(), "backup_lane", iso(), iso(), "ok", json.dumps({k: v for k, v in out.items() if k != "emails"} |
                                                                               {"emails": {k: v for k, v in (out.get("emails") or {}).items() if k != "list"}}, default=str)))
    except Exception as e:  # noqa: BLE001
        log.warning("backup lane: run log: %s", e)
    return out


def summary_lines(conn, cfg: Any) -> list[str]:
    """Plain lines for the morning report ([] while the lane is empty)."""
    try:
        lane = dict(conn.execute("SELECT state, COUNT(*) FROM backup_leads GROUP BY state").fetchall())
    except Exception:  # noqa: BLE001
        return []
    if not lane:
        return []
    c = _c(cfg)
    out = [f"Pending houses we are behind: {lane.get(WATCHING, 0)} watched (email after {c.get('min_pending_days', 7)} days pending), "
           f"{lane.get(CARD, 0)} with a backup email written or out, {lane.get(ANSWERED, 0)} agents answered, {lane.get(CLOSED, 0)} closed with the other buyer."]
    for r in conn.execute("SELECT p.street, p.city, a.name, a.phone FROM backup_leads b JOIN properties p ON p.id=b.property_id LEFT JOIN agents a "
                          "ON a.id=COALESCE(b.agent_id, p.agent_id) WHERE b.state=? AND b.updated_at>=? ORDER BY b.updated_at DESC LIMIT 8", (BACK, _ago(3))).fetchall():
        out.append(f"🔥 BACK ON THE MARKET: {r['street']}, {r['city']} · call {r['name'] or 'the agent'} {r['phone'] or ''}".rstrip())
    r = conn.execute("SELECT counts FROM daily_runs WHERE step='backup_lane' ORDER BY id DESC LIMIT 1").fetchone()
    if r:
        try:
            k = json.loads(r[0] or "{}")
            f, e, ci = k.get("found") or {}, k.get("emails") or {}, k.get("check_ins") or {}
            out.append(f"Last run: {f.get('looked_up', 0)} houses looked up ({f.get('found_pending', 0)} newly pending, {f.get('found_gone', 0)} sold or pulled), "
                       f"{e.get('cards', 0)} backup emails put in Fresh Leads, {ci.get('posted', 0)} check-ins drafted in Needs Me."
                       + ("" if k.get("lookups") else " No realtor.com lookups (they only run on the Mac)."))
        except ValueError:
            pass
    return out
