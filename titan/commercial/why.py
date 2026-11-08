"""One plain line per listing: why the engine thinks it fits the 1031 buyer (shown in the email and on the site)."""
from __future__ import annotations

from typing import Any

from .fl_geo import is_south_florida

ASSET_WORDS = {"nnn": "single-tenant net lease", "retail": "retail center", "medical": "medical building", "hotel": "hotel",
               "industrial": "industrial / flex", "office": "office building", "multifamily": "multifamily"}


def why_fits(L: dict[str, Any], cfg: Any) -> str:
    box = cfg.get("commercial.buyer_box", {})
    bits: list[str] = []
    cap = L.get("stated_cap")
    if cap:
        bits.append(f"{cap:.2f}% stated cap" + (", above the floor" if cap >= float(box.get("cap_min", 8)) + 0.75 else ""))
    else:
        bits.append("cap not on the listing (books requested)")
    price = L.get("price") or 0
    if price and price <= int(box.get("sweet_spot_max", 6_000_000)):
        bits.append("in the $3.5M–$6M sweet spot")
    elif price:
        bits.append("reach tier (over $6M)")
    kind = ASSET_WORDS.get(L.get("asset_class") or "", L.get("property_type") or "commercial")
    if L.get("credit_tenants"):
        kind += f" with {L['credit_tenants'][0].title()}"
    bits.append(kind)
    if L.get("lease_type") in ("nnn", "absolute nnn"):
        bits.append("NNN lease, low oversight")
    if L.get("remaining_term_years") and L["remaining_term_years"] >= 8:
        bits.append(f"{L['remaining_term_years']:g} years of term left")
    if not is_south_florida(L.get("county"), box.get("south_florida")) and L.get("county"):
        bits.append(f"{L['county']} County, outside the South Florida cap squeeze")
    sig = [s for s in (L.get("motivation") or []) if s not in ("value-add", "1031")]
    if sig:
        bits.append("seller signals: " + ", ".join(sig))
    if (L.get("days_on_market") or 0) >= 90 and not L.get("dom_basis"):
        bits.append(f"{L['days_on_market']} days on market")
    if L.get("brokerage_size") == "small":
        bits.append("small brokerage, easy conversation")
    if L.get("tag") == "SUSPECT":
        bits.append("numbers need checking: " + "; ".join((L.get("tag_reasons") or [])[:2]))
    return ". ".join(b[0].upper() + b[1:] for b in bits[:6]) + "."


def photo_url(L: dict[str, Any], size: str = "116") -> str | None:
    raw = L.get("raw") or {}
    url = L.get("photo") or raw.get("photo") or (raw.get("images") or raw.get("photos") or [None])[0]
    if not url:
        return None
    return str(url).replace("{s}", size)
