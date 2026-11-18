"""Rodrigo, 2026-11-18: "stop messaging agents and telling them congrats on getting it done ... I thought we are double checking"."""
from titan.commercial.books_review import DEAD_RE, commits_us, template_reply
from titan.outreach.compose import tone_problems

NICHOLAS = ("Good afternoon, Charles! Sorry for the delay, but this is being sold as the building and property along with the "
            "contents of the business, but not the business itself. It has been owner operated since it was built two years ago, "
            "so no rent rolls. Based on our market analysis, it'll produce a cap rate of seven.")


def test_being_sold_or_leased_is_not_a_closed_deal():
    for text in (NICHOLAS, "The center is 100% leased NNN.", "Fully leased to Dollar General", "Once sold the taxes reset",
                 "Sold as-is", "The seller has not sold anything else"):
        assert not DEAD_RE.search(text), text
    for text in ("We are under contract.", "It just sold, sorry.", "This one has been sold", "The property sold last week",
                 "We accepted an offer yesterday", "It is no longer available", "We leased it to a national tenant"):
        assert DEAD_RE.search(text), text


def test_pass_never_says_congrats():
    for sold in (True, False):
        body = template_reply("pass", "Nicholas", sold=sold, street="3434 FL-60")
        assert "congrat" not in body.lower()


def test_congrats_holds_any_draft():
    assert commits_us("Hey Nick,\n\nCongrats on getting it done!\n\nCharles Grant")
    assert tone_problems("Hey Ann,\n\nCongratulations on the contract. Anything else listed?\n\nBest,\nCharles")
