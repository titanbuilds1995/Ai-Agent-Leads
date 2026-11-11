"""The process behind the deals, for the investor (Rodrigo, 2026-11-11: "I want to sell him on the whole process … how many properties
I scouted through so he didn't have to … what properties I skipped … tenants that were not reliable or grade C, food trucks,
month-to-month … 40 minutes or closer to the nearest airport … the asset classes he told me … owner operators … everybody that
wouldn't agree to the cap … so he kind of sees the process of elimination").

Everything here is COUNTS and REASONS, never a price, a broker or an address, so the whole block is safe in the investor view of
the 1031 page (dashboard/public/1031.html, tab "The process"). Built by site.build_deals into deals.json → `process`.

Three parts:
  criteria   what we look for, in words (from config `commercial.buyer_box`; `commercial.process.criteria` overrides the wording)
  funnel     every listing pulled from LoopNet → the ones in the box, one step per rule, with how many each rule removed
  talks      the broker conversations: emailed, answered, how fast, and why the ones we dropped after talking were dropped
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .board import COLUMNS

# Why a listing was dropped AFTER we talked to the broker: read from the 🧠 READ FOR YOU text and the broker's own words.
# Order matters: the first bucket that matches wins.
DROP_REASONS: list[tuple[str, str, re.Pattern[str]]] = [
    ("gone", "Already leased, sold or under contract",
     re.compile(r"under contract|already sold|just sold|been sold|has sold|recently sold|just leased|been leased|fully leased to a new|off the market|no longer available|withdrawn", re.I)),
    ("tenants", "Tenants not reliable enough (month-to-month, food trucks, local credit, class C)",
     re.compile(r"month[- ]to[- ]month|\bmtm\b|food truck|flea market|no (?:credit|national) tenant|local (?:credit|tenant)|mom[- ]and[- ]pop|"
                r"class c\b|\bc[- ]class|c[- ]minus|tenant (?:quality|credit|risk)|short[- ]term lease|vacan(?:t|cy) risk|weak tenan|unreliable tenan", re.I)),
    ("owner_operator", "Owner-operated business, not a passive investment",
     re.compile(r"owner[- ]operat|owner[- ]run|owner[- ]user|business (?:for sale|included|only)|run (?:it|the business) yourself|hands[- ]on|"
                r"you(?:'d| would) (?:have to )?(?:run|operate)|requires? (?:an )?operator|management[- ]intensive|not (?:a )?passive", re.I)),
    ("cap", "Would not hold an 8 cap at the asking price (or the broker said so)",
     re.compile(r"(?:not|never|won'?t|will not|can'?t|cannot|doesn'?t|does not|isn'?t|is not)\s+(?:an? |going to |get to |hit |hold |support |pencil to |"
                r"work at |trade at |be )?(?:a |an |the |close to )?(?:true |real )?(?:8|eight|7|seven|6|six|5|five|4|four|9|nine|10|ten|12|twelve)[\s.-]*(?:%|percent|cap)|"
                r"under (?:an? |the )?(?:8|eight)[\s-]*(?:%|cap)|(?:6|7|five|six|seven|4|5)(?:\.\d)?\s*(?:%|percent)?\s*cap(?: rate)?\b|cap (?:rate )?(?:is )?(?:too )?low|"
                r"not a cap[- ]rate (?:deal|sale)|priced (?:on|for) (?:the )?(?:land|development|upside|pro ?forma)|misses the 8|below the (?:8|eight)|"
                r"firm on (?:the )?price|won'?t (?:come down|move|negotiate|budge)|no room on (?:the )?price|price is firm|proforma|pro forma|projected (?:noi|cap|income)", re.I)),
    ("box", "Outside the box (price, asset type, land or development)",
     re.compile(r"outside (?:the|our) box|over (?:the|our) (?:price|budget)|above (?:the|our) (?:price )?(?:range|box|max)|land (?:value|play|deal)|"
                r"development (?:site|deal|play)|redevelop|ground[- ]up|vacant land|entitle", re.I)),
    ("books", "Would not or could not show the real numbers",
     re.compile(r"no (?:books|financials|t-?12|rent roll)|won'?t (?:share|send|provide|release)|not (?:going to |willing to )?(?:share|send|provide|release)|"
                r"refus\w* to (?:share|send|provide)|only (?:in|during|after) (?:due diligence|dd|contract)|after (?:an )?(?:accepted )?(?:offer|loi|contract)|"
                r"can'?t (?:share|send|provide|give)|cannot (?:share|send|provide|give)|no (?:financial )?(?:information|data) (?:available|provided)", re.I)),
]
DROP_KEYS = {k: label for k, label, _ in DROP_REASONS}
DROP_KEYS["other"] = "Did not fit for another reason"

# The box rules, in the order the engine applies them (titan/commercial/scoring.in_box), with the plain words for each.
BOX_STEPS: list[tuple[str, str, re.Pattern[str]]] = [
    ("price", "Price outside the box", re.compile(r"^price outside", re.I)),
    ("asset", "Asset type we are not buying", re.compile(r"^asset type out", re.I)),
    ("units", "Multifamily too small", re.compile(r"^multifamily under", re.I)),
    ("cap", "Stated cap under the floor", re.compile(r"^stated cap .* under the floor", re.I)),
    ("land", "Land, development or a business only", re.compile(r"^land / development", re.I)),
    ("airport", "Too far from a commercial airport", re.compile(r"min to the nearest airport", re.I)),
    ("quality", "Building class or condition below the bar", re.compile(r"^quality:", re.I)),
]


def _box(cfg: Any, key: str, default: Any = None) -> Any:
    v = (cfg.get("commercial.buyer_box", {}) or {}).get(key)
    return default if v is None else v


def _money_m(n: Any) -> str:
    try:
        return f"${float(n) / 1e6:g}M"
    except (TypeError, ValueError):
        return str(n)


def criteria(cfg: Any) -> list[dict[str, str]]:
    """What we look for, in words. `commercial.process.criteria` (a list of {title, text}) replaces the generated wording when set."""
    custom = cfg.get("commercial.process.criteria")
    if custom:
        return [{"title": str(c.get("title", "")), "text": str(c.get("text", ""))} for c in custom if isinstance(c, dict)]
    types = [str(t) for t in (_box(cfg, "asset_types_in", []) or [])]
    names = {"nnn": "single-tenant NNN", "retail": "retail", "hotel": "hotels", "industrial": "industrial", "office": "office",
             "multifamily": "multifamily (5+ units)", "medical": "medical office"}
    minutes = _box(cfg, "airport_max_minutes")
    out = [
        {"title": "The box", "text": f"{_money_m(_box(cfg, 'price_min', 0))} to {_money_m(_box(cfg, 'price_max', 0))}, anywhere in Florida. "
                                     "Existing, cash-flowing property only: no land, no development sites, no businesses sold on their own."},
        {"title": f"A real {float(_box(cfg, 'cap_min', 8.0)):g} cap", "text": "The cap rate has to hold on the actual trailing twelve months, after the property is "
                                                                              "re-assessed at the sale price, with a manager and reserves in the numbers. "
                                                                              "A stated cap under the floor, or one built on projections, is out."},
        {"title": "The asset classes you asked for", "text": ", ".join(names.get(t, t) for t in types).capitalize() +
                                                           ". Relatively new to newer buildings, in markets that are growing."},
        {"title": "Reliable tenants", "text": "Credit tenants on real leases. We skipped the high yields that came with month-to-month tenants, "
                                              "food trucks, flea-market style retail or class C buildings."},
        {"title": "Passive ownership", "text": "No owner-operated businesses. If the income depends on you running it, it is not an investment."},
    ]
    if minutes:
        out.append({"title": f"{int(minutes)} minutes from an airport", "text": f"Every property within about {int(minutes)} minutes of a commercial airport, "
                                                                                 "so you can get to it."})
    bc = _box(cfg, "min_building_class")
    if bc:
        out.append({"title": f"Class {bc} or better", "text": "Building class C or better, nothing described as distressed, deferred or in a below-C area."})
    return out


def funnel(conn, cfg: Any) -> dict[str, Any]:
    """Every listing pulled → the ones in the box, one step per rule. Uses box_note (the engine's own reason) so the counts match the
    database exactly; the step order is the order the rules are applied in."""
    rows = conn.execute("SELECT in_box, box_note, status FROM commercial_listings").fetchall()
    total = len(rows)
    undisclosed = sum(1 for r in rows if r[0] is None)
    out_rows = [r for r in rows if r[0] == 0]
    steps: list[dict[str, Any]] = []
    remaining = total - undisclosed
    steps.append({"key": "pulled", "label": "Listings pulled from LoopNet", "removed": 0, "remaining": total})
    steps.append({"key": "undisclosed", "label": "Price not disclosed (asked the broker instead)", "removed": undisclosed, "remaining": remaining})
    buckets: Counter[str] = Counter()
    for r in out_rows:
        note = r[1] or ""
        for key, _label, rx in BOX_STEPS:
            if rx.search(note):
                buckets[key] += 1
                break
        else:
            buckets["other"] += 1
    for key, label, _rx in BOX_STEPS:
        n = buckets.get(key, 0)
        remaining -= n
        steps.append({"key": key, "label": label, "removed": n, "remaining": remaining})
    if buckets.get("other"):
        remaining -= buckets["other"]
        steps.append({"key": "other", "label": "Out for another reason", "removed": buckets["other"], "remaining": remaining})
    in_box = sum(1 for r in rows if r[0] == 1)
    steps.append({"key": "in_box", "label": "In the box", "removed": 0, "remaining": in_box})
    return {"total": total, "in_box": in_box, "undisclosed": undisclosed, "out": len(out_rows), "steps": steps}


def classify_drop(text: str) -> str:
    """Which bucket a dropped conversation falls in, from the read + the broker's words."""
    for key, _label, rx in DROP_REASONS:
        if rx.search(text or ""):
            return key
    return "other"


def talks(conn, cfg: Any) -> dict[str, Any]:
    """The broker conversations: how many we wrote, who answered and how fast, and why the ones we dropped after talking were dropped."""
    threads = conn.execute("SELECT id, listing_id, broker_email, first_reply_at, hours_to_reply, bounced, state FROM commercial_threads").fetchall()
    brokers = {(t[2] or "").lower() for t in threads if t[2]}
    answered = [t for t in threads if t[3] and not t[5]]
    hours = sorted(float(t[4]) for t in answered if t[4] is not None)
    within_hour = sum(1 for h in hours if h <= 1)
    within_day = sum(1 for h in hours if h <= 24)
    median = hours[len(hours) // 2] if hours else None
    # latest read per thread
    latest: dict[int, dict[str, Any]] = {}
    for r in conn.execute("SELECT thread_id, listing_id, verdict, text, created_at FROM commercial_reviews ORDER BY created_at").fetchall():
        latest[r[0]] = {"listing_id": r[1], "verdict": (r[2] or "").upper(), "text": r[3] or ""}
    last_words = {t[0]: (conn.execute("SELECT last_reply_text FROM commercial_threads WHERE id=?", (t[0],)).fetchone() or [""])[0] or "" for t in answered}
    verdicts: Counter[str] = Counter()
    drops: Counter[str] = Counter()
    for tid, rv in latest.items():
        verdicts[rv["verdict"] or "CAN'T TELL YET"] += 1
        if rv["verdict"] in ("NOT A FIT", "DOES NOT FIT"):
            drops[classify_drop(rv["text"] + "\n" + last_words.get(tid, ""))] += 1
    cols = Counter()
    for r in conn.execute("SELECT board_column, COUNT(*) FROM commercial_listings WHERE in_box=1 GROUP BY board_column").fetchall():
        cols[r[0] or "New"] = r[1]
    working = {c: cols.get(c, 0) for c in COLUMNS}
    dropped_total = sum(drops.values())
    reasons = [{"key": k, "label": DROP_KEYS[k], "count": drops.get(k, 0)} for k, _l, _r in DROP_REASONS] + [{"key": "other", "label": DROP_KEYS["other"], "count": drops.get("other", 0)}]
    reasons = [r for r in reasons if r["count"]]
    reasons.sort(key=lambda r: -r["count"])
    return {"emailed": len(threads), "brokers": len(brokers), "answered": len(answered), "within_hour": within_hour, "within_day": within_day,
            "median_hours": round(median, 1) if median is not None else None, "verdicts": dict(verdicts), "read": len(latest),
            "dropped": dropped_total, "drop_reasons": reasons,
            "still_working": sum(working.get(c, 0) for c in ("Good fit", "In touch with the agent", "Books received", "Offer out")),
            "offers_out": working.get("Offer out", 0), "books_received": working.get("Books received", 0), "columns": working}


def build_process(conn, cfg: Any) -> dict[str, Any]:
    return {"criteria": criteria(cfg), "funnel": funnel(conn, cfg), "talks": talks(conn, cfg),
            "note": cfg.get("commercial.process.note") or ""}
