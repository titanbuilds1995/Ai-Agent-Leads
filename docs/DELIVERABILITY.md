# Why our emails go to spam, and the rules that keep them out

Rodrigo, 2026-11-16: "a lot of emails that I've sent out to multiple people have gone to spam … figure out why and build a solution."

## What happened (read from the headers in rodrigo@'s mailbox, Nov 4 – Nov 9)

| Date | From | What went out | Result |
|---|---|---|---|
| Nov 4, 5:10–6:58pm | rodrigo@ via a script on the Mac (smtp.gmail.com) | ~45 identical HTML flyers "Off Market Deal Hialeah: 5 Rental Units · $460,000 · R-2 Lot", 140 KB each, inline picture, "Reply STOP to be removed", a `List-Unsubscribe` header, one every ~2.5 minutes | 8 hard bounces: aol, bellsouth, cox, yahoo "mailbox disabled", worldnet.att.net "domain couldn't be found" |
| Nov 6, 11:00–11:35am | same script | ~15 more of the same flyer | 1 bounce |
| Nov 9, 12:27–3:31pm | rodrigo@ by hand (Gmail) | the 430 KB flyer "Investment Opportunity Hialeah: 4 Rental Units · $460,000 · R-2 Lot", forwarded 5 times to 5 people | 1 bounce (barry@sharpproperties.com misspelled) |

Authentication was fine: every message carried a DKIM signature for titanrealtyinvestments.com (selector `google`) and went through Google's
servers. **The problem is not SPF/DKIM. It is what and how we sent.** Gmail, Yahoo and Outlook score a sending domain on:

1. **Bounce rate.** 10 of ~65 blast emails bounced (about 15%). Over 2% the domain gets filed as spam; over 5% they throttle it. The list
   was scraped from old owner records: aol, bellsouth, peoplepc, worldnet addresses that died years ago.
2. **Identical content to many strangers from one personal mailbox.** Same subject, same 140 KB HTML, same picture, minutes apart.
   A person never writes that; a campaign tool does. "Reply STOP to be removed" and the `List-Unsubscribe` header *tell* the receiver
   it is a campaign, and since Feb 2024 Gmail and Yahoo require bulk senders to have DMARC and one-click unsubscribe, or the mail goes
   to spam.
3. **Marketing shape.** A designed flyer, a price in the subject, "INVESTMENT OPPORTUNITY" in capitals, "You received this because…"
4. **Shared reputation.** Charles@ and rodrigo@ are on the same domain. Charles's mail is plain text, one agent at a time, personal,
   inside the send window, and it lands. The blasts from rodrigo@ pull the whole domain's score down, Charles included.

## The rules (any mailbox on our domain, any sender: Rodrigo, Charles, a script, a cloud session)

- **No flyers by email to strangers.** One plain-text email, one person, written to that person. A flyer goes as a PDF link or on a
  second email after they answer. The engine refuses anything that reads as bulk (`GmailClient.send` → `BulkContent`, no override).
- **Never send to an unverified list.** `make verify-list FILE=list.csv` first (free: syntax, duplicates, role mailboxes, dead
  domains, the domain's mail server, an SMTP check). Send only to the `.clean.csv`. Addresses that bounced once are never sent again.
- **No scripts through smtp.gmail.com from a personal mailbox.** Everything to outside addresses goes through the engine (Charles@,
  approvals board, send window, daily cap, the guards) or by hand one at a time.
- **Pace.** Under 100 new recipients a day per mailbox (Google Workspace flags a user above that and can suspend sending for 24 h);
  the engine's cap is `outreach.daily_email_cap` (120 today) spread over 8am–6pm, 5 per 20-minute round.
- **No "STOP", "unsubscribe", "you received this because", no price or "opportunity" in the subject**, no capitals, no `List-*` headers.
- **Circuit breaker.** If more than 5% of the last 7 days of sends from Charles's mailbox bounce (20+ sends), approvals stop and one
  "⛔ Sending stopped · too many bounces" card goes to Needs Rodrigo. `outreach.deliverability` in config.

## What to check once on the Mac (DNS, 10 minutes)

`make deliverability` prints SPF / DKIM / DMARC with the fix for each. Expected:

- SPF: `v=spf1 include:_spf.google.com ~all` (one record only).
- DKIM: a key under `google._domainkey` (Google Admin → Apps → Gmail → Authenticate email, 2048-bit, "Start authentication").
- DMARC: `v=DMARC1; p=quarantine; rua=mailto:dmarc@titanrealtyinvestments.com; adkim=r; aspf=r; pct=100` on `_dmarc.titanrealtyinvestments.com`.
  If there is none today, start with `p=none` for a week, read the reports, then `p=quarantine`.
- Google Postmaster Tools (postmaster.google.com): add the domain, verify with the TXT it gives, and the "Domain reputation" and
  "Spam rate" graphs show exactly how Gmail sees us. Free. Worth checking weekly.

## Getting the reputation back

Reputation recovers on its own in 2–4 weeks of clean sending: low volume, verified addresses, personal text, people answering. Nothing
else speeds it up. Do not switch to a new domain for blasts; a fresh domain with the same list and the same flyer is filtered faster.
