"""Targeting (Rodrigo, 2026-10-24): ideal = sitting 60+ days with price drops; also grab fresh listings priced right."""
from titan.ranking import rate
from titan.scoring import run_scoring
from titan.targeting import claude_priority, ideal, lane


def test_lanes(cfg):
    assert lane({"days_on_market": 75, "list_price": 400_000}, cfg, 300_000, "comps") == "SITTING"
    assert lane({"days_on_market": 5, "list_price": 300_000}, cfg, 310_000, "comps") == "FRESH_DEAL"
    assert lane({"days_on_market": 5, "list_price": 300_000}, cfg, 310_000, "estimate") == "WATCH"   # guessed ARV ≠ deal
    assert lane({"days_on_market": 5, "list_price": 400_000}, cfg, 310_000, "comps") == "WATCH"      # fresh but priced high
    assert lane({"days_on_market": 30, "list_price": 300_000}, cfg, 400_000, "comps") == "WATCH"
    assert ideal({"days_on_market": 90, "price_cuts": 2}, cfg) and not ideal({"days_on_market": 90, "price_cuts": 0}, cfg)
    assert ideal({"days_on_market": 61, "back_on_market": True}, cfg)


def test_rating_by_lane(cfg):
    assert rate(0, 300_000, 280_000, cfg, "comps", "FRESH_DEAL") == "HOT"          # price fits: move now, motivation or not
    # sitting with a price drop: HOT even 35% over our number (conversation is about flexibility)
    assert rate(10, 378_000, 280_000, cfg, "comps", "SITTING", ideal=True) == "HOT"
    assert rate(10, 378_000, 280_000, cfg, "comps", "SITTING", ideal=False) == "WARM"
    assert rate(90, 600_000, 280_000, cfg, "comps", "SITTING", ideal=True) == "COLD"   # too far apart even for a chat
    assert rate(90, 378_000, 280_000, cfg, "list_price", "SITTING", ideal=True) == "COLD"  # no real ARV


def test_claude_goes_to_targets_first_and_respects_the_cap(loaded, cfg):
    assert claude_priority({"days_on_market": 90, "price_cuts": 1}, cfg) == 0
    assert claude_priority({"days_on_market": 3}, cfg) == 1
    assert claude_priority({"days_on_market": 30, "price_cuts": 0}, cfg) is None
    loaded.execute("UPDATE properties SET days_on_market=90, price_cuts=1 WHERE id IN (SELECT id FROM properties LIMIT 2)")
    loaded.execute("UPDATE properties SET days_on_market=30, price_cuts=0, back_on_market=0 WHERE id NOT IN (SELECT id FROM properties LIMIT 2)")
    cfg.raw["scoring"]["max_claude_calls_per_run"] = 2
    seen = []

    class L:
        model, provider, session_cost, session_failures, session_successes, last_error = "m", "claude", 0.0, 0, 0, None
        cfg_ = cfg
        available = True

        def __init__(self): self.cfg = cfg

        def structured(self, purpose, system, user, schema, max_tokens=1024):
            seen.append(user); self.session_successes += 1
            return {"score": 70, "cited": [{"signal": "needs_work", "phrase": "x"}], "negatives": [], "summary": "s"}
    out = run_scoring(loaded, cfg, L(), force=True)
    assert out["claude"] == 2 and out["claude_cap_reached"] and len(seen) == 2
    assert out["rules"] == out["scored"] - 2
