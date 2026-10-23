"""RANKING: HOT / WARM / COLD from motivation + spread. All thresholds in config.ranking."""
from __future__ import annotations

from typing import Any


def rate(score: int | None, list_price: int | None, mao: int | None, cfg: Any, arv_method: str | None = None) -> str:
    r = cfg["ranking"]
    if score is None or not list_price or mao is None:
        return "COLD"
    allowed = r.get("arv_methods_for_hot_warm")
    if arv_method and allowed and arv_method not in allowed:
        return "COLD"   # guessed ARV (land default, list price) is not evidence of a deal
    if mao <= 0:
        return "COLD"
    over = (list_price - mao) / mao * 100  # % list is above MAO (negative when below)
    if score >= r["hot_min_motivation"] and over <= r["hot_max_list_over_mao_pct"]:
        return "HOT"
    if score >= r["warm_min_motivation"] and over <= r["warm_max_list_over_mao_pct"]:
        return "WARM"
    return "COLD"


def rerank(conn, cfg: Any) -> dict[str, int]:
    rows = conn.execute("SELECT v.property_id, v.list_price, v.mao, v.arv_method, s.score FROM valuations v JOIN scores s ON s.property_id=v.property_id").fetchall()
    counts = {"HOT": 0, "WARM": 0, "COLD": 0}
    for r in rows:
        rating = rate(r["score"], r["list_price"], r["mao"], cfg, r["arv_method"])
        conn.execute("UPDATE valuations SET rating=? WHERE property_id=?", (rating, r["property_id"]))
        counts[rating] += 1
    return counts
