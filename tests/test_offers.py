"""Offer log + ingest pause (Rodrigo, 2026-10-26)."""
from types import SimpleNamespace

from titan import cli
from titan.approvals import parse_card, post_reply_card, send_approved
from titan.offers import offer_price, offer_stats, set_status, sync_offers
from titan.stats import compute_stats
from tests.test_approvals import SAT_10AM_ET, FakeGmail, _cards, _sections

OFFER = ("Hey Lise,\n\nThanks for the details. We can do $209,000 cash, as is, 30-day close, 9-day inspection, $5,000 deposit.\n\n"
         "Resale nearby runs about $240,000 once the work is done.\n\nCharles")


def test_offer_price_skips_deposit_rent_and_ask():
    assert offer_price(OFFER) == 209_000
    assert offer_price("Rent is $4,500/mo. Our number is $180,000.") == 180_000
    assert offer_price("They asked $299,900. We're at $250,000.", list_price=299_900) == 250_000
    assert offer_price("Thanks, where does the seller need to be? $5,000 deposit.") is None


def test_sent_offer_card_is_logged_and_tracked(loaded, cfg, tmp_path, monkeypatch):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    g = next(iter(asana.tasks))
    asana.move(g, _sections(cfg)["approved"])
    assert send_approved(loaded, cfg, asana, FakeGmail(), now=SAT_10AM_ET)["sent"] == 1
    oid = parse_card(asana.tasks[g]["notes"])["outreach_id"]
    assert sync_offers(loaded, cfg) == 0                                  # a first email has no number: not an offer
    holding = post_reply_card(loaded, cfg, asana, oid, "Hey Lise,\n\nWorking on it, back to you today.\n\nCharles", _sections(cfg)["approved"])
    offer = post_reply_card(loaded, cfg, asana, oid, OFFER, _sections(cfg)["approved"])
    res = send_approved(loaded, cfg, asana, FakeGmail(), now=SAT_10AM_ET)
    assert res["sent"] == 2 and res["offers_logged"] == 1 and holding and offer
    assert sync_offers(loaded, cfg) == 0                                  # idempotent
    s = offer_stats(loaded)
    assert s["offers"] == 1 and s["by_status"]["SENT"] == 1 and s["median_pct_of_list"] is not None
    row = loaded.execute("SELECT id, property_id FROM offers").fetchone()
    assert loaded.execute("SELECT lead_status FROM properties WHERE id=?", (row[1],)).fetchone()[0] == "OFFER_SENT"
    set_status(loaded, row[0], "countered", "seller wants 240")
    s = compute_stats(loaded, cfg, with_asana=False)["offers"]
    assert s["by_status"]["COUNTERED"] == 1 and s["response_rate_pct"] == 100.0


def test_paused_ingest_scrapes_nothing(cfg, conn, monkeypatch, capsys):
    called = []
    monkeypatch.setattr("titan.ingest.pipeline.ingest", lambda *a, **k: called.append(1) or {})
    monkeypatch.setattr(cli, "_open", lambda c: conn)
    monkeypatch.setattr("titan.dashboard_sync.sync", lambda *a, **k: {})
    assert cfg.get("ingest.paused") is True
    cli.cmd_run_daily(cfg, SimpleNamespace(steps="ingest", fixture=None, force=False, comps_fixture=None, offline=True))
    assert not called and "PAUSED" in capsys.readouterr().out
