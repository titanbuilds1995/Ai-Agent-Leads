"""CONVERSATIONS MIRROR (Rodrigo, journal 2026-10-25: "every inbound reply as a card comment with the engine's answer").

Every reply an agent sends (email or text) is posted as a comment on that agent's card on the Asana approvals board,
together with how the engine read it and the answer it drafted (drafts live in Charles's Gmail under Titan/Review and
are never sent on their own). So Rodrigo, and a cloud session without access to Charles's inbox, can read and answer
conversations from Asana alone. Runs after every check-replies and approvals tick; idempotent (messages.mirrored_at).
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import db
from .util import iso, log, money, parse_iso, pretty_phone

PREFIX = "📩"
NO_CARD = "no_card"


def card_for_thread(conn, thread: dict[str, Any]) -> str | None:
    """The agent's most recent card, preferring one about the same house."""
    rows = conn.execute("SELECT asana_task_id, property_ids FROM outreach WHERE agent_id=? AND asana_task_id IS NOT NULL "
                        "AND COALESCE(kind,'') NOT LIKE 'reply%' "      # the lead's own card, never a (closed) answer card
                        "ORDER BY COALESCE(sent_at, created_at) DESC, id DESC", (thread["agent_id"],)).fetchall()
    want = set(json.loads(thread["property_ids"] or "[]"))
    for r in rows:
        try:
            if want & set(json.loads(r["property_ids"] or "[]")):
                return r["asana_task_id"]
        except (TypeError, ValueError):
            continue
    return rows[0]["asana_task_id"] if rows else None


def engine_answer(conn, thread: dict[str, Any], msg: dict[str, Any]) -> dict[str, Any] | None:
    """The reply the engine wrote after this inbound message: a sent message, or a draft waiting in Gmail."""
    sent = conn.execute("SELECT body, created_at FROM messages WHERE thread_id=? AND direction='out' AND id>? AND action LIKE 'reply:%' ORDER BY id LIMIT 1",
                        (thread["id"], msg["id"])).fetchone()
    if sent:
        return {"status": "sent", "body": sent["body"]}
    draft = conn.execute("SELECT body, status, error FROM outreach WHERE agent_id=? AND kind LIKE 'reply:%' AND created_at>=? ORDER BY id DESC LIMIT 1",
                         (thread["agent_id"], msg["created_at"])).fetchone()
    if draft:
        return {"status": draft["status"], "body": draft["body"], "error": draft["error"]}
    return None


def _when(s: str | None) -> str:
    dt = parse_iso(s)
    if not dt:
        return ""
    from zoneinfo import ZoneInfo
    d = dt.astimezone(ZoneInfo("America/New_York"))
    return f"{d.strftime('%a %b')} {d.day}, {d.strftime('%I:%M').lstrip('0')}{d.strftime('%p').lower()}"


# What the engine does with each kind of reply, in Rodrigo's words (Rodrigo, 2026-11-02: a flat "no" goes to Skip and he
# never needs to see it; the rest of the card already says which house, so no listing / MAO / draft lines here)
NEXT = {"NOT_INTERESTED": "Read as: not interested, seller firm. This lead goes to Skip; nothing to do.",
        "SOLD_OR_PENDING": "Read as: already under contract or sold. This lead goes to Skip; nothing to do.",
        "STOP": "Read as: asked us to stop. Do-not-contact; this lead goes to Skip.",
        "ASK_FOR_OFFER": "Read as: asked for our offer. Your call: comment \"offer N\" here, or what Charles should ask.",
        "COUNTER_WITH_NUMBER": "Read as: gave a number. Your call: comment \"offer N\" here, or what Charles should ask.",
        "BOUNCE": "Bounced: the email never arrived. Call the agent.",
        "OUT_OF_OFFICE": "Auto-reply; waiting for the real answer."}


def mirror_text(conn, thread: dict[str, Any], msg: dict[str, Any], agent: dict[str, Any]) -> str:
    from .replies.clean import clean_reply
    who = agent.get("name") or agent.get("email") or pretty_phone(agent.get("phone")) or "the agent"
    via = " (text)" if thread.get("channel") == "sms" else ""
    body = clean_reply(msg.get("body") or "")
    if len(body) > 1200:
        body = body[:1200].rstrip() + " […]"
    phone = pretty_phone(agent.get("phone")) if agent.get("phone") else ""
    cls = msg.get("classification") or ""
    head = f"{PREFIX} {who} replied{via} · {_when(msg.get('created_at'))}" + (f" · call {phone}" if phone else "")
    nxt = NEXT.get(cls) or f"Read as: {PLAIN.get(cls, 'replied')}. Charles drafts his answer on this card (NEXT EMAIL) and it waits in Needs Me."
    ans = engine_answer(conn, thread, msg)
    if ans and ans.get("status") == "sent" and ans.get("body"):
        nxt = "Charles answered:\n" + clean_reply(ans["body"])
    return "\n".join([head, "", body or "(empty message)", "", nxt])


def mirror_replies(conn, cfg: Any, asana, limit: int = 50) -> dict[str, int]:
    """Post every not-yet-mirrored inbound message on its agent's card. Never raises into the caller."""
    counts = {"posted": 0, "no_card": 0, "failed": 0}
    try:
        pending = conn.execute("SELECT m.*, t.agent_id, t.property_ids, t.state, t.channel FROM messages m JOIN threads t ON t.id=m.thread_id "
                               "WHERE m.direction='in' AND m.mirrored_at IS NULL ORDER BY m.id LIMIT ?", (limit,)).fetchall()
    except Exception as e:  # noqa: BLE001
        log.warning("conversations: cannot read messages: %s", e)
        return counts
    for row in pending:
        msg = db.row_to_dict(row)
        thread = {"id": msg["thread_id"], "agent_id": msg["agent_id"], "property_ids": msg["property_ids"], "state": msg["state"], "channel": msg["channel"]}
        try:
            gid = card_for_thread(conn, thread)
            if not gid:
                conn.execute("UPDATE messages SET mirrored_at=? WHERE id=?", (NO_CARD, msg["id"]))
                counts["no_card"] += 1
                continue
            agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (thread["agent_id"],)).fetchone()) or {}
            asana.comment(gid, mirror_text(conn, thread, msg, agent))
            conn.execute("UPDATE messages SET mirrored_at=? WHERE id=?", (iso(), msg["id"]))
            counts["posted"] += 1
        except Exception as e:  # noqa: BLE001 — a dead card or Asana hiccup must not stop the tick; retried next time
            log.warning("conversations: could not mirror message %s: %s", msg["id"], e)
            counts["failed"] += 1
    return counts


# ---------- the lead board (Rodrigo, 2026-10-26: "see exactly who answered and where we stand with each lead, and a whole
# tab with the unanswered") ----------
ANSWERED = "Answered"
NO_ANSWER = "Sent · no answer yet"
STAND_START, STAND_END = "-----WHERE WE STAND-----", "-----END WHERE WE STAND-----"
NOT_AN_ANSWER = ("BOUNCE", "OUT_OF_OFFICE")
# Rodrigo, 2026-10-27: a lead whose next move is his goes to "Needs Me"; "Answered" = we replied, the ball is with the agent
NEEDS_ME_STATUS = ("NEEDS YOU", "OUR TURN", "ANSWER DRAFTED", "OFFER DRAFTED")   # stand["kind"] values
PLAIN = {"INTERESTED": "talked about the house", "ASK_FOR_OFFER": "asked for our offer", "COUNTER_WITH_NUMBER": "gave a number",
         "QUESTION": "asked a question", "NOT_INTERESTED": "not interested", "SOLD_OR_PENDING": "already under contract or sold",
         "STOP": "asked us to stop"}


def board_sections(cfg: Any, asana) -> tuple[str, str]:
    """(no-answer section, answered section). The old Sent column becomes "Sent · no answer yet"; "Answered" is created
    right after it the first time."""
    a = cfg.get("outreach.asana", {}) or {}
    cached = getattr(asana, "_lead_board", None)
    if cached:
        return cached
    sent = a["sent"]
    secs = {x["gid"]: (x.get("name") or "") for x in asana.sections(a["project"])}
    if secs.get(sent, NO_ANSWER) != NO_ANSWER:
        asana.rename_section(sent, NO_ANSWER)
    answered = a.get("answered") or asana.ensure_section(a["project"], ANSWERED, insert_before=a.get("skip"))
    try:
        asana._lead_board = (sent, answered)
    except Exception:  # noqa: BLE001
        pass
    return sent, answered


def where_we_stand(conn, agent_id: int, pending_reply: bool = False) -> dict[str, Any] | None:
    """What Rodrigo needs to know about a lead, in a few fields (rendered by stand_lines / stand_html), or None when the
    agent has not really answered (bounces and auto-replies don't count)."""
    from .replies.clean import one_line
    msgs = [db.row_to_dict(r) for r in conn.execute(
        "SELECT m.direction, m.classification, m.created_at, m.body, t.state FROM messages m JOIN threads t ON t.id=m.thread_id "
        "WHERE t.agent_id=? ORDER BY m.created_at, m.id", (agent_id,)).fetchall()]
    real_in = [m for m in msgs if m["direction"] == "in" and (m.get("classification") or "") not in NOT_AN_ANSWER]
    if not real_in:
        return None
    last_in = real_in[-1]
    last = [m for m in msgs if not (m["direction"] == "in" and (m.get("classification") or "") in NOT_AN_ANSWER)][-1]
    cls = last_in.get("classification") or ""
    outs = [m for m in msgs if m["direction"] == "out"]
    first = (conn.execute("SELECT name FROM agents WHERE id=?", (agent_id,)).fetchone() or ["The agent"])[0] or "The agent"
    first = first.split()[0].title() if first.split() else "The agent"
    if cls in ("NOT_INTERESTED", "SOLD_OR_PENDING", "STOP") or (last.get("state") or "") in ("CLOSED", "STOPPED"):
        kind, status = "CLOSED", f"Closed · {first} said {PLAIN.get(cls, 'the conversation is over')}. Nothing to do."
    elif last["direction"] == "out":
        kind, status = "WAITING", f"Waiting on {first} since {_when(last['created_at'])}."
    elif cls in ("ASK_FOR_OFFER", "COUNTER_WITH_NUMBER"):
        kind = "NEEDS YOU"
        status = (f"Needs you · {first} {PLAIN.get(cls)}. Comment \"offer N\" here, or tell Charles what to ask."
                  + (" A holding answer is drafted." if pending_reply else ""))
    elif pending_reply:
        kind, status = "ANSWER DRAFTED", "Answer drafted · Charles's answer is on this card (NEXT EMAIL, below). Drag this card to Approved to send it."
    else:
        kind, status = "OUR TURN", f"Our turn · Charles owes {first} an answer (it lands on this card next round)."
    return {"kind": kind, "status": status, "first": first, "cls": cls,
            "last_in": f"{first} ({_when(last_in['created_at'])}): \"{one_line(last_in.get('body') or '', 220)}\"",
            "count": f"{first} wrote {len(real_in)}, we wrote {len(outs)}" + (f", last {_when(outs[-1]['created_at'])}." if outs else "."),
            "drafts": []}


def stand_lines(st: dict[str, Any]) -> list[str]:
    """Plain-text form of the block (what a client without html_notes gets, and the start of the html form)."""
    lines = [f"Status: {st['status']}", f"Last from {st['last_in']}", st["count"]]
    for d in st.get("drafts") or []:
        lines += ["", f"Drafted answer (not sent) · card \"{d['name']}\" in {d['where']}. Drag it to Approved to send.", d["body"]]
    return lines


def _with_stand(notes: str, lines: list[str]) -> str:
    block = f"{STAND_START}\n" + "\n".join(lines) + f"\n{STAND_END}\n\n"
    rest = re.sub(re.escape(STAND_START) + r".*?" + re.escape(STAND_END) + r"\n*", "", notes or "", count=1, flags=re.S)
    return block + rest


# ---------- the formatted card (Rodrigo, 2026-11-02: "where we stand … looks very convoluted … make it more visually appealing,
# organized and structured"). Asana renders html_notes to plain `notes` with every line kept, so the To:/Subject:/EMAIL markers the
# engine parses survive; the "Why this house" analysis and the how-to footer are dropped once the lead is in conversation.
BOILERPLATE_RE = re.compile(r"^(Comment feedback to get a rewrite\..*|This is Charles's ANSWER in an existing conversation\..*|"
                            r"Comment to change it · .*|Charles's answer, sent in the same email thread\..*|"
                            r"You can also edit the text between the EMAIL lines yourself\.?)$", re.M)
ANALYSIS_RE = re.compile(r"^Why this house \(engine's read, not sent\):\n(?:•[^\n]*\n?)*", re.M)
STAND_RE = re.compile(re.escape(STAND_START) + r".*?" + re.escape(STAND_END) + r"\n*", re.S)


def _e(x: Any) -> str:
    import html
    return html.escape(str(x if x is not None else ""), quote=False)


RENDERED_STAND_RE = re.compile(r"^WHERE WE STAND\n.*?\n-{10,}\n", re.S)       # our own html block as Asana renders it back
HEADING_RE = re.compile(r"^(Listing|First email( \(sent [^\n]*\))?|-{10,})\s*$", re.M)
LISTING_LINE_RE = re.compile(r"(·\s*\$[\d,]+\s*·\s*\d+ days on market|^\s*https?://\S+\s*$|^\s*Agent: )")
OUTREACH_RE = re.compile(r"^\((reply to )?outreach #\d+\)\s*$", re.M)


def card_parts(notes: str) -> dict[str, str]:
    """The pieces of a lead card's description, whatever form it is in now (plain card, marker block or rendered html):
    `email` = the To:/Subject:/EMAIL block plus the "(outreach #N)" line, `meta` = listing, agent and Rodrigo's notes."""
    rest = STAND_RE.sub("", notes or "", count=1)
    rest = RENDERED_STAND_RE.sub("", rest, count=1)
    rest = ANALYSIS_RE.sub("", rest)
    rest = BOILERPLATE_RE.sub("", rest)
    rest = HEADING_RE.sub("", rest)
    m = re.search(r"^To:.*?" + re.escape("-----END EMAIL-----"), rest, re.S | re.M)
    email = m.group(0).strip() if m else ""
    rest = rest[: m.start()] + rest[m.end():] if m else rest
    o = OUTREACH_RE.search(rest)
    if o:
        email = (email + "\n" + o.group(0).strip()).strip()
        rest = rest[: o.start()] + rest[o.end():]
    # Keep only the listing facts. Anything else left here is an older WHERE WE STAND / drafted answer that Asana rendered
    # back as plain text; folding it into the new block made the card grow every round (Thais: 12 copies, Nov 2).
    keep = [ln.strip() for ln in rest.splitlines() if LISTING_LINE_RE.search(ln)]
    meta = "\n".join(dict.fromkeys(keep))
    return {"email": email, "meta": meta}


def plain_from_html(html: str) -> str:
    """The plain-notes form of the card (clients without html_notes): tags off, text and lines kept, rules as dashes."""
    import html as h
    return h.unescape(re.sub(r"<hr/>", "-" * 40, re.sub(r"</?(?!hr)[a-z]+/?>", "", html or ""))).strip()


def _first_email_text(row: dict[str, Any] | None, parts: dict[str, str]) -> str:
    """The first email as history, without To:/Subject:/EMAIL markers so the sender never mistakes it for the next email."""
    body = (row or {}).get("body") or ""
    if not body:
        m = re.search(re.escape("-----EMAIL-----") + r"\n(.*?)\n" + re.escape("-----END EMAIL-----"), parts.get("email") or "", re.S)
        body = m.group(1) if m else ""
    return body.strip()


def lead_card_html(st: dict[str, Any] | None, notes: str, sent_when: str = "", answer: dict[str, Any] | None = None,
                   row: dict[str, Any] | None = None, agent: dict[str, Any] | None = None, props: list[dict[str, Any]] | None = None) -> str:
    """The lead card, one place for everything (Rodrigo, 2026-11-03): WHERE WE STAND (once the agent answered), NEXT EMAIL
    (Charles's answer waiting to go out: the To/Subject/EMAIL block the sender reads), the listing, the first email as history.
    Without an answer waiting, the first email stays the EMAIL block (the card as it was)."""
    parts = card_parts(notes)
    oid = re.search(r"\((?:reply to )?outreach #(\d+)\)", parts.get("email") or "")
    oid_line = f"(outreach #{oid.group(1)})" if oid else (f"(outreach #{row['id']})" if row and row.get("id") else "")
    out = ["<body>"]
    if st:
        out += ["<strong>WHERE WE STAND</strong>", "<ul>", f"<li><strong>Status:</strong> {_e(st['status'])}</li>",
                f"<li><strong>Last from</strong> {_e(st['last_in'])}</li>", f"<li><strong>Thread:</strong> {_e(st['count'])}</li>", "</ul>"]
        for d in st.get("drafts") or []:
            out += [f"<strong>Drafted answer</strong> (not sent) · card \"{_e(d['name'])}\" in {_e(d['where'])}. Drag it to Approved to send.",
                    f"<blockquote>{_e(d['body'])}</blockquote>"]
    if answer:
        if st:
            out.append("<hr/>")
        out += [f"<strong>NEXT EMAIL</strong> · Charles's answer, not sent yet. Drag this card to Approved to send it · comment to change it · "
                "or edit the text between the EMAIL lines yourself."]
        if answer.get("note"):
            out.append(_e(answer["note"]))
        out += [f"To: {_e(answer['to'])}", f"Subject: {_e(answer['subject'])}", "-----EMAIL-----", _e(answer["body"]), "-----END EMAIL-----"]
    meta = parts["meta"]
    if not meta and props and agent is not None:
        from .approvals import listing_meta
        meta = listing_meta(props[0], agent)
    if meta:
        out += ["<hr/>", "<strong>Listing</strong>", _e(re.sub(r"^Listing: ", "", meta))]
    if answer:
        first = _first_email_text(row, parts)
        if first:
            out += ["<hr/>", "<strong>First email</strong>" + (f" (sent {_e(sent_when)})" if sent_when else ""), _e(first)]
        if oid_line:
            out.append(oid_line)
    elif row and row.get("status") == "sent" and (row.get("body") or "").strip():
        # the email that went out is the truth (the card's block may still show an answer that has just been sent)
        block = f"To: {row.get('to_address') or ''}\nSubject: {row.get('subject') or ''}\n-----EMAIL-----\n{row['body'].strip()}\n-----END EMAIL-----\n{oid_line}".strip()
        out += ["<hr/>", "<strong>First email</strong>" + (f" (sent {_e(sent_when)})" if sent_when else ""), _e(block)]
    elif parts["email"]:
        out += ["<hr/>", "<strong>First email</strong>" + (f" (sent {_e(sent_when)})" if sent_when else ""), _e(parts["email"])]
    out.append("</body>")
    return "\n".join(out)


def stand_html(st: dict[str, Any], notes: str, sent_when: str = "") -> str:
    return lead_card_html(st, notes, sent_when)


def _squash(text: str) -> str:
    """Compare a description with Asana's plain rendering of our html: tags, entities, whitespace and rule lines ignored."""
    import html
    t = re.sub(r"<[^>]+>", "", text or "")
    t = html.unescape(t)
    t = re.sub(r"-{5,}", "", t)
    return re.sub(r"\s+", "", t)


def lead_board(conn, cfg: Any, asana) -> dict[str, int]:
    """Lead cards whose agent really answered: "Needs Me" when the next move is Rodrigo's (NEEDS YOU / OUR TURN / ANSWER
    DRAFTED / OFFER DRAFTED), "Answered" when the agent wrote last or the conversation closed, back to "Sent · no answer
    yet" once we replied and wait on them (Rodrigo, 2026-10-27: we answering is not them answering). A lead the agent closed
    ("no, the seller is firm", sold, stop) goes to Skip with its reply cards (Rodrigo, 2026-11-02: "I don't even need to see
    the lead"). Keeps a formatted WHERE WE STAND description on every such first-email card. Never raises into the caller."""
    from .approvals import needs_me_section, parse_card
    n = {"moved": 0, "updated": 0, "to_needs_me": 0, "to_answered": 0, "to_skip": 0}
    try:
        a = cfg.get("outreach.asana", {}) or {}
        sent, answered = board_sections(cfg, asana)
        nm = needs_me_section(cfg, asana)
        from .approvals import needs_revision_section
        from .warm_leads import wrap
        asana = wrap(cfg, asana, nm, needs_revision_section(cfg, asana), sent, answered)   # Titan Warm Leads cards too
        skip = a.get("skip")
        pending = set()
        drafts: dict[int, list[tuple[str, str, dict]]] = {}     # outreach id -> [(where, card name, parsed card)]
        draft_gids: dict[int, list[str]] = {}
        names = {a.get("to_review"): "Fresh Leads", a.get("approved"): "Approved (sends next run)", needs_me_section(cfg, asana): "Needs Me"}
        for sec in (a.get("to_review"), a.get("approved"), getattr(asana, "_needs_revision_gid", None) or a.get("needs_revision"),
                    needs_me_section(cfg, asana)):
            for t in (asana.section_tasks(sec) if sec else []):
                pc = parse_card(t.get("notes") or "")
                if pc and pc.get("reply_to"):
                    pending.add(pc["outreach_id"])
                    drafts.setdefault(pc["outreach_id"], []).append((names.get(sec, "Needs Revision"), t.get("name") or "", pc))
                    draft_gids.setdefault(pc["outreach_id"], []).append(t["gid"])
        agent_of: dict[int, int | None] = {}

        def agent_id(oid: int) -> int | None:
            if oid not in agent_of:
                r = conn.execute("SELECT agent_id FROM outreach WHERE id=?", (oid,)).fetchone()
                agent_of[oid] = r["agent_id"] if r else None
            return agent_of[oid]

        from .answers import pending as answer_pending, pending_agents as db_pending_agents
        pending_agents = {agent_id(o) for o in pending} | db_pending_agents(conn)   # answers live on the lead card now (2026-11-03)
        lead_cards = ([(t, sent) for t in asana.section_tasks(sent)] + [(t, answered) for t in asana.section_tasks(answered)]
                      + [(t, nm) for t in asana.section_tasks(nm)] + (asana.lead_cards() if hasattr(asana, "lead_cards") else []))
        for t, here in lead_cards:
            pc = parse_card(t.get("notes") or "")
            if not pc or pc.get("reply_to") or not agent_id(pc["outreach_id"]):
                continue
            row = db.row_to_dict(conn.execute("SELECT * FROM outreach WHERE id=?", (pc["outreach_id"],)).fetchone())
            if here == nm and (row or {}).get("status") != "sent":
                continue          # an unsent first email parked with its numbers: not a lead card
            aid = agent_id(pc["outreach_id"])
            ans = answer_pending(conn, pc["outreach_id"])
            if not ans:
                from .answers import hand_written
                if hand_written(conn, row, pc):       # he wrote the answer into the EMAIL lines himself: keep it, never overwrite it
                    ans = {"body": pc["body"], "note": "", "state": "pending"}
                    conn.execute("UPDATE outreach SET answer_draft=?, answer_state='pending' WHERE id=?", (pc["body"], pc["outreach_id"]))
                    pending_agents.add(aid)
            st = where_we_stand(conn, aid, aid in pending_agents)
            if not st:
                continue
            # Rodrigo, 2026-10-26: the status must move as soon as an offer is drafted, not stay at "decide a number"
            agent_oids = [o for o in pending if agent_id(o) == aid]
            agent_drafts = [d for o in agent_oids for d in drafts.get(o, [])]
            offer = next((d for d in agent_drafts if re.search(r"\$\s?\d{2,3},\d{3}", d[2]["body"])), None)
            if offer and st["kind"] != "CLOSED":
                price = re.search(r"\$\s?(\d{2,3},\d{3})", offer[2]["body"]).group(1)
                if "Approved" in offer[0]:
                    st["kind"], st["status"] = "OFFER APPROVED", f"Offer approved (${price}) · goes out from Charles on the next run."
                else:
                    st["kind"], st["status"] = "OFFER DRAFTED", (f"Offer drafted (${price}) · waiting for your approval in {offer[0]}. "
                                                                 f"Drag \"{offer[1]}\" to Approved to send it.")
            elif ans and st["kind"] != "CLOSED" and re.search(r"\$\s?\d{2,3},\d{3}", ans["body"]):
                price = re.search(r"\$\s?(\d{2,3},\d{3})", ans["body"]).group(1)
                st["kind"], st["status"] = "OFFER DRAFTED", f"Offer drafted (${price}) · it is the NEXT EMAIL on this card. Drag this card to Approved to send it."
            # Rodrigo, 2026-10-26: "in the card I do not see the answer you drafted" — a legacy reply card's text is shown here too
            if st["kind"] != "CLOSED":
                st["drafts"] = [{"where": where, "name": cname, "body": pc2["body"]} for where, cname, pc2 in agent_drafts]
            lines = stand_lines(st)
            notes = t.get("notes") or ""
            html_fn = getattr(asana, "update_html_notes", None)
            if html_fn is not None:
                answer = None
                if ans:
                    from .answers import answer_subject
                    agent_row = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (aid,)).fetchone()) or {}
                    pids = json.loads(row.get("property_ids") or "[]")
                    props = [db.row_to_dict(r) for r in conn.execute(
                        f"SELECT * FROM properties WHERE id IN ({','.join('?' * len(pids)) or 'NULL'})", pids).fetchall()]
                    # the card's own text wins (he may have edited the EMAIL lines); the database copy is the fallback
                    answer = {"to": pc["to"] or row.get("to_address") or agent_row.get("email"), "subject": pc["subject"] if pc["subject"].lower().startswith("re:") else answer_subject(row, props),
                              "body": pc["body"] if _squash(pc["body"]) != _squash(row.get("body") or "") else ans["body"], "note": ans.get("note") or ""}
                html = lead_card_html(st, notes, _when(row["sent_at"]) if row and row["sent_at"] else "", answer, row)
                if _squash(html) != _squash(notes):
                    html_fn(t["gid"], html)
                    n["updated"] += 1
            else:
                new = _with_stand(notes, lines)
                if new != notes:
                    asana.update_notes(t["gid"], new)
                    n["updated"] += 1
            # Rodrigo, 2026-10-27: "Answered" means the AGENT answered and it is on us; once we reply, the lead goes back to
            # "Sent · no answer yet" until the agent writes again. Needs Me = the next move is his.
            if st["kind"] in NEEDS_ME_STATUS:
                target = nm
            elif st["kind"] == "WAITING":
                target = sent
            elif st["kind"] == "OFFER APPROVED":
                target = here             # sends on the next run; it then reads WAITING and goes to Sent
            elif st["kind"] == "CLOSED" and skip and not (hasattr(asana, "is_warm") and asana.is_warm(t["gid"])):
                target = skip             # Rodrigo, 2026-11-02: a "no" never needs him; warm-board cards go to its Closed column
            else:
                target = answered
            if target == sent and hasattr(asana, "is_warm") and asana.is_warm(t["gid"]) and conn.execute(
                    "SELECT 1 FROM offers WHERE agent_id=? AND status IN ('SENT','COUNTERED')", (aid,)).fetchone():
                if asana.offer_out(t["gid"]):         # warm board: waiting on the agent with our offer out
                    n["moved"] += 1
                continue
            if target != here:
                asana.move(t["gid"], target)
                n["moved"] += 1
                key = "to_needs_me" if target == nm else "to_sent" if target == sent else "to_skip" if target == skip else "to_answered"
                n[key] = n.get(key, 0) + 1
                if target == skip:
                    asana.comment(t["gid"], f"{st['first']} said no ({PLAIN.get((st.get('cls') or ''), 'conversation over')}): "
                                            "moved to Skip, nothing more to do here.")
            if st["kind"] == "CLOSED" and skip:       # whatever answer was waiting for this agent is moot
                for gid in [g for o in agent_oids for g in draft_gids.get(o, [])]:
                    asana.move(gid, skip)
                    n["to_skip"] = n.get("to_skip", 0) + 1
    except Exception as e:  # noqa: BLE001
        log.warning("lead board: %s", e)
        n["error"] = str(e)[:200]
    return n
