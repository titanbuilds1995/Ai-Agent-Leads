"""Income screen: what round one taught us (Rodrigo, 2026-11-18: "look for more commercial properties tomorrow … with everything that we
learned from this round"). Read from the 208 broker answers on the 1031 boards, Nov 9–18:
- 91 answers said the building is vacant, an owner-user or a lease-up, and some brokers were annoyed we had not read the listing
  ("our listing clearly says that this is a vacant building"). A listing with no stated cap whose text says so is out.
- 11 were a business sold with the real estate (car wash, restaurant, tiki bar, karting) with no lease. Out unless a lease is stated.
- 15 were development / redevelopment / land value. Out.
- Every hotel / short-term rental that sent books (5 of 5) fell from a stated 8.3–10.2% to about 5.8–7.6% once a manager and the
  tax reset were in. A hotel's stated cap is screened at `hotel_noi_factor` (0.75) of itself.
- Four brokers said an 8 cap does not exist for small Miami multifamily: `multifamily_min_units_by_county`.
Each rule is a plain regex on the listing's own text, so the box note says exactly why a listing left."""
from __future__ import annotations

import re
from typing import Any

NOT_INCOME_RE = re.compile(
    r"delivered vacant|vacant (?:building|property|at closing|upon closing|possession)|(?:100%|fully|entirely|completely) vacant|"
    r"owner[- ]user|owner[- ]occupant|user opportunity|(?:ideal|perfect|great) for (?:an? )?(?:owner|user|end[- ]user)|"
    r"ready for (?:immediate )?occupancy|available for lease|for lease at \$|build[- ]to[- ]suit|under construction|"
    r"new construction|to be built|proposed (?:building|development)|shell space|lease[- ]up|pre[- ]leasing", re.I)
INCOME_RE = re.compile(
    r"100% (?:leased|occupied)|fully (?:leased|occupied)|\bnnn lease|net lease|tenant(?:s)? in place|in[- ]place (?:income|noi|tenants?)|"
    r"leased (?:to|through|until)|rent roll|(?:long|short)[- ]term lease|lease (?:expires|expiration|term)|years? (?:remaining|left) on|"
    r"stabilized|occupied by|corporate guarantee|investment property|income[- ]producing|cash[- ]flowing", re.I)
BUSINESS_RE = re.compile(
    r"car wash|restaurant (?:and|&|with|plus) (?:the )?(?:real estate|property|building|business)|business (?:included|for sale|and real estate|"
    r"\+ real estate|is included)|includes? (?:the )?(?:business|operating business|liquor license|equipment)|turn[- ]?key (?:business|operation)|"
    r"owner[- ]operated|owner[- ]run|going concern|liquor license|go[- ]?kart|karting|tiki bar|bar and grill|night ?club", re.I)
LEASE_RE = re.compile(r"\bleased to|\bnnn\b|triple net|ground lease|tenant pays|lease (?:in place|expires|term)|absolute net|\bnet lease", re.I)
DEVELOPMENT_RE = re.compile(r"redevelopment|land value|development (?:site|opportunity|play|potential)|assemblage|highest and best use|"
                            r"upzon|entitled for|entitlements?|ground[- ]up|tear[- ]?down|priced (?:at|below) land", re.I)


def _rule(cfg: Any, key: str, default: Any) -> Any:
    v = (cfg.get("commercial.buyer_box", {}) or {}).get(key)
    return default if v is None else v


def income_screen(L: dict[str, Any], cfg: Any) -> str | None:
    """Why this listing is not a passive income property for the 1031 partner (None = it may be). Starts with 'income:'."""
    text = L.get("text") or " ".join(str(x) for x in (L.get("description"), *(L.get("highlights") or [])) if x)
    asset = L.get("asset_class")
    cap = L.get("stated_cap")
    if cap is None:
        m = NOT_INCOME_RE.search(text)
        if m and not INCOME_RE.search(text):
            return f"income: not an income property ({m.group(0)})"
    m = DEVELOPMENT_RE.search(text)
    if m and not INCOME_RE.search(text):
        return f"income: development / land value ({m.group(0)})"
    if asset != "hotel":
        m = BUSINESS_RE.search(text)
        if m and not LEASE_RE.search(text):
            return f"income: a business sold with the real estate, no lease ({m.group(0)})"
    if asset == "hotel" and cap is not None:
        factor = float(_rule(cfg, "hotel_noi_factor", 0.75))
        floor = float(_rule(cfg, "cap_min", 8.0))
        if cap * factor < floor:
            return (f"income: hotel stated {cap}% is about {cap * factor:.1f}% once a manager and the tax reset are in "
                    f"(every hotel's books in round one fell that far)")
    by_county = _rule(cfg, "multifamily_min_units_by_county", {}) or {}
    need = by_county.get(L.get("county") or "")
    if asset == "multifamily" and need and L.get("units") and L["units"] < int(need):
        return f"income: {L['units']} units in {L.get('county')} (brokers say 8 caps do not exist there under {need} units)"
    return None
