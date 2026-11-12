"""The daily learning loop for the house (MLS single-family) pipeline (Rodrigo, 2026-11-12: "run a daily loop on how we can
make our MLS system better … take into account all the actions that we have done, from what we skipped to what we're sending
offers to, the type of messages that we're sending out … constantly learn … and also improve on its own").

Runs as the `learn` step of run-daily (after comps, before outreach, so today's emails already use it) and by hand with
`make learn-loop`. Every day:

1. EVIDENCE. Every first email is joined to the house it was about and to what happened to it: approved as written,
   changed by Rodrigo, skipped or deleted, sent, answered (and how: open, a number, a no), offered on.
2. MEASURE. Per segment (lane, county, city, price band, type, days on market, price cut, photos, comps grade, ARV vs ask,
   brokerage, how many listings the agent has, who wrote the email, its length): skip rate and reply rate against the average.
3. IMPROVE ON ITS OWN (bounded, order only):
   - `data/learned_prefs.json`: a small weight per segment. Houses like the ones he works and that get answers move UP the
     outreach order, houses like the ones he skips move DOWN. Never excludes, never changes HOT/WARM, moves at most a step a day.
   - the PLAYBOOK (`reports/knowledge/playbook.md`): Claude reads the week (skips with his comments, edits before/after, emails
     that got answers vs silence, offers) and writes short rules for the writer. A SECOND Claude call checks every rule against
     the settled decisions; only rules it clears are kept. The playbook goes into every writer prompt next to his own comments.
     Every email still passes the validators, the second reader and his approval.
4. ASK FOR THE REST. Anything that is his call (stop drafting a kind of house, pricing habits, anything about money or
   sending) is a PROPOSAL: one card each on Claude Updates → Needs Rodrigo, never repeated. A "stop drafting X" card dragged
   to Approved is applied by the loop itself the next morning; the others are picked up by the next Claude session.
5. SCORE ITSELF. One row a day (`learn_runs`): as-written rate, skip rate, reply rate, offers. The morning report shows the
   trend, what it learned, what it changed and what waits on him.

It never sends anything, never touches a price, a cap, a pause switch or the trust-ladder gates."""
from __future__ import annotations

import json
import re
import statistics
from datetime import timedelta
from typing import Any

from . import db
from .learning import APPROVED_AS_IS, APPROVED_EDITED, AUTO, OFF_MARKET, SKIPPED, knowledge_path
from .util import iso, log, now_utc, parse_iso, today_et

HOUSE_DIMS = ("lane", "county", "city", "price", "type", "dom", "price_cut", "photos", "comps", "arv_vs_ask", "brokerage", "agent_listings")
EMAIL_DIMS = ("writer", "words")
DIM_WORDS = {"lane": "lane", "county": "county", "city": "city", "price": "price", "type": "type", "dom": "days on market",
             "price_cut": "price cut", "photos": "photos", "comps": "comps grade", "arv_vs_ask": "ARV vs asking",
             "brokerage": "brokerage", "agent_listings": "agent's listings", "writer": "written by", "words": "email length"}
POSITIVE = ("INTERESTED", "ASK_FOR_OFFER", "COUNTER_WITH_NUMBER", "QUESTION")
NEGATIVE = ("NOT_INTERESTED", "SOLD_OR_PENDING", "STOP")
NOT_AN_ANSWER = ("BOUNCE", "OUT_OF_OFFICE")

# What no learned rule may touch: Rodrigo's settled decisions. Given to the writer of the rules AND to the checker.
NON_NEGOTIABLES = """- No price, percentage, range or "cash offer" in a first email or a reply. A number only goes out when Rodrigo approves it for that house.
- Charles never says or implies that we are the buyer ("we buy", "serious buyers", "we're investors"). "We're looking for properties in X".
- Every first email opens with the intro: Charles with Titan Realty Investments, I saw your listing on <street>.
- Direct and matter of fact: straight to one question after the intro, 40-90 words, no "curious", "honestly", "doesn't scare us", no reciting the listing back.
- With 4 or more photos never ask or imply "how much work does it need"; ask what pictures can't show.
- Never promise a date, a timeframe, proof of funds, a signature, an attachment, or to waive the inspection period.
- Never pursue REO / bank-owned, short sales, auctions, or listings that rule out assignment or an inspection period.
- Nothing is sent without Rodrigo's approval. No rule may be about sending on its own, the daily cap, the pause, or autopilot.
- Rodrigo's own comments on cards always win over a learned rule."""

RULE_BLOCK_RE = re.compile(r"\$\s?\d|\d\s?%|\bpercent\b|\b(auto[- ]?send|autopilot|unpause|without (his|rodrigo'?s?) (approval|go[- ]ahead)|daily cap|"
                           r"automatic(ally)?|on (its|their) own|proof of funds|waive|attach(ed|ment)?|we buy|cash offer)\b", re.I)

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "writer_rules": {"type": "array", "items": {"type": "object", "properties": {
            "rule": {"type": "string"}, "applies_to": {"type": "string", "enum": ["first_email", "reply", "any"]},
            "evidence": {"type": "string"}, "evidence_count": {"type": "integer"}}, "required": ["rule", "applies_to", "evidence", "evidence_count"]}},
        "retire": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "integer"}, "why": {"type": "string"}},
                                              "required": ["id", "why"]}},
        "proposals": {"type": "array", "items": {"type": "object", "properties": {"title": {"type": "string"}, "why": {"type": "string"},
                                                                                 "change": {"type": "string"}},
                                                 "required": ["title", "why", "change"]}},
    },
    "required": ["summary", "writer_rules", "retire", "proposals"],
}
CHECK_SCHEMA = {"type": "object", "properties": {"verdicts": {"type": "array", "items": {"type": "object", "properties": {
    "index": {"type": "integer"}, "keep": {"type": "boolean"}, "reason": {"type": "string"}}, "required": ["index", "keep", "reason"]}}},
    "required": ["verdicts"]}

REVIEW_SYSTEM = """You are the daily reviewer of a South Florida wholesaler's outreach engine (Titan Realty Investments). The engine
emails listing agents about on-market houses in the voice of Charles, a junior acquisitions agent. Rodrigo, the principal, approves,
edits or skips every email on a board, answers the agents who reply and decides every offer. You get what happened in the last days.
THE GOAL IS HOUSES UNDER CONTRACT. Approvals and answers only matter because they lead there. Your job: find what the engine
should do differently tomorrow so more conversations turn into offers and more offers into contracts: which houses and agents are
worth the effort, where leads stall (no answer, answer but no offer, offer but no contract), what Rodrigo's own moves show about
strategy, and how the emails should change so he approves them untouched and agents answer.

Settled decisions you may never contradict or weaken:
{non_negotiables}

What you return:
- summary: two or three plain sentences Rodrigo reads on his phone: what the last days show. No jargon, no selling.
- writer_rules: 0 to 3 NEW short rules for the email writer (one sentence each, imperative, concrete, about wording, angle,
  which question to ask, or what to leave out). Only a pattern you can see in at least two cards or replies (evidence_count), never
  a rule already in the playbook or already said in his own comments, never anything about prices, offers, dates, or sending.
  Fewer is better: an empty list is a good answer when the evidence is thin.
- retire: playbook rules (by id) that the evidence now contradicts, or that he overruled in a comment.
- proposals: 0 to 2 changes that are Rodrigo's call: strategy first (a kind of lead we are not working that his own deals point
  to, where the funnel leaks, who to follow up with), then which houses to stop or start drafting for, pricing habits you see in
  his offers versus the engine's range, a step in the process. title = a short question; why = the evidence with counts;
  change = exactly what would change. Never propose sending without his approval.
Say only what the data shows. Small samples: say so and propose nothing."""

CHECK_SYSTEM = """You are the second reader of rules that an analyst wants to add to an email writer's playbook at a real-estate
wholesaler. You did not write them. For each rule decide keep or drop. Drop a rule when it contradicts or weakens any settled
decision below, mentions a price / percentage / offer / date / commitment, is about sending or approval, is vague ("be more
engaging"), or the evidence given does not support it (a single card, or a guess). Keep only rules that are concrete, safe and
supported. When unsure, drop.

Settled decisions:
{non_negotiables}"""


def _c(cfg: Any) -> dict[str, Any]:
    return cfg.get("learning_loop", {}) or {}


def prefs_path(cfg: Any):
    return cfg.root / (_c(cfg).get("prefs_file") or "data/learned_prefs.json")


def playbook_path(cfg: Any):
    return knowledge_path(cfg).parent / "playbook.md"


# ---------- features ----------

def _band(v: float | None, cuts: tuple[tuple[float, str], ...], last: str) -> str | None:
    if v is None:
        return None
    for limit, name in cuts:
        if v < limit:
            return name
    return last


def house_features(p: dict[str, Any]) -> dict[str, str]:
    """The segments one house falls in. Missing data = no segment for that dimension."""
    lp, arv = p.get("list_price"), p.get("arv")
    n = p.get("agent_listings")
    f = {
        "lane": p.get("lane"),
        "county": p.get("county"),
        "city": (p.get("city") or "").strip().title() or None,
        "price": _band(lp, ((200_000, "under $200k"), (350_000, "$200-350k"), (500_000, "$350-500k"), (750_000, "$500-750k")), "$750k+") if lp else None,
        "type": (p.get("property_type") or "").strip().lower().replace("_", " ") or None,
        "dom": _band(p.get("days_on_market"), ((15, "under 15 days"), (60, "15-59 days"), (120, "60-119 days")), "120+ days"),
        "price_cut": "yes" if (p.get("price_cuts") or p.get("back_on_market")) else "no",
        "photos": _band(p.get("photos_count"), ((4, "under 4"), (15, "4-14")), "15+"),
        "comps": p.get("comp_grade"),
        "arv_vs_ask": _band(arv / lp, ((1.2, "under 1.2x"), (1.5, "1.2-1.5x")), "over 1.5x") if arv and lp else None,
        "brokerage": (p.get("brokerage") or p.get("a_brokerage") or "").strip().lower() or None,
        "agent_listings": _band(n, ((3, "1-2"), (6, "3-5")), "6+") if n else None,
    }
    return {k: str(v) for k, v in f.items() if v}


def email_features(o: dict[str, Any]) -> dict[str, str]:
    words = len((o.get("body") or "").split())
    return {"writer": "Claude" if (o.get("variant") or "") == "claude" else "template",
            "words": _band(words, ((60, "under 60 words"), (91, "60-90 words")), "over 90 words") or "?"}


# ---------- evidence ----------

def evidence(conn, now=None, mature_days: int = 3) -> list[dict[str, Any]]:
    """One row per first email (houses only): the house, the email, and what happened to it."""
    now = now or now_utc()
    listings = {r[0]: r[1] for r in conn.execute("SELECT agent_id, COUNT(*) FROM properties WHERE agent_id IS NOT NULL GROUP BY agent_id")}
    deleted = {r[0] for r in conn.execute("SELECT gid FROM board_cards WHERE gone_how='deleted'")}
    out = []
    for o in conn.execute("SELECT * FROM outreach WHERE channel='email' AND kind='first_touch' ORDER BY id").fetchall():
        o = dict(o)
        try:
            pids = [int(x) for x in json.loads(o["property_ids"] or "[]")]
        except (ValueError, TypeError):
            pids = []
        if not pids:
            continue
        p = db.row_to_dict(conn.execute(
            "SELECT p.*, v.lane, v.rating, v.arv, v.mao, v.comp_grade, a.brokerage, a.name AS agent_name FROM properties p "
            "LEFT JOIN valuations v ON v.property_id=p.id LEFT JOIN agents a ON a.id=p.agent_id WHERE p.id=?", (pids[0],)).fetchone())
        if not p:
            continue
        p["agent_listings"] = listings.get(o["agent_id"])
        d = db.row_to_dict(conn.execute("SELECT decision, feedback, hand_edited, final_body, decided_at FROM card_decisions "
                                        "WHERE outreach_id=? AND kind='first_touch' ORDER BY id LIMIT 1", (o["id"],)).fetchone())
        decision, decided_at = (d["decision"], d["decided_at"]) if d else (None, None)
        if not decision and o.get("asana_task_id") in deleted and o["status"] != "sent":
            decision = SKIPPED                        # he deleted the card: the same vote as a skip
            r = conn.execute("SELECT gone_at FROM board_cards WHERE gid=?", (o["asana_task_id"],)).fetchone()
            decided_at = r[0] if r else None
        sent = o["status"] == "sent" and bool(o.get("sent_at"))
        reply = None
        if sent:
            reply = db.row_to_dict(conn.execute(
                "SELECT m.classification, m.body, m.created_at FROM messages m JOIN threads t ON t.id=m.thread_id WHERE t.agent_id=? "
                "AND m.direction='in' AND m.created_at>=? AND COALESCE(m.classification,'') NOT IN (?,?) ORDER BY m.id LIMIT 1",
                (o["agent_id"], o["sent_at"], *NOT_AN_ANSWER)).fetchone())
        sent_dt = parse_iso(o.get("sent_at")) if sent else None
        offer = db.row_to_dict(conn.execute(f"SELECT price, list_price, internal_low, internal_high, status FROM offers WHERE property_id IN "
                                            f"({','.join('?' * len(pids))}) ORDER BY id DESC LIMIT 1", pids).fetchone())
        cls = (reply or {}).get("classification")
        out.append({
            "outreach_id": o["id"], "street": p.get("street"), "city": p.get("city"), "list_price": p.get("list_price"),
            "agent": p.get("agent_name"), "features": {**house_features(p), **email_features(o)}, "body": o.get("body") or "",
            "decision": decision, "decided_at": decided_at, "feedback": json.loads((d or {}).get("feedback") or "[]"),
            "final_body": (d or {}).get("final_body"), "hand_edited": bool((d or {}).get("hand_edited")),
            "sent": sent, "sent_at": o.get("sent_at"),
            "mature": bool(sent and (reply or (sent_dt and (now - sent_dt) >= timedelta(days=mature_days)))),
            "replied": bool(reply), "reply_class": cls, "reply_text": (reply or {}).get("body"), "reply_at": (reply or {}).get("created_at"),
            "positive": cls in POSITIVE, "negative": cls in NEGATIVE, "offer": offer,
        })
    return out


# ---------- measure ----------

def _voted(e: dict[str, Any]) -> bool:
    return e["decision"] in (APPROVED_AS_IS, APPROVED_EDITED, SKIPPED)


def segment_stats(ev: list[dict[str, Any]]) -> dict[str, Any]:
    """Skip rate (of his decisions) and positive-reply rate (of sent emails old enough to judge) per segment, and overall."""
    base = {"decided": 0, "skipped": 0, "as_is": 0, "sent": 0, "replied": 0, "positive": 0}
    segs: dict[tuple[str, str], dict[str, int]] = {}
    for e in ev:
        buckets = [base] + [segs.setdefault((k, v), dict.fromkeys(base, 0)) for k, v in e["features"].items()]
        for b in buckets:
            if _voted(e):
                b["decided"] += 1
                b["skipped"] += e["decision"] == SKIPPED
                b["as_is"] += e["decision"] == APPROVED_AS_IS
            if e["mature"]:
                b["sent"] += 1
                b["replied"] += e["replied"]
                b["positive"] += e["positive"]
    return {"base": base, "segments": segs}


def _rate(k: int, n: int) -> float | None:
    return (k / n) if n else None


def _shrunk(k: int, n: int, base: float, m: float) -> float:
    """The segment's rate pulled toward the average: a segment of 3 cards does not get to shout."""
    return (k + base * m) / (n + m)


def compute_weights(stats: dict[str, Any], cfg: Any, previous: dict[str, float] | None = None) -> dict[str, float]:
    """One weight per house segment: + he works these and agents answer, - he skips these. Bounded, and it moves at most
    `max_step` a day from yesterday's weight, so one odd day cannot flip the order."""
    c = _c(cfg)
    min_n, m = int(c.get("min_segment_decisions", 8)), float(c.get("shrink", 5))
    cap, step = float(c.get("max_weight", 0.5)), float(c.get("max_step", 0.1))
    base, prev = stats["base"], previous or {}
    b_skip, b_pos = _rate(base["skipped"], base["decided"]), _rate(base["positive"], base["sent"])
    out: dict[str, float] = {}
    keys = {f"{d}={v}" for (d, v) in stats["segments"] if d in HOUSE_DIMS} | set(prev)
    for key in keys:
        d, _, v = key.partition("=")
        s = stats["segments"].get((d, v))
        target = 0.0
        if s and d in HOUSE_DIMS:
            if b_skip is not None and s["decided"] >= min_n:
                target += b_skip - _shrunk(s["skipped"], s["decided"], b_skip, m)           # skipped less than average = up
            if b_pos is not None and s["sent"] >= min_n:
                target += 2 * (_shrunk(s["positive"], s["sent"], b_pos, m) - b_pos)       # answered more than average = up
        target = max(-cap, min(cap, target))
        old = float(prev.get(key, 0.0))
        w = round(old + max(-step, min(step, target - old)), 3)
        if abs(w) >= 0.03:
            out[key] = w
    return out


def load_prefs(cfg: Any) -> dict[str, Any]:
    try:
        p = json.loads(prefs_path(cfg).read_text(encoding="utf-8"))
        return p if isinstance(p, dict) else {}
    except (OSError, ValueError):
        return {}


def save_prefs(cfg: Any, prefs: dict[str, Any]) -> None:
    path = prefs_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prefs, indent=2, sort_keys=True), encoding="utf-8")


def boost(p: dict[str, Any], prefs: dict[str, Any]) -> float:
    w = prefs.get("weights") or {}
    return round(sum(w.get(f"{k}={v}", 0.0) for k, v in house_features(p).items()), 3) if w else 0.0


def blocked(p: dict[str, Any], prefs: dict[str, Any]) -> str | None:
    """The segment Rodrigo approved to stop drafting for, when this house is in one."""
    b = set(prefs.get("blocked") or [])
    if not b:
        return None
    return next((f"{k}={v}" for k, v in house_features(p).items() if f"{k}={v}" in b), None)


def apply_to_candidates(conn, cfg: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Used by outreach: drop houses in a segment he approved to stop, then let what he works and what gets answers go first
    INSIDE each rating + lane tier (HOT before WARM and fresh deals first stay exactly as they were)."""
    if not _c(cfg).get("enabled", True) or not _c(cfg).get("apply_to_ranking", True):
        return rows
    prefs = load_prefs(cfg)
    if not (prefs.get("weights") or prefs.get("blocked")):
        return rows
    listings = {r[0]: r[1] for r in conn.execute("SELECT agent_id, COUNT(*) FROM properties WHERE agent_id IS NOT NULL GROUP BY agent_id")}
    brokerages = {r[0]: r[1] for r in conn.execute("SELECT id, brokerage FROM agents")}
    kept = []
    for p in rows:
        q = {**p, "agent_listings": listings.get(p.get("a_id")), "brokerage": brokerages.get(p.get("a_id"))}
        if blocked(q, prefs):
            continue
        p["learned_boost"] = boost(q, prefs)
        kept.append(p)
    tier = lambda p: (0 if p.get("rating") == "HOT" else 1, {"FRESH_DEAL": 0, "SITTING": 1}.get(p.get("lane"), 2))  # noqa: E731
    return sorted(kept, key=lambda p: (*tier(p), -round(p["learned_boost"], 1)))      # stable: ties keep the engine's own order


# ---------- metrics ----------

def metrics(conn, ev: list[dict[str, Any]], run_date: str, window: int = 30) -> dict[str, Any]:
    voted = sorted((e for e in ev if _voted(e)), key=lambda e: e["decided_at"] or "", reverse=True)[:window]
    mature = [e for e in ev if e["mature"]]
    offers = [dict(r) for r in conn.execute("SELECT price, list_price, internal_low, internal_high, status FROM offers").fetchall()]
    pct_list = [o["price"] / o["list_price"] for o in offers if o["price"] and o["list_price"]]
    vs_high = [o["price"] / o["internal_high"] for o in offers if o["price"] and o["internal_high"]]
    y = (parse_iso(run_date + "T12:00:00+00:00") - timedelta(days=1)).date().isoformat()
    day = lambda ts: (ts or "")[:10] == y  # noqa: E731
    return {
        "decided": len(voted),
        "as_written_pct": _pct(sum(e["decision"] == APPROVED_AS_IS for e in voted), len(voted)),
        "skip_pct": _pct(sum(e["decision"] == SKIPPED for e in voted), len(voted)),
        "sent": len(mature), "reply_pct": _pct(sum(e["replied"] for e in mature), len(mature)),
        "positive_pct": _pct(sum(e["positive"] for e in mature), len(mature)),
        "offers": len(offers), "offer_pct_of_list": round(statistics.median(pct_list) * 100, 1) if pct_list else None,
        "offer_vs_engine_high_pct": round(statistics.median(vs_high) * 100, 1) if vs_high else None, "offers_with_range": len(vs_high),
        "yesterday": {"date": y, "as_written": sum(e["decision"] == APPROVED_AS_IS and day(e["decided_at"]) for e in ev),
                      "changed": sum(e["decision"] == APPROVED_EDITED and day(e["decided_at"]) for e in ev),
                      "skipped": sum(e["decision"] == SKIPPED and day(e["decided_at"]) for e in ev),
                      "sent": sum(e["sent"] and day(e["sent_at"]) for e in ev),
                      "replies": sum(e["replied"] and day(e["reply_at"]) for e in ev),
                      "offers": conn.execute("SELECT COUNT(*) FROM offers WHERE substr(sent_at,1,10)=?", (y,)).fetchone()[0]},
    }


def _pct(k: int, n: int) -> float | None:
    return round(k / n * 100, 1) if n else None


# ---------- proposals ----------

def stat_proposals(stats: dict[str, Any], m: dict[str, Any], cfg: Any, prefs: dict[str, Any]) -> list[dict[str, Any]]:
    """What the numbers alone say is his call: a kind of house he almost always skips, offers far from the engine's range."""
    c = _c(cfg)
    min_n, rate = int(c.get("min_segment_decisions", 8)), float(c.get("propose_skip_rate", 0.8))
    out = []
    for (d, v), s in sorted(stats["segments"].items(), key=lambda kv: -kv[1]["decided"]):
        key = f"{d}={v}"
        if d not in HOUSE_DIMS or s["decided"] < min_n or key in (prefs.get("blocked") or []):
            continue
        if s["skipped"] / s["decided"] >= rate and s["decided"] < stats["base"]["decided"]:      # not when the segment is simply every card
            out.append({"key": f"skip_segment:{key}", "kind": "skip_segment", "payload": {"segment": key},
                        "title": f"Stop drafting first emails for houses with {DIM_WORDS[d]} = {v}?",
                        "why": f"You skipped or deleted {s['skipped']} of the last {s['decided']} cards like this "
                               f"(your average is {_pct(stats['base']['skipped'], stats['base']['decided'])}%).",
                        "change": "The engine stops writing first emails for these houses (they stay in the database and the ranking). "
                                  "Drag this card to Approved and it is applied the next morning; leave it or delete it and nothing changes."})
    vs, n = m.get("offer_vs_engine_high_pct"), m.get("offers_with_range") or 0
    if vs is not None and n >= int(c.get("min_offers_for_pricing", 5)) and abs(vs - 100) >= float(c.get("pricing_gap_pct", 10)):
        side = "above" if vs > 100 else "below"
        out.append({"key": f"pricing:{side}", "kind": "note", "payload": {},
                    "title": f"Your offers run {abs(vs - 100):.0f}% {side} the top of the engine's range. Move the range?",
                    "why": f"Across {n} offers, the typical one was {vs:.0f}% of the engine's internal high (and {m.get('offer_pct_of_list')}% of asking).",
                    "change": "Nothing changes on its own. If you say yes, a Claude session adjusts how the internal range is built so it "
                              "lands where you actually offer."})
    return out


def post_proposals(conn, cfg: Any, asana, proposals: list[dict[str, Any]]) -> list[str]:
    """One card per NEW proposal on Claude Updates → Needs Rodrigo, at most `max_proposals_per_day`; a proposal is never made twice."""
    a = cfg.get("outreach.asana", {}) or {}
    posted = []
    left = int(_c(cfg).get("max_proposals_per_day", 2))
    for p in proposals:
        if left <= 0:
            break
        if conn.execute("SELECT 1 FROM learn_proposals WHERE key=?", (p["key"],)).fetchone():
            continue
        gid = None
        if asana is not None and a.get("updates_project") and a.get("needs_rodrigo"):
            try:
                t = asana.create_task(a["updates_project"], a["needs_rodrigo"], f"🧠 Daily learning · {p['title']}",
                                      f"{p['title']}\n\nWhy: {p['why']}\n\nWhat would change: {p['change']}\n\n(From the daily learning loop. "
                                      f"It asks once and never repeats a question.)")
                gid = (t or {}).get("gid")
            except Exception as e:  # noqa: BLE001
                log.warning("learn loop: proposal card: %s", e)
                continue                                   # not recorded: asked again tomorrow
        conn.execute("INSERT INTO learn_proposals(key,kind,title,body,payload,status,card_gid,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (p["key"], p["kind"], p["title"], f"{p['why']} {p['change']}", json.dumps(p.get("payload") or {}),
                      "posted" if gid else "logged", gid, iso(), iso()))
        posted.append(p["title"])
        left -= 1
    return posted


def apply_approved(conn, cfg: Any, asana, prefs: dict[str, Any]) -> list[str]:
    """A 'stop drafting X' card he dragged to Approved on Claude Updates is applied here and moved to Done."""
    c = (_c(cfg).get("asana") or {})
    if asana is None or not c.get("approved"):
        return []
    try:
        approved = {t["gid"] for t in asana.section_tasks(c["approved"])}
    except Exception as e:  # noqa: BLE001
        log.warning("learn loop: reading Approved: %s", e)
        return []
    applied = []
    for r in conn.execute("SELECT * FROM learn_proposals WHERE status='posted' AND kind='skip_segment'").fetchall():
        if r["card_gid"] not in approved:
            continue
        seg = (json.loads(r["payload"] or "{}")).get("segment")
        if not seg:
            continue
        prefs["blocked"] = sorted(set(prefs.get("blocked") or []) | {seg})
        conn.execute("UPDATE learn_proposals SET status='applied', updated_at=? WHERE id=?", (iso(), r["id"]))
        applied.append(seg)
        try:
            asana.comment(r["card_gid"], f"Applied: the engine no longer drafts first emails for {seg.replace('=', ' = ')}. "
                                         f"To undo it, tell Claude \"start drafting {seg} again\".")
            if c.get("done"):
                asana.move(r["card_gid"], c["done"])
        except Exception as e:  # noqa: BLE001
            log.warning("learn loop: closing proposal card: %s", e)
    return applied


# ---------- the playbook ----------

def active_rules(conn) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM playbook_rules WHERE retired_at IS NULL ORDER BY id").fetchall()]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", (s or "").lower())).strip()


def rule_problem(rule: str) -> str | None:
    """Deterministic guard, before any model is asked: a learned rule is never about money, promises or sending."""
    r = (rule or "").strip()
    if not (20 <= len(r) <= 300):
        return "too short or too long"
    m = RULE_BLOCK_RE.search(r)
    return f"touches a settled decision ({m.group(0)})" if m else None


def write_playbook(conn, cfg: Any) -> int:
    rules = active_rules(conn)
    tag = {"first_email": "first email", "reply": "reply", "any": "any email"}
    txt = ""
    if rules:
        txt = "\n".join(["What the daily review of our own results has taught us (patterns across many cards and replies; "
                         "Rodrigo's own comments above win whenever they disagree):"]
                        + [f"- [{tag.get(r['applies_to'], 'any email')}] {r['rule']}" for r in rules])
    path = playbook_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(txt, encoding="utf-8")
    return len(rules)


def playbook_text(cfg: Any) -> str:
    if not _c(cfg).get("enabled", True):
        return ""
    try:
        return playbook_path(cfg).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _house_line(e: dict[str, Any]) -> str:
    f = e["features"]
    bits = [f.get(k) for k in ("type", "price", "dom", "lane", "comps", "arv_vs_ask", "agent_listings", "brokerage") if f.get(k)]
    return f"{e.get('street')}, {e.get('city')} ({' · '.join(bits)}; email by {f.get('writer')}, {f.get('words')})"


def digest(conn, ev: list[dict[str, Any]], stats: dict[str, Any], m: dict[str, Any], previous: dict[str, Any] | None, days: int = 7,
           now=None) -> str:
    """What the reviewer reads: the recent actions in plain text, bounded in size."""
    since = iso((now or now_utc()) - timedelta(days=days))
    recent = lambda ts: bool(ts) and ts >= since  # noqa: E731
    cut = lambda s, n=500: re.sub(r"\s+", " ", s or "").strip()[:n]  # noqa: E731
    L = [f"NUMBERS (last {m['decided']} decisions): approved as written {m['as_written_pct']}%, skipped {m['skip_pct']}%. "
         f"Sent emails old enough to judge: {m['sent']}, answered {m['reply_pct']}%, open / asking {m['positive_pct']}%. "
         f"Offers logged: {m['offers']}, typical offer {m['offer_pct_of_list']}% of asking, {m['offer_vs_engine_high_pct']}% of the engine's internal high."]
    by_status = dict(conn.execute("SELECT status, COUNT(*) FROM offers GROUP BY status").fetchall())
    L.append(f"FUNNEL TO CONTRACT (all time): {sum(e['sent'] for e in ev)} first emails sent, {sum(e['replied'] for e in ev)} agents answered, "
             f"{sum(e['positive'] for e in ev)} open, {m['offers']} offers; offers by status: {by_status or 'none'}.")
    if previous:
        L.append(f"A WEEK AGO: as written {previous.get('as_written_pct')}%, skipped {previous.get('skip_pct')}%, answered {previous.get('reply_pct')}%.")
    base = stats["base"]
    b_skip = _rate(base["skipped"], base["decided"]) or 0
    seg = [(abs(s["skipped"] / s["decided"] - b_skip), d, v, s) for (d, v), s in stats["segments"].items() if s["decided"] >= 5]
    L.append("\nSEGMENTS FURTHEST FROM HIS AVERAGE (decisions, skipped, sent, answered positively):")
    L += [f"- {DIM_WORDS[d]} = {v}: {s['decided']} decided, {s['skipped']} skipped; {s['sent']} sent, {s['positive']} positive"
          for _, d, v, s in sorted(seg, reverse=True)[:14]] or ["- (not enough decisions per segment yet)"]

    def block(title: str, rows: list[dict[str, Any]], n: int, fmt) -> None:
        if rows:
            L.append(f"\n{title} ({len(rows)}, showing {min(n, len(rows))}):")
            L.extend(fmt(e) for e in rows[-n:])

    skipped = [e for e in ev if e["decision"] == SKIPPED and recent(e["decided_at"])]
    edited = [e for e in ev if e["decision"] == APPROVED_EDITED and recent(e["decided_at"])]
    clean = [e for e in ev if e["decision"] == APPROVED_AS_IS and recent(e["decided_at"])]
    pos = [e for e in ev if e["positive"]]
    neg = [e for e in ev if e["negative"]]
    silent = [e for e in ev if e["mature"] and not e["replied"]]
    block("SKIPPED OR DELETED BY RODRIGO", skipped, 15, lambda e: f"- {_house_line(e)}" + (f" | his words: {cut(' / '.join(e['feedback']), 300)}" if e["feedback"] else ""))
    block("CHANGED BY RODRIGO BEFORE APPROVING", edited, 8, lambda e: f"- {_house_line(e)} | his words: {cut(' / '.join(e['feedback']), 300) or '(edited the text himself)'}"
          + (f"\n  ENGINE WROTE: {cut(e['body'])}\n  WENT OUT AS: {cut(e['final_body'])}" if e["hand_edited"] and e["final_body"] else ""))
    block("APPROVED WITHOUT A CHANGE", clean, 5, lambda e: f"- {_house_line(e)}\n  EMAIL: {cut(e['body'])}")
    block("EMAILS THAT GOT AN OPEN ANSWER", pos, 6, lambda e: f"- {_house_line(e)}\n  EMAIL: {cut(e['body'])}\n  AGENT ({e['reply_class']}): {cut(e['reply_text'], 350)}")
    block("EMAILS THAT GOT A NO", neg, 6, lambda e: f"- {_house_line(e)}\n  EMAIL: {cut(e['body'])}\n  AGENT ({e['reply_class']}): {cut(e['reply_text'], 250)}")
    block("EMAILS NOBODY ANSWERED", silent, 6, lambda e: f"- {_house_line(e)}\n  EMAIL: {cut(e['body'])}")
    replies = [dict(r) for r in conn.execute("SELECT decision, feedback, draft_body, final_body FROM card_decisions WHERE kind='reply' AND decided_at>=? "
                                             "AND decision IN (?,?,?) ORDER BY id DESC LIMIT 8", (since, APPROVED_AS_IS, APPROVED_EDITED, SKIPPED))]
    if replies:
        L.append(f"\nCHARLES'S ANSWERS TO AGENTS, AND WHAT RODRIGO DID WITH THEM ({len(replies)}):")
        L += [f"- {r['decision']}" + (f" | his words: {cut(' / '.join(json.loads(r['feedback'] or '[]')), 300)}" if json.loads(r["feedback"] or "[]") else "")
              + f"\n  ANSWER: {cut(r['final_body'] or r['draft_body'], 400)}" for r in replies]
    offers = [dict(r) for r in conn.execute("SELECT street, city, price, list_price, internal_low, internal_high, status, note FROM offers ORDER BY id DESC LIMIT 12")]
    if offers:
        L.append("\nOFFERS RODRIGO SENT (his number / asking / the engine's internal range / what happened):")
        L += [f"- {o['street']}, {o['city']}: ${o['price']:,} / ${o['list_price'] or 0:,} / ${o['internal_low'] or 0:,}-${o['internal_high'] or 0:,} / {o['status']}"
              + (f" ({cut(o['note'], 120)})" if o.get("note") else "") for o in offers]
    lessons = [r[0] for r in conn.execute("SELECT text FROM lessons WHERE text NOT LIKE '(Rodrigo edited%' ORDER BY id DESC LIMIT 20")]
    if lessons:
        L.append("\nHIS OWN COMMENTS ALREADY IN THE WRITER'S PROMPT (do not repeat these as rules):")
        L += [f"- {cut(t, 220)}" for t in lessons]
    rules = active_rules(conn)
    L.append("\nPLAYBOOK NOW (rules you wrote on earlier days; retire by id when the evidence turned):")
    L += [f"- id {r['id']} [{r['applies_to']}] {r['rule']} (since {r['created_at'][:10]})" for r in rules] or ["- (empty)"]
    return "\n".join(L)


def review(conn, cfg: Any, llm, text: str, run_date: str) -> dict[str, Any]:
    """Two Claude calls: one reads the week and proposes; a second one checks every rule against the settled decisions.
    A rule the checker did not clear (or could not read) is not adopted."""
    out = {"summary": None, "added": [], "dropped": [], "retired": [], "proposals": [], "claude": False, "checked": False}
    if llm is None or not getattr(llm, "available", False):
        return out
    c = _c(cfg)
    res = llm.structured("learn_review", REVIEW_SYSTEM.format(non_negotiables=NON_NEGOTIABLES), text, REVIEW_SCHEMA, max_tokens=3000)
    if not isinstance(res, dict):
        return out
    out["claude"], out["summary"] = True, str(res.get("summary") or "").strip()[:900] or None
    out["proposals"] = [{"key": "claude:" + _norm(p.get("title"))[:80], "kind": "note", "payload": {}, "title": str(p.get("title", "")).strip()[:160],
                         "why": str(p.get("why", "")).strip()[:600], "change": str(p.get("change", "")).strip()[:400]}
                        for p in (res.get("proposals") or [])[:2] if str(p.get("title", "")).strip()]
    ids = {r["id"] for r in active_rules(conn)}
    for r in (res.get("retire") or []):
        if r.get("id") in ids:
            conn.execute("UPDATE playbook_rules SET retired_at=?, retired_why=? WHERE id=?", (iso(), str(r.get("why", ""))[:300], r["id"]))
            out["retired"].append(r["id"])
    have = {_norm(r["rule"]) for r in active_rules(conn)}
    cand = []
    for r in (res.get("writer_rules") or [])[:int(c.get("max_new_rules_per_day", 3))]:
        rule = str(r.get("rule", "")).strip()
        why = rule_problem(rule)
        if not why and int(r.get("evidence_count") or 0) < int(c.get("min_rule_evidence", 2)):
            why = "seen on fewer than two cards"
        if not why and _norm(rule) in have:
            why = "already in the playbook"
        if why:
            out["dropped"].append({"rule": rule, "why": why})
        else:
            cand.append({"rule": rule, "applies_to": r.get("applies_to") if r.get("applies_to") in ("first_email", "reply", "any") else "any",
                         "evidence": str(r.get("evidence", ""))[:400]})
    if not cand:
        return out
    chk = llm.structured("learn_check", CHECK_SYSTEM.format(non_negotiables=NON_NEGOTIABLES),
                         "RULES TO JUDGE:\n" + "\n".join(f"{i}. [{r['applies_to']}] {r['rule']}\n   evidence: {r['evidence']}" for i, r in enumerate(cand))
                         + "\n\nWHAT THE ANALYST SAW:\n" + text[:12000], CHECK_SCHEMA, max_tokens=1500)
    verdicts = {v.get("index"): v for v in (chk or {}).get("verdicts") or []} if isinstance(chk, dict) else None
    if verdicts is None:
        out["dropped"] += [{"rule": r["rule"], "why": "the second reader could not be reached; not adopted"} for r in cand]
        return out
    out["checked"] = True
    for i, r in enumerate(cand):
        v = verdicts.get(i)
        if not (v and v.get("keep") is True):
            out["dropped"].append({"rule": r["rule"], "why": "second reader: " + str((v or {}).get("reason") or "not cleared")[:200]})
            continue
        conn.execute("INSERT INTO playbook_rules(rule,applies_to,evidence,source_run,created_at) VALUES(?,?,?,?,?)",
                     (r["rule"], r["applies_to"], r["evidence"], run_date, iso()))
        out["added"].append(r["rule"])
    extra = len(active_rules(conn)) - int(c.get("max_rules", 12))
    for r in active_rules(conn)[:max(0, extra)]:                                       # the playbook stays short: oldest out
        conn.execute("UPDATE playbook_rules SET retired_at=?, retired_why='made room for newer rules' WHERE id=?", (iso(), r["id"]))
        out["retired"].append(r["id"])
    return out


# ---------- the run ----------

def _previous(conn, run_date: str, days: int = 7) -> dict[str, Any] | None:
    r = conn.execute("SELECT metrics FROM learn_runs WHERE run_date<=date(?, ?) ORDER BY run_date DESC LIMIT 1", (run_date, f"-{days} day")).fetchone() \
        or conn.execute("SELECT metrics FROM learn_runs WHERE run_date<? ORDER BY run_date ASC LIMIT 1", (run_date,)).fetchone()
    try:
        return json.loads(r[0]) if r else None
    except ValueError:
        return None


def _moves(old: dict[str, float], new: dict[str, float], n: int = 4) -> dict[str, list[str]]:
    d = sorted(((new.get(k, 0.0) - old.get(k, 0.0), k) for k in set(old) | set(new)), reverse=True)
    words = lambda k: f"{DIM_WORDS.get(k.partition('=')[0], k.partition('=')[0])} = {k.partition('=')[2]}"  # noqa: E731
    return {"up": [words(k) for x, k in d[:n] if x >= 0.02], "down": [words(k) for x, k in d[::-1][:n] if x <= -0.02]}


def run(conn, cfg: Any, llm=None, asana=None, run_date: str | None = None, use_claude: bool = True, force: bool = False, now=None) -> dict[str, Any]:
    """One day of the loop. Once per day (a second call the same day returns the stored result unless `force`)."""
    db.init_db(conn)
    c = _c(cfg)
    run_date = run_date or today_et()
    if not c.get("enabled", True):
        return {"skipped": "learning_loop.enabled is off"}
    if not force and conn.execute("SELECT 1 FROM learn_runs WHERE run_date=?", (run_date,)).fetchone():
        return {"skipped": "already ran today"}
    ev = evidence(conn, now=now, mature_days=int(c.get("mature_days", 3)))
    stats = segment_stats(ev)
    m = metrics(conn, ev, run_date, int(c.get("window", 30)))
    prefs = load_prefs(cfg)
    old_w = dict(prefs.get("weights") or {})
    applied = apply_approved(conn, cfg, asana, prefs)
    prefs["weights"] = compute_weights(stats, cfg, old_w)
    prefs["updated"] = run_date
    save_prefs(cfg, prefs)
    moves = _moves(old_w, prefs["weights"])
    proposals = stat_proposals(stats, m, cfg, prefs)
    rv = {"summary": None, "added": [], "dropped": [], "retired": [], "proposals": [], "claude": False, "checked": False}
    enough = m["decided"] >= int(c.get("min_decisions_for_review", 10)) or m["sent"] >= int(c.get("min_decisions_for_review", 10))
    if use_claude and c.get("claude_review", True) and enough:
        try:
            rv = review(conn, cfg, llm, digest(conn, ev, stats, m, _previous(conn, run_date), int(c.get("review_days", 7)), now), run_date)
        except Exception as e:  # noqa: BLE001 — the review improves the engine; it must never stop the run
            log.exception("learn loop: review failed: %s", e)
            rv["error"] = str(e)[:200]
    rules = write_playbook(conn, cfg)
    posted = post_proposals(conn, cfg, asana, proposals + rv["proposals"])
    out = {"run_date": run_date, "emails_studied": len(ev), "metrics": m, "weights": len(prefs["weights"]), "moves": moves,
           "blocked": prefs.get("blocked") or [], "applied": applied, "summary": rv["summary"], "rules_added": rv["added"],
           "rules_dropped": rv["dropped"], "rules_retired": rv["retired"], "rules_active": rules, "proposals_posted": posted,
           "claude": rv["claude"], "checked": rv["checked"], "enough_data": enough}
    conn.execute("INSERT INTO learn_runs(run_date,metrics,changes,summary,created_at) VALUES(?,?,?,?,?) ON CONFLICT(run_date) DO UPDATE SET "
                 "metrics=excluded.metrics, changes=excluded.changes, summary=excluded.summary, created_at=excluded.created_at",
                 (run_date, json.dumps(m), json.dumps({k: out[k] for k in ("moves", "applied", "rules_added", "rules_dropped", "rules_retired",
                                                                         "rules_active", "proposals_posted", "claude", "checked", "enough_data",
                                                                         "emails_studied", "blocked")}), rv["summary"], iso()))
    try:
        d = cfg.path("reports") / "learning"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"loop_{run_date}.json").write_text(json.dumps({**out, "segments": {f"{k[0]}={k[1]}": v for k, v in stats["segments"].items()},
                                                             "base": stats["base"], "weights_now": prefs["weights"]}, indent=2, default=str), encoding="utf-8")
    except OSError as e:
        log.warning("learn loop: log file: %s", e)
    return out


def _trend(now: float | None, before: float | None, good_up: bool = True) -> str:
    if now is None:
        return "n/a"
    if before is None or abs(now - before) < 0.5:
        return f"{now:.0f}%"
    better = (now > before) == good_up
    return f"{now:.0f}% ({'better' if better else 'worse'} than a week ago: {before:.0f}%)"


def summary_lines(conn, cfg: Any, run_date: str | None = None) -> list[str]:
    """Plain lines for the morning report ([] when the loop has never run)."""
    r = conn.execute("SELECT * FROM learn_runs ORDER BY run_date DESC LIMIT 1").fetchone()
    if not r:
        return []
    m, ch = json.loads(r["metrics"] or "{}"), json.loads(r["changes"] or "{}")
    prev = _previous(conn, r["run_date"]) or {}
    y = m.get("yesterday") or {}
    out = [f"Yesterday ({y.get('date')}): you approved {y.get('as_written', 0)} as written, changed {y.get('changed', 0)}, skipped "
           f"{y.get('skipped', 0)}; {y.get('sent', 0)} first emails out, {y.get('replies', 0)} answers, {y.get('offers', 0)} offers.",
           f"Scorecard: approved as written {_trend(m.get('as_written_pct'), prev.get('as_written_pct'))} of your last {m.get('decided', 0)} "
           f"decisions · skipped {_trend(m.get('skip_pct'), prev.get('skip_pct'), good_up=False)} · agents answering "
           f"{_trend(m.get('reply_pct'), prev.get('reply_pct'))} of {m.get('sent', 0)} emails."]
    if m.get("offers"):
        out.append(f"Offers: {m['offers']} logged, typically {m.get('offer_pct_of_list')}% of asking"
                   + (f" and {m['offer_vs_engine_high_pct']}% of the engine's internal high." if m.get("offer_vs_engine_high_pct") else "."))
    if r["summary"]:
        out.append(f"What it sees: {r['summary']}")
    elif not ch.get("enough_data"):
        out.append("Not enough decisions yet for the written review (it starts at 10); the numbers above are still tracked every day.")
    elif not ch.get("claude"):
        out.append("The written review did not run (Claude was not reachable); it tries again tomorrow.")
    for rule in ch.get("rules_added") or []:
        out.append(f"New rule for the writer: {rule}")
    if ch.get("rules_retired"):
        out.append(f"Rules retired: {len(ch['rules_retired'])}. Rules in the playbook now: {ch.get('rules_active', 0)}.")
    mv = ch.get("moves") or {}
    if mv.get("up"):
        out.append("Moved up the outreach order: " + "; ".join(mv["up"]) + ".")
    if mv.get("down"):
        out.append("Moved down the outreach order: " + "; ".join(mv["down"]) + ".")
    for seg in ch.get("applied") or []:
        out.append(f"Applied on your OK: no more first emails for {seg.replace('=', ' = ')}.")
    for t in ch.get("proposals_posted") or []:
        out.append(f"Waiting on you (card in Needs Rodrigo): {t}")
    return out
