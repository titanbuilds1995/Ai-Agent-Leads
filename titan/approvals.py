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
from .asana import BOT, Asana
from .outreach.compose import EMAIL_SCHEMA, compose_email, protocol_text, seed_for, signature, validate_email
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
The outreach protocol below still applies in full (no price/offer/percentage, no listing echo, no manufactured observations,
one easy question, 50-110 words, plain text, signature block unchanged).

=== OUTREACH PROTOCOL ===
{protocol}
=== END PROTOCOL ===
Return JSON: subject and body (body includes the unchanged signature block)."""


def sync_feedback(conn, cfg: Any, asana: Asana, llm) -> dict[str, int]:
    """Rewrite To Review cards that got new feedback comments; mark Skip cards as skipped."""
    a = _cfg(cfg)
    n = {"revised": 0, "skipped": 0, "revise_failed": 0}
    for t in asana.section_tasks(a["to_review"]):
        card = parse_card(t.get("notes") or "")
        if not card:
            continue
        comments = asana.comments(t["gid"])
        last_bot = max((i for i, c in enumerate(comments) if (c.get("text") or "").startswith(BOT)), default=-1)
        feedback = [c["text"] for c in comments[last_bot + 1:] if (c.get("text") or "").strip() and not c["text"].startswith(BOT)]
        if not feedback:
            continue
        op = cfg["operator"]
        out = llm.structured("revise_email", REVISE_SYSTEM.format(sender=op["sender_name"].split()[0], company=op["company"], protocol=protocol_text(cfg)),
                             json.dumps({"feedback": feedback, "subject": card["subject"], "body": card["body"],
                                         "signature_block": signature(cfg)}, ensure_ascii=False), EMAIL_SCHEMA) if llm and llm.available else None
        row = db.row_to_dict(conn.execute("SELECT property_ids FROM outreach WHERE id=?", (card["outreach_id"],)).fetchone()) or {}
        props = [db.row_to_dict(r) for r in conn.execute(
            f"SELECT * FROM properties WHERE id IN ({','.join('?' * len(json.loads(row.get('property_ids') or '[]'))) or 'NULL'})",
            json.loads(row.get("property_ids") or "[]"))]
        problems = validate_email(out["subject"], out["body"], props) if out else ["no rewrite"]
        if problems:
            asana.comment(t["gid"], f"I couldn't rewrite this one automatically ({', '.join(problems)}). Edit the text on the card, "
                                    f"or comment again with different wording.")
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


def send_approved(conn, cfg: Any, asana: Asana, gmail, now: datetime | None = None, jitter_s: int = 0) -> dict[str, Any]:
    """Send at most `per_tick` approved emails, only inside the send window and under the daily cap."""
    from .outreach.send import ensure_thread, log_message
    a = _cfg(cfg)
    res = {"sent": 0, "waiting": 0, "errors": 0}
    approved = asana.section_tasks(a["approved"])
    res["waiting"] = len(approved)
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


def rewrite_pending(conn, cfg: Any, asana: Asana, llm) -> dict[str, int]:
    """Regenerate every email still waiting for approval with the current writer (e.g. after a protocol change) and
    refresh its card: text, picture and a comment. Approved/sent cards are left alone."""
    a = _cfg(cfg)
    n = {"rewritten": 0, "failed": 0}
    rows = [db.row_to_dict(r) for r in conn.execute(
        "SELECT * FROM outreach WHERE status='pending_approval' AND asana_task_id IS NOT NULL ORDER BY id").fetchall()]
    for row in rows:
        agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (row["agent_id"],)).fetchone()) or {}
        pids = json.loads(row["property_ids"] or "[]")
        props = [db.row_to_dict(r) for r in conn.execute(
            f"SELECT p.*, v.lane, v.arv, v.arv_method, v.comp_count, v.repair_cost, v.repair_tier FROM properties p "
            f"LEFT JOIN valuations v ON v.property_id=p.id WHERE p.id IN ({','.join('?' * len(pids)) or 'NULL'})", pids)]
        if not props:
            continue
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
