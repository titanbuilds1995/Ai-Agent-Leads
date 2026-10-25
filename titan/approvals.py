"""Email approvals on Asana (Rodrigo, 2026-10-24): every first-touch email becomes a card with a picture of the email.
Rodrigo comments feedback (the engine rewrites the card) and drags good ones to Approved; approved emails go out from
Charles's Gmail during the send window, a couple at a time, at varied times. Nothing is sent from To Review.

Card notes carry the email between markers so edits made directly on the card are what gets sent."""
from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from . import db
from .asana import BOT, Asana, AsanaError
from .outreach.compose import EMAIL_SCHEMA, compose_email, protocol_text, seed_for, signature, validate_email, voice_text
from .util import iso, log, now_utc, today_et

START, END = "-----EMAIL-----", "-----END EMAIL-----"


def _cfg(cfg: Any) -> dict[str, Any]:
    return cfg.get("outreach.asana", {}) or {}


def card_notes(to: str, subject: str, body: str, outreach_id: int, meta: str) -> str:
    return (f"To: {to}\nSubject: {subject}\n{START}\n{body}\n{END}\n\n{meta}\n\n"
            f"Comment feedback to get a rewrite. Drag to Approved to send (it goes out during business hours). "
            f"You can also edit the text between the EMAIL lines yourself.\n(outreach #{outreach_id})")


def parse_card(notes: str) -> dict[str, Any] | None:
    to = re.search(r"^To:\s*(\S+@\S+)\s*$", notes or "", re.M)
    subj = re.search(r"^Subject:\s*(.+)$", notes or "", re.M)
    body = re.search(re.escape(START) + r"\n(.*?)\n" + re.escape(END), notes or "", re.S)
    oid = re.search(r"\(outreach #(\d+)\)", notes or "")
    if not (to and subj and body and oid):
        return None
    return {"to": to.group(1), "subject": subj.group(1).strip(), "body": body.group(1).strip(), "outreach_id": int(oid.group(1))}


def analysis_text(analysis: dict[str, Any] | None) -> str:
    """The think-first analysis behind the email, for Rodrigo's review (protocol §21)."""
    if not analysis:
        return ""
    lines = ["Why this house (engine's read, not sent):"]
    if analysis.get("hypothesis"):
        lines.append(f"• Hypothesis: {analysis['hypothesis']}")
    if analysis.get("rehab_level"):
        lines.append(f"• Likely rehab level: {analysis['rehab_level']} of 5")
    if analysis.get("key_unknown"):
        lines.append(f"• Biggest unknown: {analysis['key_unknown']}")
    for k, label in (("positive_signals", "Positives"), ("negative_signals", "Negatives"), ("unknown_big_ticket", "Unknown big-ticket")):
        if analysis.get(k):
            lines.append(f"• {label}: " + "; ".join(str(x) for x in analysis[k][:5]))
    return "\n".join(lines)


def listing_meta(p: dict[str, Any], agent: dict[str, Any], analysis: dict[str, Any] | None = None) -> str:
    lane = p.get("lane") or ""
    meta = (f"Listing: {p.get('street')}, {p.get('city')} · ${int(p.get('list_price') or 0):,} · {p.get('days_on_market')} days on market · "
            f"{p.get('price_cuts') or 0} price cut(s) · {lane}\n{p.get('listing_url') or ''}\n"
            f"Agent: {agent.get('name')} · {agent.get('email')} · {agent.get('brokerage') or ''}")
    at = analysis_text(analysis)
    return f"{meta}\n\n{at}" if at else meta


def post_card(conn, cfg: Any, asana: Asana, agent: dict[str, Any], props: list[dict[str, Any]], d: dict[str, Any],
              outreach_id: int) -> str:
    a = _cfg(cfg)
    p = props[0]
    lane = p.get("lane") or ""
    meta = listing_meta(p, agent, d.get("analysis"))
    name = f"{agent.get('name') or 'Agent'} · {p.get('street')}, {p.get('city')}" + (" ⚡ fresh deal" if lane == "FRESH_DEAL" else "")
    task = asana.create_task(a["project"], a["to_review"], name, card_notes(agent["email"], d["subject"], d["body"], outreach_id, meta))
    _attach_picture(cfg, asana, task["gid"], agent["email"], d["subject"], d["body"], outreach_id)
    conn.execute("UPDATE outreach SET asana_task_id=? WHERE id=?", (task["gid"], outreach_id))
    return task["gid"]


def _attach_picture(cfg: Any, asana: Asana, task: str, to: str, subject: str, body: str, outreach_id: int, rev: int = 0) -> None:
    try:
        from .email_card import render
        op = cfg["operator"]
        path = cfg.path("reports") / "email_cards" / f"{outreach_id}_{rev}.png"
        render(path, f"{op['sender_name']} <{op.get('sender_email') or cfg.secret('GMAIL_SENDER')}>", to, subject, body)
        asana.attach(task, path, f"email_{outreach_id}{'_v' + str(rev) if rev else ''}.png")
    except Exception as e:  # noqa: BLE001 — the card text is the source of truth; a missing picture is not fatal
        log.warning("email picture for outreach %s failed: %s", outreach_id, e)


REVISE_SYSTEM = """You revise one email from {sender} ({company}) to a listing agent, following Rodrigo's feedback exactly.
The outreach protocol below still applies in full (no price/offer/percentage/dollar figure, no listing echo, no manufactured observations,
one easy question, 60-120 words, plain text, signature block unchanged, and NO dashes of any kind: no em dash, en dash, ' - ' or '--'; use commas or periods).
The first paragraph must say who we are in a personable sentence or two ({sender} with {company}, this listing came up or stood out)
and the company name says it all: never "we're investors", "we buy here regularly" or "genuinely interested". We never state that we
are the buyer ("we're looking for properties in X", "what we look for", "our side"; never "we buy", "we'd buy", "serious buyers").
and name every listed street in the body. If the feedback asks for numbers or analysis rather than a change to the email, keep the email a
conversation (ask the agent for the fact instead of stating a figure).

=== OUTREACH PROTOCOL ===
{protocol}
=== END PROTOCOL ===

=== VOICE PROTOCOL ("I" for what {sender} did, "we"/"our side" for criteria; never powerless, never overcommitting) ===
{voice}
=== END VOICE PROTOCOL ===
Return JSON: subject and body (body includes the unchanged signature block)."""


TEST_RE = re.compile(r"\btest\b", re.I)
EMAIL_IN_TEXT_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


FAILED_PREFIX = "I couldn't rewrite this one automatically"


def pending_feedback(asana: Asana, task_gid: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Rodrigo's comments since the engine's last comment on the card (the engine's own comments start with BOT).
    "Couldn't rewrite" and "Held" comments do not count: the feedback stays pending until a rewrite actually lands."""
    comments = asana.comments(task_gid)
    last_bot = max((i for i, c in enumerate(comments) if (c.get("text") or "").startswith(BOT)
                    and not (c.get("text") or "").startswith((f"{BOT} {FAILED_PREFIX}", f"{BOT} Held:"))), default=-1)
    return [c["text"] for c in comments[last_bot + 1:] if (c.get("text") or "").strip() and not c["text"].startswith(BOT)], comments


def test_request(feedback: list[str], cfg: Any) -> str | None:
    """'send me a test' (optionally with an address) → the address to send a [TEST] copy to."""
    text = " ".join(feedback)
    if not TEST_RE.search(text):
        return None
    m = EMAIL_IN_TEXT_RE.search(text)
    return (m.group(0) if m else None) or cfg.secret("REPORT_TO") or None


def send_test_copy(cfg: Any, asana: Asana, gmail, task_gid: str, card: dict[str, Any], to: str) -> bool:
    if gmail is None:
        asana.comment(task_gid, f"Can't send the test copy to {to}: Charles's Gmail isn't connected on this machine.")
        return False
    try:
        gmail.send(to, f"[TEST] {card['subject']}", f"(Test copy. The real email would go to {card['to']}.)\n\n{card['body']}")
    except Exception as e:  # noqa: BLE001
        asana.comment(task_gid, f"Test copy to {to} failed: {str(e)[:200]}")
        return False
    asana.comment(task_gid, f"Test copy sent to {to} from Charles's Gmail. Nothing went to the agent. Comment more feedback, or drag to Approved when it's good.")
    return True


def sync_feedback(conn, cfg: Any, asana: Asana, llm, gmail=None) -> dict[str, int]:
    """Rewrite To Review cards that got new feedback comments (or send a test copy when asked); mark Skip cards as skipped."""
    a = _cfg(cfg)
    n = {"revised": 0, "skipped": 0, "revise_failed": 0, "tests_sent": 0}
    for t in asana.section_tasks(a["to_review"]):
        card = parse_card(t.get("notes") or "")
        if not card:
            continue
        feedback, comments = pending_feedback(asana, t["gid"])
        if not feedback:
            continue
        test_to = test_request(feedback, cfg)
        if test_to and not [f for f in feedback if not TEST_RE.search(f)]:
            n["tests_sent"] += int(send_test_copy(cfg, asana, gmail, t["gid"], card, test_to))
            continue
        row = db.row_to_dict(conn.execute("SELECT property_ids FROM outreach WHERE id=?", (card["outreach_id"],)).fetchone()) or {}
        props = [db.row_to_dict(r) for r in conn.execute(
            f"SELECT * FROM properties WHERE id IN ({','.join('?' * len(json.loads(row.get('property_ids') or '[]'))) or 'NULL'})",
            json.loads(row.get("property_ids") or "[]"))]
        op = cfg["operator"]
        facts = [{k: p.get(k) for k in ("street", "city", "zip", "list_price", "original_list_price", "price_cuts", "days_on_market", "beds", "baths",
                                        "sqft", "lot_sqft", "year_built", "property_type", "vacant")} | {"listing_remarks_verbatim": (p.get("remarks") or "")[:2000]}
                 for p in props]
        out = llm.structured("revise_email", REVISE_SYSTEM.format(sender=op["sender_name"].split()[0], company=op["company"], protocol=protocol_text(cfg), voice=voice_text(cfg)),
                             json.dumps({"feedback": feedback, "subject": card["subject"], "body": card["body"], "properties": facts,
                                         "signature_block": signature(cfg)}, ensure_ascii=False), EMAIL_SCHEMA) if llm and llm.available else None
        if out is None:
            why = "Claude isn't reachable from this job" + (f": {llm.last_error[:160]}" if llm is not None and getattr(llm, "last_error", None) else
                                                            " (not logged in, or CLAUDE_CODE_OAUTH_TOKEN missing in .env)")
            problems = [why]
        else:
            problems = validate_email(out["subject"], out["body"], props)
        if problems:
            last = (comments[-1].get("text") or "") if comments else ""
            if not last.startswith(f"{BOT} {FAILED_PREFIX}"):      # say it once; the feedback stays pending and is retried next tick
                asana.comment(t["gid"], f"{FAILED_PREFIX} ({', '.join(problems)}). I'll retry on the next round; you can also edit the "
                                        f"text on the card yourself or comment again with different wording.")
            n["revise_failed"] += 1
            continue
        notes = (t["notes"] or "")
        notes = re.sub(r"^Subject:.*$", f"Subject: {out['subject']}", notes, count=1, flags=re.M)
        notes = re.sub(re.escape(START) + r"\n.*?\n" + re.escape(END), lambda _m: f"{START}\n{out['body']}\n{END}", notes, count=1, flags=re.S)
        asana.update_notes(t["gid"], notes)
        rev = sum(1 for c in comments if (c.get("text") or "").startswith(f"{BOT} Revised")) + 1
        _attach_picture(cfg, asana, t["gid"], card["to"], out["subject"], out["body"], card["outreach_id"], rev)
        conn.execute("UPDATE outreach SET subject=?, body=? WHERE id=?", (out["subject"], out["body"], card["outreach_id"]))
        asana.comment(t["gid"], f"Revised based on your comment (picture v{rev} attached). Drag to Approved when it's good.")
        n["revised"] += 1
        if test_to:
            n["tests_sent"] += int(send_test_copy(cfg, asana, gmail, t["gid"], {**card, "subject": out["subject"], "body": out["body"]}, test_to))
    for t in asana.section_tasks(a["skip"]):
        card = parse_card(t.get("notes") or "")
        if card and conn.execute("UPDATE outreach SET status='skipped' WHERE id=? AND status='pending_approval'", (card["outreach_id"],)).rowcount:
            n["skipped"] += 1
    return n


def in_window(cfg: Any, now: datetime | None = None) -> bool:
    w = _cfg(cfg).get("send_window", {})
    tz = ZoneInfo(w.get("timezone", "America/New_York"))
    t = (now or now_utc()).astimezone(tz)
    if t.weekday() not in w.get("days", [0, 1, 2, 3, 4, 5, 6]):
        return False
    start = tuple(int(x) for x in str(w.get("start", "08:30")).split(":"))
    end = tuple(int(x) for x in str(w.get("end", "17:30")).split(":"))
    return start <= (t.hour, t.minute) < end


def send_approved(conn, cfg: Any, asana: Asana, gmail, now: datetime | None = None, jitter_s: int = 0, llm=None) -> dict[str, Any]:
    """Send at most `per_tick` approved emails, only inside the send window and under the daily cap.
    A card in Approved that has new feedback (or a test request) is NOT sent: it goes back to To Review and the
    feedback is applied there, so a comment is never overtaken by a send."""
    from .outreach.send import ensure_thread, log_message
    a = _cfg(cfg)
    res = {"sent": 0, "waiting": 0, "errors": 0, "held_for_feedback": 0}
    approved = asana.section_tasks(a["approved"])
    res["waiting"] = len(approved)
    held = []
    for t in list(approved):
        feedback, _ = pending_feedback(asana, t["gid"])
        if feedback:
            asana.move(t["gid"], a["to_review"])
            approved.remove(t)
            held.append(t["gid"])
    if held:
        res["held_for_feedback"] = len(held)
        sync_feedback(conn, cfg, asana, llm, gmail)          # apply the comment now, not next tick
        for gid in held:
            asana.comment(gid, "Held: you left a comment after approving, so this went back to To Review and your feedback was applied "
                               "above (or the test copy was sent). Drag it to Approved again when it's good.")
    if not approved or not in_window(cfg, now):
        return res
    if gmail is None:
        res["errors"] += 1
        log.error("approved emails waiting but Gmail (Charles) is not connected")
        return res
    cap = int(cfg.get("outreach.daily_email_cap", 25))
    sent_today = conn.execute("SELECT COUNT(*) FROM outreach WHERE channel='email' AND status='sent' AND substr(sent_at,1,10)=?",
                              (now_utc().strftime("%Y-%m-%d"),)).fetchone()[0]
    if jitter_s:
        time.sleep(random.randint(0, jitter_s))       # vary the send times through the day
    for t in sorted(approved, key=lambda x: x.get("created_at") or "")[: max(0, min(int(a.get("per_tick", 2)), cap - sent_today))]:
        card = parse_card(t.get("notes") or "")
        if not card:
            asana.comment(t["gid"], "Couldn't read this card (the To/Subject/EMAIL lines were changed). Fix them and it will go out.")
            res["errors"] += 1
            continue
        row = db.row_to_dict(conn.execute("SELECT * FROM outreach WHERE id=?", (card["outreach_id"],)).fetchone())
        if not row or row["status"] == "sent":
            asana.move(t["gid"], a["sent"])
            continue
        if db.is_dnc(conn, agent_id=row["agent_id"], email=card["to"]):
            asana.comment(t["gid"], "Not sent: this agent asked not to be contacted.")
            asana.move(t["gid"], a["skip"])
            continue
        try:
            ids = gmail.send(card["to"], card["subject"], card["body"])
        except Exception as e:  # noqa: BLE001
            res["errors"] += 1
            asana.comment(t["gid"], f"Send failed, will retry next round: {str(e)[:200]}")
            continue
        with db.tx(conn):
            conn.execute("UPDATE outreach SET status='sent', subject=?, body=?, gmail_thread_id=?, gmail_message_id=?, sent_at=? WHERE id=?",
                         (card["subject"], card["body"], ids.get("thread_id"), ids.get("message_id"), iso(), row["id"]))
            pids = json.loads(row["property_ids"])
            tid = ensure_thread(conn, row["agent_id"], "email", ids.get("thread_id") or f"local-{row['id']}", pids)
            log_message(conn, tid, "out", card["body"], external_id=ids.get("message_id"))
        local = (now or now_utc()).astimezone(ZoneInfo(_cfg(cfg).get("send_window", {}).get("timezone", "America/New_York")))
        asana.comment(t["gid"], f"Sent {local.strftime('%a %b %d, %I:%M %p')} from {cfg.secret('GMAIL_SENDER') or 'Charles'}.")
        asana.move(t["gid"], a["sent"])
        res["sent"] += 1
    return res


def rewrite_pending(conn, cfg: Any, asana: Asana, llm, only_old_style: bool = True) -> dict[str, int]:
    """Regenerate emails still waiting for approval with the current writer and refresh their cards (text, picture, comment).
    By default only cards written before the protocol (no stored analysis); all=True redoes every To Review card.
    Approved/sent cards are left alone."""
    a = _cfg(cfg)
    n = {"rewritten": 0, "failed": 0, "skipped_already_protocol": 0, "left_to_feedback": 0, "card_deleted": 0}
    rows = [db.row_to_dict(r) for r in conn.execute(
        "SELECT * FROM outreach WHERE status='pending_approval' AND asana_task_id IS NOT NULL ORDER BY id").fetchall()]
    for row in rows:
        if only_old_style and (row.get("analysis") or "").strip() not in ("", "{}"):
            n["skipped_already_protocol"] += 1
            continue
        try:
            feedback, comments = pending_feedback(asana, row["asana_task_id"])
        except AsanaError as e:                      # card deleted on the board: nothing to rewrite, don't send it either
            log.warning("card for outreach %s is gone (%s); marking skipped", row["id"], e)
            conn.execute("UPDATE outreach SET status='skipped' WHERE id=?", (row["id"],))
            n["card_deleted"] += 1
            continue
        if feedback or any((c.get("text") or "").startswith(f"{BOT} Revised based on your comment") for c in comments):
            n["left_to_feedback"] += 1              # Rodrigo's own feedback shapes this one; sync_feedback owns it
            continue
        agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (row["agent_id"],)).fetchone()) or {}
        pids = json.loads(row["property_ids"] or "[]")
        props = [db.row_to_dict(r) for r in conn.execute(
            f"SELECT p.*, v.lane, v.mao, v.arv, v.arv_method, v.comp_count, v.repair_cost, v.repair_tier FROM properties p "
            f"LEFT JOIN valuations v ON v.property_id=p.id WHERE p.id IN ({','.join('?' * len(pids)) or 'NULL'})", pids)]
        if not props:
            continue
        props = props[: int(cfg.get("outreach.max_properties_per_email", 2))]
        try:
            d = compose_email(llm, cfg, agent, props, seed_for(agent["id"], pids, today_et()))
        except Exception as e:  # noqa: BLE001
            n["failed"] += 1
            log.error("rewrite of outreach %s failed: %s", row["id"], e)
            continue
        conn.execute("UPDATE outreach SET subject=?, body=?, variant=?, analysis=? WHERE id=?",
                     (d["subject"], d["body"], d["variant"], json.dumps(d.get("analysis") or {}), row["id"]))
        asana.update_notes(row["asana_task_id"], card_notes(row["to_address"], d["subject"], d["body"], row["id"],
                                                             listing_meta(props[0], agent, d.get("analysis"))))
        _attach_picture(cfg, asana, row["asana_task_id"], row["to_address"], d["subject"], d["body"], row["id"], rev=99)
        asana.comment(row["asana_task_id"], "Rewritten under the new outreach protocol (think first, no listing echo). New picture attached.")
        n["rewritten"] += 1
    return n
