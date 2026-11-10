"""OM requests to brokers and what comes back (Rodrigo, 2026-11-09: "the most important thing is seeing who communicates the
fastest and can send us the documents ASAP").

SEND: a card dragged to "Approved · send OM request" on the 1031 board is emailed by Charles@ at the next approvals round, inside
the send window, counting against the daily email cap, `per_tick` at a time. The thread is remembered in `commercial_threads`,
the card moves to "In touch with the agent" with a 📤 comment. Nothing sends from any other column.

READ: every check-replies run opens those threads. A real answer is mirrored to the card (📩, who, when, how many hours it took),
every attachment is filed in data/listings/<listing_id>/, and when the books arrive (OM / T-12 / rent roll / P&L) the listing is
regraded on the spot (evidence grade, tag, offer anchor) and the card moves to "Books received". A bounce is marked, never
counted as an answer. The response clock (hours_to_reply) feeds the site and the deals email."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import db
from ..util import iso, log, now_utc, parse_iso
from . import documents, scoring
from .board import SEND, ensure_board
from .fl_geo import region_for

START, END = "=== EMAIL (what will be sent) ===", "=== END EMAIL ==="
LISTING_URL_RE = re.compile(r"loopnet\.com/Listing/(?:[^/\s]+/)?(\d+)/?", re.I)
BOUNCE_RE = re.compile(r"mailer-daemon|postmaster|delivery (status )?notification|undeliverable|delivery failure|inbox is full|address not found", re.I)
AUTOREPLY_RE = re.compile(r"out of (the )?office|auto-?reply|automatic reply|away from (my )?(email|desk)|on vacation", re.I)
BOOK_KINDS = {"om", "t12", "pnl", "rent_roll", "lease_abstract"}


def parse_om_card(notes: str) -> dict[str, Any] | None:
    to = re.search(r"^To:\s*(\S+@\S+)\s*$", notes or "", re.M)
    subj = re.search(r"^Subject:\s*(.+)$", notes or "", re.M)
    body = re.search(re.escape(START) + r"\n(.*?)\n" + re.escape(END), notes or "", re.S)
    lid = LISTING_URL_RE.search(notes or "")
    if not (to and subj and body and lid):
        return None
    return {"to": to.group(1).strip(), "subject": subj.group(1).strip(), "body": body.group(1).strip(), "listing_id": lid.group(1)}


def _sent_today(conn, run_date: str) -> int:
    """Commercial emails sent today (OM requests + the no-cap blast); the residential cap counts its own."""
    return conn.execute("SELECT COUNT(*) FROM outreach WHERE channel='email' AND status='sent' AND kind='om_request' AND substr(sent_at,1,10)=?",
                        (run_date,)).fetchone()[0]


def pace(cfg: Any) -> tuple[int, int]:
    """(per round, per day) for commercial sends. Rodrigo, 2026-11-09: faster than the residential 5 a round, 'but not too too fast'."""
    per_round = int(cfg.get("commercial.outreach.per_round") or cfg.get("outreach.asana.per_tick", 2))
    cap = int(cfg.get("commercial.outreach.daily_cap") or cfg.get("outreach.daily_email_cap", 25))
    return per_round, cap


def send_om_requests(conn, cfg: Any, asana, gmail, now: datetime | None = None) -> dict[str, Any]:
    """Email the cards in the send column, per_tick at a time, in the window, under the cap. Idempotent per listing."""
    from ..approvals import in_window
    res: dict[str, Any] = {"sent": 0, "waiting": 0, "errors": 0, "skipped": 0}
    project = cfg.get("commercial.asana.project")
    if not project or asana is None:
        return res
    from .blast import pending, send_queue
    secs = ensure_board(asana, project)
    cards = asana.section_tasks(secs[SEND])
    res["waiting"] = len(cards)
    res["queue_waiting"] = len(pending(conn, cfg))
    if not cards and not res["queue_waiting"]:
        return res
    if not in_window(cfg, now):
        res["reason"] = "outside the send window"
        return res
    if gmail is None:
        res["reason"] = "Gmail not connected"
        return res
    now = now or now_utc()
    run_date = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
    per_round, cap = pace(cfg)
    room = max(0, min(per_round, cap - _sent_today(conn, run_date)))
    for t in sorted(cards, key=lambda x: x.get("created_at") or "")[:room]:
        notes = t.get("notes") or ""
        if not notes:
            notes = (asana.task(t["gid"]) or {}).get("notes") or ""
        card = parse_om_card(notes)
        if not card:
            asana.comment(t["gid"], "This card has no OM-request email on it (To / Subject / EMAIL lines), so nothing was sent. Moved back to New.")
            asana.move(t["gid"], secs["New"])
            res["skipped"] += 1
            continue
        if conn.execute("SELECT 1 FROM commercial_threads WHERE listing_id=? AND bounced=0", (card["listing_id"],)).fetchone():
            asana.comment(t["gid"], "Already emailed this broker about this listing; not sending twice. Moved to In touch with the agent.")
            asana.move(t["gid"], secs["In touch with the agent"])
            res["skipped"] += 1
            continue
        try:
            sent = gmail.send(card["to"], card["subject"], card["body"])
        except Exception as e:  # noqa: BLE001
            res["errors"] += 1
            log.error("OM request send failed for %s: %s", card["listing_id"], e)
            asana.comment(t["gid"], f"Could not send (Gmail: {str(e)[:160]}). Still in the send column; the next round retries.")
            continue
        ts = iso(now)
        cur = conn.execute("INSERT INTO outreach (agent_id, property_ids, channel, kind, status, subject, body, to_address, gmail_thread_id, "
                           "gmail_message_id, run_date, created_at, sent_at, asana_task_id) VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (json.dumps([f"loopnet:{card['listing_id']}"]), "email", "om_request", "sent", card["subject"], card["body"], card["to"],
                            sent.get("thread_id"), sent.get("message_id"), run_date, ts, ts, t["gid"]))
        row = conn.execute("SELECT broker_name FROM commercial_listings WHERE listing_id=?", (card["listing_id"],)).fetchone()
        conn.execute("INSERT INTO commercial_threads (listing_id, broker_email, broker_name, gmail_thread_id, gmail_message_id, outreach_id, subject, "
                     "sent_at, state, card_gid, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (card["listing_id"], card["to"].lower(), row[0] if row else None, sent.get("thread_id"), sent.get("message_id"), cur.lastrowid,
                      card["subject"], ts, "SENT", t["gid"], ts, ts))
        conn.execute("UPDATE commercial_listings SET board_column=? WHERE listing_id=?", ("In touch with the agent", card["listing_id"]))
        asana.comment(t["gid"], f"📤 Sent to {card['to']} at {now.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC. Waiting for the books; "
                               f"the reply clock is running.")
        asana.move(t["gid"], secs["In touch with the agent"])
        res["sent"] += 1
    left = room - res["sent"]
    if left > 0 and res["queue_waiting"]:     # the no-cap blast rides the same round, after the cards
        q = send_queue(conn, cfg, gmail, left, now=now)
        res["queue_sent"], res["queue_waiting"], res["errors"] = q["sent"], q["waiting"], res["errors"] + q["errors"]
    return res


def _is_ours(from_addr: str, cfg: Any) -> bool:
    f = (from_addr or "").lower()
    for k in ("GMAIL_SENDER", "REPORT_TO"):
        v = (cfg.secret(k) or "").lower()
        if v and v in f:
            return True
    dom = (cfg.get("operator.sender_email") or "").split("@")[-1].lower()
    return bool(dom and f"@{dom}" in f)


def _from_rodrigo(from_addr: str, cfg: Any) -> bool:
    me = (cfg.secret("REPORT_TO") or "").lower()
    return bool(me and me in (from_addr or "").lower())


def record_rodrigo_message(conn, cfg: Any, asana, secs: dict[str, str], t: dict[str, Any], m: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Rodrigo wrote to the broker himself in the OM thread (2026-11-10, Byronessa: Charles cc'd him and he answered Luca). The card
    shows it, a dollar figure in his words is an offer (card → Offer out), and the thread becomes his: Charles sends nothing there on
    his own any more and it is never archived. Logged in `outreach` as kind om_rodrigo (one row per Gmail message)."""
    from ..offers import offer_price
    from ..replies.clean import clean_reply
    ts = datetime.fromtimestamp(m.get("internal_ts") or 0, tz=timezone.utc)
    body = clean_reply(m.get("body") or "")
    row = conn.execute("SELECT price FROM commercial_listings WHERE listing_id=?", (t["listing_id"],)).fetchone()
    ask = row[0] if row and row[0] else None
    price = offer_price(body, ask)
    if price and price < (0.25 * ask if ask else 250_000):
        price = None                       # a deposit, a tax bill or a payroll figure, not a purchase price
    conn.execute("INSERT INTO outreach (agent_id, property_ids, channel, kind, status, subject, body, to_address, gmail_thread_id, gmail_message_id, "
                 "run_date, created_at, sent_at, asana_task_id) VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (json.dumps([f"loopnet:{t['listing_id']}"]), "email", "om_rodrigo", "sent", m.get("subject") or t.get("subject"), body[:5000],
                  t.get("broker_email"), t.get("gmail_thread_id"), m["id"], iso(ts)[:10], iso(now), iso(ts), t.get("card_gid")))
    conn.execute("UPDATE commercial_threads SET rodrigo_at=COALESCE(rodrigo_at, ?), updated_at=? WHERE id=?", (iso(ts), iso(now), t["id"]))
    t["rodrigo_at"] = t.get("rodrigo_at") or iso(ts)
    if price:
        conn.execute("UPDATE commercial_listings SET board_column='Offer out' WHERE listing_id=? AND board_column IS NOT 'Closed'", (t["listing_id"],))
    if asana is not None and t.get("card_gid"):
        note = (f"✉️ You wrote to {t.get('broker_name') or t.get('broker_email')} ({ts.strftime('%Y-%m-%d %H:%M')} UTC):\n{body[:1500]}"
                + (f"\n\nOffer out: ${price:,}" + (f" ({price / ask * 100:.0f}% of the ${ask:,} ask)" if ask else "") + "." if price else "")
                + "\n\nThis thread is yours now: Charles sends nothing here on his own (his drafts wait for your 'send it').")
        asana.comment(t["card_gid"], note)
        if price and secs.get("Offer out"):
            asana.move(t["card_gid"], secs["Offer out"])
        elif not price:                     # he answered the broker himself: his move is done
            from .books_review import _unpark
            _unpark(conn, cfg, asana, t["listing_id"], t["card_gid"])
    return {"price": price}


def file_attachments(gmail, message: dict[str, Any], folder: Path) -> list[dict[str, Any]]:
    """Save every file on one message into the listing's folder. Returns [{filename, kind, path}]."""
    saved = []
    try:
        files = gmail.attachments(message["id"])
    except Exception as e:  # noqa: BLE001
        log.warning("attachments for %s failed: %s", message.get("id"), e)
        return saved
    folder.mkdir(parents=True, exist_ok=True)
    for f in files:
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", f.get("filename") or "attachment")[:120]
        if not name.lower().endswith((".pdf", ".xlsx", ".xls", ".csv", ".docx", ".txt")):
            continue
        path = folder / name
        path.write_bytes(f["bytes"])
        saved.append({"filename": name, "kind": documents.classify(name), "path": str(path), "mime": f.get("mime")})
    return saved


def regrade(conn, cfg: Any, listing_id: str) -> dict[str, Any] | None:
    """Re-read the listing's folder (the books that just arrived) and re-score it. Returns the new verdict."""
    row = db.row_to_dict(conn.execute("SELECT * FROM commercial_listings WHERE listing_id=?", (listing_id,)).fetchone())
    if not row:
        return None
    L = json.loads(row.get("data") or "{}")
    L["raw"] = json.loads(row.get("raw") or "{}")
    L.update(documents.doc_facts(L, cfg.root / cfg.get("commercial.documents.dir", "data/listings"), enabled=False))
    if L.get("doc_noi"):
        L["stated_noi"] = L["doc_noi"]
        L["trailing"] = True
    if L.get("doc_occupancy") and not L.get("occupancy"):
        L["occupancy"] = L["doc_occupancy"]
    if L.get("doc_lease_type") and not L.get("lease_type"):
        L["lease_type"] = L["doc_lease_type"]
    if L.get("doc_tenants"):
        L["tenant_names"] = L["doc_tenants"]
    if L.get("doc_gross"):
        L["gross_income"] = L["doc_gross"]
        from . import extract as x
        L["expense_ratio"] = x.expense_ratio(L.get("stated_noi"), L["doc_gross"])
    L["region"] = region_for(L.get("county"))
    L.update(scoring.score(L, cfg))
    from .run import store
    store(conn, L, now_utc().strftime("%Y-%m-%d"))
    return L


def verdict_text(L: dict[str, Any]) -> str:
    from ..util import money
    lines = [f"Regraded with the books: {L.get('tag')} · evidence grade {L.get('evidence_grade')} ({L.get('evidence_note')}) · score {L.get('score')}."]
    if L.get("stated_noi"):
        lines.append(f"NOI from the documents {money(L['stated_noi'])}" + (f" · back-solved cap {L['back_solved_cap']:.2f}%" if L.get("back_solved_cap") else "")
                     + (f" · OFFER ANCHOR {money(L['offer_anchor'])}" if L.get("offer_anchor") else ""))
    if L.get("tag_reasons"):
        lines.append("Why: " + "; ".join(L["tag_reasons"]))
    if L.get("tenant_names"):
        lines.append("Tenants on the rent roll: " + ", ".join(L["tenant_names"][:12]))
    if L.get("docs_missing"):
        lines.append("Still missing: " + ", ".join(L["docs_missing"]))
    return "\n".join(lines)


def poll_om_replies(conn, cfg: Any, gmail, asana=None, now: datetime | None = None) -> dict[str, Any]:
    """Open every OM thread, mirror new answers to the card, file attachments, regrade when the books are in."""
    res: dict[str, Any] = {"threads": 0, "replies": 0, "books": 0, "bounces": 0, "rodrigo": 0, "offers": 0, "errors": 0}
    if gmail is None:
        return res
    now = now or now_utc()
    project = cfg.get("commercial.asana.project")
    secs = ensure_board(asana, project) if (asana is not None and project) else {}
    doc_dir = cfg.root / cfg.get("commercial.documents.dir", "data/listings")
    for t in conn.execute("SELECT * FROM commercial_threads WHERE gmail_thread_id IS NOT NULL AND state NOT IN ('CLOSED')").fetchall():
        t = db.row_to_dict(t)
        res["threads"] += 1
        try:
            msgs = gmail.thread_messages(t["gmail_thread_id"])
        except Exception as e:  # noqa: BLE001
            res["errors"] += 1
            log.error("OM thread %s: %s", t["gmail_thread_id"], e)
            continue
        for m in sorted(msgs, key=lambda x: x.get("internal_ts", 0)):
            if "DRAFT" in (m.get("label_ids") or []):
                continue
            if _from_rodrigo(m.get("from"), cfg):
                if not conn.execute("SELECT 1 FROM outreach WHERE kind='om_rodrigo' AND gmail_message_id=?", (m["id"],)).fetchone():
                    got = record_rodrigo_message(conn, cfg, asana, secs, t, m, now)
                    res["rodrigo"] += 1
                    res["offers"] += 1 if got["price"] else 0
                continue
            if _is_ours(m.get("from"), cfg):
                continue
            if conn.execute("SELECT 1 FROM commercial_messages WHERE gmail_message_id=?", (m["id"],)).fetchone():
                continue
            ts = datetime.fromtimestamp(m.get("internal_ts") or 0, tz=timezone.utc)
            body = (m.get("body") or "").strip()
            conn.execute("INSERT OR IGNORE INTO commercial_messages (thread_id, gmail_message_id, from_addr, body, created_at) VALUES (?,?,?,?,?)",
                         (t["id"], m["id"], (m.get("from") or "")[:200], body[:5000], iso(ts)))
            if BOUNCE_RE.search(m.get("from") or "") or BOUNCE_RE.search(body[:400]):
                conn.execute("UPDATE commercial_threads SET bounced=1, state='BOUNCED', updated_at=? WHERE id=?", (iso(now), t["id"]))
                res["bounces"] += 1
                if asana is not None and t.get("card_gid"):
                    asana.comment(t["card_gid"], f"Bounced: {t['broker_email']} did not take the email. Call the broker or find another address.")
                continue
            if AUTOREPLY_RE.search(body[:400]) or AUTOREPLY_RE.search(m.get("subject") or ""):
                continue
            sent_at = parse_iso(t["sent_at"]) or now
            hours = round((ts - sent_at).total_seconds() / 3600, 1)
            first = t.get("first_reply_at") is None
            files = file_attachments(gmail, m, doc_dir / t["listing_id"]) if m.get("attachments") else []
            books = [f for f in files if f["kind"] in BOOK_KINDS or f["filename"].lower().endswith((".xlsx", ".xls", ".csv"))]
            att = json.loads(t.get("attachments") or "[]") + [{**f, "message_id": m["id"], "at": iso(ts)} for f in files]
            conn.execute("UPDATE commercial_threads SET first_reply_at=COALESCE(first_reply_at, ?), last_reply_at=?, hours_to_reply=COALESCE(hours_to_reply, ?), "
                         "reply_count=reply_count+1, attachments=?, last_reply_text=?, state=?, updated_at=? WHERE id=?",
                         (iso(ts), iso(ts), hours, json.dumps(att), body[:2000], "BOOKS" if (books or t.get("state") == "BOOKS") else "REPLIED", iso(now), t["id"]))
            t["first_reply_at"] = t.get("first_reply_at") or iso(ts)
            res["replies"] += 1
            column = "Books received" if books else "In touch with the agent"
            conn.execute("UPDATE commercial_listings SET board_column=? WHERE listing_id=? AND board_column NOT IN ('Offer out','Closed','Not a fit')",
                         (column, t["listing_id"]))
            note = (f"📩 {t.get('broker_name') or t['broker_email']} answered" + (f" in {hours:g} h" if first else "") + f" ({ts.strftime('%Y-%m-%d %H:%M')} UTC):\n"
                    f"{body[:1200]}" + (f"\n\nFiles filed under data/listings/{t['listing_id']}/: " + ", ".join(f["filename"] for f in files) if files else ""))
            verdict, regrade_err = None, None
            if books:
                res["books"] += 1
                try:
                    verdict = regrade(conn, cfg, t["listing_id"])
                except Exception as e:  # noqa: BLE001
                    log.exception("regrade %s failed: %s", t["listing_id"], e)
                    regrade_err = f"{type(e).__name__}: {e}"[:200]
            if asana is not None and not t.get("card_gid") and secs.get(column):
                t["card_gid"] = _card_for_thread(conn, cfg, asana, project, secs[column], t)
            if asana is not None and t.get("card_gid"):
                asana.comment(t["card_gid"], note + (("\n\n" + verdict_text(verdict)) if verdict else "")
                              + (f"\n\nRegrade failed ({regrade_err}); the books review retries it." if regrade_err else ""))
                if secs.get(column):
                    cur = conn.execute("SELECT board_column FROM commercial_listings WHERE listing_id=?", (t["listing_id"],)).fetchone()
                    if cur and cur[0] == column:
                        asana.move(t["card_gid"], secs[column])
    return res


def _card_for_thread(conn, cfg: Any, asana, project: str, section: str, t: dict[str, Any]) -> str | None:
    """A blast email got a real answer: now the listing earns its card (no card was made when the email went out)."""
    from .asana_push import card_name, card_notes
    row = conn.execute("SELECT data FROM commercial_listings WHERE listing_id=?", (t["listing_id"],)).fetchone()
    try:
        L = json.loads(row[0]) if row and row[0] else {}
    except json.JSONDecodeError:
        L = {}
    if not L:
        return None
    L.setdefault("listing_id", t["listing_id"])
    try:
        ids = json.loads(t.get("listing_ids") or "[]")
    except json.JSONDecodeError:
        ids = []
    extra = f"\n\nThe email also asked about listing(s) {', '.join(i for i in ids if i != t['listing_id'])}." if len(ids) > 1 else ""
    try:
        made = asana.create_task(project, section, "OM request · " + card_name(L), card_notes(cfg, L, with_email=False) + extra)
    except Exception as e:  # noqa: BLE001
        log.error("card for thread %s failed: %s", t.get("id"), e)
        return None
    gid = made.get("gid")
    conn.execute("UPDATE commercial_threads SET card_gid=? WHERE id=?", (gid, t["id"]))
    return gid


def response_clock(conn) -> dict[str, dict[str, Any]]:
    """{listing_id: {sent_at, hours_to_reply, state, reply_count}} for the site and the email."""
    out = {}
    for r in conn.execute("SELECT listing_id, sent_at, first_reply_at, hours_to_reply, state, reply_count, bounced FROM commercial_threads").fetchall():
        out[r[0]] = {"sent_at": r[1], "first_reply_at": r[2], "hours_to_reply": r[3], "state": r[4], "reply_count": r[5], "bounced": bool(r[6])}
    return out
