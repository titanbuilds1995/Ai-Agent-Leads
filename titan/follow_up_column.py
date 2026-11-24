"""THE FOLLOW UP COLUMN (Rodrigo, 2026-11-24: "we need to have a column on our board that handles the follow up leads, I will write
comments on who to follow up with and then just leave the task there").

A "Follow Up" column on the Email Approvals board (created the first time, right before "Sent · no answer yet"). Rodrigo drags a
lead card there and comments what to follow up about ("follow up, ask if the seller is still open", "follow up Monday about the
roof", or the email itself). Then he leaves it:

- The engine reads his comment, Charles writes the follow-up in the agent's Gmail thread from the whole conversation and his words
  (`replies.writer.write_answer`, his comment is `rodrigo_instruction`; a dictated email is tidied, his words kept), and the SECOND
  READER reads it against the thread (two agents on every answer). Cleared → the card rides through Approved, is sent at the next
  send round (send window, daily cap, contact guard, nothing-attached and bulk checks all apply) and comes BACK to Follow Up with a
  📤 comment. Not cleared / "hold" / reader unreachable → Needs Me with the reason, nothing sends.
- A date in his comment waits for that day ("tomorrow", "Monday", "next week", "in 3 days", "in 2 weeks", "Dec 3", "12/3"): one ⏰
  comment, the card stays in Follow Up, the email is written and sent that morning.
- The agent answers while the card sits there → Needs Me (the lead board takes it from there).
- The automatic follow-up sequence leaves cards in this column alone: Rodrigo runs those himself.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from . import db
from .util import first_name, iso, log, now_utc

FOLLOW_UP = "Follow Up"
TZ = ZoneInfo("America/New_York")
WAIT_PREFIX = "⏰ Follow-up set for"
BACK_PREFIX = "Back in Follow Up"
FAIL_PREFIX = "I couldn't rewrite this one automatically"     # same prefix as approvals.FAILED_PREFIX: his comment stays pending

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
MONTH_RE = re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
                      r"jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\.? (\d{1,2})(?:st|nd|rd|th)?\b")
NOT_TIMING = r"(?<!close )(?<!closing )(?<!close it )(?<!inspection )(?<!escrow )(?<!closed )"
NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "ten": 10, "couple": 2, "few": 3}
HOLD_RE = re.compile(r"\b(?:don'?t send|do not send|hold (?:it|off|this)|not yet|let me (?:see|look|read)|wait for me)\b", re.I)


def _table(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS follow_up_column (gid TEXT PRIMARY KEY, outreach_id INTEGER, seen_at TEXT NOT NULL, "
                 "instruction TEXT, due_at TEXT, state TEXT, sent_count INTEGER DEFAULT 0, updated_at TEXT)")


def section(cfg: Any, asana) -> str | None:
    """The Follow Up column's gid (config `outreach.asana.follow_up`, or created on the board before the Sent column)."""
    a = cfg.get("outreach.asana", {}) or {}
    if a.get("follow_up"):
        return a["follow_up"]
    cached = getattr(asana, "_follow_up_gid", None)
    if cached:
        return cached
    if not a.get("project"):
        return None
    gid = asana.ensure_section(a["project"], FOLLOW_UP, insert_before=a.get("sent"))
    try:
        asana._follow_up_gid = gid
    except Exception:  # noqa: BLE001
        pass
    return gid


def card_gids(cfg: Any, asana) -> set[str]:
    """Cards in the Follow Up column right now (the automatic sequence leaves them alone). Never raises."""
    try:
        sec = section(cfg, asana)
        return {t["gid"] for t in asana.section_tasks(sec)} if sec else set()
    except Exception as e:  # noqa: BLE001
        log.warning("follow up column not read: %s", e)
        return set()


def due_from(text: str, now: datetime | None = None) -> datetime | None:
    """When his comment says to follow up later, 8am ET that day (as UTC); None = now."""
    t = (text or "").lower()
    local = (now or now_utc()).astimezone(TZ)
    day = None
    m = re.search(NOT_TIMING + r"\bin (\d+|a|an|one|two|three|four|five|six|seven|ten|a couple(?: of)?|a few) (day|week|month)s?\b", t)
    if m:
        k = m.group(1).replace("a couple of", "couple").replace("a couple", "couple").replace("a few", "few")
        k = int(k) if k.isdigit() else NUM.get(k, 1)
        day = local.date() + timedelta(days=k * {"day": 1, "week": 7, "month": 30}[m.group(2)])
    elif re.search(r"\btomorrow\b", t):
        day = local.date() + timedelta(days=1)
    elif re.search(r"\bnext week\b", t):
        day = local.date() + timedelta(days=7 - local.weekday())          # next Monday
    elif re.search(r"\bnext month\b", t):
        day = local.date() + timedelta(days=30)
    else:
        m = re.search(r"\b(" + "|".join(DAYS) + r")\b", t)
        mm = MONTH_RE.search(t)
        sl = re.search(r"\b(?:on|by|until|after) (\d{1,2})/(\d{1,2})\b", t)      # "on 12/3"; a bare "3/2" is beds and baths
        if mm or sl:
            mon, dd = (MONTHS.index(mm.group(1)[:3]) + 1, int(mm.group(2))) if mm else (int(sl.group(1)), int(sl.group(2)))
            try:
                day = local.date().replace(month=mon, day=dd)
                if day < local.date():
                    day = day.replace(year=day.year + 1)
            except ValueError:
                day = None
        elif m:
            want = DAYS.index(m.group(1))
            ahead = (want - local.weekday()) % 7 or 7
            day = local.date() + timedelta(days=ahead)
    if day is None or day <= local.date():
        return None
    return datetime(day.year, day.month, day.day, 8, 0, tzinfo=TZ).astimezone(ZoneInfo("UTC"))


def _answered_since(conn, agent_id: int, since: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM messages m JOIN threads t ON t.id=m.thread_id WHERE t.agent_id=? AND m.direction='in' "
        "AND COALESCE(m.classification,'') NOT IN ('BOUNCE','OUT_OF_OFFICE') AND m.created_at>=? LIMIT 1", (agent_id, since)).fetchone())


def _instruction(text: str, agent: dict[str, Any]) -> str:
    return (f"Rodrigo put this lead in his Follow Up column and wrote: \"{text}\". Write Charles's follow-up email to "
            f"{first_name(agent.get('name')) or 'the agent'} in the same thread, doing what he says. The agent has not written since our "
            "last email: do not thank them for an answer they did not send. If he only said to follow up, write a short check-in that "
            "names the house and asks ONE easy question nobody has asked or answered in the thread yet.")


def write(conn, cfg: Any, llm, row: dict[str, Any], agent: dict[str, Any], props: list[dict[str, Any]], text: str
          ) -> tuple[str | None, dict[str, Any] | None, str]:
    """(body, review, source). His dictated email is tidied and read; otherwise Claude writes it and the second reader reads it."""
    from . import second_read
    from .answers import dictation, tidy_dictation
    from .replies.writer import reader_case, write_checked
    d = dictation([text])
    if d:
        body = tidy_dictation(llm, cfg, d[0], agent)
        _, review = second_read.run(llm, cfg, reader_case(conn, row, agent, props, instruction=_instruction(text, agent)), body)
        return body, review, "his words"
    body, problems, review = write_checked(conn, cfg, llm, row, agent, props, instruction=_instruction(text, agent))
    return body, review, ("Charles (Claude)" if body else "; ".join(problems) or "Claude not available")


def work(conn, cfg: Any, asana, llm, now: datetime | None = None) -> dict[str, int]:
    """One pass over the Follow Up column. Sends nothing itself: a cleared follow-up goes to Approved (send_approved sends it) and
    comes back here once it went."""
    from .answers import pending, set_answer
    from .approvals import _cfg, needs_me_section, parse_card, pending_feedback
    from .house_card import load
    from . import second_read
    n = {"scheduled": 0, "to_approved": 0, "to_needs_me": 0, "back": 0, "answered": 0, "failed": 0}
    sec = section(cfg, asana)
    if not sec:
        return n
    _table(conn)
    a = _cfg(cfg)
    nm = needs_me_section(cfg, asana)
    now = now or now_utc()
    stamp = iso(now)

    # 1. a follow-up that went out: the card comes back to Follow Up (send_approved filed it in Sent)
    for r in conn.execute("SELECT * FROM follow_up_column WHERE state='approved'").fetchall():
        r = db.row_to_dict(r)
        if pending(conn, r["outreach_id"]):
            continue                                            # still waiting in Approved (window / cap / pace)
        went = conn.execute("SELECT sent_at FROM outreach WHERE asana_task_id=? AND kind LIKE 'reply%' AND status='sent' AND sent_at>=? "
                            "ORDER BY sent_at DESC LIMIT 1", (r["gid"], r["updated_at"] or r["seen_at"])).fetchone()
        if went:
            if r["gid"] in {t["gid"] for t in asana.section_tasks(a["sent"])}:
                asana.move(r["gid"], sec)
                asana.comment(r["gid"], f"{BACK_PREFIX}: your follow-up went out. The card stays here; comment again when you want the "
                                        "next one. If the agent answers it moves to Needs Me.")
                n["back"] += 1
            conn.execute("UPDATE follow_up_column SET state='sent', sent_count=sent_count+1, seen_at=?, updated_at=? WHERE gid=?",
                         (went[0], stamp, r["gid"]))
        else:
            conn.execute("UPDATE follow_up_column SET state='held', updated_at=? WHERE gid=?", (stamp, r["gid"]))   # pulled / held at send
    conn.commit()

    # 2. the cards in the column
    for t in asana.section_tasks(sec):
        gid = t["gid"]
        card = parse_card(t.get("notes") or "")
        if not card:
            continue
        row, agent, props = load(conn, card["outreach_id"])
        if not row:
            continue
        rec = db.row_to_dict(conn.execute("SELECT * FROM follow_up_column WHERE gid=?", (gid,)).fetchone())
        if not rec:
            conn.execute("INSERT INTO follow_up_column(gid, outreach_id, seen_at, state, updated_at) VALUES(?,?,?,?,?)",
                         (gid, row["id"], stamp, "watching", stamp))
            rec = {"gid": gid, "outreach_id": row["id"], "seen_at": stamp, "state": "watching", "instruction": None, "due_at": None}
        if row.get("status") != "sent":
            asana.move(gid, nm)
            asana.comment(gid, "Moved to Needs Me: the first email to this agent has not gone out yet, so there is nothing to follow up "
                               "on. Drag it to Approved to send the first email; after that the Follow Up column works for it.")
            n["to_needs_me"] += 1
            continue
        if _answered_since(conn, row["agent_id"], rec["seen_at"]):
            asana.move(gid, nm)
            asana.comment(gid, f"{first_name(agent.get('name')) or 'The agent'} answered, so this lead is out of Follow Up and in Needs Me.")
            conn.execute("UPDATE follow_up_column SET state='answered', updated_at=? WHERE gid=?", (stamp, gid))
            n["answered"] += 1
            continue
        feedback, comments = pending_feedback(asana, gid)
        text = "\n".join(f for f in feedback if f.strip()).strip()
        if text:
            from .answers import dictation
            due = None if dictation([text]) else due_from(text, now)      # a date inside a dictated email is the email's, not his
            conn.execute("UPDATE follow_up_column SET instruction=?, due_at=?, state='scheduled', updated_at=? WHERE gid=?",
                         (text, iso(due) if due else None, stamp, gid))
            rec.update(instruction=text, due_at=iso(due) if due else None, state="scheduled")
            if due:
                asana.comment(gid, f"{WAIT_PREFIX} {due.astimezone(TZ).strftime('%a %b %d')} (from your comment). Charles writes it that "
                                   "morning, the second reader checks it, and it goes out in the send window. The card stays here.")
                n["scheduled"] += 1
                conn.commit()
                continue
        if rec.get("state") != "scheduled" or not rec.get("instruction"):
            continue
        if rec.get("due_at") and rec["due_at"] > stamp:
            continue
        if db.is_dnc(conn, agent_id=row["agent_id"], email=agent.get("email")) or (agent.get("email_confidence") or "") == "bounced":
            asana.move(gid, nm)
            asana.comment(gid, "Not written: this agent's email bounced or they asked not to be contacted. Call instead. Moved to Needs Me.")
            conn.execute("UPDATE follow_up_column SET state='held', updated_at=? WHERE gid=?", (stamp, gid))
            n["to_needs_me"] += 1
            continue
        if pending(conn, row["id"]):
            asana.move(gid, nm)
            asana.comment(gid, "A NEXT EMAIL is already waiting on this card, so I did not write another one. It is in Needs Me: drag it "
                               "to Approved to send that one, or comment what to change.")
            conn.execute("UPDATE follow_up_column SET state='held', updated_at=? WHERE gid=?", (stamp, gid))
            n["to_needs_me"] += 1
            continue
        body, review, source = write(conn, cfg, llm, row, agent, props, rec["instruction"])
        if not body:
            last = (comments[-1].get("text") or "") if comments else ""
            if FAIL_PREFIX not in last:
                asana.comment(gid, f"{FAIL_PREFIX} (follow-up not written: {source}). I'll try again next round; the card stays here.")
            n["failed"] += 1
            continue
        hold = bool(HOLD_RE.search(rec["instruction"]))
        go = second_read.passed(review) and not hold
        note = (f"FOLLOW UP from your comment (written by {source}). " + second_read.line(review)
                + (" You said to hold it: drag to Approved when it's good." if hold else
                   "" if go else " Not sent: drag to Approved if you want it to go anyway, or comment what to change."))
        set_answer(conn, cfg, asana, row["id"], body, note=note.strip(), approved=go, quiet=True)
        asana.comment(gid, (f"Follow-up written{' and cleared' if review else ''}: it goes out at the next send round, then the card comes back to Follow Up. "
                            "Drag it out of Approved to stop it. " if go else "Follow-up written, NOT sent: it is on this card in Needs Me. ")
                      + note)
        conn.execute("UPDATE follow_up_column SET state=?, updated_at=? WHERE gid=?", ("approved" if go else "held", stamp, gid))
        n["to_approved" if go else "to_needs_me"] += 1
        conn.commit()
    conn.commit()
    return n
