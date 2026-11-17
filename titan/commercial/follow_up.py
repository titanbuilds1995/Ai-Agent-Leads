"""One follow-up to every 1031 broker who never answered Charles's first email (Rodrigo, 2026-11-17: "let's FU with everyone
that did not answer on the first commercial email we sent").

Who gets it: a broker with a first email out (commercial_threads, state SENT), no reply, no bounce, sent at least `min_days` ago,
who has not answered us in ANY of their threads and whom Rodrigo is not already talking to. Once per broker, in the broker's own
Gmail thread, so their mail client shows it under the first note. Before each send the Gmail thread itself is opened: if the
broker wrote anything there (an answer the database missed), nothing goes.
Who never gets it: a thread Rodrigo wrote in, a card on Working Deals or in his columns (Offer out / Closed / Not a fit / Reviewed /
Needs Revision / Good fit / Needs Me), a bounced or closed thread, a do-not-contact address.

Same voice and house rules as the first email (validate_om_request): greeting on its own line, Charles introduces himself, one
question, no numbers, no dashes, no links. Paced with the rest of the commercial sends: per_round at a time, inside the send window,
under the shared daily cap.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from .. import db
from ..util import iso, log, now_utc
from .board import NEEDS_ME, NEEDS_REVISION, NOT_A_FIT, REVIEWED
from .books_review import _first_name, _signature
from .om_request import validate_om_request
from ..outreach.compose import format_paragraphs

KIND = "om_follow_up"
VARIANT = "follow_up_1"
HIS_COLUMNS = ("Offer out", "Closed", NOT_A_FIT, REVIEWED, NEEDS_REVISION, "Good fit", NEEDS_ME)
DOCS = "the trailing twelve, the rent roll and the offering memorandum"


def _key(email: str) -> str:
    return f"follow_up:{(email or '').strip().lower()}"


def follow_up_body(first: str, place: str, shows_cap: bool, several: bool = False, cfg: Any = None) -> str:
    if several:
        ask = ("Does any of them hold an eight cap or better at asking once it sells and the taxes are reassessed? "
               f"If one does, send over {DOCS} for that one and you will have a quick answer.")
    elif shows_cap:
        ask = f"Could you send over {DOCS} so we can verify the cap holds up?"
    else:
        ask = ("At the asking price, does it hold an eight cap or better once it sells and the taxes are reassessed? "
               f"If it does, send over {DOCS} and you will have a quick answer.")
    body = (f"Hey {first},\n\n"
            f"Charles with Titan Realty Investments, following up on my note last week about {place}.\n\n"
            f"We are still placing the 1031 exchange and the clock is running, so I need to know if this one is in or out. {ask}\n\n"
            "If it is under contract or off the market, a one line reply saves us both the back and forth.\n\n"
            f"Best regards,\n\n{_signature(cfg)}")
    return format_paragraphs(body)


def _listing(conn, listing_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT street, city, stated_cap, data FROM commercial_listings WHERE listing_id=?", (listing_id,)).fetchone()
    if not row:
        return {}
    out = {"street": row[0], "city": row[1], "stated_cap": row[2]}
    try:
        d = json.loads(row[3] or "{}")
    except json.JSONDecodeError:
        d = {}
    for k in ("street", "city", "stated_cap"):
        out[k] = out.get(k) or d.get(k)
    return out


def _place(conn, t: dict[str, Any]) -> tuple[str, bool, bool]:
    """(where, the listing shows a cap, several listings in the first email)."""
    try:
        ids = [str(i) for i in json.loads(t.get("listing_ids") or "[]")]
    except json.JSONDecodeError:
        ids = []
    if len(ids) > 1:
        return "your listings on LoopNet", False, True
    L = _listing(conn, str(t.get("listing_id") or (ids[0] if ids else "")))
    if not (L.get("street") or "").strip():
        return "your listing on LoopNet", bool(L.get("stated_cap")), False
    from .om_request import _where                    # "Portfolio of 3 Properties" -> "the 3-property listing in Tampa" (portfolio is a banned word)
    return _where(L), bool(L.get("stated_cap")), False


def candidates(conn, cfg: Any, now: datetime | None = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Brokers owed the follow-up (one thread per broker, the oldest first email first) and why the others were left out."""
    now = now or now_utc()
    cutoff = iso(now - timedelta(days=float(cfg.get("commercial.follow_up.min_days", 3) or 0)))
    skipped = {"answered": 0, "rodrigo_is_talking": 0, "already_sent": 0, "too_recent": 0, "bounced_or_closed": 0, "do_not_contact": 0}
    rows = [db.row_to_dict(r) for r in conn.execute(
        "SELECT * FROM commercial_threads WHERE broker_email IS NOT NULL AND broker_email<>'' ORDER BY sent_at, id")]
    answered = {(t["broker_email"] or "").lower() for t in rows if t.get("first_reply_at") or (t.get("reply_count") or 0) > 0}
    talking = {(t["broker_email"] or "").lower() for t in rows if t.get("rodrigo_at")}
    dnc = {(r[0] or "").lower() for r in conn.execute("SELECT email FROM do_not_contact WHERE email IS NOT NULL")}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for t in rows:
        email = (t["broker_email"] or "").strip().lower()
        if email in seen:
            continue
        if (t.get("state") or "").upper() != "SENT" or t.get("bounced") or t.get("cleared_at"):
            skipped["bounced_or_closed"] += 1
            continue
        if t.get("first_reply_at"):
            continue
        seen.add(email)
        if email in dnc:
            skipped["do_not_contact"] += 1
        elif conn.execute("SELECT 1 FROM commercial_kv WHERE key=?", (_key(email),)).fetchone():
            skipped["already_sent"] += 1
        elif email in talking:
            skipped["rodrigo_is_talking"] += 1
        elif email in answered:
            skipped["answered"] += 1
        elif (t.get("sent_at") or "") > cutoff:
            skipped["too_recent"] += 1
        else:
            out.append(t)
    return out, skipped


def _broker_wrote(gmail, cfg: Any, t: dict[str, Any]) -> bool | None:
    """Gmail's own word: did the broker (anyone not on our side) write in this thread? None = could not look."""
    from .outreach import _is_ours
    if not t.get("gmail_thread_id"):
        return False
    try:
        msgs = gmail.thread_messages(t["gmail_thread_id"])
    except Exception as e:  # noqa: BLE001
        log.warning("follow-up: thread %s: %s", t["gmail_thread_id"], e)
        return None
    for m in msgs:
        if "DRAFT" in (m.get("label_ids") or []):
            continue
        if not _is_ours(m.get("from"), cfg):
            return True
    return False


def _sent_today(conn, run_date: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM outreach WHERE channel='email' AND status='sent' AND kind IN ('om_request', ?) AND substr(sent_at,1,10)=?",
                        (KIND, run_date)).fetchone()[0]


def send_follow_ups(conn, cfg: Any, asana, gmail, now: datetime | None = None) -> dict[str, Any]:
    from ..approvals import in_window
    from .closeout import columns_by_card
    res: dict[str, Any] = {"sent": 0, "waiting": 0, "errors": 0, "skipped": {}}
    if not cfg.get("commercial.follow_up.enabled", False):
        return res
    now = now or now_utc()
    todo, res["skipped"] = candidates(conn, cfg, now)
    res["waiting"] = len(todo)
    if not todo:
        return res
    if gmail is None:
        res["reason"] = "Gmail not connected"
        return res
    if not in_window(cfg, now):
        res["reason"] = "outside the send window"
        return res
    where: dict[str, str] = {}
    project = cfg.get("commercial.asana.project")
    if asana is not None and project:
        try:
            from .board import ensure_board
            where = columns_by_card(asana, ensure_board(asana, project))
        except Exception as e:  # noqa: BLE001
            log.warning("follow-up: board columns: %s", e)
            res["reason"] = "could not read the board"     # without the board we cannot tell which brokers are his: send nothing
            return res
    run_date = now.strftime("%Y-%m-%d")
    per_round = int(cfg.get("commercial.follow_up.per_round") or cfg.get("commercial.outreach.per_round") or 3)
    cap = int(cfg.get("commercial.outreach.daily_cap") or 60)
    room = max(0, min(per_round, cap - _sent_today(conn, run_date)))
    if room <= 0:
        res["reason"] = "daily cap reached"
        return res
    for t in todo:
        if res["sent"] >= room:
            break
        gid, email = t.get("card_gid"), t["broker_email"].strip()
        mine = bool(gid) and (where.get(gid) in HIS_COLUMNS or (hasattr(asana, "on_working") and asana.on_working(gid)))
        wrote = None if mine else _broker_wrote(gmail, cfg, t)
        if mine or wrote:
            conn.execute("INSERT OR IGNORE INTO commercial_kv (key, value) VALUES (?,?)", (_key(email), "skipped: " + ("his card" if mine else "broker answered in Gmail")))
            res["skipped"]["rodrigo_is_talking" if mine else "answered"] += 1
            res["waiting"] -= 1
            continue
        if wrote is None:
            res["errors"] += 1                     # could not open the thread: try again next round rather than guess
            continue
        ts = iso(now)
        if not conn.execute("INSERT OR IGNORE INTO commercial_kv (key, value) VALUES (?,?)", (_key(email), ts)).rowcount:
            continue                               # another round took this broker: never two follow-ups
        place, shows_cap, several = _place(conn, t)
        body = follow_up_body(_first_name(t.get("broker_name"), email), place, shows_cap, several, cfg)
        subject = t.get("subject") or "Your listing on LoopNet"
        subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        problems = validate_om_request(subject, body)
        if problems and all(p.startswith("length") for p in problems):   # a long street name: say "your listing" instead
            body = follow_up_body(_first_name(t.get("broker_name"), email), "your listing on LoopNet", shows_cap, several, cfg)
            problems = validate_om_request(subject, body)
        if problems:
            conn.execute("DELETE FROM commercial_kv WHERE key=?", (_key(email),))
            res["errors"] += 1
            log.warning("follow-up to %s not sent (%s)", email, "; ".join(problems))
            continue
        try:
            sent = gmail.send(email, subject, body, thread_id=t.get("gmail_thread_id"))
        except Exception as e:  # noqa: BLE001
            conn.execute("DELETE FROM commercial_kv WHERE key=?", (_key(email),))
            res["errors"] += 1
            log.warning("follow-up to %s failed: %s", email, e)
            continue
        conn.execute("INSERT INTO outreach (agent_id, property_ids, channel, kind, status, subject, body, variant, to_address, gmail_thread_id, "
                     "gmail_message_id, run_date, created_at, sent_at, asana_task_id) VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (json.dumps([f"loopnet:{t['listing_id']}"]), "email", KIND, "sent", subject, body, VARIANT, email,
                      sent.get("thread_id") or t.get("gmail_thread_id"), sent.get("message_id"), run_date, ts, ts, gid))
        conn.execute("UPDATE commercial_threads SET updated_at=? WHERE id=?", (ts, t["id"]))
        res["sent"] += 1
        res["waiting"] -= 1
        if asana is not None and gid:
            try:
                asana.comment(gid, f"📤 Follow-up sent to {email}: still placing the 1031, in or out, send the books if it clears the bar. "
                                   "One per broker; the reply clock keeps running.")
            except Exception as e:  # noqa: BLE001
                log.warning("follow-up: comment on %s: %s", gid, e)
    conn.commit()
    return res
