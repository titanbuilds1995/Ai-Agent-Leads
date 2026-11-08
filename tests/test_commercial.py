"""Commercial screen for the 1031 buyer (2026-11-08): LoopNet rows → extraction → score / tag / grade → files, broker list, Asana cards, diff."""
import json
from datetime import date
from pathlib import Path

import pytest

from titan.commercial import extract as x
from titan.commercial import loopnet, outputs, scoring
from titan.commercial.asana_push import card_specs, push
from titan.commercial.fl_geo import county_for_city, county_from_text, region_for
from titan.commercial.om_request import om_request, validate_om_request
from titan.commercial.run import run, sample_table
from tests.test_approvals import FakeAsana

FIX = Path(__file__).parent / "fixtures" / "commercial" / "loopnet_rows.json"
ROWS = json.loads(FIX.read_text())
TODAY = date(2026, 11, 8)


class Fake1031Asana(FakeAsana):
    def ensure_project(self, name, notes=""): return "proj-1031"
    def ensure_tag(self, name): return "tag-1031"
    def tag_task(self, task, tag): self.tagged = getattr(self, "tagged", []) + [(task, tag)]


def clean_listing(**over):
    L = {"listing_id": "1", "listing_url": "https://www.loopnet.com/Listing/1/", "street": "100 Main St", "city": "Lakeland", "county": "Polk", "state": "FL",
         "asset_class": "nnn", "property_type": "Retail", "price": 4_500_000, "stated_noi": 378_000, "stated_cap": 8.4, "occupancy": 100.0, "tenancy": "single",
         "tenant_names": ["Dollar General"], "credit_tenants": ["dollar general"], "lease_type": "nnn", "remaining_term_years": 12.0, "days_on_market": 140,
         "price_cuts": 1, "motivation": ["priced to sell"], "brokerage_size": "small", "trailing": True, "broker_name": "Jane Roe", "broker_email": "jane@roe.com",
         "broker_phone": "+18635550100", "brokerage": "Roe Realty", "building_sf": 9_100, "ppsf": 494.5, "detail_level": "full"}
    L.update(over)
    return L


# --- extraction ------------------------------------------------------------------------------------------------------------

def test_money_percent_and_size_parsing():
    assert x.money("$7.895M") == 7_895_000 and x.money("$512,244") == 512_244 and x.money("Price Not Disclosed") is None
    assert x.money_range("$689.7K - $3M") == (689_700, 3_000_000)
    assert x.percent("9.35%") == 9.35 and x.percent("On Request") is None
    assert x.sqft("55K") == 55_000 and x.sqft("174.4 AC") == 7_596_864 and x.sqft("3,512 SF") == 3_512


def test_text_signals():
    t = ("Established net operating income of approximately $512,244. 100% leased to Dollar General on an absolute NNN lease with 12 years remaining. "
         "Priced to sell, owner retiring. 8.25% cap rate. Pro forma rents at stabilization. Rent roll available upon signing the confidentiality agreement.")
    assert x.noi(t) == 512_244 and x.occupancy(t) == 100.0 and x.lease_type(t) == "absolute nnn" and x.remaining_term_years(t) == 12.0
    assert x.tenant_count(t) == 1 and x.tenancy(t, 1) == "single" and x.credit_tenants(t) == ["dollar general"]
    assert set(x.motivation_signals(t)) >= {"priced to sell", "owner retiring"} and x.is_proforma(t) and x.cap_from_text(t) == 8.25
    assert x.occupancy("value-add through full occupancy initiatives") is None           # Fort Myers card: an aspiration, not a fact
    assert x.brokerage_size("CBRE") == "institutional" and x.brokerage_size("Trust Realty USA LLC") == "regional" and x.brokerage_size("Daniel Hampson") == "small"


def test_florida_counties_statewide():
    assert county_for_city("Fort Myers") == "Lee" and county_for_city("Ft. Lauderdale") == "Broward" and county_for_city("St. Petersburg") == "Pinellas"
    assert county_from_text("has served Hernando County golfers") == "Hernando" and region_for("Lee") == "Southwest" and region_for(None) == "Unknown"


# --- mapper ----------------------------------------------------------------------------------------------------------------

def test_normalize_summary_card_and_full_sheet():
    Ls = {L["listing_id"]: L for L in loopnet.dedupe(ROWS)}
    office = Ls["28440860"]
    assert office["price"] == 7_895_000 and office["stated_cap"] == 9.35 and office["asset_class"] == "office" and office["county"] == "Broward"
    assert office["broker_email"] == "adam@trustrealtyusa.com" and office["broker_phone"] == "+13054336143" and office["detail_level"] == "summary"
    mf = Ls["40757439"]
    assert mf["detail_level"] == "full" and mf["stated_noi"] == 512_244 and mf["units"] == 46 and mf["asset_class"] == "multifamily"
    assert mf["listed_at"] == "2026-06-02" and mf["occupancy"] is None and mf["county"] == "Lee"
    assert Ls["42237709"]["asset_class"] == "land"              # "Commercial" type but a development site in the text
    assert Ls["40044930"]["price"] is None                      # Price Not Disclosed
    assert Ls["31866396"]["portfolio"]


def test_dedupe_prefers_the_full_sheet():
    summary = [r for r in ROWS if r.get("propertyId") == "40757439" and not r.get("propertyFacts")]
    full = [r for r in ROWS if r.get("propertyId") == "40757439" and r.get("propertyFacts")]
    assert full
    out = loopnet.dedupe(summary + full + summary)
    assert len(out) == 1 and out[0]["detail_level"] == "full"


def test_search_input_carries_the_buyer_box(cfg):
    inp = loopnet.search_input(cfg, "https://www.loopnet.com/search/retail-space/fl/for-sale/")
    assert inp["PriceMin"] == 3_500_000 and inp["PriceMax"] == 8_000_000 and inp["CapRateMin"] == "8.0" and inp["includeListingDetails"] is False


# --- scoring / tags / grades ---------------------------------------------------------------------------------------------------

def test_clean_listing_scores_high_with_anchor_at_target_cap(cfg):
    s = scoring.score(clean_listing(), cfg)
    assert s["in_box"] is True and s["tag"] == "CLEAN" and s["score"] >= 70 and s["tier"] == "core"
    # 378k at 8% = $4.725M, above the ask: a listing that already clears the target opens at the asking price, never above it
    assert s["offer_anchor"] == 4_500_000 and s["offer_anchor_at_target"] == 4_725_000 and s["back_solved_cap"] == 8.4 and not s["cap_mismatch"]
    assert s["evidence_grade"] == "C"                       # numbers in the text only, no documents yet
    # the anchor bites when the NOI does not support the stated cap: 350k / 4.5M = 7.78% → open at 350k / 8% = $4.375M
    s2 = scoring.score(clean_listing(stated_noi=350_000), cfg)
    assert s2["offer_anchor"] == 4_375_000 and s2["tag"] == "SUSPECT" and s2["cap_mismatch"]


def test_cap_floor_and_price_box_are_hard(cfg):
    assert scoring.score(clean_listing(stated_cap=7.9, stated_noi=355_500), cfg)["in_box"] is False
    assert scoring.score(clean_listing(price=9_000_000), cfg)["in_box"] is False
    assert scoring.score(clean_listing(price=None), cfg)["in_box"] is None
    assert scoring.score(clean_listing(asset_class="land"), cfg)["in_box"] is False
    assert scoring.score(clean_listing(asset_class="multifamily", units=4), cfg)["in_box"] is False
    reach = scoring.score(clean_listing(price=7_000_000, stated_noi=588_000), cfg)
    assert reach["tier"] == "reach" and reach["parts"]["price_band"] < scoring.score(clean_listing(), cfg)["parts"]["price_band"]


def test_cooked_books_flags(cfg):
    s = scoring.score(clean_listing(stated_cap=9.0), cfg)        # 378k / 4.5M = 8.4%, stated 9.0 → 60 bps off
    assert s["tag"] == "SUSPECT" and s["cap_mismatch"] and any("vs NOI/price" in r for r in s["tag_reasons"])
    assert scoring.score(clean_listing(proforma=True, trailing=False), cfg)["tag"] == "SUSPECT"
    assert scoring.score(clean_listing(tenant_names=[], credit_tenants=[], has_rent_roll=False), cfg)["tag"] == "SUSPECT"      # 100% with no names
    assert scoring.score(clean_listing(lease_type="gross", expense_ratio=0.2), cfg)["tag"] == "SUSPECT"
    assert scoring.score(clean_listing(occupancy=None), cfg)["tag"] == "SUSPECT"
    assert scoring.score(clean_listing(asset_class="hotel", hotel_metrics=False), cfg)["tag"] == "SUSPECT"
    assert scoring.score(clean_listing(), cfg, ppsf_median=150.0)["tag"] == "SUSPECT"                 # $494/SF vs $150 county median
    assert scoring.score(clean_listing(stated_noi=None), cfg)["tag"] == "NEEDS-OM"
    assert scoring.score(clean_listing(stated_cap=None), cfg)["tag"] == "NEEDS-OM"


def test_anchor_never_above_asking_and_implied_from_cap(cfg):
    s = scoring.score(clean_listing(stated_noi=None, stated_cap=9.35, price=7_895_000), cfg)
    assert s["offer_anchor"] == 7_895_000 and s["offer_anchor_at_target"] > 7_895_000 and "clears" in s["offer_anchor_basis"]


def test_evidence_grades():
    L = clean_listing()
    assert scoring.evidence_grade(L)[0] == "C"
    docs = [{"kind": "t12", "downloaded": True}, {"kind": "rent_roll", "downloaded": True}]
    assert scoring.evidence_grade({**L, "documents": docs, "doc_noi": 380_000})[0] == "A"
    assert scoring.evidence_grade({**L, "documents": docs, "doc_noi": 300_000})[0] == "B"
    assert scoring.evidence_grade({**L, "documents": [{"kind": "om", "downloaded": True}], "doc_noi": 378_000, "doc_expenses": 90_000})[0] == "B"
    assert scoring.evidence_grade({**L, "proforma": True, "trailing": False})[0] == "D"
    assert scoring.evidence_grade({**L, "stated_noi": None, "stated_cap": None})[0] == "D"


def test_region_bump_outside_south_florida(cfg):
    polk, dade = scoring.score(clean_listing(county="Polk"), cfg), scoring.score(clean_listing(county="Miami-Dade"), cfg)
    assert polk["parts"]["region"] > dade["parts"]["region"] == 0


# --- outputs -----------------------------------------------------------------------------------------------------------------

def test_top25_sorted_by_grade_then_score_and_broker_list_dedupes(cfg):
    a = {**clean_listing(listing_id="a", score=60, in_box=True, tag="CLEAN", evidence_grade="B")}
    b = {**clean_listing(listing_id="b", score=90, in_box=True, tag="CLEAN", evidence_grade="D", broker_email="JANE@roe.com", street="200 Oak St")}
    c = {**clean_listing(listing_id="c", score=95, in_box=True, tag="SUSPECT", evidence_grade="A", broker_email="z@z.com", broker_name="Z")}
    d = {**clean_listing(listing_id="d", score=50, in_box=None, tag="NEEDS-OM", evidence_grade="D", broker_email="n@n.com")}
    ranked = outputs.rank([a, b, c, d])
    assert [L["listing_id"] for L in ranked] == ["c", "b", "a", "d"]
    assert [L["listing_id"] for L in outputs.top_clean(ranked)] == ["a", "b"]           # grade B before D, CLEAN only
    brokers = outputs.broker_contacts(ranked)
    assert len(brokers) == 2 and brokers[0]["broker_email"] == "jane@roe.com" and brokers[0]["listing_count"] == 2
    assert "200 Oak St" in " ".join(brokers[0]["listings"]) and all("T-12" in m for m in brokers[0]["missing_docs"])


def test_om_request_in_house_style(cfg):
    L = {"broker_name": "Alex Lucke, CCIM", "street": "611 Druid Rd E", "city": "Clearwater", "asset_class": "industrial", "docs_missing": ["T-12", "rent roll", "OM"]}
    om = om_request(cfg, L)
    assert om["body"].startswith("Hey Alex,\n\n") and "Titan Realty Investments" in om["body"] and "Charles Grant" in om["body"]
    assert "trailing twelve" in om["body"] and "rent roll" in om["body"] and validate_om_request(om["subject"], om["body"]) == []


# --- the run: idempotent store, diff, files, cards ---------------------------------------------------------------------------------

def test_run_offline_writes_files_stores_and_diffs(conn, cfg, tmp_path):
    cfg.root = tmp_path
    out = run(conn, cfg, apify=None, raw_rows=ROWS, push_asana=False, email=False, run_date="2026-11-07", today=TODAY)
    ranked = out.pop("ranked")
    assert out["counts"]["total"] == len(loopnet.dedupe(ROWS)) and out["counts"]["in_box"] >= 5 and out["diff"]["new"] == out["counts"]["in_box"]
    for key in ("master_csv", "broker_contacts_csv", "needs_om_csv", "workbook", "pdf", "diff_md"):
        assert Path(out["files"][key]).exists(), key
    assert conn.execute("SELECT COUNT(*) FROM commercial_listings").fetchone()[0] == out["counts"]["total"]
    assert conn.execute("SELECT COUNT(*) FROM commercial_snapshots WHERE run_date='2026-11-07'").fetchone()[0] == out["counts"]["total"]
    sample = sample_table(ranked, 10)
    assert len({L["county"] for L in sample}) >= 3
    # same day again: nothing doubles
    again = run(conn, cfg, apify=None, raw_rows=ROWS, push_asana=False, email=False, run_date="2026-11-07", today=TODAY)
    assert conn.execute("SELECT COUNT(*) FROM commercial_listings").fetchone()[0] == out["counts"]["total"]
    assert again["diff"]["new"] == out["diff"]["new"]
    # next day: a price drop, a listing gone from a FULL pull, nothing new
    rows2 = [dict(r) for r in ROWS if r.get("propertyId") != "28440860"]
    for r in rows2:
        if r.get("propertyId") == "41959395":
            r["price"] = r["priceText"] = r["formattedPrice"] = "$5.5M"
    day2 = run(conn, cfg, apify=None, raw_rows=rows2, push_asana=False, email=False, run_date="2026-11-08", full_pull=True, today=TODAY)
    assert day2["diff"]["new"] == 0 and day2["diff"]["price_drops"] == 1 and day2["diff"]["gone"] == 1
    gone = conn.execute("SELECT status, gone_at FROM commercial_listings WHERE listing_id='28440860'").fetchone()
    assert gone["status"] == "gone" and gone["gone_at"]
    dropped = conn.execute("SELECT price, price_cuts, previous_price FROM commercial_listings WHERE listing_id='41959395'").fetchone()
    assert dropped["price"] == 5_500_000 and dropped["price_cuts"] == 1 and dropped["previous_price"] == 6_000_000
    assert "Price drops (1)" in Path(day2["files"]["diff_md"]).read_text()


def test_full_sheet_from_an_earlier_run_is_kept_when_today_is_a_summary_card(conn, cfg, tmp_path):
    cfg.root = tmp_path
    run(conn, cfg, apify=None, raw_rows=ROWS, push_asana=False, email=False, run_date="2026-11-07", today=TODAY)
    summary_only = [r for r in ROWS if not r.get("propertyFacts")]
    assert not any(r.get("propertyId") == "40757439" for r in summary_only)
    card = {"propertyId": "40757439", "listingUrl": "https://www.loopnet.com/Listing/40757439/", "address": "5450-5460 5th Ave", "city": "Fort Myers", "price": "$6.9M",
            "propertyType": "Apartments", "brokerName": "Meidan Mike Magen", "brokerCompany": "Compass", "_detailLevel": "summary"}
    out = run(conn, cfg, apify=None, raw_rows=summary_only + [card], push_asana=False, email=False, run_date="2026-11-08", today=TODAY)
    L = next(L for L in out["ranked"] if L["listing_id"] == "40757439")
    assert L["detail_level"] == "full" and L["stated_noi"] == 512_244 and L["price"] == 6_900_000 and L["units"] == 46


def test_asana_cards_top_tier_and_needs_om_once(conn, cfg, tmp_path):
    cfg.root = tmp_path
    out = run(conn, cfg, apify=None, raw_rows=ROWS, push_asana=False, email=False, run_date="2026-11-07", today=TODAY)
    ranked = out["ranked"]
    ranked.append({**clean_listing(listing_id="clean1"), **scoring.score(clean_listing(listing_id="clean1"), cfg), "region": "Central"})
    from titan.commercial.run import store
    store(conn, ranked[-1], "2026-11-07")
    asana = Fake1031Asana()
    res = push(conn, cfg, asana, ranked)
    specs = card_specs(cfg, ranked)
    assert res["cards"] == len(specs) >= 2 and len(asana.tagged) == res["cards"]
    top = [t for g, t in asana.tasks.items() if asana.where[g] == asana.ensure_section("proj-1031", "Top tier")]
    om = [t for g, t in asana.tasks.items() if asana.where[g] == asana.ensure_section("proj-1031", "Needs OM")]
    assert len(top) == 1 and "OFFER ANCHOR: $4,500,000" in top[0]["notes"] and "Broker: Jane Roe" in top[0]["notes"]
    assert om and "=== EMAIL (what will be sent) ===" in om[0]["notes"] and "Titan Realty Investments" in om[0]["notes"]
    assert push(conn, cfg, asana, ranked)["cards"] == 0            # idempotent


def test_documents_gated_and_grades_from_pdfs(tmp_path):
    from titan.commercial import documents as d
    L = {"listing_id": "9", "raw": {"description": "Brochure: https://example.invalid/flyer.pdf"},
         "text": "Rent roll and T-12 available upon signing the confidentiality agreement."}
    out = d.doc_facts(L, tmp_path, enabled=False)
    kinds = {(x["kind"], x.get("gated")) for x in out["documents"]}
    assert ("t12", True) in kinds and ("rent_roll", True) in kinds and ("brochure", None) in kinds or ("brochure", False) in kinds
    assert out["docs_missing"] == ["T-12", "rent roll", "OM", "lease abstract"] and "T-12" in " ".join(out["docs_gated"])
    facts = d.facts_from_text("Net Operating Income $425,000\nGross Income $650,000\nOperating Expenses $225,000\n95% occupied\nDollar General     $120,000\nLease expiration 12/31/2032")
    assert facts["noi"] == 425_000 and facts["gross"] == 650_000 and facts["occupancy"] == 95.0 and facts["tenants"] == ["Dollar General"] and facts["expirations"] == ["12/31/2032"]
