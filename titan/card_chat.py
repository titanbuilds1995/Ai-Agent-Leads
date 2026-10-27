"""Chat on the cards (Rodrigo, 2026-10-27: "I asked for input and got nothing back ... make it like a chat where it reads what
I say and answers back, because sometimes I have input about the leads").

Before this, a question on a To Review card got "Holding it ... questions are answered by Claude in a chat session", and a
comment on a lead card (Sent / Answered / Needs Me) was never read at all. Now:

- a question on any card → Claude answers it in a comment on that card, from what the engine knows about the lead
  (listing, our internal numbers, the conversation with the agent, the draft, his earlier comments). Nothing moves.
- input about a lead ("he told me the seller inherited it", "she's motivated") → saved as a lead note (`lead_notes`), which
  goes into every later rewrite and reply draft for that agent, and Claude says what it took from it.
- on a lead card whose agent has a drafted answer waiting, a change ("make it a 30 day close") is also passed to that reply
  card, where the engine rewrites it (the Lise gap, 2026-10-26).
The answer always lands on the card he commented on, and nothing new is created on any board (Rodrigo, 2026-10-27, Richard
Dipietro: "we are still creating a second card when I ask a question ... it's confusing"). Lead cards in every column are
handled by `approvals.lead_card_comment`, which calls `answer` here.
Claude here only talks to Rodrigo: it never sends, approves, or puts a number in front of an agent."""
from __future__ import annotations

import json
import re
from typing import Any

from . import db
from .asana import BOT
from .util import log

ANSWER_PREFIX = "💬"
FAILED = "Held: couldn't answer yet"          # a "Held:" comment does not consume his comment: retried next round
FORWARD_PREFIX = "From your comment on the lead card:"
QUESTION_RE = re.compile(r"\?|^\s*(?:why|what|how|when|where|who|which|is|are|was|were|do|does|did|can|could|should|would|will|"
                         r"have|has)\b|\b(?:explain|be more clear|clarify|walk me through|tell me|what do you think|thoughts on|"
                         r"your (?:take|read|opinion)|help me understand|remind me|any idea|analysis|feedback|strategy|advice|"
                         r"thoughts|what do you think|give me|take a look|look at it again|where .{0,40}should be|"
                         r"what (?:am i|are we|you're|you are) (?:not )?(?:seeing|missing)|something(?:'s| is) off|game ?plan|"
                         r"what(?:'s| is) going on|what do we do|how (?:do|should|could) we)\b", re.I)

SYSTEM = """You are Claude, working with Rodrigo Gayoso (Principal, Titan Realty Investments, South Florida wholesaling). Rodrigo
left a comment on an Asana card about one lead. Answer him like a sharp acquisitions partner in a chat: plain English, no jargon,
straight to the point. A quick question gets 2–6 sentences. When he asks for analysis, a game plan, "what am I not seeing", or
where the price should be, give him the full read (Rodrigo, 2026-10-27, the Carlton and Castro answers are the bar):
  - short headed sections in plain text (CAPS heading, then lines starting with "- "), no markdown symbols like ** or #;
  - start with the thing that changes the picture (wrong comps: product type / pool / waterfront / townhouse vs house, a
    layout quirk, HOA rules, why the last contract died), and say when the engine's own numbers above are off and why;
  - value it by every realistic exit (flip to end user, builder/land, landlord or Airbnb income, owner-occupant) with rough
    numbers and the assumptions behind them, then one zone: WHERE THE PRICE SHOULD BE;
  - WHAT I'D DO: concrete next steps in order (what to ask, when to follow up, what to offer if asked, where to walk);
  - TO VERIFY: the 3–5 facts to check before any number goes out, and what he can say to get them ("numbers at $540k",
    "pull dry lot comps", "skip it").
Use only what is in the data below plus general South Florida market knowledge, labeled as an estimate; when something isn't
known, say so and say how we'd find out (ask the agent, pull comps, call). Never invent facts about this house.

House rules you must respect in what you suggest: the first message to an agent never has a price; a number reaches an agent
only when Rodrigo decides it; the ARV / MAO / ranges here are internal screening numbers; terms are as-is cash, $5k deposit,
inspection period always kept. You can't send, approve or move anything: if he wants an email changed he comments the change,
to send he drags the card to Approved, a written offer comes from `titan.cli approve-offer <property_id> <price>`.

If his comment gives us information about the lead (what the agent or seller said, condition, motivation, timing, his own read),
put it in lead_note as one plain sentence; otherwise lead_note is "". If his comment asks to change the drafted answer to the
agent, put that instruction in reply_change; otherwise reply_change is ""."""
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}, "lead_note": {"type": "string"}, "reply_change": {"type": "string"}},
          "required": ["answer", "lead_note", "reply_change"], "additionalProperties": False}


def is_question(feedback: list[str]) -> bool:
    return any(QUESTION_RE.search(f) for f in feedback)


def lead_notes(conn, agent_id: int | None) -> list[str]:
    if not agent_id:
        return []
    return [r[0] for r in conn.execute("SELECT text FROM lead_notes WHERE agent_id=? ORDER BY id", (agent_id,)).fetchall()]


def context(conn, card: dict[str, Any], notes: str, comments: list[dict[str, Any]]) -> dict[str, Any]:
    o = db.row_to_dict(conn.execute("SELECT * FROM outreach WHERE id=?", (card["outreach_id"],)).fetchone()) or {}
    agent = db.row_to_dict(conn.execute("SELECT name, brokerage, phone, email FROM agents WHERE id=?", (o.get("agent_id"),)).fetchone()) or {}
    pids = json.loads(o.get("property_ids") or "[]")
    props = [db.row_to_dict(r) for r in conn.execute(
        f"SELECT p.id, p.street, p.city, p.zip, p.list_price, p.original_list_price, p.price_cuts, p.days_on_market, p.beds, p.baths, "
        f"p.sqft, p.lot_sqft, p.year_built, p.property_type, p.lead_status, p.remarks, v.arv, v.arv_method, v.comp_count, "
        f"v.repair_tier, v.repair_cost, v.mao, v.lane FROM properties p LEFT JOIN valuations v ON v.property_id=p.id "
        f"WHERE p.id IN ({','.join('?' * len(pids)) or 'NULL'})", pids).fetchall()]
    for p in props:
        p["remarks"] = (p.get("remarks") or "")[:1500]
    thread = [{"from": "agent" if m["direction"] == "in" else "Charles", "when": m["created_at"], "kind": m["classification"],
               "text": (m["body"] or "")[:1500]} for m in conn.execute(
        "SELECT m.direction, m.created_at, m.classification, m.body FROM messages m JOIN threads t ON t.id=m.thread_id "
        "WHERE t.agent_id=? ORDER BY m.created_at, m.id", (o.get("agent_id"),)).fetchall()][-12:]
    talk = [{"who": "engine/Claude" if (c.get("text") or "").startswith(BOT) else "Rodrigo", "text": (c.get("text") or "")[:800]}
            for c in comments[-20:]]
    return {"card_text": (notes or "")[:4000], "email_status": o.get("status"), "agent": agent, "properties": props,
            "conversation_with_agent": thread, "earlier_notes_from_rodrigo": lead_notes(conn, o.get("agent_id")),
            "comment_thread_on_this_card": talk}


def pending_reply_card(asana, cfg: Any, conn, agent_id: int | None) -> str | None:
    """The gid of a drafted answer to this agent still waiting on the board (To Review / Needs Revision / Needs Me)."""
    from .approvals import _cfg, needs_me_section, needs_revision_section, parse_card
    if not agent_id:
        return None
    a = _cfg(cfg)
    for sec in (a["to_review"], needs_revision_section(cfg, asana), needs_me_section(cfg, asana)):
        for t in asana.section_tasks(sec):
            pc = parse_card(t.get("notes") or "")
            if pc and pc.get("reply_to"):
                r = conn.execute("SELECT agent_id FROM outreach WHERE id=?", (pc["outreach_id"],)).fetchone()
                if r and r[0] == agent_id:
                    return t["gid"]
    return None


def answer(conn, cfg: Any, asana, llm, t: dict[str, Any], card: dict[str, Any], feedback: list[str], comments: list[dict[str, Any]],
           lead_card: bool = False, tail: str = "", forward_change: bool = True, say_failed: bool = True) -> dict[str, Any] | None:
    """Answer his comment on the card, always on the card he commented on. Returns Claude's output (answer / lead_note /
    reply_change) when answered (his comment is consumed); None = Claude unreachable, said once (unless say_failed is False:
    the caller has its own deterministic handling and a "Held" comment would be misleading). forward_change=False leaves
    passing a change to the reply card to the caller (which also holds that card when it sits in Approved)."""
    out = None
    if llm is not None and getattr(llm, "available", False):
        user = json.dumps({"rodrigo_says": feedback, **context(conn, card, t.get("notes") or "", comments)}, ensure_ascii=False, default=str)
        out = llm.structured("card_chat", SYSTEM, user, SCHEMA, max_tokens=2500)
    if not out or not (out.get("answer") or "").strip():
        from .llm import login_problem
        err = getattr(llm, "last_error", None) if llm is not None else None
        why = login_problem(cfg, err) or "Claude isn't reachable from this job" + (f": {err[:160]}" if err else "")
        last = (comments[-1].get("text") or "") if comments else ""
        if say_failed and not last.startswith(f"{BOT} {FAILED}"):
            asana.comment(t["gid"], f"{FAILED} ({why}). Nothing moved; I'll answer on the next round.")
        return None
    o = conn.execute("SELECT agent_id FROM outreach WHERE id=?", (card["outreach_id"],)).fetchone()
    agent_id = o[0] if o else None
    extra = []
    note = (out.get("lead_note") or "").strip()
    if note:
        conn.execute("INSERT OR IGNORE INTO lead_notes(agent_id, outreach_id, text, created_at) VALUES(?,?,?,?)",
                     (agent_id, card["outreach_id"], note[:600], db.iso()))
        extra.append(f"Noted for this lead (goes into every rewrite and reply from now on): {note}")
    change = (out.get("reply_change") or "").strip()
    if lead_card and change and forward_change:
        gid = pending_reply_card(asana, cfg, conn, agent_id)
        if gid:      # no BOT prefix: on the reply card it reads as his feedback and the engine rewrites the reply
            asana.comment_raw(gid, f"{FORWARD_PREFIX} {' '.join(feedback)}")
            extra.append("Your change was passed to the drafted answer card; it gets rewritten there on this round or the next.")
    asana.comment(t["gid"], "\n\n".join([f"{ANSWER_PREFIX} {out['answer'].strip()}"] + extra + ([tail] if tail else [])))
    return out
