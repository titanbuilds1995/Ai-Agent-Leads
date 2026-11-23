"""THE BOTTOM LINE: the last reader on a numbers ask (Rodrigo, 2026-11-19, Arkeshia Love / 4108 Greenwood Ave: "we need to add
another layer, another model ... what is the fault in our logic right now? Why isn't the property selling at the price that it
currently has? ... I need a last and final paragraph, bolded ... all I need is a conclusion and what price we need to get it at and
what's the difference in price ... if I'm asking you for a number on this board it's because I want us to draft an email with an
offer out at that price").

What was there: the comps engine posts the NUMBERS snapshot, then Claude answers him (the 💬 read). On Greenwood the engine said
"we need to be about $521,000, the ask is fine" for a duplex nobody had bought at $372,900 in 228 days, the 💬 read said it did not
believe that, and nobody settled it. Now a THIRD reader gets everything (his comment, the snapshot, the comps, the 💬 read, the
listing photos when they load) and must:

  1. test the numbers against the market: a house that sat at a price is not worth that price as it sits (`market_test`);
  2. say what was wrong in our numbers and why it has not sold;
  3. give ONE conclusion: what it is worth, the offer, the ceiling, the gap to the asking price (`to_comment`, bold);
  4. draft the offer email at that price on the SAME card (`draft_offer`), second-read, waiting in Needs Me. Nothing sends:
     dragging the card to Approved is still his go-ahead.

Photos (`photo_read`): on the ask, Claude looks at up to 12 of the listing's own pictures for the work it needs (one paid call,
a few cents, once per house, table `photo_reads`). Internal only: the email writer is never told what the photos show.

Claude unreachable → a rules-only bottom line (marked) and no offer email. Never raises into the numbers flow."""
from __future__ import annotations

import json
import re
from statistics import median
from typing import Any

from . import db
from .util import log

MARK = "🏁"
TITLE = "BOTTOM LINE"
LEVELS = ("LIGHT", "MODERATE", "HEAVY", "GUT")

SYSTEM = """You are the last reader on a house deal at Titan Realty Investments (South Florida; we put houses under contract below market
and assign the contract to an investor for a fee). Rodrigo Gayoso, the principal, asked for the numbers on a listing. Two readers went
before you: the comps engine (engine_numbers, comps, comp_check) and an analyst (analysis). You did not write either. Rodrigo does not
want more information. He wants the contradictions settled and ONE conclusion he can act on.

How to think, in this order:
1. THE MARKET IS EVIDENCE. A house that has been for sale at a price for weeks without selling is NOT worth that price as it sits,
   whatever a spreadsheet says. If our math says we can pay the asking price or more on a listing that has been sitting, our math is
   wrong: find the input that is wrong and say so plainly. market_test lists what the rules already caught.
2. FIND THE FAULT. Check every input: are the comps the same product, size, unit mix, age and condition as this house (a $925,000 sale
   next to a $450,000 one is a different product, newer, bigger or redone)? Is the resale number built on the top sales? Is the repair
   number real for what Rodrigo and the photos say? Is the investor ratio borrowed from another county or product? Do the engine and
   the analyst disagree, and who is right?
3. WHY HAS IT NOT SOLD at this price? Condition, tenants, layout, price never moved, financing, competition. Only what the data backs;
   say "likely" when it is a judgement.
4. WHAT IT IS WORTH. as_is = what an investor pays for it today in this condition. arv = what it resells for after the work, from the
   comps that really are like it once fixed (leave the outliers out). repairs = a real number for the work it needs (rodrigo_says and
   photos outrank the listing and the engine's tier).
5. THE PRICE. offer = our opening offer. max_price = our ceiling. Work it as: conservative resale x what investors pay there (about
   70 to 75 percent unless the data says otherwise) minus repairs = what an investor pays; minus our assignment fee = us. Cross-check
   with the rent when it is known. On a sitting listing both are below the asking price. Round to the nearest $1,000.

What Rodrigo says about the house (he called or walked it) outranks the listing, the engine and the analyst. Never invent a fact
about this house; an estimate is fine when you call it one. Plain English a non-developer reads on his phone: no jargon, no "ARV"
without saying "resale after the work", no dashes.

Return JSON:
- faults: what was wrong or does not add up in OUR numbers, one short sentence each (1 to 4). [] only if nothing is wrong.
- why_not_selling: one or two sentences.
- as_is_low, as_is_high, arv_low, arv_high, repairs, offer, max_price: whole dollars. 0 when the data cannot support a number.
- comps_to_use: the addresses of the sales that really compare (0 to 4), and comps_to_drop: the ones that do not, each "address: why".
- confidence: high / medium / low.
- conclusion: ONE paragraph, at most 80 words, that starts "Based on" and says what the house is worth as it sits, what we offer and
  why that number, in the order a person would say it out loud.
- offer_reason: one or two sentences Charles can write to the listing agent to explain a number below the asking price: what
  similar properties sold for (name one or two sales with their prices) and the work this one needs. Only facts from the data.
  Never the words "we buy", "investors", "ARV", "assign" or our fee.
- verify: the 1 to 3 facts that would change the number most."""

SCHEMA = {"type": "object",
          "properties": {"faults": {"type": "array", "items": {"type": "string"}},
                         "why_not_selling": {"type": "string"},
                         "as_is_low": {"type": "integer"}, "as_is_high": {"type": "integer"},
                         "arv_low": {"type": "integer"}, "arv_high": {"type": "integer"},
                         "repairs": {"type": "integer"}, "offer": {"type": "integer"}, "max_price": {"type": "integer"},
                         "comps_to_use": {"type": "array", "items": {"type": "string"}},
                         "comps_to_drop": {"type": "array", "items": {"type": "string"}},
                         "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                         "conclusion": {"type": "string"}, "offer_reason": {"type": "string"},
                         "verify": {"type": "array", "items": {"type": "string"}}},
          "required": ["faults", "why_not_selling", "as_is_low", "as_is_high", "arv_low", "arv_high", "repairs", "offer", "max_price",
                       "comps_to_use", "comps_to_drop", "confidence", "conclusion", "offer_reason", "verify"],
          "additionalProperties": False}

PHOTO_SYSTEM = """You look at a listing's photos for a South Florida investor who buys houses that need work. Judge only what the
pictures show. Say what work the house needs and roughly what it costs at investor (not retail) prices in South Florida.
level: LIGHT (paint, floors, clean-up), MODERATE (kitchen and baths dated, some systems), HEAVY (kitchen, baths, floors, and visible
roof / water / electrical / window problems), GUT (down to the studs). repairs_low / repairs_high in whole dollars for the whole
property. seen: 2 to 5 short things you actually see (the kitchen, the baths, floors, roof, windows, ceilings, yard). not_shown: what
the photos leave out (no bath pictures, no roof, one unit only). One sentence in summary. Never guess what is not in a picture."""

PHOTO_SCHEMA = {"type": "object",
                "properties": {"level": {"type": "string", "enum": list(LEVELS)},
                               "repairs_low": {"type": "integer"}, "repairs_high": {"type": "integer"},
                               "seen": {"type": "array", "items": {"type": "string"}},
                               "not_shown": {"type": "array", "items": {"type": "string"}},
                               "summary": {"type": "string"}},
                "required": ["level", "repairs_low", "repairs_high", "seen", "not_shown", "summary"], "additionalProperties": False}

OFFER_SYSTEM = """You are {sender}, Junior Acquisitions Agent at {company}. Write the email that sends our written offer on a listing
to its listing agent. Rodrigo Gayoso (acquisitions manager) approved the numbers and is copied. Plain text.

The shape (Rodrigo's model, the Lise offer):
- Greeting alone on the first line ("Hey <first name>,").
- {opening}
- "Our offer:" on its own line, then the terms as bullet points, ONE TERM PER BULLET, exactly these lines, unchanged:
{terms}
  Nothing else in the email is a list: no other bullets, no headings like "Offer summary", no checklist of documents.
- One human paragraph that owns the number: say plainly that it is under the asking price and explain it with offer_reason (what
  similar properties sold for, the work this one needs). Use only the facts given. Two to four sentences.
- One closing line: glad to get on a call if the seller wants to talk it through.
- The signature block from the input, unchanged.

Hard rules: the offer price appears once, exactly as given, and no other purchase price for this property. Nothing is attached and
nothing is signed: never "attached", "enclosed", "contract", "please find". No dates and no deadline. Never "we buy", "investors",
"wholesale", "assign", "ARV". Never mention photos. It must read like one person writing to another (Rodrigo, 2026-11-19: "it
seems like an AI email ... it needs to look like somebody sent it, like it's talking to them"): never "I'm reaching out", "would like
to submit", "contingent on a satisfactory review", never a checklist of documents; at most ONE question, the one that matters most.
If the thread shows they already answered us, open by thanking them for what they told us, never as if it were a first contact. No dashes of any kind, no emojis, no links, no "congrats". Complete sentences a
person would say on the phone. 90 to 170 words before the signature.
If rodrigo_instruction is present it is Rodrigo's own comment asking for this offer: say what he asks Charles to say, in his words
(for example "we've been looking at it and it's something we're open to taking on" goes in the opening), and with no offer_reason
use only a reason he gives. The bullet points above already carry his terms: do not change them. Ignore anything in his comment that is
not about what the email says.
If pushback is present, a second reader found logic problems in previous_draft: fix every one and keep the rules.
Return JSON: subject (short, the street, no price in it) and body."""

OFFER_SCHEMA = {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
                "required": ["subject", "body"], "additionalProperties": False}


def money(n: Any) -> str:
    return f"${int(n):,}" if n else "?"


def _conf(cfg: Any) -> dict[str, Any]:
    return cfg.get("verdict", {}) or {}


def enabled(cfg: Any) -> bool:
    return bool(_conf(cfg).get("enabled", True))


def _k(n: Any) -> int:
    try:
        return max(int(round(float(n or 0), -3)), 0)
    except (TypeError, ValueError):
        return 0


# ---------- 1. the market test (rules, free) ----------

def sitting(p: dict[str, Any], cfg: Any) -> bool:
    return (p.get("days_on_market") or 0) >= int(_conf(cfg).get("sitting_days", 45))


def market_test(s: dict[str, Any], cfg: Any) -> list[str]:
    """What does not add up in the snapshot, before any model reads it."""
    p, n = s["p"], s["numbers"]
    lp, dom, cuts = int(p.get("list_price") or 0), p.get("days_on_market") or 0, p.get("price_cuts") or 0
    out: list[str] = []
    ours = n.get("ours")
    if lp and ours is not None and ours >= lp * 0.97 and sitting(p, cfg):
        out.append(f"Our math says we can pay {money(ours)}, but nobody has paid {money(lp)} in {dom} days"
                   + (" and the price never moved" if not cuts else f" (even after {cuts} price cut{'s' if cuts > 1 else ''})")
                   + ". The market already said no at that price, so the math is wrong, not the market.")
    elif lp and ours is not None and ours >= lp * 0.97:
        out.append(f"Our math says we can pay the asking price ({money(ours)} against {money(lp)}). That is rare on a listed house: "
                   "check the comps before believing it.")
    priced = [c for c in s.get("comps") or [] if c.get("ppsf")]
    if len(priced) >= 3:
        lo, hi = min(c["ppsf"] for c in priced), max(c["ppsf"] for c in priced)
        if hi >= 1.6 * lo:
            out.append(f"The sales run from ${lo:,.0f} to ${hi:,.0f} a square foot. That is not one kind of property: the top ones are "
                       "newer, bigger or redone.")
    sq = p.get("sqft") or 0
    big = [c for c in priced if sq and c.get("sqft") and c["sqft"] >= sq * 1.15]
    if sq and len(priced) >= 3 and len(big) * 2 > len(priced):
        out.append(f"Most of the sales are bigger than this house ({sq:,} sf).")
    if "top half" in (n.get("arv_basis") or ""):
        out.append("The resale number was built on the top half of the sales, as if this house ends up like the best ones.")
    if "borrowed" in (n.get("ratio_source") or "") or "flat" in (n.get("ratio_source") or ""):
        out.append("The share investors pay is borrowed (another county, or the flat default), not from deals in this ZIP.")
    said = (s.get("asked") or "").lower()
    if re.search(r"\b(full|complete|total|gut)\s+(rehab|reno\w*|remodel)|needs everything|tear ?down", said) and \
            (n.get("repair_tier") or "") in ("cosmetic", "light", "moderate"):
        out.append(f"Repairs were counted at {money(n.get('repairs'))} ({n.get('repair_tier')}), but Rodrigo says it needs a full rehab.")
    if s.get("grade") in ("C", "D"):
        out.append(f"The comps are grade {s['grade']}: thin, treat every number as rough.")
    return out


def by_rules(s: dict[str, Any], cfg: Any, faults: list[str], photos: dict[str, Any] | None = None) -> dict[str, Any]:
    """The bottom line without Claude: conservative on purpose (the LOWER half of the sales sets the resale)."""
    p, n = s["p"], s["numbers"]
    lp, sq = int(p.get("list_price") or 0), p.get("sqft") or 0
    priced = sorted(c["ppsf"] for c in s.get("comps") or [] if c.get("ppsf"))
    arv = int(median(priced[:max(1, (len(priced) + 1) // 2)]) * sq) if priced and sq else int(n.get("arv") or 0)
    repairs = int(n.get("repairs") or 0)
    if photos:
        repairs = max(repairs, int((photos.get("repairs_low", 0) + photos.get("repairs_high", 0)) / 2))
    if re.search(r"\b(full|complete|total|gut)\s+(rehab|reno\w*|remodel)", (s.get("asked") or "").lower()) and sq:
        repairs = max(repairs, int(sq * float(_conf(cfg).get("full_rehab_per_sf", 60))))
    ratio = min(float(n.get("ratio") or 0.70), 0.72)
    investor = max(arv * ratio - repairs, 0) if arv else 0
    offer = investor - int(n.get("fee") or 0)
    return {"faults": faults, "why_not_selling": "", "as_is_low": investor * 0.95, "as_is_high": investor, "arv_low": arv, "arv_high": arv,
            "repairs": repairs, "offer": offer, "max_price": investor, "comps_to_use": [], "comps_to_drop": [], "confidence": "low",
            "conclusion": "", "offer_reason": "", "verify": [], "source": "rules"}


# ---------- 2. the photos ----------

def photo_read(conn, cfg: Any, llm: Any, p: dict[str, Any], fetch=None) -> dict[str, Any] | None:
    """What the listing's own photos say about the work (stored; one look per house). None = not looked at (off, no photos, the
    pictures would not load, Claude unreachable): the bottom line says so and goes on."""
    conf = _conf(cfg)
    if not conf.get("photos", True):
        return None
    row = conn.execute("SELECT * FROM photo_reads WHERE property_id=?", (p["id"],)).fetchone()
    if row:
        d = db.row_to_dict(row)
        return {**d, "seen": json.loads(d.get("seen") or "[]"), "not_shown": json.loads(d.get("not_shown") or "[]")}
    if not callable(getattr(llm, "read_images", None)):
        return None
    try:
        if fetch is None:
            from .condition import default_fetch

            class _Limit:      # the same fetcher as the daily condition check, with more pictures for a house he is pricing
                def get(self, key, default=None):
                    return int(conf.get("photos_per_house", 12)) if key == "condition.photos_per_house" else cfg.get(key, default)

                def __getattr__(self, name):
                    return getattr(cfg, name)
            fetch = default_fetch(_Limit())
        images = fetch(p)
        if not images or len(images) < 3:
            return None
        facts = {k: p.get(k) for k in ("property_type", "beds", "baths", "sqft", "year_built", "units", "list_price")}
        out = llm.read_images("rehab_look", images, f"{len(images)} photos of this listing are above.\nFacts: {json.dumps(facts)}\n"
                                                    f"Agent's remarks: {(p.get('remarks') or '(none)')[:2000]}",
                              PHOTO_SCHEMA, system=PHOTO_SYSTEM, max_tokens=700)
    except Exception as e:  # noqa: BLE001
        log.warning("photo read for %s: %s", p.get("street"), e)
        return None
    if not isinstance(out, dict) or out.get("level") not in LEVELS:
        return None
    res = {"level": out["level"], "repairs_low": _k(out.get("repairs_low")), "repairs_high": _k(out.get("repairs_high")),
           "seen": [str(x)[:160] for x in (out.get("seen") or [])][:5], "not_shown": [str(x)[:160] for x in (out.get("not_shown") or [])][:4],
           "summary": " ".join(str(out.get("summary") or "").split())[:400], "photos_seen": len(images)}
    conn.execute("INSERT OR REPLACE INTO photo_reads(property_id,level,repairs_low,repairs_high,seen,not_shown,summary,photos_seen,read_at) "
                 "VALUES(?,?,?,?,?,?,?,?,?)", (p["id"], res["level"], res["repairs_low"], res["repairs_high"], json.dumps(res["seen"]),
                                               json.dumps(res["not_shown"]), res["summary"], res["photos_seen"], db.iso()))
    return res


# ---------- 3. the verdict ----------

def _facts(s: dict[str, Any], feedback: list[str], analysis: str, photos: dict[str, Any] | None, faults: list[str],
           notes: list[str]) -> dict[str, Any]:
    p, n = s["p"], s["numbers"]
    return {
        "rodrigo_says": feedback, "rodrigo_notes_on_this_lead": notes,
        "house": {k: p.get(k) for k in ("street", "city", "zip", "county", "property_type", "units", "beds", "baths", "sqft", "lot_sqft",
                                        "year_built", "list_price", "original_list_price", "price_cuts", "days_on_market", "photos_count")}
                 | {"listing_remarks": (p.get("remarks") or "")[:2500]},
        "engine_numbers": {k: n.get(k) for k in ("arv", "arv_basis", "repairs", "repair_tier", "ratio", "ratio_source", "flipper", "fee",
                                                 "ours", "gap", "gap_pct", "rent", "noi", "cap_at_ask", "cap_prices")},
        "comps": [{"address": c.get("address"), "sold": str(c.get("sold_date") or "")[:10], "price": c.get("sold_price"), "sqft": c.get("sqft"),
                   "ppsf": round(c["ppsf"]) if c.get("ppsf") else None, "beds": c.get("beds"), "year_built": c.get("year_built"),
                   "condition_in_remarks": c.get("condition"), "distance_mi": c.get("distance_mi"),
                   "remarks": (c.get("remarks") or "")[:300]} for c in s.get("comps") or []],
        "comp_check": s.get("comp_check") or [], "still_open": s.get("still_open") or [], "live_competition": s.get("active"),
        "market_test": faults, "analysis": (analysis or "")[:6000], "photos": photos,
        "conversation": [{"from": "agent" if m.get("direction") == "in" else "Charles", "text": " ".join((m.get("body") or "").split())[:600]}
                         for m in (s.get("messages") or [])[-6:]],
    }


def settle(v: dict[str, Any], s: dict[str, Any], cfg: Any) -> dict[str, Any]:
    """Guards on whatever came back (model or rules): whole thousands, low ≤ high, and on a sitting listing nothing at or above
    the asking price. The offer is never above the ceiling or the as-is value."""
    p = s["p"]
    lp = int(p.get("list_price") or 0)
    for k in ("as_is_low", "as_is_high", "arv_low", "arv_high", "repairs", "offer", "max_price"):
        v[k] = _k(v.get(k))
    for lo, hi in (("as_is_low", "as_is_high"), ("arv_low", "arv_high")):
        if v[lo] and v[hi] and v[lo] > v[hi]:
            v[lo], v[hi] = v[hi], v[lo]
    v["capped"] = False
    if lp and sitting(p, cfg):
        cap = _k(lp * (1 - float(_conf(cfg).get("sitting_discount_pct", 3)) / 100) - 500)
        for k in ("as_is_low", "as_is_high", "max_price", "offer"):
            if v[k] > cap:
                v[k], v["capped"] = cap, True
    if v["max_price"] and v["as_is_high"]:
        v["max_price"] = min(v["max_price"], v["as_is_high"])
    v["max_price"] = v["max_price"] or v["as_is_high"]
    if v["offer"] and v["max_price"]:
        v["offer"] = min(v["offer"], v["max_price"])
    if v["offer"] < 30_000:
        v["offer"] = 0
    v["list_price"] = lp
    v["gap"] = lp - v["offer"] if lp and v["offer"] else None
    v["gap_pct"] = round(v["gap"] / lp * 100) if v.get("gap") is not None and lp else None
    return v


def build(conn, cfg: Any, llm: Any, s: dict[str, Any], feedback: list[str], analysis: str = "",
          photos: dict[str, Any] | None = None) -> dict[str, Any]:
    faults = market_test(s, cfg)
    notes: list[str] = []
    try:
        from .card_chat import lead_notes
        notes = lead_notes(conn, (s.get("agent") or {}).get("id"))
    except Exception:  # noqa: BLE001
        pass
    out = None
    if llm is not None and getattr(llm, "available", False):
        try:
            out = llm.structured("verdict", SYSTEM, json.dumps(_facts(s, feedback, analysis, photos, faults, notes), ensure_ascii=False, default=str),
                                 SCHEMA, max_tokens=1800)
        except Exception as e:  # noqa: BLE001 — the bottom line never breaks the numbers flow
            log.warning("verdict for %s: %s", s["p"].get("street"), e)
    if isinstance(out, dict) and (out.get("conclusion") or "").strip():
        v = {**out, "source": "claude"}
        v["faults"] = [str(x).strip() for x in (out.get("faults") or []) if str(x).strip()][:4] or faults[:3]
    else:
        v = by_rules(s, cfg, faults, photos)
    v["photos"] = photos
    return settle(v, s, cfg)


# ---------- 4. what he reads ----------

def _e(x: Any) -> str:
    import html
    return html.escape(str(x), quote=False)


def headline(v: dict[str, Any]) -> str:
    parts = [f"Asking {money(v['list_price'])}." if v.get("list_price") else ""]
    if v.get("as_is_high"):
        lo, hi = v.get("as_is_low"), v["as_is_high"]
        parts.append(f"Worth about {money(lo)} to {money(hi)} as it sits." if lo and lo != hi else f"Worth about {money(hi)} as it sits.")
    if v.get("offer"):
        parts.append(f"Offer {money(v['offer'])}." + (f" Do not go over {money(v['max_price'])}." if v.get("max_price", 0) > v["offer"] else ""))
        if v.get("gap") is not None and v["gap"] > 0:
            parts.append(f"That is {money(v['gap'])} ({v['gap_pct']}%) under asking.")
    else:
        parts.append("No offer number yet: the data does not support one.")
    return " ".join(x for x in parts if x)


def to_comment(s: dict[str, Any], v: dict[str, Any], tail: str = "") -> str:
    """Short, bold, the last thing on the card (comments allow strong / em / ul / li only)."""
    p = s["p"]
    out = [f"<body>🤖 {MARK} <strong>{TITLE} · {_e(p['street'])}, {_e(p['city'])}</strong>\n\n<strong>{_e(headline(v))}</strong>\n"]
    if v.get("source") == "rules":
        out.append("\n<em>Rules only: Claude was not reachable, so this is the conservative math (lower half of the sales). "
                   "Comment 'numbers' again later for the full read. No offer email drafted.</em>\n")
    if (v.get("conclusion") or "").strip():
        out.append(f"\n<strong>{_e(v['conclusion'].strip())}</strong>\n")
    if v.get("faults"):
        out.append("\n<strong>What was wrong in our numbers:</strong>\n<ul>" + "".join(f"<li>{_e(x)}</li>" for x in v["faults"]) + "</ul>")
    if (v.get("why_not_selling") or "").strip():
        out.append(f"<strong>Why it has not sold:</strong> {_e(v['why_not_selling'].strip())}\n")
    nums = []
    if v.get("arv_high"):
        lo, hi = v.get("arv_low"), v["arv_high"]
        nums.append("Resale after the work: " + (f"{money(lo)} to {money(hi)}" if lo and lo != hi else money(hi)))
    if v.get("repairs"):
        nums.append(f"Work it needs: about {money(v['repairs'])}")
    if v.get("comps_to_use"):
        nums.append("Sales that really compare: " + "; ".join(str(x) for x in v["comps_to_use"][:4]))
    if v.get("comps_to_drop"):
        nums.append("Left out: " + "; ".join(str(x) for x in v["comps_to_drop"][:4]))
    ph = v.get("photos")
    if ph:
        nums.append(f"Photos ({ph.get('photos_seen')} looked at): {str(ph.get('level') or '').lower()} rehab, about "
                    f"{money(ph.get('repairs_low'))} to {money(ph.get('repairs_high'))}. {ph.get('summary') or ''}".strip())
    if nums:
        out.append("\n<ul>" + "".join(f"<li>{_e(x)}</li>" for x in nums) + "</ul>")
    if v.get("verify"):
        out.append("<strong>Check first:</strong>\n<ul>" + "".join(f"<li>{_e(x)}</li>" for x in v["verify"][:3]) + "</ul>")
    if v.get("capped"):
        out.append("<em>Held under the asking price: it has been sitting there without a buyer.</em>\n")
    out.append(_e(f"Confidence: {v.get('confidence') or 'low'}.") + (f" {_e(tail)}" if tail else "") + "</body>")
    return "".join(out)


# ---------- 5. the offer email ----------

TERM_RES = {"inspection_days": re.compile(r"(\d{1,2})[\s-]*day(?:s)?\s+inspection", re.I),
            "close_days": re.compile(r"(\d{1,3})[\s-]*day(?:s)?\s+clos", re.I),
            "deposit": re.compile(r"\$?\s?(\d{1,3}(?:,\d{3})|\d{1,2}\s?k)\s+(?:escrow|deposit|emd|earnest)", re.I)}
TITLE_RE = re.compile(r"as soon as (?:the )?title\b[^.,;]{0,30}", re.I)
# the same terms said the other way round ("Inspection period needs to be 15 days and escrow let's lower it to 5k", Alberto Pascual)
TERM_RES_REV = {"inspection_days": re.compile(r"inspection(?: period)?[^.,\d$]{0,30}?(\d{1,2})[\s-]*day", re.I),
                "close_days": re.compile(r"\bclos(?:e|ing)[^.,\d$]{0,30}?(\d{1,3})[\s-]*day", re.I),
                "deposit": re.compile(r"(?:escrow|deposit|emd|earnest)[^.,\d$]{0,30}?\$?\s?(\d{1,3},\d{3}|\d{1,2}\s?k)\b", re.I)}
CARD_OFFER_RE = re.compile(r"\boffer\b[^$]{0,80}\$\s?(\d{2,3},\d{3})\b", re.I | re.S)


def parse_terms(text: str) -> dict[str, Any]:
    """The terms he states in his comment ("5k escrow, 15 day inspection, 30 day closing or as soon as the title is approved")."""
    out: dict[str, Any] = {}
    for key, rx in TERM_RES.items():
        m = rx.search(text or "") or TERM_RES_REV[key].search(text or "")
        if m:
            raw = m.group(1).lower().replace(",", "").replace(" ", "")
            out[key] = int(float(raw[:-1]) * 1000) if raw.endswith("k") else int(raw)
    if TITLE_RE.search(text or ""):
        out["close_note"] = ", or as soon as title is approved"
    return out


def offer_terms(cfg: Any, price: int, p: dict[str, Any], over: dict[str, Any] | None = None) -> list[str]:
    """His written-offer terms (2026-11-19: "the same thing we write on all of them: 5k escrow, 15 day inspection, cash close");
    what he states in the comment wins (`parse_terms`)."""
    t = {**(_conf(cfg).get("offer_terms", {}) or {}), **(over or {})}
    close, insp, emd = int(t.get("close_days", 30)), int(t.get("inspection_days", 15)), int(t.get("deposit", cfg["formula"].get("emd", 5000)))
    note = t.get("close_note") or ("" if p.get("vacant") else ", or longer if that works better for the seller")
    return [f"{BULLET} Price: ${price:,}, cash", f"{BULLET} As is, no repairs asked of the seller", f"{BULLET} {insp} day inspection period",
            f"{BULLET} ${emd:,} escrow deposit", f"{BULLET} Close in {close} days{note}"]


BULLET = "•"       # Rodrigo, 2026-11-19: "each term needs to be a set bullet point"


def terms_sentences(cfg: Any, price: int, p: dict[str, Any], over: dict[str, Any] | None = None) -> str:
    """The terms as sentences (not used for offers: Rodrigo, 2026-11-19, wants each term as its own bullet point; kept for replies
    that only mention the terms in passing)."""
    t = {**(_conf(cfg).get("offer_terms", {}) or {}), **(over or {})}
    close, insp, emd = int(t.get("close_days", 30)), int(t.get("inspection_days", 15)), int(t.get("deposit", cfg["formula"].get("emd", 5000)))
    note = t.get("close_note") or ("" if p.get("vacant") else ", or longer if that works better for the seller")
    return (f"Our offer is ${price:,}, cash. We would take the property as is, put down a ${emd:,} escrow deposit and close in {close} days"
            f"{note}. We would need a {insp} day inspection period.")


LABEL_LINE_RE = re.compile(r"(?m)^\s*(?:[-•*]\s+)?[A-Z][\w ()/&'-]{1,45}:\s+\S")
HEADING_RE = re.compile(r"(?mi)^\s*(?:offer summary|summary|terms|the main terms|key terms|next steps|association questions[^\n]*)\s*:?\s*$")


def form_letter(body: str) -> list[str]:
    """Why an email reads like a form / an AI wrote it, not a person: label lines ("Purchase price: …"), section headings, bullets,
    length. Empty = reads like an email."""
    text = (body or "").split("\n\nBest regards")[0]
    out = []
    bullets = len(re.findall(r"(?m)^\s*[-•*]\s+\S", text))
    labels = len(LABEL_LINE_RE.findall(re.sub(r"(?m)^\s*[-•*]\s+.*$", "", text)))      # the offer's own term bullets are his format
    if labels >= 3:
        out.append(f"{labels} label lines outside the terms (\"Purchase price: …\")")
    if bullets > 6:
        out.append(f"{bullets} bullet points (the terms are five; the rest reads as a checklist)")
    if HEADING_RE.search(text):
        out.append("section headings")
    words = len(text.split())
    if words > 220:
        out.append(f"{words} words")
    if re.search(r"\bI'?m reaching out\b|\bwould like to submit\b|\bplease find\b|\bper our\b", text, re.I):
        out.append("form-letter phrases (\"I'm reaching out\", \"would like to submit\")")
    return out


def offer_problems(body: str, price: int, list_price: int | None) -> list[str]:
    from .claims import unbacked_claims
    from .outreach.compose import DASH_RE
    text = body.split("\n\nBest regards")[0]
    out = []
    if f"${price:,}" not in text:
        out.append(f"the offer price ${price:,} is missing")
    if text.count(f"${price:,}") > 2:
        out.append("the offer price is repeated")
    try:
        out += [f"says something is attached or signed ({c})" for c in unbacked_claims(body)]
    except Exception:  # noqa: BLE001
        pass
    if DASH_RE.search(text):
        out.append("dash")
    if re.search(r"\b(we buy|we'?d buy|investors?|wholesal\w*|assign\w*|arv|congrat\w*|contract|photos?|pictures?)\b", text, re.I):
        out.append("a word the offer never uses (we buy / investor / assign / ARV / contract / congrats / photos)")
    out += [f"reads like a form, not a person: {x}" for x in form_letter(body) if "words" not in x]
    words = len(text.split())
    if words < 60 or words > 200:
        out.append(f"length {words}")
    return out


def template_offer(cfg: Any, agent: dict[str, Any], p: dict[str, Any], v: dict[str, Any], sent_before: bool,
                   over: dict[str, Any] | None = None) -> dict[str, str]:
    from .outreach.compose import signature
    from .util import first_name
    op = cfg["operator"]
    first = first_name(agent.get("name")) or "there"
    opening = (f"Thanks for the information on {p['street']}. We went through the numbers on our side and I am sending our offer over."
               if sent_before else
               f"{op['sender_name'].split()[0]} with {op['company']}. We went through the numbers on {p['street']} and I am sending our offer over.")
    reason = (v.get("offer_reason") or "").strip() or "It reflects what similar properties nearby have sold for and the work this one needs."
    body = (f"Hey {first},\n\n{opening} I copied Rodrigo, our acquisitions manager, since he approved the numbers.\n\n"
            + "Our offer:\n\n" + "\n".join(offer_terms(cfg, v["offer"], p, over))
            + f"\n\nI know that is under the asking price. {reason}\n\nLet me know what the seller thinks. I am happy to get on a call "
              "if that is easier.\n\n" + signature(cfg))
    return {"subject": f"Offer on {p['street']}", "body": body}


def write_offer(cfg: Any, llm: Any, agent: dict[str, Any], p: dict[str, Any], v: dict[str, Any], sent_before: bool,
                thread: list[dict[str, str]] | None = None, pushback: list[str] | None = None, previous: str | None = None,
                instruction: str | None = None, over: dict[str, Any] | None = None) -> dict[str, str]:
    """Claude writes it in the Lise shape; anything that fails the checks twice falls back to the fixed template."""
    from .outreach.compose import signature
    from .util import first_name
    op = cfg["operator"]
    sender = op["sender_name"].split()[0]
    opening = ("One or two sentences: thank them briefly for what they told us (no fluff), say we went through the numbers and here is "
               "our offer, and that Rodrigo, our acquisitions manager, is copied because he approved the numbers."
               if sent_before else
               f"First paragraph: \"{sender} with {op['company']}.\" then that we went through the numbers on the street and are sending "
               "our offer, and that Rodrigo, our acquisitions manager, is copied because he approved the numbers.")
    system = OFFER_SYSTEM.format(sender=sender, company=op["company"], opening=opening, terms="\n".join(offer_terms(cfg, v["offer"], p, over)))
    user = json.dumps({"agent_first_name": first_name(agent.get("name")), "street": p.get("street"), "city": p.get("city"),
                       "asking_price": p.get("list_price"), "offer_price": v["offer"], "offer_reason": v.get("offer_reason"),
                       "thread": thread or [], "signature_block": signature(cfg),
                       **({"rodrigo_instruction": instruction} if instruction else {}),
                       **({"pushback": pushback, "previous_draft": previous or ""} if pushback else {})}, ensure_ascii=False, default=str)
    if llm is not None and getattr(llm, "available", False):
        for _ in range(2):
            try:
                out = llm.structured("offer_email", system, user, OFFER_SCHEMA, max_tokens=900)
            except Exception as e:  # noqa: BLE001
                log.warning("offer email for %s: %s", p.get("street"), e)
                out = None
            if not out or not (out.get("body") or "").strip():
                break
            body = str(out["body"]).strip()
            subject = re.sub(r"\$\s?[\d,]+k?", "", str(out.get("subject") or "")).strip() or f"Offer on {p['street']}"
            if not offer_problems(body, v["offer"], p.get("list_price")):
                return {"subject": subject, "body": body, "writer": "claude"}
    return {**template_offer(cfg, agent, p, v, sent_before, over), "writer": "template"}


def draft_offer(conn, cfg: Any, asana, llm, gid: str, card: dict[str, Any], s: dict[str, Any], v: dict[str, Any],
                his_number: bool = False, instruction: str | None = None) -> str:
    """Put the offer email at the bottom-line price on the card he asked on. Returns the line for the bottom-line comment.
    A first email that never went: the offer replaces it on the card (Needs Me). A lead in conversation: it becomes the NEXT
    EMAIL (Needs Me), unless an answer is already waiting there (never overwritten). Nothing is approved or sent here."""
    from . import second_read
    conf = _conf(cfg)
    p, agent = s["p"], s.get("agent") or {}
    price = v.get("offer") or 0
    change = f"To change the number, comment it (\"offer {int(price / 1000)}k\") or edit it between the EMAIL lines." if price else ""
    if not price or not (his_number or (conf.get("draft_offer", True) and v.get("source") == "claude")):
        return ""
    row = db.row_to_dict(conn.execute("SELECT * FROM outreach WHERE id=?", (card["outreach_id"],)).fetchone()) or {}
    to = row.get("to_address") or agent.get("email") or card.get("to") or ""
    if not to:
        return "No offer email drafted: there is no email on file for this agent. Call them with the number."
    if db.is_dnc(conn, agent_id=agent.get("id"), email=to):
        return "No offer email drafted: this agent is on do-not-contact."
    sent_before = row.get("status") == "sent"
    from . import answers
    if sent_before and not his_number and answers.pending(conn, card["outreach_id"]):
        return (f"A NEXT EMAIL is already waiting on this card, so I did not replace it with the offer. Comment \"offer {int(price / 1000)}k\" "
                "and it becomes the offer email.")
    thread: list[dict[str, str]] = []
    try:
        from .replies.writer import _thread_text
        thread = _thread_text(conn, row["agent_id"]) if sent_before and row.get("agent_id") else []
    except Exception:  # noqa: BLE001
        pass
    over = parse_terms(instruction or "")
    d = write_offer(cfg, llm, agent, p, v, sent_before, thread, instruction=instruction, over=over)
    state = {"body": d["body"]}

    def rewrite(pushback: list[str]) -> str | None:
        new = write_offer(cfg, llm, agent, p, v, sent_before, thread, pushback=pushback, previous=state["body"],
                          instruction=instruction, over=over)
        if new.get("writer") != "claude":
            return None
        state["body"] = new["body"]
        return new["body"]

    case = {"who": f"listing agent {agent.get('name') or ''}, residential listing",
            "house_rules": "This IS a written offer and the price in it is intended: Rodrigo asked for the number on the card and the "
                           "draft waits for his approval. Judge the logic only: does it fit what the agent told us, does it state a fact "
                           "about the house or the sales that is not in the facts, does it repeat or contradict the thread.",
            "thread": thread, "facts": {"street": p.get("street"), "asking_price": p.get("list_price"), "offer_price": price,
                                        "offer_reason": v.get("offer_reason"), "rodrigo_says": s.get("asked"),
                                        "comps": [{"address": c.get("address"), "price": c.get("sold_price"), "sqft": c.get("sqft")}
                                                  for c in s.get("comps") or []]},
            "rodrigo_instruction": instruction or (f"Draft the offer email at {money(price)} for my approval."
                                                   + (" The number is mine." if his_number else ""))}
    body, review = d["body"], None
    try:
        body, review = second_read.run(llm, cfg, case, d["body"], rewrite)
    except Exception as e:  # noqa: BLE001
        log.warning("second read of the offer for %s: %s", p.get("street"), e)
    if review is not None and review.get("verdict") == "HOLD":
        return f"No offer email on the card: the second reader says hold. {second_read.line(review)}"
    read = second_read.line(review)
    cc = [x for x in [cfg.secret("REPORT_TO")] if x and conf.get("cc_rodrigo", True)]
    try:
        if sent_before:
            answers.set_answer(conn, cfg, asana, card["outreach_id"], body, quiet=True,
                               note=f"Offer at {money(price)} ({'your number' if his_number else 'from the bottom line'}). {read}".strip())
            where = "It is the NEXT EMAIL on this card"
        else:
            from .approvals import _attach_picture, needs_me_section
            from .house_card import rerender
            conn.execute("UPDATE outreach SET subject=?, body=?, variant=? WHERE id=?", (d["subject"], body, "offer:verdict", card["outreach_id"]))
            if not (hasattr(asana, "update_html_notes") and rerender(conn, asana, gid, card["outreach_id"], to, d["subject"], body, cc)):
                from .house_card import email_block
                notes = asana.task(gid).get("notes") or ""
                cut = notes.find("To: ")
                head = re.sub(r"(?m)^Cc:.*\n\Z", "", notes[:cut]) if cut >= 0 else notes + "\n"
                asana.update_notes(gid, head + email_block(to, d["subject"], body, card["outreach_id"], cc))
            _attach_picture(cfg, asana, gid, to, d["subject"], body, card["outreach_id"], rev=200)
            nm = needs_me_section(cfg, asana)
            asana.move(gid, nm)
            where = "It replaced the email on this card"
    except Exception as e:  # noqa: BLE001
        log.exception("offer draft on card %s: %s", gid, e)
        return f"The offer email could not be put on the card ({str(e)[:100]}). The number stands."
    try:
        from .learning import record_price
        record_price(conn, cfg, p["id"], int(price))
    except Exception:  # noqa: BLE001
        pass
    return (f"The offer email at {money(price)} is drafted. {where}" + (", Cc you" if cc and not sent_before else "")
            + f". Nothing sends until you drag the card to Approved. {change} {read}").strip()


# ---------- the card shows the bottom line ----------

LINE_RE = re.compile(r"Offer \$([\d,]+)\.(?: Do not go over \$([\d,]+)\.)?")
ASK_RE = re.compile(r"Asking \$([\d,]+)\.")


def latest(conn, property_id: int) -> dict[str, Any] | None:
    try:
        return db.row_to_dict(conn.execute("SELECT * FROM verdicts WHERE property_id=? ORDER BY id DESC LIMIT 1", (property_id,)).fetchone())
    except Exception:  # noqa: BLE001 — a database from before this table
        return None


def learn_from_comments(conn, card: dict[str, Any], gid: str, comments: list[dict[str, Any]]) -> bool:
    """A 🏁 BOTTOM LINE comment written by hand (a Claude session on the card) is stored like the engine's own, so the card's
    numbers section shows it (Zaira Zavala, 2026-11-19: "the card there is the same as the older one was"). True when stored."""
    text = next((c.get("text") or "" for c in reversed(comments or []) if f"{MARK} {TITLE}" in (c.get("text") or "")), "")
    m = LINE_RE.search(text)
    if not m or not card.get("outreach_id"):
        return False
    from .comment_router import card_props
    props = card_props(conn, card["outreach_id"])
    if len(props) != 1:
        return False
    offer = int(m.group(1).replace(",", ""))
    top = int((m.group(2) or m.group(1)).replace(",", ""))
    have = latest(conn, props[0]["id"])
    if have and have.get("offer") == offer and have.get("max_price") == top:
        return False
    a = ASK_RE.search(text)
    conn.execute("INSERT INTO verdicts(property_id,outreach_id,card_gid,list_price,as_is_low,as_is_high,arv_low,arv_high,repairs,offer,max_price,"
                 "confidence,source,body,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (props[0]["id"], card["outreach_id"], gid, int(a.group(1).replace(",", "")) if a else props[0].get("list_price"), 0, 0, 0, 0, 0,
                  offer, top, None, "card", "{}", db.iso()))
    return True


def card_lines(conn, p: dict[str, Any]) -> list[str]:
    """The numbers section of the house card once a bottom line exists: the price to be at, first and bold; the engine's older
    screen numbers are named as replaced, not shown as the answer."""
    v = latest(conn, p["id"]) if conn is not None and p.get("id") else None
    if not v or not v.get("offer"):
        return []
    lp = int(v.get("list_price") or p.get("list_price") or 0)
    gap = f" · {money(lp - v['offer'])} ({round((lp - v['offer']) / lp * 100)}%) under asking" if lp and lp > v["offer"] else ""
    out = [f"<li><strong>BOTTOM LINE: offer {_e(money(v['offer']))}</strong>"
           + (f" · do not go over {_e(money(v['max_price']))}" if (v.get("max_price") or 0) > v["offer"] else "") + f"{_e(gap)}</li>"]
    if v.get("as_is_high"):
        out.append(f"<li>Worth as it sits: about {_e(money(v.get('as_is_low') or v['as_is_high']))} to {_e(money(v['as_is_high']))}</li>")
    if v.get("arv_high"):
        out.append(f"<li>Resale after the work: about {_e(money(v['arv_high']))}" + (f" · work about {_e(money(v['repairs']))}" if v.get("repairs") else "") + "</li>")
    out.append(f"<li>From the bottom line of {_e(str(v.get('created_at') or '')[:10])} (the pinned comment has the why). "
               "It replaces the engine's first screen numbers.</li>")
    return out


# ---------- the one call the numbers flow makes ----------

def after_numbers(conn, cfg: Any, asana, llm, gid: str, card: dict[str, Any], snaps: list[tuple[dict[str, Any], dict[str, Any]]],
                  feedback: list[str], analysis: str = "", fetch=None) -> int:
    """Post the bottom line for every house in the snapshot (and draft the offer when the ask was about ONE house). Never raises."""
    if not enabled(cfg) or not snaps:
        return 0
    done = 0
    for p, s in snaps:
        try:
            photos = photo_read(conn, cfg, llm, p, fetch)
            v = build(conn, cfg, llm, s, feedback, analysis, photos)
            tail = draft_offer(conn, cfg, asana, llm, gid, card, s, v) if len(snaps) == 1 else \
                "Two houses on this card: no offer email drafted. Comment the house and the number."
            conn.execute("INSERT INTO verdicts(property_id,outreach_id,card_gid,list_price,as_is_low,as_is_high,arv_low,arv_high,repairs,offer,"
                         "max_price,confidence,source,body,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (p["id"], card.get("outreach_id"), gid, v.get("list_price"), v["as_is_low"], v["as_is_high"], v["arv_low"], v["arv_high"],
                          v["repairs"], v["offer"], v["max_price"], v.get("confidence"), v.get("source"),
                          json.dumps({k: v.get(k) for k in ("faults", "why_not_selling", "conclusion", "offer_reason", "verify")}), db.iso()))
            html = to_comment(s, v, tail)
            fn = getattr(asana, "comment_html", None)
            fn(gid, html) if callable(fn) else asana.comment(gid, re.sub(r"<[^>]+>", "", html))
            done += 1
        except Exception as e:  # noqa: BLE001
            log.exception("bottom line for %s on card %s: %s", p.get("street"), gid, e)
    return done


# ---------- "offer 290k": his number, same card ----------

PRICE_RE = re.compile(r"\$\s?(\d{2,3},\d{3}|\d{5,7})\b|\$?\s?\b(\d{2,3}(?:\.\d+)?)\s?k\b|\b(\d{2,3},\d{3})\b", re.I)
OFFER_WORD_RE = re.compile(r"\boffer\b|\bmake it\b|\bgo with\b|\bdraft\b", re.I)
NOT_YET_RE = re.compile(r"\?|\b(?:first|until|unless|wait|hold off|don'?t|do not|not yet|what would|should we)\b", re.I)


def asked_price(feedback: list[str]) -> int | None:
    """He asks for an offer at ONE price ("offer 290k", "Let's send an offer at $250,000 … also our terms 5k escrow, 15 day
    inspection", "let's draft a $275k asking price"). Before 2026-11-19 these went to the email rewriter, whose rules forbid any
    price: it dropped the offer, kept one side sentence and said "Revised" (Tamara Mason, twice). A question, a condition ("ask
    about the roof first"), two different prices or a bare number is not an offer ask."""
    text = " ".join(f.strip() for f in feedback if f.strip())
    if not text or not OFFER_WORD_RE.search(text) or NOT_YET_RE.search(text):
        return None
    found = set()
    for m in PRICE_RE.finditer(text):
        raw = (m.group(1) or m.group(3) or "").replace(",", "")
        n = int(raw) if raw else int(float(m.group(2)) * 1000)
        if 30_000 <= n <= 20_000_000:
            found.add(n)
    return found.pop() if len(found) == 1 else None


def offer_comment(conn, cfg: Any, asana, llm, gid: str, card: dict[str, Any], feedback: list[str]) -> bool:
    """"offer 290k" on a card → the offer email at HIS number on that card (Needs Me; replaces a waiting draft). True = handled."""
    price = asked_price(feedback)
    said = " ".join(f.strip() for f in feedback if f.strip())
    terms_only = None
    if not price and parse_terms(said) and not NOT_YET_RE.search(said):
        # the card already holds an offer and he only changes its terms ("inspection needs to be 15 days, escrow 5k"): same price,
        # new terms. Before 2026-11-23 this went to the email rewriter, which refuses any price, and the card sat in Needs Revision.
        m = CARD_OFFER_RE.search(card.get("body") or "")
        if m:
            price = int(m.group(1).replace(",", ""))
            terms_only = (f"Keep this offer as it is, same price. Change only these terms: {said}\n\nThe offer email on the card now "
                          f"(keep what it says apart from those terms):\n{card.get('body') or ''}")
    if not price or not enabled(cfg):
        return False
    from .answers import dictation
    if dictation(feedback):          # the whole email in his words: that path keeps his text as it is
        return False
    from .comment_router import card_props
    props = card_props(conn, card["outreach_id"])
    if len(props) != 1:
        asana.comment(gid, "Two houses on this card: say which one the number is for (the street and the number in one comment, "
                           "written as the email you want), and I'll draft it.")
        return True
    p = props[0]
    agent = db.row_to_dict(conn.execute("SELECT * FROM agents WHERE id=?", (p.get("agent_id"),)).fetchone()) or {} if p.get("agent_id") else {}
    last = db.row_to_dict(conn.execute("SELECT * FROM verdicts WHERE property_id=? ORDER BY id DESC LIMIT 1", (p["id"],)).fetchone()) or {}
    reason = (json.loads(last.get("body") or "{}").get("offer_reason") or "") if last else ""
    lp = int(p.get("list_price") or 0)
    v = {"offer": _k(price), "offer_reason": reason, "source": "rodrigo", "list_price": lp}
    line = draft_offer(conn, cfg, asana, llm, gid, card, {"p": p, "agent": agent, "comps": [], "asked": " ".join(feedback)}, v, his_number=True,
                       instruction=terms_only or said)
    gap = f" That is {money(lp - v['offer'])} ({round((lp - v['offer']) / lp * 100)}%) under asking." if lp and lp > v["offer"] else ""
    asana.comment(gid, (line or "The offer email could not be drafted.") + gap)
    return True
