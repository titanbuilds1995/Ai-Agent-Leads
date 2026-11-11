"""The one-time "sorry for the delay" note (Rodrigo, 2026-11-11).

"Pause everything … make sure that we've answered everybody … no commitments to anybody. Just let them know: sorry that we've been
a little backed up on all the properties we are analyzing, but we're getting through them … only people that we haven't answered
at all, and if I'm in communication with them, don't email them … we will get back with you."

Who gets it: a 1031 broker who really answered us (not a bounce, not an auto-reply) before `commercial.delay_note.before` and has
heard NOTHING back since: no reply from Charles, nothing from Rodrigo. Once per broker, in the broker's own Gmail thread.
Who never gets it: a thread Rodrigo wrote in, a card on the Working Deals board or in Offer out / Closed / Not a fit / Reviewed /
Needs Revision / Good fit (those are his), a broker read NOT A FIT / DOES NOT FIT ("we will get back with you" would be untrue),
a closed or bounced thread. Before each send the Gmail thread itself is opened: if anyone on our side wrote after the broker's
first answer (by hand, from any mailbox), nothing goes.

It is the only thing that still goes to a broker while `outreach.sending_paused` is on. No date, no promise, no number.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .. import db
from ..util import iso, log, now_utc
from .board import NEEDS_REVISION, NOT_A_FIT, REVIEWED
from .books_review import ARCHIVE_VERDICTS, _first_name, _signature

KIND = "om_reply"                       # logged like Charles's other replies: later reads see it, and it counts as his one answer
HIS_COLUMNS = ("Offer out", "Closed", NOT_A_FIT, REVIEWED, NEEDS_REVISION, "Good fit")


def note_body(first: str, place: str, cfg: Any = None) -> str:
    return (f"Hey {first},\n\n"
            f"Thank you for getting back to us on {place}, and sorry for the slow reply. We have been analyzing a lot of properties "
            "and it has been very busy on our side, so we have not been able to get to everybody we reached out to.\n\n"
            "We are working through them and we will get back with you.\n\n"
            f"Best regards,\n\n{_signature(cfg)}")


def _key(email: str) -> str:
    return f"delay_note:{(email or '').strip().lower()}"


def _answered(conn, t: dict[str, Any]) -> bool:
    """Anything from our side after the broker's first answer that the database knows about."""
    if conn.execute("SELECT 1 FROM commercial_reviews WHERE thread_id=? AND reply_sent_at IS NOT NULL", (t["id"],)).fetchone():
        return True
    return bool(conn.execute("SELECT 1 FROM outreach WHERE kind IN ('om_reply','om_rodrigo') AND status='sent' AND "
                             "(gmail_thread_id=? OR (asana_task_id IS NOT NULL AND asana_task_id=?)) AND COALESCE(sent_at, created_at) >= ?",
                             (t.get("gmail_thread_id") or "-", t.get("card_gid") or "-", t.get("first_reply_at") or "")).fetchone())


def candidates(conn, cfg: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Brokers owed the note (one thread per broker, the oldest answer first) and why the others were left out."""
    before = str(cfg.get("commercial.delay_note.before") or "9999")
    skipped = {"rodrigo_is_talking": 0, "already_answered": 0, "not_a_fit": 0, "already_sent": 0, "answered_after_the_pause": 0}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    busy: set[str] = set()                    # brokers Rodrigo or Charles is already talking to in ANY of their threads
    rows = [db.row_to_dict(r) for r in conn.execute(
        "SELECT * FROM commercial_threads WHERE first_reply_at IS NOT NULL AND COALESCE(bounced,0)=0 AND broker_email IS NOT NULL "
        "ORDER BY first_reply_at, id")]
    for t in rows:
        if t.get("rodrigo_at") or _answered(conn, t):
            busy.add((t["broker_email"] or "").lower())
    for t in rows:
        email = (t["broker_email"] or "").strip().lower()
        if not email or email in seen:
            continue
        if conn.execute("SELECT 1 FROM commercial_kv WHERE key=?", (_key(email),)).fetchone():
            skipped["already_sent"] += 1
            seen.add(email)
            continue
        if t.get("rodrigo_at"):
            skipped["rodrigo_is_talking"] += 1
            seen.add(email)
            continue
        if email in busy:
            skipped["already_answered"] += 1
            seen.add(email)
            continue
        if (t.get("state") or "").upper() == "CLOSED" or t.get("cleared_at"):
            skipped["not_a_fit"] += 1
            continue
        r = conn.execute("SELECT verdict FROM commercial_reviews WHERE thread_id=? AND COALESCE(source,'') NOT IN ('note','closeout') "
                         "ORDER BY id DESC LIMIT 1", (t["id"],)).fetchone()
        if r and (r[0] or "").upper() in ARCHIVE_VERDICTS:
            skipped["not_a_fit"] += 1
            continue
        if (t["first_reply_at"] or "") >= before:
            skipped["answered_after_the_pause"] += 1
            continue
        seen.add(email)
        out.append(t)
    return out, skipped


def _place(conn, t: dict[str, Any]) -> str:
    try:
        ids = json.loads(t.get("listing_ids") or "[]")
    except json.JSONDecodeError:
        ids = []
    if len(ids) > 1:
        return "your listings"
    row = conn.execute("SELECT data FROM commercial_listings WHERE listing_id=?", (t["listing_id"],)).fetchone()
    try:
        street = (json.loads(row[0] or "{}").get("street") or "").strip() if row else ""
    except json.JSONDecodeError:
        street = ""
    return street or "your listing"


def _ours_wrote_after(gmail, cfg: Any, t: dict[str, Any]) -> bool | None:
    """Gmail's own word: did anyone on our side write in this thread after the broker's first answer? None = could not look."""
    from .outreach import _is_ours
    if not t.get("gmail_thread_id"):
        return None
    try:
        msgs = gmail.thread_messages(t["gmail_thread_id"])
    except Exception as e:  # noqa: BLE001
        log.warning("delay note: thread %s: %s", t["gmail_thread_id"], e)
        return None
    theirs = False
    for m in sorted(msgs, key=lambda m: m.get("internal_ts") or 0):
        if "DRAFT" in (m.get("label_ids") or []):
            continue
        if _is_ours(m.get("from"), cfg):
            if theirs:
                return True
        else:
            theirs = True
    return False


def send_delay_notes(conn, cfg: Any, asana, gmail, now: datetime | None = None) -> dict[str, Any]:
    from ..approvals import in_window
    from .closeout import columns_by_card
    res: dict[str, Any] = {"sent": 0, "waiting": 0, "errors": 0, "skipped": {}}
    if not cfg.get("commercial.delay_note.enabled", False):
        return res
    now = now or now_utc()
    todo, res["skipped"] = candidates(conn, cfg)
    res["waiting"] = len(todo)
    if not todo:
        return res
    if gmail is None:
        res["reason"] = "Gmail not connected"
        return res
    if not in_window(cfg, now, even_if_paused=True):
        res["reason"] = "outside the send window"
        return res
    where: dict[str, str] = {}
    project = cfg.get("commercial.asana.project")
    if asana is not None and project:
        try:
            from .board import ensure_board
            where = columns_by_card(asana, ensure_board(asana, project))
        except Exception as e:  # noqa: BLE001
            log.warning("delay note: board columns: %s", e)
            res["reason"] = "could not read the board"     # without the board we cannot tell which brokers are his: send nothing
            return res
    room = int(cfg.get("commercial.delay_note.per_round", 20))
    for t in todo:
        if res["sent"] >= room:
            break
        gid, email = t.get("card_gid"), t["broker_email"].strip()
        mine = gid and (where.get(gid) in HIS_COLUMNS or (hasattr(asana, "on_working") and asana.on_working(gid)))
        wrote = None if mine else _ours_wrote_after(gmail, cfg, t)
        if mine or wrote:
            conn.execute("INSERT OR IGNORE INTO commercial_kv (key, value) VALUES (?,?)", (_key(email), "skipped: " + ("his card" if mine else "already answered in Gmail")))
            res["skipped"]["rodrigo_is_talking" if mine else "already_answered"] += 1
            res["waiting"] -= 1
            continue
        if wrote is None:
            res["errors"] += 1                     # could not open the thread: try again next round rather than guess
            continue
        ts = iso(now)
        if not conn.execute("INSERT OR IGNORE INTO commercial_kv (key, value) VALUES (?,?)", (_key(email), ts)).rowcount:
            continue                               # another round took this broker: never two notes
        body = note_body(_first_name(t.get("broker_name"), email), _place(conn, t), cfg)
        subject = t.get("subject") or ""
        subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        try:
            sent = gmail.send(email, subject, body, thread_id=t.get("gmail_thread_id"))
        except Exception as e:  # noqa: BLE001
            conn.execute("DELETE FROM commercial_kv WHERE key=?", (_key(email),))
            res["errors"] += 1
            log.warning("delay note to %s failed: %s", email, e)
            continue
        conn.execute("INSERT INTO outreach (agent_id, property_ids, channel, kind, status, subject, body, variant, to_address, gmail_thread_id, "
                     "gmail_message_id, run_date, created_at, sent_at, asana_task_id) VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (json.dumps([f"loopnet:{t['listing_id']}"]), "email", KIND, "sent", subject, body, "delay_note", email,
                      sent.get("thread_id") or t.get("gmail_thread_id"), sent.get("message_id"), ts[:10], ts, ts, gid))
        conn.execute("UPDATE commercial_threads SET updated_at=? WHERE id=?", (ts, t["id"]))
        res["sent"] += 1
        res["waiting"] -= 1
        if asana is not None and gid:
            try:
                asana.comment(gid, f"📤 Sorry-for-the-delay note sent to {email} (we are busy, working through the properties, we will get "
                                   "back with you; no dates, no promises). All other sending is paused.")
            except Exception as e:  # noqa: BLE001
                log.warning("delay note comment %s: %s", gid, e)
    return res
