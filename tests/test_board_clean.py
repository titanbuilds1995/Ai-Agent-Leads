"""Daily Fresh Leads clean-up (Rodrigo, 2026-11-12): houses under contract / sold / off market leave Fresh Leads for Skip."""
import json

from titan import board_clean
from titan.approvals import parse_card, sync_feedback
from titan.learning import rate, scan_board
from tests.test_approvals import _cards, _sections


def _house(loaded, asana, gid):
    oid = parse_card(asana.tasks[gid]["notes"])["outreach_id"]
    pids = json.loads(loaded.execute("SELECT property_ids FROM outreach WHERE id=?", (oid,)).fetchone()[0])
    return oid, pids


def test_classify_realtor_records():
    c = board_clean.classify
    assert c({"status": "for_sale", "flags": {}}) == "active"
    assert c({"status": "for_sale", "flags": {"is_pending": True}}) == "pending"
    assert c({"status": "for_sale", "mls_status": "Active Under Contract"}) == "pending"
    assert c({"status": "sold"}) == "sold"
    assert c({"status": "off_market", "mls_status": "Withdrawn"}) == "off_market"
    assert c({}) is None and c(None) is None


def test_gone_houses_move_to_skip_and_never_send(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s = _sections(cfg)
    gids = list(asana.tasks)
    assert len(gids) >= 2
    gone_oid, gone_pids = _house(loaded, asana, gids[0])
    gone = set(gone_pids)
    calls = []

    def lookup(p):
        calls.append(p["id"])
        return "pending" if p["id"] in gone else "active"

    out = board_clean.run(loaded, cfg, asana, lookup=lookup, sleep_s=0)
    assert out["to_skip"] >= 1 and asana.where[gids[0]] == s["skip"]
    assert any("Board clean-up" in c and "under contract" in c for c in asana.stories[gids[0]])
    assert loaded.execute("SELECT status FROM outreach WHERE id=?", (gone_oid,)).fetchone()[0] == "skipped"
    assert {r[0] for r in loaded.execute(f"SELECT lead_status FROM properties WHERE id IN ({','.join('?' * len(gone))})",
                                         list(gone))} == {"EXCLUDED"}
    still = [g for g in gids if not set(_house(loaded, asana, g)[1]) & gone]
    assert still and all(asana.where[g] == s["to_review"] for g in still)
    # not his vote: the trust ladder does not count it as a skip
    scan_board(loaded, cfg, asana)
    sync_feedback(loaded, cfg, asana, None)
    assert rate(loaded, "first_touch", 30)["skipped"] == 0
    # the next morning the moved card is not looked up again
    calls.clear()
    board_clean.run(loaded, cfg, asana, lookup=lookup, sleep_s=0)
    assert not gone & set(calls)


def test_failed_or_unclear_lookups_change_nothing(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s = _sections(cfg)

    def blocked(p):
        raise RuntimeError("403 realtor.com")

    out = board_clean.run(loaded, cfg, asana, lookup=blocked, sleep_s=0)
    assert out["to_skip"] == 0 and all(w == s["to_review"] for w in asana.where.values())
    assert out["checked"] <= cfg["outreach"]["board_clean"]["stop_after_errors"] and out.get("warning")
    out = board_clean.run(loaded, cfg, asana, lookup=lambda p: None, sleep_s=0)
    assert out["to_skip"] == 0 and out["unknown"] >= 1


def test_list_only_moves_nothing(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s = _sections(cfg)
    out = board_clean.run(loaded, cfg, asana, lookup=lambda p: "sold", dry_run=True, sleep_s=0)
    assert out["to_skip"] >= 1 and all(w == s["to_review"] for w in asana.where.values())
    assert "SKIP" in board_clean.plain(out)


def test_homeharvest_lookup_uses_the_address_search():
    seen = {}

    def scrape(loc, **kw):
        seen["loc"], seen["kw"] = loc, kw
        return [{"property_id": "9", "status": "sold"}]

    fn = board_clean.homeharvest_lookup({"ingest": {}} and _Cfg(), scrape=scrape)
    assert fn({"street": "1 Main St", "city": "Miami", "state": "FL", "zip": "33101", "source_id": "9"}) == "sold"
    assert seen["loc"] == "1 Main St, Miami, FL 33101" and "listing_type" not in seen["kw"]


class _Cfg:
    def get(self, key, default=None):
        return default
