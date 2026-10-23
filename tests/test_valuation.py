import json

from titan.ranking import rate
from titan.valuation import compute_mao, repair_cost, repair_tier, select_comps, value_property
from tests.conftest import FIXTURES


def test_repair_tiers():
    assert repair_tier("Fire damage, structural issues") == "gut"
    assert repair_tier("Needs a new roof and has water damage") == "heavy"
    assert repair_tier("Needs some TLC, dated kitchen") == "moderate"
    assert repair_tier("Move-in ready, fresh paint") == "cosmetic"
    assert repair_tier("Nice home", default="moderate") == "moderate"


def test_repair_cost_uses_config(cfg):
    p = {"sqft": 1500, "property_type": "single_family"}
    assert repair_cost(p, "heavy", cfg) == 1500 * cfg["repairs"]["heavy"]
    assert repair_cost({"sqft": 300, "property_type": "single_family"}, "cosmetic", cfg) == cfg["repairs"]["min_repair_budget"]


def test_mao_formula(cfg):
    f = cfg["formula"]
    assert compute_mao(500000, 50000, cfg) == int(round(500000 * f["arv_multiplier"] - 50000 - f["assignment_fee"], -2))


def test_comp_selection_and_arv(cfg):
    comps = json.loads((FIXTURES / "sold_comps_sample.json").read_text())
    subject = {"sqft": 1450, "property_type": "single_family", "lat": 25.783, "lng": -80.212, "zip": "33125",
               "list_price": 385000, "remarks": "Estate sale, needs TLC, as-is", "estimate": 520000}
    sel = select_comps(subject, comps, cfg)
    assert len(sel) == 8                                   # far / wrong size / wrong type excluded
    res = value_property(subject, cfg, lambda p: comps)
    assert res["arv_method"] == "comps" and res["comp_count"] == 8
    assert 480000 <= res["arv"] <= 560000
    assert res["repair_tier"] == "moderate"
    assert res["mao"] == compute_mao(res["arv"], res["repair_cost"], cfg)
    assert res["offer_price"] <= res["mao"]
    assert abs(res["spread_pct"] - (385000 - res["mao"]) / 385000 * 100) < 0.2


def test_fallback_to_estimate_when_no_comps(cfg):
    subject = {"sqft": 1450, "property_type": "single_family", "zip": "33125", "list_price": 385000, "remarks": "", "estimate": 520000}
    res = value_property(subject, cfg, lambda p: [])
    assert res["arv_method"] == "estimate" and res["arv"] == 520000


def test_land_valuation(cfg):
    subject = {"property_type": "land", "lot_sqft": 21780, "list_price": 165000, "remarks": "vacant lot", "estimate": None}
    res = value_property(subject, cfg, None)
    assert res["arv_method"] == "land_default" and res["arv"] == int(cfg["valuation"]["land_arv_per_acre_default"] * 0.5)


def test_ranking_rules(cfg):
    assert rate(70, 300000, 300000, cfg) == "HOT"            # at MAO
    assert rate(70, 360000, 300000, cfg) == "HOT"            # +20%
    assert rate(70, 361000, 300000, cfg) == "WARM"           # just over 20%, within 35%
    assert rate(55, 400000, 300000, cfg) == "WARM"           # 55 motivation, +33%
    assert rate(55, 410000, 300000, cfg) == "COLD"           # +36.7%
    assert rate(64, 300000, 300000, cfg) == "WARM"           # motivation below hot line
    assert rate(49, 250000, 300000, cfg) == "COLD"
    assert rate(90, 300000, 0, cfg) == "COLD"


def test_pipeline_ranks(loaded):
    rows = loaded.execute("SELECT p.street, v.rating, v.mao, s.score FROM valuations v JOIN properties p ON p.id=v.property_id JOIN scores s ON s.property_id=p.id").fetchall()
    ratings = {r["street"]: r["rating"] for r in rows}
    assert any(v == "HOT" for v in ratings.values())
    assert all(r["mao"] is not None for r in rows)
