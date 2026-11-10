"""Not a fit = pass email, then off the board (Rodrigo, 2026-11-10: "everybody that we're 100% they're not a fit with, let's just put
them at the end of my board and delete them. If in the conversation we're 100% sure … send them that last email that we mentioned and
get them out. I don't want to have to see them here on my board").

Every round, for each 1031 card (either board):
- it sits in **Not a fit** (he dragged it there, or a broker said leased / sold), or
- the latest read says NOT A FIT / DOES NOT FIT, nothing else was pitched, it does not need him, and it sits in a column the engine
  owns (New / In touch / Books received), never one he chose (Needs Me / Needs Revision / Reviewed / Good fit / Offer out / Closed),
and the thread is not his own (he wrote in it) →
1. the polite pass goes out in the broker's thread (Rodrigo's own words, `template_reply("pass")`) unless Charles already declined in
   that thread, the broker never answered, or a drafted reply is still waiting to go; inside the send window, `per_round` at most;
2. the thread is archived in Charles's inbox;
3. the card moves to Not a fit (the last column) with one 🗑 comment and is marked complete, so it leaves the board (history kept:
   the card can be found in search and reopened).
A cleared broker who writes again with something that is not a pass is reopened in Needs Me."""
from __future__ import annotations

import json
import re
from typing import Any

from .. import db
from ..util import iso, log, now_utc
from .board import IN_TOUCH, NEEDS_ME, NEEDS_REVISION, NO_ANSWER, NOT_A_FIT, REVIEWED, SEND, ensure_board
from .books_review import ARCHIVE_VERDICTS, DEAD_RE, _first_name, _listing, _messages, template_reply

DECLINE_RE = re.compile(r"won'?t work for us|will not work for us|(?:we(?:'ll| will)?|going to) pass\b|pass (?:on|for now)|"
                        r"(?:doesn'?t|does not|isn'?t|is not) (?:a )?fit|not a fit|below what|not for us|not what (?:we|a partner)", re.I)
HIS_COLUMNS = (NEEDS_ME, NEEDS_REVISION, REVIEWED, SEND, "Good fit", "Offer out", "Closed")
CLEARABLE = ("New", NO_ANSWER, IN_TOUCH, "Books received", NOT_A_FIT)


def columns_by_card(asana, secs: dict[str, str]) -> dict[str, str]:
    """{card gid: column} for every open card on the board (and on Working Deals when the client is wrapped)."""
    out: dict[str, str] = {}
    for col, sec in secs.items():
        try:
            for t in asana.section_tasks(sec):
                if not t.get("completed"):
                    out.setdefault(t["gid"], col)
        except Exception as e:  # noqa: BLE001
            log.warning("closeout: column %s: %s", col, e)
    return out


def declined(conn, t: dict[str, Any]) -> bool:
    """Charles already told this broker no in this thread (the fixed pass, or a reply that says it does not fit)."""
    if conn.execute("SELECT 1 FROM commercial_reviews WHERE thread_id=? AND reply_kind='pass' AND reply_sent_at IS NOT NULL", (t["id"],)).fetchone():
        return True
    r = _latest(conn, t["id"])
    if r and (r.get("verdict") or "").upper() in ARCHIVE_VERDICTS and r.get("reply_sent_at"):
        return True                              # Charles already answered the not-fit read itself
    for (body,) in conn.execute("SELECT reply_draft FROM commercial_reviews WHERE thread_id=? AND reply_sent_at IS NOT NULL", (t["id"],)):
        if DECLINE_RE.search(body or ""):
            return True
    if t.get("gmail_thread_id"):
        for (body,) in conn.execute("SELECT body FROM outreach WHERE kind='om_reply' AND status='sent' AND gmail_thread_id=?", (t["gmail_thread_id"],)):
            if DECLINE_RE.search(body or ""):
                return True
    return False


def _latest(conn, thread_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM commercial_reviews WHERE thread_id=? AND COALESCE(source,'') NOT IN ('note','closeout') ORDER BY id DESC LIMIT 1",
                       (thread_id,)).fetchone()
    return db.row_to_dict(row) if row else None


def clear_not_fit(conn, cfg: Any, asana, gmail, now=None) -> dict[str, Any]:
    res: dict[str, Any] = {"cleared": 0, "passes": 0, "waiting": 0, "reopened": 0, "errors": 0}
    project = cfg.get("commercial.asana.project")
    if asana is None or not project or not cfg.get("commercial.review.clear_not_fit", True):
        return res
    now = now or now_utc()
    secs = ensure_board(asana, project)
    where = columns_by_card(asana, secs)
    per_round = int(cfg.get("commercial.review.pass_per_round", 10))
    from ..approvals import in_window
    in_win = in_window(cfg, now)
    threads = [db.row_to_dict(r) for r in conn.execute("SELECT * FROM commercial_threads WHERE card_gid IS NOT NULL").fetchall()]
    by_card: dict[str, list[dict[str, Any]]] = {}
    for t in threads:
        by_card.setdefault(t["card_gid"], []).append(t)
    _reopen(conn, asana, secs, threads, res, now)

    for gid, col in where.items():
        if col not in CLEARABLE:
            continue
        ts = by_card.get(gid) or []
        if any(t.get("cleared_at") for t in ts):
            continue
        his_drag = col == NOT_A_FIT
        if not ts and not his_drag:
            continue                             # no conversation and he did not call it: nothing to decide here
        reasons: list[str] = []
        ok = True
        for t in ts:
            r = _latest(conn, t["id"])
            other = json.loads((r or {}).get("other_deals") or "[]") if r else []
            sure = his_drag or (r is not None and (r.get("verdict") or "").upper() in ARCHIVE_VERDICTS and not other and not r.get("needs_rodrigo"))
            if not sure or (t.get("rodrigo_at") and not his_drag):
                ok = False
                break
            if r and r.get("reply_draft") and not r.get("reply_sent_at"):
                if his_drag and r.get("reply_kind") != "pass":
                    # he decided it is not a fit: the drafted answer (a question, an ask for the books) is dropped, the pass goes instead
                    conn.execute("UPDATE commercial_reviews SET reply_draft=NULL WHERE id=? AND reply_sent_at IS NULL", (r["id"],))
                else:
                    ok = False                   # Charles's drafted pass goes first (send_approved_replies): never two emails
                    res["waiting"] += 1
                    break
        if not ok:
            continue
        # 1) the pass, where one is still owed
        for t in ts:
            msgs = _messages(conn, t["id"])
            if t.get("rodrigo_at") or t.get("bounced") or not msgs or declined(conn, t):
                continue
            if not in_win or res["passes"] >= per_round or gmail is None:
                ok = False
                res["waiting"] += 1
                break
            L = _listing(conn, t["listing_id"])
            last = (msgs[-1].get("body") or "") if msgs else ""
            body = template_reply("pass", _first_name(t.get("broker_name"), t.get("broker_email")), cfg, sold=bool(DEAD_RE.search(last)),
                                  street=L.get("street"), seed=int(t.get("id") or 0))
            subject = t.get("subject") or ""
            subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
            stamp = iso(now)
            try:
                sent = gmail.send(t["broker_email"], subject, body, thread_id=t.get("gmail_thread_id"))
            except Exception as e:  # noqa: BLE001
                ok = False
                res["errors"] += 1
                log.warning("closeout pass %s: %s", t.get("listing_id"), e)
                break
            conn.execute("INSERT INTO outreach (agent_id, property_ids, channel, kind, status, subject, body, to_address, gmail_thread_id, gmail_message_id, "
                         "run_date, created_at, sent_at, asana_task_id) VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (json.dumps([f"loopnet:{t['listing_id']}"]), "email", "om_reply", "sent", subject, body, t["broker_email"],
                          sent.get("thread_id") or t.get("gmail_thread_id"), sent.get("message_id"), stamp[:10], stamp, stamp, gid))
            conn.execute("INSERT INTO commercial_reviews (thread_id, listing_id, gmail_message_id, verdict, needs_rodrigo, text, source, reply_draft, "
                         "reply_kind, created_at, reply_sent_at, forwarded_at, archived_at, parked_at) VALUES (?,?,?,?,0,?,?,?,?,?,?,?,?,?)",
                         (t["id"], t["listing_id"], t.get("last_msg_id"), "NOT A FIT", "closeout pass", "closeout", body, "pass", stamp, stamp, stamp, stamp, stamp))
            res["passes"] += 1
            reasons.append(f"pass sent to {t['broker_email']}")
        if not ok:
            continue
        # 2) archive, 3) off the board
        for t in ts:
            if gmail is not None and t.get("gmail_thread_id"):
                try:
                    gmail.archive_thread(t["gmail_thread_id"])
                except Exception as e:  # noqa: BLE001
                    log.warning("closeout archive %s: %s", t.get("listing_id"), e)
        try:
            if col != NOT_A_FIT and secs.get(NOT_A_FIT):
                asana.move(gid, secs[NOT_A_FIT])
            why = "; ".join(reasons) or ("Charles already told them no" if ts and any(declined(conn, t) for t in ts) else "no email owed")
            asana.comment(gid, f"🗑 Not a fit · cleared off the board ({why}). The card is marked done, not deleted: search finds it, and a "
                               "new answer from the broker that is not a pass brings it back to Needs Me.")
            asana.complete(gid)
        except Exception as e:  # noqa: BLE001
            res["errors"] += 1
            log.warning("closeout card %s: %s", gid, e)
            continue
        stamp = iso(now)
        for t in ts:
            conn.execute("UPDATE commercial_threads SET cleared_at=?, updated_at=? WHERE id=?", (stamp, stamp, t["id"]))   # state kept: a new answer is still read
            conn.execute("UPDATE commercial_listings SET board_column=? WHERE listing_id=?", (NOT_A_FIT, t["listing_id"]))
        conn.execute("UPDATE commercial_listings SET board_column=? WHERE asana_task_id=? OR asana_om_task_id=?", (NOT_A_FIT, gid, gid))
        res["cleared"] += 1
    return res


def _reopen(conn, asana, secs: dict[str, str], threads: list[dict[str, Any]], res: dict[str, Any], now) -> None:
    """A cleared broker wrote again and the read is not a pass (or they pitched something): the card comes back, in Needs Me."""
    for t in threads:
        if not t.get("cleared_at"):
            continue
        r = _latest(conn, t["id"])
        if not r or (r.get("created_at") or "") <= t["cleared_at"]:
            continue
        other = json.loads(r.get("other_deals") or "[]")
        if (r.get("verdict") or "").upper() in ARCHIVE_VERDICTS and not other and not r.get("needs_rodrigo"):
            continue
        try:
            if hasattr(asana, "uncomplete"):
                asana.uncomplete(t["card_gid"])
            if secs.get(NEEDS_ME):
                asana.move(t["card_gid"], secs[NEEDS_ME])
            asana.comment(t["card_gid"], "📩 Back from Not a fit: the broker wrote again (see the read above).")
        except Exception as e:  # noqa: BLE001
            res["errors"] += 1
            log.warning("closeout reopen %s: %s", t.get("card_gid"), e)
            continue
        conn.execute("UPDATE commercial_threads SET cleared_at=NULL, updated_at=? WHERE card_gid=?", (iso(now), t["card_gid"]))
        res["reopened"] += 1
