"""Commercial screen for the 1031 buyer (2026-11-08): LoopNet rows → extraction → score / tag / grade → files, broker list, Asana cards, diff."""
import json
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
        self.threads[f"t{self._n}"] = [{"id": f"m{self._n}", "from": "Charles@titanrealtyinvestments.com", "body": body, "internal_ts": 1_700_000_000, "label_ids": [], "attachments": []}]
        return {"message_id": f"m{self._n}", "thread_id": f"t{self._n}"}

    def thread_messages(self, tid): return list(self.threads.get(tid, []))
    def attachments(self, mid): return self.files.get(mid, [])


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
