"""Rank a thin MLS hot sheet (no remarks, no agent contact) so Rodrigo can pull the best rows back out of Matrix with a full
export. Signals available on a hot sheet: size of the latest price cut, back-on-market, and asking $/sqft against the median
for the same county + property type on the same sheet. Output: a CSV plus a comma list of MLS numbers to paste into Matrix."""
from __future__ import annotations

import csv
import statistics
from pathlib import Path
from typing import Any, Iterable

from .ingest.normalize import Listing, passes_filters

COLUMNS = ["rank", "points", "mls", "address", "county", "type", "price", "cut_pct", "back_on_market", "beds", "baths", "sqft",
           "ppsf", "ppsf_vs_median_pct", "why"]


def _cut_pct(l: Listing) -> float:
    if l.original_list_price and l.list_price and l.original_list_price > l.list_price:
        return round(100 * (1 - l.list_price / l.original_list_price), 1)
    return 0.0


def rank(listings: Iterable[Listing], cfg: Any, top: int | None = None) -> list[dict[str, Any]]:
    kept = [l for l in listings if passes_filters(l, cfg)[0]]
    medians: dict[tuple[str, str], float] = {}
    groups: dict[tuple[str, str], list[float]] = {}
    for l in kept:
        if l.sqft and l.list_price and l.property_type != "land":
            groups.setdefault((l.county or "", l.property_type or ""), []).append(l.list_price / l.sqft)
    for k, v in groups.items():
        if len(v) >= 5:
            medians[k] = statistics.median(v)
    rows = []
    for l in kept:
        cut = _cut_pct(l)
        ppsf = round(l.list_price / l.sqft) if l.sqft and l.list_price and l.property_type != "land" else None
        med = medians.get((l.county or "", l.property_type or ""))
        below = round(100 * (1 - ppsf / med), 1) if ppsf and med else None
        points = 2 * min(cut, 20) + (8 if l.back_on_market else 0) + (0.6 * min(max(below, 0), 50) if below else 0)
        if l.property_type == "multi_family":
            points += 4
        why = []
        if cut:
            why.append(f"cut {cut}%")
        if l.back_on_market:
            why.append("back on market")
        if below and below > 0:
            why.append(f"$/sqft {below}% under {l.county} {l.property_type.replace('_', ' ')} median")
        rows.append({"points": round(points, 1), "mls": l.source_id, "address": l.street, "county": l.county,
                     "type": l.property_type, "price": l.list_price, "cut_pct": cut, "back_on_market": "yes" if l.back_on_market else "",
                     "beds": l.beds, "baths": l.baths, "sqft": l.sqft, "ppsf": ppsf, "ppsf_vs_median_pct": below,
                     "why": "; ".join(why)})
    rows.sort(key=lambda r: (-r["points"], r["price"] or 0))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows[:top] if top else rows


def write(rows: list[dict[str, Any]], out_csv: Path, paste_n: int = 100) -> Path:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    paste = out_csv.with_suffix(".mls.txt")
    paste.write_text(",".join(r["mls"] for r in rows[:paste_n] if r["mls"]) + "\n")
    return paste
