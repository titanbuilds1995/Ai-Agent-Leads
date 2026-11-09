"""Quality screen (Rodrigo, 2026-11-09: "high quality properties … take out properties that promise a high cap rate but are below
C class neighborhoods"). What LoopNet gives us: the building class (A/B/C), year built, and the listing text. What it does not
give us: a neighborhood class. So this screens the building and the words, flags the rest, and says so."""
from __future__ import annotations

import re
from typing import Any

CLASS_RANK = {"A": 3, "B": 2, "C": 1, "D": 0}
DISTRESS_RE = re.compile(r"deferred maintenance|needs? (?:major |significant |extensive )?(?:work|repairs?|renovation|rehab)|as[- ]is\b|distress|"
                         r"fire damage|hurricane damage|roof (?:leak|issue)|code violation|condemn|boarded|vacant (?:building|property)|shell condition|"
                         r"receivership|foreclos|\breo\b|bank[- ]owned|short sale|mold|structural", re.I)
ROUGH_AREA_RE = re.compile(r"high[- ]crime|rough (?:area|neighborhood)|transitional (?:area|neighborhood)|up[- ]and[- ]coming|opportunity zone|"
                           r"section 8|workforce housing|class d\b|\bd[- ]class\b|c[- ]minus|c-\b", re.I)
PREMIUM_RE = re.compile(r"class a\b|\ba[- ]class\b|trophy|institutional[- ]quality|newly (?:built|constructed|renovated)|fully renovated|"
                        r"credit tenant|corporate guarantee|investment[- ]grade", re.I)


def quality_screen(L: dict[str, Any], cfg: Any) -> dict[str, Any]:
    """quality: 'pass' | 'watch' | 'out', with the reasons. 'out' leaves the box."""
    box = cfg.get("commercial.buyer_box", {}) or {}
    min_class = str(box.get("min_building_class") or "C").upper()
    min_year = box.get("min_year_built")
    text = L.get("text") or ""
    reasons: list[str] = []
    verdict = "pass"
    bc = str(L.get("building_class") or "").strip().upper()[:1]
    if bc and bc in CLASS_RANK and CLASS_RANK[bc] < CLASS_RANK.get(min_class, 1):
        verdict = "out"
        reasons.append(f"building class {bc} (minimum {min_class})")
    if ROUGH_AREA_RE.search(text):
        verdict = "out"
        reasons.append("listing describes a below-C area: " + ROUGH_AREA_RE.search(text).group(0))
    if DISTRESS_RE.search(text):
        verdict = "out" if verdict == "out" else "watch"
        reasons.append("condition language: " + DISTRESS_RE.search(text).group(0))
    if min_year and L.get("year_built") and L["year_built"] < int(min_year):
        verdict = "out" if verdict == "out" else "watch"
        reasons.append(f"built {L['year_built']}, before {min_year}")
    if not bc:
        reasons.append("building class not stated")
    if PREMIUM_RE.search(text):
        reasons.append("premium language: " + PREMIUM_RE.search(text).group(0))
    return {"quality": verdict, "quality_reasons": reasons, "neighborhood_class": None}
