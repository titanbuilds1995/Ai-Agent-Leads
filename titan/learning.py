"""THE TRUST LADDER (Rodrigo, 2026-10-26): "keep editing these emails until I'm approving most of them, then you send the
first emails by yourself, then you learn how to answer people, then you send ranges. Step by step we add to the knowledge base."

Three pieces:
1. Every decision Rodrigo makes on a card is recorded (`card_decisions`): approved as written, approved after his comments or
   his own edits, skipped/deleted. Every comment he leaves is a lesson (`lessons`).
2. The knowledge base (`reports/knowledge/learned.md`) is rebuilt from those every tick: his comments, newest first, and the
   emails he approved without touching them. It is injected into every first-touch and revise prompt, so the writer learns.
3. The ladder: each stage unlocks from a measured rate, never by itself. When a gate is met the engine puts ONE control card on
   the approvals board ("⚙ Autopilot · first emails"); dragging it to Approved is Rodrigo's go-ahead, dragging it out turns it
   off, and the engine takes it out itself if the rate falls.
     Stage 1  Coaching           every email approved by hand (today)
     Stage 2  First emails       new first-touch cards go out on their own after a veto window unless he comments/skips/moves them
     Stage 3  Replies            measured on reply cards now; auto-answering info questions is the next build
     Stage 4  Ranges             measured on the numbers he approves vs the internal range; never automatic yet
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import db
from .asana import BOT
from .util import iso, log, now_utc, parse_iso

APPROVED_AS_IS, APPROVED_EDITED, SKIPPED, AUTO = "approved_as_is", "approved_edited", "skipped", "auto"
CONTROL_PREFIX = "⚙ Autopilot"
REVISED_PREFIX = f"{BOT} Revised based on your comment"
AUTO_COMMENT = "Autopilot:"
TEST_ONLY_RE = re.compile(r"^\s*(please\s+)?(send\s+(me\s+)?)?(a\s+)?test\b", re.I)

STAGES = [
    ("first_touch", "Stage 2 · first emails go out on their own", "first emails you approve without changing a word"),
    ("reply", "Stage 3 · Charles answers info questions on his own", "answers you approve without changing a word"),
    ("ranges", "Stage 4 · ranges go out on their own", "numbers you pick that land inside the engine's range"),
]


def init(conn) -> None:
    db.init_db(conn)          # tables live in db.SCHEMA; idempotent, so older databases get them on the first tick


def _a(cfg: Any) -> dict[str, Any]:
    return cfg.get("autonomy", {}) or {}


def is_control(t: dict[str, Any]) -> bool:
    return (t.get("name") or "").startswith(CONTROL_PREFIX)


def rodrigo_comments(comments: list[dict[str, Any]]) -> list[str]:
    """His own words on a card: every comment the engine did not write, minus bare 'send me a test' requests."""
    out = []
    for c in comments:
        txt = (c.get("text") or "").strip()
        if txt and not txt.startswith(BOT) and not TEST_ONLY_RE.match(txt):
            out.append(txt)
    return out


def record_lesson(conn, kind: str, text: str, outreach_id: int | None, card_gid: str | None,
                  before: str | None = None, after: str | None = None) -> None:
    conn.execute("INSERT OR IGNORE INTO lessons(kind,outreach_id,card_gid,text,before_body,after_body,created_at) VALUES(?,?,?,?,?,?,?)",
                 (kind, outreach_id, card_gid, text.strip()[:2000], before, after, iso()))


def record_decision(conn, card_gid: str, outreach_id: int | None, kind: str, decision: str, comments: list[dict[str, Any]],
                    final_body: str | None = None, engine_body: str | None = None) -> bool:
    """One row per card, first decision wins. `engine_body` is the engine's latest text (its own rewrite after his comment
    included): a final body different from it means Rodrigo edited the card by hand. Returns True when newly recorded."""
    if conn.execute("SELECT 1 FROM card_decisions WHERE card_gid=?", (card_gid,)).fetchone():
        return False
    fb = rodrigo_comments(comments)
    rounds = sum(1 for c in comments if (c.get("text") or "").startswith((REVISED_PREFIX, f"{BOT} Held:")))
    hand = bool(final_body and engine_body and _norm(final_body) != _norm(engine_body))
    auto_moved = any((c.get("text") or "").startswith(f"{BOT} {AUTO_COMMENT}") for c in comments)
    if decision in (APPROVED_AS_IS, APPROVED_EDITED):
        if fb or rounds or hand:
            decision = APPROVED_EDITED
        elif auto_moved:
            decision = AUTO                       # went out on autopilot: not a vote either way
        else:
            decision = APPROVED_AS_IS
    draft = None
    if outreach_id:
        r = conn.execute("SELECT draft_body FROM outreach WHERE id=?", (outreach_id,)).fetchone()
        draft = r["draft_body"] if r else None
    conn.execute("INSERT INTO card_decisions(card_gid,outreach_id,kind,decision,rounds,hand_edited,feedback,draft_body,final_body,decided_at) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?)", (card_gid, outreach_id, kind, decision, rounds, int(hand), json.dumps(fb), draft, final_body, iso()))
    for txt in fb:
        record_lesson(conn, kind, txt, outreach_id, card_gid, draft, final_body)
    if hand and not fb:
        record_lesson(conn, kind, "(Rodrigo edited the text on the card himself; compare before/after)", outreach_id, card_gid, engine_body, final_body)
    return True


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def card_kind(notes: str) -> str:
    return "reply" if re.search(r"\(reply to outreach #\d+\)", notes or "") else "first_touch"


def scan_board(conn, cfg: Any, asana) -> dict[str, int]:
    """Record decisions for cards in Sent and Skip that are not on file yet (backfills the history; idempotent).
    Cards sent by send_approved are recorded there with the hand-edit check; this catches the rest."""
    from .approvals import parse_card
    a = cfg.get("outreach.asana", {}) or {}
    n = {"recorded": 0}
    known = {r[0] for r in conn.execute("SELECT card_gid FROM card_decisions").fetchall()}
    try:
        from .conversations import board_sections
        answered = board_sections(cfg, asana)[1]
    except Exception:  # noqa: BLE001
        answered = None
    for section, decision in ((a.get("sent"), APPROVED_AS_IS), (answered, APPROVED_AS_IS), (a.get("skip"), SKIPPED)):
        if not section:
            continue
        for t in asana.section_tasks(section):
            if t["gid"] in known or is_control(t):
                continue
            card = parse_card(t.get("notes") or "")
            if not card:
                continue
            try:
                comments = asana.comments(t["gid"])
            except Exception as e:  # noqa: BLE001
                log.warning("learning: comments for %s: %s", t["gid"], e)
                continue
            if record_decision(conn, t["gid"], card["outreach_id"], card_kind(t.get("notes") or ""), decision, comments, card["body"]):
                n["recorded"] += 1
    return n


# ---------- the ladder ----------

def rate(conn, kind: str, window: int) -> dict[str, Any]:
    """Share of Rodrigo's last `window` decisions on this kind of card that were 'approved as written'.
    Autopilot sends are not votes; skips and edits count against."""
    rows = conn.execute("SELECT decision FROM card_decisions WHERE kind=? AND decision != ? ORDER BY decided_at DESC, id DESC LIMIT ?",
                        (kind, AUTO, window)).fetchall()
    d = [r[0] for r in rows]
    as_is = d.count(APPROVED_AS_IS)
    return {"decided": len(d), "as_is": as_is, "edited": d.count(APPROVED_EDITED), "skipped": d.count(SKIPPED),
            "rate": (as_is / len(d)) if d else None}


def range_rate(conn, window: int) -> dict[str, Any]:
    rows = conn.execute("SELECT price, internal_low, internal_high FROM price_decisions ORDER BY decided_at DESC, id DESC LIMIT ?", (window,)).fetchall()
    inside = sum(1 for r in rows if r[1] and r[2] and r[1] <= r[0] <= r[2])
    return {"decided": len(rows), "as_is": inside, "rate": (inside / len(rows)) if rows else None}


def record_price(conn, cfg: Any, property_id: int, price: int) -> None:
    """Stage 4 data: the number Rodrigo picked next to the engine's internal range for that house."""
    from .valuation import preliminary_range
    p = db.row_to_dict(conn.execute("SELECT p.*, v.arv, v.arv_method, v.comp_count, v.repair_tier, v.repair_cost, v.mao FROM properties p "
                                    "LEFT JOIN valuations v ON v.property_id=p.id WHERE p.id=?", (property_id,)).fetchone()) or {}
    try:
        rng = preliminary_range(p, cfg) if p else None
    except Exception:  # noqa: BLE001
        rng = None
    conn.execute("INSERT INTO price_decisions(property_id,price,internal_low,internal_high,decided_at) VALUES(?,?,?,?,?)",
                 (property_id, int(price), int(rng["low"]) if rng else None, int(rng["high"]) if rng else None, iso()))


def ladder(conn, cfg: Any) -> list[dict[str, Any]]:
    out = []
    for kind, title, what in STAGES:
        g = _a(cfg).get(kind, {}) or {}
        window, need, unlock = int(g.get("window", 30)), int(g.get("min_decided", 30)), float(g.get("unlock_rate", 0.8))
        r = range_rate(conn, window) if kind == "ranges" else rate(conn, kind, window)
        ready = r["decided"] >= need and r["rate"] is not None and r["rate"] >= unlock
        out.append({"kind": kind, "title": title, "what": what, "window": window, "need": need, "unlock": unlock, **r, "ready": ready})
    return out


def ladder_lines(conn, cfg: Any, on: dict[str, bool] | None = None) -> list[str]:
    on = on or {}
    lines = []
    for s in ladder(conn, cfg):
        pct = "n/a" if s["rate"] is None else f"{s['rate']:.0%}"
        state = "ON" if on.get(s["kind"]) else ("READY, waiting for your go-ahead" if s["ready"] else "learning")
        lines.append(f"- {s['title']}: {pct} of the last {s['decided']} ({s['what']}; unlocks at {s['unlock']:.0%} over {s['need']}) · {state}")
    return lines


# ---------- the knowledge base ----------

def knowledge_path(cfg: Any):
    return cfg.path("reports") / "knowledge" / "learned.md"


def build_knowledge(conn, cfg: Any) -> str:
    k = _a(cfg).get("knowledge", {}) or {}
    max_lessons, max_examples = int(k.get("max_lessons", 30)), int(k.get("max_examples", 3))
    lessons = conn.execute("SELECT l.kind, l.text, l.created_at, o.property_ids FROM lessons l LEFT JOIN outreach o ON o.id=l.outreach_id "
                           "WHERE l.text NOT LIKE '(Rodrigo edited%' ORDER BY l.created_at DESC, l.id DESC").fetchall()
    seen, picked = set(), []
    for r in lessons:
        key = _norm(r["text"]).lower()
        if key in seen:
            continue
        seen.add(key)
        picked.append(r)
        if len(picked) >= max_lessons:
            break
    edits = conn.execute("SELECT before_body, after_body FROM lessons WHERE text LIKE '(Rodrigo edited%' AND before_body IS NOT NULL "
                         "AND after_body IS NOT NULL ORDER BY created_at DESC, id DESC LIMIT 2").fetchall()
    good = conn.execute("SELECT final_body FROM card_decisions WHERE kind='first_touch' AND decision=? AND final_body IS NOT NULL "
                        "ORDER BY decided_at DESC, id DESC LIMIT ?", (APPROVED_AS_IS, max_examples)).fetchall()
    if not (picked or good or edits):
        return ""
    out = ["What Rodrigo has taught us on past cards (his own words, newest first). These override any habit of yours; "
           "apply each one that fits this email:"]
    for r in picked:
        tag = "reply" if r["kind"] == "reply" else "first email"
        out.append(f"- [{tag}] {_norm(r['text'])[:400]}")
    for e in edits:
        out += ["", "He rewrote this one by hand. Before:", e["before_body"].strip(), "After (what he wanted):", e["after_body"].strip()]
    if good:
        out += ["", "First emails he approved without changing a word (the standard to hit; never copy their wording, the house is different):"]
        for i, r in enumerate(good, 1):
            out += [f"--- example {i} ---", r["final_body"].strip()]
    return "\n".join(out)


def write_knowledge(conn, cfg: Any) -> int:
    txt = build_knowledge(conn, cfg)
    path = knowledge_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(txt, encoding="utf-8")
    return len(txt)


def knowledge_text(cfg: Any) -> str:
    """The learned block for the drafting prompts ('' until Rodrigo has decided some cards). Read fresh each call."""
    try:
        return knowledge_path(cfg).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def learned_block(cfg: Any) -> str:
    """The knowledge base wrapped for a drafting prompt (first touch and revise)."""
    k = knowledge_text(cfg)
    return f"\n=== WHAT RODRIGO HAS TAUGHT US ===\n{k}\n=== END ===\n" if k else ""


# ---------- stage 2: autopilot for first emails ----------

def control_card(asana, cfg: Any, kind: str = "first_touch") -> tuple[dict[str, Any] | None, str | None]:
    """The autopilot control card for this stage and the section it sits in."""
    a = cfg.get("outreach.asana", {}) or {}
    from .approvals import needs_revision_section
    for sec in (a.get("approved"), a.get("to_review"), a.get("skip"), needs_revision_section(cfg, asana)):
        if not sec:
            continue
        for t in asana.section_tasks(sec):
            if is_control(t) and f"[{kind}]" in (t.get("notes") or ""):
                return t, sec
    return None, None


CONTROL_NOTES = """Autopilot for FIRST EMAILS [{kind}]

You approved {as_is} of your last {decided} first emails without changing a word ({rate}). That clears the bar you set ({unlock} over {need}).

Drag THIS card to Approved to turn autopilot on. From then on, a new first email card that you leave alone for {veto} hours
(no comment, not moved, not skipped) moves itself to Approved and goes out in the normal send window, at the normal pace,
under the same daily cap. Comment on a card, skip it or move it and autopilot leaves it to you.

Drag this card out of Approved (anywhere) to turn autopilot off at once. If your approve-as-written rate drops under {pause},
the engine turns it off itself and tells you here.

Replies and numbers are NOT on autopilot. Those are the next stages and keep going through you."""


def autopilot_tick(conn, cfg: Any, asana, now=None) -> dict[str, Any]:
    """Stage 2. Offer the control card when the gate is met; when it sits in Approved, move first-touch cards nobody touched
    for `veto_hours` from To Review to Approved (send_approved then sends them at the normal pace). Never raises."""
    res = {"on": False, "offered": False, "moved": 0, "paused": False}
    g = _a(cfg).get("first_touch", {}) or {}
    if not g or g.get("enabled") is False:
        return res
    a = cfg.get("outreach.asana", {}) or {}
    try:
        stage = next(s for s in ladder(conn, cfg) if s["kind"] == "first_touch")
        card, where = control_card(asana, cfg)
        if card is None:
            if stage["ready"]:
                notes = CONTROL_NOTES.format(kind="first_touch", as_is=stage["as_is"], decided=stage["decided"], rate=f"{stage['rate']:.0%}",
                                             unlock=f"{stage['unlock']:.0%}", need=stage["need"], veto=g.get("veto_hours", 3),
                                             pause=f"{float(g.get('pause_below', 0.65)):.0%}")
                asana.create_task(a["project"], a["to_review"], f"{CONTROL_PREFIX} · first emails · ready for your go-ahead", notes)
                res["offered"] = True
            return res
        if where != a.get("approved"):
            return res
        res["on"] = True
        if stage["rate"] is not None and stage["decided"] >= int(g.get("min_decided", 30)) and stage["rate"] < float(g.get("pause_below", 0.65)):
            asana.move(card["gid"], a["to_review"])
            asana.comment(card["gid"], f"Autopilot OFF: you approved only {stage['rate']:.0%} of the last {stage['decided']} first emails as written. "
                                       f"Back to approving each one. Drag this card to Approved again when you want it back on.")
            res.update(on=False, paused=True)
            return res
        veto_s = float(g.get("veto_hours", 3)) * 3600
        now = now or now_utc()
        for t in asana.section_tasks(a["to_review"]):
            if is_control(t) or card_kind(t.get("notes") or "") != "first_touch":
                continue
            created = parse_iso(t.get("created_at"))
            if not created or (now - created).total_seconds() < veto_s:
                continue
            from .approvals import parse_card
            pc = parse_card(t.get("notes") or "")
            if not pc:
                continue
            row = conn.execute("SELECT status, variant FROM outreach WHERE id=?", (pc["outreach_id"],)).fetchone()
            if not row or row["status"] != "pending_approval" or (row["variant"] or "") != "claude":
                continue                                    # only Claude-written emails; a template fallback waits for Rodrigo
            comments = asana.comments(t["gid"])
            if rodrigo_comments(comments) or any((c.get("text") or "").startswith(REVISED_PREFIX) for c in comments):
                continue                                    # he engaged with it: his call, not autopilot's
            asana.comment(t["gid"], f"{AUTO_COMMENT} nobody touched this for {g.get('veto_hours', 3)} hours, so it moved to Approved and goes out "
                                    f"in the next send round. Comment on it before then to hold it.")
            asana.move(t["gid"], a["approved"])
            res["moved"] += 1
    except Exception as e:  # noqa: BLE001 — autopilot must never break the approvals tick
        log.warning("autopilot: %s", e)
        res["error"] = str(e)[:200]
    return res


def learn_tick(conn, cfg: Any, asana, now=None) -> dict[str, Any]:
    """Run on every approvals tick: record decisions, rebuild the knowledge base, run the autopilot."""
    init(conn)
    out: dict[str, Any] = {}
    try:
        out["board"] = scan_board(conn, cfg, asana)
    except Exception as e:  # noqa: BLE001
        log.warning("learning: board scan failed: %s", e)
        out["board"] = {"error": str(e)[:200]}
    out["knowledge_chars"] = write_knowledge(conn, cfg)
    out["autopilot"] = autopilot_tick(conn, cfg, asana, now)
    out["ladder"] = {s["kind"]: {"rate": s["rate"], "decided": s["decided"], "ready": s["ready"]} for s in ladder(conn, cfg)}
    return out
