"""The 1031 board is the CRM (Rodrigo, 2026-11-08: "Asana is a CRM; the dashboard is where the investor looks"). Columns are
his categories; the engine only ever CREATES cards (in New) and READS where he dragged them, so the site and the email follow
the board. Nothing on this board sends."""
from __future__ import annotations

import re
from typing import Any

from ..util import log

SEND = "Approved · send OM request"      # drag a card here = Charles emails the broker for the books (2026-11-09)
NEEDS_ME = "Needs Me"                     # the next move is Rodrigo's (2026-11-10: "why are the emails on the people that need me not on my board?")
NEEDS_REVISION = "Needs Revision"         # drag a card here + comment = talk it over with Claude (2026-11-10, like the email board)
REVIEWED = "Reviewed"                     # he answered on the card: Claude acts on it, then the card comes back to Needs Me (2026-11-10)
NOT_A_FIT = "Not a fit"                   # always the LAST column; cards there are cleared off the board after the pass email (closeout.py)
COLUMNS = ["New", SEND, NEEDS_ME, NEEDS_REVISION, REVIEWED, "Good fit", "In touch with the agent", "Books received", "Offer out", "Closed", NOT_A_FIT]
STAYS_PUT = ("Offer out", "Closed", "Not a fit", NEEDS_ME, NEEDS_REVISION)   # the engine never pulls a card out of these to park it
RENAMES = {"top tier": "Good fit", "needs om": "New", "suspect": "Not a fit", "contacted": "In touch with the agent", "skip": "Closed",
           "approved": SEND, "send om request": SEND, "send": SEND,
           "waiting on the broker": "In touch with the agent"}   # the Working Deals board's name for the same column (working.py)
INVESTOR_COLUMNS = ["Good fit", "In touch with the agent", "Books received", "Offer out"]     # what the shareable view shows
LISTING_URL_RE = re.compile(r"loopnet\.com/Listing/(?:[^/\s]+/)?(\d+)/?", re.I)


def column_key(name: str | None) -> str | None:
    n = (name or "").strip().lower()
    for c in COLUMNS:
        if c.lower() == n:
            return c
    return RENAMES.get(n)


def ensure_board(asana, project: str) -> dict[str, str]:
    """Rename the first-cut columns to Rodrigo's categories and add the missing ones. {column: section gid}."""
    have: dict[str, str] = {}
    for sec in asana.sections(project):
        name = sec.get("name") or ""
        key = column_key(name)
        if key and key != name.strip():
            asana.rename_section(sec["gid"], key)
            log.info("1031 board: renamed column %r → %r", name, key)
        if key:
            have[key] = sec["gid"]
    for col in COLUMNS:
        if col not in have:
            before = have.get("Good fit") if col in (NEEDS_ME, NEEDS_REVISION, REVIEWED) else None    # up front, next to the send column
            have[col] = asana.ensure_section(project, col, insert_before=before) if before else asana.ensure_section(project, col)
    last_at_end(asana, project, have.get(NOT_A_FIT))
    return have


def last_at_end(asana, project: str, section: str | None) -> None:
    """Rodrigo, 2026-11-10: "everybody that we're 100% they're not a fit with, let's just put them at the end of my board"."""
    if not section or not hasattr(asana, "order_section_after"):
        return
    try:
        order = [s["gid"] for s in asana.sections(project)]
        if order and order[-1] != section:
            asana.order_section_after(project, section, order[-1])
    except Exception as e:  # noqa: BLE001
        log.warning("could not put %s at the end of the board: %s", NOT_A_FIT, e)


def read_board(asana, project: str) -> dict[str, dict[str, Any]]:
    """{listing_id: {column, gid, name}} for every open card that links a LoopNet listing."""
    out: dict[str, dict[str, Any]] = {}
    for t in asana.project_tasks(project):
        if t.get("completed"):
            continue
        notes = t.get("notes") or ""
        if not notes:
            try:
                notes = (asana.task(t["gid"]) or {}).get("notes") or ""
            except Exception:  # noqa: BLE001
                continue
        m = LISTING_URL_RE.search(notes)
        if not m:
            continue
        col = None
        for mem in t.get("memberships") or []:
            if (mem.get("project") or {}).get("gid") == project or len(t.get("memberships") or []) == 1:
                col = column_key((mem.get("section") or {}).get("name"))
        out[m.group(1)] = {"column": col or "New", "gid": t["gid"], "name": t.get("name")}
    return out


def apply_board(conn, board: dict[str, dict[str, Any]]) -> int:
    """Write each card's column onto its listing row. Returns how many rows changed."""
    n = 0
    for lid, info in board.items():
        cur = conn.execute("SELECT board_column FROM commercial_listings WHERE listing_id=?", (lid,)).fetchone()
        if cur is not None and cur[0] != info["column"]:
            conn.execute("UPDATE commercial_listings SET board_column=?, asana_task_id=COALESCE(asana_task_id, ?) WHERE listing_id=?",
                         (info["column"], info["gid"], lid))
            n += 1
        elif cur is not None and not cur[0]:
            conn.execute("UPDATE commercial_listings SET board_column=? WHERE listing_id=?", (info["column"], lid))
            n += 1
    return n
