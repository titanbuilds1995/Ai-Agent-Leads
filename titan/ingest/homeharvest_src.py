"""Free Realtor.com source via HomeHarvest (github.com/ZacharyHampton/HomeHarvest, pinned 0.8.18, code read 2026-10-23:
it only calls realtor.com's own GraphQL API). No Apify cost. Realtor.com blocks most cloud/datacenter IPs, so this runs
on the Mac; from a cloud container the source fails cleanly and the other sources carry on.

`return_type="raw"` hands back realtor.com's snake_case records (location.address.*, description.*, advertisers[]),
the shape `realtor.map_item` was written for, so both Realtor paths share one mapper."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from ..util import log, today_et
from . import realtor
from .normalize import Listing

# config.yaml market.property_types → HomeHarvest SearchPropertyType values
TYPES = {"single_family": ["single_family"], "townhouse": ["townhomes"], "multi_family": ["multi_family", "duplex_triplex"],
         "land": ["land"], "condo": ["condos"]}

Scrape = Callable[..., list[dict[str, Any]]]


def _scraper() -> Scrape:
    from homeharvest import scrape_property  # lazy: optional dependency, only needed when this source runs
    return scrape_property


def _types(cfg: Any) -> list[str]:
    out: list[str] = []
    for t in cfg["market"]["property_types"]:
        out += [x for x in TYPES.get(t, []) if x not in out]
    return out


def _save_raw(cfg: Any, name: str, items: list[dict[str, Any]]) -> None:
    try:
        d = cfg.path("raw")
        d.mkdir(parents=True, exist_ok=True)
        (Path(d) / f"{today_et()}_homeharvest_{name}.json").write_text(json.dumps(items, default=str))
    except Exception as e:  # noqa: BLE001 — a raw dump is a debugging aid, never fatal
        log.warning("homeharvest: could not save raw %s: %s", name, e)


def fetch_for_sale(cfg: Any, scrape: Scrape | None = None) -> tuple[list[Listing], dict[str, Any]]:
    """Per county: listed in the lookback window + updated in it (catches price cuts on older listings). Deduped."""
    scrape = scrape or _scraper()
    m, hh = cfg["market"], cfg.get("ingest.homeharvest", {}) or {}
    days = m["lookback_days"]
    common = dict(listing_type="for_sale", return_type="raw", property_type=_types(cfg), price_min=m["price_min"],
                  price_max=m["price_max"], exclude_pending=True, limit=hh.get("limit_per_query", 3000),
                  extra_property_data=hh.get("extra_property_data", True), proxy=hh.get("proxy") or None)
    seen: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for county in m["counties"]:
        loc = f"{county} County, FL"
        for label, kw in (("new", {"past_days": days}), ("updated", {"updated_in_past_hours": days * 24})):
            try:
                items = scrape(loc, **common, **kw) or []
            except Exception as e:  # noqa: BLE001 — one county/query failing must not drop the rest
                errors[f"{county}:{label}"] = f"{type(e).__name__}: {e}"[:200]
                log.warning("homeharvest %s %s failed: %s", county, label, e)
                continue
            for it in items:
                if isinstance(it, dict):
                    seen.setdefault(str(it.get("property_id") or it.get("listing_id") or id(it)), it)
    items = list(seen.values())
    if not items and errors and len(errors) == 2 * len(m["counties"]):
        raise RuntimeError(f"every HomeHarvest query failed (realtor.com blocks cloud IPs; run on the Mac): {next(iter(errors.values()))}")
    _save_raw(cfg, "for_sale", items)
    from .pipeline import map_items
    listings, extra = map_items("homeharvest", items, realtor.map_item)
    for l in listings:
        l.source = "homeharvest"
    if errors:
        extra["errors"] = errors
    return listings, extra


def comps_fn(cfg: Any, conn, scrape: Scrape | None = None) -> Callable[[dict[str, Any]], list[dict[str, Any]]]:
    """Sold comps per county, fetched once per day (comps_cache), filtered per subject by valuation.select_comps."""
    from .. import db
    cache: dict[str, list[dict[str, Any]]] = {}
    months = cfg.get("valuation.comp_months", 6)

    def load(county: str) -> list[dict[str, Any]]:
        key = f"hh-sold:{county}:{today_et()}"
        if key in cache:
            return cache[key]
        row = conn.execute("SELECT comps FROM comps_cache WHERE key=?", (key,)).fetchone()
        if row:
            cache[key] = json.loads(row["comps"])
            return cache[key]
        try:
            items = (scrape or _scraper())(f"{county} County, FL", listing_type="sold", return_type="raw",
                                           past_days=int(timedelta(days=30 * months).days), property_type=_types(cfg),
                                           limit=cfg.get("ingest.homeharvest.limit_per_query", 3000), extra_property_data=False)
        except Exception as e:  # noqa: BLE001
            log.error("homeharvest sold comps for %s failed: %s", county, e)
            cache[key] = []
            return []
        comps = []
        for it in items or []:
            l = realtor.map_item(it) if isinstance(it, dict) else None
            price = _sold_price(it) if l else None
            if not l or not price:
                continue
            comps.append({"address": l.address_line, "sold_price": price, "sqft": l.sqft, "lot_sqft": l.lot_sqft,
                          "property_type": l.property_type, "lat": l.lat, "lng": l.lng, "zip": l.zip,
                          "sold_date": it.get("last_sold_date") or (it.get("description") or {}).get("sold_date")})
        conn.execute("INSERT OR REPLACE INTO comps_cache(key,fetched_at,comps) VALUES(?,?,?)", (key, db.iso(), json.dumps(comps)))
        cache[key] = comps
        return comps

    return lambda p: load(p["county"]) if p.get("county") else []


def _sold_price(it: dict[str, Any]) -> int | None:
    from .normalize import to_int
    return to_int((it.get("description") or {}).get("sold_price")) or to_int(it.get("last_sold_price"))
