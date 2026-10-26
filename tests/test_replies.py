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


def test_bounces_are_never_counters():
    from titan.replies.classifier import classify_rules
    bounce = ("** Recipient inbox full ** Your message couldn't be delivered to dror2invest@yahoo.com. Their inbox is full, "
              "or it's getting too much mail right now. The response from the remote server was: 552 5.2.2 This message could not be "
              "delivered because the recipient's mailbox is full. 100k under appraiser value, 12% return on 430k purchase.")
    c = classify_rules(bounce, 439900)
    assert c.label == "BOUNCE" and c.number is None
    assert classify_rules("Address not found. Your message wasn't delivered to x@y.com because the address couldn't be found.", 400000).label == "BOUNCE"
    assert classify_rules("Seller would take 520k, send me your best offer", 439900).label != "BOUNCE"


def test_strip_quoted_handles_wrapped_gmail_header():
    from titan.outreach.gmail_client import strip_quoted
    raw = ("Hi Charles, it's a gut.  Needs a roof, the AC is window units.\n\nI'd rather be transparent.\n\n"
           "On Sat, Oct 24, 2026 at 12:44 PM Charles Grant <\nCharles@titanrealtyinvestments.com> wrote:\n\n> Hey Lise\n\n-- \n\n Lise Freeman\n561-907-0095\n")
    out = strip_quoted(raw)
    assert out.endswith("I'd rather be transparent.") and "titan" not in out.lower() and "Lise Freeman" not in out


def test_condition_details_keep_the_conversation_going(cfg):
    """Lise Freeman, 2026-10-24: a frank condition rundown with no question is INTERESTED (ask the next thing), not a QUESTION about us."""
    c = classify_rules("Hi Charles, it's a gut. Needs a roof, the AC is window units, new kitchen, new bath. Electrical most likely needs done. "
                       "My seller inherited this from his aunt. I'd rather be transparent.", 299900)
    assert c.label == "INTERESTED"
    a = decide(c, _ctx(history_in="it's a gut. needs a roof, kitchen, bath, electrical. seller inherited it.", history_out="", auto_replies=0), cfg, "email", {"state": "OPEN"})
    assert a.reply and "realistically" in a.reply and "Charles Grant" in a.reply and "$" not in a.reply


def test_unclear_reply_gets_no_canned_answer(cfg):
    from titan.replies.classifier import Classification
    a = decide(Classification("QUESTION", 0.3, reasons=["unmatched"]), _ctx(), cfg, "email", {"state": "OPEN"})
    assert a.reply is None and a.thread_state == "OPEN" and "human" in a.note
    a = decide(Classification("QUESTION", 0.7, reasons=["question"]), _ctx(), cfg, "email", {"state": "OPEN"}, "what is your EMD?")
    assert a.reply and "5,000" in a.reply


def test_heal_bounces_refiles_old_misread_bounces(loaded, cfg):
    from titan import db
    from titan.replies.inbox import heal_bounces
    from titan.util import iso
    aid = loaded.execute("SELECT id FROM agents LIMIT 1").fetchone()[0]
    pid = loaded.execute("SELECT id FROM properties LIMIT 1").fetchone()[0]
    loaded.execute("INSERT INTO threads(agent_id,channel,external_id,property_ids,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                   (aid, "email", "t-bounce", f"[{pid}]", "ESCALATED", iso(), iso()))
    tid = loaded.execute("SELECT id FROM threads WHERE external_id='t-bounce'").fetchone()[0]
    loaded.execute("INSERT INTO messages(thread_id,direction,body,external_id,classification,confidence,created_at) VALUES(?,?,?,?,?,?,?)",
                   (tid, "in", "** Recipient inbox full ** Your message couldn't be delivered to x@yahoo.com. 552 5.2.2 mailbox is full", "m-bounce", "COUNTER_WITH_NUMBER", 0.85, iso()))
    assert heal_bounces(loaded) == 1
    assert loaded.execute("SELECT state FROM threads WHERE id=?", (tid,)).fetchone()[0] == "BOUNCED"
    assert loaded.execute("SELECT classification FROM messages WHERE external_id='m-bounce'").fetchone()[0] == "BOUNCE"
    assert loaded.execute("SELECT email_confidence FROM agents WHERE id=?", (aid,)).fetchone()[0] == "bounced"
    assert heal_bounces(loaded) == 0


def test_bring_your_best_offer_is_an_ask_for_offer():
    from titan.replies.classifier import classify_rules
    c = classify_rules("That's the question isn't it. I would say just bring your best offer. They will ask for flexibility in the move out as they need the money for the next house.", 299900)
    assert c.label == "ASK_FOR_OFFER"
