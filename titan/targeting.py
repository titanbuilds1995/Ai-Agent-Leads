"""Who we go after (Rodrigo, 2026-10-24). Scraping is free, so every active listing is kept; this decides who is a target.

  SITTING     on the market `sitting_min_dom`+ days. The ideal seller, best with price drops or back on market.
  FRESH_DEAL  listed within `fresh_max_dom` days AND the asking price already fits our internal range (real comps only).
              Move fast and lock it in before other buyers.
  WATCH       everything else. Kept, re-checked every run; moves into a lane when it sits or drops its price.

Lanes feed ranking (ranking.rate), who Claude scores first (scoring), and outreach order (outreach.send)."""
from __future__ import annotations

from typing import Any

LANES = ("FRESH_DEAL", "SITTING", "WATCH")
REAL_COMPS = ("comps", "comps_wide")


def _t(cfg: Any) -> dict[str, Any]:
    return cfg.get("targeting", {}) or {}


def lane(p: dict[str, Any], cfg: Any, internal_target: int | None = None, arv_method: str | None = None) -> str:
    """internal_target = the median-ratio screening number (valuations.mao). A fresh listing is a FRESH_DEAL only when the
    ask is already within fresh_price_within_pct of that target on real comps (not the top of the range, which was too loose)."""
    t = _t(cfg)
    dom = p.get("days_on_market")
    lp = p.get("list_price")
    if dom is not None and dom <= t.get("fresh_max_dom", 14) and lp and internal_target and arv_method in REAL_COMPS \
            and lp <= internal_target * (1 + t.get("fresh_price_within_pct", 5) / 100):
        return "FRESH_DEAL"
    if dom is not None and dom >= t.get("sitting_min_dom", 60):
        return "SITTING"
    return "WATCH"


def ideal(p: dict[str, Any], cfg: Any) -> bool:
    """Sitting 60+ days AND a price drop or back on market: the seller Rodrigo most wants to talk to."""
    return (p.get("days_on_market") or 0) >= _t(cfg).get("sitting_min_dom", 60) and \
        bool((p.get("price_cuts") or 0) > 0 or p.get("back_on_market"))


def claude_priority(p: dict[str, Any], cfg: Any) -> int | None:
    """Order in which listings get a Claude score (lower first); None = keyword rules are enough for now.
    Before valuation we can't know a fresh listing's price fits, so every fresh listing is a candidate."""
    t = _t(cfg)
    dom = p.get("days_on_market")
    if ideal(p, cfg):
        return 0
    if dom is not None and dom <= t.get("fresh_max_dom", 14):
        return 1
    if dom is not None and dom >= t.get("sitting_min_dom", 60):
        return 2
    if (p.get("price_cuts") or 0) > 0 or p.get("back_on_market"):
        return 3
    return None
