"""Rodrigo, 2026-11-24: a Follow Up column. He comments who to follow up with and leaves the card there."""
from datetime import datetime, timezone

from titan.approvals import parse_card, send_approved
from titan.follow_up import candidates
from titan.follow_up_column import due_from, section, work
from titan.outreach.send import log_message
from tests.test_approvals import SAT_10AM_ET, FakeGmail
from tests.test_follow_up import _sent

GOOD = ("Hey {first},\n\nCharles with Titan Realty Investments again about {street}. Is the seller still living in the house, "
        "or is it vacant right now?\n\n")


class LLM:
    available = True

    def __init__(self, cfg, verdict="SEND"):
        from titan.outreach.compose import signature
        self.sig, self.verdict, self.calls = signature(cfg), verdict, []

    def structured(self, purpose, system, user, schema, **k):
        self.calls.append(purpose)
        if purpose == "second_read":
            return {"verdict": self.verdict, "problems": [] if self.verdict == "SEND" else ["asks what she already answered"],
                    "why": "ok", "fix": ""}
        return {"read": "no answer yet", "body": GOOD.format(first="Maria", street="the house") + self.sig}


def test_due_dates():
    now = datetime(2026, 11, 24, 14, 0, tzinfo=timezone.utc)        # Tuesday 9am ET
    assert due_from("follow up with her and ask about the roof", now) is None
    assert due_from("follow up tomorrow", now).astimezone().date().isoformat() >= "2026-11-25"
    assert due_from("follow up on Monday", now).strftime("%Y-%m-%d") == "2026-11-30"
    assert due_from("follow up in 2 weeks", now).strftime("%Y-%m-%d") == "2026-12-08"
    assert due_from("check back Dec 3", now).strftime("%Y-%m-%d") == "2026-12-03"
    assert due_from("it sat on the market, a 3/2, we can close in 30 days", now) is None


def test_comment_writes_checks_sends_and_comes_back(loaded, cfg, tmp_path, monkeypatch):
    asana, s = _sent(loaded, cfg, tmp_path, monkeypatch)
    cfg.raw["outreach"]["second_read"] = {"enabled": True, "max_rounds": 2}
    fu = section(cfg, asana)
    gid = next(g for g, t in asana.tasks.items() if parse_card(t["notes"]))
    asana.move(gid, fu)
    asana.comment_raw(gid, "follow up with her, ask if it's vacant")
    assert gid not in {c["outreach"]["asana_task_id"] for c in candidates(loaded, cfg, asana)}   # the sequence leaves it alone
    llm = LLM(cfg)
    out = work(loaded, cfg, asana, llm)
    assert out["to_approved"] == 1 and asana.where[gid] == s["approved"] and "second_read" in llm.calls
    assert work(loaded, cfg, asana, llm)["to_approved"] == 0                                  # never twice
    gm = FakeGmail()
    assert send_approved(loaded, cfg, asana, gm, now=SAT_10AM_ET)["sent"] == 1
    assert gm.sent[0][1].startswith("Re: ") and "vacant" in gm.sent[0][2]
    assert work(loaded, cfg, asana, llm)["back"] == 1 and asana.where[gid] == fu


def test_reader_objects_goes_to_needs_me(loaded, cfg, tmp_path, monkeypatch):
    asana, s = _sent(loaded, cfg, tmp_path, monkeypatch)
    cfg.raw["outreach"]["second_read"] = {"enabled": True, "max_rounds": 2}
    fu = section(cfg, asana)
    gid = next(g for g, t in asana.tasks.items() if parse_card(t["notes"]))
    asana.move(gid, fu)
    asana.comment_raw(gid, "follow up")
    out = work(loaded, cfg, asana, LLM(cfg, verdict="HOLD"))
    assert out["to_needs_me"] == 1 and asana.where[gid] == asana.ensure_section(None, "Needs Me")


def test_later_date_waits_and_answer_moves_to_needs_me(loaded, cfg, tmp_path, monkeypatch):
    asana, s = _sent(loaded, cfg, tmp_path, monkeypatch)
    cfg.raw["outreach"]["second_read"] = {"enabled": True, "max_rounds": 2}
    fu = section(cfg, asana)
    gids = [g for g, t in asana.tasks.items() if parse_card(t["notes"])][:2]
    for g in gids:
        asana.move(g, fu)
    asana.comment_raw(gids[0], "follow up with him next week")
    out = work(loaded, cfg, asana, LLM(cfg))
    assert out["scheduled"] == 1 and asana.where[gids[0]] == fu and "Follow-up set for" in asana.stories[gids[0]][-1]
    oid = parse_card(asana.tasks[gids[1]]["notes"])["outreach_id"]
    aid = loaded.execute("SELECT agent_id FROM outreach WHERE id=?", (oid,)).fetchone()[0]
    tid = loaded.execute("SELECT id FROM threads WHERE agent_id=?", (aid,)).fetchone()[0]
    log_message(loaded, tid, "in", "Seller is flexible, call me.", external_id="in-9", classification="INTERESTED")
    assert work(loaded, cfg, asana, LLM(cfg))["answered"] == 1 and asana.where[gids[1]] == asana.ensure_section(None, "Needs Me")
    assert asana.where[gids[0]] == fu                                                         # still waiting for its day
