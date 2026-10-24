"""VALUATION + OFFER PRICE: comps → ARV, repairs by tier, MAO, spread."""
from __future__ import annotations

import json
import math
import re
from statistics import median
from typing import Any, Callable

from . import db
from .util import log, today_et

TIER_PATTERNS = [
    ("gut", r"\bgut\b|\bfire damage\b|\bcollapsed\b|\bcondemned\b|\bunsafe\b|\bteardown\b|\btear[\s-]down\b|\bshell\b|\bstructural\b|\bfoundation (issue|problem|crack)\b|\bmajor (repairs|renovation|rehab)\b|\bcash only\b.*\b(no (power|water|roof))"),
    ("heavy", r"\bneeds (a )?(new )?roof\b|\broof (leak|damage|needs)\b|\bwater damage\b|\bflood\w*\b|\bmold\b|\btermite\b|\bhurricane damage\b|\bstorm damage\b|\bfull(y)? (renovation|rehab)\b|\bcomplete (renovation|rehab)\b|\bextensive\b|\bhandyman special\b|\bcontractor'?s? special\b|\bfixer[\s-]?upper\b|\binvestor'?s? special\b|\bbring your contractor\b|\bopen permits?\b|\bcode violations?\b|\bunpermitted\b|\bsignificant (work|repairs)\b"),
    ("moderate", r"\bneeds? (some )?(tlc|work|updating|repairs?|renovation|love|attention)\b|\bdated\b|\boriginal (kitchen|bath|condition)\b|\bas[\s-]is\b|\bsweat equity\b|\bdiamond in the rough\b|\bgood bones\b|\brehab\b|\bfixer\b|\bupdating\b|\bdeferred maintenance\b|\bmotivated\b|\bestate\b|\bprobate\b"),
    ("cosmetic", r"\bcosmetic\b|\bpaint\b|\bcarpet\b|\bminor\b|\blight (work|updating)\b|\bwell[\s-]maintained\b|\bmove[\s-]in ready\b|\brenovated\b|\bremodeled\b|\bupdated\b|\bturn[\s-]?key\b|\bnew roof\b"),
]


def repair_tier(remarks: str, default: str = "moderate") -> str:
    text = remarks or ""
    for tier, pat in TIER_PATTERNS:
        if re.search(pat, text, re.I):
            return tier
    return default


def repair_cost(p: dict[str, Any], tier: str, cfg: Any) -> int:
    r = cfg["repairs"]
    if p.get("property_type") == "land":
        acres = (p.get("lot_sqft") or 0) / 43560
        return int(max(r.get("min_repair_budget", 0) / 2, acres * r.get("land_per_acre", 2500)))
    sqft = p.get("sqft") or 1200
    return int(max(r.get("min_repair_budget", 0), sqft * r[tier]))


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def select_comps(subject: dict[str, Any], candidates: list[dict[str, Any]], cfg: Any, radius: float | None = None) -> list[dict[str, Any]]:
    """candidates: dicts with sold_price, sqft, property_type, lat, lng, sold_date(optional), address."""
    v = cfg["valuation"]
    radius = radius or v["comp_radius_miles"]
    tol = v["comp_sqft_tolerance_pct"] / 100
    s_sqft = subject.get("sqft") or 0
    s_type = subject.get("property_type")
    out = []
    for c in candidates:
        if not c.get("sold_price"):
            continue
        if s_type and c.get("property_type") and c["property_type"] != s_type:
            continue
        if s_type != "land":
            if not c.get("sqft") or not s_sqft:
                continue
            if abs(c["sqft"] - s_sqft) / s_sqft > tol:
                continue
        if subject.get("lat") and subject.get("lng") and c.get("lat") and c.get("lng"):
            d = haversine_miles(subject["lat"], subject["lng"], c["lat"], c["lng"])
            if d > radius:
                continue
            c = {**c, "distance_mi": round(d, 2)}
        elif (c.get("zip") and subject.get("zip") and c["zip"] != subject["zip"]):
            continue
        out.append(c)
    return out


def arv_from_comps(subject: dict[str, Any], comps: list[dict[str, Any]], cfg: Any) -> tuple[int | None, float | None]:
    if not comps:
        return None, None
    if subject.get("property_type") == "land":
        ppa = [c["sold_price"] / max(c.get("lot_sqft") or 1, 1) * 43560 for c in comps if c.get("lot_sqft")]
        if not ppa:
            return None, None
        acres = (subject.get("lot_sqft") or 0) / 43560
        return int(median(ppa) * max(acres, 0.1)), round(median(ppa), 2)
    ppsf = median(c["sold_price"] / c["sqft"] for c in comps)
    return int(ppsf * (subject.get("sqft") or 0)), round(ppsf, 2)


def buy_ratio(p: dict[str, Any], arv: int, cfg: Any) -> dict[str, Any]:
    """Share of ARV to pay: the measured investor ratio for this county/ZIP/type/band, else formula.arv_multiplier."""
    from .market_ratios import lookup
    f = cfg["formula"]
    if f.get("ratio_source", "market") == "market":
        hit = lookup(cfg.root / f.get("market_ratios_file", "data/market_ratios.csv"), p.get("county"), p.get("zip"),
                     p.get("property_type"), arv, f.get("market_ratio_percentile", "median"), p.get("city"))
        if hit:
            return hit
    return {"ratio": f["arv_multiplier"], "source": f"flat {f['arv_multiplier']:.0%} (no market ratio for this property)"}


def preliminary_range(p: dict[str, Any], cfg: Any) -> dict[str, Any] | None:
    """INTERNAL screening range, never sent: ARV × (p25 … p75 investor ratio) − repairs − fee, with the assumptions behind it.
    p needs arv, repair_cost (and county/zip/property_type/city for the ratio lookup)."""
    from .market_ratios import lookup
    arv = p.get("arv")
    if not arv:
        return None
    f = cfg["formula"]
    repairs = int(p.get("repair_cost") or 0)
    path = cfg.root / f.get("market_ratios_file", "data/market_ratios.csv")
    lo = lookup(path, p.get("county"), p.get("zip"), p.get("property_type"), arv, "p25", p.get("city"))
    hi = lookup(path, p.get("county"), p.get("zip"), p.get("property_type"), arv, "p75", p.get("city"))
    if lo and hi:
        r_lo, r_hi, src = lo["ratio"], hi["ratio"], lo["source"]
    else:
        r_lo = r_hi = f["arv_multiplier"]
        src = f"flat {f['arv_multiplier']:.0%} (no market ratio)"
    low = max(int(round(arv * r_lo - repairs - f["assignment_fee"], -3)), 0)
    high = max(int(round(arv * r_hi - repairs - f["assignment_fee"], -3)), 0)
    method, n = p.get("arv_method") or "?", p.get("comp_count")
    uncertainty = "high" if method in ("estimate", "list_price", "land_default", "zip_resale_psf", "comps_thin") or not lo else \
                  "medium" if method == "comps_wide" else "lower"
    return {"low": low, "high": high, "uncertainty": uncertainty, "assumptions": [
        f"ARV ${arv:,.0f} from {method}" + (f" ({n} comps)" if n else ""),
        f"investor buy ratio {r_lo:.0%}–{r_hi:.0%} of resale ({src})",
        f"repairs ${repairs:,.0f} ({p.get('repair_tier') or 'default'} tier, estimated from the remarks; condition not verified)",
        f"minus ${f['assignment_fee']:,.0f} fee"]}


def compute_mao(arv: int, repairs: int, cfg: Any, ratio: float | None = None) -> int:
    f = cfg["formula"]
    r = f["arv_multiplier"] if ratio is None else ratio
    return int(round(arv * r - repairs - f["assignment_fee"], -2))


def value_property(p: dict[str, Any], cfg: Any, comps_fn: Callable[[dict[str, Any]], list[dict[str, Any]]] | None) -> dict[str, Any]:
    v = cfg["valuation"]
    tier = repair_tier(p.get("remarks") or "", cfg["repairs"].get("default_tier", "moderate"))
    repairs = repair_cost(p, tier, cfg)
    candidates = comps_fn(p) if comps_fn else []
    comps = select_comps(p, candidates, cfg)
    method = "comps"
    if len(comps) < v["min_comps"]:
        wider = select_comps(p, candidates, cfg, radius=v["fallback_radius_miles"])
        if len(wider) >= v["min_comps"]:
            comps, method = wider, "comps_wide"
    arv, ppsf = arv_from_comps(p, comps, cfg)
    if arv is None or len(comps) < v["min_comps"]:
        zpsf = None
        if p.get("sqft") and p.get("property_type") != "land":
            from .market_ratios import zip_resale_psf
            zpsf = zip_resale_psf(cfg.root / cfg["formula"].get("market_ratios_file", "data/market_ratios.csv"),
                                  p.get("county"), p.get("zip"), p.get("property_type"), p["sqft"])
        if p.get("estimate"):
            arv, method = int(p["estimate"]), "estimate"
        elif zpsf:
            arv, ppsf, method = int(zpsf * p["sqft"]), zpsf, "zip_resale_psf"
        elif p.get("property_type") == "land" and p.get("lot_sqft"):
            arv, method = int(v["land_arv_per_acre_default"] * p["lot_sqft"] / 43560), "land_default"
        elif arv is not None:
            method = "comps_thin"
        else:
            arv, method = int(p.get("list_price") or 0), "list_price"   # no better information: conservative
    adj = cfg.get("valuation.condition_adjustment_pct", {}).get(tier, 0)
    arv = int(arv * (1 + adj / 100))
    br = buy_ratio(p, arv, cfg)
    mao = compute_mao(arv, repairs, cfg, br["ratio"])
    lp = int(p.get("list_price") or 0)
    spread = round((lp - mao) / lp * 100, 1) if lp else None
    offer = int(round(mao * (1 - cfg["formula"].get("offer_below_mao_pct", 0) / 100), -2))
    return {"arv": arv, "arv_method": method, "comp_count": len(comps), "ppsf": ppsf, "repair_tier": tier, "repair_cost": repairs,
            "mao": mao, "buy_ratio": br["ratio"], "buy_ratio_source": br["source"], "list_price": lp, "spread_pct": spread, "offer_price": max(offer, 0),
            "comps": [{k: c.get(k) for k in ("address", "sold_price", "sqft", "sold_date", "distance_mi")} for c in comps[:12]]}


def run_valuation(conn, cfg: Any, comps_fn: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
                  min_score: int | None = None) -> dict[str, Any]:
    from .ranking import rate
    min_score = min_score if min_score is not None else cfg["ranking"]["score_min_for_valuation"]
    run_id = db.start_run(conn, "value", today_et())
    rows = conn.execute("SELECT p.*, s.score FROM properties p JOIN scores s ON s.property_id=p.id "
                        "LEFT JOIN valuations v ON v.property_id=p.id "
                        "WHERE s.score >= ? AND p.lead_status != 'EXCLUDED' AND (v.property_id IS NULL OR v.list_price != p.list_price OR v.valued_at < s.scored_at)",
                        (min_score,)).fetchall()
    n, errors = 0, 0
    for r in rows:
        p = db.row_to_dict(r)
        try:
            res = value_property(p, cfg, comps_fn)
        except Exception as e:  # noqa: BLE001
            errors += 1
            log.exception("valuation failed for %s: %s", p["address_key"], e)
            continue
        rating = rate(p["score"], res["list_price"], res["mao"], cfg, res["arv_method"])
        conn.execute("INSERT INTO valuations(property_id,arv,arv_method,comp_count,ppsf,repair_tier,repair_cost,mao,list_price,spread_pct,"
                     "offer_price,rating,comps,valued_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(property_id) DO UPDATE SET "
                     "arv=excluded.arv, arv_method=excluded.arv_method, comp_count=excluded.comp_count, ppsf=excluded.ppsf, "
                     "repair_tier=excluded.repair_tier, repair_cost=excluded.repair_cost, mao=excluded.mao, list_price=excluded.list_price, "
                     "spread_pct=excluded.spread_pct, offer_price=excluded.offer_price, rating=excluded.rating, comps=excluded.comps, valued_at=excluded.valued_at",
                     (p["id"], res["arv"], res["arv_method"], res["comp_count"], res["ppsf"], res["repair_tier"], res["repair_cost"],
                      res["mao"], res["list_price"], res["spread_pct"], res["offer_price"], rating, json.dumps(res["comps"]), db.iso()))
        n += 1
    counts = {"valued": n, "errors": errors}
    db.finish_run(conn, run_id, "ok" if not errors else "partial", counts)
    log.info("valuation: %s", counts)
    return counts


# --- comps source: Apify sold listings, cached per zip/type for the day -------------------------

def apify_comps_fn(cfg: Any, client, conn) -> Callable[[dict[str, Any]], list[dict[str, Any]]]:
    """Pull recently sold listings per county once per day via the Zillow actor, then filter locally per subject."""
    from .ingest import zillow
    cache: dict[str, list[dict[str, Any]]] = {}

    def load_county(county: str) -> list[dict[str, Any]]:
        key = f"sold:{county}:{today_et()}"
        if key in cache:
            return cache[key]
        row = conn.execute("SELECT comps FROM comps_cache WHERE key=?", (key,)).fetchone()
        if row:
            cache[key] = json.loads(row["comps"])
            return cache[key]
        sub_cfg = type(cfg)(raw={**cfg.raw, "market": {**cfg["market"], "counties": [county]}}, secrets=cfg.secrets, root=cfg.root)
        try:
            items = client.run_actor(cfg.get("ingest.apify.sold_actor"), zillow.actor_input(sub_cfg, sold=True),
                                     cfg.get("ingest.apify.max_items_per_actor", 3000), f"sold-{county}")
        except Exception as e:  # noqa: BLE001
            log.error("sold comps for %s failed: %s", county, e)
            items = []
        comps = []
        for it in items:
            l = zillow.map_item(it)
            if not l or not l.list_price:
                continue
            comps.append({"address": l.address_line, "sold_price": l.list_price, "sqft": l.sqft, "lot_sqft": l.lot_sqft,
                          "property_type": l.property_type, "lat": l.lat, "lng": l.lng, "zip": l.zip,
                          "sold_date": (l.price_history[-1].get("date") if l.price_history else None)})
        conn.execute("INSERT OR REPLACE INTO comps_cache(key,fetched_at,comps) VALUES(?,?,?)", (key, db.iso(), json.dumps(comps)))
        cache[key] = comps
        return comps

    def fn(p: dict[str, Any]) -> list[dict[str, Any]]:
        return load_county(p.get("county") or "") if p.get("county") else []

    return fn
