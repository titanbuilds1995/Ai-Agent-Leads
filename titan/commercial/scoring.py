"""Score (0–100), back-solved cap, offer anchor, cooked-books tag and evidence grade for one normalized LoopNet listing.

The listing dict comes from titan/commercial/loopnet.py (`normalize`) plus titan/commercial/documents.py (`doc_facts`). Every weight
and threshold is read from config.yaml → commercial (buyer box + scoring); nothing here is hardcoded to the buyer.

Rules, as the buyer wrote them (2026-11-08):
1. stated cap vs the 8% target (higher is better) — when the back-solved cap disagrees, the LOWER of the two is scored
2. back-solved cap = stated NOI / asking price; off by more than 50 bps → flag `cap_mismatch`
3. days on market and price reductions (longer / more cuts = more negotiable)
4. motivation language
5. lease quality: NNN > NN > gross; credit tenant > local; longer remaining term
6. small/local brokerage over institutional
7. OFFER ANCHOR = price at which stated NOI yields the target cap (NOI / 0.08)
COOKED BOOKS: CLEAN / SUSPECT / NEEDS-OM. EVIDENCE GRADE A–D is a separate column (documents.py decides the inputs)."""
from __future__ import annotations

from statistics import median
from typing import Any

from .fl_geo import is_south_florida

ASSET_FIT = {   # 0–1 of the asset_fit weight. NNN / single-tenant get the bump (low oversight); hands-on stays in.
    "nnn": 1.0, "retail": 0.6, "medical": 0.7, "industrial": 0.65, "office": 0.5, "multifamily": 0.5, "hotel": 0.4,
}
LEASE_SCORE = {"absolute nnn": 1.0, "nnn": 0.9, "nn": 0.65, "ground lease": 0.6, "modified gross": 0.45, "gross": 0.3, None: 0.25}
BROKERAGE_SCORE = {"small": 1.0, "regional": 0.55, "institutional": 0.0}


def _w(cfg: Any, key: str, default: float) -> float:
    return float(cfg.get(f"commercial.scoring.{key}", default))


def _box(cfg: Any, key: str, default: Any = None) -> Any:
    return cfg.get(f"commercial.buyer_box.{key}", default)


def back_solved_cap(noi: int | None, price: int | None) -> float | None:
    if not noi or not price or price <= 0:
        return None
    return round(100.0 * noi / price, 2)


def offer_anchor(noi: int | None, target_cap: float) -> int | None:
    """The price at which the stated NOI hits the target cap. The number the buyer opens with."""
    if not noi or target_cap <= 0:
        return None
    return int(round(noi / (target_cap / 100.0), -3))


def implied_noi(cap: float | None, price: int | None) -> int | None:
    if not cap or not price:
        return None
    return int(round(price * cap / 100.0))


def in_box(L: dict[str, Any], cfg: Any) -> tuple[bool | None, str]:
    """(True, '') in the box; (False, why) out; (None, why) unknown (price not disclosed)."""
    price = L.get("price")
    if not price:
        return None, "price not disclosed"
    if price < int(_box(cfg, "price_min", 0)) or price > int(_box(cfg, "price_max", 10**12)):
        return False, "price outside the box"
    if L.get("asset_class") not in set(_box(cfg, "asset_types_in", [])):
        return False, f"asset type out ({L.get('asset_class') or L.get('property_type') or 'unknown'})"
    if L.get("asset_class") == "multifamily" and L.get("units") and L["units"] < int(_box(cfg, "multifamily_min_units", 5)):
        return False, "multifamily under the unit minimum"
    cap = L.get("stated_cap")
    if cap is not None and cap < float(_box(cfg, "cap_min", 8.0)):
        return False, f"stated cap {cap}% under the floor"
    if L.get("business_only") or L.get("asset_class") in ("land", "business"):
        return False, "land / development / business only"
    if L.get("airport_ok") is False:
        return False, f"{L.get('airport_minutes')} min to the nearest airport ({L.get('airport_code')}), over the {_box(cfg, 'airport_max_minutes')} min limit"
    if L.get("quality") == "out":
        return False, "quality: " + "; ".join(L.get("quality_reasons") or [])
    return True, ""


def cooked_books(L: dict[str, Any], cfg: Any, ppsf_median: float | None = None) -> tuple[str, list[str]]:
    """CLEAN / SUSPECT / NEEDS-OM plus the reasons. NEEDS-OM when NOI or cap is missing entirely (OM request, not an offer)."""
    reasons: list[str] = []
    noi, cap, price = L.get("stated_noi"), L.get("stated_cap"), L.get("price")
    if noi is None or cap is None:
        missing = [x for x, v in (("NOI", noi), ("cap", cap)) if v is None]
        return "NEEDS-OM", [f"missing {' and '.join(missing)}"]
    if L.get("proforma"):
        reasons.append("cap/NOI described as pro forma / projected / upon stabilization")
    bs = back_solved_cap(noi, price)
    if bs is not None and abs(bs - cap) * 100 > _w(cfg, "cap_mismatch_bps", 50):
        reasons.append(f"stated cap {cap}% vs NOI/price {bs}%")
    occ, tenants = L.get("occupancy"), L.get("tenant_names") or []
    if occ == 100 and not tenants and not L.get("has_rent_roll"):
        reasons.append("100% occupancy with no rent roll or tenant names")
    er = L.get("expense_ratio")
    if er is not None and er < _w(cfg, "min_expense_ratio_non_nnn", 0.30) and (L.get("lease_type") or "") not in ("nnn", "absolute nnn"):
        reasons.append(f"implied expenses {er:.0%} of gross on a non-NNN asset")
    if occ is None or not L.get("lease_type"):
        missing = [x for x, v in (("occupancy", occ), ("lease type", L.get("lease_type"))) if not v]
        reasons.append("NOI stated but no " + " or ".join(missing))
    if L.get("asset_class") == "hotel" and not L.get("hotel_metrics"):
        reasons.append("hotel with no RevPAR / ADR / occupancy")
    ppsf = L.get("ppsf")
    if ppsf and ppsf_median and ppsf > _w(cfg, "suspect_ppsf_multiple", 2.0) * ppsf_median:
        reasons.append(f"${ppsf:,.0f}/SF vs county+type median ${ppsf_median:,.0f}")
    if reasons:
        return "SUSPECT", reasons
    if not L.get("trailing"):
        # consistent, but nothing says the NOI is actual/trailing: clean on the numbers, say so
        return "CLEAN", ["numbers consistent; NOI not labeled trailing (confirm with the T-12)"]
    return "CLEAN", ["trailing NOI, cap, occupancy and lease type stated and consistent"]


def evidence_grade(L: dict[str, Any]) -> tuple[str, str]:
    """A = trailing P&L / T-12 + rent roll downloaded and the numbers match the stated cap; B = OM/brochure with NOI + expense
    breakdown; C = only a cap / NOI figure in the listing text; D = nothing, or pro forma only."""
    docs = L.get("documents") or []
    kinds = {d.get("kind") for d in docs if d.get("downloaded")}
    doc_noi = L.get("doc_noi")
    if ({"t12", "pnl"} & kinds) and ("rent_roll" in kinds) and doc_noi and L.get("stated_cap") and L.get("price"):
        if abs(back_solved_cap(doc_noi, L["price"]) - L["stated_cap"]) * 100 <= 50:
            return "A", "T-12/P&L + rent roll on file; NOI matches the stated cap"
        return "B", "T-12 + rent roll on file but the NOI does not match the stated cap"
    if ({"om", "brochure"} & kinds) and (doc_noi or L.get("doc_expenses")) and L.get("doc_expenses"):
        return "B", "OM/brochure with NOI and an expense breakdown, no trailing financials"
    if L.get("proforma") and not L.get("trailing"):
        return "D", "pro forma / projected numbers only"
    if L.get("stated_noi") or L.get("stated_cap"):
        return "C", "cap/NOI figure in the listing text only, no backup"
    return "D", "no financials"


def _cap_points(L: dict[str, Any], cfg: Any) -> float:
    w = _w(cfg, "cap", 25)
    cap = L.get("stated_cap")
    bs = back_solved_cap(L.get("stated_noi"), L.get("price"))
    use = min(x for x in (cap, bs) if x is not None) if (cap is not None or bs is not None) else None
    if use is None:
        return 0.0
    floor, top = float(_box(cfg, "cap_min", 8.0)), float(_box(cfg, "cap_min", 8.0)) + 2.5
    if use < floor:
        return 0.0
    pts = w * (0.4 + 0.6 * min(1.0, (use - floor) / (top - floor)))
    if L.get("proforma"):
        pts *= 0.5
    return pts


def _dom_points(L: dict[str, Any], cfg: Any) -> float:
    w = _w(cfg, "dom_and_cuts", 12)
    dom, cuts = L.get("days_on_market") or 0, L.get("price_cuts") or 0
    base = 0.15 if dom < 30 else 0.4 if dom < 90 else 0.7 if dom < 180 else 0.85
    return w * min(1.0, base + 0.15 * min(cuts, 2))


def _motivation_points(L: dict[str, Any], cfg: Any) -> float:
    w = _w(cfg, "motivation", 12)
    sig = [s for s in (L.get("motivation") or []) if s not in ("value-add", "1031")]
    return w * min(1.0, len(sig) / 3.0)


def _lease_points(L: dict[str, Any], cfg: Any) -> float:
    w = _w(cfg, "lease_quality", 20)
    base = LEASE_SCORE.get(L.get("lease_type"), 0.25) * 0.6
    if L.get("credit_tenants"):
        base += 0.25
    elif L.get("tenancy") == "single":
        base += 0.1
    term = L.get("remaining_term_years")
    if term is not None:
        base += 0.15 if term >= 10 else 0.1 if term >= 5 else 0.03
    return w * min(1.0, base)


def _price_band_points(L: dict[str, Any], cfg: Any) -> float:
    w = _w(cfg, "price_band", 8)
    price = L.get("price") or 0
    sweet = int(_box(cfg, "sweet_spot_max", 6_000_000))
    if not price:
        return 0.0
    return w if price <= sweet else w * 0.35


def tier(L: dict[str, Any], cfg: Any) -> str:
    price = L.get("price") or 0
    return "reach" if price > int(_box(cfg, "sweet_spot_max", 6_000_000)) else "core"


def score(L: dict[str, Any], cfg: Any, ppsf_median: float | None = None) -> dict[str, Any]:
    """Everything the master sheet needs for one listing. Deterministic; the parts are returned for the card."""
    ok, why = in_box(L, cfg)
    target = float(_box(cfg, "target_cap", 8.0))
    parts = {
        "cap": round(_cap_points(L, cfg), 1), "dom_and_cuts": round(_dom_points(L, cfg), 1), "motivation": round(_motivation_points(L, cfg), 1),
        "lease_quality": round(_lease_points(L, cfg), 1),
        "brokerage": round(_w(cfg, "brokerage", 8) * BROKERAGE_SCORE.get(L.get("brokerage_size") or "small", 0.5), 1),
        "asset_fit": round(_w(cfg, "asset_fit", 10) * ASSET_FIT.get(L.get("asset_class") or "", 0.3), 1),
        "price_band": round(_price_band_points(L, cfg), 1),
        "region": round(_w(cfg, "region", 5) * (0.0 if is_south_florida(L.get("county"), _box(cfg, "south_florida")) else 1.0), 1),
    }
    total = int(round(min(100.0, sum(parts.values()))))
    tag, reasons = cooked_books(L, cfg, ppsf_median)
    grade, grade_why = evidence_grade(L)
    noi = L.get("stated_noi")
    anchor = offer_anchor(noi, target)
    anchor_basis = "stated NOI"
    if anchor is None and L.get("stated_cap") and L.get("price"):
        anchor, anchor_basis = offer_anchor(implied_noi(L["stated_cap"], L["price"]), target), "NOI implied from the stated cap"
    anchor_at_target = anchor
    if anchor and L.get("price") and anchor > L["price"]:
        # the ask already clears the target cap: open at (not above) the asking price
        anchor, anchor_basis = L["price"], anchor_basis + "; asking already clears the target cap"
    return {
        "score": total, "parts": parts, "in_box": ok, "box_note": why, "tier": tier(L, cfg),
        "back_solved_cap": back_solved_cap(noi, L.get("price")),
        "cap_mismatch": any("vs NOI/price" in r for r in reasons),
        "offer_anchor": anchor, "offer_anchor_at_target": anchor_at_target, "offer_anchor_basis": anchor_basis if anchor else "",
        "tag": tag, "tag_reasons": reasons, "evidence_grade": grade, "evidence_note": grade_why,
    }


def ppsf_medians(listings: list[dict[str, Any]], min_peers: int = 4) -> dict[tuple[str, str], float]:
    """Median $/SF by (county, asset_class) over the in-box pull, for the price-per-SF sanity check."""
    groups: dict[tuple[str, str], list[float]] = {}
    for L in listings:
        if L.get("ppsf") and L.get("county") and L.get("asset_class"):
            groups.setdefault((L["county"], L["asset_class"]), []).append(float(L["ppsf"]))
    return {k: median(v) for k, v in groups.items() if len(v) >= min_peers}
