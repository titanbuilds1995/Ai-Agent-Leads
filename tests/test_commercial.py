"""Commercial screen for the 1031 buyer (2026-11-08): LoopNet rows → extraction → score / tag / grade → files, broker list, Asana cards, diff."""
import json
import re
from datetime import date
from pathlib import Path

import pytest
from titan.util import iso

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
    def project_tasks(self, project, completed_since="now"): return [dict(t) for t in self.tasks.values()]


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
    om2 = om_request(cfg, {**L, "stated_cap": 8.5})
    assert "verify" in om2["body"] and "eight plus cap" in om2["body"] and validate_om_request(om2["subject"], om2["body"]) == []


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
    cfg.raw["outreach"]["asana"]["send_window"] = {"start": "00:00", "end": "23:59"}   # broker replies respect the window; tests run at any hour
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
    ranked.append({**clean_listing(listing_id="900001", listing_url="https://www.loopnet.com/Listing/900001/"), **scoring.score(clean_listing(listing_id="900001"), cfg), "region": "Central"})
    from titan.commercial.run import store
    store(conn, ranked[-1], "2026-11-07")
    asana = Fake1031Asana()
    res = push(conn, cfg, asana, ranked)
    specs = card_specs(cfg, ranked)
    assert res["cards"] == len(specs) >= 2 and len(asana.tagged) == res["cards"]
    new = [t for g, t in asana.tasks.items() if asana.where[g] == asana.ensure_section("proj-1031", "New")]
    assert len(new) == len(specs)                                   # every card lands in New; Rodrigo sorts them by dragging
    top = [t for t in new if not t["name"].startswith("OM request")]
    om = [t for t in new if t["name"].startswith("OM request")]
    assert len(top) == 1 and "OFFER ANCHOR: $4,500,000" in top[0]["notes"] and "Broker: Jane Roe" in top[0]["notes"]
    assert om and "=== EMAIL (what will be sent) ===" in om[0]["notes"] and "Titan Realty Investments" in om[0]["notes"]
    assert push(conn, cfg, asana, ranked)["cards"] == 0            # idempotent
    conn.execute("UPDATE commercial_listings SET asana_task_id=NULL, asana_om_task_id=NULL")   # another machine's DB: the board itself says no
    assert push(conn, cfg, asana, ranked)["cards"] == 0


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


# --- the visual layer (Rodrigo, 2026-11-08): board columns, the deals email, the site feed ------------------------------------------

def test_board_columns_rename_and_read(conn, cfg, tmp_path):
    from titan.commercial.board import COLUMNS, apply_board, column_key, ensure_board, read_board
    asana = Fake1031Asana()
    asana._sections = {"s1": "Top tier", "s2": "Needs OM", "s3": "Suspect", "s4": "Contacted", "s5": "Skip"}
    secs = ensure_board(asana, "proj-1031")
    assert set(secs) == set(COLUMNS) and asana._sections["s1"] == "Good fit" and asana._sections["s2"] == "New"
    assert column_key("Needs OM") == "New" and column_key("good fit") == "Good fit" and column_key("Whatever") is None
    cfg.root = tmp_path
    run(conn, cfg, apify=None, raw_rows=ROWS, push_asana=False, email=False, run_date="2026-11-07", today=TODAY)
    gid = asana.create_task("proj-1031", secs["Good fit"], "card", "facts\nhttps://www.loopnet.com/Listing/28440860/\n")["gid"]
    asana.tasks[gid]["memberships"] = [{"project": {"gid": "proj-1031"}, "section": {"name": "Good fit"}}]
    board = read_board(asana, "proj-1031")
    assert board["28440860"]["column"] == "Good fit"
    assert apply_board(conn, board) == 1
    assert conn.execute("SELECT board_column FROM commercial_listings WHERE listing_id='28440860'").fetchone()[0] == "Good fit"


def test_deals_email_has_a_card_per_in_box_listing_with_photo_and_why(conn, cfg, tmp_path):
    from titan.commercial.email_html import build_email
    cfg.root = tmp_path
    out = run(conn, cfg, apify=None, raw_rows=ROWS, push_asana=False, email=False, run_date="2026-11-07", today=TODAY)
    ranked = out["ranked"]
    html, text = build_email(ranked, cfg, "2026-11-07", out["counts"])
    n_box = sum(1 for L in ranked if L.get("in_box"))
    assert html.count("See the property") == n_box and "Why this fits" in html and "images1.loopnet.com" in html
    assert "OFFER ANCHOR" not in html and "Score parts" not in html          # the investor gets this email: internal numbers stay out
    assert "840 SW 81st Ave" in text and Path(out["files"]["deals_email"]).exists()
    cfg.raw["commercial"]["site"]["url"] = "https://example.vercel.app"
    html2, _ = build_email(ranked, cfg, "2026-11-07", out["counts"])
    assert "https://example.vercel.app/1031.html#28440860" in html2


def test_site_feed_splits_public_and_private(conn, cfg, tmp_path):
    from titan.commercial.board import COLUMNS
    from titan.commercial.site import build_deals, publish
    cfg.root = tmp_path
    out = run(conn, cfg, apify=None, raw_rows=ROWS, push_asana=False, email=False, run_date="2026-11-07", today=TODAY)
    conn.execute("UPDATE commercial_listings SET board_column='Good fit' WHERE listing_id='28440860'")
    snap = build_deals(conn, cfg, "2026-11-07")
    assert snap["columns"] == COLUMNS and len(snap["deals"]) == out["counts"]["in_box"]
    d = next(x for x in snap["deals"] if x["listing_id"] == "28440860")
    assert d["board_column"] == "Good fit" and d["investor"] is True and d["why"] and d["photo"].endswith("/116/image.jpg")
    assert "broker_email" not in d and "offer_anchor" not in d
    assert snap["private"]["28440860"]["broker_email"] == "adam@trustrealtyusa.com" and snap["private"]["28440860"]["score"] == d.get("score", snap["private"]["28440860"]["score"])
    assert all(x["investor"] is False for x in snap["deals"] if x["board_column"] == "New")
    res = publish(conn, cfg, "2026-11-07")
    assert Path(res["local"]).exists() and res["uploaded"] is False and "BLOB" in res["skipped"]
    assert out["site"]["deals"] == out["counts"]["in_box"]


# --- OM requests out, books back (Rodrigo, 2026-11-09: "who communicates the fastest and can send us the documents ASAP") ---------

class FakeOMGmail:
    def __init__(self):
        self.sent, self.threads, self.files = [], {}, {}
        self._n = 0

    def send(self, to, subject, body, **kw):
        self._n += 1
        self.sent.append((to, subject, body))
        self.kw = getattr(self, "kw", []) + [kw]
        self.threads[f"t{self._n}"] = [{"id": f"m{self._n}", "from": "Charles@titanrealtyinvestments.com", "body": body, "internal_ts": 1_700_000_000, "label_ids": [], "attachments": []}]
        return {"message_id": f"m{self._n}", "thread_id": f"t{self._n}"}

    def thread_messages(self, tid): return list(self.threads.get(tid, []))
    def attachments(self, mid): return self.files.get(mid, [])
    def archive_thread(self, tid): self.archived = getattr(self, "archived", []) + [tid]


def _om_board(conn, cfg, tmp_path):
    cfg.root = tmp_path
    cfg.raw["commercial"]["asana"]["project"] = "proj-1031"
    cfg.raw["outreach"]["asana"]["per_tick"] = 5
    run(conn, cfg, apify=None, raw_rows=ROWS, push_asana=False, email=False, run_date="2026-11-07", today=TODAY)
    asana = Fake1031Asana()
    from titan.commercial.board import SEND, ensure_board
    secs = ensure_board(asana, "proj-1031")
    from titan.commercial.asana_push import card_notes
    L = json.loads(conn.execute("SELECT data FROM commercial_listings WHERE listing_id='28440860'").fetchone()[0])
    L["in_box"] = True
    gid = asana.create_task("proj-1031", secs[SEND], "OM request · 840 SW 81st Ave", card_notes(cfg, L, with_email=True))["gid"]
    return asana, secs, gid


def test_om_requests_send_from_the_send_column_once_and_move_the_card(conn, cfg, tmp_path):
    from titan.commercial.board import SEND
    from titan.commercial.outreach import send_om_requests
    from tests.test_approvals import SAT_10AM_ET
    asana, secs, gid = _om_board(conn, cfg, tmp_path)
    gm = FakeOMGmail()
    out = send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)
    assert out["sent"] == 1 and gm.sent[0][0] == "adam@trustrealtyusa.com" and "Titan Realty Investments" in gm.sent[0][2]
    assert asana.where[gid] == secs["In touch with the agent"] and any("📤 Sent" in c for c in asana.stories[gid])
    th = conn.execute("SELECT * FROM commercial_threads").fetchone()
    assert th["listing_id"] == "28440860" and th["gmail_thread_id"] == "t1" and th["state"] == "SENT"
    assert conn.execute("SELECT kind, status FROM outreach").fetchone()[0] == "om_request"
    assert conn.execute("SELECT board_column FROM commercial_listings WHERE listing_id='28440860'").fetchone()[0] == "In touch with the agent"
    # dragged back into the send column by mistake: not sent twice
    asana.move(gid, secs[SEND])
    out2 = send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)
    assert out2["sent"] == 0 and out2["skipped"] == 1 and len(gm.sent) == 1
    # outside the window: waits
    from datetime import datetime, timezone
    asana.move(gid, secs[SEND])
    conn.execute("DELETE FROM commercial_threads")
    assert send_om_requests(conn, cfg, asana, gm, now=datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc))["sent"] == 0


def test_reply_clock_books_filed_and_regraded(conn, cfg, tmp_path):
    from titan.commercial.outreach import poll_om_replies, send_om_requests
    from tests.test_approvals import SAT_10AM_ET
    asana, secs, gid = _om_board(conn, cfg, tmp_path)
    gm = FakeOMGmail()
    send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)
    sent_ts = int(SAT_10AM_ET.timestamp())
    conn.execute("UPDATE commercial_threads SET sent_at=?", (iso(SAT_10AM_ET),))
    # 1) a plain answer three hours later, no files
    gm.threads["t1"].append({"id": "r1", "from": "Adam Goldstein <adam@trustrealtyusa.com>", "body": "Happy to send the package, give me a day.",
                             "internal_ts": sent_ts + 3 * 3600, "label_ids": [], "attachments": []})
    out = poll_om_replies(conn, cfg, gm, asana)
    th = conn.execute("SELECT * FROM commercial_threads").fetchone()
    assert out["replies"] == 1 and th["hours_to_reply"] == 3.0 and th["state"] == "REPLIED" and th["reply_count"] == 1
    assert any("answered in 3 h" in c for c in asana.stories[gid]) and asana.where[gid] == secs["In touch with the agent"]
    # 2) the books arrive: a T-12 with a real NOI and a rent roll with the tenant
    t12 = ("Trailing 12 months actuals\nGross Income $980,000\nOperating Expenses $350,000\nNet Operating Income $630,000\n92% occupied\n").encode()
    rr = ("Rent Roll\nSuite 100  Dollar General     $120,000\nLease expiration 12/31/2032\nTenants: 1 tenant\nNNN lease\n").encode()
    gm.files["r2"] = [{"filename": "840 Building T-12.pdf", "mime": "application/pdf", "bytes": t12}, {"filename": "Rent Roll.pdf", "mime": "application/pdf", "bytes": rr}]
    gm.threads["t1"].append({"id": "r2", "from": "adam@trustrealtyusa.com", "body": "Attached.", "internal_ts": sent_ts + 26 * 3600, "label_ids": [],
                             "attachments": [{"filename": "840 Building T-12.pdf"}, {"filename": "Rent Roll.pdf"}]})
    import titan.commercial.documents as d
    monkey = d.pdf_text
    d.pdf_text = lambda path, max_pages=60: Path(path).read_bytes().decode()      # the fake "PDFs" are plain text
    try:
        out = poll_om_replies(conn, cfg, gm, asana)
    finally:
        d.pdf_text = monkey
    th = conn.execute("SELECT * FROM commercial_threads").fetchone()
    assert out["books"] == 1 and th["state"] == "BOOKS" and th["hours_to_reply"] == 3.0 and th["reply_count"] == 2
    folder = tmp_path / "data" / "listings" / "28440860"
    assert (folder / "840_Building_T-12.pdf").exists() and (folder / "Rent_Roll.pdf").exists()
    row = conn.execute("SELECT board_column, stated_noi, back_solved_cap, offer_anchor, tag, evidence_grade FROM commercial_listings WHERE listing_id='28440860'").fetchone()
    assert row["board_column"] == "Books received" and row["stated_noi"] == 630_000 and asana.where[gid] == secs["Books received"]
    assert row["back_solved_cap"] == 7.98 and row["offer_anchor"] == 7_875_000          # 630k at 8% is just under the $7.895M ask
    assert row["tag"] == "SUSPECT" and "vs NOI/price" in " ".join(json.loads(conn.execute("SELECT tag_reasons FROM commercial_listings WHERE listing_id='28440860'").fetchone()[0]))
    assert row["evidence_grade"] == "B"                                                 # T-12 + rent roll on file but the NOI misses the stated 9.35%
    assert any("Regraded with the books" in c and "OFFER ANCHOR $7,875,000" in c for c in asana.stories[gid])
    # 3) nothing double-counted on the next poll
    assert poll_om_replies(conn, cfg, gm, asana)["replies"] == 0


def test_bounce_is_not_an_answer(conn, cfg, tmp_path):
    from titan.commercial.outreach import poll_om_replies, send_om_requests
    from tests.test_approvals import SAT_10AM_ET
    asana, secs, gid = _om_board(conn, cfg, tmp_path)
    gm = FakeOMGmail()
    send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)
    gm.threads["t1"].append({"id": "b1", "from": "Mail Delivery Subsystem <mailer-daemon@googlemail.com>", "body": "Address not found", "internal_ts": 1_700_000_100,
                             "label_ids": [], "attachments": []})
    out = poll_om_replies(conn, cfg, gm, asana)
    th = conn.execute("SELECT * FROM commercial_threads").fetchone()
    assert out["bounces"] == 1 and out["replies"] == 0 and th["bounced"] == 1 and th["hours_to_reply"] is None
    assert any("Bounced" in c for c in asana.stories[gid])


# --- airport + quality screens (Rodrigo, 2026-11-09) -------------------------------------------------------------------------------

def test_airport_screen_limits_the_box_to_45_minutes(cfg):
    from titan.commercial.airports import airport_screen, nearest
    assert nearest(26.218624, -80.235947)["code"] == "FXE"
    near = airport_screen({"lat": 26.218624, "lng": -80.235947}, cfg)
    far = airport_screen({"lat": 30.73, "lng": -85.2}, cfg)          # Marianna, over an hour from Panama City
    assert near["airport_ok"] is True and near["airport_minutes"] < 45 and far["airport_ok"] is False
    assert airport_screen({}, cfg)["airport_ok"] is None               # no coordinates: not thrown out, just unknown
    assert scoring.score(clean_listing(**far), cfg)["in_box"] is False
    assert "airport" in scoring.score(clean_listing(**far), cfg)["box_note"]
    assert scoring.score(clean_listing(**near), cfg)["in_box"] is True


def test_quality_screen_building_class_and_language(cfg):
    from titan.commercial.quality import quality_screen
    assert quality_screen({"building_class": "D", "text": "great cap rate"}, cfg)["quality"] == "out"
    assert quality_screen({"building_class": "C", "text": "stabilized retail"}, cfg)["quality"] == "pass"
    assert quality_screen({"building_class": "B", "text": "located in a high-crime transitional area"}, cfg)["quality"] == "out"
    w = quality_screen({"building_class": "B", "text": "some deferred maintenance on the roof"}, cfg)
    assert w["quality"] == "watch" and "condition language" in w["quality_reasons"][0]
    assert scoring.score(clean_listing(quality="out", quality_reasons=["building class D (minimum C)"]), cfg)["in_box"] is False
    assert scoring.score(clean_listing(quality="watch"), cfg)["in_box"] is True


def test_om_request_speaks_as_the_principal(cfg):
    """Rodrigo, 2026-11-09: never 'we have a buyer'; a 1031 exchange with a partner of ours, Titan as the principal."""
    from titan.commercial.om_request import om_request, validate_om_request

    for L in (
        {"broker_name": "Armond Aivazyan", "street": "24639 US Hwy 19 N", "address": "24639 US Hwy 19 N", "city": "Clearwater",
         "asset_class": "nnn", "stated_cap": 8.25, "docs_missing": ["T-12", "rent roll", "OM"]},
        {"broker_name": "Jane Doe", "street": "1 Main St", "address": "1 Main St", "city": "Tampa", "asset_class": "retail",
         "docs_missing": ["OM"]},
    ):
        r = om_request(cfg, L)
        assert "partner of ours" in r["body"]
        assert "buyer" not in r["body"].lower()
        assert validate_om_request(r["subject"], r["body"]) == []
    assert "names a buyer" in " ".join(validate_om_request("x", "Hey Jo,\n\nCharles with Titan Realty Investments. I saw it.\n\nOur buyer wants it.\n\nCharles"))


# --- the no-cap blast (Rodrigo, 2026-11-09: one email per broker without a cap rate, no card until they answer) -----------------

def test_cap_question_for_one_and_for_several_listings(cfg):
    from titan.commercial.om_request import cap_question, validate_om_request
    Ls = [{"broker_name": "Jane Doe, CCIM", "street": "1 Main St", "city": "Tampa", "asset_class": "retail"},
          {"street": "2 Oak Ave", "city": "Orlando", "asset_class": "office"}, {"street": "3 Pine Rd", "city": "Miami", "asset_class": "nnn"},
          {"street": "4 Elm St", "city": "Ocala", "asset_class": "nnn"}]
    for n in (1, 2, 4):
        r = cap_question(cfg, Ls[:n])
        assert validate_om_request(r["subject"], r["body"]) == [], (n, r["body"])
        assert "eight cap" in r["body"] and "reassessed" in r["body"] and "partner of ours" in r["body"] and "buyer" not in r["body"].lower()
    assert "1 Main St in Tampa and 3 more." in cap_question(cfg, Ls)["body"]
    assert cap_question(cfg, Ls[:1])["subject"] == "1 Main St, Tampa on LoopNet"
    port = cap_question(cfg, [{"broker_name": "Landy", "street": "Portfolio of 2 Properties For Sale in", "city": "Coral Gables", "asset_class": "retail"}])
    assert "I saw the 2-property listing in Coral Gables on LoopNet." in port["body"] and port["subject"] == "Your 2-property listing, Coral Gables on LoopNet"
    assert validate_om_request(port["subject"], port["body"]) == []


def test_blast_queue_groups_by_broker_sends_at_the_commercial_pace_and_a_reply_makes_the_card(conn, cfg, tmp_path):
    from titan.commercial.blast import build_queue, pending, send_queue
    from titan.commercial.outreach import poll_om_replies, send_om_requests
    from tests.test_approvals import SAT_10AM_ET
    cfg.root = tmp_path
    cfg.raw["commercial"]["asana"]["project"] = "proj-1031"
    cfg.raw["commercial"]["outreach"] = {"per_round": 3, "daily_cap": 100}
    run(conn, cfg, apify=None, raw_rows=ROWS, push_asana=False, email=False, run_date="2026-11-07", today=TODAY)
    conn.execute("UPDATE commercial_listings SET in_box=1, stated_cap=NULL, broker_email='same@broker.com' WHERE listing_id IN "
                 "(SELECT listing_id FROM commercial_listings ORDER BY score DESC LIMIT 2)")
    conn.execute("UPDATE commercial_listings SET in_box=1, stated_cap=NULL, broker_email='solo@broker.com' WHERE listing_id IN "
                 "(SELECT listing_id FROM commercial_listings WHERE broker_email<>'same@broker.com' ORDER BY score DESC LIMIT 1)")
    conn.execute("UPDATE commercial_listings SET in_box=1, stated_cap=8.5 WHERE listing_id IN "
                 "(SELECT listing_id FROM commercial_listings WHERE broker_email NOT IN ('same@broker.com','solo@broker.com') ORDER BY score DESC LIMIT 1)")
    q = build_queue(conn, cfg, "2026-11-09")
    emails = {e["broker_email"]: e for e in q}
    assert len(emails["same@broker.com"]["listing_ids"]) == 2 and "your listings on LoopNet" in emails["same@broker.com"]["body"]
    assert len(emails["solo@broker.com"]["listing_ids"]) == 1
    assert all(conn.execute("SELECT stated_cap FROM commercial_listings WHERE listing_id=?", (i,)).fetchone()[0] is None
               for e in q for i in e["listing_ids"])          # cap-stated listings are the cards' business, not the blast's
    assert (tmp_path / "data/commercial_queue/2026-11-09.json").exists()
    n = len(pending(conn, cfg))
    assert n == len(q) >= 2
    asana = Fake1031Asana()
    gm = FakeOMGmail()
    out = send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)      # no cards in the send column → the round sends the queue
    assert out["queue_sent"] == 3 and len(gm.sent) == 3 and out["queue_waiting"] == n - 3
    th = conn.execute("SELECT * FROM commercial_threads WHERE broker_email='same@broker.com'").fetchone()
    assert th is not None and len(json.loads(th["listing_ids"])) == 2 and th["card_gid"] is None
    # a database that never saw the pull (the Mac) learns the listing from the queue file when it sends
    conn.execute("DELETE FROM commercial_listings WHERE listing_id=?", (json.loads(th["listing_ids"])[0],))
    conn.execute("DELETE FROM commercial_threads WHERE id=?", (th["id"],))
    assert send_queue(conn, cfg, gm, 1, now=SAT_10AM_ET)["sent"] == 1
    assert conn.execute("SELECT street FROM commercial_listings WHERE listing_id=?", (json.loads(th["listing_ids"])[0],)).fetchone()[0]
    th = conn.execute("SELECT * FROM commercial_threads WHERE broker_email='same@broker.com'").fetchone()
    assert conn.execute("SELECT COUNT(*) FROM outreach WHERE kind='om_request' AND status='sent'").fetchone()[0] == 4
    assert len(pending(conn, cfg)) == n - 3                            # a second round never emails the same broker twice
    assert send_queue(conn, cfg, gm, 0, now=SAT_10AM_ET)["sent"] == 0
    # the broker answers → the listing earns its card in "In touch with the agent"
    gm.threads[th["gmail_thread_id"]].append({"id": "r1", "from": "same@broker.com", "body": "Yes, it clears 8 at asking. OM attached soon.",
                                              "internal_ts": 1_700_010_000, "label_ids": [], "attachments": []})
    res = poll_om_replies(conn, cfg, gm, asana, now=SAT_10AM_ET)
    assert res["replies"] == 1
    th2 = conn.execute("SELECT card_gid, state FROM commercial_threads WHERE id=?", (th["id"],)).fetchone()
    assert th2["card_gid"] and th2["state"] == "REPLIED"
    from titan.commercial.board import ensure_board
    assert asana.where[th2["card_gid"]] == ensure_board(asana, "proj-1031")["In touch with the agent"]
    assert any("📩" in c for c in asana.stories[th2["card_gid"]])


def test_bootstrap_loads_the_pull_from_apify_datasets_once_and_builds_the_queue(conn, cfg, tmp_path):
    from titan.commercial.blast import bootstrap, pending
    cfg.root = tmp_path
    cfg.raw["commercial"]["bootstrap"] = {"label": "test pull", "min_listings": 1000, "datasets": ["d1", "d2"]}

    class FakeApify:
        calls = []
        def dataset_items(self, dataset_id, max_items):
            self.calls.append(dataset_id)
            return ROWS if dataset_id == "d1" else []

    ap = FakeApify()
    res = bootstrap(conn, cfg, ap, run_date="2026-11-09")
    assert ap.calls == ["d1", "d2"] and res["loaded"] == conn.execute("SELECT COUNT(*) FROM commercial_listings").fetchone()[0] > 0
    assert res["queued"] == len(pending(conn, cfg))
    assert bootstrap(conn, cfg, ap, run_date="2026-11-09")["skipped"] == "already done" and ap.calls == ["d1", "d2"]
    assert bootstrap(conn, cfg, None)["skipped"] == "no datasets or no Apify"


class FakeReviewLLM:
    available = True
    def __init__(self): self.prompts = []
    def structured(self, purpose, system, user, schema, max_tokens=1024):
        self.prompts.append((purpose, user))
        if "Trailing 12 months actuals" in user:
            return {"verdict": "DOES NOT FIT", "what_they_said": "Adam sent the T-12 and the rent roll.",
                    "books_say": "NOI of $630,000 on the trailing twelve against a $7.895M ask is a 7.98% cap, under the stated 9.35%.",
                    "numbers": ["NOI (T-12) $630,000", "Real cap at ask 7.98%"], "red_flags": ["Stated cap 9.35% is not supported by the books."],
                    "next_step": "NEEDS YOU: decide whether to anchor at $7.875M (8% on the real NOI) or pass.", "needs_rodrigo": True, "reply_draft": ""}
        return {"verdict": "CAN'T TELL YET", "what_they_said": "Adam will send the package in a day.", "books_say": "No documents yet.",
                "numbers": [], "red_flags": [], "next_step": "Wait for the books; Charles thanks him.", "needs_rodrigo": False,
                "reply_draft": "Hey Adam,\n\nThanks, that works. Whenever the trailing twelve and rent roll are ready, send them over and we will turn them around fast.\n\nBest regards,\n\nCharles Grant\nJunior Acquisitions Agent\nTitan Realty Investments\nOffice Line: +1 (561) 405 7379"}


def test_books_review_reads_every_answer_for_rodrigo_and_send_it_sends_the_reply(conn, cfg, tmp_path):
    """Rodrigo, 2026-11-09: 'I need everything to be read for me.' Every answer + its books → one 🧠 READ FOR YOU comment on the card;
    a plain 'send it' under a drafted reply sends it in the same thread; a spreadsheet counts as books and is read; 'leased' → Not a fit."""
    from titan.commercial.books_review import pending, review_pending, send_approved_replies
    from titan.commercial.outreach import poll_om_replies, send_om_requests
    from tests.test_approvals import SAT_10AM_ET
    asana, secs, gid = _om_board(conn, cfg, tmp_path)
    cfg.raw["commercial"]["review"]["auto_reply"] = False                         # the manual path (his "send it")
    gm = FakeOMGmail()
    send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)
    sent_ts = int(SAT_10AM_ET.timestamp())
    conn.execute("UPDATE commercial_threads SET sent_at=?", (iso(SAT_10AM_ET),))
    llm = FakeReviewLLM()
    assert review_pending(conn, cfg, asana, llm)["reviewed"] == 0                 # nothing answered yet: nothing to read
    # 1) a plain answer: read, no card move, Charles's reply drafted
    gm.threads["t1"].append({"id": "r1", "from": "adam@trustrealtyusa.com", "body": "Happy to send the package, give me a day.",
                             "internal_ts": sent_ts + 3 * 3600, "label_ids": [], "attachments": []})
    poll_om_replies(conn, cfg, gm, asana)
    assert [t["listing_id"] for t in pending(conn)] == ["28440860"]
    out = review_pending(conn, cfg, asana, llm)
    assert out["reviewed"] == 1 and out["claude"] == 1 and out["needs_rodrigo"] == 0
    read = [c for c in asana.stories[gid] if "READ FOR YOU" in c]
    assert len(read) == 1 and "CAN'T TELL YET" in read[0] and "Charles's reply" in read[0] and "Hey Adam," in read[0]
    assert asana.where[gid] == secs["In touch with the agent"]
    assert review_pending(conn, cfg, asana, llm)["reviewed"] == 0                 # once per message
    # nothing sends until Rodrigo says so
    assert send_approved_replies(conn, cfg, asana, gm)["waiting"] == 1 and len(gm.sent) == 1
    asana.comment_raw(gid, "send it")
    s = send_approved_replies(conn, cfg, asana, gm)
    assert s["sent"] == 1 and len(gm.sent) == 2 and gm.sent[-1][0] == "adam@trustrealtyusa.com" and gm.sent[-1][1].startswith("Re: ")
    assert any("📤 Charles's reply sent" in c for c in asana.stories[gid])
    assert conn.execute("SELECT COUNT(*) FROM outreach WHERE kind='om_reply' AND status='sent'").fetchone()[0] == 1
    assert send_approved_replies(conn, cfg, asana, gm)["sent"] == 0              # never twice
    # 2) the books come as a SPREADSHEET: filed, read, regraded, card to Books received, Claude's read on the card
    import openpyxl, io
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "T12"
    for row in (("Trailing 12 months actuals",), ("Gross Income", 980000), ("Operating Expenses", 350000), ("Net Operating Income", 630000), ("Occupancy", "92% occupied")):
        ws.append(row)
    buf = io.BytesIO(); wb.save(buf)
    gm.files["r2"] = [{"filename": "CFC Income Statement.xlsx", "mime": "application/vnd.ms-excel", "bytes": buf.getvalue()}]
    gm.threads["t1"].append({"id": "r2", "from": "adam@trustrealtyusa.com", "body": "Attached, owner is negotiable.", "internal_ts": sent_ts + 26 * 3600,
                             "label_ids": [], "attachments": [{"filename": "CFC Income Statement.xlsx"}]})
    out = poll_om_replies(conn, cfg, gm, asana)
    assert out["books"] == 1
    out = review_pending(conn, cfg, asana, llm)
    assert out["reviewed"] == 1 and out["needs_rodrigo"] == 1
    assert "Trailing 12 months actuals" in llm.prompts[-1][1] and "Net Operating Income | $630,000" in llm.prompts[-1][1]   # the spreadsheet was READ
    row = conn.execute("SELECT board_column, stated_noi FROM commercial_listings WHERE listing_id='28440860'").fetchone()
    assert row["board_column"] == "Books received" and row["stated_noi"] == 630_000 and asana.where[gid] == secs["Books received"]
    last = [c for c in asana.stories[gid] if "READ FOR YOU" in c][-1]
    assert "DOES NOT FIT" in last and "NEEDS YOU" in last and "Real cap at ask 7.98%" in last
    # 3) Claude unreachable → rules-only read, marked as such; 'leased' → Not a fit
    gm.threads["t1"].append({"id": "r3", "from": "adam@trustrealtyusa.com", "body": "Update: we just signed a lease for the entire space, it is off market.",
                             "internal_ts": sent_ts + 48 * 3600, "label_ids": [], "attachments": []})
    poll_om_replies(conn, cfg, gm, asana)
    out = review_pending(conn, cfg, asana, llm=None)
    assert out["reviewed"] == 1 and out["rules"] == 1 and out["moved"] == {"Not a fit": 1}
    last = [c for c in asana.stories[gid] if "READ FOR YOU" in c][-1]
    assert "NOT A FIT" in last and "rules only" in last and asana.where[gid] == secs["Not a fit"]
    assert conn.execute("SELECT state FROM commercial_threads").fetchone()[0] == "CLOSED"


def test_spreadsheets_are_read_like_pdfs(tmp_path):
    import openpyxl
    from titan.commercial import documents as d
    folder = tmp_path / "L1"; folder.mkdir()
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(("Rent Roll",)); ws.append(("Suite 100", "Dollar General", 120000)); ws.append(("Net Operating Income", 314638))
    wb.save(folder / "financials.xlsx")
    (folder / "notes.csv").write_text("a,b\n1,2\n")
    files = d.local_files(folder)
    assert [(f["name"], f["kind"]) for f in files] == [("financials.xlsx", "pnl"), ("notes.csv", "pnl")]
    text = d.doc_text(folder / "financials.xlsx")
    assert "Dollar General | $120,000" in text and d.facts_from_text(text)["noi"] == 314638


class FakeTriageLLM:
    available = True
    def structured(self, purpose, system, user, schema, max_tokens=1024):
        sig = "Charles Grant\nJunior Acquisitions Agent\nTitan Realty Investments\nOffice Line: +1 (561) 405 7379"
        user = user.rsplit("--- ", 1)[-1]          # the broker's LAST message decides (the prompt carries the whole thread)
        if "vacant" in user:
            return {"verdict": "NOT A FIT", "what_they_said": "Adam says the building is vacant.", "books_say": "No documents yet.",
                    "numbers": [], "red_flags": [], "needs_rodrigo": False, "next_step": "Pass.",
                    "reply_draft": "Hey Adam,\n\nThanks, a vacant building is not a fit for us.\n\n" + sig, "other_deals": []}
        if "pocket" in user:
            return {"verdict": "DOES NOT FIT", "what_they_said": "Adam says the Orlando site is weak but pitched an off-market Amoco.",
                    "books_say": "No documents yet.", "numbers": [], "red_flags": [], "needs_rodrigo": False,
                    "next_step": "Charles asks for the T-12 on the Amoco.", "reply_draft": "Hey Adam,\n\nThanks. Send the trailing twelve on the Amoco.\n\n" + sig,
                    "other_deals": ["Amoco gas station, West Palm Beach · pocket listing · $3.7M · broker says 8% at that price"]}
        if "NDA" in user:
            return {"verdict": "CAN'T TELL YET", "what_they_said": "Adam wants the NDA signed first.", "books_say": "No documents yet.",
                    "numbers": [], "red_flags": [], "needs_rodrigo": True, "next_step": "NEEDS YOU: sign the NDA.",
                    "reply_draft": "Hey Adam,\n\nWe are reviewing the NDA and will get it back to you shortly.\n\n" + sig, "other_deals": []}
        return {"verdict": "CAN'T TELL YET", "what_they_said": "Adam will send the package tomorrow.", "books_say": "No documents yet.",
                "numbers": [], "red_flags": [], "needs_rodrigo": False, "next_step": "Wait for the books.",
                "reply_draft": "Hey Adam,\n\nThanks, send it over when ready.\n\n" + sig, "other_deals": []}


def _answer(conn, cfg, asana, gm, text, mid, hours, files=None):
    from titan.commercial.outreach import poll_om_replies
    from tests.test_approvals import SAT_10AM_ET
    sent_ts = int(SAT_10AM_ET.timestamp())
    gm.threads["t1"].append({"id": mid, "from": "adam@trustrealtyusa.com", "body": text, "internal_ts": sent_ts + int(hours * 3600),
                             "label_ids": [], "attachments": [{"filename": f["filename"]} for f in (files or [])]})
    if files:
        gm.files[mid] = files
    poll_om_replies(conn, cfg, gm, asana)


def test_simple_replies_go_on_their_own_needs_you_is_forwarded_and_not_fit_is_archived(conn, cfg, tmp_path):
    """Rodrigo, 2026-11-09 evening: "has Charles actually answered any of the agents that were simple answers? … forward me the
    emails that need me … the ones that do not fit, archive them … agents sent us deals on pocket listings."."""
    from titan.commercial.books_review import review_pending, send_approved_replies, triage_inbox
    from titan.commercial.outreach import send_om_requests
    from tests.test_approvals import SAT_10AM_ET
    asana, secs, gid = _om_board(conn, cfg, tmp_path)
    cfg.secrets["REPORT_TO"] = "rodrigo@titanrealtyinvestments.com"
    gm = FakeOMGmail()
    send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)
    conn.execute("UPDATE commercial_threads SET sent_at=?", (iso(SAT_10AM_ET),))
    llm = FakeTriageLLM()
    # 1) a simple answer: the reply goes at the next round with no comment from Rodrigo; nothing forwarded, nothing archived
    _answer(conn, cfg, asana, gm, "Happy to send the package tomorrow.", "r1", 1)
    review_pending(conn, cfg, asana, llm)
    read = [c for c in asana.stories[gid] if "READ FOR YOU" in c][-1]
    assert "goes out at the next round on its own" in read
    assert send_approved_replies(conn, cfg, asana, gm)["sent"] == 1 and gm.sent[-1][0] == "adam@trustrealtyusa.com"
    assert triage_inbox(conn, cfg, asana, gm) == {"forwarded": 0, "archived": 0, "waiting": 0, "errors": 0}
    # 2) "hold" under the review stops the next one
    _answer(conn, cfg, asana, gm, "Package coming tomorrow, promise.", "r2", 2)
    review_pending(conn, cfg, asana, llm)
    asana.comment_raw(gid, "hold, let me look at this one")
    assert send_approved_replies(conn, cfg, asana, gm)["waiting"] == 1 and len(gm.sent) == 2
    # 3) NEEDS YOU (an NDA): the reply waits for him; the email is forwarded to his inbox with the file attached, once
    nda = [{"filename": "CA-NDA.pdf", "mime": "application/pdf", "bytes": b"%PDF-1.4 nda"}]
    _answer(conn, cfg, asana, gm, "Please sign the NDA attached before I release the OM.", "r3", 3, files=nda)
    out = review_pending(conn, cfg, asana, llm)
    assert out["needs_rodrigo"] == 1
    assert send_approved_replies(conn, cfg, asana, gm)["waiting"] == 1
    tri = triage_inbox(conn, cfg, asana, gm)
    assert tri["forwarded"] == 1 and tri["archived"] == 0
    to, subject, body = gm.sent[-1]
    assert to == "rodrigo@titanrealtyinvestments.com" and subject.startswith("FW [NEEDS YOU]") and "840 SW 81st Ave" in subject
    assert "NEEDS YOU: sign the NDA." in body and "Please sign the NDA attached" in body and "app.asana.com" in body
    assert gm.kw[-1]["attachments"][0]["filename"] == "CA-NDA.pdf"
    assert any("📬 Forwarded to your inbox (needs you)" in c for c in asana.stories[gid])
    assert triage_inbox(conn, cfg, asana, gm)["forwarded"] == 0                  # once
    assert not getattr(gm, "archived", [])
    asana.comment_raw(gid, "ok send it")                                         # his word is still what sends a NEEDS YOU reply
    assert send_approved_replies(conn, cfg, asana, gm)["sent"] == 1 and "reviewing the NDA" in gm.sent[-1][2]
    # 4) a pocket listing pitched in a DOES NOT FIT answer: forwarded (not archived), the deal named on the card
    _answer(conn, cfg, asana, gm, "Orlando is weak, but I have a pocket listing, an Amoco in West Palm at $3.7M.", "r4", 4)
    out = review_pending(conn, cfg, asana, llm)
    assert out["other_deals"] == 1
    read = [c for c in asana.stories[gid] if "READ FOR YOU" in c][-1]
    assert "Other deals they pitched" in read and "Amoco" in read
    send_approved_replies(conn, cfg, asana, gm)                                   # Charles asks for the T-12 on his own
    tri = triage_inbox(conn, cfg, asana, gm)
    assert tri["forwarded"] == 1 and tri["archived"] == 0 and gm.sent[-1][1].startswith("FW [POCKET / OTHER DEALS]")
    assert "Amoco gas station, West Palm Beach" in gm.sent[-1][2]
    # 5) a flat NOT A FIT: Charles's pass note goes first, then the thread is archived in his inbox
    _answer(conn, cfg, asana, gm, "Actually the building is vacant now.", "r5", 5)
    review_pending(conn, cfg, asana, llm)
    assert triage_inbox(conn, cfg, asana, gm)["waiting"] == 1                   # reply not sent yet: not archived
    assert send_approved_replies(conn, cfg, asana, gm)["sent"] == 1
    tri = triage_inbox(conn, cfg, asana, gm)
    assert tri["archived"] == 1 and gm.archived == ["t1"]
    assert any("🗂 Archived in Charles's inbox" in c for c in asana.stories[gid])
    assert triage_inbox(conn, cfg, asana, gm)["archived"] == 0                   # once


def test_charles_answers_books_asks_for_books_and_passes_politely_on_his_own(conn, cfg, tmp_path):
    """Rodrigo, 2026-11-10: "if someone sends all of the information … answer them before I can get to it … anything but the books
    or questions, grab the conversation, can you please send us the books … answer politely to anyone that it's not a fit … plant the
    seed, pocket listings, distressed properties, apartments." Each fixed reply goes on its own, once per thread."""
    from titan.commercial.books_review import review_pending, send_approved_replies, template_reply
    from titan.commercial.om_request import validate_om_request
    from titan.commercial.outreach import send_om_requests
    from tests.test_approvals import SAT_10AM_ET
    asana, secs, gid = _om_board(conn, cfg, tmp_path)
    gm = FakeOMGmail()
    send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)
    conn.execute("UPDATE commercial_threads SET sent_at=?, broker_name='Adam Smith'", (iso(SAT_10AM_ET),))
    llm = FakeTriageLLM()
    # 1) an answer with no books and no question → "can you please send us the books", on its own
    _answer(conn, cfg, asana, gm, "Thanks for reaching out, it is a great property with a strong tenant.", "r1", 1)
    review_pending(conn, cfg, asana, llm)
    assert send_approved_replies(conn, cfg, asana, gm)["sent"] == 1
    body = gm.sent[-1][2]
    assert body.startswith("Hey Adam,") and "Can you please send us the books?" in body and "840 SW 81st Ave" in body and "Charles Grant" in body
    assert conn.execute("SELECT reply_kind FROM commercial_reviews ORDER BY id DESC").fetchone()[0] == "ask_books"
    # 2) a question gets Claude's answer, not the fixed ask (and never a second ask)
    _answer(conn, cfg, asana, gm, "Is the exchange already open? Happy to send the package tomorrow.", "r2", 2)
    review_pending(conn, cfg, asana, llm)
    assert conn.execute("SELECT reply_kind FROM commercial_reviews ORDER BY id DESC").fetchone()[0] is None
    # 3) the books arrive → thanks + answer within 24 hours, even though the read NEEDS him
    import openpyxl, io
    wb = openpyxl.Workbook(); ws = wb.active
    for row in (("Trailing 12 months actuals",), ("Net Operating Income", 630000)):
        ws.append(row)
    buf = io.BytesIO(); wb.save(buf)
    _answer(conn, cfg, asana, gm, "Please sign the NDA too. T-12 attached.", "r3", 3,
            files=[{"filename": "T12.xlsx", "mime": "application/vnd.ms-excel", "bytes": buf.getvalue()}])
    out = review_pending(conn, cfg, asana, llm)
    assert out["needs_rodrigo"] == 1
    send_approved_replies(conn, cfg, asana, gm)
    body = gm.sent[-1][2]
    assert body.startswith("Hey Adam,") and "840 SW 81st Ave" in body and "24 hours" in body
    # 4) not a fit → polite pass that plants the seed; then the thread is archived as before
    _answer(conn, cfg, asana, gm, "Update: the building is vacant now.", "r4", 4)
    review_pending(conn, cfg, asana, llm)
    send_approved_replies(conn, cfg, asana, gm)
    body = gm.sent[-1][2]
    assert "This one won't work for us, but we are always buying." in body and "pocket listings" in body and "apartment buildings" in body
    # the fixed replies never call us someone's agent
    for k, o in (("books_ack", {}), ("ask_books", {}), ("ask_books", {"later": True}), ("pass", {}), ("pass", {"sold": True})):
        t = template_reply(k, "Adam", cfg, **o)
        assert not re.search(r"\b(?:buyer|client|investor)s?\b", t, re.I), t


def test_rodrigo_writing_in_the_thread_shows_on_the_card_logs_the_offer_and_silences_charles(conn, cfg, tmp_path):
    """Rodrigo, 2026-11-10 (Byronessa): Charles cc'd him, he answers the broker himself. The card must read it, the offer moves the
    card to Offer out, and Charles must not auto-answer inside his negotiation."""
    from titan.commercial.books_review import review_pending, send_approved_replies, triage_inbox
    from titan.commercial.outreach import poll_om_replies, send_om_requests
    from tests.test_approvals import SAT_10AM_ET
    asana, secs, gid = _om_board(conn, cfg, tmp_path)
    cfg.secrets["REPORT_TO"] = "rodrigo@titanrealtyinvestments.com"
    gm = FakeOMGmail()
    send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)
    conn.execute("UPDATE commercial_threads SET sent_at=?", (iso(SAT_10AM_ET),))
    ask = conn.execute("SELECT price FROM commercial_listings WHERE listing_id='28440860'").fetchone()[0]
    offer = int(ask * 0.7)
    gm.threads["t1"].append({"id": "x1", "from": "Rodrigo D. Gayoso <rodrigo@titanrealtyinvestments.com>",
                             "body": f"Hi Adam,\n\nOur offer is ${offer:,} cash with a $100,000 deposit.\n\nOn Mon wrote:\n> old", "internal_ts": int(SAT_10AM_ET.timestamp()) + 7200,
                             "label_ids": [], "attachments": []})
    out = poll_om_replies(conn, cfg, gm, asana)
    assert out["rodrigo"] == 1 and out["offers"] == 1 and out["replies"] == 0
    note = [c for c in asana.stories[gid] if "✉️ You wrote" in c]
    assert len(note) == 1 and f"Offer out: ${offer:,}" in note[0] and "> old" not in note[0]
    assert asana.where[gid] == secs["Offer out"]
    assert poll_om_replies(conn, cfg, gm, asana)["rodrigo"] == 0                  # once
    assert conn.execute("SELECT COUNT(*) FROM outreach WHERE kind='om_rodrigo'").fetchone()[0] == 1
    # the broker answers (not a fit, no books, anything): reviewed for him, but nothing auto-sends and nothing is archived
    _answer(conn, cfg, asana, gm, "Building is vacant now, sorry.", "r1", 3)
    review_pending(conn, cfg, asana, FakeTriageLLM())
    read = [c for c in asana.stories[gid] if "READ FOR YOU" in c][-1]
    assert "you are writing to this broker yourself" in read
    n = len(gm.sent)
    assert send_approved_replies(conn, cfg, asana, gm)["waiting"] == 1 and len(gm.sent) == n
    assert triage_inbox(conn, cfg, asana, gm)["archived"] == 0
    assert asana.where[gid] == secs["Offer out"]                                    # the offer card does not jump back


def test_deal_sheet_from_the_card_shows_inside_numbers_privately_and_only_our_price_to_the_investor(conn, cfg, tmp_path):
    """Rodrigo, 2026-11-10 (Byronessa): "an internal and an external side … internal tells me what we are getting it for and how it
    makes sense, the outside what works for them, the price we will sell it at." Numbers come from a DEAL SHEET comment on the
    card (never the public repo); his own offer email fills 'Our offer' by itself."""
    from titan.commercial.board import ensure_board
    from titan.commercial.deal_sheet import parse_sheet, sync
    from titan.commercial.outreach import poll_om_replies, send_om_requests
    from titan.commercial.site import build_deals
    from tests.test_approvals import SAT_10AM_ET
    asana, secs, gid = _om_board(conn, cfg, tmp_path)
    gm = FakeOMGmail()
    send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)                         # the broker thread exists (card moves on later)
    s = parse_sheet("📊 DEAL SHEET\nAsking: $4,590,000\nOur offer: $3,000,000 (drafted, not sent)\nOur ceiling: $3.3M\nInvestor price: 3,550,000\n"
                    "Investor cap: 8.0%\nReal NOI: $372,604\nInternal: taxes reset at sale\nInternal: no payroll for a 24/7 desk\nInvestor: #1 rated on Booking.com")
    assert s["our_offer"] == 3_000_000 and s["offer_status"] == "drafted, not sent" and s["ceiling"] == 3_300_000 and s["investor_cap"] == 8.0
    assert s["internal"] == ["taxes reset at sale", "no payroll for a 24/7 desk"] and s["investor"] == ["#1 rated on Booking.com"]
    asana.move(gid, secs["Books received"])
    asana.project_tasks = lambda project, completed_since="now": [{**dict(t), "gid": g, "memberships": [{"project": {"gid": "proj-1031"}, "section": {"name": next(n for n, sg in secs.items() if sg == asana.where[g])}}]}
                                                                  for g, t in asana.tasks.items()]
    asana.comment_raw(gid, "📊 DEAL SHEET\nOur offer: $3,000,000 (drafted)\nOur ceiling: $3,300,000\nInvestor price: $3,550,000\nInvestor cap: 8.0%\n"
                           "Internal: taxes reset at sale\nInvestor: #1 rated on Booking.com")
    out = sync(conn, cfg, asana, publish=False)
    assert out["sheets"] == 1 and out["changed"] == 1
    assert sync(conn, cfg, asana, publish=False)["changed"] == 0                     # unchanged: nothing rewritten
    asana.comment_raw(gid, "DEAL SHEET\nTheir counter: $4.1M")                         # a one-line update merges in
    sync(conn, cfg, asana, publish=False)
    snap = build_deals(conn, cfg)
    lid = "28440860"
    pub = next(d for d in snap["deals"] if d["listing_id"] == lid)
    deal = snap["private"][lid]["deal"]
    ask = snap["private"][lid]["asking"]
    assert deal["counter"] == 4_100_000 and deal["our_offer"] == 3_000_000 and deal["spread"] == 550_000 and deal["investor_noi"] == 284_000
    assert pub["price"] == 3_550_000 and pub["price_text"] == "Titan price" and pub["listing_url"] is None and pub["why"] == "#1 rated on Booking.com"
    leak = json.dumps(pub)
    for secret in ("3000000", "3300000", "4100000", str(ask), "taxes reset", "loopnet.com/Listing"):
        assert secret not in leak, secret
    # Rodrigo's own email to the broker becomes the offer, marked sent, newer than the sheet's draft
    cfg.secrets["REPORT_TO"] = "rodrigo@titanrealtyinvestments.com"
    gm.threads["t1"].append({"id": "x1", "from": "rodrigo@titanrealtyinvestments.com", "body": "Hi Adam,\n\nPurchase price: $3,100,000, all cash.",
                             "internal_ts": 4_000_000_000, "label_ids": [], "attachments": []})
    poll_om_replies(conn, cfg, gm, asana)
    sync(conn, cfg, asana, publish=False)
    deal = build_deals(conn, cfg)["private"][lid]["deal"]
    assert deal["our_offer"] == 3_100_000 and deal["offer_status"].startswith("sent ")


def test_cards_that_need_rodrigo_land_in_needs_me_once_and_overlapping_rounds_never_double_send(conn, cfg, tmp_path):
    """Rodrigo, 2026-11-10: "why are all of the emails that I am getting on the people that need me not on my board?" The card behind
    every forward goes to the 1031 board's Needs Me column (never out of Offer out / Closed / Not a fit); his 'send it' takes it back to
    In touch. Two rounds at once (check-replies + approvals) forwarded Luca and Cameron twice: a review is claimed before acting."""
    from titan.commercial.board import NEEDS_ME
    from titan.commercial.books_review import review_pending, send_approved_replies, triage_inbox
    from titan.commercial.outreach import send_om_requests
    from tests.test_approvals import SAT_10AM_ET
    asana, secs, gid = _om_board(conn, cfg, tmp_path)
    secs = __import__("titan.commercial.board", fromlist=["ensure_board"]).ensure_board(asana, "proj-1031")
    assert NEEDS_ME in secs
    cfg.secrets["REPORT_TO"] = "rodrigo@titanrealtyinvestments.com"
    gm = FakeOMGmail()
    send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)
    conn.execute("UPDATE commercial_threads SET sent_at=?", (iso(SAT_10AM_ET),))
    _answer(conn, cfg, asana, gm, "Please sign the NDA attached before I release the OM.", "r1", 1)
    review_pending(conn, cfg, asana, FakeTriageLLM())
    # a second round racing the first: the claim is already taken, so it does nothing
    rid = conn.execute("SELECT id FROM commercial_reviews ORDER BY id DESC").fetchone()[0]
    conn.execute("UPDATE commercial_reviews SET forwarded_at='x' WHERE id=?", (rid,))
    n = len(gm.sent)
    out = triage_inbox(conn, cfg, asana, gm)
    assert out["forwarded"] == 0 and len(gm.sent) == n and out.get("parked") == 1 and asana.where[gid] == secs[NEEDS_ME]
    conn.execute("UPDATE commercial_reviews SET forwarded_at=NULL WHERE id=?", (rid,))
    out = triage_inbox(conn, cfg, asana, gm)
    assert out["forwarded"] == 1 and out.get("parked") is None                   # parked once: he can drag it away and it stays away
    asana.comment_raw(gid, "send it")
    assert send_approved_replies(conn, cfg, asana, gm)["sent"] == 1
    assert asana.where[gid] == secs["In touch with the agent"]                    # his move is done
    assert send_approved_replies(conn, cfg, asana, gm)["sent"] == 0               # and never twice
    # a card he put in Offer out is never pulled into Needs Me
    asana.move(gid, secs["Offer out"])
    conn.execute("UPDATE commercial_listings SET board_column='Offer out'")
    _answer(conn, cfg, asana, gm, "Also please sign this second NDA.", "r2", 2)
    review_pending(conn, cfg, asana, FakeTriageLLM())
    triage_inbox(conn, cfg, asana, gm)
    assert asana.where[gid] == secs["Offer out"]
    # a move that fails is retried next round, not marked done
    class Boom(Exception): pass
    _answer(conn, cfg, asana, gm, "One more NDA, sorry.", "r3", 3)
    review_pending(conn, cfg, asana, FakeTriageLLM())
    asana.move(gid, secs["In touch with the agent"]); conn.execute("UPDATE commercial_listings SET board_column='In touch with the agent'")
    real_move = asana.move
    asana.move = lambda *a: (_ for _ in ()).throw(Boom("Asana 500"))
    triage_inbox(conn, cfg, asana, gm)
    assert conn.execute("SELECT parked_at FROM commercial_reviews ORDER BY id DESC").fetchone()[0] is None
    asana.move = real_move
    assert triage_inbox(conn, cfg, asana, gm).get("parked") == 1 and asana.where[gid] == secs[NEEDS_ME]



class FakeNoteLLM:
    available = True
    def __init__(self): self.calls = []
    def structured(self, purpose, system, user, schema, max_tokens=1024):
        self.calls.append(purpose)
        if purpose == "commercial_card_chat":
            return {"answer": "Pass at the ask: the real cap is 7.4 after the tax reset. Aim at $3.2M."}
        return {"email": "Hey Adam,\n\nWe will sign the NDA today. Please send the trailing twelve and the rent roll once it is back.\n\n"
                         "Charles Grant\nJunior Acquisitions Agent\nTitan Realty Investments\nOffice Line: +1 (561) 405 7379"}


def test_the_1031_board_talks_back_notes_become_emails_questions_get_answers(conn, cfg, tmp_path):
    """Rodrigo, 2026-11-10: "can I just put a message on the comments and it will read it when I put it in Approved, like the other
    board … have this whole board have conversations with us … if I send it to Needs Me, it can answer me back what it thinks"."""
    from datetime import timedelta
    from titan.commercial.board import NEEDS_ME, SEND, ensure_board
    from titan.commercial.books_review import send_approved_replies
    from titan.commercial.card_notes import handle_notes, needs_me_takes
    from titan.commercial.outreach import send_om_requests
    from tests.test_approvals import SAT_10AM_ET
    asana, secs, gid = _om_board(conn, cfg, tmp_path)
    secs = ensure_board(asana, "proj-1031")
    gm = FakeOMGmail()
    send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)
    conn.execute("UPDATE commercial_threads SET sent_at=?, broker_name='Adam Smith'", (iso(SAT_10AM_ET),))
    llm = FakeNoteLLM()
    now = SAT_10AM_ET + timedelta(hours=1)
    when = {}
    raw = asana.comment_raw
    def comment_raw(task, text):                   # each of his comments keeps the time it was written (5 minutes before "now")
        when[text] = iso(now - timedelta(minutes=5)); raw(task, text)
    asana.comment_raw = comment_raw
    asana.comments = lambda task: [{"text": t, "created_at": when.get(t, "2000-01-01")} for t in asana.stories[task]]
    # 0) a DEAL SHEET update is never read as a note
    asana.comment_raw(gid, "📊 DEAL SHEET\nInternal: ask Luca where the seller would trade")
    assert handle_notes(conn, cfg, asana, llm, now=now)["notes"] == 0
    # 1) a note telling Charles what to say → drafted on the card, NOT sent
    now += timedelta(minutes=1)
    asana.comment_raw(gid, "tell him we will sign the NDA and ask for the T-12")
    out = handle_notes(conn, cfg, asana, llm, now=now)
    assert out["drafted"] == 1 and any("✍️ From your note (not sent" in c for c in asana.stories[gid])
    n = len(gm.sent)
    assert send_approved_replies(conn, cfg, asana, gm, now=now)["sent"] == 0 and len(gm.sent) == n
    # 2) he drags the card to Approved → the waiting draft is approved and goes out in the broker's thread
    asana.move(gid, secs[SEND])
    assert send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET).get("approved") == 1
    assert asana.where[gid] == secs["In touch with the agent"]
    assert send_approved_replies(conn, cfg, asana, gm, now=now)["sent"] == 1 and "sign the NDA" in gm.sent[-1][2]
    assert gm.sent[-1][0] == "adam@trustrealtyusa.com" and gm.kw[-1].get("thread_id") == "t1"
    # 3) a question gets a 💬 answer on the card; nothing is drafted or sent
    now += timedelta(hours=1)
    asana.comment_raw(gid, "what do you think, is this worth chasing?")
    out = handle_notes(conn, cfg, asana, llm, now=now)
    assert out.get("answered") == 1 and out["drafted"] == 0
    assert any(c.startswith("🤖 💬 Pass at the ask") for c in asana.stories[gid])
    # 4) a note that ends with "send it" goes on its own
    now += timedelta(hours=1)
    asana.comment_raw(gid, "tell him we will sign the NDA and send it")
    handle_notes(conn, cfg, asana, llm, now=now)
    assert send_approved_replies(conn, cfg, asana, gm, now=now)["sent"] == 1
    # 5) a card in Needs Me gets ONE take, until something new happens
    asana.move(gid, secs[NEEDS_ME])
    conn.execute("UPDATE commercial_listings SET board_column=?", (NEEDS_ME,))
    assert needs_me_takes(conn, cfg, asana, llm)["takes"] == 1
    assert any("💬 What I think" in c for c in asana.stories[gid])
    assert needs_me_takes(conn, cfg, asana, llm)["takes"] == 0


def test_no_dates_no_commitments_no_fake_attachments_go_out_on_their_own(conn, cfg, tmp_path):
    """Rodrigo, 2026-11-10 (Adriana): "let's not have us answer or say anything that has dates involved, or committing us to do
    anything like sending anything signed or POF." 21 of 25 replies on 11-09/10 said "identify within 45 days", offered proof of
    funds, or claimed a signed NDA was attached. Those drafts now wait for his 'send it'; nothing goes out after 5:30pm ET."""
    from titan.commercial.books_review import commits_us, send_approved_replies
    bad = ["We need to identify within 45 days and are moving now.", "We can provide proof of funds on request.",
           "The signed NDA is attached.", "We're reviewing the P&Ls now.", "We will send the LOI by Friday."]
    for b in bad:
        assert commits_us(f"Hey Adam,\n\n{b}\n\nCharles Grant\nJunior Acquisitions Agent"), b
    assert not commits_us("Hey Adam,\n\nThanks. Please send the trailing twelve and the rent roll.\n\nCharles Grant\nJunior Acquisitions Agent\n"
                          "Titan Realty Investments\nOffice Line: +1 (561) 405 7379")
    from titan.commercial.outreach import send_om_requests
    from tests.test_approvals import SAT_10AM_ET
    asana, secs, gid = _om_board(conn, cfg, tmp_path)
    gm = FakeOMGmail()
    send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)
    assert "proof of funds" not in gm.sent[-1][2].lower()                        # the first email no longer offers it either
    tid = conn.execute("SELECT id, gmail_message_id FROM commercial_threads").fetchone()
    conn.execute("INSERT INTO commercial_reviews (thread_id, listing_id, verdict, needs_rodrigo, text, source, reply_draft, created_at) "
                 "VALUES (?,?,?,?,?,?,?,?)", (tid[0], "28440860", "CAN'T TELL YET", 0, "old read", "claude",
                                              "Hey Adam,\n\nWe need to identify within 45 days and are moving now.\n\nCharles Grant", iso(SAT_10AM_ET)))
    n = len(gm.sent)
    assert send_approved_replies(conn, cfg, asana, gm, now=SAT_10AM_ET)["waiting"] == 1 and len(gm.sent) == n   # an old auto draft is held
    cfg.raw["outreach"]["asana"]["send_window"] = {"start": "08:30", "end": "17:30"}
    asana.comment_raw(gid, "send it")
    from datetime import timedelta
    late = SAT_10AM_ET.replace(hour=23)                                           # 7pm ET: his go-ahead waits for the morning
    assert send_approved_replies(conn, cfg, asana, gm, now=late)["sent"] == 0
    assert send_approved_replies(conn, cfg, asana, gm, now=SAT_10AM_ET + timedelta(minutes=5))["sent"] == 1



def test_needs_revision_column_and_the_read_gives_the_true_8_cap_pros_cons_and_doubts(conn, cfg, tmp_path):
    """Rodrigo, 2026-11-10: "a section that says needs revision this way I can chat with the agent there … give me the data on what our
    assumption is on where we need to be for a true 8 cap, then the pros and cons, what it thinks they're lying about, and where we agree"."""
    from datetime import timedelta
    from titan.commercial.board import NEEDS_ME, NEEDS_REVISION, ensure_board
    from titan.commercial.books_review import format_review
    from titan.commercial.card_notes import handle_notes
    from titan.commercial.outreach import send_om_requests
    from tests.test_approvals import SAT_10AM_ET
    text = format_review({"verdict": "LEANS FIT", "what_they_said": "x", "books_say": "y", "numbers": [], "red_flags": [], "next_step": "z",
                          "eight_cap": ["NOI used $372,604 (T-12)", "True 8 cap at $3.24M (ask $4.59M)"], "pros": ["#1 rated"], "cons": ["owner-run"],
                          "doubts": ["no payroll in the books"], "believe": ["revenue ties to the OTA statements"]})
    for part in ("Where it has to be for a true 8 cap", "True 8 cap at $3.24M", "Pros:", "Cons:", "What I don't believe", "What checks out:"):
        assert part in text, part
    asana, secs, gid = _om_board(conn, cfg, tmp_path)
    secs = ensure_board(asana, "proj-1031")
    assert NEEDS_REVISION in secs
    gm = FakeOMGmail()
    send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)
    llm = FakeNoteLLM()
    now = SAT_10AM_ET + timedelta(hours=1)
    when = {}
    raw = asana.comment_raw
    def comment_raw(task, text):
        when[text] = iso(now - timedelta(minutes=5)); raw(task, text)
    asana.comment_raw = comment_raw
    asana.comments = lambda task: [{"text": t, "created_at": when.get(t, "2000-01-01")} for t in asana.stories[task]]
    # dragged to Needs Revision with no comment → straight back to Needs Me, with one line
    asana.move(gid, secs[NEEDS_REVISION])
    handle_notes(conn, cfg, asana, llm, now=now)
    assert asana.where[gid] == secs[NEEDS_ME] and any("no comment on it" in c for c in asana.stories[gid])
    # with a question → answered on the card, then to Needs Me
    now += timedelta(minutes=10)
    asana.move(gid, secs[NEEDS_REVISION])
    asana.comment_raw(gid, "why don't you believe the cap?")
    out = handle_notes(conn, cfg, asana, llm, now=now)
    assert out.get("answered") == 1 and asana.where[gid] == secs[NEEDS_ME]


def test_reviewed_column_his_answer_is_acted_on_then_the_card_comes_back_to_needs_me(conn, cfg, tmp_path):
    """Rodrigo, 2026-11-10: "once I answer the question on the chat I need to drag it somewhere … a card for everyone that I have answered
    already, and then once it's answered it can be dragged to Needs Me again"."""
    from datetime import timedelta
    from titan.commercial.board import NEEDS_ME, REVIEWED, ensure_board
    from titan.commercial.card_notes import handle_notes
    from titan.commercial.outreach import send_om_requests
    from tests.test_approvals import SAT_10AM_ET
    asana, secs, gid = _om_board(conn, cfg, tmp_path)
    secs = ensure_board(asana, "proj-1031")
    gm = FakeOMGmail()
    send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)
    llm = FakeNoteLLM()
    now = SAT_10AM_ET + timedelta(hours=1)
    when = {}
    raw = asana.comment_raw
    def comment_raw(task, text):
        when[text] = iso(now - timedelta(minutes=5)); raw(task, text)
    asana.comment_raw = comment_raw
    asana.comments = lambda task: [{"text": t, "created_at": when.get(t, "2000-01-01")} for t in asana.stories[task]]
    # in Reviewed with nothing new: it stays there
    asana.move(gid, secs[REVIEWED])
    handle_notes(conn, cfg, asana, llm, now=now)
    assert asana.where[gid] == secs[REVIEWED]
    # he answers on the card ("tell him we will sign the NDA") and leaves it in Reviewed → Charles's email is drafted, card back to Needs Me
    now += timedelta(minutes=10)
    asana.comment_raw(gid, "tell him we will sign the NDA and ask for the T-12")
    out = handle_notes(conn, cfg, asana, llm, now=now)
    assert out["drafted"] == 1 and asana.where[gid] == secs[NEEDS_ME]


def test_financials_dropped_on_a_1031_card_are_filed_read_and_never_trigger_an_email(conn, cfg, tmp_path):
    """Rodrigo, 2026-11-10: "once I get all of the info from the finances on the deals … should I drop them on the Asana card …
    yes let's do that". A spreadsheet attached to the card lands in data/listings/<id>/, is read once, and a fresh read is posted."""
    import io, openpyxl
    from titan.commercial.card_files import pull_card_files
    from titan.commercial.outreach import send_om_requests
    from tests.test_approvals import SAT_10AM_ET
    asana, secs, gid = _om_board(conn, cfg, tmp_path)
    gm = FakeOMGmail()
    send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)
    wb = openpyxl.Workbook(); ws = wb.active
    for row in (("Trailing 12 months actuals",), ("Net Operating Income", 610000)):
        ws.append(row)
    buf = io.BytesIO(); wb.save(buf)
    files = {"https://s3/t12": buf.getvalue()}
    asana.project_tasks = lambda project, completed_since="now": [{"gid": g, "name": t.get("name"), "modified_at": "2026-11-10T15:00:00Z"} for g, t in asana.tasks.items()]
    asana.attachments = lambda task: ([{"gid": "att1", "name": "T-12 2026.xlsx", "download_url": "https://s3/t12", "size": len(files["https://s3/t12"])},
                                       {"gid": "att2", "name": "photo.png", "download_url": "https://s3/p"}] if task == gid else [])
    asana.download = lambda url: files[url]
    files["https://s3/p"] = b"\x89PNG fake"
    class ShotLLM(FakeReviewLLM):                       # a screenshot of numbers is typed out by Claude and read as text
        def transcribe_image(self, purpose, data, media_type, prompt, schema, max_tokens=4000):
            return {"kind": "pnl", "text": "Revenue YTD 2026 | $402,000\nNet Operating Income YTD | $301,000"}
    n = len(gm.sent)
    from titan.commercial.board import NEEDS_ME, NEEDS_REVISION, ensure_board
    secs = ensure_board(asana, "proj-1031")
    asana.move(gid, secs[NEEDS_REVISION])               # he drops the files and leaves the card in Needs Revision
    out = pull_card_files(conn, cfg, asana, ShotLLM(), now=SAT_10AM_ET)
    assert out["files"] == 2 and out["reads"] == 1
    assert (tmp_path / "data" / "listings" / "28440860" / "P&L - photo (from screenshot).txt").read_text().count("$301,000") == 1
    assert asana.where[gid] == secs[NEEDS_ME]           # read, then back to Needs Me
    saved = tmp_path / "data" / "listings" / "28440860" / "T-12 2026.xlsx"
    assert saved.exists()
    assert any("📎 Filed your file(s)" in c and "T-12 2026.xlsx" in c for c in asana.stories[gid])
    assert any("READ FOR YOU" in c for c in asana.stories[gid])
    assert conn.execute("SELECT reply_draft FROM commercial_reviews ORDER BY id DESC").fetchone()[0] is None   # nothing drafted to the broker
    assert len(gm.sent) == n
    # the same card checked again: nothing new, nothing read twice
    asana.project_tasks = lambda project, completed_since="now": [{"gid": g, "modified_at": "2026-11-10T16:00:00Z"} for g in asana.tasks]
    assert pull_card_files(conn, cfg, asana, FakeReviewLLM(), now=SAT_10AM_ET)["files"] == 0
