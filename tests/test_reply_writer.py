"""Stage 3 (Rodrigo, 2026-10-26): Charles drafts his answer to each agent who replied, as a reply card Rodrigo edits and approves."""
from titan import db
from titan.approvals import BOT, parse_card, send_approved, sync_feedback
from titan.learning import APPROVED_AS_IS, APPROVED_EDITED, rate
from titan.outreach.compose import signature
from titan.outreach.send import log_message
from titan.replies.writer import draft_reply_cards, validate_reply
from tests.test_approvals import SAT_10AM_ET, FakeGmail, _cards, _sections

GOOD = "Hi Maria,\n\nThanks, that helps a lot. Knowing the roof is from 2015 takes one big question off the table on our side.\n\nIs the seller living there now, or would it be delivered vacant?"


class _LLM:
    available = True

    def __init__(self, body=GOOD):
        self.body, self.calls = body, 0

    def structured(self, purpose, system, user, schema, **kw):
        self.calls += 1
        if purpose == "revise_email":
            import json
            return {"subject": "Re: x", "body": json.loads(user)["body"].replace("Thanks, that helps a lot.", "Thank you, that's useful.")}
        return {"read": "roof 2015; occupancy unknown", "body": self.body + "\n\n" + self.sig}


def _setup(loaded, cfg, tmp_path, monkeypatch, cls="INTERESTED", text="Roof is from 2015. Seller is open."):
    asana, _ = _cards(loaded, cfg, tmp_path, monkeypatch)
    s = _sections(cfg)
    gid = next(iter(asana.tasks))
    asana.move(gid, s["approved"])
    send_approved(loaded, cfg, asana, FakeGmail(), now=SAT_10AM_ET)
    oid = parse_card(asana.tasks[gid]["notes"])["outreach_id"]
    aid = loaded.execute("SELECT agent_id FROM outreach WHERE id=?", (oid,)).fetchone()[0]
    tid = loaded.execute("SELECT id FROM threads WHERE agent_id=?", (aid,)).fetchone()[0]
    log_message(loaded, tid, "in", text, external_id="in-1", classification=cls, confidence=0.8)
    _LLM.sig = signature(cfg)
    return asana, s, oid


def test_answer_is_drafted_once_on_the_lead_card_and_waits_in_needs_me(loaded, cfg, tmp_path, monkeypatch):
    """Rodrigo, 2026-10-27: Fresh Leads holds only first emails he has not touched; a drafted answer is his. 2026-11-03: it sits
    ON the lead card (NEXT EMAIL), never on a second card."""
    from titan.answers import pending
    asana, s, oid = _setup(loaded, cfg, tmp_path, monkeypatch)
    llm = _LLM()
    before = len(asana.tasks)
    assert draft_reply_cards(loaded, cfg, asana, llm)["drafted"] == 1
    assert len(asana.tasks) == before and not any(t["name"].startswith("Reply ·") for t in asana.tasks.values())
    gid = loaded.execute("SELECT asana_task_id FROM outreach WHERE id=?", (oid,)).fetchone()[0]
    assert asana.where[gid] == asana.ensure_section(None, "Needs Me")
    card = parse_card(asana.tasks[gid]["notes"])
    assert card["outreach_id"] == oid and card["subject"].startswith("Re: ") and "roof is from 2015" in card["body"].lower()
    assert pending(loaded, oid)["body"] == card["body"] and "NEXT EMAIL" in asana.tasks[gid]["notes"]
    assert draft_reply_cards(loaded, cfg, asana, llm)["drafted"] == 0            # one answer per message, no duplicates
    # approved as written → sent in the thread, stage 3 counts it
    asana.move(gid, s["approved"])
    assert send_approved(loaded, cfg, asana, FakeGmail(), now=SAT_10AM_ET)["sent"] == 1
    assert rate(loaded, "reply", 20)["as_is"] == 1 and pending(loaded, oid) is None


def test_comment_on_the_lead_card_rewrites_the_answer_not_the_first_email(loaded, cfg, tmp_path, monkeypatch):
    from titan.answers import pending
    asana, s, oid = _setup(loaded, cfg, tmp_path, monkeypatch)
    first = loaded.execute("SELECT body FROM outreach WHERE id=?", (oid,)).fetchone()[0]
    draft_reply_cards(loaded, cfg, asana, _LLM())
    gid = loaded.execute("SELECT asana_task_id FROM outreach WHERE id=?", (oid,)).fetchone()[0]
    asana.stories[gid].append("say thank you instead")
    assert sync_feedback(loaded, cfg, asana, _LLM())["revised"] == 1
    assert loaded.execute("SELECT body FROM outreach WHERE id=?", (oid,)).fetchone()[0] == first
    assert "that's useful" in pending(loaded, oid)["body"] and "that's useful" in parse_card(asana.tasks[gid]["notes"])["body"]
    asana.move(gid, s["approved"])
    send_approved(loaded, cfg, asana, FakeGmail(), now=SAT_10AM_ET)
    assert rate(loaded, "reply", 20)["edited"] == 1 and rate(loaded, "reply", 20)["as_is"] == 0


def test_offer_requests_get_a_holding_answer_and_numbers_are_refused(loaded, cfg, tmp_path, monkeypatch):
    asana, s, _ = _setup(loaded, cfg, tmp_path, monkeypatch, "ASK_FOR_OFFER", "Just bring your best offer.")
    bad = _LLM("Hi Maria,\n\nThanks. We could do $250,000 cash, our offer would close in 14 days. Does that work for the seller?")
    from titan.answers import pending
    assert draft_reply_cards(loaded, cfg, asana, bad)["failed"] == 1 and pending(loaded, _) is None
    assert draft_reply_cards(loaded, cfg, asana, _LLM())["drafted"] == 1
    gid = loaded.execute("SELECT asana_task_id FROM outreach WHERE id=?", (_,)).fetchone()[0]
    assert "holding answer only" in asana.tasks[gid]["notes"] and "NEXT EMAIL" in asana.tasks[gid]["notes"]


def test_no_claude_no_cards_and_closed_threads_are_left_alone(loaded, cfg, tmp_path, monkeypatch):
    asana, s, _ = _setup(loaded, cfg, tmp_path, monkeypatch, "NOT_INTERESTED", "Not interested, firm at list.")
    assert draft_reply_cards(loaded, cfg, asana, None)["drafted"] == 0
    assert draft_reply_cards(loaded, cfg, asana, _LLM())["drafted"] == 0


def test_validate_reply():
    assert validate_reply(GOOD) == []
    # Rodrigo, 2026-10-27: a reply never asks how much work a photographed listing needs either
    ask = GOOD + " Also, how much work would you say it really needs?"
    assert any("how much work" in x for x in validate_reply(ask, [{"photos_count": 12}]))
    assert validate_reply(ask, [{"photos_count": 2}]) == []
    assert validate_reply(ask) == []                     # no listing given: nothing to judge against
    assert "price or offer language" in validate_reply(GOOD + " We'd come in around 250k.")
    assert "says we are the buyer / investors" in validate_reply(GOOD + " We buy a lot here.")
    assert "dash" in validate_reply(GOOD.replace("Thanks,", "Thanks —"))
