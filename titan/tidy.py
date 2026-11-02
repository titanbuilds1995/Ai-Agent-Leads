"""Comment tidy (Rodrigo, 2026-11-02: "delete things that are old and not needed … they add a lot of noise").

The engine talks to Rodrigo through comments, and over a card's life the same thing gets said again and again: four
"Rewritten …" notes, "Holding it" + "Held" pairs, "Moved to Needs Me" twice, yesterday's "Comps re-checked" under today's.
Every round, for each card whose comments were read anyway, the superseded ones are removed:

- a TRANSIENT comment (holding, held, moved, couldn't rewrite, pointers) is removed once ANY later engine comment exists;
- a REPEAT family (Rewritten …, Comps re-checked, Revised …) keeps only its newest member;
- Rodrigo's own comments, replies from agents (📩), emails sent (📤), numbers, 💬 answers and the engine's LAST comment are
  never touched (pending_feedback finds his new comments after the last engine comment, so that one must stay).
Only the author can delete an Asana story; every engine comment is written with Rodrigo's token, so that holds."""
from __future__ import annotations

from typing import Any

from .asana import BOT

TRANSIENT = ("Holding it", "Held:", "Moved to Needs Me", "No comment on this card", "I couldn't rewrite", "Couldn't rewrite",
             "Numbers are above", "Your comment went to the answer", "Your answer to", "Can't send the test copy", "Test copy to")
REPEAT_FAMILIES = ("Rewritten", "Comps re-checked", "Revised", "Charles's answer went out", "Correction")
KEEP = ("📩", "📤", "NUMBERS", "💬", "Sent ", "DEAL SNAPSHOT", "DRAFTED ANSWER", "Answer:")
MAX_PER_CARD = 4            # deletes per card per round: bounded Asana traffic; the rest goes next round


def _body(c: dict[str, Any]) -> str:
    t = (c.get("text") or "").strip()
    return t[len(BOT):].strip() if t.startswith(BOT) else ""


def _family(body: str) -> str | None:
    for f in REPEAT_FAMILIES:
        if body.startswith(f):
            return f
    return None


def superseded(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The engine comments that no longer tell Rodrigo anything (oldest first)."""
    bots = [(i, c) for i, c in enumerate(comments) if (c.get("text") or "").strip().startswith(BOT) and c.get("gid")]
    if len(bots) < 2:
        return []
    last_i = bots[-1][0]
    newest_of: dict[str, int] = {}
    for i, c in bots:
        fam = _family(_body(c))
        if fam:
            newest_of[fam] = i
    out = []
    for i, c in bots:
        if i == last_i:
            continue
        body = _body(c)
        if body.startswith(KEEP):
            continue
        if body.startswith(TRANSIENT):
            out.append(c)
            continue
        fam = _family(body)
        if fam and newest_of.get(fam) != i:
            out.append(c)
    return out


def tidy_comments(asana, task: str, comments: list[dict[str, Any]]) -> int:
    """Delete the superseded engine comments on one card (at most MAX_PER_CARD). Never raises."""
    n = 0
    for c in superseded(comments)[:MAX_PER_CARD]:
        try:
            asana.delete_story(c["gid"])
            n += 1
        except Exception:  # noqa: BLE001 — a comment that will not go stays; nothing else depends on it
            break
    if n:
        gone = {c["gid"] for c in superseded(comments)[:n]}
        comments[:] = [c for c in comments if c.get("gid") not in gone]
    return n


def remove_family(asana, task: str, prefix: str) -> int:
    """Delete every engine comment on the card that starts with `prefix` (before a fresh one of the same kind is posted)."""
    n = 0
    try:
        for c in asana.comments(task):
            if _body(c).startswith(prefix) and c.get("gid"):
                asana.delete_story(c["gid"])
                n += 1
    except Exception:  # noqa: BLE001
        pass
    return n
