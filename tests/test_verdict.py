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
              "over. I copied Rodrigo, our acquisitions manager, since he approved the numbers.\n\nOur offer:\n\n• Price: $255,000, cash\n"
              "• As is, no repairs asked of the seller\n• 15 day inspection period\n• $5,000 escrow deposit\n• Close in 30 days, or longer if "
              "that works better for the seller\n\nI know that is under the asking price. Two similar homes nearby sold for $300,000 and "
              "$330,000, and this one needs a full rehab before it can be lived in.\n\nLet me know what the seller thinks. I am happy to get "
              "on a call if that is easier.\n\nBest regards,\n\nCharles Grant\nJunior Acquisitions Agent\nTitan Realty Investments\n"
              "Office Line: +1 (561) 405 7379")

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
    assert "• Price: $255,000, cash" in card["body"] and "• 15 day inspection period" in card["body"] and card["cc"] == ["boss@example.com"] and "$" not in card["subject"]
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
    assert verdict.offer_terms(cfg, 250000, {}, verdict.parse_terms(" ".join(tamara)))[-1] == "• Close in 30 days, or as soon as title is approved"
    assert verdict.asked_price(["Okay let's draft a $275k asking price, and write the same thing we write on all of them: 5k escrow, "
                                "15 day inspection, cash close, same as the other ones."]) == 275000
    assert verdict.asked_price(["offer 250k, the ARV is 400k"]) is None            # two prices: not ours to guess
    asana, gid = _setup(loaded, cfg, tmp_path, monkeypatch, "offer 262k")
    _sitting(loaded)
    llm = Reader(offer=262000)
    n = sync_feedback(loaded, cfg, asana, llm)
    assert n.get("offers_drafted") == 1 and "verdict" not in llm.calls and "revise_email" not in llm.calls
    card = parse_card(asana.tasks[gid]["notes"])
    assert "• Price: $262,000, cash" in card["body"]
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


def test_a_form_letter_is_named_and_an_offer_never_looks_like_one(cfg):
    """Rodrigo, 2026-11-19 (Denise): "it seems like an AI email ... it needs to look like somebody sent it"."""
    form = ("Hi Denise,\n\nI'm reaching out about your listing. Titan would like to submit a cash offer.\n\nOffer summary\n\n"
            "Purchase price: $275,000, all cash\nEarnest money deposit: $5,000\nInspection period: 10 days\nClosing: within 30 days\n\n"
            "Best regards,\n\nCharles Grant")
    why = " ".join(verdict.form_letter(form))
    assert "label lines" in why and "section headings" in why and "form-letter phrases" in why
    assert [x for x in verdict.offer_problems(form + " " + "word " * 60, 275000, None) if "reads like a form" in x]
    assert not verdict.form_letter(OFFER_BODY) and not verdict.offer_problems(OFFER_BODY, 255000, 300000)
    t = verdict.template_offer(cfg, {"name": "Tamara Mason"}, {"street": "455 NW 68th Ave", "city": "Plantation"},
                               {"offer": 250000, "offer_reason": ""}, False, verdict.parse_terms("15 day inspection, 30 day closing or as soon as the title is approved"))
    assert not verdict.form_letter(t["body"]) and "• Close in 30 days, or as soon as title is approved" in t["body"]
    assert t["body"].count("•") == 5                                   # each term its own bullet (Rodrigo, 2026-11-19)


def test_card_shows_the_bottom_line_and_a_conclusion_ask_triggers_it(loaded, cfg, tmp_path, monkeypatch):
    """Zaira Zavala, 2026-11-19: "I need a final analysis ... a clear conclusion on the price we need to be at" got another long read, and
    the card kept showing the engine's old ARV / MAO."""
    from titan.comment_router import classify
    from titan import house_card
    assert "numbers" in classify(["Hey I need a final analysis on this property, you did not give me something clear conclusion on the "
                                  "price we need to be at for this deal."])
    asana, gid = _setup(loaded, cfg, tmp_path, monkeypatch, "")
    card = parse_card(asana.tasks[gid]["notes"])
    hand = [{"text": "🤖 🏁 BOTTOM LINE · 890 E 52nd St, Hialeah\n\nAsking $395,000. Offer $260,000. Do not go over $275,000. That is ..."}]
    assert verdict.learn_from_comments(loaded, card, gid, hand) and not verdict.learn_from_comments(loaded, card, gid, hand)   # stored once
    _, _, props = house_card.load(loaded, card["outreach_id"])
    lines = " ".join(house_card._numbers(props[0]))
    assert "BOTTOM LINE: offer $260,000" in lines and "do not go over $275,000" in lines and "$135,000 (34%) under asking" in lines
    assert "Internal MAO" not in lines


def test_terms_said_backwards_and_offer_price_on_card():
    """Alberto Pascual, 2026-11-19: "Inspection period needs to be 15 day's and escrow let's lower it to 5k" on a card that already
    held the $540,000 offer went to the first-email rewriter and sat in Needs Revision."""
    from titan import verdict
    t = verdict.parse_terms("Inspection period needs to be 15 day's and escrow let's lower it to 5k")
    assert t["inspection_days"] == 15 and t["deposit"] == 5000
    t = verdict.parse_terms("5k escrow, 15 day inspection, 30 day closing")       # the usual order still wins
    assert t == {"deposit": 5000, "inspection_days": 15, "close_days": 30}
    m = verdict.CARD_OFFER_RE.search("Our offer:\n• Price: $540,000, cash\n• $10,000 escrow deposit")
    assert m and m.group(1) == "540,000"
    assert not verdict.CARD_OFFER_RE.search("How old are the roof and the AC?")


def test_run_the_numbers_with_a_repair_budget_is_not_an_offer():
    # Daniel Schachtel, 2026-11-23: "$30,000 … to make it nice" in a run-the-numbers voice note became a $30,000 offer
    from titan.verdict import asked_price
    note = ("let's run the numbers and tell me what a debt service would be if we bought it, how much would we cash flow, "
            "let's say I spend an extra $30,000 on it, buy it with a conventional loan, make an offer that works")
    assert asked_price([note]) is None
    assert asked_price(["Let's send an offer at $250,000"]) == 250000


def test_offer_terms_from_his_words_and_one_price_only():
    from titan import verdict as v
    # Devon Mclean, 2026-11-24: "3k in escrow" was missed; Juan Mora: "no buying agent commission … we pay all closing cost" too
    assert v.parse_terms("send an offer at 180k, 15 day inspection, 3k in escrow")["deposit"] == 3000
    t = v.parse_terms("offer $500,000 - 15 day inspection, $10k escrow, no buying agent commission and we pay all closing cost")
    assert t["deposit"] == 10000 and t["extra"] == ["No buyer's agent commission", "We pay all closing costs"]
    # the terms of the offer already on the card are read back (a redraft keeps them)
    card = "• Price: $500,000, cash\n• 15 day inspection period\n• $10,000 escrow deposit\n• Close in 30 days\n• We pay all closing costs"
    assert v.parse_terms(card)["deposit"] == 10000 and v.parse_terms(card)["extra"] == ["We pay all closing costs"]
    # two prices in one offer email is refused, and an older read's number is cut from the reason
    body = ("Hey Devon,\n\nOur offer:\n\n• Price: $180,000, cash\n\nSimilar duplexes sold for $310,000, so we are offering $195,000."
            "\n\nBest regards")
    assert any("second offer price $195,000" in x for x in v.offer_problems(body, 180000, 375000))
    assert v.clean_reason("Comps sold for $310,000. It needs work, so we are offering $195,000.", 180000) == "Comps sold for $310,000."


def test_send_them_an_email_is_a_rewrite_not_a_hold():
    from titan import comment_router as cr
    # Catherine Nagles, 2026-11-24: dropped as a hold because of the bare word "send"
    said = ["Hey I tried calling this number, let's send them an email letting him know there is no Catherine at the number "
            "that I called, just wondering who do I need to get in touch with for this property"]
    assert "rewrite" in cr.classify(said) and "hold" not in cr.classify(said)
