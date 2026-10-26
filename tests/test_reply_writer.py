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


def test_reply_card_is_drafted_once_and_waits_in_to_review(loaded, cfg, tmp_path, monkeypatch):
    asana, s, oid = _setup(loaded, cfg, tmp_path, monkeypatch)
    llm = _LLM()
    assert draft_reply_cards(loaded, cfg, asana, llm)["drafted"] == 1
    replies = [g for g, t in asana.tasks.items() if t["name"].startswith("Reply ·")]
    assert len(replies) == 1 and asana.where[replies[0]] == s["to_review"]
    card = parse_card(asana.tasks[replies[0]]["notes"])
    assert card["reply_to"] and card["outreach_id"] == oid and "roof is from 2015" in card["body"].lower()
    assert draft_reply_cards(loaded, cfg, asana, llm)["drafted"] == 0            # one card per message, no duplicates
    # approved as written → stage 3 counts it
    asana.move(replies[0], s["approved"])
    assert send_approved(loaded, cfg, asana, FakeGmail(), now=SAT_10AM_ET)["sent"] == 1
    assert rate(loaded, "reply", 20)["as_is"] == 1


def test_comment_on_a_reply_card_rewrites_the_reply_not_the_first_email(loaded, cfg, tmp_path, monkeypatch):
    asana, s, oid = _setup(loaded, cfg, tmp_path, monkeypatch)
    first = loaded.execute("SELECT body FROM outreach WHERE id=?", (oid,)).fetchone()[0]
    draft_reply_cards(loaded, cfg, asana, _LLM())
    rid = next(g for g, t in asana.tasks.items() if t["name"].startswith("Reply ·"))
    asana.stories[rid].append("say thank you instead")
    assert sync_feedback(loaded, cfg, asana, _LLM())["revised"] == 1
    assert loaded.execute("SELECT body FROM outreach WHERE id=?", (oid,)).fetchone()[0] == first
    assert "that's useful" in loaded.execute("SELECT reply_draft FROM messages WHERE reply_card=?", (rid,)).fetchone()[0]
    asana.move(rid, s["approved"])
    send_approved(loaded, cfg, asana, FakeGmail(), now=SAT_10AM_ET)
    assert loaded.execute("SELECT decision FROM card_decisions WHERE card_gid=?", (rid,)).fetchone()[0] == APPROVED_EDITED


def test_offer_requests_get_a_holding_answer_and_numbers_are_refused(loaded, cfg, tmp_path, monkeypatch):
    asana, s, _ = _setup(loaded, cfg, tmp_path, monkeypatch, "ASK_FOR_OFFER", "Just bring your best offer.")
    bad = _LLM("Hi Maria,\n\nThanks. We could do $250,000 cash, our offer would close in 14 days. Does that work for the seller?")
    assert draft_reply_cards(loaded, cfg, asana, bad)["failed"] == 1 and not any(t["name"].startswith("Reply ·") for t in asana.tasks.values())
    assert draft_reply_cards(loaded, cfg, asana, _LLM())["drafted"] == 1
    rid = next(g for g, t in asana.tasks.items() if t["name"].startswith("Reply ·"))
    assert "holding answer only" in asana.tasks[rid]["notes"]


def test_no_claude_no_cards_and_closed_threads_are_left_alone(loaded, cfg, tmp_path, monkeypatch):
    asana, s, _ = _setup(loaded, cfg, tmp_path, monkeypatch, "NOT_INTERESTED", "Not interested, firm at list.")
    assert draft_reply_cards(loaded, cfg, asana, None)["drafted"] == 0
    assert draft_reply_cards(loaded, cfg, asana, _LLM())["drafted"] == 0


def test_validate_reply():
    assert validate_reply(GOOD) == []
    assert "price or offer language" in validate_reply(GOOD + " We'd come in around 250k.")
    assert "says we are the buyer / investors" in validate_reply(GOOD + " We buy a lot here.")
    assert "dash" in validate_reply(GOOD.replace("Thanks,", "Thanks —"))
