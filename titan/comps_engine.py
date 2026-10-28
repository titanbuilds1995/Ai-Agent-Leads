"""Comp selection, second version (Rodrigo, 2026-10-28: "the comps that are bringing me are just incorrect").

What went wrong on real cards, and what this module does about it:
- 73 Ann Lee Ln (Alex Castro): a 1974 townhouse in an HOA was comped against detached pool houses → ARV $496k instead of
  ~$300k. Now every house gets a PRODUCT (detached / attached / multi / condo / land / mobile) read from the type, the
  sub-type, the remarks, the HOA and the lot, and only the same product counts.
- 1515 NE 16th St (David Carlton): lot value polluted by waterfront sales; a main house + detached cottage valued as one
  1,700 sf house. Now waterfront sales never comp a dry house, new builds never comp an old one, and split layouts /
  unpermitted work / an impossible bed count for the size are flagged and discounted, with the reason shown.
- 2327 Polk St (Richard Dipietro): a 1942 duplex next to a $3.1M new build at $977/sf. Now sales far off the group's
  $/sf are dropped for every product, year built and bed count are matched, and each comp is ranked by similarity.
- The engine's valuation and the numbers snapshot used different rules (wide median vs renovated median). Now both use
  `select()` + `arv()` here, so the card and the engine agree.

Every valuation carries a GRADE (A–D) and the reasons, so a weak comp set is loud on the card before a number is built on
it. `titan/comps_loop.py` back-tests these rules on real sales every day and tunes the parameters (data/comps_tuning.json).
"""
from __future__ import annotations

import json
import math
import re
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

DEFAULTS: dict[str, Any] = {
    "radius_miles": 0.5, "fallback_radius_miles": 1.0, "max_radius_miles": 2.0,
    "sqft_tol_pct": 25, "relaxed_sqft_tol_pct": 35,
    "beds_tol": 1, "year_tol": 30, "lot_ratio": [0.4, 2.5],
    "ppsf_band": [0.5, 1.7], "min_price": 30000,
    "min_comps": 3, "want": 10,
    "weights": {"distance": 1.0, "sqft": 1.0, "recency": 0.5, "year": 0.5, "beds": 0.3, "condition": 0.4},
    "layout_discount_pct": {"split": 8, "unpermitted": 7, "sqft_suspect": 5},
}
PRODUCTS = ("detached", "attached", "multi", "condo", "land", "mobile")

ATTACHED_RE = re.compile(r"\b(town ?house|town ?home|villa|attached|end unit|corner unit|zero[- ]lot|row ?house|duet|twin home|"
                         r"semi[- ]detached|patio home|cluster home|garden home)\b", re.I)
SUBTYPE_ATTACHED = {"townhouse", "townhouses", "townhomes", "townhome", "villa", "duet", "attached"}
SUBTYPE_CONDO = {"condo", "condos", "condominium", "coop", "co-op", "apartment"}
WATER_RE = re.compile(r"\b(water ?front|ocean ?front|ocean access|intracoastal|on the (water|canal|lake|bay|river)|canal ?front|"
                      r"lake ?front|bay ?front|river ?front|direct (ocean|water|bay|canal)|deep ?water|boat (dock|lift|slip)|dockage|"
                      r"private dock|no fixed bridges|wide water)\b", re.I)
POOL_RE = re.compile(r"\b(pool)\b", re.I)
NEW_RE = re.compile(r"\b(new construction|newly (built|constructed)|brand new (home|house|construction|build)|to be built|"
                    r"under construction|pre[- ]construction)\b", re.I)
SPLIT_RE = re.compile(r"\b(detached|separate|additional|second|2nd|extra)\s+(guest ?house|guest ?cottage|cottage|casita|in[- ]law|"
                      r"mother[- ]in[- ]law|efficiency|studio|unit|structure|apartment|dwelling|building|quarters)\b|"
                      r"\b(guest ?house|casita|in[- ]law (suite|quarters|unit)|mother[- ]in[- ]law|efficiency (unit|apartment)|"
                      r"separate entrance|private entrance to|two (separate )?structures|main house (and|plus|with) )\b", re.I)
UNPERMIT_RE = re.compile(r"\b(unpermitted|without (a )?permits?|no permits?|open permits?|expired permits?|hold (the seller )?harmless|"
                         r"permits? (were |was )?(not|never) (closed|obtained|pulled|finaled)|not permitted|non[- ]permitted|"
                         r"buyer (to|must) (verify|assume|close) (all )?permits?)\b", re.I)
ADDITION_RE = re.compile(r"\b(addition|converted|conversion|enclosed (garage|porch|patio|carport|lanai)|garage (was )?converted|"
                         r"added (a |an )?(bedroom|bath|room|unit))\b", re.I)
RENOVATED_RE = re.compile(r"\b(renovated|remodel(?:ed)?|fully updated|completely updated|new kitchen|brand new|turnkey|turn[- ]key|"
                          r"move[- ]in ready|rehabbed|newly built|new construction|updated throughout|gut renovated)\b", re.I)
ASIS_RE = re.compile(r"\b(as[- ]is|tlc|handyman|fixer|needs (?:work|updating|repairs|renovation)|investor special|investors? "
                     r"(?:special|opportunity|delight)|cash only|original condition|bring your contractor|tear ?down|teardown)\b", re.I)


# ---------- parameters ----------

def tuning_path(cfg: Any) -> Path:
    return cfg.root / (cfg.get("comps.tuning_file") or "data/comps_tuning.json")


def params(cfg: Any, product: str | None = None) -> dict[str, Any]:
    """DEFAULTS ← config `comps:` ← valuation.* legacy keys ← data/comps_tuning.json (global, then per product)."""
    out = json.loads(json.dumps(DEFAULTS))
    v = cfg.get("valuation", {}) or {}
    for src, dst in (("comp_radius_miles", "radius_miles"), ("fallback_radius_miles", "fallback_radius_miles"),
                     ("comp_sqft_tolerance_pct", "sqft_tol_pct"), ("min_comps", "min_comps")):
        if v.get(src) is not None:
            out[dst] = v[src]
    for k, val in (cfg.get("comps", {}) or {}).items():
        if k in out and isinstance(val, dict) and isinstance(out[k], dict):
            out[k].update(val)
        elif k in out:
            out[k] = val
    try:
        tuned = json.loads(tuning_path(cfg).read_text())
    except (OSError, ValueError):
        tuned = {}
    for scope in ("global", product or ""):
        for k, val in (tuned.get(scope) or {}).items():
            if k in out and not isinstance(out[k], dict):
                out[k] = val
    return out


# ---------- reading a house ----------

def _raw(p: dict[str, Any]) -> dict[str, Any]:
    r = p.get("raw")
    if isinstance(r, str):
        try:
            r = json.loads(r)
        except ValueError:
            r = None
    return r if isinstance(r, dict) else {}


def sub_type(p: dict[str, Any]) -> str:
    st = p.get("sub_type")
    if not st:
        d = _raw(p).get("description") or {}
        st = d.get("sub_type") if isinstance(d, dict) else None
    return str(st or "").lower().strip()


def product(p: dict[str, Any]) -> str | None:
    """detached / attached / multi / condo / land / mobile, or None when nothing says."""
    t = (p.get("property_type") or "").lower()
    st = sub_type(p)
    remarks = p.get("remarks") or ""
    if t == "land":
        return "land"
    if t == "mobile":
        return "mobile"
    if t == "condo" or st in SUBTYPE_CONDO:
        return "condo"
    if t == "multi_family" or (p.get("units") or 0) > 1:
        return "multi"
    if t == "townhouse" or st in SUBTYPE_ATTACHED:
        return "attached"
    if t == "single_family":
        if ATTACHED_RE.search(remarks):
            return "attached"
        lot = p.get("lot_sqft") or 0
        if (p.get("hoa") or 0) > 0 and 0 < lot < 3000:
            return "attached"          # an HOA fee on a tiny lot is a townhouse whatever the feed calls it (73 Ann Lee Ln)
        return "detached"
    return None


def condition_of(remarks: str | None) -> str:
    r = remarks or ""
    if RENOVATED_RE.search(r):
        return "renovated"
    if ASIS_RE.search(r):
        return "as-is"
    return "not stated"


def flags(p: dict[str, Any], year: int | None = None) -> dict[str, bool]:
    """Things that change which sales count or what the number means."""
    r = p.get("remarks") or ""
    tags = {str(t).lower() for t in ((p.get("tags") if isinstance(p.get("tags"), list) else None) or _raw(p).get("tags") or []) if t}
    year = year or datetime.now().year
    yb = p.get("year_built") or 0
    beds, sqft = p.get("beds") or 0, p.get("sqft") or 0
    return {
        "waterfront": bool(p.get("water")) or bool(WATER_RE.search(r)) or any(w in t for t in tags for w in ("waterfront", "canal", "water_view", "lake", "ocean")),
        "pool": bool(p.get("pool")) or bool(POOL_RE.search(r)) or any("pool" in t for t in tags),
        "new_build": (yb >= year - 3) or bool(NEW_RE.search(r)) or "new_construction" in tags,
        "split": bool(SPLIT_RE.search(r)),
        "unpermitted": bool(UNPERMIT_RE.search(r)),
        "addition": bool(ADDITION_RE.search(r)),
        "sqft_suspect": bool(sqft and beds and ((beds >= 4 and sqft / beds < 320) or (beds >= 3 and sqft / beds < 270))),
    }


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def ppsf(c: dict[str, Any]) -> float | None:
    return c["sold_price"] / c["sqft"] if c.get("sold_price") and c.get("sqft") else None


def date_key(c: dict[str, Any]) -> int:
    d = re.sub(r"\D", "", str(c.get("sold_date") or ""))[:8]
    return int(d) if d.isdigit() and len(d) == 8 else 0


def age_days(c: dict[str, Any], today: datetime | None = None) -> int | None:
    k = date_key(c)
    if not k:
        return None
    try:
        return max(((today or datetime.now()) - datetime.strptime(str(k), "%Y%m%d")).days, 0)
    except ValueError:
        return None


def street_key(addr: str | None) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (addr or "").lower().split(",")[0]).strip()


def comp_key(c: dict[str, Any]) -> str:
    return f"{street_key(c.get('address'))}|{int(c.get('sold_price') or 0)}"


def dedupe(comps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per sale: the same address at the same price shows up twice in the feed (an MLS row and a public-record
    row, a few days apart). Keep the row with the MLS #."""
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for c in comps:
        k = comp_key(c)
        if not street_key(c.get("address")):
            k = f"#{id(c)}"
        if k not in best:
            best[k] = c
            order.append(k)
        elif not best[k].get("mls") and c.get("mls"):
            best[k] = c
    return [best[k] for k in order]


# ---------- selection ----------

def _distance(s: dict[str, Any], c: dict[str, Any]) -> float | None:
    if s.get("lat") and s.get("lng") and c.get("lat") and c.get("lng"):
        return round(haversine_miles(s["lat"], s["lng"], c["lat"], c["lng"]), 2)
    return None


def select(subject: dict[str, Any], candidates: Iterable[dict[str, Any]], prm: dict[str, Any] | None = None,
           exclude: Iterable[str] = (), months: int | None = None, want: int | None = None,
           today: datetime | None = None) -> dict[str, Any]:
    """→ {comps, dropped: [(comp, reason)], radius, tier, notes, product, flags}. Comps carry distance_mi, ppsf, condition,
    similarity. `exclude` = comp keys Claude or Rodrigo ruled out. Hard rules first, then the nearest radius that gives
    enough, relaxing the soft matches (beds, year, lot) only when strict leaves too few."""
    prm = prm or DEFAULTS
    want = want or int(prm.get("want", 10))
    need = int(prm.get("min_comps", 3))
    today = today or datetime.now()
    s_prod, s_flags = product(subject), flags(subject, today.year)
    s_sqft, s_beds, s_year, s_lot = subject.get("sqft") or 0, subject.get("beds") or 0, subject.get("year_built") or 0, subject.get("lot_sqft") or 0
    excl = set(exclude)
    notes: list[str] = []
    dropped: list[tuple[dict[str, Any], str]] = []
    pool: list[dict[str, Any]] = []
    cutoff = None
    if months:
        m = today.month - months
        y = today.year + (m - 1) // 12
        cutoff = int(f"{y}{(m - 1) % 12 + 1:02d}01")
    for c in dedupe(list(candidates)):
        c = dict(c)
        price = c.get("sold_price") or 0
        if price < prm.get("min_price", 30000):
            if price:
                dropped.append((c, "not an arm's-length price"))
            continue
        if comp_key(c) in excl:
            dropped.append((c, "ruled out on review"))
            continue
        if cutoff and date_key(c) and date_key(c) < cutoff:
            dropped.append((c, f"older than {months} months"))
            continue
        c_prod = c["_product"] if "_product" in c else product(c)
        if s_prod and c_prod and c_prod != s_prod:
            dropped.append((c, f"different product ({c_prod}, we are {s_prod})"))
            continue
        if s_prod == "land":
            if not c.get("lot_sqft"):
                dropped.append((c, "no lot size"))
                continue
        else:
            if not c.get("sqft") or not s_sqft:
                if s_sqft:
                    dropped.append((c, "no size on the sale"))
                    continue
            elif abs(c["sqft"] - s_sqft) / s_sqft > prm.get("relaxed_sqft_tol_pct", 35) / 100:
                dropped.append((c, f"size off by {abs(c['sqft'] - s_sqft) / s_sqft:.0%}"))
                continue
        c_flags = c.get("flags") if isinstance(c.get("flags"), dict) and "waterfront" in c["flags"] else flags(c, today.year)
        if c_flags["waterfront"] and not s_flags["waterfront"]:
            dropped.append((c, "waterfront sale, this house is not"))
            continue
        if c_flags["new_build"] and not s_flags["new_build"]:
            dropped.append((c, "new construction"))
            continue
        c["distance_mi"] = _distance(subject, c)
        if c["distance_mi"] is None and subject.get("zip") and c.get("zip") and str(c["zip"])[:5] != str(subject["zip"])[:5]:
            dropped.append((c, "other ZIP, no coordinates"))
            continue
        c["ppsf"] = ppsf(c)
        c["condition"] = condition_of(c.get("remarks"))
        c["flags"] = c_flags
        pool.append(c)

    def strict_ok(c: dict[str, Any]) -> bool:
        if s_sqft and c.get("sqft") and abs(c["sqft"] - s_sqft) / s_sqft > prm.get("sqft_tol_pct", 25) / 100:
            return False
        if s_beds and c.get("beds") and abs(c["beds"] - s_beds) > prm.get("beds_tol", 1):
            return False
        if s_year and c.get("year_built") and abs(c["year_built"] - s_year) > prm.get("year_tol", 30):
            return False
        lo, hi = prm.get("lot_ratio", [0.4, 2.5])
        if s_prod == "detached" and s_lot and c.get("lot_sqft") and not (lo <= c["lot_sqft"] / s_lot <= hi):
            return False
        return True

    radii = [prm.get("radius_miles", 0.5), prm.get("fallback_radius_miles", 1.0), prm.get("max_radius_miles", 2.0)]
    chosen, radius_used, tier = [], radii[-1], "relaxed"
    for t_name, keep in (("strict", strict_ok), ("relaxed", lambda c: True)):
        for r in radii:
            inr = [c for c in pool if keep(c) and (c["distance_mi"] is None or c["distance_mi"] <= r)]
            if s_flags["waterfront"]:
                wet = [c for c in inr if c["flags"]["waterfront"]]
                if len(wet) >= need:
                    inr = wet
            if len(inr) >= need:
                chosen, radius_used, tier = inr, r, t_name
                break
        if chosen:
            break
    if not chosen:
        chosen, radius_used, tier = [c for c in pool if c["distance_mi"] is None or c["distance_mi"] <= radii[-1]], radii[-1], "relaxed"
    if tier == "relaxed" and chosen:
        notes.append("too few close matches: size, bed count or year built were relaxed")
    if s_flags["waterfront"] and chosen and not all(c["flags"]["waterfront"] for c in chosen):
        notes.append("this house is on the water; dry sales understate it")
    # $/sf outliers: the sale that is a different animal (a $3.1M new build next to a 1942 duplex)
    priced = [c for c in chosen if c.get("ppsf")]
    if len(priced) >= 4:
        med = statistics.median(c["ppsf"] for c in priced)
        lo, hi = prm.get("ppsf_band", [0.5, 1.7])
        keep = []
        for c in chosen:
            if c.get("ppsf") and not (lo * med <= c["ppsf"] <= hi * med):
                dropped.append((c, f"${c['ppsf']:,.0f}/sf is far off the group's ${med:,.0f}/sf"))
            else:
                keep.append(c)
        chosen = keep
    w = prm.get("weights", DEFAULTS["weights"])
    for c in chosen:
        c["similarity"] = round(_similarity(subject, c, w, s_sqft, s_beds, s_year, today), 3)
    chosen.sort(key=lambda c: (-c["similarity"], -date_key(c)))
    used = chosen[:want]
    for c in chosen[want:]:
        dropped.append((c, "further down the ranking"))
    used.sort(key=lambda c: -date_key(c))
    return {"comps": used, "dropped": dropped, "radius": radius_used, "tier": tier, "notes": notes, "product": s_prod, "flags": s_flags,
            "area": f"within {radius_used:g} mi" if subject.get("lat") else f"ZIP {subject.get('zip')}"}


def _similarity(s: dict[str, Any], c: dict[str, Any], w: dict[str, float], s_sqft: float, s_beds: float, s_year: float,
                today: datetime) -> float:
    """1.0 = a twin next door sold last week; each mismatch costs its weight × how far off it is (0..1)."""
    cost = 0.0
    d = c.get("distance_mi")
    cost += w.get("distance", 1) * min((d if d is not None else 1.0) / 2.0, 1.0)
    if s_sqft and c.get("sqft"):
        cost += w.get("sqft", 1) * min(abs(c["sqft"] - s_sqft) / s_sqft / 0.35, 1.0)
    age = age_days(c, today)
    cost += w.get("recency", 0.5) * min((age if age is not None else 180) / 365, 1.0)
    if s_year and c.get("year_built"):
        cost += w.get("year", 0.5) * min(abs(c["year_built"] - s_year) / 50, 1.0)
    if s_beds and c.get("beds"):
        cost += w.get("beds", 0.3) * min(abs(c["beds"] - s_beds) / 2, 1.0)
    total = sum(w.values()) or 1.0
    return max(0.0, 1.0 - cost / total)


# ---------- the number ----------

def arv(subject: dict[str, Any], comps: list[dict[str, Any]], prm: dict[str, Any] | None = None,
        mode: str = "renovated", apply_layout: bool = True) -> dict[str, Any]:
    """→ {arv, ppsf, basis, discount_pct, reasons}. mode renovated (ARV = what it sells for fixed up: the renovated sales'
    median $/sf when 3+ say so, else the upper half) or median (plain, used by the back-test)."""
    prm = prm or DEFAULTS
    out: dict[str, Any] = {"arv": None, "ppsf": None, "basis": "", "discount_pct": 0, "reasons": []}
    if product(subject) == "land":
        lots = [c for c in comps if c.get("lot_sqft") and c.get("sold_price")]
        if lots and subject.get("lot_sqft"):
            per = statistics.median(c["sold_price"] / c["lot_sqft"] for c in lots)
            out.update(arv=int(round(per * subject["lot_sqft"], -3)), ppsf=round(per, 2),
                       basis=f"median ${per:,.0f} per lot sf of {len(lots)} lot sales")
        return out
    sqft = subject.get("sqft") or 0
    priced = [c for c in comps if c.get("ppsf") or ppsf(c)]
    for c in priced:
        c.setdefault("ppsf", ppsf(c))
        c.setdefault("condition", condition_of(c.get("remarks")))
    if not priced or not sqft:
        return out
    reno = [c for c in priced if c["condition"] == "renovated"]
    vals = sorted(c["ppsf"] for c in priced)
    if mode == "median":
        psf, basis = statistics.median(vals), f"median $/sf of {len(priced)} sales"
    elif len(reno) >= 3:
        psf, basis = statistics.median(c["ppsf"] for c in reno), f"median $/sf of the {len(reno)} renovated sales"
    elif len(priced) >= 4:
        psf, basis = statistics.median(vals[len(vals) // 2:]), f"median $/sf of the top half of {len(priced)} sales (few say renovated)"
    else:
        psf, basis = statistics.median(vals), f"median $/sf of only {len(priced)} sales (thin)"
    value = psf * sqft
    disc = 0
    if apply_layout:
        f = flags(subject)
        pct = prm.get("layout_discount_pct", DEFAULTS["layout_discount_pct"])
        if f["split"]:
            disc += pct.get("split", 8)
            out["reasons"].append("main house + separate unit: a retail buyer pays less than for one house of this size")
        if f["unpermitted"]:
            disc += pct.get("unpermitted", 7)
            out["reasons"].append("unpermitted work in the remarks: buyers and lenders discount it until it is permitted")
        if f["sqft_suspect"] and not f["split"]:
            disc += pct.get("sqft_suspect", 5)
            out["reasons"].append("bed count is high for the size: likely a conversion or addition; the sf may not all be legal")
        if disc:
            value *= 1 - min(disc, 25) / 100
            basis += f", minus {min(disc, 25)}% for the layout"
    out.update(arv=int(round(value, -3)), ppsf=round(psf, 2), basis=basis, discount_pct=min(disc, 25))
    return out


# ---------- the grade ----------

def grade(subject: dict[str, Any], sel: dict[str, Any], today: datetime | None = None) -> dict[str, Any]:
    """A = build an offer on it; B = fine, read the notes; C = check the MLS first; D = a guess, not a number."""
    comps = sel.get("comps") or []
    priced = [c for c in comps if c.get("ppsf")]
    pts, why = 0, []
    n = len(comps)
    pts += 2 if n >= 6 else 1 if n >= 4 else 0
    if n < 4:
        why.append(f"only {n} sales")
    dist = [c["distance_mi"] for c in comps if c.get("distance_mi") is not None]
    md = statistics.median(dist) if dist else None
    pts += 2 if md is not None and md <= 0.5 else 1 if md is not None and md <= 1.0 else 0
    if md is not None and md > 1.0:
        why.append(f"sales are {md:g} mi away on median")
    if len(priced) >= 3:
        vals = [c["ppsf"] for c in priced]
        cv = statistics.pstdev(vals) / (statistics.mean(vals) or 1)
        pts += 2 if cv <= 0.12 else 1 if cv <= 0.22 else 0
        if cv > 0.22:
            why.append(f"$/sf spread is wide ({cv:.0%})")
    ages = [a for a in (age_days(c, today) for c in comps) if a is not None]
    if ages and statistics.median(ages) <= 180:
        pts += 1
    elif ages:
        why.append("most sales are over 6 months old")
    if len([c for c in priced if c.get("condition") == "renovated"]) >= 3:
        pts += 1
    else:
        why.append("fewer than 3 sales say renovated")
    if sel.get("tier") == "strict":
        pts += 1
    else:
        why.append("size / beds / year matching was relaxed")
    f = sel.get("flags") or {}
    if not (f.get("split") or f.get("unpermitted") or f.get("sqft_suspect")):
        pts += 1
    else:
        why.append("layout or permit flags on this house")
    if sel.get("product"):
        pts += 1
    else:
        why.append("property type unclear")
    letter = "A" if pts >= 9 else "B" if pts >= 7 else "C" if pts >= 5 else "D"
    if not subject.get("sqft") and sel.get("product") != "land":
        letter, why = "D", why + ["our size is unknown"]
    elif not sel.get("product") and letter in "AB":
        letter = "C"
    elif (f.get("split") or f.get("unpermitted")) and letter == "A":
        letter = "B"
    return {"grade": letter, "points": pts, "why": why}


def grade_line(g: dict[str, Any] | None) -> str:
    if not g:
        return ""
    meaning = {"A": "solid, build on it", "B": "fine, read the notes", "C": "check the MLS before offering",
               "D": "a guess, not a number"}[g["grade"]]
    return f"Comps grade {g['grade']} ({meaning})" + (f": {'; '.join(g['why'][:3])}" if g.get("why") else "")


def drop_summary(dropped: list[tuple[dict[str, Any], str]], limit: int = 5) -> list[str]:
    """'3 waterfront sales', '2 new builds' …: what was ruled out and why, grouped, for the card."""
    counts: dict[str, int] = {}
    for _, why in dropped:
        key = re.sub(r"\(.*?\)|\$[\d,]+/sf is far off the group's \$[\d,]+/sf|\d+%", "", why).strip()
        key = {"is far off the group's /sf": "far off the group's $/sf", "size off by": "wrong size"}.get(key, key) or why
        if key in ("further down the ranking", "other ZIP, no coordinates"):
            continue
        counts[key] = counts.get(key, 0) + 1
    out = [f"{n} × {k}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    return out[:limit]
