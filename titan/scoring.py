"""MOTIVATION SCORING: 0–100 with cited phrases. Claude reads remarks + structured fields; rules fallback is deterministic."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from . import db
from .llm import LLM
from .util import log, today_et

# signal → regex over remarks. Weights come from config.scoring.signals.
SIGNAL_PATTERNS: dict[str, str] = {
    "cash_only": r"\bcash[\s-]*(only|offers?|buyers?( only)?|deals?)\b|\bno financing\b|\bwill not qualify for (conventional )?financing\b|\bcash or hard money\b",
    "as_is": r"\bas[\s-]is\b|\bsold as is\b|\bno repairs\b|\bseller will (not|n't) make (any )?repairs\b",
    "needs_work": r"\bneeds? (some |a little |lots of |a lot of )?(tlc|work|updating|repairs?|renovation|love|attention)\b|\bhandyman\b|\bfixer[\s-]?upper\b|\bfixer\b|\bcontractor'?s? special\b|\bbring your (tools|contractor|vision)\b|\bdiamond in the rough\b|\brehab\b|\bdeferred maintenance\b|\bsweat equity\b|\bgut\b",
    "motivated_seller": r"\bmotivated\b|\bmust sell\b|\bseller (is )?(very )?(eager|anxious|ready)\b|\bpriced to sell\b|\bpriced (for|to) (a )?quick sale\b|\bquick (sale|close)\b|\bneed(s)? to sell\b|\bwon'?t last\b|\bseller says sell\b",
    "bring_all_offers": r"\bbring (all |your |us an? )?offers?\b|\ball (reasonable )?offers? (will be )?(considered|welcome|reviewed|entertained)\b|\bsubmit (all |your )?offers?\b|\bmake (an|us an|your) offer\b|\bopen to (all )?offers\b|\bseller will (consider|review|entertain) all offers\b",
    "price_reduced": r"\bprice (reduced|reduction|drop|improvement|adjustment|cut)\b|\breduced\b|\bnew (lower )?price\b|\bhuge reduction\b|\bbelow (market|appraisal|appraised)\b",
    "estate_probate": r"\bestate( sale)?\b|\bprobate\b|\binherited\b|\bheirs?\b|\btrust(ee)? sale\b|\bpersonal representative\b|\bdeceased\b|\bsettling (an |the )?estate\b|\bexecutor\b",
    "life_event": r"\brelocat(ing|ion|ed)\b|\bdivorce\b|\bjob transfer\b|\btransferred\b|\bmoving out of (state|town|the country)\b|\bdownsizing\b|\bhealth (reasons|issues)\b|\bretiring\b|\bowner moved\b",
    "tenant_occupied": r"\btenant[\s-]occupied\b|\bdo not disturb (the )?tenants?\b|\btenants? in place\b|\bmonth[\s-]to[\s-]month\b|\blease (in place|ends|expires)\b|\brented\b",
    "investor_special": r"\binvestor'?s?( special| alert| dream| opportunity)\b|\bgreat (for|rental) investors?\b|\bperfect for (an )?investors?\b|\bflip\b|\bfix (and|&|n) flip\b|\bvalue[\s-]add\b|\bbrrr\b|\binvestment (opportunity|property)\b",
    "distress_status": r"\bshort[\s-]?sale\b|\bpre[\s-]?foreclosure\b|\bforeclos\w*\b|\breo\b|\bbank[\s-]owned\b|\bauction\b|\blis pendens\b|\bthird[\s-]party approval\b|\blender approval\b",
    "seller_financing": r"\bseller financ\w*\b|\bowner financ\w*\b|\bowner will carry\b|\bsubject[\s-]to\b|\bwrap\b|\bcreative financing\b",
    "code_violations": r"\bcode (violations?|enforcement|liens?)\b|\bopen permits?\b|\bexpired permits?\b|\bunpermitted\b|\bliens?\b|\bno (final )?co\b",
    "damage": r"\bfire damage\b|\bwater damage\b|\bflood(ed| damage)\b|\bmold\b|\broof (leak|damage|needs)\b|\bhurricane damage\b|\bstorm damage\b|\bfoundation (issue|problem|crack)\b|\btermite\b|\bcollapsed\b|\bdamaged?\b",
    "vacant": r"\bvacant\b|\bunoccupied\b|\bno tenants?\b|\bimmediate occupancy\b",
}
NEGATIVE_PATTERNS: dict[str, str] = {
    "fully_renovated": r"\b(fully|completely|totally|newly|beautifully|recently|tastefully) (renovated|remodeled|updated|upgraded)\b|\bbrand new (kitchen|roof|everything)\b|\bno expense spared\b|\bnew construction\b",
    "turnkey": r"\bturn[\s-]?key\b|\bmove[\s-]in ready\b|\bimmaculate\b|\bpristine\b|\bshowcase\b|\bmodel home\b|\bluxur(y|ious)\b",
    "multiple_offers": r"\bmultiple offers?\b|\bmultiple[\s-]offer situation\b|\boffers? (in hand|received)\b",
    "highest_and_best": r"\bhighest (and|&) best\b|\bbest (and|&) final\b|\bbidding war\b",
}
STRUCTURED = ("dom_over_60", "two_plus_cuts", "back_on_market", "expired_relisted")

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},   # 0-100 is enforced after parsing: structured outputs reject minimum/maximum (400)
        "cited": {"type": "array", "items": {"type": "object", "properties": {
            "signal": {"type": "string"}, "phrase": {"type": "string"}}, "required": ["signal", "phrase"], "additionalProperties": False}},
        "negatives": {"type": "array", "items": {"type": "object", "properties": {
            "signal": {"type": "string"}, "phrase": {"type": "string"}}, "required": ["signal", "phrase"], "additionalProperties": False}},
        "summary": {"type": "string"},
    },
    "required": ["score", "cited", "negatives", "summary"],
    "additionalProperties": False,
}

SYSTEM = """You score seller motivation for a South Florida cash buyer. Read the listing remarks and structured fields and return JSON.

Scoring: start at 0. Add the weight for each positive signal present (once each), add the structured weights, add the (negative) weight for each negative signal, then clamp to 0–100. Weights:
{weights}

Rules:
- Cite the exact phrase from the remarks for every signal you count (copy it verbatim, max 12 words). Structured signals cite the field, e.g. "days_on_market=84".
- Agent puffery ("won't last!") is not motivation unless paired with a concrete signal.
- "As-is" alone on a renovated property is weak; "as-is" with "needs work" or "cash" is strong.
- Multiple offers / highest and best means the seller has leverage: apply the negative.
- summary: one plain sentence an investor can read in 2 seconds."""


def remarks_hash(l: dict[str, Any]) -> str:
    key = f"{l.get('remarks','')}|{l.get('days_on_market')}|{l.get('price_cuts')}|{l.get('list_price')}|{l.get('status_flags')}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _snippet(text: str, m: re.Match) -> str:
    """The matched phrase, extended to whole words, as it appears in the remarks (max 8 words)."""
    s, e = m.start(), m.end()
    while s > 0 and (text[s - 1].isalnum() or text[s - 1] in "-'"):
        s -= 1
    while e < len(text) and (text[e].isalnum() or text[e] in "-'"):
        e += 1
    words = re.sub(r"\s+", " ", text[s:e]).strip().split(" ")
    return " ".join(words[:8]).strip(" ,.;:!")


def structured_signals(p: dict[str, Any]) -> list[tuple[str, str]]:
    out = []
    if (p.get("days_on_market") or 0) > 60:
        out.append(("dom_over_60", f"days_on_market={p['days_on_market']}"))
    if (p.get("price_cuts") or 0) >= 2:
        out.append(("two_plus_cuts", f"price_cuts={p['price_cuts']}"))
    if p.get("back_on_market"):
        out.append(("back_on_market", "price_history: back on market"))
    flags = p.get("status_flags") or []
    if isinstance(flags, str):
        flags = json.loads(flags or "[]")
    if any(f in flags for f in ("short_sale", "reo", "auction", "pre_foreclosure")):
        out.append(("distress_status", f"status_flags={','.join(flags)}"))
    if any(f in flags for f in ("probate", "estate")):
        out.append(("estate_probate", f"status_flags={','.join(flags)}"))
    if p.get("vacant"):
        out.append(("vacant", "vacant=1"))
    return out


def score_rules(p: dict[str, Any], weights: dict[str, int], negatives: dict[str, int]) -> dict[str, Any]:
    text = p.get("remarks") or ""
    cited, neg, seen = [], [], set()
    total = 0
    for sig, pat in SIGNAL_PATTERNS.items():
        m = re.search(pat, text, re.I)
        if m and sig not in seen:
            seen.add(sig)
            total += weights.get(sig, 0)
            cited.append({"signal": sig, "phrase": _snippet(text, m)})
    for sig, phrase in structured_signals(p):
        if sig not in seen:
            seen.add(sig)
            total += weights.get(sig, 0)
            cited.append({"signal": sig, "phrase": phrase})
    for sig, pat in NEGATIVE_PATTERNS.items():
        m = re.search(pat, text, re.I)
        if m:
            total += negatives.get(sig, 0)
            neg.append({"signal": sig, "phrase": _snippet(text, m)})
    # "as-is" on an otherwise clean listing is weak
    if "as_is" in seen and not ({"needs_work", "cash_only", "damage", "estate_probate", "distress_status"} & seen):
        total -= weights.get("as_is", 0) // 2
    score = max(0, min(100, total))
    top = sorted(cited, key=lambda c: -weights.get(c["signal"], 0))[:3]
    summary = ("Signals: " + ", ".join(c["signal"].replace("_", " ") for c in top)) if top else "No motivation signals in remarks."
    if neg:
        summary += "; negatives: " + ", ".join(n["signal"].replace("_", " ") for n in neg)
    return {"score": score, "cited": cited, "negatives": neg, "summary": summary, "method": "rules"}


def score_llm(llm: LLM, p: dict[str, Any], weights: dict[str, int], negatives: dict[str, int]) -> dict[str, Any] | None:
    w = "\n".join(f"- {k}: +{v}" for k, v in weights.items()) + "\n" + "\n".join(f"- {k}: {v}" for k, v in negatives.items())
    flags = p.get("status_flags")
    user = json.dumps({
        "remarks": (p.get("remarks") or "")[:6000],
        "list_price": p.get("list_price"), "original_list_price": p.get("original_list_price"),
        "price_cuts": p.get("price_cuts"), "days_on_market": p.get("days_on_market"), "back_on_market": bool(p.get("back_on_market")),
        "vacant": bool(p.get("vacant")), "status_flags": flags, "property_type": p.get("property_type"),
        "year_built": p.get("year_built"), "photos_count": p.get("photos_count"),
    }, ensure_ascii=False)
    out = llm.structured("score", SYSTEM.format(weights=w), user, SCORE_SCHEMA, max_tokens=int(llm.cfg.get("scoring.max_tokens", 1024)))
    if not out:
        return None
    out["score"] = max(0, min(100, int(out.get("score", 0))))
    out["method"] = "claude"
    return out


def score_property(llm: LLM | None, p: dict[str, Any], cfg: Any) -> dict[str, Any]:
    weights = cfg.get("scoring.signals", {})
    negatives = cfg.get("scoring.negatives", {})
    res = None
    if llm is not None and llm.available:
        res = score_llm(llm, p, weights, negatives)
        if res is not None and res["score"] >= 40 and not res["cited"]:
            res = None  # a score with no evidence is not acceptable; fall back
    if res is None:
        res = score_rules(p, weights, negatives)
    return res


_ITEM = {k: v for k, v in SCORE_SCHEMA["properties"].items()}
BATCH_SCHEMA = {"type": "object", "properties": {"results": {"type": "array", "items": {
    "type": "object", "properties": {"id": {"type": "integer"}, **_ITEM}, "required": ["id", *SCORE_SCHEMA["required"]],
    "additionalProperties": False}}}, "required": ["results"], "additionalProperties": False}


def _llm_input(p: dict[str, Any]) -> dict[str, Any]:
    return {"remarks": (p.get("remarks") or "")[:6000], "list_price": p.get("list_price"), "original_list_price": p.get("original_list_price"),
            "price_cuts": p.get("price_cuts"), "days_on_market": p.get("days_on_market"), "back_on_market": bool(p.get("back_on_market")),
            "vacant": bool(p.get("vacant")), "status_flags": p.get("status_flags"), "property_type": p.get("property_type"),
            "year_built": p.get("year_built"), "photos_count": p.get("photos_count")}


def score_llm_batch(llm: LLM, props: list[dict[str, Any]], weights: dict[str, int], negatives: dict[str, int]) -> dict[int, dict[str, Any]]:
    """Score many listings in one call (Claude Code plan: fewer calls = less plan usage). Returns {property id: result}."""
    w = "\n".join(f"- {k}: +{v}" for k, v in weights.items()) + "\n" + "\n".join(f"- {k}: {v}" for k, v in negatives.items())
    system = SYSTEM.format(weights=w) + "\n\nYou receive a JSON list of listings, each with an id. Score every one independently and return one result per id."
    user = json.dumps([{"id": p["id"], **_llm_input(p)} for p in props], ensure_ascii=False)
    out = llm.structured("score_batch", system, user, BATCH_SCHEMA, max_tokens=int(llm.cfg.get("scoring.max_tokens", 1024)) * len(props))
    res: dict[int, dict[str, Any]] = {}
    ids = {p["id"] for p in props}
    for r in (out or {}).get("results", []) if isinstance(out, dict) else []:
        try:
            pid = int(r.get("id"))
        except (TypeError, ValueError):
            continue
        if pid not in ids:
            continue
        r = {k: r.get(k) for k in ("score", "cited", "negatives", "summary")}
        r["score"] = max(0, min(100, int(r.get("score") or 0)))
        r["cited"], r["negatives"] = r.get("cited") or [], r.get("negatives") or []
        r["summary"], r["method"] = r.get("summary") or "", "claude"
        if r["score"] >= 40 and not r["cited"]:
            continue                      # a score with no evidence is not acceptable; rules take it
        res[pid] = r
    return res


def run_scoring(conn, cfg: Any, llm: LLM | None = None, limit: int | None = None, force: bool = False) -> dict[str, Any]:
    """Score every property whose remarks/structured inputs changed since the last score."""
    run_id = db.start_run(conn, "score", today_et())
    from .ingest.normalize import exclude_flagged
    excluded = exclude_flagged(conn, cfg)
    rows = conn.execute("SELECT p.*, s.remarks_hash AS prev_hash, s.method AS prev_method FROM properties p LEFT JOIN scores s ON s.property_id=p.id "
                        "WHERE p.lead_status NOT IN ('CLOSED','DNC','EXCLUDED') ORDER BY p.last_seen DESC").fetchall()
    from .targeting import claude_priority
    n, skipped, methods = 0, 0, {"rules": 0, "claude": 0}
    warning = None
    cap = int(cfg.get("scoring.max_claude_calls_per_run", 1500))
    claude_used = 0
    items = [db.row_to_dict(r) for r in rows]
    for p in items:
        p["_prio"] = claude_priority(p, cfg)
    items.sort(key=lambda p: (99 if p["_prio"] is None else p["_prio"]))     # target lanes get Claude first
    prefetched: dict[int, dict[str, Any]] = {}
    if llm and llm.available and llm.provider == "claude_code":
        # Claude Code plan: score the target listings in batches up front, then fall through the normal loop
        todo = [p for p in items if p["_prio"] is not None and (force or p.get("prev_hash") != remarks_hash(p) or p.get("prev_method") == "rules")][:cap]
        if limit:
            todo = todo[:limit]
        size = max(1, int(cfg.get("scoring.claude_code_batch_size", 20)))
        w, neg = cfg.get("scoring.signals", {}), cfg.get("scoring.negatives", {})
        for i in range(0, len(todo), size):
            if not llm.available:
                break                       # plan limit hit or switched off after failures
            prefetched.update(score_llm_batch(llm, todo[i:i + size], w, neg))
            if llm.session_failures >= 5 and llm.session_successes == 0:
                warning = (f"Claude Code scoring failed {llm.session_failures} times in a row and was switched off for this run; "
                           f"the rest are scored by the keyword rules. Last error: {llm.last_error}")
                log.error(warning)
                llm.provider = "rules"
        if getattr(llm, "limit_hit", None):
            warning = f"Claude Code plan limit reached; the rest are scored by rules and get Claude on the next run. ({llm.limit_hit})"
    for p in items:
        h = remarks_hash(p)
        want_claude = bool(llm and llm.available and p["_prio"] is not None and claude_used < cap)
        # unchanged listings are skipped, except rules scores of target listings once Claude can take them
        if not force and p.get("prev_hash") == h and not (want_claude and p.get("prev_method") == "rules"):
            skipped += 1
            continue
        if p["id"] in prefetched:
            res = prefetched[p["id"]]
        elif llm and (llm.provider == "claude_code" or getattr(llm, "limit_hit", None)):
            res = score_property(None, p, cfg)          # batched already (or limit hit): rules for the rest
        else:
            res = score_property(llm if want_claude else None, p, cfg)
        if want_claude or p["id"] in prefetched:
            claude_used += 1
        if llm and llm.available and llm.session_failures >= 5 and llm.session_successes == 0:
            warning = (f"Claude scoring failed {llm.session_failures} times in a row and was switched off for this run; "
                       f"the rest are scored by the keyword rules. Last error: {llm.last_error}")
            log.error(warning)
            llm.provider = "rules"
        conn.execute("INSERT INTO scores(property_id,score,cited,signals,negatives,summary,method,model,remarks_hash,scored_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(property_id) DO UPDATE SET score=excluded.score, cited=excluded.cited, "
                     "signals=excluded.signals, negatives=excluded.negatives, summary=excluded.summary, method=excluded.method, "
                     "model=excluded.model, remarks_hash=excluded.remarks_hash, scored_at=excluded.scored_at",
                     (p["id"], res["score"], db.j(res["cited"]), db.j([c["signal"] for c in res["cited"]]), db.j(res["negatives"]),
                      res["summary"], res["method"], (f"claude_code:{cfg.get('scoring.claude_code_model', 'sonnet')}" if p["id"] in prefetched
                                                       else llm.model) if (llm and res["method"] == "claude") else None, h, db.iso()))
        methods[res["method"]] += 1
        n += 1
        if limit and n >= limit:
            break
    counts = {"scored": n, "skipped_unchanged": skipped, **methods, "llm_cost_usd": round(llm.session_cost, 4) if llm else 0,
              "claude_failed": llm.session_failures if llm else 0, "excluded_reo_short_sale": excluded,
              "claude_cap_reached": bool(llm and llm.available and claude_used >= cap)}
    if warning:
        counts["warning"] = warning
    db.finish_run(conn, run_id, "ok", counts)
    log.info("scoring: %s", counts)
    return counts
