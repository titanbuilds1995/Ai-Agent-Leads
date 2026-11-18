"""No 55+ communities, no townhouses (Rodrigo, 2026-11-18): flagged at ingest, ruled out in the database, cards off the board."""
import json

from titan import product_clean
from titan.approvals import parse_card
from titan.ingest.normalize import detect_flags, exclude_flagged, product_flags
from tests.test_approvals import _cards, _sections


def test_flags_and_negations():
    assert "age_restricted" in detect_flags("Beautiful villa in a 55+ community, clubhouse and pool")
    assert "age_restricted" in detect_flags("Active adult living at its best")
    assert "age_restricted" in detect_flags("Located in Kings Point, Delray Beach")
    assert "age_restricted" in detect_flags("55 and older community")
    assert "age_restricted" not in detect_flags("NOT a 55+ community! No age restrictions. Built 1955+ updates")
    assert "age_restricted" not in detect_flags("3/2 single family home with a 2-car garage")
    assert "townhouse" in detect_flags("Spacious 2-story townhome with a patio")
    assert "townhouse" not in detect_flags("Single family home, not a townhouse")
    assert product_flags({"property_type": "townhouse"}) == ["townhouse"]
    assert product_flags({"property_type": "single_family", "raw": json.dumps({"description": {"sub_type": "townhomes"},
                                                                               "tags": ["senior_community"]})}) == ["age_restricted", "townhouse"]


def test_board_cards_move_to_skip_and_put_back_is_kept(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s = _sections(cfg)
    gids = list(asana.tasks)
    oid = parse_card(asana.tasks[gids[0]]["notes"])["outreach_id"]
    pids = json.loads(loaded.execute("SELECT property_ids FROM outreach WHERE id=?", (oid,)).fetchone()[0])
    loaded.execute(f"UPDATE properties SET remarks='Lovely home in a 55+ community' WHERE id IN ({','.join('?' * len(pids))})", pids)
    out = product_clean.run(loaded, cfg, asana, dry_run=True)       # list only: nothing changes
    assert out["to_skip"] >= 1 and asana.where[gids[0]] == s["to_review"]
    assert loaded.execute("SELECT lead_status FROM properties WHERE id=?", (pids[0],)).fetchone()[0] != "EXCLUDED"
    out = product_clean.run(loaded, cfg, asana)
    assert out["to_skip"] >= 1 and asana.where[gids[0]] == s["skip"]
    assert any("Rule check" in c and "55+" in c for c in asana.stories[gids[0]])
    assert loaded.execute("SELECT status FROM outreach WHERE id=?", (oid,)).fetchone()[0] == "skipped"
    assert {r[0] for r in loaded.execute(f"SELECT lead_status FROM properties WHERE id IN ({','.join('?' * len(pids))})", pids)} == {"EXCLUDED"}
    assert all(asana.where[g] == s["to_review"] for g in gids[1:] if not set(json.loads(loaded.execute(
        "SELECT property_ids FROM outreach WHERE id=?", (parse_card(asana.tasks[g]["notes"])["outreach_id"],)).fetchone()[0])) & set(pids))
    # he drags it back: the house is kept for good, the next clean-up leaves it alone
    asana.move(gids[0], s["to_review"])
    out = product_clean.run(loaded, cfg, asana)
    assert out["put_back_by_you"] == 1
    assert loaded.execute("SELECT status FROM outreach WHERE id=?", (oid,)).fetchone()[0] == "pending_approval"
    exclude_flagged(loaded, cfg)
    product_clean.run(loaded, cfg, asana)
    assert asana.where[gids[0]] == s["to_review"]
    assert loaded.execute("SELECT lead_status FROM properties WHERE id=?", (pids[0],)).fetchone()[0] != "EXCLUDED"
