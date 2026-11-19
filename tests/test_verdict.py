"""The bottom line (Rodrigo, 2026-11-19, Arkeshia Love / 4108 Greenwood Ave): the engine said "we need to be at $521,000, the ask
is fine" on a duplex nobody bought at $372,900 in 228 days. A last reader settles the numbers into one bold conclusion (worth,
offer, gap to asking), last on the card, and the offer email at that price is drafted on the same card. Nothing sends."""
import json

from titan import verdict
from titan.approvals import parse_card, sync_feedback

from tests.test_approvals import _sections
from tests.test_comment_router import _setup

ASK = "give me the comps for this property, it really needs a full rehab and I think the price is way too high"
OFFER_BODY = ("Hey Richard,\n\nCharles with Titan Realty Investments. We went through the numbers on the property and I am sending our offer "
              "over. I copied Rodrigo, our acquisitions manager, since he approved the numbers.\n\nOur offer:\n\nPrice: $255,000, cash\n"
              "As is, no repairs asked of the seller\n15 day inspection period\n$5,000 escrow deposit\nClose in 30 days, or longer if that works "
              "better for the seller\n\nI know that is under the asking price. Two similar homes nearby sold for $300,000 and $330,000, and "
              "this one needs a full rehab before it can be lived in.\n\nIf the seller wants to talk it through, I am glad to get on a "
              "call.\n\nBest regards,\n\nCharles Grant\nJunior Acquisitions Agent\nTitan Realty Investments\nOffice Line: +1 (561) 405 7379")


class Reader:
    """Claude for the card chat, the last reader, the offer writer and the second reader."""
    available = True

    def __init__(self, offer=255000):
        self.calls, self.offer = [], offer

    def structured(self, purpose, system, user, schema, max_tokens=1024):
        self.calls.append(purpose)
        if purpose == "verdict":
            data = json.loads(user)
            assert data["market_test"] and data["comps"] and data["rodrigo_says"]          # it gets the rules' findings and the sales
            return {"faults": ["The resale number came from the best sales, which are redone houses."],
                    "why_not_selling": "It needs a full rehab and the price never moved.",
                    "as_is_low": 265000, "as_is_high": 9_000_000, "arv_low": 400000, "arv_high": 420000, "repairs": 90000,
                    "offer": self.offer, "max_price": 9_000_000, "comps_to_use": ["103 Test St"], "comps_to_drop": ["100 Test St: redone"],
                    "confidence": "medium", "conclusion": "Based on the sales that compare and the work it needs, it is worth about "
                    "$270,000 as it sits, so we offer $255,000.", "offer_reason": "Two similar homes nearby sold for $300,000 and $330,000, "
                    "and this one needs a full rehab.", "verify": ["Roof age"]}
        if purpose == "offer_email":
            return {"subject": "Offer on the property $255,000", "body": OFFER_BODY.replace("$255,000", f"${self.offer:,}")}
        if purpose == "second_read":
            return {"verdict": "SEND", "problems": [], "fix": "", "why": "The offer fits what we know."}
        if purpose == "card_chat":
            return {"answer": "The top sales are not like this one.", "lead_note": "", "reply_change": ""}
        raise AssertionError(purpose)


def _sitting(loaded, price=300000, dom=228):
    loaded.execute("UPDATE properties SET list_price=?, days_on_market=?, price_cuts=0", (price, dom))


def test_market_test_catches_the_greenwood_fault(cfg):
    s = {"p": {"list_price": 372900, "days_on_market": 228, "price_cuts": 0, "sqft": 1548},
         "numbers": {"ours": 521000, "arv_basis": "median $/sf of the top half of 5 sales (few say renovated)", "repairs": 54180,
                     "repair_tier": "moderate", "ratio_source": "broward * multifamily * (county, n=311, borrowed from broward)"},
         "comps": [{"ppsf": 518, "sqft": 1833}, {"ppsf": 234, "sqft": 1920}, {"ppsf": 389, "sqft": 1800}, {"ppsf": 359, "sqft": 1505},
                   {"ppsf": 577, "sqft": 1602}], "asked": "it really needs a full rehab", "grade": "B"}
    faults = " ".join(verdict.market_test(s, cfg))
    assert "nobody has paid $372,900 in 228 days" in faults and "the math is wrong, not the market" in faults
    assert "$234 to $577" in faults and "top half" in faults and "borrowed" in faults and "full rehab" in faults
    # whatever comes back, nothing of ours sits at or above the asking price of a house that has been sitting
    v = verdict.settle({"as_is_low": 500000, "as_is_high": 536000, "arv_low": 0, "arv_high": 802000, "repairs": 54180, "offer": 521000,
                        "max_price": 536000}, s, cfg)
    assert v["offer"] < 372900 and v["max_price"] < 372900 and v["capped"] and v["gap"] > 0
    r = verdict.settle(verdict.by_rules(s, cfg, [], None), s, cfg)                 # rules only: lower half of the sales, full rehab counted
    assert r["source"] == "rules" and r["repairs"] >= 1548 * 60 and r["offer"] < 372900


def test_bottom_line_is_last_and_the_offer_is_drafted_on_the_card(loaded, cfg, tmp_path, monkeypatch):
    asana, gid = _setup(loaded, cfg, tmp_path, monkeypatch, ASK)
    _sitting(loaded)
    cfg.secrets["REPORT_TO"] = "boss@example.com"
    cfg.raw["outreach"]["second_read"]["enabled"] = True          # two agents on anything that goes to a person
    llm = Reader()
    n = sync_feedback(loaded, cfg, asana, llm)
    assert n["numbers"] >= 1 and n.get("verdicts") == 1
    last = asana.stories[gid][-1]
    assert "BOTTOM LINE" in last and "🏁" in last and "<strong>Asking $300,000." in last            # bold, last on the card
    assert "Offer $255,000." in last and "Do not go over $290,000." in last                         # an absurd ceiling is held under the asking price
    assert "That is $45,000 (15%) under asking." in last
    assert "What was wrong in our numbers" in last and "Why it has not sold" in last and "Based on the sales" in last
    assert "⚖️ Second read" in last and "Nothing sends until you drag the card to Approved" in last
    card = parse_card(asana.tasks[gid]["notes"])
    assert "Price: $255,000, cash" in card["body"] and card["cc"] == ["boss@example.com"] and "$" not in card["subject"]
    assert asana.where[gid] == asana.ensure_section(None, "Needs Me") and asana.where[gid] != _sections(cfg)["approved"]
    row = loaded.execute("SELECT body, variant, status FROM outreach WHERE id=?", (card["outreach_id"],)).fetchone()
    assert row["variant"] == "offer:verdict" and "$255,000" in row["body"] and row["status"] != "sent"
    assert loaded.execute("SELECT offer, max_price FROM verdicts").fetchone()["offer"] == 255000
    before = len(asana.stories[gid])
    assert sync_feedback(loaded, cfg, asana, llm).get("verdicts", 0) == 0 and len(asana.stories[gid]) == before     # once


def test_without_claude_the_rules_answer_and_no_offer_email(loaded, cfg, tmp_path, monkeypatch):
    asana, gid = _setup(loaded, cfg, tmp_path, monkeypatch, ASK)
    _sitting(loaded)
    before = asana.tasks[gid]["notes"]

    class Off:
        available = False
    sync_feedback(loaded, cfg, asana, Off())
    last = asana.stories[gid][-1]
    assert "BOTTOM LINE" in last and "Rules only" in last and "No offer email drafted" in last
    assert asana.tasks[gid]["notes"] == before                                     # the email on the card is untouched


def test_his_number_becomes_the_offer(loaded, cfg, tmp_path, monkeypatch):
    assert verdict.asked_price(["offer 290k"]) == 290000 and verdict.asked_price(["let's offer $285,000"]) == 285000
    assert verdict.asked_price(["make it 262.5k"]) == 262500
    assert verdict.asked_price(["what would be a good offer for them"]) is None
    assert verdict.asked_price(["offer 290k but ask about the roof first"]) is None and verdict.asked_price(["290k"]) is None
    # Tamara Mason, 2026-11-18: two comments, a price and his terms; the rewriter dropped the offer and said "Revised"
    tamara = ["Let's send an offer at $250,000 - draft that as an offer and let's email them and send it to them, tell them we've been "
              "looking at it and it's something we're open to taking on..", "Also our terms 5k escrow,  15 day inspection, 30 day closing "
              "or as soon as the title is approved."]
    assert verdict.asked_price(tamara) == 250000
    assert verdict.parse_terms(" ".join(tamara)) == {"inspection_days": 15, "close_days": 30, "deposit": 5000,
                                                     "close_note": ", or as soon as title is approved"}
    assert verdict.offer_terms(cfg, 250000, {}, verdict.parse_terms(" ".join(tamara)))[-1] == "Close in 30 days, or as soon as title is approved"
    assert verdict.asked_price(["Okay let's draft a $275k asking price, and write the same thing we write on all of them: 5k escrow, "
                                "15 day inspection, cash close, same as the other ones."]) == 275000
    assert verdict.asked_price(["offer 250k, the ARV is 400k"]) is None            # two prices: not ours to guess
    asana, gid = _setup(loaded, cfg, tmp_path, monkeypatch, "offer 262k")
    _sitting(loaded)
    llm = Reader(offer=262000)
    n = sync_feedback(loaded, cfg, asana, llm)
    assert n.get("offers_drafted") == 1 and "verdict" not in llm.calls and "revise_email" not in llm.calls
    card = parse_card(asana.tasks[gid]["notes"])
    assert "Price: $262,000, cash" in card["body"]
    assert "$38,000 (13%) under asking" in asana.stories[gid][-1]
    assert asana.where[gid] == asana.ensure_section(None, "Needs Me")


def test_photos_are_read_once_and_reach_the_last_reader(loaded, cfg):
    p = dict(loaded.execute("SELECT * FROM properties LIMIT 1").fetchone())
    looks = []

    class Eyes:
        available = True

        def read_images(self, purpose, images, prompt, schema, system=None, max_tokens=600):
            looks.append(len(images))
            return {"level": "HEAVY", "repairs_low": 85000, "repairs_high": 110000, "seen": ["Original kitchen"], "not_shown": ["No roof"],
                    "summary": "Kitchen and both baths are original."}
    fetch = lambda _p: [(b"\xff\xd8\xff", "image/jpeg")] * 5        # noqa: E731
    a = verdict.photo_read(loaded, cfg, Eyes(), p, fetch)
    b = verdict.photo_read(loaded, cfg, Eyes(), p, fetch)
    assert a["level"] == "HEAVY" and b["repairs_high"] == 110000 and b["seen"] == ["Original kitchen"] and looks == [5]
    assert verdict.photo_read(loaded, cfg, Eyes(), {**p, "id": 999999}, lambda _p: None) is None        # pictures did not load: goes on


def test_his_stuck_comments_now_work():
    """2026-11-19, cards stuck in Needs Revision: Caridad's rewrite was refused because his QUESTION used the words "price
    reduction"; Denise's "The email we send: ..." was not read as his dictated email."""
    from titan.answers import dictation
    from titan.outreach.compose import copy_problems
    p = [{"remarks": "Tenant in place.", "price_cuts": 0, "list_price": 625000, "original_list_price": 625000, "days_on_market": 94}]
    ask = "Hey Caridad,\n\nCharles with Titan Realty Investments. Do you think the seller is willing to work with us on a price reduction?\n\nBest regards,"
    assert not [x for x in copy_problems(ask, p) if "came down" in x]
    claim = "Hey Caridad,\n\nI saw it has had a price reduction. Do you know why?\n\nBest regards,"
    assert [x for x in copy_problems(claim, p) if "came down" in x]
    d = dictation(['The email we send: "Hi Denise,\nTitan Realty Investments would like to submit a cash offer.\nPurchase price: $275,000, all cash"'])
    assert d and d[0].startswith("Hi Denise") and "$275,000" in d[0]
