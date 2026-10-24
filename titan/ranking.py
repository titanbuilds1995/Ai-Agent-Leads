"""RANKING: HOT / WARM / COLD from motivation + spread. All thresholds in config.ranking."""
from __future__ import annotations

from typing import Any


def rate(score: int | None, list_price: int | None, mao: int | None, cfg: Any, arv_method: str | None = None,
         lane: str | None = None, ideal: bool = False) -> str:
    """HOT / WARM / COLD. FRESH_DEAL (fresh listing, price already fits on real comps) is HOT. SITTING listings
    (60+ days) are rated with a wider allowed gap, since the conversation is about flexibility; a sitting listing with
    a price drop / back on market ('ideal') is HOT within that gap. Everything else: motivation + spread as before."""
    r = cfg["ranking"]
    if lane == "FRESH_DEAL":
        return "HOT"
    if score is None or not list_price or mao is None:
        return "COLD"
    allowed = r.get("arv_methods_for_hot_warm")
    if arv_method and allowed and arv_method not in allowed:
        return "COLD"   # guessed ARV (land default, list price) is not evidence of a deal
    if mao <= 0:
        return "COLD"
    over = (list_price - mao) / mao * 100  # % list is above MAO (negative when below)
    if lane == "SITTING":
        if over <= r.get("sitting_hot_max_list_over_mao_pct", 40) and (ideal or score >= r["warm_min_motivation"]):
            return "HOT"
        if over <= r.get("sitting_warm_max_list_over_mao_pct", 60):
            return "WARM"
        return "COLD"
    if score >= r["hot_min_motivation"] and over <= r["hot_max_list_over_mao_pct"]:
        return "HOT"
    if score >= r["warm_min_motivation"] and over <= r["warm_max_list_over_mao_pct"]:
        return "WARM"
    return "COLD"


def rerank(conn, cfg: Any) -> dict[str, int]:
    from .targeting import ideal, lane
    from .valuation import preliminary_range
    rows = conn.execute("SELECT p.*, v.list_price AS v_list, v.mao, v.arv, v.arv_method, v.comp_count, v.repair_cost, v.repair_tier, "
                        "s.score FROM valuations v JOIN scores s ON s.property_id=v.property_id JOIN properties p ON p.id=v.property_id "
                        "WHERE p.lead_status != 'EXCLUDED'").fetchall()
    counts: dict[str, int] = {"HOT": 0, "WARM": 0, "COLD": 0, "FRESH_DEAL": 0, "SITTING": 0, "SITTING_IDEAL": 0, "WATCH": 0}
    for r in rows:
        p = dict(r)
        rng = preliminary_range(p, cfg)
        ln = lane(p, cfg, rng["high"] if rng else None, p["arv_method"])
        best = ideal(p, cfg)
        rating = rate(r["score"], r["v_list"], r["mao"], cfg, r["arv_method"], ln, best)
        conn.execute("UPDATE valuations SET rating=?, lane=? WHERE property_id=?", (rating, ln, r["id"]))
        counts[rating] += 1
        counts[ln] += 1
        if ln == "SITTING" and best:
            counts["SITTING_IDEAL"] += 1
    return counts
