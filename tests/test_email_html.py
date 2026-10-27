from email import message_from_bytes
import base64

from titan.outreach.email_html import body_html
from titan.outreach.gmail_client import GmailClient

OFFER = """Hi Lise,

Thanks for the info, it helped. I've cc'd Rodrigo, our acquisitions manager, who went through the numbers with me and signed off on them.

Here is where we stand on 529 23rd Ave N:

Price: $209,000, cash, as is
Closing: 30 days (we can go longer since the house is not vacant)
Deposit: $5,000 into escrow
Inspection: 9 days

We are not represented by an agent, so there is no buyer side commission. If the seller was planning on 6% & up, you could take 4% <and> the seller keeps the other 2%.

Best regards,

Charles Grant
Junior Acquisitions Agent
Titan Realty Investments
Office Line: +1 (561) 405 7379"""


def test_body_html_is_gmail_composer_style():
    h = body_html(OFFER)
    assert h.startswith('<div dir="ltr"><div>Hi Lise,</div><div><br></div><div>Thanks for the info')
    # the long paragraph is ONE div: no hard breaks, the mail client flows it to its width
    assert "<div>Thanks for the info, it helped. I've cc'd Rodrigo, our acquisitions manager, who went through the numbers with me and signed off on them.</div>" in h
    # the terms block is a bulleted list
    assert "<ul" in h and "<li>Price: $209,000, cash, as is</li>" in h and "<li>Inspection: 9 days</li>" in h
    assert h.count("<li>") == 4
    # the signature keeps its lines, no bullets
    assert "<div>Charles Grant</div><div>Junior Acquisitions Agent</div><div>Titan Realty Investments</div><div>Office Line: +1 (561) 405 7379</div></div>" in h
    assert "<li>Office Line" not in h
    # text is escaped
    assert "6% &amp; up" in h and "4% &lt;and&gt; the seller" in h


def test_first_email_without_a_list_is_plain_paragraphs():
    body = "Hey Eugene,\n\nCharles with Titan Realty Investments. I saw your listing on NW 5th St.\n\nIs the seller flexible on timing?\n\nBest regards,\n\nCharles Grant"
    h = body_html(body)
    assert "<ul" not in h
    assert h == ('<div dir="ltr"><div>Hey Eugene,</div><div><br></div><div>Charles with Titan Realty Investments. I saw your listing on NW 5th St.</div>'
                 '<div><br></div><div>Is the seller flexible on timing?</div><div><br></div><div>Best regards,</div><div><br></div><div>Charles Grant</div></div>')


def test_mime_carries_text_and_html_alternatives():
    g = GmailClient.__new__(GmailClient)
    g.sender, g.sender_name = "charles@example.com", "Charles Grant"
    raw = GmailClient._mime(g, "lise@example.com", "Re: Quick question", OFFER, cc=["rodrigo@example.com"])["raw"]
    msg = message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg.get_content_type() == "multipart/alternative"
    parts = msg.get_payload()
    assert [p.get_content_type() for p in parts] == ["text/plain", "text/html"]
    assert parts[0].get_payload(decode=True).decode().strip() == OFFER
    assert "<li>Price: $209,000, cash, as is</li>" in parts[1].get_payload(decode=True).decode()
    assert msg["Cc"] == "rodrigo@example.com"


def test_mime_keeps_a_designed_html_when_given():
    g = GmailClient.__new__(GmailClient)
    g.sender, g.sender_name = "charles@example.com", "Charles Grant"
    raw = GmailClient._mime(g, "r@example.com", "Titan morning report", "text version", html="<p>designed</p>")["raw"]
    msg = message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg.get_payload()[1].get_payload(decode=True).decode().strip() == "<p>designed</p>"
