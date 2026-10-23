from datetime import date
from pathlib import Path

from titan.ingest import mls_csv
from titan.ingest.normalize import passes_filters

D = Path(__file__).parent / "fixtures" / "mls"
TODAY = date(2026, 10, 23)


def _by_mls(name):
    return {l.source_id: l for l in mls_csv.parse_file(D / name, as_of=TODAY)}


def test_area_code_to_county():
    assert mls_csv.county_from_area("22") == "Miami-Dade"
    assert mls_csv.county_from_area("3180") == "Broward"
    assert mls_csv.county_from_area("5590") == "Palm Beach"
    assert mls_csv.county_from_area("6090") is None            # Treasure Coast
    assert mls_csv.county_from_area("98") is None              # Keys
    assert mls_csv.county_from_area("5940 Florida Other") is None


def test_hot_sheet_price_cut_row(cfg):
    l = _by_mls("hot_sheet.csv")["A11000001"]
    assert (l.street, l.county, l.list_price, l.property_type, l.source) == ("1520 NW 4th Avenue", "Broward", 360000, "single_family", "mls")
    assert l.price_cuts == 1 and l.original_list_price == 380000       # 5.26% off
    assert l.last_price_change_at.startswith("2026-10-23")
    assert l.beds == 3 and l.baths == 2 and l.sqft == 1210
    assert passes_filters(l, cfg, today=None)[0]


def test_hot_sheet_types_status_and_bad_rows():
    ls = _by_mls("hot_sheet.csv")
    assert ls["A11000002"].property_type == "condo" and ls["A11000002"].street == "2775 NE 187th St Unit 424"
    assert ls["A11000003"].property_type == "townhouse" and ls["A11000003"].back_on_market   # temp-off → active
    assert ls["A11000004"].price_cuts == 0 and ls["A11000004"].county is None              # 900% is a typo fix; out of area
    assert "pending" in ls["A11000005"].listing_status


def test_full_matrix_export_has_agent_and_remarks(cfg):
    l = _by_mls("full_export.csv")["F10000009"]
    assert (l.city, l.zip, l.county) == ("Homestead", "33030", "Miami-Dade")
    assert l.agent_name == "Ana Perez" and l.agent_email == "ana@example.com" and l.agent_phone == "+13055550199"
    assert l.brokerage == "Perez Realty" and l.days_on_market == 74 and l.original_list_price == 349000
    assert "estate" in l.status_flags and "cash" in l.remarks.lower()
    assert l.listed_at.startswith("2026-07-12")
    assert passes_filters(l, cfg)[0]


def test_parse_dir_reads_every_csv():
    assert len(mls_csv.parse_dir(D)) == 6
