"""THE SECOND READER (Rodrigo, 2026-11-12: "I want another agent to look at it and read the thread and test the logic. So two
agents, one answers, one reads the whole thing and reads the email we have and pushes back on the actual logic and if it makes
sense on what we're sending").

Every answer Charles drafts to an agent or a broker is read by a SECOND Claude call that did not write it. It gets the whole
conversation (both sides), what we know about the property, Rodrigo's instruction when there is one, and the draft, and it judges
the LOGIC only: does the email answer what they actually said, does it ask for something they already gave / answered / refused,
does it contradict what we wrote before, does it assume a fact nobody stated, is this the email that moves the deal, should an
email go at all. Wording rules (length, banned phrases, no numbers) stay with the validators; this is the judgment check.

Verdicts: SEND (the logic holds) · FIX (a rewrite cures it: the writer gets the pushback and writes again, then it is read again)
· HOLD (no email should go from Charles now: Rodrigo's move). What is left after `max_rounds` rewrites is shown on the card and the
draft never goes out on its own. Paid API like the other card work (`second_read` purpose), one extra call per drafted answer."""
from __future__ import annotations

import json
from typing import Any, Callable

from .util import log

MARK = "⚖️ Second read"
VERDICTS = ("SEND", "FIX", "HOLD")

SYSTEM = """You are the second reader at Titan Realty Investments, a Florida real estate investment company. Charles Grant (junior
acquisitions agent) drafted an email to a listing agent or a commercial broker. You did NOT write it. Before it goes anywhere you
read the WHOLE conversation, oldest to newest, both sides, and then the draft, and you test the logic of what we are about to send.
Rodrigo Gayoso (principal) asked for this after emails went out that asked a broker for documents he had already sent, asked again
for something he said he cannot give, and asked for books right after he asked for a phone call.

You are skeptical and specific. Read the thread first and write down for yourself what the other side has told us, asked us, given
us and refused, and what we already said and asked. Then check the draft against that:
1. Does it answer what they ACTUALLY said or asked in their last message? An email that ignores their question, or answers a
   different one, fails.
2. Does it ask for anything they already gave, already answered, or said they cannot or will not provide, anywhere in the thread?
   Does it repeat a question or a request we already sent?
3. Does it contradict anything we wrote earlier, or anything they told us?
0. NOTHING is ever attached to our emails and we never sign anything in them: a draft that says a file is attached, enclosed, signed or sent over is FIX, every time.
4. Does it state or assume a fact that is not in the thread or the property facts (a document "attached", something "received",
   something they "mentioned", a condition, a price, a timeline)? Does it say something did not arrive when we cannot know that?
5. Is this the right email NOW? If they asked for a call, sound irritated, asked for a number / an offer / proof of funds / a
   signature, or said no, should Charles be writing at all, or is it Rodrigo's move? Does the question in the draft actually move
   the deal forward, or is it filler when the obvious next step is something else?
6. When Rodrigo gave an instruction, does the draft do what he asked, all of it and nothing he did not ask? His instruction is the
   brief: do NOT second-guess what he chose to say. Only flag where the draft misses or distorts his instruction, or where it
   collides with a fact in the thread he may not have seen (say which message).
7. Would a busy agent reading this think we did not read their email?

Not your job: wording, tone, length, greeting, signature, house style. Do not flag those. Do not invent problems to look useful:
when the logic holds, say SEND with no problems. Every problem you list must point at something concrete: quote or name the line
in the thread or the draft it comes from.

Verdict:
- SEND: the logic holds; send it as written.
- FIX: there is a real logic problem and a rewrite cures it. In "fix" tell the writer exactly what to change (what to drop, what to
  answer, what to ask instead). Never write the email yourself.
- HOLD: no email from Charles should go now, whatever the wording (their message calls for Rodrigo: a call, a number, a decision,
  a complaint), or the draft is a fixed text that cannot be changed and does not fit this thread.

Return JSON: verdict, problems (short lines, [] when SEND), fix (one or two sentences for the writer, "" unless FIX), why (ONE
plain sentence for Rodrigo, who is not a developer: what you checked and what you found)."""

SCHEMA = {"type": "object",
          "properties": {"verdict": {"type": "string", "enum": list(VERDICTS)},
                         "problems": {"type": "array", "items": {"type": "string"}},
                         "fix": {"type": "string"},
                         "why": {"type": "string"}},
          "required": ["verdict", "problems", "fix", "why"], "additionalProperties": False}


def enabled(cfg: Any) -> bool:
    return bool(cfg.get("outreach.second_read.enabled", False))


def check(llm, cfg: Any, case: dict[str, Any], draft: str) -> dict[str, Any] | None:
    """One second read of `draft`. `case`: {"who": ..., "thread": [{"from", "when", "text"}], "facts": ..., "instruction": ...,
    "fixed_text": bool, ...}. None when Claude is unreachable or the answer is unusable (the caller treats that as "not read")."""
    if llm is None or not getattr(llm, "available", False):
        return None
    user = json.dumps({**case, "draft_to_check": draft}, ensure_ascii=False, default=str)
    try:
        out = llm.structured("second_read", SYSTEM, user, SCHEMA, max_tokens=1500)
    except Exception as e:  # noqa: BLE001 — the second read never breaks a round
        log.warning("second read: %s", e)
        return None
    if not isinstance(out, dict) or out.get("verdict") not in VERDICTS:
        return None
    problems = [str(p).strip() for p in (out.get("problems") or []) if str(p).strip()]
    verdict = out["verdict"]
    if verdict == "FIX" and not problems and not (out.get("fix") or "").strip():
        verdict = "SEND"            # "fix" with nothing to fix is a pass
    return {"verdict": verdict, "problems": problems[:6], "fix": (out.get("fix") or "").strip(), "why": (out.get("why") or "").strip()}


def run(llm, cfg: Any, case: dict[str, Any], draft: str,
        rewrite: Callable[[list[str]], str | None] | None = None) -> tuple[str, dict[str, Any] | None]:
    """Writer and second reader, back and forth. `rewrite(pushback)` returns the writer's new draft (None = it could not); with no
    `rewrite` (a fixed text, Rodrigo's own words) the draft is read once. Returns (final draft, review); review is None when the
    second read is switched off, else {"verdict": SEND / FIX / HOLD / None (not read), "problems", "why", "rounds", "first"}:
    `first` holds what the reader objected to before a rewrite cured it."""
    if not enabled(cfg) or not (draft or "").strip():
        return draft, None
    rounds = max(0, int(cfg.get("outreach.second_read.max_rounds", 2))) if rewrite else 0
    review: dict[str, Any] = {"verdict": None, "problems": [], "why": "", "rounds": 0, "first": []}
    for n in range(rounds + 1):
        r = check(llm, cfg, case, draft)
        if r is None:
            review.update(verdict=None, problems=[], why="")
            return draft, review
        review.update(verdict=r["verdict"], problems=r["problems"], why=r["why"])
        if r["verdict"] != "FIX" or n == rounds:
            return draft, review
        if not review["first"]:
            review["first"] = r["problems"] or [r["fix"]]
        pushback = r["problems"] + ([f"What to change: {r['fix']}"] if r["fix"] else [])
        try:
            new = rewrite(pushback)
        except Exception as e:  # noqa: BLE001
            log.warning("rewrite after second read: %s", e)
            new = None
        if not (new or "").strip():
            return draft, review       # the writer could not do better: the objection stands on this draft
        draft = new
        review["rounds"] = n + 1
    return draft, review


def passed(review: dict[str, Any] | None) -> bool:
    """True when nothing stands in the way: the second read is off, or it said SEND."""
    return review is None or review.get("verdict") == "SEND"


def line(review: dict[str, Any] | None) -> str:
    """The one line Rodrigo sees on the card."""
    if review is None:
        return ""
    v = review.get("verdict")
    if v is None:
        return f"{MARK}: not done (Claude was unreachable), so this waits for you."
    if v == "SEND":
        if review.get("rounds"):
            was = "; ".join(review.get("first") or [])[:300]
            return f"{MARK}: agrees after {review['rounds']} rewrite{'s' if review['rounds'] > 1 else ''}" + (f" (it pushed back on: {was})." if was else ".")
        return f"{MARK}: agrees. " + (review.get("why") or "")[:300]
    what = "; ".join(review.get("problems") or [])[:500] or (review.get("why") or "")[:300]
    if v == "HOLD":
        return f"{MARK} says HOLD, this is your move: {what}"
    return f"{MARK} still objects" + (f" after {review['rounds']} rewrite{'s' if review['rounds'] > 1 else ''}" if review.get("rounds") else "") + f": {what}"
