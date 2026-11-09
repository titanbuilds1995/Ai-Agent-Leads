"""Florida commercial airports (scheduled passenger service) and the drive-time screen (Rodrigo, 2026-11-09: "no further than 45
minutes from a local airport"). Straight-line miles × a road factor ≈ drive minutes; the numbers are in config."""
from __future__ import annotations

import math
from typing import Any

AIRPORTS = [   # (code, name, lat, lng)
    ("MIA", "Miami International", 25.7959, -80.2870), ("FLL", "Fort Lauderdale–Hollywood", 26.0742, -80.1506),
    ("PBI", "Palm Beach International", 26.6832, -80.0956), ("MCO", "Orlando International", 28.4312, -81.3081),
    ("SFB", "Orlando Sanford", 28.7776, -81.2375), ("TPA", "Tampa International", 27.9755, -82.5332),
    ("PIE", "St. Pete–Clearwater", 27.9102, -82.6874), ("RSW", "Southwest Florida (Fort Myers)", 26.5362, -81.7552),
    ("SRQ", "Sarasota–Bradenton", 27.3954, -82.5544), ("PGD", "Punta Gorda", 26.9202, -81.9905),
    ("JAX", "Jacksonville International", 30.4941, -81.6879), ("DAB", "Daytona Beach", 29.1799, -81.0581),
    ("MLB", "Melbourne Orlando", 28.1028, -80.6453), ("GNV", "Gainesville Regional", 29.6900, -82.2718),
    ("TLH", "Tallahassee", 30.3965, -84.3503), ("PNS", "Pensacola", 30.4734, -87.1866),
    ("VPS", "Destin–Fort Walton Beach", 30.4832, -86.5254), ("ECP", "Northwest Florida Beaches (Panama City)", 30.3571, -85.7954),
    ("EYW", "Key West", 24.5561, -81.7596), ("VRB", "Vero Beach Regional", 27.6556, -80.4179),
    ("PFN", "Panama City (Bay County)", 30.2121, -85.6828), ("LAL", "Lakeland Linder", 27.9889, -82.0186),
    ("OCF", "Ocala International", 29.1726, -82.2242), ("APF", "Naples Municipal", 26.1526, -81.7753),
    ("MTH", "Florida Keys Marathon", 24.7261, -81.0514), ("SGJ", "St. Augustine (Northeast Florida)", 29.9592, -81.3398),
    ("OPF", "Miami-Opa Locka Executive", 25.9070, -80.2784), ("FXE", "Fort Lauderdale Executive", 26.1973, -80.1707),
    ("BCT", "Boca Raton", 26.3785, -80.1077), ("SUA", "Witham Field (Stuart)", 27.1817, -80.2211), ("FPR", "Treasure Coast Intl (Fort Pierce)", 27.4951, -80.3683),
    ("ORL", "Orlando Executive", 28.5455, -81.3329), ("KIS", "Kissimmee Gateway", 28.2898, -81.4371),
]


def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest(lat: float | None, lng: float | None, minutes_per_mile: float = 1.4) -> dict[str, Any] | None:
    """The closest airport with straight-line miles and an estimated drive time."""
    if lat is None or lng is None:
        return None
    best = None
    for code, name, alat, alng in AIRPORTS:
        d = haversine_miles(float(lat), float(lng), alat, alng)
        if best is None or d < best["miles"]:
            best = {"code": code, "name": name, "miles": round(d, 1), "minutes": int(round(d * minutes_per_mile))}
    return best


def airport_screen(L: dict[str, Any], cfg: Any) -> dict[str, Any]:
    """Adds airport_code / airport_name / airport_miles / airport_minutes / airport_ok to the listing."""
    box = cfg.get("commercial.buyer_box", {}) or {}
    max_min = box.get("airport_max_minutes")
    mpm = float(box.get("airport_minutes_per_mile", 1.4))
    a = nearest(L.get("lat"), L.get("lng"), mpm)
    if a is None:
        return {"airport_ok": None, "airport_note": "no coordinates on the listing"}
    out = {"airport_code": a["code"], "airport_name": a["name"], "airport_miles": a["miles"], "airport_minutes": a["minutes"]}
    out["airport_ok"] = True if not max_min else a["minutes"] <= int(max_min)
    return out
