"""What investors actually pay, as a share of resale, by county / ZIP / property type / price band.

Source: Rodrigo's WhatsApp sales engine (github.com/titanbuilds1995/titan-whatsapp-sales, data/market_ratios.csv): every
buy-and-resell pair mined from Miami-Dade and Broward deed records (iBuyers and bulk sales stripped). Copied to
data/market_ratios.csv; refresh it from that repo (`python3 export_intelligence.py` there).

Lookup ladder (same as that engine's underwrite.py): ZIP + band → ZIP any band → city → county + band → county. County is in every
step, so a Broward property never borrows a Miami-Dade number. Palm Beach has no flip history (its roll keeps one sale per
parcel), so it borrows the Broward, then Miami-Dade, county-wide ratio — the ratio is the stable half of the model."""
from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Any

COUNTY_KEY = {"Miami-Dade": "miami-dade", "Broward": "broward", "Palm Beach": "palm-beach"}
TYPE_KEY = {"single_family": "sfr", "townhouse": "sfr", "multi_family": "multifamily", "condo": "condo"}
BANDS = [(0, 300_000, "<300k"), (300_000, 500_000, "300-500k"), (500_000, 750_000, "500-750k"),
         (750_000, 1_000_000, "750k-1M"), (1_000_000, 2_000_000, "1-2M")]
PCT = {"p25": "pct_of_resale_p25", "median": "pct_of_resale_median", "p75": "pct_of_resale_p75"}
DONORS = ("broward", "miami-dade")


def band_for(value: float) -> str:
    for lo, hi, name in BANDS:
        if lo <= value < hi:
            return name
    return "2M+"


@lru_cache(maxsize=4)
def _load(path: str) -> dict[tuple[str, str, str, str], dict[str, str]]:
    with open(path, newline="") as fh:
        return {(r["county"], r["zip"], r["prop_type"], r["price_band"]): r for r in csv.DictReader(fh)}


def lookup(path: Path, county: str | None, zip_code: str | None, prop_type: str | None, resale: float,
           percentile: str = "median", city: str | None = None) -> dict[str, Any] | None:
    """→ {"ratio": 0.724, "source": "broward 33023 sfr 300-500k (zip, n=174)", ...} or None when nothing applies."""
    if not Path(path).exists():
        return None
    rows = _load(str(path))
    c, t = COUNTY_KEY.get(county or ""), TYPE_KEY.get(prop_type or "")
    if not c or not t:
        return None
    band, z = band_for(resale or 0), (zip_code or "")[:5]
    city_key = f"CITY:{(city or '').strip().upper()}"
    if c in DONORS:
        tries = [(c, z, t, band), (c, z, t, "*"), (c, city_key, t, "*"), (c, "*", t, band), (c, "*", t, "*")]
    else:
        tries = [(d, "*", t, "*") for d in DONORS]
    col = PCT.get(percentile, PCT["median"])
    for key in tries:
        r = rows.get(key)
        if r and r.get(col):
            borrowed = "" if key[0] == c else f", borrowed from {key[0]}"
            return {"ratio": round(float(r[col]) / 100, 4), "percentile": percentile, "sample_size": int(r["sample_size"] or 0),
                    "level": r["geography_level"], "hold_months": float(r["typical_hold_months"] or 0) or None,
                    "source": f"{key[0]} {key[1]} {key[2]} {key[3]} ({r['geography_level']}, n={r['sample_size']}{borrowed})"}
    return None



def zip_resale_psf(path: Path, county: str | None, zip_code: str | None, prop_type: str | None, sqft: float,
                   min_sample: int = 10) -> float | None:
    """Median resale $/sf inside this ZIP only (price per foot never leaves its ZIP: it spans ~5x within a county).
    ZIP rows are per price band; like underwrite.py, take the band the resulting value actually lands in."""
    if not Path(path).exists() or not sqft:
        return None
    c, t, z = COUNTY_KEY.get(county or ""), TYPE_KEY.get(prop_type or ""), (zip_code or "")[:5]
    rows = [r for (rc, rz, rt, _), r in _load(str(path)).items()
            if (rc, rz, rt) == (c, z, t) and r.get("resale_psf_median") and int(r.get("psf_sample_size") or 0) >= min_sample]
    if not rows:
        return None
    for r in rows:
        if band_for(float(r["resale_psf_median"]) * sqft) == r["price_band"]:
            return float(r["resale_psf_median"])
    return float(max(rows, key=lambda r: int(r["psf_sample_size"]))["resale_psf_median"])
