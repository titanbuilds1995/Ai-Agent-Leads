"""Step 1: every sold / listed house within a radius of the deal, from realtor.com through HomeHarvest (free, Mac only).

One row per house with the listing agent's contact and a first read of the remarks (INVESTOR_BUY: sold as-is / cash only /
TLC; FLIP_RESALE: sold renovated). The owner / buyer / seller columns are left blank for the Chrome step to fill in."""
from __future__ import annotations

import csv
import math
import re
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from ..ingest import realtor
from ..ingest.homeharvest_src import _sold_price
from ..util import log

TYPES = ["single_family", "townhomes", "multi_family", "duplex_triplex"]

INVESTOR_BUY_RE = re.compile(r"\b(cash only|cash buyers?|investors?|as[- ]is|tlc|handyman|fixer|needs? (work|updating|repairs?)|"
                             r"bring your (contractor|tools)|estate sale|probate|rehab|tear ?down|flip|great bones|sweat equity)\b", re.I)
FLIP_RE = re.compile(r"\b(fully|completely|newly|totally|beautifully|tastefully) (renovated|remodeled|updated|redone)|"
                     r"\b(renovated|remodeled) (from top to bottom|throughout)|\bbrand new (kitchen|roof|a/?c|bathrooms?|everything)|"
                     r"\bmove[- ]in ready\b.*\bnew\b|\bnew (roof|kitchen|impact windows)\b.*\bnew (roof|kitchen|bath|a/?c|flooring)", re.I)

# The Chrome step fills these in (Property Appraiser, Redfin "Bought with", the Clerk for a mortgage).
CHROME_COLS = ["buyer_name", "buyer_type", "buyer_mailing", "deed_date", "deed_price", "seller_name", "seller_type",
               "buyer_agent", "buyer_agent_phone", "buyer_agent_email", "buyer_agent_office", "cash", "notes"]
COLS = ["address", "city", "zip", "status", "miles", "sold_date", "sold_price", "list_price", "sqft", "psf", "beds", "baths",
        "year_built", "signal", "listing_agent", "listing_agent_phone", "listing_agent_email", "listing_office", "listing_url",
        "remarks", *CHROME_COLS]

Scrape = Callable[..., list[dict[str, Any]]]


def _scraper() -> Scrape:
    from homeharvest import scrape_property
    return scrape_property


def miles(a: tuple[float, float] | None, b: tuple[float | None, float | None]) -> float | None:
    if not a or b[0] is None or b[1] is None:
        return None
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return round(3958.8 * 2 * math.asin(math.sqrt(h)), 2)


def signal(remarks: str) -> str:
    if FLIP_RE.search(remarks or ""):
        return "FLIP_RESALE"
    if INVESTOR_BUY_RE.search(remarks or ""):
        return "INVESTOR_BUY"
    return ""


def slug(address: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", address.split(",")[0].lower()).strip("_")


def _subject_point(address: str, scrape: Scrape) -> tuple[float, float] | None:
    for lt in ("sold", "pending", "for_sale"):
        try:
            for it in scrape(address, listing_type=lt, return_type="raw", limit=5, extra_property_data=False) or []:
                l = realtor.map_item(it)
                if l and l.lat and l.lng:
                    return l.lat, l.lng
        except Exception as e:  # noqa: BLE001
            log.warning("buyers: subject lookup (%s) failed: %s", lt, e)
    return None


def pull(address: str, radius: float = 1.5, months: int = 24, out_dir: Path | str = "reports/buyers",
         scrape: Scrape | None = None) -> Path:
    """Write reports/buyers/<slug>/area_sales.csv. Keeps any column the Chrome step already filled in on an earlier run."""
    scrape = scrape or _scraper()
    folder = Path(out_dir) / slug(address)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "area_sales.csv"
    kept = {r["address"].upper(): r for r in read_csv(path)}
    print(f"Finding {address} on realtor.com…", flush=True)
    here = _subject_point(address, scrape)
    print("  found it" if here else "  not found (distances will be blank, the pull still works)", flush=True)
    rows: dict[str, dict[str, Any]] = {}
    for lt, extra in (("sold", {"past_days": int(timedelta(days=30 * months).days)}), ("pending", {}), ("for_sale", {})):
        print(f"Pulling {lt.replace('_', ' ')} homes within {radius} miles… (can take a few minutes)", flush=True)
        try:
            items = scrape(address, listing_type=lt, radius=radius, return_type="raw", property_type=TYPES,
                           limit=3000, extra_property_data=False, **extra) or []
        except Exception as e:  # noqa: BLE001
            log.error("buyers: %s pull around %s failed: %s", lt, address, e)
            continue
        print(f"  {len(items)} found", flush=True)
        for it in items:
            l = realtor.map_item(it) if isinstance(it, dict) else None
            if not l:
                continue
            sold = _sold_price(it) if lt == "sold" else None
            price = sold or l.list_price
            key = l.street.upper()
            if key in rows and lt != "sold":
                continue  # the sale is the more useful row; a relisted flip shows up as its own sold + for_sale pair only once
            row = {"address": l.street, "city": l.city, "zip": l.zip, "status": lt, "miles": miles(here, (l.lat, l.lng)),
                   "sold_date": (it.get("last_sold_date") or (it.get("description") or {}).get("sold_date") or "")[:10] if lt == "sold" else "",
                   "sold_price": sold or "", "list_price": l.list_price or "", "sqft": l.sqft or "",
                   "psf": round(price / l.sqft) if price and l.sqft else "", "beds": l.beds or "", "baths": l.baths or "",
                   "year_built": l.year_built or "", "signal": signal(l.remarks), "listing_agent": l.agent_name or "",
                   "listing_agent_phone": l.agent_phone or l.agent_phone_raw or "", "listing_agent_email": l.agent_email or "",
                   "listing_office": l.brokerage or "", "listing_url": l.listing_url or "",
                   "remarks": re.sub(r"\s+", " ", l.remarks or "")[:300]}
            for c in CHROME_COLS:
                row[c] = (kept.get(key) or {}).get(c, "")
            rows[key] = row
    for key, old in kept.items():  # rows the Chrome step added (off-MLS buys from the appraiser) stay
        rows.setdefault(key, {c: old.get(c, "") for c in COLS})
    ordered = sorted(rows.values(), key=lambda r: (r["miles"] == "" or r["miles"] is None, r["miles"] or 0))
    write_csv(path, ordered, COLS)
    log.info("buyers: %d houses around %s → %s", len(ordered), address, path)
    return path


def read_csv(path: Path) -> list[dict[str, str]]:
    if not Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(f)]


def write_csv(path: Path, rows: list[dict[str, Any]], cols: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in cols})
