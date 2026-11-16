"""Rodrigo, 2026-11-16: his flyer blasts from rodrigo@ went to spam. The engine now refuses bulk-looking mail, reads the mailbox's
bounces, checks the domain's DNS and stops sending when the bounce rate climbs."""
from datetime import datetime, timezone

import pytest

from titan.deliverability import (BulkContent, blast_report, bounce_breaker, bulk_markers, dns_report, format_report, verify_list)

FLYER_HTML = '<div style="background:#f4f4f2;font-family:Georgia"><table role="presentation" width="640"><tr><td><img src="cid:x"></td></tr></table>' + "x" * 50_000
NOW = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)


def test_a_flyer_blast_reads_as_bulk_and_a_plain_email_does_not():
    marks = bulk_markers("Off Market Deal Hialeah: 5 Rental Units · $460,000 · R-2 Lot",
                         "TITAN REALTY INVESTMENTS - OFF MARKET DEAL ... You received this because you have bought investment property. Reply STOP to be removed.",
                         html=FLYER_HTML, headers={"List-Unsubscribe": "<mailto:x>"})
    joined = " ".join(marks).lower()
    assert "subject reads as an ad" in joined and "flyer" in joined and "mailing-list header" in joined and "marketing language" in joined
    assert any("reply stop" in m.lower() for m in bulk_markers("Hi", "Reply STOP to be removed."))
    assert bulk_markers("Re: 123 Main St", "Hey Eugene,\n\nCharles with Titan Realty Investments. I saw your listing on 123 Main St.\n\n"
                                            "Is the seller open to an as-is sale, or are they holding for asking?\n\nCharles") == []


def test_gmail_send_refuses_bulk_to_an_outside_address_but_not_our_own_reports():
    from titan.outreach.gmail_client import GmailClient
    g = GmailClient.__new__(GmailClient)
    g.sender, g.sender_name = "charles@titanrealtyinvestments.com", "Charles Grant"
    with pytest.raises(BulkContent):
        g.send("owner@yahoo.com", "Investment Opportunity Hialeah: $460,000", "Reply STOP to be removed", html=FLYER_HTML)
    # the morning report (HTML, to Rodrigo) is internal mail: the guard does not apply (the send reaches the Gmail API layer)
    from titan.outreach import gmail_client as gc
    gc.retry = lambda fn, **kw: fn()
    g.svc = type("S", (), {"users": lambda self: type("U", (), {"messages": lambda self: type("M", (), {
        "send": lambda self, userId, body: type("E", (), {"execute": lambda self: {"id": "m1", "threadId": "t1"}})()})()})()})()
    assert g.send("rodrigo@titanrealtyinvestments.com", "Titan morning report", "report", html=FLYER_HTML)["message_id"] == "m1"


class FakeGmail:
    sender = "rodrigo@titanrealtyinvestments.com"

    def __init__(self, sent, bounces):
        self.sent, self.bounces = sent, bounces
        self.msgs = {}
        for i, (to, subj, ts) in enumerate(sent):
            self.msgs[f"s{i}"] = {"to": to, "subject": subj, "internal_ts": ts, "body": "flyer"}
        for i, (dead, ts) in enumerate(bounces):
            self.msgs[f"b{i}"] = {"to": self.sender, "subject": "Delivery Status Notification (Failure)", "internal_ts": ts,
                                  "body": f"Address not found\nYour message wasn't delivered to {dead} because the address couldn't be found.\n"
                                          f"The response from the remote server was:\n554 30 Sorry, your message to {dead} cannot be delivered. This mailbox is disabled (554.30)."}

    def search_refs(self, q, max_results=500):
        kind = "b" if "mailer-daemon" in q else "s"
        return [{"id": k, "thread_id": k} for k in self.msgs if k.startswith(kind)]

    def message(self, mid):
        return self.msgs[mid]


def test_blast_report_finds_the_blast_and_the_dead_addresses():
    day = int(datetime(2026, 11, 4, 22, 0, tzinfo=timezone.utc).timestamp())
    sent = [(f"owner{i}@yahoo.com", "Off Market Deal Hialeah: 5 Rental Units", day + i * 150) for i in range(40)]
    sent += [("rodrigo@titanrealtyinvestments.com", "TEST flyer", day)]          # to ourselves: not outreach
    sent += [("walter@grandoaks.com", "Re: 331 John Ringling", day + 99_000)]
    bounces = [(f"owner{i}@yahoo.com", day + 20) for i in range(4)]
    rep = blast_report(FakeGmail(sent, bounces), days=30, now=NOW)
    assert rep["sent"] == 41 and len(rep["bounces"]) == 4 and rep["bounce_pct"] == 9.8
    assert rep["blasts"][0]["recipients"] == 40 and "off market deal" in rep["blasts"][0]["subject"]
    assert rep["dead_addresses"] == [f"owner{i}@yahoo.com" for i in range(4)]
    text = format_report(rep)
    assert "bounced" in text and "40 people" in text and "9.8%" in text


def test_breaker_stops_only_on_a_real_bounce_rate():
    class Cfg(dict):
        def get(self, k, d=None):
            return dict.get(self, k, d)
    cfg = Cfg({"outreach.deliverability": {"max_bounce_pct": 5, "min_sends": 20}})
    day = int(NOW.timestamp()) - 3600
    bad = FakeGmail([(f"o{i}@aol.com", "Deal", day) for i in range(30)], [(f"o{i}@aol.com", day) for i in range(3)])
    assert "STOPPED sending" in bounce_breaker(bad, cfg, NOW)
    fine = FakeGmail([(f"o{i}@aol.com", "Deal", day) for i in range(30)], [("o1@aol.com", day)])
    assert bounce_breaker(fine, cfg, NOW) is None
    few = FakeGmail([("o1@aol.com", "Deal", day)], [("o1@aol.com", day)])      # 100% of one email is noise, not a signal
    assert bounce_breaker(few, cfg, NOW) is None
    cfg["outreach.deliverability"] = {"breaker": False}
    assert bounce_breaker(bad, cfg, NOW) is None


def test_dns_report_speaks_plainly():
    good = {"titanrealtyinvestments.com": ["v=spf1 include:_spf.google.com ~all", "google-site-verification=abc"],
            "google._domainkey.titanrealtyinvestments.com": ["v=DKIM1; k=rsa; p=MIIB"], "_dmarc.titanrealtyinvestments.com": ["v=DMARC1; p=quarantine; rua=mailto:d@x"]}
    r = dns_report("titanrealtyinvestments.com", resolver=lambda n: good.get(n, []))
    assert r["spf"]["ok"] and r["dkim"]["ok"] and r["dmarc"]["ok"]
    r = dns_report("titanrealtyinvestments.com", resolver=lambda n: [])
    assert not r["spf"]["ok"] and "include:_spf.google.com" in r["spf"]["fix"]
    assert not r["dkim"]["ok"] and "Google Admin" in r["dkim"]["fix"]
    assert not r["dmarc"]["ok"] and "p=quarantine" in r["dmarc"]["fix"]
    two = dns_report("x.com", resolver=lambda n: ["v=spf1 a ~all", "v=spf1 include:_spf.google.com ~all"] if n == "x.com" else [])
    assert "more than one SPF" in two["spf"]["note"]


def test_verify_list_splits_clean_dead_unknown():
    mx = lambda d: [] if d == "nomail.example" else ["mx." + d]
    rcpt = lambda host, email: False if email.startswith("gone@") else (None if email.startswith("maybe@") else True)
    r = verify_list(["Good@Gmail.com", "good@gmail.com", "gone@yahoo.com", "maybe@cox.net", "fpages1@worldnet.att.net", "info@brokerage.com",
                     "bad address", "x@nomail.example", "known@dead.com"], mx=mx, rcpt=rcpt, dead_known={"known@dead.com"})
    assert [x["email"] for x in r["clean"]] == ["good@gmail.com"]
    dead = {x["email"]: x["why"] for x in r["dead"]}
    assert set(dead) == {"gone@yahoo.com", "fpages1@worldnet.att.net", "bad address", "x@nomail.example", "known@dead.com"}
    assert "bounced before" in dead["known@dead.com"]
    assert {x["email"] for x in r["unknown"]} == {"maybe@cox.net", "info@brokerage.com"}
