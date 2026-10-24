import json
from datetime import datetime, timezone

import pytest

from titan import db
from titan.outreach.send import ensure_thread, log_message
from titan.replies.classifier import classify_rules, parse_money
from titan.replies.inbox import handle_inbound, inbound_sms
from titan.replies.responder import decide
from tests.conftest import FIXTURES

REPLIES = json.loads((FIXTURES / "replies.json").read_text())
LIST_PRICE = 385000


@pytest.mark.parametrize("case", REPLIES, ids=[f"r{c['id']}" for c in REPLIES])
def test_classifier_fixture(case):
    c = classify_rules(case["text"], LIST_PRICE)
    assert c.label == case["expected"], f"{case['text']!r}: got {c.label} ({c.reasons})"
    if "number" in case:
        assert c.number == case["number"]
    if "email" in case:
        assert c.email == case["email"]


def test_parse_money_variants():
    assert parse_money("we are at 310", 385000) == 310000
    assert parse_money("$310,000 cash", 385000) == 310000
    assert parse_money("310k", 385000) == 310000
    assert parse_money("maybe 3.9", 385000) == 390000
    assert parse_money("call me at 305-555-0101", 385000) is None
    assert parse_money("3 bedrooms 2 baths", 385000) is None
    assert parse_money("10% below", 385000) is None


def _ctx(**kw):
    d = {"agent": {"id": 1, "name": "Maria Lopez", "email": "maria@kw.com", "email_confidence": "high", "phone": "+13055550101"}, "agent_name": "Maria Lopez",
         "street": "107 NW 11th St", "city": "Miami", "county": "Miami-Dade", "list_price": 385000, "mao": 300000, "offer_price": 294000}
    d.update(kw)
    return d


def test_responder_conversation_first(cfg):
    """No number, counter or commitment ever goes out automatically (Rodrigo, 2026-10-24)."""
    from titan.replies.classifier import Classification as C
    thread = {"state": "OPEN", "bridge_sent": 0}
    a = decide(C("ASK_FOR_OFFER", 0.9), _ctx(), cfg, "email", thread, text="Just send me an offer")
    assert "$" not in a.reply and a.thread_state == "ESCALATED" and a.lead_status == "NEEDS_REVIEW" and a.review
    assert "random number" in a.reply and "acquisitions" in a.reply and "?" in a.reply   # holding reply, company framing, one question
    a = decide(C("COUNTER_WITH_NUMBER", 0.9, number=325000), _ctx(), cfg, "email", thread, text="Seller would take 325")
    assert "$" not in a.reply and "325" not in a.reply and a.review and a.lead_status == "NEEDS_REVIEW" and a.signal == "FLEXIBLE"
    a = decide(C("COUNTER_WITH_NUMBER", 0.9, number=390000), _ctx(), cfg, "email", thread, text="They want 390")
    assert a.signal == "DISCUSSING"                                     # a number above list is not flexibility
    a = decide(C("STOP", 0.9), _ctx(), cfg, "email", thread)
    assert a.dnc and a.thread_state == "STOPPED" and a.reply is None
    a = decide(C("SOLD_OR_PENDING", 0.9), _ctx(), cfg, "email", thread)
    assert a.thread_state == "CLOSED" and a.lead_status == "CLOSED"
    a = decide(C("NOT_INTERESTED", 0.9), _ctx(), cfg, "email", thread, text="Seller is firm at list")
    assert a.thread_state == "CLOSED" and "reach out" in a.reply and "$" not in a.reply


def test_interested_asks_one_question_at_a_time_and_never_repeats(cfg):
    from titan.replies.classifier import Classification as C
    thread = {"state": "OPEN"}
    a = decide(C("INTERESTED", 0.9), _ctx(history_in="Yes the seller is motivated", history_out="", auto_replies=0), cfg, "email", thread,
               text="Yes the seller is motivated")
    assert a.signal == "FLEXIBLE" and a.lead_status == "FLEXIBLE" and a.review and a.reply.count("?") <= 2 and "really needs" in a.reply
    # agent already covered condition and timing → next question is the seller's expectations, not a repeat
    hist = "It's mostly cosmetic, roof is 4 years old, and they'd like to close within 30 days"
    a = decide(C("INTERESTED", 0.9), _ctx(history_in=hist, history_out="How much work would you say it really needs?", auto_replies=1),
               cfg, "email", thread, text=hist)
    assert "realistically needs to be" in a.reply and "timing" not in a.reply.lower() and "really needs" not in a.reply
    # after max_auto_questions we stop and bring it to Rodrigo
    a = decide(C("INTERESTED", 0.9), _ctx(history_in="ok", auto_replies=2), cfg, "email", thread, text="ok")
    assert a.reply is None and a.thread_state == "ESCALATED" and a.lead_status == "NEEDS_REVIEW" and a.review


def test_questions_answered_from_facts_or_escalated(cfg):
    from titan.replies.classifier import Classification as C
    from titan.util import inspection_phrase
    thread = {"state": "OPEN"}
    a = decide(C("QUESTION", 0.8), _ctx(), cfg, "email", thread, text="Do you need an inspection period? Who pays closing costs?")
    assert inspection_phrase(cfg["outreach"]["terms"]) in a.reply and "buyer pays closing costs" in a.reply and "$" not in a.reply.replace("$5,000", "")
    a = decide(C("QUESTION", 0.8), _ctx(), cfg, "email", thread, text="Who is this? How much would you pay?")
    assert a.reply is None and a.lead_status == "NEEDS_REVIEW" and a.review          # price question → Rodrigo
    a = decide(C("QUESTION", 0.8), _ctx(), cfg, "email", thread, text="Will you be assigning the contract?")
    assert a.reply is None and a.thread_state == "ESCALATED"


def test_sms_positive_reply_never_sends_an_offer(cfg):
    from titan.replies.classifier import Classification as C
    a = decide(C("ASK_FOR_OFFER", 0.9), _ctx(), cfg, "sms", {"state": "OPEN"}, text="send me your offer")
    assert "$" not in a.reply and a.review and not hasattr(a, "handoff")


def _email_thread(conn, agent_email="maria.lopez@test.com"):
    a = conn.execute("SELECT a.id, p.id pid FROM agents a JOIN properties p ON p.agent_id=a.id JOIN valuations v ON v.property_id=p.id WHERE v.rating='HOT' LIMIT 1").fetchone()
    conn.execute("UPDATE agents SET email=?, email_confidence='high' WHERE id=?", (agent_email, a["id"]))
    tid = ensure_thread(conn, a["id"], "email", "gm-thread-1", [a["pid"]])
    log_message(conn, tid, "out", "first touch", external_id="gm-msg-0")
    return db.row_to_dict(conn.execute("SELECT * FROM threads WHERE id=?", (tid,)).fetchone()), a["id"], a["pid"]


def test_email_reply_flow_draft_mode(loaded, cfg, monkeypatch, tmp_path):
    monkeypatch.setenv("TITAN_MODE", "DRAFT_FOR_APPROVAL")
    cfg.root = tmp_path
    thread, aid, pid = _email_thread(loaded)
    drafts = []

    class G:
        def create_draft(self, to, subject, body, label=None, thread_id=None, in_reply_to=None):
            drafts.append((to, subject, body, label)); return {"draft_id": "d1", "message_id": "m1", "thread_id": thread_id}
        def send(self, *a, **k): raise AssertionError("must not send in draft mode")

    res = handle_inbound(loaded, cfg, thread, "Yes, the seller is flexible. When could you close?", "gm-msg-1", "email", gmail=G())
    assert res["reply_status"] == "draft" and drafts[0][3] == "Titan/Review" and "$" not in drafts[0][2]
    # duplicate delivery is ignored
    assert handle_inbound(loaded, cfg, thread, "Yes, the seller is flexible. When could you close?", "gm-msg-1", "email", gmail=G()) == {"skipped": "duplicate"}
    res = handle_inbound(loaded, cfg, thread, "Just send me your best offer", "gm-msg-2", "email", gmail=G())
    assert res["state"] == "ESCALATED" and "$" not in drafts[-1][2] and res["review"]
    review = open(res["review"]).read()
    for must in ("What the agent actually said", "seller is flexible", "Missing information", "Preliminary internal range",
                 "NOT an approved offer", "Recommended next step", "approve-offer"):
        assert must in review, must
    # auto-replies stop once Rodrigo owns it; the review is refreshed with the new message
    res = handle_inbound(loaded, cfg, thread, "Any update?", "gm-msg-3", "email", gmail=G())
    assert res["action"].startswith("logged only") and "Any update?" in open(review_path := res.get("review") or (tmp_path / "reports" / "review").glob("*.md").__next__()).read()
    assert loaded.execute("SELECT lead_status FROM properties WHERE id=?", (pid,)).fetchone()[0] == "NEEDS_REVIEW"


def test_email_stop_adds_dnc_and_cancels_queue(loaded, cfg):
    thread, aid, pid = _email_thread(loaded)
    res = handle_inbound(loaded, cfg, thread, "Please remove me from your list.", "gm-msg-9", "email")
    assert res["classification"] == "STOP"
    assert db.is_dnc(loaded, agent_id=aid)
    assert loaded.execute("SELECT state FROM threads WHERE id=?", (thread["id"],)).fetchone()[0] == "STOPPED"


def test_sms_offer_request_is_held_for_review(loaded, cfg, monkeypatch, tmp_path):
    monkeypatch.setenv("TITAN_MODE", "FULL_AUTO")
    cfg.root = tmp_path
    a = loaded.execute("SELECT a.id, a.phone, p.id pid FROM agents a JOIN properties p ON p.agent_id=a.id JOIN valuations v ON v.property_id=p.id "
                       "WHERE v.rating IN ('HOT','WARM') AND a.is_office_line=0 LIMIT 1").fetchone()
    loaded.execute("UPDATE agents SET email='agent@brokerage.com', email_confidence='high' WHERE id=?", (a["id"],))
    tid = ensure_thread(loaded, a["id"], "sms", a["phone"], [a["pid"]])
    log_message(loaded, tid, "out", "hi", external_id="SM0")
    sent_mail, sent_sms = [], []

    class G:
        def send(self, to, subject, body, **kw): sent_mail.append((to, subject, body)); return {"message_id": "m1", "thread_id": "gt1"}
    class T:
        def send(self, to, body): sent_sms.append((to, body)); return {"sid": f"SM{len(sent_sms)}", "status": "queued"}

    res = inbound_sms(loaded, cfg, a["phone"], "Yes seller is motivated, send me your offer", "SM-in-1", gmail=G(), twilio=T())
    assert not sent_mail and all("$" not in b for _, b in sent_sms)     # no written offer, no number, even in FULL_AUTO
    assert res["state"] == "ESCALATED" and res["review"]
    assert loaded.execute("SELECT lead_status FROM properties WHERE id=?", (a["pid"],)).fetchone()[0] == "NEEDS_REVIEW"


def test_approve_offer_is_the_only_way_a_number_goes_out(loaded, cfg, tmp_path):
    from titan.approve import approve_offer
    cfg.root = tmp_path
    thread, aid, pid = _email_thread(loaded)
    res = approve_offer(loaded, cfg, pid, 287500, gmail=None, note="after call with agent")
    body = open(res["local"]).read()
    assert "$287,500" in body and "7-day inspection" in body and res["status"] == "draft"
    assert loaded.execute("SELECT lead_status FROM properties WHERE id=?", (pid,)).fetchone()[0] == "OFFER_DRAFTED"
    assert loaded.execute("SELECT kind FROM outreach WHERE kind='offer:approved'").fetchone()


def test_sms_stop_from_unknown_number(loaded, cfg):
    res = inbound_sms(loaded, cfg, "+19999999999", "STOP", "SM-u")
    assert res == {"skipped": "unknown sender"} and db.is_dnc(loaded, phone="+19999999999")
