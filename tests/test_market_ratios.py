from pathlib import Path

from titan.market_ratios import band_for, lookup
from titan.valuation import buy_ratio, compute_mao

CSV = Path(__file__).resolve().parents[1] / "data" / "market_ratios.csv"


def test_zip_band_hit_uses_measured_median():
    r = lookup(CSV, "Broward", "33023", "single_family", 400_000)
    assert r["ratio"] == 0.68 and "33023 sfr 300-500k" in r["source"] and r["sample_size"] == 174


def test_falls_back_to_county_and_palm_beach_borrows():
    assert lookup(CSV, "Miami-Dade", "99999", "single_family", 400_000)["source"].startswith("miami-dade *")
    pb = lookup(CSV, "Palm Beach", "33411", "single_family", 400_000)
    assert pb and "borrowed from broward" in pb["source"]
    assert lookup(CSV, "Broward", "33023", "land", 400_000) is None


def test_percentile_choice():
    lo = lookup(CSV, "Broward", "33023", "single_family", 400_000, "p25")["ratio"]
    hi = lookup(CSV, "Broward", "33023", "single_family", 400_000, "p75")["ratio"]
    assert lo < 0.68 < hi


def test_valuation_uses_market_ratio_then_flat_fallback(cfg):
    p = {"county": "Broward", "zip": "33023", "property_type": "single_family"}
    br = buy_ratio(p, 400_000, cfg)
    assert br["ratio"] == 0.68
    assert compute_mao(400_000, 30_000, cfg, br["ratio"]) == 400_000 * 0.68 - 30_000 - cfg["formula"]["assignment_fee"]
    land = buy_ratio({"county": "Broward", "zip": "33023", "property_type": "land"}, 100_000, cfg)
    assert land["ratio"] == cfg["formula"]["arv_multiplier"] and "flat" in land["source"]
    cfg["formula"]["ratio_source"] = "flat"
    assert buy_ratio(p, 400_000, cfg)["ratio"] == cfg["formula"]["arv_multiplier"]


def test_bands():
    assert band_for(299_999) == "<300k" and band_for(300_000) == "300-500k" and band_for(2_500_000) == "2M+"


def test_zip_psf_fallback_only_inside_the_zip():
    from titan.market_ratios import zip_resale_psf
    assert zip_resale_psf(CSV, "Broward", "99999", "single_family", 1500) is None
    assert zip_resale_psf(CSV, "Palm Beach", "33411", "single_family", 1500) is None
    assert zip_resale_psf(CSV, "Broward", "33023", "single_family", 1500) == 296.0   # 1500 sf x $296 lands in 300-500k


def test_city_step_before_county():
    r = lookup(CSV, "Miami-Dade", "99999", "single_family", 400_000, city="Coral Gables")
    assert r["ratio"] == 0.767 and "CITY:CORAL GABLES" in r["source"]
