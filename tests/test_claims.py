"""Rodrigo, 2026-11-12 (Eric Cederman): we told a broker a signed document was attached and nothing was. Never again."""
import pytest

from titan.claims import UnbackedClaim, unbacked_claims
from tests.test_approvals import SAT_10AM_ET
from tests.test_commercial import FakeOMGmail, FakeReviewLLM, _om_board


@pytest.mark.parametrize("body", ["Hey Eric,\n\nThe signed NDA is attached.", "Please find the executed confidentiality agreement.",
                                  "Hey Eric,\n\nI've signed the CA, let me know.", "See attached LOI.", "Enclosed is our proof of funds."])
def test_claims_of_a_file_or_a_signature_are_caught(body):
    assert unbacked_claims(body)
    assert unbacked_claims(body, attachments=[{"filename": "nda.pdf"}]) == []        # a real file with it: fine


@pytest.mark.parametrize("body", ["Hey Eric,\n\nThanks for sending them over. Can you share the rent roll?\n\nCharles Grant",
                                  "Hey Lise,\n\nHow old is the roof?\n\nOn Mon, Lise wrote:\n> see attached disclosures"])
def test_plain_emails_and_their_quoted_history_pass(body):
    assert unbacked_claims(body) == []


def test_an_enclosed_room_is_a_room_not_an_attachment():
    """Rodrigo, 2026-11-23 (Alim Rahman, 8870 Taft St): "was the enclosed Florida room done with permits?" was stopped as a
    claim that a file was enclosed, and no comment of his could get it out."""
    assert unbacked_claims("Hey Alim,\n\nWas the enclosed Florida room done with permits?") == []
    assert unbacked_claims("Is the enclosed porch under air? And the enclosed garage?") == []
    assert unbacked_claims("Enclosed is our proof of funds.")
    assert unbacked_claims("The contract is enclosed.")


def test_the_gmail_send_itself_refuses_outside_mail_only():
    from titan.outreach.gmail_client import GmailClient

    class Svc:                                   # any call to Gmail means the email went out
        def users(self):
            raise KeyboardInterrupt("reached Gmail")
    g = object.__new__(GmailClient)
    g.sender, g.sender_name, g.svc = "charles@titanrealtyinvestments.com", "Charles Grant", Svc()
    with pytest.raises(UnbackedClaim):
        g.send("eric@broker.com", "Re: NDA", "Hey Eric,\n\nThe signed NDA is attached.")
    with pytest.raises(KeyboardInterrupt):          # a forward to our own domain is not checked
        g.send("rodrigo@titanrealtyinvestments.com", "FW", "Broker said: see attached OM")


def test_send_it_does_not_override_a_false_attachment_claim(conn, cfg, tmp_path):
    from titan.commercial.books_review import review_pending, send_approved_replies
    from titan.commercial.outreach import poll_om_replies, send_om_requests
    from titan.util import iso
    asana, secs, gid = _om_board(conn, cfg, tmp_path)
    cfg.raw["commercial"]["review"]["auto_reply"] = False
    gm = FakeOMGmail()
    send_om_requests(conn, cfg, asana, gm, now=SAT_10AM_ET)
    conn.execute("UPDATE commercial_threads SET sent_at=?", (iso(SAT_10AM_ET),))
    gm.threads["t1"].append({"id": "r1", "from": "adam@trustrealtyusa.com", "body": "Please sign the CA and I'll send the package.",
                             "internal_ts": int(SAT_10AM_ET.timestamp()) + 3600, "label_ids": [], "attachments": []})
    poll_om_replies(conn, cfg, gm, asana)
    review_pending(conn, cfg, asana, FakeReviewLLM())
    conn.execute("UPDATE commercial_reviews SET reply_draft='Hey Adam,\n\nThe signed CA is attached. Please send the T-12.\n\nCharles Grant'")
    asana.comment_raw(gid, "send it")
    before = len(gm.sent)
    for _ in range(2):
        out = send_approved_replies(conn, cfg, asana, gm, now=SAT_10AM_ET)
        assert out["sent"] == 0 and out["stopped"] == 1
    assert len(gm.sent) == before
    assert sum("STOPPED, not sent" in c for c in asana.stories[gid]) == 1             # told once, not every round


def test_house_words_are_not_attachment_claims():
    """Alim Rahman, 8870 Taft St (2026-11-23): "the enclosed Florida room" stopped his first email three times."""
    from titan.claims import unbacked_claims
    assert unbacked_claims("Was the enclosed Florida room done with permits?") == []
    assert unbacked_claims("It has a 2-car attached garage and an enclosed patio.") == []
    assert unbacked_claims("The contract is attached.") == ["attached"]
    assert "enclosed" in unbacked_claims("Please find enclosed our offer.")
