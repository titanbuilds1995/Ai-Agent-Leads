# Twilio A2P 10DLC campaign: what to paste

Brand registration is done (Rodrigo, 2026-10-26). The campaign is the step that unlocks sending. Every field below is
written to match what the engine actually does, so the reviewer's checks (website, opt-in flow, sample messages,
privacy wording) all line up. Replace `YOUR-VERCEL-DOMAIN` with the Vercel project's domain (Vercel → the dashboard
project → Domains; e.g. `titan-dashboard.vercel.app`), or add the custom domain `go.titanrealtyinvestments.com` to the
project first and use that.

## The pieces that must exist before you submit

| Piece | Where | Status |
|---|---|---|
| Opt-in form | `https://YOUR-VERCEL-DOMAIN/sms-consent` | built (`dashboard/public/sms-consent.html`), live on the next Vercel deploy |
| SMS terms page | `https://YOUR-VERCEL-DOMAIN/sms-terms` | built |
| Privacy policy with the SMS sentence | `https://YOUR-VERCEL-DOMAIN/privacy` | built; **also add the same sentence to the privacy page on titanrealtyinvestments.com** (below) |
| Pop-up on the company website | one script tag on titanrealtyinvestments.com (below) | to add |
| Consent records | Vercel Blob `titan/consents/` → `make consents` → `agents.sms_consent_at` | built; the engine refuses to text a number without one |

The reviewer opens the website you gave on the brand. They look for (1) a way to opt in, (2) the privacy policy saying
mobile information is not shared with third parties for marketing, (3) a link to SMS terms. Having the form on a Vercel
subdomain is fine as long as the main site links to it or embeds it.

## Add to titanrealtyinvestments.com (two things)

**The site is built on Base44 (Rodrigo, 2026-10-26): use `docs/BASE44_BRIEF.md` instead of the script tag below.** Base44 builds the
form natively and posts to our API; the script-tag route is for a site where you can paste HTML.

**1. The pop-up.** Paste before `</body>` on the site (Wix/Squarespace/WordPress: "custom code" / "code injection" / footer):

```html
<script src="https://YOUR-VERCEL-DOMAIN/sms-consent-embed.js" data-form="https://YOUR-VERCEL-DOMAIN/sms-consent" defer></script>
```

That adds an "OK to text me" button bottom-right that opens the form in a pop-up. Options: `data-label="Text me instead"`,
`data-auto="1"` (opens on page load; not recommended, reviewers prefer a button or link), `data-hide-button="1"` and open it
from any link with `onclick="TitanSmsConsent.open();return false;"`.

**2. The privacy sentence.** Add this to the privacy policy page on the main site, word for word (it is the sentence
TCR reviewers search for):

> Mobile information, including phone numbers and SMS opt-in consent, is never sold, rented or shared with third parties
> or affiliates for marketing or promotional purposes. Reply STOP to any text to opt out, or HELP for help.

Then link that page to `https://YOUR-VERCEL-DOMAIN/sms-terms` as "SMS terms".

## Campaign form, field by field

**Campaign use case:** Low Volume Mixed (under 2,000 segments a day; cheapest, one review) — or Mixed if volume will be
higher. Do not pick "Customer Care" alone: the first text is outreach.

**Campaign description** (paste):

> Titan Realty Investments is a South Florida real estate investment company. Our acquisitions team sends conversational,
> one-to-one text messages to licensed real estate agents and property owners about a specific property they have listed
> for sale or discussed with us: questions about the condition of the listing, whether the seller has flexibility on price,
> scheduling a walkthrough, and the status of an offer. Each conversation is about one property, is written for that
> person and is handled by a staff member. Contacts are reached first by email or phone and are texted only after they
> opt in through our web form (https://YOUR-VERCEL-DOMAIN/sms-consent) or ask us in writing to text them. We do not send
> promotions, alerts, newsletters or bulk messages. Every first text includes our company name and "Reply STOP to opt out".

**Message flow / how consumers opt in** (paste; this is the field Rodrigo asked about):

> Opt-in happens on our website form at https://YOUR-VERCEL-DOMAIN/sms-consent (also embedded as a pop-up on
> https://titanrealtyinvestments.com). When an agent or owner is already talking with us about a property by email or
> phone, we send them the link; they enter their name and mobile number and check a consent box that reads: "By checking
> this box and submitting, I agree to receive SMS text messages from Titan Realty Investments at the mobile number above
> about properties I have listed or discussed with Titan. Message frequency varies. Message and data rates may apply. Reply
> STOP at any time to opt out and HELP for help. Consent is not a condition of any purchase." The form links to our SMS
> terms (https://YOUR-VERCEL-DOMAIN/sms-terms) and privacy policy (https://YOUR-VERCEL-DOMAIN/privacy). Each submission is
> stored with the date, time, IP address and the exact consent wording, and our system will not send a text to any number
> that does not have a stored opt-in. A contact can also opt in by texting our office line first or by asking in writing
> (email) to be texted; those requests are logged the same way.

**Opt-in keywords:** `START, YES, UNSTOP`
**Opt-in message:**
> Titan Realty Investments: you're opted in to texts about your listing. Msg frequency varies. Msg & data rates may apply. Reply STOP to opt out, HELP for help.

**Opt-out keywords:** `STOP, STOPALL, UNSUBSCRIBE, CANCEL, END, QUIT`
**Opt-out message:**
> Titan Realty Investments: you have been unsubscribed and will receive no further texts from us. Reply START to opt back in.

**Help keywords:** `HELP, INFO`
**Help message:**
> Titan Realty Investments: text support. Email Charles@titanrealtyinvestments.com or call (561) 405-7379. Reply STOP to opt out.

**Message samples** (these are the engine's real first-touch texts, `SMS_TEMPLATES` + `STOP_LINE` in `titan/outreach/compose.py`):

1. > Hi Maria, Charles w/ Titan Realty. Quick q on NW 11th St: mostly cosmetic, or something bigger? Any flexibility on price? Reply STOP to opt out.
2. > Maria, Charles with Titan Realty here. Looking at NW 11th St for a renovation. Is the seller open to some flexibility on price? Reply STOP to opt out.
3. > Hi Maria, Charles from Titan Realty. On NW 11th St: how deep is the project, and is there some flexibility on price? Reply STOP to opt out.
4. > Thanks Maria. If the seller would consider a 14-day as-is close, I can get you something in writing this week. What day works for a quick walkthrough?

**Checkboxes:** Subscriber opt-in = yes. Subscriber opt-out = yes. Subscriber help = yes. Embedded links = no (the engine's
`validate_sms` rejects any URL). Embedded phone numbers = no. Age-gated = no. Direct lending = no. Number pooling = no.

**Message volume:** well under 2,000 segments a day (the engine's `daily_sms_cap` is 25).

## After it is approved

1. Messaging Service → add the campaign → add the Titan number to the service's Sender Pool. The number then registers
   with carriers (up to a day).
2. Messaging Service → Integration → Incoming messages: "Send a webhook" to `<WEBHOOK_PUBLIC_URL>/twilio/inbound`
   (BLOCKERS.md item 4; the VPS webhook must be up first). Turn on Advanced Opt-Out so STOP/HELP replies are handled
   by Twilio too; the engine also records STOP into do_not_contact.
3. On the Mac's `.env`: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_MESSAGING_SERVICE_SID`. `make doctor` should go green on the Twilio row.
4. `config.yaml` → `outreach.sms_consent_url: https://YOUR-VERCEL-DOMAIN/sms-consent`; keep `channel: EMAIL_FIRST`.
   The engine texts an agent only after their opt-in has been pulled (`make consents` or any `check-replies` tick).
5. Send one test: opt yourself in on the form, run `make consents`, confirm your name shows, then a text to your own number.

## Honest note on the risk

The campaign reviewer may still push back because the first contact with an agent is unsolicited (email) and the
consent only covers the texts. That is the correct structure: the cold step is email, which does not need carrier
consent; texting starts only after the agent says yes. If they reject with a consent reason, reply with the opt-in URL
and the stored-record description above; that answers it in nearly every case.
