"""Rodrigo, 2026-11-15 (Walter got three emails in an hour): don't email someone who already acknowledged us, and don't send a
second email the same day before they answer, without his OK written after a bold warning."""
from titan.asana import BOT
from titan.comment_router import classify
from titan.contact_guard import ContactHold, already_warned, confirmed_after_hold, hold_reason, is_ack, warning

DOM = "titanrealtyinvestments.com"
NOW = 1_800_000_000


def msg(frm, body, hours_ago):
    return {"from": frm, "body": body, "internal_ts": NOW - int(hours_ago * 3600)}


def test_an_acknowledgement_holds_the_next_email():
    thread = [msg("Rodrigo <rodrigo@titanrealtyinvestments.com>", "Here is the deal, flyer attached.", 50),
              msg("Walter Neal <walter@grandoaks.com>", "Excellent\n\nOn Sun, Nov 15, 2026 at 11:07 Rodrigo wrote:\n> Here is the deal", 49)]
    r = hold_reason(thread, DOM, NOW)
    assert r and "Walter Neal" in r and "Excellent" in r
    assert "**HOLD, not sent:" in warning(r)


def test_a_real_question_is_not_an_acknowledgement():
    assert not is_ack("Thanks. What is the roof age and who pays the insurance?")
    assert not is_ack("Thanks for this. " + "We reviewed the rent roll and the leases and want to talk numbers with the seller next week " * 2)
    assert is_ack("Absolutely, will review tonight 👍")
    assert hold_reason([msg("Rodrigo <rodrigo@titanrealtyinvestments.com>", "Deal attached.", 5),
                        msg("Walter <walter@grandoaks.com>", "Thanks, can you send the T-12?", 4)], DOM, NOW) is None


def test_two_emails_in_a_day_with_no_answer_is_bombarding():
    thread = [msg("Walter <walter@grandoaks.com>", "Send me what you have on Sarasota, and the rent roll please, we want to look this week.", 30),
              msg("Rodrigo <rodrigo@titanrealtyinvestments.com>", "Here it is.", 2)]
    assert "1 time in the last 24 hours" in hold_reason(thread, DOM, NOW)
    old = [thread[0], msg("Rodrigo <rodrigo@titanrealtyinvestments.com>", "Here it is.", 72)]
    assert hold_reason(old, DOM, NOW) is None        # a follow-up days later is fine


def test_only_his_ok_after_the_warning_lets_it_through():
    before = [{"text": "send it"}, {"text": f"{BOT} {warning('Walter already answered.')}"}]
    assert not confirmed_after_hold(before, BOT) and already_warned(before, BOT)
    after = before + [{"text": "send anyway"}]
    assert confirmed_after_hold(after, BOT)
    again = after + [{"text": f"{BOT} {warning('Walter already answered again.')}"}]
    assert not confirmed_after_hold(again, BOT)       # a new warning needs a new OK
    assert "send" in classify(["send anyway"])        # on a house card it moves the card to Approved


def test_gmail_send_refuses_without_ok():
    import pytest
    from titan.outreach.gmail_client import GmailClient
    g = GmailClient.__new__(GmailClient)
    g.sender = "charles@titanrealtyinvestments.com"
    g.thread_messages = lambda tid: [msg("Charles <charles@titanrealtyinvestments.com>", "Deal attached.", 3),
                                     msg("Walter <walter@grandoaks.com>", "Perfect, got it.", 2)]
    with pytest.raises(ContactHold):
        g.send("walter@grandoaks.com", "Re: deal", "One more thing.", thread_id="t1")


def test_told_us_to_stop_blocks_even_with_his_ok():
    """2026-11-18, Larry Starr: "Don't contact me again. I will take action against you if you do." No override."""
    import pytest
    from titan.contact_guard import StopContact, told_us_to_stop
    from titan.outreach.gmail_client import GmailClient
    thread = [msg("Charles <charles@titanrealtyinvestments.com>", "Does it hold an eight cap? Please stop me if not.", 50),
              msg("Larry <larry@svn.com>", "Don't contact me again. I will take action against you if you do.", 2)]
    assert told_us_to_stop(thread[:1], DOM) is None                    # our own words never trip it
    assert told_us_to_stop(thread, DOM)
    g = GmailClient.__new__(GmailClient)
    g.sender = "charles@titanrealtyinvestments.com"
    g.thread_messages = lambda tid: thread
    with pytest.raises(StopContact):
        g.send("larry@svn.com", "Re: deal", "Thanks for the update.", thread_id="t1", confirmed=True)
