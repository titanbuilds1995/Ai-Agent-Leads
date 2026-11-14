# Titan lead engine: read this first

Rodrigo Gayoso (Principal, Titan Realty Investments) runs this. It scrapes on-market South Florida listings through Apify,
scores and values them with Claude, finds the listing agent, and drafts offer emails to agents in Rodrigo's voice.
Pipeline: ingest → score → value → rank → enrich → outreach drafts → morning report. Entry points are in the `Makefile`.

## Rules for every session
- **Never ask Rodrigo to paste API keys into chat.** Keys live in two places only:
  - his Mac: `.env` in the repo folder (gitignored, already filled in, `make doctor` passed Apify and Anthropic on 2026-10-23);
  - cloud sessions: the environment's settings (environment variables `APIFY_TOKEN`, `ANTHROPIC_API_KEY`).
  If a key is missing in a cloud session, say so in one line and point him to the environment settings. Don't ask again.
- **Don't re-ask settled decisions** (list below). Read `STATUS.md` → "RESUME HERE" for the next step before proposing one.
- **Before you finish any task:** update the "Current status" section below (2–5 lines: what changed, what's next),
  commit, and push. That is how the next session knows what happened.
- **Work lands on one branch.** Start from the repo's default branch and merge your work back into it (or tell Rodrigo
  to merge the PR). Work left on a side branch is invisible to the next session.
- Keep replies short and plain. Rodrigo is not a developer; give him exact commands to paste.
- Run `make test` before pushing. Never send a real email or text without Rodrigo's explicit go-ahead.
- **Two agents on anything that answers a person (Rodrigo, 2026-11-12: "I want this to be saved for any agent that we build, also
  for our MLS deals for single family homes").** Every agent we build that writes to an agent, broker, seller or anyone outside,
  on any pipeline (single-family / MLS houses, 1031 commercial, whatever comes next), works as a pair: one writes, a SECOND reader
  that did not write it reads the whole thread plus the draft and pushes back on the logic before it reaches a card or goes out.
  Use `titan/second_read.py` (`second_read.run(llm, cfg, case, draft, rewrite)`), don't build a new one; show its "⚖️ Second read"
  line where Rodrigo sees the draft; what it did not clear never goes on its own. Details: Decisions → Two agents on every answer.
- **Cost rule (Rodrigo, 2026-10-24): always use what is free or already paid for before spending money.** Check free /
  already-subscribed options (his Claude Code plan, free scrapers, existing connectors) before any paid API or service,
  and never switch on a paid fallback without asking.

## Decisions already made
- **Every email is approved on Asana** (Rodrigo, 2026-10-24; `outreach.approval: asana`): board "Titan Outreach — Email
  Approvals" (project 1219847112999805; sections To Review 1219847260413153, Approved 1219847113107056, Sent 1219847113142042,
  Skip 1219846985445292). One card per email with a PNG of the email + the text between EMAIL markers. Rodrigo's comments →
  `sync_feedback` rewrites the card; dragging to Approved is his go-ahead; `titan.cli approvals` (cron every 20 min, 8–17,
  random jitter) sends `per_tick` approved emails from Charles@ inside the send window (8:30–5:30 ET, every day) under
  `daily_email_cap`, then moves the card to Sent. Nothing sends from To Review. **"Rewrite + send" (Rodrigo, 2026-10-27):** a comment that says what to write AND ends with a plain "send it" ("say we can close in 30 days and send it") is his go-ahead: Claude rewrites it and the card goes straight to Approved; without "send it" the rewrite returns to To Review. Anything conditional, later, to someone else, or with a number/offer keeps the To Review step (`comment_router.send_after_rewrite`). **A new comment on an Approved card holds
  the send**: the card goes back to To Review and the feedback is applied at once. "send me a test" (+ optional address) on
  any card sends a [TEST] copy to Rodrigo (REPORT_TO) from Charles@; `titan.cli test-email <outreach_id>` does it by hand.
  **Every email opens with a short intro** (Rodrigo, Asana 2026-10-24, later: "they don't really know who we are"): first paragraph =
  Charles with Titan Realty Investments, this listing came up / stood out (protocol §0, `INTRO_RE` requires the company name; this
  replaced the earlier no-self-intro rule). **Tightened 2026-10-25 (Rodrigo, Eugene Schroeder card): the name says it all. Never
  "we're investors who buy here regularly", never "genuinely interested" (reads as AI), and since we wholesale, never state that we
  are the buyer: "we're looking for properties in X", "what we look for", "our side"; never "we buy" / "we'd buy" / "serious buyers"
  (protocol §0b, voice §17, `BUYER_RE` rejects them).** **Layout + plain intro (Rodrigo, 2026-10-26):** greeting alone on line 1
  ("Hey Eugene,"), blank line, short paragraphs of 1–3 sentences, question in its own paragraph (`format_paragraphs()` runs on
  every first email, rewrite and reply; `validate_email` rejects a greeting not on its own line). Intro is just "Charles with Titan
  Realty Investments. I saw your listing on X."; `BANNED_RE` rejects "I'll keep this short", "stood out to me", "caught my eye",
  "…and the surrounding area"; never say anything about photos (protocol §0a). **Third column "Needs Revision" (Rodrigo, 2026-10-26):** a card he
  comments on (in To Review or Approved) goes to Needs Revision until the rewrite lands, then back to To Review for his decision;
  nothing ever sends from Needs Revision; a card dragged there without a comment gets one prompt. The engine creates the section
  itself (`needs_revision_section`, between To Review and Approved). **Agent phone on every card** (`agent_line`, older cards
  patched on the next tick), on the reply mirror and on the website's conversation cards (tap to call), so Rodrigo can call
  the agents he likes. A "couldn't rewrite" comment does NOT consume feedback (retried next
  tick, reason shown, e.g. Claude not logged in from cron). `rewrite-pending` leaves cards that Rodrigo's feedback already shaped
  alone and marks deleted cards skipped. Needs ASANA_TOKEN in the Mac's .env.
- **Outreach protocol = `docs/style/OUTREACH_PROTOCOL.md`** (Rodrigo, 2026-10-24): think first (facts / signals / inferences /
  unknowns / rehab level 1–5 / hypothesis / key unknown), then write; never echo the listing's wording; never manufacture
  observations (no photos are analyzed); investment hypothesis + one easy question; 50–110 words; varied openings; no
  flat 70%. Injected verbatim into every first-touch and revise prompt (`FIRST_TOUCH_SCHEMA` has the analysis before the
  email; the analysis is stored in `outreach.analysis` and shown on the Asana card). `BANNED_RE` in compose.py rejects
  §27 phrases, listing echo and fake observations. Fallback templates are angle-based (`angle_for`). `titan.cli
  rewrite-pending` regenerates To Review cards after a protocol change.
- **Voice protocol = `docs/style/VOICE_PROTOCOL.md`** (Rodrigo, 2026-10-24): junior acquisitions agent for a real buying
  company. "I" only for what Charles personally did/will do; "we" / "our side" / "what we look for" for criteria, risk,
  pricing philosophy; never powerless ("ask my boss"), never overcommitting ("we can definitely pay"), not a corporate
  robot, don't overuse "the team". Injected into first-touch and revise prompts; `PERSONAL_RE` rejects the listed
  personal framings; canned replies rewritten to it.
- **Conversation first (Rodrigo, Asana 2026-10-24) — overrides any older offer-percentage rule.** The first message opens a
  conversation (from **Charles**, property-specific, one or two verified details, asks about seller flexibility). It never
  contains a price, percentage, range or "cash offer". Replies: one question at a time, never re-ask; "send an offer" and any
  seller number get a holding reply only and go to Rodrigo as a review summary (`titan/review.py`, `reports/review/`,
  `titan.cli review-queue`). Signals kept apart: RESPONDED < DISCUSSING < FLEXIBLE. A number reaches an agent only through
  `titan.cli approve-offer <property_id> <price>` (Rodrigo decides per house; creates a draft, never sends).
  The ZIP-ratio MAO/range is an INTERNAL screening tool only (ranking + review summary, labeled as such).
- **Voice/sender:** outreach is written and signed by **Charles** (Rodrigo's assistant) from Charles@; the style guide and
  Rodrigo's example emails are tone references only. Charles Grant, Junior Acquisitions Agent.
- **Terms:** as-is cash, 14-day close, $5,000 EMD, **always keep a 7-day inspection period** (never promise to waive it).
  **Written offers (Rodrigo, 2026-10-26, Lise):** 30-day close ("longer if needed" when not vacant), 9-day inspection, $5k deposit;
  offer emails thank briefly (no fluff), list the terms, then a human paragraph that owns a below-ask number and explains it
  (resale range, cost of the work). Unrepresented: listing agent can take 4%, seller saves 2%.
  **The Lise offer (card 1219900327903853, 2026-10-26) is the model for every written offer** (Rodrigo: "a great starting point"):
  personable, brief thanks, Charles cc's Rodrigo as acquisitions manager who approved the numbers, terms as a short list, human
  paragraph owning the number, and **no contract attached** (agents don't care; offer by email first).
- **Reply-to:** Charles@titanrealtyinvestments.com. Postal address: 13075 Marcella Blvd, Loxahatchee, FL.
- Texting waits on Twilio A2P 10DLC approval. **Channel = EMAIL_FIRST** until then (Rodrigo, Asana 2026-10-23).
- **Sender mailbox = Charles@titanrealtyinvestments.com** (`GMAIL_SENDER`). Signature is exactly `operator.signature_lines`:
  Charles Grant / Junior Acquisitions Agent / Titan Realty Investments / Office Line: +1 (561) 405 7379. **No opt-out footer**
  (Rodrigo, 2026-10-24: it reads as marketing). "Remove me"/stop replies are still caught and honored (do-not-contact).
- **Communication runs on the paid API (Rodrigo, 2026-10-27: "let's have it run on the API, that's not a problem")**:
  `scoring.communication_provider: api` → card rewrites, card chat answers, reply drafts, first emails and reply reading go
  straight to the Anthropic API (key in the Mac's .env), never through the Mac's Claude login, which broke again and again
  (token revoked / keychain locked). Pennies per card. Scoring stays on the plan (volume). `paid_fallback_when_login_fails: true`
  is the backstop if communication is ever switched back to claude_code.
- **Scoring runs on the Claude Code plan** (`scoring.provider: claude_code`): `claude -p` on the Mac, 20 listings per call,
  API key stripped from the child env so the plan is billed; plan limit → rest by rules, Claude next run. Paid API only with
  `scoring.allow_paid_api: true`. Check with `make score-test`.
- **Charles's Gmail is connected on the Mac** (Rodrigo, 2026-10-24) — don't ask again. **Never send from any other mailbox**
  (Rodrigo, 2026-10-26, after Lise got a reply from rodrigo@ signed Charles): `GmailClient` reads the logged-in profile and refuses
  to start unless it equals `GMAIL_SENDER`. If the token on the Mac is re-authorised as the wrong account, every send stops until
  `rm secrets/gmail_token.json && make gmail-auth` as Charles. Re-authed as Charles 2026-10-26 10:15am (`make gmail-test` OK).
  Google project erudite-cyclist-495716-p1 (display name GoogleData) is **Internal**, so the login does not expire every 7 days;
  never click "Make external".
- **Scoring in cloud sessions:** no Anthropic key needed. The engine scores with the rules fallback, then Claude (this session)
  reads the remarks and confirms or corrects the scores. Gmail drafts can be made through the session's Gmail connector.
- **Asana board "Titan Lead Engine — Claude Updates"** (project gid 1219837397052431; sections Updates 1219837472466797,
  Needs Rodrigo 1219837590904714, Approved 1219837472466733, Done 1219837472466861). Post what you finished to Updates,
  questions to Needs Rodrigo; on every round read Approved (description + comments), act, then move the card to Done.
- **Focus (2026-10-23): the free scraper (HomeHarvest, runs on the Mac).** The MLS hot sheet is parked (price cuts only).
- **Offer ratio = what investors actually paid in that ZIP** (Rodrigo, 2026-10-23), not a flat 70%:
  `data/market_ratios.csv` from github.com/titanbuilds1995/titan-whatsapp-sales (deed-record flips; refresh from there).
  Ladder ZIP+band → ZIP → city → county; Palm Beach borrows Broward's county ratio; flat 0.70 only when nothing applies.
  Code: titan/market_ratios.py, valuation.buy_ratio. ZIP resale $/sf is the ARV fallback when comps are thin.
- **Never pursue REO / bank-owned or short sales** (Rodrigo, 2026-10-24): `market.exclude_flags` in config; filtered at ingest,
  and `exclude_flagged()` marks already-stored ones EXCLUDED (also `not_a_home`: boat slips, parking, storage, timeshares, lot rent) (no score, valuation or outreach) on every ingest/score run.
  Negations ("not a short sale or foreclosure") don't count; pre-foreclosure is NOT excluded.
- **Never pursue listings that rule out assignment or an inspection period** (Rodrigo, 2026-10-25): flags `no_assignment`
  ("no assignments", "not assignable", "no wholesalers") and `no_inspection` ("0 day inspection", "inspection waived") are in
  `market.exclude_flags`. Agent preference (not built yet, 2026-10-25 journal): rank big-brokerage and very active agents down,
  the small agent with one or two listings up; few/no interior photos is a "call now" signal; MAO on the card with repair
  credits for stated new roof/AC; email in the morning, text later the same day once texting is live.
- **Data guards (2026-10-25):** a bounce (mailer-daemon, "inbox full") is classified BOUNCE, never a counter; the agent's email is
  marked `bounced`, the lead goes NEEDS_CALL. ARV above `valuation.max_arv_to_list` (1.75×) the ask is `*_suspect`: never HOT,
  never FRESH_DEAL, never "close to asking". Flat-fee / institutional domains (opendoor, beycome, flatfee…) are skipped like
  generic mailboxes. The report's data block is base64 (`TITAN-DATA-B64`) because Gmail wrapped the JSON.
- **Targeting (Rodrigo, 2026-10-24):** scrape every active listing (free; `ingest.homeharvest.scope: all_active`, $50k price
  bands), then `titan/targeting.py` lanes: **SITTING** (60+ days; ideal = with price drops / back on market) and
  **FRESH_DEAL** (≤14 days and list ≤ internal high +5% on real comps; lock it in fast); the rest WATCH. Ranking, Claude
  scoring order (cap `scoring.max_claude_calls_per_run`) and outreach order follow the lanes. Outreach stays focused on HOT.
- **Trust ladder (Rodrigo, 2026-10-26)**: keep editing until he approves most emails as written, then first emails go out on
  their own, then replies, then ranges; step by step into a knowledge base. `titan/learning.py`: every card decision is
  recorded (`card_decisions`: approved as written / after his comments or hand edits / skipped; autopilot sends are not votes),
  every comment is a lesson (`lessons`), his offer numbers vs the internal range (`price_decisions`). `reports/knowledge/learned.md`
  (rebuilt every approvals tick) goes into every first-touch and revise prompt. Gates in `autonomy:` (config): stage 2 at 80% as
  written over 30 cards → engine posts ONE "⚙ Autopilot · first emails" card in To Review; **dragging it to Approved is his
  go-ahead**, dragging it out turns it off, the engine turns it off under 65%. With it on, a Claude-written first email nobody
  touched for 3 h moves itself to Approved. Stage 3: **Charles drafts his own answer** to every agent waiting on us (`titan/replies/writer.py`, reply card in To Review, no
  number ever, holding answer when they ask for an offer); Rodrigo edits/approves like first emails and that is what stage 3 measures.
  Stage 4 (ranges) is measured only. Nothing replies or quotes on its own yet.
  `titan.cli ladder` prints where each stage stands; the morning report has a Trust ladder section.
- **Lead board (Rodrigo, 2026-10-26)**: the Sent column is renamed "Sent · no answer yet" (the unanswered list); cards whose agent
  really answered (not bounces or auto-replies) move to a new "Answered" column, each with a WHERE WE STAND block at the top
  (status: NEEDS YOU / OUR TURN / ANSWER DRAFTED / WAITING ON THE AGENT / CLOSED, their last words, counts). `conversations.lead_board`,
  runs with check-replies and approvals. **The drafted reply is shown in that block too** ("DRAFTED ANSWER (not sent yet)", card
  name, column, full text; Rodrigo 2026-10-26: he looks at the lead's own card). Approval still happens by dragging the reply card.
  **The status moves with the draft**: a pending reply card with a dollar figure → "OFFER DRAFTED ($N)" (in To Review) or "OFFER
  APPROVED ($N)" (in Approved). **"Needs Me" column (Rodrigo, 2026-10-27):** answered leads whose next move is his (NEEDS YOU /
  OUR TURN / ANSWER DRAFTED / OFFER DRAFTED) and cards with numbers posted sit in Needs Me; Answered = we replied, ball with the
  agent. **Cc:** a card may carry a `Cc:` line above the email; it is sent as Cc (offers Cc Rodrigo).
- **Per-lead rundowns on Asana are formatted, not walls of text** (Rodrigo, 2026-10-26): when he asks about a lead, put it on the
  Claude Updates card as `html_notes` with `<h1>` address, `<h2>` sections (Status / The house / Our offer / The numbers / Engine
  screen / Conversation so far), `<table>` for figures (header row in `<strong>`), `<hr/>` between sections. Asana html allows only
  body, strong, em, u, s, code, ol, ul, li, a, blockquote, pre, h1, h2, hr, img, table, tr, td: **no `<p>`, `<br>`, `<th>`** (400 XML
  invalid). Comments allow no h1/h2/table/hr: use `<strong>` headings + `<ul>` lists and pin the snapshot. Approval-card `notes` stay
  plain text (the engine parses the EMAIL markers). **Always also pin the snapshot as a comment on the lead's OWN card on the
  Email Approvals board** (e.g. in Answered): that is where Rodrigo looks; a separate card alone was missed (Lise, 2026-10-26).
- **New listings PAUSED for the agent-email strategy (Rodrigo, 2026-10-26):** "let me finish with what we have": `ingest.paused: true`
  in config.yaml; run-daily skips the scrape, everything else (score, value, enrich, outreach, approvals, replies) keeps working the
  stored listings. Turn back on only when Rodrigo says. **Offer log** (`titan/offers.py`, table `offers`): every sent email with an
  offer figure (reply card or approve-offer) is logged on the next approvals tick; status SENT/COUNTERED/ACCEPTED/UNDER_CONTRACT/
  CLOSED/REJECTED/DEAD via `titan.cli offers <id> <STATUS> --note`; market stats (count, % of list, by city) in the morning report
  Scoreboard and dashboard stats; `titan.cli offers` writes `reports/offers.csv`. Rodrigo copies offers onto his main results board himself.
- **The photos already show the work (Rodrigo, 2026-10-27):** a listing with 10 or 15 pictures makes the condition obvious, so an
  email that says "we just need to know how much work it needs" (or implies it: "how deep is the project", "mostly cosmetic or
  something bigger", "what's your read on the condition", "I can't tell from the listing") sounds dumb. Rule: with **4 or more
  photos** (or an unknown count) never ask or imply it; ask about what pictures can't show (roof/AC age, plumbing, electrical,
  permits, occupancy, why it's sitting, flexibility). Only with **fewer than 4 photos** may the email say the listing doesn't give
  much to go on and ask about the work. Code: `WORK_RE` / `FEW_PHOTOS_RE` / `photo_rule_problems()` in compose.py (first emails,
  rewrites, replies), `may_ask_how_much_work` in every prompt, protocol §0c, `few_photos` template angle; every approvals tick
  rewrites up to 15 waiting cards that still break it (`rewrite_pending(violating_only=True)`). Photo counts come from the scraper
  (`photos_count`, shown on the card's listing line).
- **One card, one place (Rodrigo, 2026-10-27, Richard Dipietro: "we are still creating a second card when I ask a question …
  it's confusing"):** whatever he asks on a card is answered ON THAT CARD. A numbers ask posts the NUMBERS snapshot as a comment
  there (no NUMBERS card on Claude Updates any more, `numbers_snapshot`); a question gets its 💬 answer there; a comment on a
  lead card (Sent / Answered / Needs Me / Needs Revision / To Review / Approved) is handled by `approvals.lead_card_comment`, and
  only what changes the answer to the agent reaches the reply card: a dictated answer (becomes the reply card), a plain "send
  it" (sends it), a wording change (forwarded as "From your comment on the lead card: …", rewritten there, held if it sat in
  Approved). The engine creates no card for a question; the only cards it still creates are the reply card (one per agent
  answer, completed when sent), the 📞 call card when he asks to call, and the daily alarm cards.
- **Fresh Leads vs Needs Me (Rodrigo, 2026-10-27: "once you finish something and you send it to me, it never goes into To
  Review, it goes Needs Me. To Review is only properties that I've not touched yet, and they're fresh"):** the To Review column is
  renamed **Fresh Leads** (same gid 1219847260413153, config `to_review`; `ensure_fresh_leads_name` renames it once). Fresh Leads =
  new first emails nobody has touched (the engine's own photo-rule rewrites of untouched cards stay there). **Anything worked on
  after he touched it goes to Needs Me**, never back to Fresh Leads: a rewrite from his comment, a hold or a question answered
  (card moves out of Fresh Leads), numbers, a call card, a dictated answer's reply card, every engine-drafted reply card
  (`post_reply_card` default), the autopilot go-ahead card. Cloud sessions doing card work by hand: same rule.
- **Comps (Rodrigo, 2026-10-28: "the comps are just incorrect"; he starts his offers from these numbers):** `titan/comps_engine.py` is the
  one place comps are picked, for the engine's valuation AND the numbers snapshot. Same PRODUCT only (a townhouse in an HOA never comps
  against detached pool houses, a duplex is not a house), no waterfront / new-build sales unless we are one, $/sf outliers out, beds /
  year built / lot matched when known, similarity-ranked, renovated sales set the exit, split layouts / unpermitted work / impossible bed
  counts discount the ARV with the reason shown. Every valuation has a **comps grade A–D** (`comp_grade`; on the card's listing line and
  in the snapshot's "Comp check"): A = build on it, D = a guess. **Daily loop** `titan/comps_loop.py` (run-daily step `comps`, by hand
  `make comps-loop`, measure-only `make comps-check`): back-tests the rules on real sales (every sold home in a ZIP pool valued from the
  others), tunes radius / size / year / beds and keeps a setting only on 150+ sales with a clear gain (`data/comps_tuning.json`, read by
  the engine), re-values every house on the board + HOT (an ARV move of 10%+ gets one comment on the lead card), and **Claude reviews
  the comp sets behind the houses he is working** (`comps.loop.claude_reviews_per_day` 15, paid API like the other card work; its
  exclusions feed the valuation; his comp comments from the cards are in its prompt). Morning report section "Comps (how good the
  numbers are)". Comps window = last 12 months (his rule) for both paths.
- **Direct and matter of fact (Rodrigo, 2026-10-28: "stop saying the house doesn't scare us, that sounds dumb, go straight into
  it ... stop with 'new electrical panel' ... sound more serious, it sounds very AI-ish"):** after the intro line go straight to
  the question (one short sentence of context at most). Never tell the agent what doesn't scare / bother / worry us or what is
  "fine on our side"; never recite the listing's upgrades back; never mention the electrical panel; no "curious", "honestly",
  "potentially", "the bigger question is". 40–90 words. Code: `DIRECT_RE` / `tone_problems()` in compose.py (first emails,
  rewrites, replies), protocol §0d, voice protocol examples rewritten, templates rewritten; every approvals tick rewrites up to
  15 waiting cards that still break it (same path as the photo rule).
- **End-of-day report (Rodrigo, 2026-10-29)**: every evening an email (house style, `titan/end_of_day.py`) with the day's
  activity: who he moved off Fresh Leads (from Asana's own move history, so nothing is missed), cards he deleted (`board_cards`
  snapshot every check-replies / approvals run; a vanished card that answers 404 = deleted), calls (Aircall isn't connected:
  a card taken off Fresh Leads into a working column with no comment = "probably called"; a comment saying called / spoke /
  voicemail = "called"), emails out, replies in, numbers posted, offers, his comments, the board per column, and **the goal:
  25 leads a day emailed or talked to** (distinct agents; eliminated don't count; `report.end_of_day.daily_goal`), with the last
  7 days. Sent by the first check-replies run after 7pm ET (`report.end_of_day.hour_et`; next morning catches up if the Mac
  slept); `make eod-report` sends one now (doesn't block the 7pm one).
- **Titan Warm Leads board** (Rodrigo, 2026-10-28; project 1219996826543058, columns Needs Me / Needs Revision / Waiting on the
  agent / Offer out / Closed, gids in `outreach.asana.warm_leads`): answered leads he drags off the approvals board. The engine
  reads it like the lead columns (`titan/warm_leads.py`): comments handled, WHERE WE STAND kept, moves stay on the warm board
  (Needs Me ↔ Waiting on the agent; Offer out when an offer is logged and never pulled back out; Closed). Reply cards and
  sending stay on the approvals board. Moving answered leads onto it automatically is NOT built (he moves them himself).
- **Second touch = "Good morning" follow-up (Rodrigo, 2026-11-02: "send a good morning email to all of the agents that never
  responded back to us")**: `titan/follow_up.py`, `titan.cli follow-ups` / `make follow-ups`. For every first email sent 3+ days ago
  (`outreach.follow_up.after_days`) whose agent never really answered (bounces / auto-replies don't count), nothing sent since, not
  bounced, not do-not-contact, no answer card already waiting, not parked by Rodrigo ("too fresh, wait 45 days" in lead_notes): one
  reply card "Follow-up · Agent · street" in Needs Me, same Gmail thread (`(reply to outreach #N)`), opens "Good morning <name>," then
  one line that we emailed about the house and one easy question; 25–60 words, no price, `validate_follow_up`. Claude writes it
  (paid API like other card work), template fallback. One follow-up per first email (`follow_ups` table). `--approve` puts them
  straight into Approved, only when Rodrigo said in words to send them; `--list` prints who would get one. Not run on its own yet
  (no cron / tick); Rodrigo asks for it, or say when he wants it automatic.
- **Needs Revision holds only commented cards (Rodrigo, 2026-11-02: "if it doesn't have a comment, it needs to get out of Needs
  Revision … clean Needs Revision into only things that I sent out fresh")**: a card dragged there with no comment goes straight
  back to Needs Me at the next round, text untouched, with a one-line comment (`back_to_needs_me` in `sync_feedback`); no prompt,
  no wait. Commented cards still park there until the rewrite lands (or "couldn't rewrite" / "Held"). **Send pace: `per_tick` 5**
  (was 2; "why are those people still on my approved board"), still inside the window and the daily cap.
- **Clean cards (Rodrigo, 2026-11-02: "very unorganized … where we stand looks very convoluted … delete things that are old … no need
  to paste all of their footer")**: (1) answered lead cards get a FORMATTED description (`conversations.stand_html`, `update_html_notes`):
  WHERE WE STAND as a short list (Status / Last from the agent / Thread), the drafted answer in a quote, then Listing, then the first
  email; the pre-send "Why this house" analysis and the how-to footer are dropped once the lead is in conversation (Asana renders
  html_notes to plain notes with the lines kept, so `parse_card` still works; `card_parts` reads any form back). (2) Mirrored replies
  show the agent's words only (`titan/replies/clean.py`: quoted history, greeting, closing, name/phone/license/link signature
  stripped; also in "Last from" and in Claude's reply prompts) plus one "Read as: … what happens next" line; no listing / MAO /
  Gmail-draft lines. (3) Comment tidy (`titan/tidy.py`, runs inside `pending_feedback` on every card whose comments are read): older
  "Rewritten …", "Comps re-checked", "Revised" copies and transient "Holding it / Held / Moved to Needs Me / couldn't rewrite" notes
  are deleted (never Rodrigo's comments, never 📩/📤/NUMBERS/💬, never the engine's last comment; 4 per card per round). (4) The
  comps loop comments only on houses with an engaged lead status and replaces its previous comment. (5) **A flat "no" needs no one
  (Rodrigo, 2026-11-02, Edward Martinez: "I don't even need to see the lead")**: NOT_INTERESTED / SOLD_OR_PENDING / STOP → the lead
  card and any reply card waiting for that agent go to Skip (warm-board cards to its Closed column) with one comment; `NOT_RE` learned
  "No they are not", "firm", "not at this time", "won't come down"; `heal_misreads` re-files such replies stored as INTERESTED.
- **ONE CARD PER LEAD, the answer lives on the lead card (Rodrigo, 2026-11-03, Thais Guevara: "I don't want other cards … I said
  the email that I wanted and then I approved it … it's giving it back to me and saying that it needs me instead of drafting the email
  and sending it")**: no more "Reply ·" / "Follow-up ·" cards. Charles's next email to an agent (engine draft, follow-up, offer, or
  Rodrigo's dictation) is a **NEXT EMAIL block on the lead card itself** (To / Subject: Re: … / EMAIL markers, under WHERE WE STAND;
  the first email stays below as history), kept in `outreach.answer_draft` (`titan/answers.py`: `set_answer`, `pending`, `clear`).
  **Dragging the lead card to Approved sends it** in the agent's Gmail thread; the card gets a 📤 Sent comment, the block clears and
  the card goes back to Sent · no answer yet. On a lead card his comment does: the email in his words ("Answer with: Hey …", or the
  text as one comment + "put that into an email and send it" as the next) → Claude tidies the dictation (punctuation only, his
  words kept) → NEXT EMAIL → straight to Approved when he said send (or the card already sat in Approved); "send it" → Approved;
  "hold"/"wait" → Needs Me; a change ("ask her about the roof") → the NEXT EMAIL is rewritten, or written from the conversation
  when none is drafted yet (`replies.writer.write_answer`, `rodrigo_instruction`); numbers / questions as before, on that card.
  A lead card in Approved with no answer on it is sent to Needs Me with a comment (never silently). Text he types into the EMAIL
  lines himself counts as the answer (`hand_written`). Old reply cards still on the board are folded into their lead card and
  completed (`fold_reply_cards`, every approvals round); one already in Approved sends as it is. Warm-board cards: "send it" sends
  from there (`answer_state: approved`), the card stays on the warm board.
- **Commercial screen for the 1031 buyer (Rodrigo, 2026-11-08; `titan/commercial/`, config `commercial:`):** LoopNet only, through
  Apify (`memo23/loopnet-scraper-ppe`). Box $3.5M–$8M, cap floor 8% (a stated cap under 8 is never in the box; a missing cap is
  NEEDS-OM), sweet spot to $6M (above = "reach"), all of Florida with non-South-Florida scored a little higher, asset types in:
  NNN / retail / hotel / industrial / office / multifamily 5+ / medical; land, development and business-only out. Every listing: score
  0–100, back-solved cap (NOI ÷ price, 50 bps off = flag), OFFER ANCHOR (stated NOI ÷ 8%, never above the ask), cooked-books tag
  CLEAN / SUSPECT / NEEDS-OM, EVIDENCE GRADE A–D as its own column (Top 25 sorts grade first, then score). Outputs in
  `reports/commercial/<date>/` (master.csv/.xlsx, top25_1031.pdf Titan-branded CLEAN only by region, broker_contacts.csv = Monday's
  book-request list deduped by email with the documents missing per listing, needs_om.csv, diff.md). Public OM/brochure PDFs go to
  `data/listings/<id>/` and are read; gated docs are recorded, never requested. Asana board "Titan 1031 · Commercial (LoopNet)",
  cards tagged `1031-buyer`, Needs OM cards carry the OM-request email in Charles's voice; **nothing on that board sends**.
  `make commercial-sample` (pennies), `make commercial` (full pull, costs Apify credit), `commercial.schedule: daily` for the morning
  re-run + diff. Known gap: LoopNet blocks the actor's full property sheets most of the time (0–1 of 3 come through); the engine
  retries them every run and keeps any sheet it ever got. Do not try proxies / crawlers around LoopNet's block from a cloud session.
- **1031 deals are VISUAL (Rodrigo, 2026-11-08: "I'm a visual guy … I want to send them the properties in an email with everything
  laid out … Asana is the CRM, the dashboard is where the investor looks"):** (1) after every pull a Titan-style HTML email
  (`titan/commercial/email_html.py`, one LoopNet photo card per in-box listing: facts, "why this fits" line from `why.py`, link to the
  property page) goes to REPORT_TO; it is the email he forwards to the investor, so no internal numbers in it. (2) The site: a "1031
  Deals" page on the Vercel dashboard (`dashboard/public/1031.html`, data `titan/deals.json` in the Blob store from
  `titan/commercial/site.py`, served by `dashboard/api/deals.js`); private view = dashboard password (everything, broker contact,
  internal block), investor view = `?k=DEALS_SHARE_KEY` showing only the investor columns and no internal numbers; gallery per column,
  a page per property (`#<listing_id>`). (3) The Asana 1031 board columns ARE the categories (`titan/commercial/board.py`): New ·
  Good fit · Not a fit · In touch with the agent · Books received · Offer out · Closed; every new card lands in New, Rodrigo drags, the
  engine reads the column onto the listing (`board_column`) and the site/email follow it. (4) LoopNet sheets: never route around the
  block from the cloud; `make commercial-sheets` lists the top listings still missing a sheet + the prompt for a Claude Code session on
  the Mac with Claude in Chrome to read them in HIS browser into `data/listings/<id>/sheet.txt` (read on the next run like OM PDFs).
  Needs on Vercel + .env: `DEALS_SHARE_KEY`; config `commercial.site.url` = the dashboard URL (blank → email links go to LoopNet).
- **OM requests out, books back (Rodrigo, 2026-11-09: "the most important thing is seeing who communicates the fastest and can send us
  the documents ASAP … cut the fat out"; `titan/commercial/outreach.py`):** the 1031 board has a column **"Approved · send OM request"**;
  a card dragged there is emailed by Charles@ at the next approvals round (window, daily cap, `per_tick`; never twice for one listing;
  logged in `outreach` as kind `om_request` and in `commercial_threads`), then the card moves to In touch with the agent with a 📤
  comment. Every check-replies and approvals round opens those Gmail threads: a real answer is mirrored on the card (📩 who, when,
  **hours to reply** = the response clock, also on the site's private block and the deals email), every attachment is filed in
  `data/listings/<id>/`, and when books arrive (OM / T-12 / rent roll / P&L / a spreadsheet) the listing is regraded on the spot
  (evidence grade, CLEAN/SUSPECT, offer anchor off the real NOI; comment "Regraded with the books") and the card moves to **Books
  received**. Bounces are marked, never counted as answers; out-of-office is ignored. The residential reply engine never touches
  these threads (`commercial_threads` / `commercial_messages`, not `threads`). `make commercial-round` runs one round by hand.
  **Charles speaks as the principal (Rodrigo, 2026-11-09: "we have to make sure that we do not say that we have a buyer … a partner of ours …
  sound like we are the principal")**: every OM request says "We are placing a 1031 exchange with a partner of ours and the clock is running",
  never "a buyer we work with" / client / investor (`validate_om_request` rejects those words). Cap-stated listings get "we need the numbers
  behind the cap rate to verify it holds up as a real eight plus cap"; no-cap listings get the eight-cap question. **No proof-of-funds offer
  any more** (Rodrigo, 2026-11-10: no commitments).
- **The no-cap blast (Rodrigo, 2026-11-09: "the rest of the agents that do not show a cap rate, I want us to email them asking them if it fits an
  8 cap, once sold and re assessed … send those emails out also today"; `titan/commercial/blast.py`, `make commercial-blast`):** one email per
  broker naming all of their in-box listings without a cap rate (`cap_question`: "does it hold an eight cap or better at asking once it sells and
  the taxes are reassessed? If it does, we want to take a look and we move fast"), queued in `data/commercial_queue/<date>.json` (gitignored: it
  holds broker emails and a listing snapshot per entry, so a Mac that never saw the pull can send and store them; the file travels by hand, never
  through GitHub). **No Asana card** for these until the broker answers: `poll_om_replies` then creates the card in the column the answer earns
  (`_card_for_thread`). **Commercial pace** (`commercial.outreach.per_round` 25 / `daily_cap` 450, Rodrigo: "shorten the time frame … but not too
  too fast"): every approvals round sends the "Approved · send OM request" cards first, then the queue, inside the send window; commercial sends
  are kind `om_request` and do not count against the residential `daily_email_cap`. `titan.cli commercial-blast --list` shows who is waiting.
  **Bootstrap from Apify (Rodrigo, 2026-11-09: "how do I add the leads in to there, can you do it")**: the Mac never saw the cloud's pull, so
  `commercial.bootstrap.datasets` lists that pull's 21 Apify dataset ids (reads are free); the first approvals round on a database with fewer than
  `min_listings` loads them, scores offline (no cards / email / site), builds the queue and records a `commercial_runs` row kind `bootstrap`
  (`blast.bootstrap`, stage `commercial_bootstrap`). Pace set to **8 per round / 220 a day** ("space it out between the next couple of days").
- **Every broker answer is READ for Rodrigo (Rodrigo, 2026-11-09: "I need everything to be read for me"; `titan/commercial/books_review.py`,
  `make books-review`):** after `poll_om_replies` mirrors an answer and files the attachments, every check-replies / approvals round reads the
  thread (broker's words + every file in `data/listings/<id>/`, PDFs AND spreadsheets, which were filed but never read before) and posts ONE
  "🧠 READ FOR YOU · <verdict>" comment on the 1031 card: what they said / what the books say / numbers (real NOI ÷ ask = real cap) / red flags /
  next step ("NEEDS YOU:" when it is his: NDA, proof of funds, a number) / Charles's reply when one is due. Verdicts FITS · LEANS FIT · CAN'T
  TELL YET · DOES NOT FIT · NOT A FIT. Paid API like other card work; rules-only read (marked) when Claude is unreachable, retried. Books on
  file are regraded here too and the card moves to Books received; "leased / sold / under contract" → Not a fit + thread CLOSED. **A plain
  "send it" comment under the review sends Charles's drafted reply** in the same Gmail thread (kind `om_reply`, not counted against the OM
  pace; 📤 comment). Documents behind a link (Dropbox/Drive) are listed, never fetched (NEEDS YOU). One review per inbound message
  (`commercial_reviews`); `commercial.review.per_round` 20. Nothing else sends.
- **Simple replies go on their own; what needs him lands in his inbox; what does not fit is archived (Rodrigo, 2026-11-09 evening:
  "has Charles actually answered any of the agents that were simple answers? … forward me the emails that need me … the ones that do
  not fit, archive them … agents sent us deals on pocket listings")**: `commercial.review.auto_reply: true` → Charles's drafted reply
  under a 🧠 READ FOR YOU that is NOT marked NEEDS YOU goes out at the next approvals / check-replies round on its own (the review says
  so; a "hold" / "wait" / "don't send" comment under it stops it; NEEDS YOU reviews still wait for "send it"). `triage_inbox`
  (`books_review.py`, runs after the sends): a review that needs him, a FITS / LEANS FIT, or a broker who pitched other / pocket /
  off-market deals (new review field `other_deals`, shown on the card as "Other deals they pitched") is FORWARDED from Charles@ to
  REPORT_TO ("FW [NEEDS YOU] <address> · <broker>", the read + the broker's words + their files attached, 📬 comment); NOT A FIT /
  DOES NOT FIT threads are ARCHIVED in Charles's inbox once Charles's pass note has gone (🗂 comment; the thread is still read by id,
  so a later answer is not lost). Once per review (`commercial_reviews.forwarded_at / archived_at`), retroactive on existing reviews.
- **Charles answers the 1031 brokers fast in Rodrigo's words; a thread Rodrigo writes in is his (Rodrigo, 2026-11-10: "if someone sends all of the
  information … answer them before I can get to it … anything but the books or questions, grab the conversation … answer politely to anyone
  that it's not a fit … plant the seed")**: three fixed replies (`books_review.template_kind` / `template_reply`), each once per thread, go out on
  their own at the next round (window, `auto_reply`): **books_ack** (books attached or linked: thanks, underwriting team on it, answer no later than
  24 hours; goes even when the read NEEDS YOU), **ask_books** (no books, no question, no NDA: "can you please send us the books", we analyze off the
  books), **pass** (NOT A FIT / DOES NOT FIT, nothing else pitched: won't work, always buying, pocket listings / distressed / apartments, capital
  ready, other partners looking). Questions, NDAs, proof of funds, pocket pitches still get Claude's draft. **Rodrigo's own email in an OM thread**
  (from REPORT_TO, e.g. reply-all after Charles cc's him) is mirrored on the card (✉️ You wrote …), a purchase-price figure moves the card to Offer
  out, and the thread becomes his (`commercial_threads.rodrigo_at`): nothing auto-sends there, never archived, the card does not jump columns.
- **Deal sheet per 1031 deal, inside and outside view (Rodrigo, 2026-11-10, Byronessa: "what numbers we are negotiating, what they are
  asking, where we land … internal: what we are getting it for and how it makes sense; outside: what works for them, the price we sell it at")**:
  `titan/commercial/deal_sheet.py`. **The numbers live on the 1031 Asana card, never in this repo (it is PUBLIC)**: any comment containing
  "DEAL SHEET" is read as `Key: value` lines (Asking / Our offer (status) / Their counter / Landed at / Our ceiling / Investor price / Investor
  cap / Investor NOI / Real NOI, plus repeatable Internal: / Found: / Investor: lines); later comments win, so a one-line update works.
  Rodrigo's own offer email in the thread fills Our offer and marks it sent. Read every approvals round for cards in Good fit / In touch /
  Books received / Offer out / Closed (stage `deal_sheets`, table `commercial_deals`); a change republishes the site. Private view: the full
  sheet (offer % of ask, spread). Investor view: our price, the cap and NOI at that price, the Investor lines; never the contract price,
  the asking price or the LoopNet link.
- **1031 Needs Me column (Rodrigo, 2026-11-10: "why are all of the emails that I am getting on the people that need me not on my board?")**:
  the 1031 board has a **Needs Me** column (`board.NEEDS_ME`, created by `ensure_board` next to the send column). Every review that is
  forwarded to him (NEEDS YOU, FITS / LEANS FIT, other deals pitched) moves its card there once (`commercial_reviews.parked_at`,
  retroactive on the latest review of every thread), never out of Offer out / Closed / Not a fit. It leaves Needs Me when his move is done:
  his 'send it' reply went, or he wrote the broker himself (back to In touch; a price → Offer out). Forwards, replies and archives are
  claimed before they happen (`_claim`), so overlapping check-replies / approvals rounds never email anyone twice (Luca and Cameron got
  two forwards each on 11-10).
- **The 1031 board talks back, like the email board (Rodrigo, 2026-11-10: "can I just put a message on the comments and it will read it
  when I put it in Approved, like the other board … have this whole board have conversations with us … if I send it to Needs Me, it can
  answer me back what it thinks")**: `titan/commercial/card_notes.py`, stages `om_notes` (before `om_requests`) and `needs_me_takes`.
  (1) A note telling Charles what to say (or the email itself) → Claude writes Charles's reply in the broker's thread and posts it on the
  card "✍️ From your note"; it goes out when the note ends with a plain "send it", when he drags the card to "Approved · send OM
  request" (for a card already in conversation that drag approves the waiting draft; nothing waiting = one comment), or on a later
  "send it". Stored as a review row (reply_kind note / note_send), sent by `send_approved_replies`. (2) A question on a card → "💬" answer
  from the listing, reads, deal sheet and thread. (3) A card in the 1031 Needs Me column → one "💬 What I think" per new development.
  DEAL SHEET comments are never notes. Paid API like other card work; Claude unreachable → retried next round, the note kept.
- **No dates, no commitments, no claims in anything Charles sends on his own (Rodrigo, 2026-11-10, after Charles auto-answered Adriana:
  "I did not approve that message … let's not have us answer or say anything that has dates involved, or committing us to do anything like
  sending anything signed or POF")**: the books-review prompt no longer feeds "identify within 45 days" / "proof of funds on request" (it
  did, and 21 of 25 replies on 11-09/10 carried them; two told a broker a signed NDA was attached when nothing was). `books_review.COMMIT_RE`
  / `commits_us()`: a draft with a date, timeframe, weekday, month, "identify", closing timing, proof of funds, signing, NDA, LOI, contract,
  deposit, "we will send / provide", or any claim of an attachment is marked NEEDS YOU and never goes out on its own (also re-checked at
  send time for drafts stored earlier); only his "send it" sends it. Broker replies also wait for the send window (8:30–5:30 ET).
  The three fixed replies are his own words and still go; books_ack keeps his "within 24 hours" (his wording, 11-10 morning).
- **1031 Needs Revision + what every read must give (Rodrigo, 2026-11-10: "a section that says needs revision this way I can chat with the
  agent there … give me the data on what our assumption is on where we need to be for a true 8 cap, then the pros and cons, what it thinks
  they're lying about, and where we agree")**: the 1031 board has a **Needs Revision** column (`board.NEEDS_REVISION`, up front). A card
  there with his comment is answered (💬) or rewritten (✍️) by `card_notes.handle_notes`, then moves to Needs Me; one dragged there with no
  comment goes straight back to Needs Me with one line (same as the email board). Every 🧠 READ FOR YOU and every 💬 What I think now gives:
  where it has to be for a TRUE 8 cap with the assumptions (NOI used, tax after a sale at ~85% of price x county millage, manager / reserve
  a passive owner carries, the price), pros, cons, what we don't believe (inflated / left out / untrue, and why), and what checks out.
- **1031 Reviewed column (Rodrigo, 2026-11-10: "once I answer the question on the chat I need to drag it somewhere … a card for everyone that
  I have answered already, and then once it's answered it can be dragged to Needs Me again")**: `board.REVIEWED`, next to Needs Revision. He
  answers on the card and drags it to Reviewed; `handle_notes` acts on his answer (💬 answer or ✍️ Charles's email), then moves the card back
  to Needs Me. A Reviewed card with nothing new on it stays there. Flow: Needs Me → (he answers) → Reviewed → (Claude acts) → Needs Me.
- **Financials dropped on a 1031 card are read (Rodrigo, 2026-11-10: "should I drop them on the Asana card … yes let's do that")**:
  `titan/commercial/card_files.py`, approvals-round stage `card_files` (after `om_replies`). Cards changed since the last look
  (`commercial_kv.card_files_seen`) are checked for attachments; each new PDF / Excel / CSV / Word / text file is downloaded once
  (`commercial_card_files`) into `data/listings/<listing_id>/`, the listing is regraded and a fresh 🧠 READ FOR YOU is posted with
  `review_thread(no_reply=True)`: a read of HIS files never drafts or sends anything to the broker. Images are ignored; a card with no
  LoopNet link gets one comment. Cards never emailed get the regrade line only. Asana client: `attachments()`, `download()` (pre-signed
  link, fetched without the token). **Screenshots too** (11-10, Julio's "Year-to-date.jpg"): a JPG / PNG is typed out by Claude
  (`llm.transcribe_image`, paid API, pennies) into "<kind> - <name> (from screenshot).txt" next to it, and that text is read. Files are
  read BEFORE notes in the round (stage `card_files` before `om_notes`), so a card with fresh files is never bounced out of Needs
  Revision; after the read a card he put in Needs Revision / Reviewed comes back to Needs Me. A read never moves a card out of a column
  he chose (Needs Me / Needs Revision / Reviewed / Offer out / Closed / Not a fit).
- **1031 Not a fit = pass email, then off the board; Working Deals board (Rodrigo, 2026-11-10: "everybody that we're 100% they're not a fit
  with, let's just put them at the end of my board and delete them … send them that last email … get them out … another board where I can
  add everybody that I'm in communication with and that I've already finished filtering … if they need me, it needs to be on Needs Me")**:
  (1) `titan/commercial/closeout.py` (stage `not_fit_cleared`, after `om_triage` in check-replies and approvals): a card in **Not a fit**
  (now always the LAST column), or one read NOT A FIT / DOES NOT FIT with nothing pitched and not NEEDS YOU in New / In touch / Books
  received, gets the polite pass (`template_reply("pass")`) unless Charles already declined in that thread (`DECLINE_RE`), the broker never
  answered, the thread is Rodrigo's, or a drafted pass is still waiting (never two emails); then the thread is archived and the card is
  marked **done** (leaves the board, kept in search; a later non-pass answer reopens it in Needs Me). His drag to Not a fit drops any other
  waiting draft. Inside the send window, `commercial.review.pass_per_round` 10. A card in a column he chose is never cleared.
  (2) **"Titan 1031 · Working Deals"** (project 1220397720099247, config `commercial.asana.working_project`; columns Needs Me · Needs
  Revision · Reviewed · Waiting on the broker · Books received · Offer out · Closed · Not a fit): he moves filtered conversations there
  himself. `titan/commercial/working.py` wraps the Asana client for every commercial stage: engine moves land on the same column of the
  card's own board ("In touch with the agent" = "Waiting on the broker"; New / send / Good fit have none, so the card stays), column reads
  include both boards, and a card on both boards is taken off the 1031 board. (3) A broker asking for a call (`CALL_RE`) makes the read
  NEEDS YOU, so the card goes to Needs Me.
- **1031 "In touch with the agent" = live conversations only (Rodrigo, 2026-11-10: "that tab is only if we're negotiating and there's
  something moving … if they're not a fit, put everybody into not a fit")**: new column **"Sent · no answer yet"** (`board.NO_ANSWER`, right
  before In touch; the engine creates it). An OM request lands there when sent; the broker's first real answer moves the card to In touch
  (books → Books received). Every poll, a card still in In touch whose broker never answered (no reply, no bounce, Rodrigo silent) is sorted
  into Sent · no answer yet (`outreach.park_unanswered`). A broker who said no 8 cap / not a cap-rate sale is a DOES NOT FIT read → `closeout`
  clears it to Not a fit (pass email where owed, then off the board). Nothing he dragged himself is touched.
- **1031 cards are readable (Rodrigo, 2026-11-11: "put the name of the agent and key things about the property as the card names …
  give it good headlines and make things bold")**: card name = `Agent · Asset · City · $5.0M · 8.3% cap` (`asana_push.card_name`; no score,
  no "OM request" prefix); description = `card_html`: bold headline (ASSET · address · LoopNet link), THE NUMBERS / DO WE BELIEVE IT? /
  THE PROPERTY / WHAT WE STILL NEED / BROKER as short bold-headed lists, score in words, then the OM REQUEST block (plain To / Subject /
  EMAIL-marker lines, "sent <date>" once it went) so `parse_om_card` and the LoopNet-link readers still work on Asana's plain rendering.
  Stage `card_format` (approvals round, before `card_files`) refreshes every older card on the 1031 and Working Deals boards once
  (`commercial_kv card_format:<gid>`, 40 a round), from the database or from the old card's own text (`listing_from_card`); a name Rodrigo
  typed himself (e.g. "Craig and Larry Walsh · …") is kept, only the description is formatted. One card per listing (`already_on_board`).
- **Charles answers a 1031 broker on his own ONCE per thread (Rodrigo, 2026-11-11, Cameron Cauley / NAI Pensacola: "we emailed him 3-4 times
  the same thing asking him something that he already gave us and then he said to give him a call and we asked for it again")**: after Charles's
  first reply in a thread, every further email waits for Rodrigo's "send it" (only the thanks-for-the-books and the polite pass, once each, may
  still go). Nothing goes on its own, not even a fixed reply, when the broker asks for a call, sounds annoyed ("not understanding", "again",
  "already sent"), or says he attached files that never reached the engine (then NEEDS YOU: check Charles's inbox; never tell him they did not
  come through, never ask him to resend). The fixed "can you please send us the books" never goes after Charles has written or after the broker
  said he attached something. Claude's read now sees what Charles already sent ("WHAT CHARLES ALREADY SENT") and must never re-ask anything the
  broker gave, answered or said he cannot provide. Checked when the read is written and again at send time (`books_review.hold_reasons`).
  Attachments Outlook names only in the part headers are now picked up (`gmail_client._attachment_parts`); OneDrive links count as doc links.
- **Broker answers that are not a reply are found too (Rodrigo, 2026-11-11, Christian Bennett / 772 Cortaro Dr: "we got the trailing twelve
  … but I do not see it on my board … seems like a glitch, let's fix it")**: `poll_om_replies` only opened each OM thread by its Gmail id, so a
  broker who sent a NEW email (new subject, a forward, an assistant's address) was never seen and the card was parked in "Sent · no answer yet".
  `titan/commercial/strays.py` (inside `poll_om_replies`, so every check-replies / approvals round): one sweep of Charles's inbox since the last
  look; a message from a broker's own address, or one that names a listing's street ("772 Cortaro") and is written to Charles from a non-automated
  sender (LoopNet / Crexi alerts are skipped), goes through the same handler as a reply (`outreach.take_message`): 📩 on the card marked "came in
  as a separate email", files filed, regraded, card to Books received / In touch, 🧠 read at the next review. Every message looked at is remembered
  (`commercial_stray_seen`), 150 per round (`commercial.strays.per_round`). `make om-strays` runs it now and prints who was missed
  (`ARGS=--redo` looks again from the first OM request). Charles's own answers still go in the original thread. Also fixed: two answers from one
  broker in the same round no longer wipe the first one's files / BOOKS state, and a plain note after the books no longer pulls the card back to In touch.
- **The 1031 reply check opens only conversations with new mail (2026-11-11, the Mac's log: Gmail "Quota exceeded … Units per minute per user")**:
  `poll_om_replies` used to open all ~400 OM threads every check-replies AND approvals round; Gmail cut it off and every thread after the cut
  was skipped that round (the likely reason answers and books sat unread). Now one search (`strays.new_refs`: inbound mail since the last look,
  minus what was handled) decides which threads to open; the same list feeds the stray sweep. A quota error stops the round, leaves the look-back
  date alone, and the unread ones are picked up next round. `util.retry` waits 20 / 40 / 60 s on a quota error instead of 2–6 s. A Gmail client
  without `search_refs` (tests) still opens every thread. The residential reply check (`replies/inbox.py`) still opens every thread: same change
  is worth making there if the quota errors continue.
- **The investor sees the PROCESS, not just the properties (Rodrigo, 2026-11-11: "sell him on the whole process … how many properties I
  scouted through so he didn't have to … what properties I skipped … tenants that were not reliable or grade C, food trucks, month-to-month
  … 40 minutes or closer to the nearest airport … owner operators … the process of elimination")**: `titan/commercial/process.py` builds a
  `process` block into deals.json (`site.build_deals`), counts and reasons only (never a price, address or broker), so it stays in the
  investor view. Tab **"The process"** on the 1031 page (`#process`, both views): headline "We went through N listings so you didn't have
  to", tiles, **What we looked for** (criteria written from `commercial.buyer_box`; `commercial.process.criteria` = his own wording,
  `commercial.process.note` = one paragraph), **The process of elimination** (one row per box rule from `box_note`, how many it took out,
  what was left), **Then we talked to the brokers** (book requests, answered, within an hour, median hours, books, offers) and **Why we
  dropped them after talking** (NOT A FIT / DOES NOT FIT reads bucketed by `DROP_REASONS` from the read + the broker's words: gone /
  tenants (month-to-month, food trucks, class C, local credit) / owner-operator / won't hold the cap / outside the box / won't show the
  books / other). The gallery head links to it. Published with every site publish (Mac); the Mac's database holds the conversations.
- **ALL SENDING PAUSED (Rodrigo, 2026-11-11 ~4pm ET: "pause everything right now, no more email sending out … we're sending out
  way too many, I'm a little overwhelmed")**: `outreach.sending_paused: true` in config.yaml. `approvals.in_window` answers "closed"
  while it is on, so every sender waits exactly as it does outside the send window: approved cards (houses), OM requests, the no-cap
  blast queue, Charles's 1031 replies (even after his "send it"), pass emails. Cards and drafts stay where they are. Reports and
  forwards to Rodrigo himself still go. **Turn it off only when Rodrigo says so, and before that tell him what is queued** (blast
  queue, approved cards, drafted auto-replies all go at the normal pace the moment it is off). The one exception: the **delay note**
  (`titan/commercial/delay_note.py`, approvals stage `delay_notes`, `commercial.delay_note`): one "sorry for the slow reply, we
  have been analyzing a lot of properties, we are working through them and we will get back with you" in the broker's own thread, his
  wording, no date / promise / number, to each 1031 broker who really answered before the pause and heard nothing back. Never to a
  thread Rodrigo wrote in, a broker Charles already answered in any thread, a card on Working Deals or in Offer out / Closed / Not a
  fit / Reviewed / Needs Revision / Good fit, a not-a-fit read, a bounce; Gmail's thread is opened before each send and anything
  from our side after the broker's answer stops it. Once per broker (`commercial_kv delay_note:<email>`), 20 a round, logged as
  `om_reply` variant `delay_note`, so it counts as Charles's one answer in that thread. Residential agents get nothing (Rodrigo
  handles those himself).
- **Two agents on every answer (Rodrigo, 2026-11-12: "I want another agent to look at it and read the thread and test the logic …
  one answers, one reads the whole thing and reads the email we have and pushes back on the actual logic")**: `titan/second_read.py`,
  config `outreach.second_read` (`enabled`, `max_rounds` 2). Every answer Charles drafts is read by a SECOND Claude call (purpose
  `second_read`, paid API, one extra call per draft) that gets the whole conversation both ways, the property / listing facts, what is
  on file, Rodrigo's instruction, and the draft. It judges LOGIC only (does it answer what they said, re-ask what they gave / answered /
  refused, contradict us, assume a fact nobody stated, is an email the right move now); wording stays with the validators. Verdicts
  SEND · FIX (the writer gets the pushback, rewrites, it is read again) · HOLD (Rodrigo's move). Where: house replies
  (`replies.writer.write_checked`, used by `draft_reply_cards` and by answers written or rewritten from his lead-card comment), 1031
  broker replies (`books_review.second_read_reply`: Claude's draft is rewritten by `commercial_reply_rewrite`; a FIXED reply
  (books_ack / ask_books / pass) cannot be rewritten, so the reader only says whether it fits this thread), and emails written from his
  note on a 1031 card (`card_notes`). The card shows one "⚖️ Second read: …" line (agrees / agrees after N rewrites + what it pushed
  back on / still objects / says HOLD / not done). **Nothing the reader did not clear goes on its own**: 1031 reply → NEEDS YOU, waits
  for "send it" (a stopped fixed reply loses its auto kind); reader unreachable = not cleared. **His own "send it" still sends** (his
  email, his call): the objection is shown and he can pull it. His dictated words are read, never rewritten. Not covered: first emails
  and the "Good morning" follow-up (no conversation to check), the fixed delay note.
- **Nothing "attached" that isn't (Rodrigo, 2026-11-12, Eric Cederman: "we told them we would attach a signed document and we did
  not")**: Charles never attaches or signs anything. `titan/claims.py` `unbacked_claims()` finds "attached", "enclosed", "please
  find", "signed NDA / CA / copy", "I've signed", "sent it over" in our own words (quoted history and signature ignored). It is
  checked inside `GmailClient.send` for every email to an outside address with no file on it and raises `UnbackedClaim`: **nothing
  overrides it, not his "send it", not autopilot, not a fixed reply**. The house approvals send and the 1031 reply send check first:
  the card gets one "STOPPED, not sent" comment (house card → Needs Me), never retried as is. Every writer prompt (house replies,
  1031 reads, card notes) and the second reader say the same. Mail to our own domain (forwards with the real files) is not checked.
- **House cards look like the 1031 cards (Rodrigo, 2026-11-12: "add the same design that we have for the loopnet commercial properties
  into our MLS deals for single family homes so it's that organized")**: `titan/house_card.py`. Card name = `Agent · 3bd House · City ·
  $300k · 92 days · 2 cuts` (+ "⚡ fresh deal"); description = bold headline (HOUSE · street, city · Listing link), then THE NUMBERS
  (internal, never sent: asking, $/sf, ARV + basis or "looks too high", repairs, internal MAO and % of asking) / DO WE BELIEVE IT?
  (comps grade A–D + notes) / THE HOUSE / WHY THIS HOUSE (the engine's read) / AGENT, then FIRST EMAIL (plain To / Subject / EMAIL
  markers / "(outreach #N)", so parse_card and the sender work as before). Rewrites redraw the whole card (`rerender`); answered
  leads show the same sections under WHERE WE STAND (`lead_card_html` with props). Approvals stage `house_card_format` refreshes
  older cards once each, all in one round (`card_format` table): waiting cards keep the text on the card (his hand edits), sent cards show
  what went out, cards in conversation are only renamed (lead_board draws their description).
- **Fresh Leads is cleaned every morning (Rodrigo, 2026-11-12: "cleaning our board daily for leads that already went under contract
  or are no longer available that are on fresh leads")**: `titan/board_clean.py`, run-daily step `board_clean` (after ingest, 5:30am
  on the Mac; by hand `make clean-board`, `make clean-board-list` prints only). Every house on a Fresh Leads card is looked up on
  realtor.com (HomeHarvest single-address search, free, Mac only; the cloud is blocked). All houses on the card pending / contingent /
  under contract / sold / withdrawn / expired → card to Skip with one "Board clean-up" comment, its email `skipped` (never sends),
  the house EXCLUDED (no new email). One of two houses gone → card stays, one comment. A failed or unclear lookup changes nothing.
  These moves are not his votes (`learning.OFF_MARKET`, left out of the trust ladder). Config `outreach.board_clean`. Only Fresh
  Leads; Needs Me / Sent / Answered are not touched.
- **Daily learning loop for the house pipeline (Rodrigo, 2026-11-12: "run a daily loop on how we can make our MLS system better …
  from what we skipped to what we're sending offers to, the type of messages … constantly learn … improve on its own")**:
  `titan/learn_loop.py`, run-daily step `learn` (after comps, before outreach; 5:30am on the Mac), by hand `make learn-loop`,
  `make learn-check` prints what it knows. Every day it joins each first email to its house and to what happened (approved as
  written / changed / skipped / deleted, answered and how, offered on) and measures skip rate + reply rate per segment (lane, county,
  city, price band, type, days on market, price cut, photos, comps grade, ARV vs ask, brokerage, agent's listing count, writer, length).
  **On its own (bounded):** (1) `data/learned_prefs.json` (gitignored) re-orders outreach INSIDE each rating + lane tier toward what he
  works and what gets answers (`apply_to_candidates` in `candidate_rows`; never excludes, never changes HOT/WARM; a segment needs 8
  decisions, a weight moves 0.1 a day, max 0.5); (2) the **playbook** (`reports/knowledge/playbook.md`, table `playbook_rules`, max 12):
  Claude reads the week and writes up to 3 short rules for the writer, a SECOND Claude call checks each against the settled decisions
  (`NON_NEGOTIABLES`), only cleared rules are kept (reader unreachable = none); `rule_problem` rejects anything about money, dates,
  attachments or sending before any model sees it; the playbook goes into every writer prompt after his own comments (`learned_block`),
  which always win. **His call (never on its own):** proposals, one card each on Claude Updates → Needs Rodrigo ("🧠 Daily learning · …"),
  max 2 a day, never the same twice (`learn_proposals`): "stop drafting first emails for <segment>?" when he skipped 80%+ of 8+ cards
  (dragging that card to Approved applies it the next morning: `prefs.blocked`, card to Done), offers running 10%+ off the engine's
  range (a question only; a Claude session changes the range if he says yes), and Claude's own (notes). One scorecard a day
  (`learn_runs`: as-written %, skip %, reply %, offers) with the week-ago trend; morning report section "What the engine learned";
  detail in `reports/learning/loop_<date>.json`. Two paid API calls a day, only once there are 10 decisions or 10 judged emails. It
  never sends, never touches a price, a cap, the pause or the trust-ladder gates. Config `learning_loop:`. To undo a block: remove the
  segment from `blocked` in `data/learned_prefs.json` on the Mac.
- **The backup lane: pending houses (Rodrigo, 2026-11-12: "the goal here is to get things under contract … properties that match
  our criteria and are pending, maybe pending for a while, we should email the agents and tell them that if anything falls through we
  are interested and have a follow up sequence with those agents. Those are the hottest leads. When others have stopped reaching out
  and they are ripe")**: `titan/backup_lane.py`, run-daily step `backup` (after enrich, before outreach), `make backup-lane`,
  `make backup-list` (prints only), config `outreach.backup_lane`, table `backup_leads`. FIND: houses the engine rated HOT / WARM that
  are under contract: marked pending by the board clean-up or a feed, the agent told us in a reply ("under contract", not "sold"), or
  found by a realtor.com lookup of the HOT / WARM houses we never emailed (150 lookups a morning, Mac only, table `status_checks`; a
  house found sold / pulled is EXCLUDED on the way). A house in the lane has `lead_status` BACKUP (never gets the normal first email).
  EMAIL after it has been pending 7 days (`min_pending_days`; we only know the day WE first saw it pending): Charles, intro, "I saw your
  listing on X is under contract", if anything changes we would like a look, one easy question; never a price, never a guess about the
  other deal (`validate_backup`); outreach kind `backup_touch`, variant `backup:*` (autopilot never moves it), a card "⏳ PENDING ·
  backup · …" in Fresh Leads, max 10 a day, one per agent. SEQUENCE after it is sent: every 10 days while still pending and the agent
  silent, a "Good morning" check-in as the NEXT EMAIL on the same card in Needs Me, up to 3. WATCH every 2 days: sold → sequence stops
  (unsent card to Skip); **back on the market → 🔥 comment with the agent's phone, card to Needs Me, lead NEEDS_CALL** (a house never
  emailed goes back into the normal lane with back_on_market set); the agent answers → the normal reply engine has it. Every email in
  the lane is second-read (`second_read.run`); a HOLD never reaches the board. The learning loop's goal is now contracts, and it sees
  the lane's counts. Not built: pulling ALL pending listings county-wide (new listings are still paused; today the lane only knows houses
  already in the database), the real pending date, texting in the sequence.
- Full list of build-time judgment calls: `DECISIONS.md`. Open items that need Rodrigo: `BLOCKERS.md`.

## Still open (ask once, then record the answer here)
- (none)

## Current status
- 2026-11-14, RESUME HERE: Rodrigo wants BOTH (1) investor phones/emails from the FB/IG groups, kept running: prompt saved at
  `docs/prompts/facebook_investor_contacts.md` (Mac, Claude in Chrome, read-only; tri-county, any deal, tagged for reuse:
  reports/buyers/fb_investors.csv + fb_deals.csv), and
  (2) a deed-record cash buyer / flipper search for West Palm: WAIT for Rodrigo's instructions on how to run it before building.
  PBC appraiser, clerk and Sunbiz are blocked from the cloud (Mac only); emails need a paid skip trace (ask first).
- 2026-11-14: Rodrigo asked to pull other investors' emails from the Instagram/Facebook groups he is in (cash buyer list for Tortuga).
  The cloud cannot see them. Gave him a read-only Claude-in-Chrome prompt for the Mac (only contact info posted publicly for deals,
  human pace, CSV in reports/buyers/, no DMs); suggested deed-record cash buyers in 33461 as the stronger free source. Nothing built.
- 2026-11-14 ~7:25am ET: morning check only. Report on time. Board clean-up and backup lane run live (≈30 Fresh Leads cards to Skip
  Nov 13–14 with "Board clean-up" comments; 51 pending houses watched). **Claude unreachable on the Mac** (learning review skipped, new
  cards are templates): Needs Rodrigo card asks for `make doctor`. Hot list frozen since Nov 12 (ingest paused), learning loop is ranking
  "ARV over 1.5x" up (the inflated segment): worth capping. Sending still paused.
- 2026-11-14, RESUME HERE: Rodrigo wants to sell **82 Tortuga Rd** (contract $245k) to cash buyers through Facebook groups and asked to
  analyze the best cash-deal posts there. The cloud cannot see Facebook (groups need his login; Apify refused this session's token).
  Built a playbook + ready-to-paste post (artifact https://claude.ai/artifact/P1qSWmaoqrrU4jXvSuurVt: post at $269,900, ARV $400–420k,
  3 comps, flow post → DM → one showing → highest and best → assignment). Next: the real group analysis from his Mac with Claude in
  Chrome (prompt given in chat), then tune the post. Watch: inspection likely ends ~Nov 20; confirm "and/or assigns" is in the PSA.
- 2026-11-13 ~7:20am ET: morning check only. **No 7am report** (Mac quiet since ~5:46pm Nov 12). Gap seen: Martin Hoffman's comment
  ended "just ask that and send it" but the rewrite landed in Needs Me, not Approved (rewrite + send rule; sending is paused anyway).
  Chevelle Young (Warm Leads, Needs Revision) comps ask unanswered, waits on the Mac. Cards posted on Claude Updates.
- 2026-11-12 ~6:30pm ET, RESUME HERE: **backup lane for pending houses built** (see Decisions → The backup lane) on top of the
  learning loop below. First run on the Mac: tomorrow's 5:30am run-daily (`make backup-list` shows what it would do, `make backup-lane`
  runs it now). Tested on fixtures only (7 tests, 407 total); never run against realtor.com or the real board. Watch tomorrow: the report
  section "Backup lane", how many of the ~11k stored houses come back pending (150 lookups a day, HOT first), and that no "⏳ PENDING"
  card appears before a house has been in the lane 7 days (so the first backup cards land around Nov 20, except houses whose agent
  already told us "under contract" a week ago: those can card tomorrow). Sending is still paused, so approved backup emails wait too.
  Open for Rodrigo: 7 days pending / check-in every 10 days x3 are my defaults; pulling all pending listings (not only houses we already
  have) needs new listings switched back on for that pull.
- 2026-11-12 ~5:30pm ET, RESUME HERE: **daily learning loop built** (see Decisions → Daily learning loop). First run on the Mac is
  tomorrow's 5:30am run-daily (`make learn-loop` on the Mac runs it now). Tested on fixtures only (6 tests, 400 total): the cloud has
  no database, so the first real run is the test. Watch tomorrow's report section "What the engine learned": the scorecard line must
  show real counts (0 decisions = the join outreach → card_decisions is not matching on the Mac), "What it sees" must be Claude's
  words (missing = the `learn_review` call failed, check `logs/`), and at most 2 "🧠 Daily learning" cards in Needs Rodrigo. With
  sending and new listings both paused the loop has little fresh data: it learns mostly from the decisions already on file.
  Not covered yet: the 1031 board, call outcomes (no Aircall), offers' final outcome beyond the status Rodrigo logs.
- 2026-11-12 ~4:35pm ET: the Mac redrew ~100 old Fresh Leads cards at 4:26pm ET. Four oldest (outreach #5 Saidelys, #6 Eddy name,
  #10 Adrienne, #24 Eugene; the cards hand-edited in the cloud on Oct 26) were skipped by the Mac, likely because their DB status is
  not pending: redrawn by hand from the cloud from each card's own text (no ARV/MAO, the cloud has no DB); email blocks unchanged.
  Rodrigo was working the board at the same time (moved Eddy/Saidelys/Eugene to Approved; sending is paused, nothing goes).
- Older status entries: `docs/STATUS_LOG.md` (newest first).
