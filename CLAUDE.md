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
  older cards once each, 40 a round (`card_format` table): waiting cards keep the text on the card (his hand edits), sent cards show
  what went out, cards in conversation are only renamed (lead_board draws their description).
- Full list of build-time judgment calls: `DECISIONS.md`. Open items that need Rodrigo: `BLOCKERS.md`.

## Still open (ask once, then record the answer here)
- (none)

## Current status
- 2026-11-12 ~10am ET, RESUME HERE: **guard against "the signed document is attached" when nothing is** (see Decisions → Nothing
  "attached" that isn't). Eric Cederman's thread is not fixed by this (the email is out); Rodrigo should send Eric the signed document
  himself or correct it. If a real document must go, Rodrigo sends it; Charles's email never mentions it. 389 tests.
- 2026-11-12 ~10am ET, RESUME HERE: **house cards in the 1031 layout** (see Decisions → House cards look like the 1031 cards). New
  cards get it at once; the ~100+ older cards on the Email Approvals board are redrawn by the Mac's approvals rounds, 40 a round
  (about an hour for all). Tested on the fake Asana only: watch the first redrawn cards (bold sections show, and a card dragged to
  Approved still sends; sending is paused anyway). Not done: the 1031-style website gallery / photo email for houses. 380 tests.
- 2026-11-12 ~9:30am ET: the two-agent rule is now a standing rule for every agent on every pipeline (Rules for every session).
  House (MLS single-family) replies already run through it; first emails and follow-ups still do not (question open with Rodrigo).
- 2026-11-12 ~9am ET, RESUME HERE: **second reader built** (see Decisions → Two agents on every answer). 5 new tests, 377 pass.
  NOT verified against the live API (no Anthropic key in the cloud): watch the first "⚖️ Second read" lines on the Mac's next drafts
  (1031 🧠 reads and house NEXT EMAIL comments); if every one says "not done", the `second_read` call is failing (check `logs/`).
  Sending is still paused and Charles still silent; this only changes what gets drafted. Open for Rodrigo: should the reader also
  check first emails and follow-ups, and should it be able to stop an email he himself said "send it" on (today it only warns).
- 2026-11-12 ~7:30am ET: morning check only, no code change. Report arrived 7:00am; sending still paused (0 sent Nov 11). Flags posted on
  Claude Updates: FRESH_DEAL ARVs run 1.4–1.7x the ask (11 fresh deals look inflated), Opendoor / beycome / big brokerages on the HOT
  list, stale leads (Milton under contract, Aldo bounced), 34 new Fresh Leads cards are templates (no Claude analysis, 3rd day).
- 2026-11-11 evening, RESUME HERE: **Charles is fully silent** (Rodrigo: "have Charles stop all of the email conversations and the
  threads that he has going on right now"). `commercial.delay_note.enabled: false` on top of `outreach.sending_paused: true`: nothing
  goes to any agent or broker, not even the sorry-for-the-delay note (the "one exception" in Decisions → ALL SENDING PAUSED is OFF).
  Delay notes the Mac sent before it pulled this are out and cannot be recalled; how many is only visible on the Mac / the 📤 comments.
  The engine still reads incoming mail, mirrors it on the cards and drafts; drafts wait. Mail to Rodrigo (reports, forwards) still goes.
  Do NOT turn either switch back on without Rodrigo. 378 tests.
- 2026-11-11 ~4:30pm ET, RESUME HERE: **all sending to agents and brokers is PAUSED** on Rodrigo's word (see Decisions → ALL SENDING
  PAUSED). The Mac picks it up on its 10-min self-update; the pause commit reached the default branch ~4:10pm ET. The delay note
  goes out from the Mac's next approvals rounds inside the window (20 a round). Not verified live: the cloud cannot see the Mac's
  database, so how many brokers get the note is unknown until the 📤 "Sorry-for-the-delay note sent" comments appear on the 1031
  cards (brokers from the blast with no card get it without a comment). Brokers read not-a-fit who still owe a pass get nothing while
  paused. Do NOT unpause without Rodrigo. 376 tests.
- 2026-11-11 ~3pm ET, RESUME HERE: **"The process" tab on the 1031 page built** (see Decisions → The investor sees the PROCESS). Checked
  against the real Nov 9 pull in a scratch DB (1,290 pulled → 616 no price → 131 out by rule: 31 price, 36 asset type, 3 small MF, 3 cap
  under floor, 3 land/business, 4 airport, 51 quality → 543 in the box); the conversation half (brokers emailed / answered / why dropped)
  fills from the Mac's database on its next site publish (needs BLOB_READ_WRITE_TOKEN on the Mac; the cloud cannot publish). Vercel
  serves the new tab once the default branch is deployed. Rodrigo said "12 cap" in passing; the box and every screen say 8 cap, so the
  page says 8; `commercial.process.criteria` / `note` take his own wording. Not built: the process in the deals EMAIL. 372 tests.
- 2026-11-11 ~7:30am ET, RESUME HERE: **82 Tortuga is signed** (Authentisign "Signing complete" Tue 5:34pm ET; Peter's "who signs"
  answered by the signing itself). **Nov 10 end-of-day said "102 of 25 · 300 answers": all 1031 broker emails (om_request / om_reply /
  om_rodrigo) were counted as house leads**; fixed in `end_of_day` (and the residential daily cap no longer counts om_reply / om_rodrigo),
  test added. Real house count Nov 10: 0 first emails. 25 new Fresh Leads cards Nov 10 are templates (no Claude analysis). No 7am report
  by 7:25am today (Nov 10's came 9:05am).
- 2026-11-11 ~11:30am ET, RESUME HERE: **772 Cortaro Dr (Christian Bennett, card 1220358581918664) sat in "Sent · no answer yet" although he sent
  the T-12**: the engine only read replies inside its own Gmail threads. Built the inbox sweep for stray broker answers (see Decisions → Broker
  answers that are not a reply). Not verified live: the cloud cannot read Charles's inbox, so whether Christian's email was a new thread (most
  likely) or simply arrived after the Mac's last round is unconfirmed. **Rodrigo runs `make om-strays` on the Mac** (or waits for the next round):
  it prints every broker answer the board had missed and puts each on its card. Watch: Christian's card gets 📩 + the T-12 filed + Books received;
  check the list for false matches (a non-broker email naming a street). If Christian's email still is not found, look at how it arrived
  (Spam is not searched; a link instead of a file is listed, never fetched). **Root cause found after that from the Mac's log: Gmail quota errors were skipping threads every round** (see Decisions → opens only conversations with new mail); fixed the same day. 365 tests.
- 2026-11-10 ~10pm ET, RESUME HERE: **Cameron Cauley (8100 Opportunity Dr, Milton; card 1220358628718111, Needs Me) got three emails from
  Charles on his own**: two said his attachments "didn't come through" and re-asked for a T-12 he had said he can't give; the third (the fixed
  ask_books reply) asked for the books right after he asked for a call. Causes: the read never saw Charles's own earlier emails; fixed replies
  bypassed the call-request hold; no limit on how many times Charles answers one broker; his files never reached the engine (13 KB forward =
  no files; cause unconfirmed, possibly Outlook naming files only in headers). Fixed (see Decisions → once per thread). **Rodrigo calls Cameron
  (850-291-3341)** and should look in Charles's Gmail whether the OM / lease / income statement are there; if so drop them on the card.
  Other threads where Charles already sent 2+ replies on his own were not audited yet. 363 tests.
- 2026-11-11 ~10:30am ET, RESUME HERE: **both 1031 boards are in the new card shape, done from the cloud** (Rodrigo: "do the same thing on
  the 1031 initial sheet"). All 44 open cards on "Titan 1031 · Commercial (LoopNet)" + the 6 on Working Deals renamed and reformatted through
  the Asana connector (listing data from a scratch bootstrap of the 21 Apify datasets). The 10 unsent cards in New carried the 11-08 draft
  ("working with a 1031 exchange buyer … happy to sign a confidentiality agreement"): replaced with today's OM-request wording, same in the
  engine (`refresh_cards`: a card in New gets `email_block` fresh). The Mac's `card_format` stage re-renders the same 50 once more (same
  result, harmless) and marks them. Completed Not-a-fit cards untouched. 363 tests.
- 2026-11-11 ~9:30am ET, RESUME HERE: **1031 cards renamed and reformatted** (see Decisions → 1031 cards are readable). The 6 open
  Working Deals cards (Adriana Montes, Michael Grant, Larry Starr, Dennis Hopper, Brian Rowe, Craig and Larry Walsh) were updated live
  from the cloud and render correctly; the ~60 cards on the 1031 board get it from the Mac's next approvals rounds (stage `card_format`,
  40 per round). Watch: the first round's names on the 1031 board, and that a card dragged to "Approved · send OM request" still sends
  (the email block is unchanged). Not touched: 🧠 READ FOR YOU / 💬 comments (still their own format). 362 tests.
- 2026-11-11 ~7:30am ET, RESUME HERE: **Coral Shores: Rodrigo wants to pursue** ("I like the asset class, our investor could like this
  asset"). He agrees the broker's P&L projection is not serious (manager-written, 12% teaser fee, flood-only insurance, no reserves, three
  units imputed). A reply to Michael's post-NDA email is a DRAFT in Rodrigo's own Gmail (reply in that thread, Cc Michael's office address
  + Charles@): thanks, we like the asset, the six missing items (wind/liability premiums, association budget / reserve study / assessments,
  2024 storm claims, Aug–Oct 2026 + H2 2025 bookings per unit and the three never-rented units, condo docs / rental rules, why the April
  contract fell through), one candid line on owner-side underwriting, no number, no dates. He sends it himself. Note on the card. Next:
  when the documents land (they will come to rodrigo@ as Drive links again), read them in the cloud through the Drive connector and
  update the 8-cap range on the card; then a DEAL SHEET comment with his offer / ceiling when he decides.
- 2026-11-10 ~5:55pm ET, RESUME HERE: **Sleep Inn DeFuniak Springs (17834 State Hwy 285 S, Dennis Hopper / DSH; card 1220358635284810,
  Working Deals → Needs Revision)**: Rodrigo got the Revere CRE "Tier 1 Private files" and dropped them on the card as ONE ZIP (13 MB).
  Nobody could read it: the cloud cannot download Asana files or reach platform.reverecre.com, and `card_files` skipped `.zip`. Built:
  a ZIP on a card is unpacked and every PDF / spreadsheet / document / text / screenshot inside is filed and read like a direct
  attachment (`card_files.unzip`, test added). **The Mac's next approvals round reads the package and posts the 🧠 READ FOR YOU; the
  card then goes back to Needs Me.** Faster path given to Rodrigo: unzip and upload the files to his Google Drive, the cloud reads them
  through the Drive connector at once. Also fixed: three commercial tests called `send_approved_replies` on the wall clock and failed
  outside the 8:30–5:30 ET send window; pinned to SAT_10AM_ET. 359 tests.
- 2026-11-10 ~1:35pm ET, RESUME HERE: **Coral Shores / 9022 Midnight Pass Rd (Michael Grant, Dreamify) read from the post-NDA files.**
  Rodrigo signed the NDA; Michael's package (13 files) arrived as Drive links in Rodrigo's inbox, so the cloud read them through the
  Drive connector (the engine never follows links and cannot download Asana files from the cloud). Full read pinned on the card
  (1220358634696289, Working Deals board), card moved Needs Revision → Needs Me; the numbers live on the card, not here (public repo).
  Verdict: leans does not fit at ask once management, insurance and reserves are real; 8 cap sits well under the ask. Nothing sent to
  Michael. **Rodrigo must remove the two "82_Tortuga" PDFs attached to that card by mistake (Peter Dacko's contract)**, or the Mac's
  card_files stage reads them as deal documents. The Mac's next round will also download the P&L and post its own 🧠 read (duplicate,
  harmless). Engine gap seen: the ✍️ draft on a Working Deals card still says "drag to Approved · send OM request", a column that board
  does not have (use "send it").
- 2026-11-10 ~1:00pm ET, RESUME HERE: **In touch cleaned (Rodrigo: "most of them were dead")**. Of 26 cards there: 7 dead (broker said no
  8 cap: Estero/Rob Carroll, Key Largo/Hampson, Orlando All American/McGregor, Miami NE 2nd/Yesharim, Davie Orange/Waite, Bonita Springs/
  McClimans, Panama City/Clubbs new build) moved to Not a fit by hand; the Mac's next round sends the pass where still owed (McGregor,
  Yesharim) and clears them. 14 never answered (sent 11-09 ~5pm): the Mac creates "Sent · no answer yet" and sorts them there on its next
  check-replies/approvals round (see Decisions). 5 stay in In touch: Bushnell/April (karting, asked for books), Spring Hill/Pannone (sending
  numbers), Tarpon Springs/Foose (P&L only in DD), Tampa 16th/Silverman and Deland/Hasan (empty replies, asked to resend; weak). Also still
  to be cleared by the engine: the 52 cards sitting in Not a fit (10 passes per round). 358 tests.
- 2026-11-10 ~1:30pm ET, RESUME HERE: **Julio's hotel (2648 NE 32nd St) P&Ls were never read by Claude**: every read since ~2pm Nov 10
  was "rules only". Cause found: the 🧠 read's word budget (max_tokens 1200) was too small for the new sections (8-cap, pros, cons,
  doubts, believe), so Claude's answer was cut off, thrown away and labeled "unreachable"; and "retried next round" was never true.
  Fixed: budget 4000 (card chat / notes 2000), and rules-only reads are really retried with Claude (max 3, 30 min apart, no new email
  to the broker; `books_review.retry_pending`). Julio's card gets the full read at the Mac's next round. The JPG still needs a check
  (same API key; may be image size). Rules numbers on the card are raw: 2023 "YTD" $56,812 is a part year.
- 2026-11-10 ~12:20pm ET, RESUME HERE: **1031 clean-up built** (see Decisions → 1031 Not a fit). The 1031 board's "In touch with the agent"
  held 35 cards: 13 already read DOES NOT FIT and told no (the engine archived them but never moved them: that was the gap), 5 needed
  Rodrigo (moved to Needs Me by hand with a 🤖 note: Cameron Cauley call, Eric Cederman NDA, Albert Hernandez gas stations, Curt Ware
  number, DeFuniak hotel platform link), 15 never answered, a few waiting on docs. The Mac's next rounds clear the not-fit ones (pass
  where still owed, e.g. Steve McGregor and Babba Yesharim), plus the 45 in Not a fit, 10 pass emails per round in the window. Working
  Deals board created, empty: Rodrigo moves cards there. Watch: the first 🗑 comments and that no broker gets two passes. 356 tests.
- 2026-11-10 ~11:40am ET, RESUME HERE: **why Charles told Julio "we haven't received the Box invitation"** (7:49am ET): Charles reads
  only the broker's thread; the Box invite came as its own email, so the read assumed it never arrived and the draft went out on
  its own (before the no-commitments guard landed; it also said "identify within 45 days"). Fixed: a draft that says something did
  not arrive / asks to resend waits for Rodrigo, and the prompt forbids it. Also fixed: P&Ls named "P_L …" (how the card files are
  saved) were not read as P&Ls, and Matthews' "CONFIDENTIALITY NOTICE" footer was read as an NDA request (the 16:05 rules-only read).
  **That 16:05 read was rules-only: Claude was unreachable from the Mac (the JPG failed the same way): check ANTHROPIC_API_KEY /
  API credit on the Mac.** (11-10 noon: `make doctor` passed the Anthropic key, so it was a passing failure; a Gmail "Quota exceeded" in doctor is a busy minute, not a login problem, and doctor now says so.) The retry re-reads the folder, so Julio's P&Ls get read once Claude is reachable. 353 tests.
- 2026-11-10 ~11:15am ET, RESUME HERE: **Julio's card** (2648 NE 32nd St, card 1220386118395527) is in Needs Revision with 3 P&L PDFs
  (2022, YTD 2023, 2024 & 2025) and a "Year-to-date.jpg", no comment. Fixed so the Mac's next round reads them: files before the
  Needs Revision bounce, the JPG transcribed by Claude, then a 🧠 READ FOR YOU and the card back to Needs Me. The cloud can't download
  Asana files (asanausercontent.com blocked), so the read happens on the Mac. Watch that card for 📎 + 🧠. 352 tests.
- 2026-11-10 ~10:50am ET (real time; this session's earlier status lines from "~10:15am" on were stamped hours late), RESUME HERE:
  Rodrigo dragged **Julio Leyva / 2648 NE 32nd St, Fort Lauderdale** (card 1220386118395527) to Needs Revision with **no comment**, so the
  next round sends it back to Needs Me. The real numbers (historical P&Ls) sit in a Box folder Julio linked; the engine never opens links
  and the cloud cannot reach box.com (proxy 403). Told Rodrigo: download the P&Ls from Box, attach them to the card (card_files reads them),
  and write what he wants on the card. Only the OM (no expense lines, $425k NOI unverified) has been read so far.
- 2026-11-10 ~3pm ET, RESUME HERE: **drop financials on the 1031 card** (see Decisions). Built and tested against a fake Asana only:
  watch the first real attachment (📎 Filed your file(s) … comment, then a 🧠 READ FOR YOU). 352 tests.
- 2026-11-10 ~2:30pm ET, RESUME HERE: **1031 Reviewed column** (see Decisions): columns now New · Approved · send OM request · Needs Me ·
  Needs Revision · Reviewed · Good fit · Not a fit · In touch · Books received · Offer out · Closed. The Mac creates it on its next round. 351 tests.
- 2026-11-10 ~2:15pm ET, RESUME HERE: **1031 Needs Revision column + the four new parts in every deal read** (see Decisions). Reads
  written from the Mac's next round on carry them; older reads do not (drag a card to Needs Revision with "redo the read" to get one). 350 tests.
- 2026-11-10 ~1:45pm ET: Rodrigo asked where the 1031 comps live and to continue the analysis in his own Claude account. There are no
  independent commercial sales comps (the engine pulls comps for houses only); Byronessa's only comps are the 3 broker-picked sales in the OM.
  Built the Claude Doc "Byronessa · 7320 Byron Ave · Deal Analysis" (https://claude.ai/artifact/AEis1sRaKXUHowoY2MmGKG): status, property,
  T-12, tax reset, a chart of the 8-cap price by how it is run, comps, offer history, open questions. Numbers live there and on the Asana card.
- 2026-11-10 ~1:15pm ET, RESUME HERE: **Charles's unapproved replies stopped** (see Decisions → No dates, no commitments). Audit of the
  1031 cards: 25 replies sent, 21 with "identify within 45 days" / proof of funds / signed NDA; the St Petersburg broker was told twice that
  a signed NDA was attached (nothing was): Rodrigo should correct that one himself. OM request no longer offers proof of funds. 349 tests.
- 2026-11-10 ~12:30pm ET, RESUME HERE: **the 1031 board talks back** (see Decisions): notes on a card become Charles's next email
  (sent by "send it" or by dragging the card to Approved), questions get a 💬 answer, Needs Me cards get one 💬 What I think. The 5 cards in
  Needs Me (Michael, Kate, Armond, Cameron, Jake) get their take at the Mac's next approvals round. Watch the first ✍️ / 💬 comments. 348 tests.
- 2026-11-10 ~11:45am ET, RESUME HERE: Rodrigo could not find 9022 Midnight Pass Rd (Michael/Dreamify) on the board: the Mac had
  created the 1031 Needs Me column but moved nothing into it. Moved by hand: Michael, Kate Clifford, Armond Aivazyan, Cameron Cauley,
  Jake Hayutin (Luca stays in Offer out). Fixed: a card was marked parked before the move, so one failed move was never retried; now a
  failed move is retried next round. Exact cause on the Mac not confirmed (check `logs/` for "park … in Needs Me"). 347 tests.
- 2026-11-10 ~11:15am ET, RESUME HERE: **1031 Needs Me column built** (see Decisions). The cards behind his forwards sat in In touch
  (Cameron Cauley, Armond Aivazyan, Michael/Dreamify, Kate Clifford) and Books received (Jake Hayutin, Tampa); Luca stays in Offer out.
  The Mac's next approvals round creates the column and moves them. Duplicate forwards fixed. Watch: the column appears and holds those 5. 347 tests.
- 2026-11-10 ~10:40am ET, RESUME HERE: **Luca answered the Byronessa offer at 8:59am ET**: too far to negotiate point by point; a small
  short-term rental is not run with four on payroll. Nothing auto-sent (thread checked). Re-run with an STR manager fee instead of payroll:
  numbers posted as a DEAL SHEET update on the card (not here: public repo). A reply for Rodrigo to send himself was given in chat (concede
  the payroll point, manager-fee framing, ask where the seller trades, no new number). Next: Luca's answer; ask the 1031 partner if they self-manage.
- 2026-11-10 ~10:15am ET, RESUME HERE: **Rodrigo sent the Byronessa offer at 8:48am ET** from rodrigo@ in Luca's thread (To Charles, Cc
  Luca, signed Charles): the engine logs it on the Mac's next round (✉️ comment, Offer out). Card moved to **Offer out** by hand and a pinned
  📊 DEAL SHEET comment posted on it (card 1220369552444711). **Built:** the deal sheet on the 1031 site (see Decisions) and a more human books
  thank-you (three wordings, first name from the name or the mailbox, names the street). The site updates once the Mac republishes
  (needs BLOB_READ_WRITE_TOKEN on the Mac). **Flag for Rodrigo:** this repo is public and older status notes here hold deal numbers. 346 tests.
- 2026-11-10 ~9:45am ET, RESUME HERE: **built Rodrigo's fast-reply rules for the 1031 brokers** (see Decisions → Charles answers the 1031
  brokers fast). Applies to every NEW broker answer from the Mac's next round (10-min self-update); yesterday's answers are not re-answered.
  **Byronessa:** Charles cc'd Rodrigo at 8:04am ET in Luca's thread ("Rodrigo will take it from here"); Rodrigo replies there himself. Before this
  fix the engine skipped his emails (card blind) and could have auto-answered Luca inside his negotiation; now his email shows on the card, $3.0M
  moves it to Offer out, Charles stays silent there. Watch: the first ✉️ comment on card 1220369552444711 and the first books_ack / ask_books /
  pass sends (📤). Default branch fast-forwarded. 345 tests.
- 2026-11-10 ~8:30am ET, RESUME HERE: **Byronessa, 7320 Byron Ave Miami Beach (Luca Migliore, ask $4.59M) read from the books Rodrigo uploaded** (T-12 Nov25–Oct26, 2025 P&L, OM). Real T-12: revenue $783,831, NOI after taxes+insurance $372,604 = **8.1% at ask** (OM's $390k / 8.5% leaves out ~$18k; Luca's 8.2 is honest). Taxes today $40.8k on ~$2.3M taxable; a sale reassesses to ~$74k at ask (85% of price × ~19 mills, estimate: miamidadepa.gov is blocked from the cloud) → 7.4% at ask. **8% after reassessment = $4.30M on the owner's books; $3.65M once a 4% manager and 4% FF&E reserve are in** (owner-operated, no PM fee, no reserves, no debt service). First target was open $3.7M / land $3.9–4.1M; **Rodrigo then pointed out the books carry almost no staff** (front desk $31k, night $1.7k: one part-timer, owner-run; cleaning IS in the books, Airbnb/Booking do not pay for it). With 3–4 on-site staff (~$42k loaded each) + 4% FF&E the 8-cap-after-reassessment price is **$2.7M–$3.2M**, under the $3.5M box floor; at $4.59M it is a 4–5 cap staffed. Revised read: only a deal if the partner runs it owner-style, or in the low $3Ms. **Offer email written for Rodrigo to send himself (chat, ~9am ET): $3.2M cash, $100k deposit, 30-day DD from full books, 30-day close, as-is with FF&E, reasons = reassessment / no payroll for 24/7 desk / no FF&E reserve; ceiling $3.5M.** Ask still $4,590,000. **Then Rodrigo: "we need room to send it to my buyer at an 8 cap"** → offer email revised to **$3.0M, Titan and/or assigns** (at $3.0M it is an 8.5 cap even with 3 staff + reserve); resell to the 1031 buyer at ~$3.5–3.55M (8 cap after reassessment on books + 4% reserve + one more full-time person) = ~$300–350k room; our contract ceiling $3.3M. If the buyer underwrites the owner's books + reserve only, the buyer price is ~$3.97M and the ceiling rises to ~$3.7M. If he sends it, log it with `titan.cli offers` and move the 1031 card to Offer out.
- 2026-11-10 ~7:30am ET, RESUME HERE: **Peter Dacko (82 Tortuga, $245k): terms agreed Mon** ($5k EMD, 10-day inspection; Peter
  "that will be fine"), revised PSA sent 6:08pm for e-signature, and he asks **who signs for the LLC** (Needs Rodrigo card). His 11:15am
  counter ("$2000 is not enough to place the property in escrow") was read as SOLD and the approvals card went to Skip (the warm-board
  card is right); fixed: bare "in escrow" no longer means sold (test added). No 7am report by 7:25am today.
- 2026-11-09 ~6:30pm ET, RESUME HERE: **Rodrigo's evening asks built** (see Decisions → Simple replies go on their own). Answer to his
  question: **no, Charles had answered nobody**: all 21 drafted replies sat on the cards waiting for "send it". From the Mac's next
  round (after the 10-min self-update): the drafted replies that do not need him go out (about 19; Kate Clifford's and Michael/Dreamify's
  NDA replies wait for him), Kate's, Michael's, both LEANS FIT (Armond, Luca) and Albert Hernandez's pocket pitch are forwarded to
  rodrigo@ with the files, and the 29 not-fit threads are archived in Charles's inbox once Charles's pass note has gone. **Watch the
  first round**: 📤 / 📬 / 🗂 comments on the 1031 cards, the FW emails in his inbox (attachments), and that the office line complaint
  (Dreamify: dead air, no voicemail) gets checked. Sends outside the 8:30–5:30 window wait for morning. 354 tests.
- 2026-11-09 ~5:40pm ET, RESUME HERE: **Day-one status of the 1031 conversations, charted for Rodrigo** (artifact
  https://claude.ai/artifact/DCUPvzF3VjFztcR2j4ubWm; read-only, nothing changed on the board). From the 65 cards: 26 OM requests sent
  11:41am–12:40pm, 11 answered (42%); 29 blast brokers answered (cards created on answer); 40 answers read: 19 NOT A FIT, 11 DOES NOT
  FIT, 8 CAN'T TELL YET, 2 LEANS FIT (Armond / Clearwater Trulieve 8.25%; Luca Migliore / 7320 Byron Ave Miami Beach, OM says 8.50%,
  **rules-only read: the Mac could not reach Claude at 5:32pm ET, retries next round**); 30 of 40 answered within an hour. NEEDS YOU:
  Kate Clifford (CA/NDA, Jax Beach) and Michael/Dreamify (NDA + call 941-800-8585, Siesta Key; says our office line drops to dead air).
  Engine gaps seen: Justin Thibaut (Cape Coral) read NOT A FIT but stayed in "In touch with the agent" (the move did not fire);
  blast send counts are only on the Mac. Board: New 10 (sample), In touch 31, Books received 6, Not a fit 18, rest 0.
- 2026-11-09 ~2:15pm ET, RESUME HERE: **"Read everything for me" built** (see Decisions → Every broker answer is READ): the Mac's next round
  (10-min self-update, then check-replies / approvals) reads today's 6 answers retroactively and posts a 🧠 READ FOR YOU comment on each card:
  Cameron Cauley (OM + lease + income statement, $3.45M / 9.88% claimed), Armond Aivazyan (OM), Curt Ware (xlsx now actually read), Kate Clifford
  (CA/NDA → NEEDS YOU), Nathan Eissler (sends later), Mark Allen (leased → Not a fit), Mohammad Hasan (empty). Armond + Curt get regraded and
  move to Books received there (the poll missed it; a regrade error now shows on the card). **Watch the first round**: the 🧠 comments, the two
  moves, and that "send it" under a drafted reply sends it. Default branch fast-forwarded to this work. 352 tests.
- 2026-11-09 ~1:30pm ET, RESUME HERE: **Rodrigo: "I'm getting a lot of answers, open the emails, analyze them, get to work. What's
  needed?"** Checked from the cloud: the Mac IS working (27 OM requests sent 11:41am–12:40pm ET, 6 real answers mirrored on the 1031 cards:
  Cameron Cauley 8100 Opportunity Dr (OM + lease + income statement, price drop to $3.45M = 9.88% cap, "owner ready for offers"), Armond
  Aivazyan Clearwater Trulieve (OM filed), Curt Ware Siesta Key (income-statement xlsx filed, "owner negotiable"), Kate Clifford Jax Beach
  (wants the CA/NDA signed first, asks our exchange timing), Nathan Eissler Alachua (sends later today), Mark Allen Longwood (just leased =
  Not a fit), Mohammad Hasan Deland (empty reply)). **The cloud cannot open these emails**: both the Gmail connector and the cloud's
  GMAIL_TOKEN_JSON_CONTENT are rodrigo@, the answers are in Charles@ and the files are on the Mac (`data/listings/<id>/`). Options given to
  Rodrigo: (a) paste Charles's `secrets/gmail_token.json` from the Mac into the cloud env var GMAIL_TOKEN_JSON_CONTENT, or (b) run the
  analysis as a Claude Code session on the Mac, or (c) build "books review": Claude reads the books on arrival and posts the analysis on the
  card (not built). **Two engine gaps found, not fixed yet:** spreadsheets are filed but never read (`documents.local_files` only takes
  pdf/txt/md, so Curt's xlsx counts for nothing); Armond's and Curt's cards did not move to Books received / got no "Regraded with the books"
  line although `poll_om_replies` should have done both (check `logs/` on the Mac). Cameron's attachments were not filed at all (no
  "Files filed" line): likely links or a nested part.
- 2026-11-09 ~12:15pm ET, RESUME HERE: **Rodrigo said go: 26 OM-request cards (every in-box listing with a stated cap; the 5 missing broker
  emails were found online: Michael Grant, Adriana Montes, Dan Fischer, Mohammad Hasan ×3 in one email) sit in "Approved · send OM request"**.
  **No-cap blast built**: one email per broker (372 brokers / 468 listings), eight-cap-once-reassessed question, no card until the broker answers.
  **Nothing to do on the Mac**: its next approvals round (after the 10-min self-update) bootstraps the statewide pull from the 21 Apify datasets,
  builds the queue on the Mac, then sends cards first, then the queue, **8 per 20-min round / 220 a day** → ~130 today, ~220 Tue, the rest Wed
  morning. Watch the first round: `commercial_runs` row kind `bootstrap`, 📤 comments on the 26 cards, `commercial_threads` rows; a blast reply
  creates its card in "In touch with the agent". Verified in the cloud on a scratch DB (bootstrap loads 1,290 listings from the 21 datasets in 60 s, queue 378 brokers). 350 tests.
- 2026-11-09 ~9:45am ET, RESUME HERE: **FULL STATEWIDE PULL DONE in the cloud** on the new Apify Starter plan ($6.88: 7 searches ≈1,830
  rows + 347 property sheets, 280 of which came through this time): **1,307 listings, 542 in the box across 41 counties, 435 brokers**,
  629 "price not disclosed" (kept, flagged, out of the lists), 136 out (51 on the new quality screen, 4 on the 45-min airport screen),
  0 CLEAN / 17 SUSPECT / 525 NEEDS-OM; only 28 in-box listings state a cap, 25 an NOI. Files `reports/commercial/2026-11-09/`
  (gitignored; master.xlsx, broker_contacts.csv, needs_om.csv, deals_email.html), buyer brief `reports/commercial/buyer_brief_2026-11-09.pdf`
  sent to Rodrigo (chat + inbox). **New this morning (Rodrigo):** airport screen (`titan/commercial/airports.py`, ≤45 min, config
  `airport_max_minutes`), quality screen (`titan/commercial/quality.py`: building class ≥ C, no below-C-area / distress language; LoopNet
  has no neighborhood class), both on cards/email/site/brief; the sourced-property agreement draft for the buyer's agent
  (`docs/legal/Titan_Sourced_Property_Agreement.docx` + .md; attorney review before signing). Apify saga: his first upgrade landed on
  the Creator plan (no Store actors) → removed → Starter ($39/mo, $19 cap shown; raise it in Billing → Limits if a pull stops).
  **The Mac's DB does not have this pull**: on the Mac run `make commercial` (its own pull, ~$7) or copy the cloud's raw files; the
  cloud cannot post the 542 cards (no ASANA_TOKEN) nor the site (no BLOB token). Monday: he drags cards to "Approved · send OM request".
- 2026-11-09 ~1:30am ET: **buyer brief** (`make buyer-brief` → `reports/commercial/buyer_brief_<date>.pdf`, `titan/commercial/buyer_brief.py`):
  one sheet for the 1031 buyer and his agent with the live numbers (screened / fit / brokers / by type / by region), the week's plan
  (book requests Monday, analysis as books land, review call Wednesday, offers after), and what we need from them (proof of funds,
  confidentiality agreements, a decision on the call). `commercial.buyer_name` prints on it. Sent to Rodrigo from the sample numbers.
- 2026-11-09 ~1am ET, RESUME HERE: **the loop is closed** (see Decisions → OM requests out, books back): drag a 1031 card to "Approved ·
  send OM request" → Charles@ sends the book request → answers, response clock and attachments land on the card → books regrade the
  deal and move it to Books received. Default branch `claude/funny-bohr-d0mkrt` fast-forwarded to this work (Vercel deploys from it);
  everything also pushed to the new public repo `titanbuilds1995/Commercial-Tracing-Software` (main). Rodrigo set DEALS_SHARE_KEY.
  **Still on Rodrigo:** the dashboard URL for `commercial.site.url`; the Apify plan (Starter $39/mo recommended, pull twice a week);
  run `make commercial` on the Mac (full pull, cards, email, site). Then drag the best cards to the send column; the first live round
  is the thing to watch (📤 comment, card in In touch with the agent, `commercial_threads` row). 24 commercial tests.
- 2026-11-08 ~11pm ET, RESUME HERE: **the visual layer for the 1031 deals** (see Decisions → 1031 deals are VISUAL): deals email with
  photo cards (sent to Rodrigo from the cloud for the sample; `reports/commercial/<date>/deals_email.html`), the "1031 Deals" page on the
  dashboard (private + investor views; `deals.json` goes to the Blob store on every pull once BLOB_READ_WRITE_TOKEN is on the Mac), the
  board columns as his categories (renamed by the engine on the Mac's first `make commercial`; the 10 sample cards sit in what becomes
  New), `make commercial-sheets` for the Chrome-on-the-Mac sheet reading. 21 commercial tests, 330 total. **Rodrigo must:** add
  `DEALS_SHARE_KEY` (any long random string) to the Mac's .env AND to the Vercel project's environment variables (then redeploy), put
  the dashboard URL in `config.yaml → commercial.site.url`, run `make commercial` (or `make commercial-offline RAW="raw/*loopnet_*.json"`
  to publish what the cloud already pulled, no Apify spend); the investor link is `<site>/1031.html?k=<DEALS_SHARE_KEY>`. Vercel deploys
  the dashboard from the repo's default branch, so this branch must be merged for the page to exist. Photos could not be checked from
  the cloud (the image CDN is blocked here); size token 116 is what LoopNet's own pages use.
- 2026-11-08 ~8pm ET, RESUME HERE: **Commercial screen for the 1031 buyer built** (`titan/commercial/`, see Decisions; `make commercial-sample`,
  `make commercial`, `make commercial-offline`; 18 new tests, 327 total pass). **Sample pull done in the cloud** (15 LoopNet rows per
  asset family, $0.21 of Apify credit): 109 listings, 66 in the box across 14 counties, **all 66 NEEDS-OM** (0 CLEAN) because LoopNet
  blocked every full property sheet (NOI / occupancy / lease live there; search rows only show the cap when the broker put it on the
  placard, 15 of 66). Files: `reports/commercial/2026-11-07/` (master.xlsx with Master / Top 25 / Broker Contacts / Needs OM / Diff,
  broker_contacts.csv = 63 brokers for Monday, top25_1031.pdf is empty until something is CLEAN). Asana board **"Titan 1031 ·
  Commercial (LoopNet)"** (project 1220324280677489) created with the 10-listing sanity sample as "OM request" cards in Needs OM
  (posted by hand from the cloud, so they carry no Asana tag; the engine tags its own cards `1031-buyer` on the Mac and never doubles a
  card already on the board). **Waiting on Rodrigo:** (1) sanity-check the 10 sample cards / scoring; (2) Apify budget for the full
  statewide pull (~$4–8 once, same again per daily re-run; free plan caps at $5/month, BLOCKERS #9); (3) then `make commercial` on
  the Mac (ASANA_TOKEN there posts the cards). Not done: LoopNet property sheets (BLOCKERS #10), so evidence grades are C/D until OMs
  come back; the OM PDFs brokers send can be dropped in `data/listings/<listing_id>/` and are read on the next run.
- 2026-11-07 ~7:30am ET, RESUME HERE: **Peter Dacko accepted 82 Tortuga Rd at $245,000** (Fri 2:30pm; wants proof of funds;
  our Nov 4 AS IS contract, back-up terms in Paragraph 20). Milton (359 E 12th St, $440k) escrow due ~Mon Nov 9. Juan Huice $285k
  offer out, no answer. Mac woke Fri ~11am (catch-up ran), quiet after ~2:30pm; no 7am report Sat. Needs Rodrigo cards posted.
- 2026-11-06 ~7:30am ET, RESUME HERE: **Mac off since Wed Nov 4 ~8pm ET** (no reports Thu/Fri, nothing on Asana). **Milton
  Ramirez (359 E 12th St) is UNDER CONTRACT**: Rodrigo sent the signed contract Nov 4; Milton asked for the $10k escrow (3 business
  days) then the 10-day inspection; close by Nov 30. Card stays on Warm Leads "Hot Offer Sent OUT" (approvals-board Skip is the old
  misread); Needs Rodrigo card posted. Juan Huice: $285k offer sent Nov 4 by hand. Fixed `comps_loop.review_due` (used the wall
  clock instead of run_date; its test failed since Nov 4). All tests pass.
- 2026-11-05 ~7:30am ET, RESUME HERE: **Milton Ramirez (359 E 12th St, Hialeah) is a live deal the engine skipped**: he countered $440k /
  $10k EMD / 10-day inspection / close by Nov 30 and asks for a complete, fully signed contract; "fully signed contract" matched
  SOLD_RE ("signed contract") → read as sold → Skip. Fixed (SOLD_RE needs "have/got/accepted a signed contract"; test added). Peter Dacko (82 Tortuga): "other offer is $20k better … at $260K", Rodrigo is
  negotiating himself. No 7am report yet today (Mac asleep); Nov 4 end-of-day said 0 of 25 although Rodrigo sent 3 offers by hand.
- 2026-11-03 ~3pm ET, RESUME HERE: **one card per lead** (see Decisions): the answer to an agent is the NEXT EMAIL block on the lead
  card; dragging the lead card to Approved sends it; dictations on the card become that email; reply cards are folded in. Cause of
  Thais's loop: his dictated email went to the "Reply ·" card, the lead card he dragged to Approved had nothing to send and bounced
  to Needs Me, and "SEND HER THE EMAIL" was read as a question. Thais: at 2:13pm ET her email (his words: code flags / rear structure /
  liens / free and clear / updated photos) was put on a "Reply · Thais Guevara" card in Approved (gid 1220184869462027); it sends at
  the Mac's next round as it is, then the new code posts 📤 on her lead card and completes that reply card. Watch the first live round: the fold of any remaining reply
  cards and the first lead-card send (comment "📤 Sent to …" on the lead card). 308 tests.
- 2026-11-03 ~7:30am ET, RESUME HERE: **no 7am report and no Nov 2 end-of-day** (Mac asleep after ~6:45pm Mon). Fixed: lead cards
  grew a new WHERE WE STAND copy every round (Thais had 12, Seneve the old draft stacked so Rodrigo saw "the old email"):
  `card_parts` now keeps only the listing / URL / Agent lines as meta. Applied by hand: Aldo (his "send it + we tried calling twice"),
  Javier (his dictated email, first property only), Thais (reply card with his code / liens / photos questions); Seneve reply was
  already right. 1 new test.
- 2026-11-02 ~4:10pm ET, RESUME HERE: **clean cards** (see Decisions → Clean cards): formatted WHERE WE STAND description on answered
  leads, agent footers stripped from mirrored replies, superseded bot comments deleted as rounds pass, comps comments deduped, and a
  "no" from the agent sends the lead + its reply card to Skip on its own. Edward Martinez (said no) moved to Skip by hand with his reply
  card. The Mac pulls within 10 min; existing cards get the new description and lose old comments over the next few rounds (4 deletes
  per card per round). Watch: the first live html description (Lise / Richard) and that `parse_card` still reads it (the fake in
  tests mimics Asana's rendering; verified once on a throwaway card). 306 tests.
- 2026-11-02 ~1:30pm ET, RESUME HERE: Rodrigo asked why 10 follow-ups still sat in Approved (2 per round; Arcides, Abraham,
  McGee, Thais went 12:21/12:40pm) and why Zaira Zavala, Javier Rodriguez, John Sacristan sat in Needs Revision (dragged there
  Oct 28/29, no comment). Merged: per_tick 5; a no-comment Needs Revision card returns to Needs Me at the next round (his rule, see
  Decisions; replaces the first attempt of a 3 h fresh rewrite). The Mac pulls within 10 min: next round sends 5 and moves the three. 302 tests.
- 2026-11-02 ~6am ET, RESUME HERE: **Good morning follow-ups.** Rodrigo asked for a second email to every agent who never answered.
  14 follow-up cards written in the cloud and posted in **Needs Me** (same-thread reply cards): Veronica Zavaleta, Arcides Otero,
  Richard Dewitt, Liselotte Seer, Robert Tammaro, Laura Sanders (first email Oct 30), Carlos Montoya, Yerislenia Dominguez (Oct 29),
  Olga Montoya (Oct 27), Michael Balthazar, Damon Penn, Michael McGee (Oct 26), Abraham Elbaz, Thais Guevara (Oct 24). Left out on
  purpose: Douglas Montas + Dror Gerges (bounced; call them), David Carlton (Rodrigo: too fresh, wait 45 days), Richard Dipietro +
  Cheryl Medeiros (they answered; ball with them). **Rodrigo approved all 14 in chat ("just do it"): moved to Approved ~6:30am ET**; they go out 2 per 20-min tick from Charles@
  starting 8:30am ET (about 2.5 hours for all 14), each card moves to Sent with the time. Engine feature built for next time (`make follow-ups`,
  see Decisions); it does not run on its own. Gmail on the Mac is working again (sends landed Oct 30). 301 tests.
- 2026-10-30 ~9am ET, RESUME HERE: **catch-up on wake** (Rodrigo: "make sure it happens when I turn the computer on"). Mac
  asleep since 3:45pm Oct 29: no EOD Oct 29, no 7am report since Oct 27. Now every check-replies run (every 15 min while the Mac
  is on) checks `titan/catch_up.py` `due()` and starts `titan.cli catch-up` in the background: today's missed full run-daily, then
  the morning report (a failed report email retries hourly; nothing after 9pm ET). Full run-daily now writes a `run_daily` row and
  runs one at a time (`logs/run_daily.lock`). The evening report catches up on yesterday at any hour, not only before noon. No
  crontab change needed (rides on check-replies). Still needed: Charles's Gmail on the Mac (`make gmail-test`), or nothing sends. 297 tests.
- 2026-10-29 ~3:15pm ET, RESUME HERE: **Approved is not sending because Charles's Gmail is not connected on the Mac** (the new
  alarm card posted 3:02pm: "10 approved email(s) are waiting"; nothing sent since Richard's reply at 9am). Rodrigo must run
  `make gmail-test` / re-auth as Charles at the Mac. Rounds themselves run (2:44pm round handled Celia + Juan). Juan Huice: his
  "change the email … subfloors" hit the numbers path and the text never changed → rewritten by hand (Needs Me) and fixed:
  `DICTATED_RE` in comment_router; a numbers comment that also dictates a change now falls through to the rewrite. 291 tests.
- 2026-10-29, (superseded: the Mac was back by 9am Oct 29) **Mac off since ~3:04pm ET Oct 28** (last Asana activity 19:04Z; no 7am report Oct 28 or 29). Same code
  runs clean here (tests + offline run-daily + report with HTML data block). Richard's dictated answer ("too far, follow up in 30
  days") sits in Approved, sends once the Mac is on. **David Carlton held**: Rodrigo commented "too fresh, wait ~45 days, make a
  long-term follow-ups board" but the engine rewrote the email and the card reached Approved; cloud moved it to Needs Me with a
  comment + Needs Rodrigo card (send now or build the follow-up column). Not built yet: Long-term follow-ups column (wait N days → back
  to Fresh Leads); comment_router should treat "wait N days / follow up later" as a park, not a rewrite.
- 2026-10-29 ~3pm ET, RESUME HERE: Needs Revision worked by hand in the cloud (10 cards): Edward Martinez rewritten from his
  dictation, **Aya Olmedillo is now a written OFFER ($163k, 15-day inspection, 30-day close after, $2k escrow, we pay closing, Cc
  Rodrigo; the engine refused it as "dollar figure" — gap: an offer dictated on a first-email card should become the offer, not fail)**,
  Alan Alcala + Peter Zhuang rewritten to the direct rule (sat since Oct 27 untouched) → all four in Needs Me; Carlos Montoya back
  to Approved (his comment was just the phone number); Richard's lead card → Sent · no answer yet (answered 9am); Zaira, Javier,
  John Sacristan have no comment → prompted. Aldo Ramos asked for comps at 2:56pm: the Mac's next round posts the numbers.
- 2026-10-29 ~3pm ET, RESUME HERE: **approvals rounds were dying partway** (Rodrigo: "it reads some and doesn't read others…
  there's a lot of approved, it's already past the 20 minutes"). Causes: no retry on Asana 429 (a round reads comments on 100+
  cards), no outer try (one error killed the round), sending ran LAST, rounds overlapped (no lock). Now: Asana client retries
  429/5xx; `cmd_approvals` sends FIRST, runs every stage on its own (`stage()`), one round at a time (`logs/approvals.lock`),
  and posts an alarm card when Gmail is down with Approved cards waiting (`alert_gmail_down`). End-of-day report needs the Mac
  awake at 7pm: `make wake-evening` adds a 6:55pm pmset wake (told Rodrigo). Real fix = run the engine in the cloud (not built;
  DB lives on the Mac). 290 tests.
- 2026-10-29 ~2:30pm ET, RESUME HERE: **the engine now reads the Titan Warm Leads board.** Until now it never looked there:
  Lise's comment of Oct 28 5:01pm (closing costs, $3,000 deposit, screenshot of the email) was never answered and Peter's card
  got no status. The Mac picks this up within 10 min; Lise's comment is answered at the next approvals tick. Also found: Peter's
  9am reply is hidden in Charles's Gmail (likely Spam: Rodrigo's search finds the thread but the message doesn't show); the
  Oct 27 email to Peter never carried the Cc to Rodrigo; Peter's lockbox code was read as a price. Not fixed yet (offered). 290 tests.
- 2026-10-29, RESUME HERE: **end-of-day report built** (see Decisions). Nothing to install: the Mac pulls it within 10 min and
  tonight's arrives after 7pm ET. Deletes are only seen from the Mac's first run on this code (moves and comments come from Asana
  history, so today's are complete). Calls are a board-based guess; real call counts need the Aircall API (not connected; ask
  Rodrigo before adding). 289 tests. Next: read tonight's report with him; tune the "probably called" rule if it misreads.
- 2026-10-28 ~2pm ET, RESUME HERE: **comp engine rebuilt + daily comp loop** (Rodrigo: "the comps are just incorrect … fix the engine …
  run a loop daily"). Built from the real misses on the cards: townhouse vs pool houses (Castro), split layout + waterfront lot sales
  (Carlton), $3.1M new build next to a 1942 duplex (Dipietro), unpermitted 4-bed in 1,166 sf (McCarthy). New `titan/comps_engine.py`
  (product match, exclusions, similarity ranking, layout discounts, grade A–D) used by valuation and the snapshot; `titan/comps_loop.py`
  runs inside run-daily (step `comps`, after value, 5:30am on the Mac) → back-test on real sales, tuning, re-value the board, Claude
  review (15/day), "Comps" section in the 7am report. **Nothing to install: the Mac pulls the code on its 10-min self-update; the first
  loop runs tomorrow 5:30am** (`make comps-loop` on the Mac runs one now; `make comps-check` only measures). Grades and "Comp check"
  appear on cards at their next rewrite / numbers snapshot. 282 tests. Next: read tomorrow's Comps section; if the back-test error is
  high (>12%) look at the by-product breakdown in `reports/comps/loop_<date>.json`.
- 2026-10-28 ~8am ET, RESUME HERE: **direct tone** (see Decisions). 44 of the first 100 Fresh Leads cards break it ("curious" 11,
  "doesn't scare us" 10, "fine on our side" 6, "the bigger question" 5…): the Mac rewrites them on its own, 15 per 20-min tick
  (bot comment "Rewritten to be direct"), on the API; cards his comments shaped are left alone. 268 tests.
- 2026-10-28 ~7:30am ET, RESUME HERE: **no 7am report today** (none in Rodrigo's inbox by 7:25am; Mac asleep or the run failed:
  `make report` / check `logs/`). The cloud review read yesterday's 2:11pm designed report and found its data block missing: the Gmail
  connector returns the HTML part as text, so `with_data_block()` now also puts TITAN-DATA-B64 in small grey type at the foot of the
  HTML. Funnel "Answered by us 4 of 3 · 133%" fixed (capped at agents who replied). Board: 82 Fresh Leads (all with intro, no banned
  phrases), 0 Approved, 13 Sent, 2 Needs Me (Lise, Richard reply), 3 Needs Revision, 19 Skip; every Rodrigo comment has a bot answer.
  Lise: offer sent 1:19pm, she has 2 higher offers; Rodrigo asked her the number himself. Richard: wants a range (comps say $400–460k).
- 2026-10-27 ~3:50pm ET, RESUME HERE: **Fresh Leads / Needs Me rule** (Rodrigo: "we're making this mistake right now, it's not
  good"): I had put Peter Dacko's and Olga Montoya's finished emails back in To Review; both moved to Needs Me. To Review renamed
  Fresh Leads on Asana (board description rewritten); engine changed so every worked-on card lands in Needs Me (see Decisions).
  Waiting on him in Needs Me: Peter Dacko (drag to Approved to send), Olga Montoya (same), Richard's reply card (range $400–460k
  posted; holding answer or "offer N"), Lise lead card (ball with Lise). 266 tests.
- 2026-10-27 ~3:35pm ET, RESUME HERE: Rodrigo asked for a board check ("I need to send an email out soon"). Done on Asana by hand:
  **Peter Dacko** (he called Peter; Peter sends the lockbox once emailed): follow-up email written on the card, To Review, no price;
  **Olga Montoya**: his ask applied (why no interior photos + would the seller hear a cash proposal), To Review; **Lise**: the
  1:30pm "reply from Lise" asking "at what number" was Rodrigo's OWN email from rodrigo@ (Cc'd on Charles's thread), the engine
  read it as her asking for an offer and drafted a holding reply → that reply card moved to Skip, correction on her lead card,
  ball is with Lise (2 higher offers, she will present our $209k); **Richard** ("pull duplex comps"): the cloud can't rerun
  HomeHarvest (realtor.com unreachable), so the existing set was cleaned on the reply card: duplex ARV $700–750k, range
  $400–460k, his call (holding answer as is, or "offer N"). Alan Alcala + Peter Zhuang sit in Needs Revision with no comment.
  Engine fixes: `is_ours()` skips any inbound mail from GMAIL_SENDER / REPORT_TO / our domain; `street_name()` drops unit
  suffixes (Olga's "no address" rewrite failure); multi-family comps drop $/sf outliers (`drop_ppsf_outliers`); report_html
  f-string that broke Python 3.11 (`make setup`) fixed. 265 tests.
- 2026-10-27 ~3:30pm ET, RESUME HERE: **no more second cards** (Rodrigo: Richard's "re check these comps … give me a range" on the
  lead card made two new NUMBERS cards on Claude Updates and put the numbers + 💬 answer on the Reply card). Now every answer lands
  on the card he commented on: numbers snapshot as a comment (no NUMBERS card anywhere), 💬 answer, lead card → Needs Me; a reply
  card waiting for that agent is left alone (held if it was in Approved); only a dictated answer / "send it" / a wording change
  reaches it (`lead_card_comment`, one rule for every column a lead card can sit in; the old `card_chat.lead_cards` is gone).
  Today's two duplicate NUMBERS cards for Richard were completed on Asana; yesterday's two are still in Needs Rodrigo. The
  re-check he asked for (Polk St duplex, range $400–470k) is in the 💬 comment on the "Reply · Richard Dipietro" card (Needs Me).
  262 tests.
- 2026-10-27 ~3pm ET, RESUME HERE: **report graphs redone** (Rodrigo: "the graphs in the email were horrible, not easy to
  understand"). The 14-day column chart (28 thin bars, 9px numbers, no readable dates) is gone. "Day by day" is now one row per day
  for the last 7 days, newest first, Today tinted gold, a navy bar "4 sent" and a gold bar "2 replies" with the number written after
  every bar, zero days in faint words, week totals + reply rate on top, the 7 days before in one sentence. "From email to offer" is
  four steps (emails sent → agents who replied → answered by us → offers sent), each with a big number, a plain line like
  "3 of 9 wrote back · 33%", and bars on one shared scale. Percent-width table cells only (what Gmail/iPhone draw reliably).
  Checked at 640px and 390px with Playwright. Lands in tomorrow's 7am report; `make report` on the Mac sends one now. 261 tests.
- 2026-10-27 ~1:35pm ET, RESUME HERE: the Mac was STUCK on morning code all day (its `git pull` died on an untracked
  `reports/2026-10-27.md`); Rodrigo cleared it, reports are untracked now, and every approvals tick checks `code_behind()` (alarm
  card). First API rewrites landed 1:19pm. **Card chat gap:** David Carlton's and Alex Castro's "take a look and tell me what
  you're seeing" got a rewritten email and no answer (chat only ran on held comments): now any question / ask for analysis
  gets its 💬 answer whatever else the comment triggers (`route_comment`), QUESTION_RE widened. Both answered by hand on their
  cards (Carlton: all exits $500–560k, builder/Airbnb play; Castro: wrong comps, townhouse ARV ~$300k, HOA rules, not a deal
  above ~$175k). Lise / Cheryl / Richard answers were in Approved waiting on `make approvals-now`. 252 tests.
- 2026-10-27 ~2:30pm ET, RESUME HERE: **photo rule** (Rodrigo: with 10–15 pictures we can see the work, so "we need to know how much
  work it needs" sounds dumb; only under 4 pictures). Built into the validator, all three prompts, the protocol (§0c) and the fallback
  templates (a `few_photos` angle for thin listings). 48 of the 91 To Review cards asked it today: the Mac's next approvals ticks
  rewrite them on their own (15 per 20-min tick, bot comment "Rewritten: the listing has N photos…"), nothing for Rodrigo to run.
  Cards his comments already shaped are left alone as before. Cards show "· N photos" on the listing line. 257 tests.
- 2026-10-27 ~1:15pm ET, RESUME HERE: **Richard** (same as Lise): Rodrigo dictated the answer on the lead card and dragged it to
  Approved; no reply card existed, so the engine kept bouncing it. Reply card made by hand (gid 1219948219758582, Approved).
  Engine now: a dictated answer on a lead card ("Answer the email with: Hey Richard, …") becomes the reply card itself
  (`dictated_answer`; Approved when the lead card was dragged to Approved, else To Review); sent lead cards in Approved are
  feedback, not sends. **One card per lead** (Rodrigo: "two cards for Lise"): when an answer/offer card sends, its text is
  posted on the lead card ("📤 Sent to …") and the answer card is completed (off the board). Inbound replies always mirror to the
  lead card, never a reply card. A numbers comment that also asks for analysis (David Carlton, 1:02pm) gets a card-chat answer
  after the table. **Watch: David Carlton's comment is the first live test of card chat on the API** (needs the Mac to pull;
  next approvals tick). Lise's offer + Cheryl's + Richard's answers in Approved, not yet sent as of 1:10pm. 251 tests.
- 2026-10-27 ~1:30pm ET: **rewrite + send** built (see Decisions → Asana). Rodrigo: "I tell it what to write, it rewrites it,
  it already has my tone, so it can just send" — only when his comment says send (his choice). Hand edits between the EMAIL
  lines + drag to Approved still send word for word.
- 2026-10-27 ~1pm ET, RESUME HERE: **Lise's $209k offer and Cheryl's answer were never sent**: Rodrigo's comments landed on
  the LEAD cards, and the engine applied them to the first email (already sent) while the answer cards sat in To Review.
  Both moved to Approved by hand (send at the next tick). Fix: a comment on a lead card now rewrites / sends / holds the
  answer card waiting for that agent (`sync_feedback`, `_lead_note`); with no answer waiting it says so. **Lead board
  rule changed (Rodrigo): "Answered" = the AGENT wrote last (or closed); once we reply the lead goes back to "Sent · no answer
  yet"; Needs Me = his move.** Mac login still refused on both paths (token 401 invalid + keychain "Not logged in"): cron
  rewrites fail until `make claude-login` at the Mac; **Rodrigo moved the communication part to the paid API** (`scoring.communication_provider: api`;
  the Mac login is no longer in the path for card work; scoring still on the plan). Lise's card had gone to Answered because the
  old code read OFFER APPROVED as "waiting on the agent": an approved, unsent offer now stays put. Merged with the parallel "card chat" commit
  (`titan/card_chat.py` answers first when Claude is reachable; this deterministic routing is the no-Claude safety net). 249 tests.
- 2026-10-27 ~1pm ET, RESUME HERE: **chat on the cards** (Rodrigo: "I asked for input and got nothing back"). Before, a question
  on a card got "Holding it" and comments on sent lead cards (Sent / Answered / Needs Me) were never read. Now
  (`titan/card_chat.py`, runs every approvals tick): a question on any card → Claude answers in a "💬" comment from the listing,
  internal numbers, the agent conversation and the card's comment thread (nothing moves); input about a lead ("seller
  inherited it") → saved in `lead_notes` and fed into every later rewrite and reply draft for that agent; a change on a lead
  card with a drafted answer waiting is passed to that reply card ("From your comment on the lead card: …") and rewritten
  there (closes the Lise gap). Claude unreachable → one "Held: couldn't answer yet", retried next round. Uses the Mac's Claude
  login like rewrites. Still open: "offer N" on a lead card isn't wired to approve-offer (chat tells him the command).
- 2026-10-27 ~12:30pm ET, RESUME HERE: Rodrigo never got the "results as graphs" email: the designed report landed at 7:19am,
  after the 7:00am send, so today's was text only. Added two charts to it (`report_html.day_chart`: emails sent vs agent replies
  per day, 14 days; `funnel`: emails → replies → conversations → offers), built from table cells so Gmail/iPhone draw them.
  First designed report with graphs arrives tomorrow 7am; `make report` on the Mac sends one now.
- 2026-10-27 ~9am ET, RESUME HERE: **new "Needs Me" column** (Rodrigo: "one more, it should say needs me"; engine creates it
  before To Review, `needs_me_section`). Needs Me = the next move is his: numbers posted (comment router now parks those here,
  not in Needs Revision; old numbers-held cards migrate on the next tick: Carlton, Montas, Kara, Dipietro), and lead cards with
  status NEEDS YOU / OUR TURN / ANSWER DRAFTED / OFFER DRAFTED. **Answered** = we replied, waiting on the agent (or closed /
  offer approved). Lise moves to Needs Me (her $209k offer is still unsent in To Review). Nothing ever sends from Needs Me.
- 2026-10-27 ~8:30am ET, RESUME HERE: Rodrigo asked why Carlton, Montas, Kara, Dipietro still sit in Needs Revision. They got
  their NUMBERS snapshots yesterday 4:40pm and wait on his call by design (comment what to send, or drag to Approved). Two errors
  fixed in `comps_report`: the same sale listed twice (MLS + public record) now counts once (`dedupe`; Carlton's ARV unchanged),
  and an agent's stated rent only applies to the house his thread is about (Richard's $4,500 Polk St rent had landed on Riviera
  Beach; correction comment posted on his card). Still unanswered from his comments: Kara subfloors, Montas rent figure.
- 2026-10-27 ~7:45am ET, RESUME HERE: morning review. 9 sent / 3 real replies (Lise, Richard, Cheryl) / 1 bounce. **Lise's $209k offer
  card is still in To Review (never sent)**: Needs Rodrigo card posted. Cheryl (Sapphire St): "working with an offer, happy to take a
  backup, happy to show"; her testimonial-link uid was read as a $495,059 counter → links/brackets stripped before price parsing.
  Charles's reply card (no number) in To Review + Needs Rodrigo card (backup offer?). Adrienne: NW 28th Ct unconfirmed → removed,
  card back to To Review. **Cron Claude still refused** (30 new cards came out as templates; Montas/Carlton/Foland "token refused"):
  30 cards being rewritten in the cloud; "buyer angle" template line removed and banned. 241 tests.
- 2026-10-27 RESUME HERE: **morning report is a designed email** (Rodrigo: "needs to be a nice design, like all of our other
  designs"). `titan/report_html.py` renders it in the house style (navy bar + gold rule, paper, white panels, serif headings, mono
  labels; email-safe tables + inline styles, 640px, phone-checked): headline, 4 KPI tiles, a card per conversation (status pill,
  agent tap-to-call, their last words, gold "Next step", internal ARV/MAO, Asana card + listing links), scoreboard, trust ladder
  progress bars, top targets, engine health. Sent as HTML + the same text version (the cloud still reads TITAN-DATA-B64 from the
  text part). Repeated warnings collapse ("×28 today"); stale BLOCKERS.md no longer pasted into the email. Takes effect at the
  next 7am report. 239 tests.
- 2026-10-26 ~5pm ET, RESUME HERE: **comments now do what they ask** (Rodrigo: "why would it not give me what I am asking?").
  `titan/comment_router.py` sorts each card comment: numbers (comps / ARV / land / cash flow / where we need to be) → numbers
  snapshot (`titan/comps_report.py`, Lise's layout + compact comps table 6–10 sales / last 12 months / averages, ARV = exit,
  investor price, our number, gap, cap-rate prices when rent is stated) as a NUMBERS card in Claude Updates → Needs Rodrigo +
  a pinned comment on the lead's card; email held in Needs Revision. send = ONLY a plain go-ahead (allowlist; any condition,
  question, negation, number, recipient or stop emoji holds) → Approved untouched. call ("mark hot", "call tomorrow") →
  NEEDS_CALL + 📞 card. check ("double check that's their listing") → the agent's listings go into the rewrite. Reviewed in 4
  rounds by an independent reviewer: 5 → 6 → 7.5 → 8.5/10. HomeHarvest comps now keep pool/water, MLS #, remarks, year built.
  The six stuck cards get handled on the next tick: Balthazar → sent as is + call card; Carlton, Montas, Kara, Dipietro →
  numbers; Foland → rewrite with the agent's listings. Not verified on the live Asana API yet (html_notes / html_text payloads):
  check the first NUMBERS card. 236 tests.
- 2026-10-26 ~3:30pm ET, RESUME HERE: **"token refused" on Asana, root fix.** Every rewrite used ONLY the token in .env; when it was
  refused, nothing else was tried, though the Mac's own Claude login (keychain) often worked (hence "fixes itself"). Now `claude -p`
  tries the .env token, then the Mac's login, and keeps the one that works for the run; a card fails only if BOTH are refused
  (comment shows both raw errors). Also: .env token wins over a stale one exported in the shell; timeouts no longer dump the prompt
  into the error (it could match "401"/"oauth token" and be misread as a login problem). `make claude-check` tests both logins.
  Stuck Needs Revision cards (Carlton, Balthazar, Montas, Kara, Foland, Dipietro) retry on the next tick. 217 tests.
- 2026-10-26 ~3:15pm ET: first written offer (Lise, $209k) approved by Rodrigo as the template for future offers (recorded
  under Terms). He expects agents to answer faster once texting (Twilio 10DLC) is live.
- 2026-10-26 ~2:45pm ET, RESUME HERE: Rodrigo paused new listings for this strategy (`ingest.paused: true`) to work the ones we
  have (email + offers), and asked to track every offer sent: offer log built (`titan.cli offers`, reports/offers.csv, Scoreboard
  "Offers sent" line with median % of list and per-city stats). Caveat: with the scrape off, stored listings no longer refresh
  (price cuts, pending/sold aren't seen); check status before an offer. 214 tests.
- 2026-10-26 ~2:10pm ET: cloud back-up scheduled task created ("Titan · apply Rodrigo's card comments", trig_01DMPwemtJ2ezjUVbtgi1MkN,
  hourly 8:07am–6:07pm ET, fresh session each run) BUT it stores no connectors (this org can't pass them via the tool): Rodrigo must
  add the Asana connector in claude.ai → Routines, else each run stops with "no Asana tools". Flow PDF for Rodrigo:
  docs/flow/Titan_Flow_2026-10-26.pdf (source docs/flow/flow.html, rendered with Playwright).
- 2026-10-26 ~1:35pm ET, RESUME HERE: Rodrigo is fed up with the Mac login loop. Cloud applied his comments by hand: Manuel Arce,
  Eddy Gervais, Saidelys Garcia, Eugene Schroeder rewritten → To Review (🤖 "Revised" comments so the engine won't redo them);
  Jason Welt → Skip. Auction filter widened (bankruptcy, court approval, trustee's sale, sealed/highest bid, chapter 7/11/13).
  Still open: Richard Dipietro comps + offer number for 2327 Polk St ($4,500/mo); Mac cron token (`make claude-login`, fixed to
  read the clipboard). Proposed to Rodrigo: a cloud scheduled task that applies card comments so Asana never waits on the Mac. 211 tests.
- 2026-10-26 ~1:10pm ET: first `make claude-login` saved HALF a token (setup-token prints it over two lines; input() read one)
  → 401 "OAuth access token is invalid". Fixed: it now reads the token from the Mac clipboard (pbpaste), joins wrapped lines and
  refuses anything under 90 chars. Rodrigo runs `git pull && make claude-login` again (new token each time). 210 tests.
- 2026-10-26 ~1pm ET, RESUME HERE: **root fix for "Not logged in" on Asana.** Cause (proven by the error text): cron passes
  CLAUDE_CODE_OAUTH_TOKEN from .env when it exists; "Not logged in" means it isn't in .env on the Mac (a bad token gives an auth
  error instead). `claude setup-token` only PRINTS the token; it was never saved. It "worked" sometimes because cron can borrow the
  keychain login while Rodrigo is at the Mac. Built: `make claude-login` (runs setup-token, takes the pasted token, strips wrap
  breaks, writes .env, tests the way cron runs), `make claude-check`, `make approvals-now`; failure comments now say exactly which
  case + the fix; one alarm card per day in Claude Updates → Needs Rodrigo (`titan/health.py`). Next: Rodrigo runs
  `make claude-login` at the Mac; then the Needs Revision cards (Manuel, Eddy, Saidelys, Eugene) rewrite; Richard Dipietro asks for
  comps + an offer number on 2327 Polk St ($4,500/mo rent) before answering; Jason Welt = auction → Skip + exclude auctions. 209 tests.
- 2026-10-26 ~1:15pm ET: Lise offer card gets Rodrigo's 3:31pm asks: Cc rodrigo@titanrealtyinvestments.com (his comment had a typo,
  "titanrelatyinvestments") and an opening line that Charles cc'd his acquisitions manager, who approved the numbers. Lead card
  status now says OFFER DRAFTED; Cc support built (card `Cc:` line → Gmail Cc). 206 tests.
- 2026-10-26 ~1pm ET: Rodrigo asked why Asana keeps saying "couldn't rewrite ... Not logged in". Cause: cron can't use the Mac
  keychain login, so it needs CLAUDE_CODE_OAUTH_TOKEN in .env; `make score-test` ran in Terminal (keychain) and passed, hiding it.
  Rodrigo ran `claude setup-token` today (by full path: `claude` isn't on his Terminal PATH). score-test now also says whether
  cron is logged in. Intermittent successes before = keychain unlocked while he was at the Mac. Next: confirm the next tick rewrites.
- 2026-10-26 ~12:30pm ET: Rodrigo asked to apply his feedback to every waiting card. 74 To Review first-email cards rewritten in
  the cloud to the new layout + plain intro + human tone (Jason Welt, C Glansen and the Lise offer card left alone; Eugene and
  Saidelys are in Needs Revision with fresh comments, the Mac's engine rewrites those). Engine now formats every email the same
  way (`format_paragraphs`), new banned phrases, plainer template intros. Pictures on rewritten cards are stale; the text sends.
- 2026-10-26 ~11:30am ET: Rodrigo commented on **Lise's own card** (moved it to Needs Revision) to change the offer: brief thanks,
  30-day close, 9-day inspection, human last paragraph. Applied by hand to the offer card (gid 1219900327903853, To Review) and the
  lead card moved back to Answered. **Gap:** the engine applies a comment on a lead card to that card's ORIGINAL first email, not to
  the pending reply card; feedback on a lead card with a pending reply should rewrite the reply card instead (not built yet).
- 2026-10-26 ~noon ET: **stage 3 started**: the engine writes reply cards itself (check-replies + approvals ticks; one card per
  agent message; skipped when an answer card is already on the board). Comments on a reply card now rewrite the reply (no intro rule,
  validate_reply) instead of overwriting the first email's text (old bug). Expect a card for Richard Dipietro on the next tick. 204 tests.
- 2026-10-26 ~11:30am ET: lead board is LIVE on Asana (Answered: Lise, Richard, Lise reply card). Fixed two wrong reads: Richard's
  rents + broker license # in his signature were read as a $3.6M counter (signature lines and rent amounts no longer count as prices),
  and Lise's "bring your best offer" was filed as a question; `heal_misreads()` re-files old messages on the next check. An offer
  request now shows NEEDS YOU even when a holding reply is drafted. 199 tests.
- 2026-10-26 ~11am ET: **Lise offer card written** ("Offer · Lise Freeman · 529 23rd Ave N · $209,000", gid 1219900327903853, To
  Review, reply to outreach #2): $209k cash as is, 14 day close, $5k deposit, 7 day inspection, we cover closing costs, 30 days
  post-occupancy with $5k escrow holdback, unrepresented (she takes 4%, seller saves 2%). Rodrigo's exit ~$240k → spread ~$26.5k.
  Holding reply moved to Skip. Formatted deal card on Claude Updates (gid 1219896181471379). Sends only when Rodrigo drags it.
- 2026-10-26 ~noon ET, RESUME HERE: built the **trust ladder** (learning from every card decision + knowledge base in the prompts +
  autopilot for first emails behind his go-ahead card) and the **lead board** (Answered column with WHERE WE STAND; Sent renamed
  "Sent · no answer yet"). Both appear on the board within ~30 min of the Mac pulling. Next build: the engine writes reply cards
  itself for info-gathering replies (stage 3 data), so Rodrigo edits those like first emails. Lise still waits on his number. 197 tests.
- 2026-10-26 ~10am ET, RESUME HERE: **Lise's 9:01am reply went out from rodrigo@ (the Mac's Gmail token is Rodrigo's account
  now, which is also why her thread 404'd); Rodrigo: never again.** Guard added (mailbox must equal GMAIL_SENDER; sends refused
  otherwise). Rodrigo must re-auth the Mac as Charles (`rm secrets/gmail_token.json && make gmail-auth`). Lise then replied again
  9:31am: "bring your best offer", seller needs move-out flexibility → ASK_FOR_OFFER (classifier taught "bring your best offer").
  Holding reply card (no number) waits in To Review; "DECIDE A NUMBER" card in Needs Rodrigo with the internal math (ARV ~$482k,
  gut $80–110k, 65/74/82% ratios → ~$190k / $246k / $300k; my read $230–250k + 30 days post-closing occupancy). When Rodrigo
  comments "offer N", write the offer as a reply card on the approvals board. 190 tests.
- 2026-10-26 ~9:50am ET, RESUME HERE: Lise Freeman's reply went out Mon 9:01am ET (first try 404'd on the stale Gmail thread id;
  a parallel session added the fresh-message fallback). **Rodrigo paused sending**: Approved is empty, nothing sends until he drags a
  card there. Sent so far: Thais Guevara + Lise Freeman (Sat 12:44pm, before his edits), Abraham Elbaz + Dror Gerges (Sat 4:05pm;
  Dror bounced), Richard Dipietro (Sun 3:41pm), Lise reply (Mon 9:01am). This commit: **Needs Revision column** (engine creates it;
  commented cards park there until rewritten, then return to To Review; held Approved cards go there) and **agent phones** on cards /
  mirror / website. Still on Rodrigo: cron Claude token (`claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN`), Charles's Gmail token in
  the cloud env, Vercel redeploy check. 188 tests.
- 2026-10-26 7:40am ET, RESUME HERE: Monday report read. 5 emails sent total, 2 real replies, 68 new listings, 25 HOT, 78 cards
  waiting (30 new this morning, intro OK). Lise's answer (card "Reply · Lise Freeman", Approved) goes out 8:30–8:50am ET.
  **Cloud GMAIL_TOKEN_JSON_CONTENT is still Rodrigo's mailbox** (profile = rodrigo@), so the cloud still cannot read/send as
  Charles; replies reach Asana through the mirror. Added `heal_bounces()` (old misread bounces → BOUNCED/NEEDS_CALL, so the Dror
  "counter" stops showing as a conversation) and `first_error` in check_replies counts (28 "partial" runs overnight, cause
  unknown until the log shows it). Still to decide with Rodrigo: auto-send info-gathering replies; "offer N" comment sends the
  written offer; the Conversations board columns. 186 tests.
- 2026-10-25 ~5:45pm ET, RESUME HERE: **Lise Freeman is answered**: her reply (full gut: roof, window AC units, kitchen, bath,
  electrical; seller inherited it) reached Asana through the new conversations mirror; Charles's answer is the card
  "Reply · Lise Freeman · 529 23rd Ave N" (gid 1219880631430547) in **Approved** on the Email Approvals board and goes out from
  Charles's Gmail in the same thread at the first tick Monday 8:30–8:50am ET (a check-in is scheduled for 9:00am ET to verify).
  Built today: (1) conversations mirror (`titan/conversations.py`, every inbound reply → comment on the agent's card, runs with
  check-replies and approvals); (2) **reply cards** (`(reply to outreach #N)` footer; Approved → sent in the same Gmail thread,
  `post_reply_card()`); (3) voice rule from Rodrigo's Eugene Schroeder comment: company name says it all, never "we buy" / "we're
  investors" / "genuinely interested" (`BUYER_RE`, protocol §0/§0b, voice §17); all 48 To Review cards fixed by hand on Asana;
  (4) reply bugs: Gmail's wrapped "On … wrote:" header leaked into reply text (why Lise's reply drew a canned "who are you"
  answer), condition rundowns are INTERESTED, unclear replies get no canned answer, canned replies use the real signature;
  (5) website **Board** tab + `titan/stats.py` (reply rate, answer rate, awaiting answer, needs Rodrigo, daily series, Asana card
  counts, conversations by stage; snapshot synced on every approvals tick too) and a Scoreboard in the morning report;
  (6) `make doctor` checks the cron Claude token and Asana. Critic: `docs/CRITIC_2026-10-25.md` (7.9 now, 8.3 after Rodrigo's items).
  **Rodrigo must do once at the Mac:** `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN` in .env (cron rewrites fail with "Not logged
  in" until then), then `rewrite-pending --all`; paste Charles's `secrets/gmail_token.json` into the cloud's GMAIL_TOKEN_JSON_CONTENT
  (today's value is Rodrigo's own mailbox); check the Vercel site shows the Board tab. Cards on the Claude Updates board say the same.
  185 tests.
- 2026-10-25 ~4:40pm ET, RESUME HERE: **the cloud's `GMAIL_TOKEN_JSON_CONTENT` is Rodrigo's mailbox token, not Charles's**
  (Gmail profile = rodrigo@). Cloud sessions cannot read Charles's inbox or send as Charles until Rodrigo pastes the contents of
  the Mac's `secrets/gmail_token.json` (Charles's token) into that environment variable. Workaround built this session:
  **conversations mirror** (`titan/conversations.py`): every inbound reply (email or text) is posted as a comment on the
  agent's card on the Email Approvals board, with the engine's classification, lead status, internal MAO and the draft answer
  (drafts never send on their own). Runs after every `check-replies` (cron, every 15 min) and `approvals` tick; idempotent via
  `messages.mirrored_at`; a message with no card is marked `no_card`. Lise Freeman's reply (card gid 1219848010908101, Sent
  section) and the two Dror Gerges bounces show up there once the Mac pulls (within ~25 min of this merge). Next: read Lise's
  comment on Asana, classify, write Charles's reply (conversation protocol: one question, no numbers unless Rodrigo approves),
  and either put it on the card for Rodrigo or send it through the Mac. 174 tests.
- 2026-10-25 3:15pm ET, RESUME HERE: Mac is current (cron self-updates every 10 min via `git pull --ff-only`), Claude Code token
  works from cron (`make score-test` OK), all 37 To Review cards rewritten with the intro; Rodrigo was told to run
  `run-daily --steps value && rewrite-pending --all` so the bad "close to asking" lines drop. Rodrigo set GMAIL_TOKEN_JSON_CONTENT
  in the cloud environment (Charles's token JSON): a NEW session should read Charles's inbox with
  `build_clients(cfg, None, need=("gmail",))` → `gmail.search('from:lisefreeman@gmail.com')` / `thread_messages`. **Lise Freeman
  (529 23rd Ave N, Lake Worth Beach, $299,900) replied to Charles and nobody has answered her**: read it, classify, draft Charles's
  reply for Rodrigo, give the internal range if she talks price. Emails are landing in inboxes (not spam). Next build (Rodrigo,
  journal 2026-10-25): a Conversations board on Asana (Engine handling / Needs Rodrigo / Waiting on agent / Closed), every inbound
  reply as a card comment with the engine's answer; decisions pending from Rodrigo: auto-send info-gathering replies? "offer N"
  comment sends the written offer? Also pending: agent ranking (small agents up, Compass/Elliman/Sotheby's/CB down), MAO on the
  card with repair credits, call list for few-photo listings, email-then-text sequence, flip-ratio database.
- 2026-10-25 morning: Mac ran at 5:30am on OLD code (no git pull yet): 30 new To Review cards without the intro and without
  Claude analysis (templates), 11,010 properties, 37 HOT. Report showed two bounces (Dror Gerges, inbox full) misread as
  COUNTER_WITH_NUMBER → fixed (BOUNCE class). Inflated ARVs (multi-family $8M/$15M, land $2M) drove FRESH_DEAL + "close to asking"
  → `max_arv_to_list` guard. No-assignment / no-inspection exclusions added (Rodrigo). Report JSON block now base64. 171 tests.
  Rodrigo is journaling ideas (call list, MAO with repair credits, email-then-text, ratio database); nothing built from those yet.
- 2026-10-24 (afternoon): Rodrigo reviewed the board: 4 cards approved with comments (Richard Dipietro, Abraham Elbaz, Eugene
  Schroeder, Dror Gerges), feedback on Glenn Smith (triplex: wants a rental-yield number) and Douglas Montas (the global
  "introduce yourself first" rule), 4 cards deleted (Alex Salinas, Yuri Skutnik, Ingrid Vengoechea, Brett Colby). Cron rewrites
  failed twice with "no rewrite" (Claude Code not usable from cron; likely no CLAUDE_CODE_OAUTH_TOKEN). This commit: intro-first
  rule (protocol §0, INTRO_RE, templates), revise prompt gets listing facts + both streets required, failed rewrites keep the
  feedback pending and show the reason, rewrite-pending skips feedback-shaped cards and tolerates deleted cards. 168 tests pass.
- 2026-10-24 ~4pm ET: Rodrigo had commented on 14 cards and approved them; the Mac's cron held them all and could not rewrite
  (Claude Code unusable from cron). Claude (cloud) applied all 14 comments by hand on the cards (intro + his asks, bot comment
  "Revised based on your comment" so the engine treats them as handled), moved Ceyda Hergul to Skip, answered Jason Welt
  (auction; Skip). Rodrigo's rule: Asana must work without his computer. Open asks: ARV for Sapphire St (Weston) and lot value for
  511 NW 46th St (Miami 33127) from the Mac comps; a spaced second email for flagged cards (Balthazar) is not built yet.
- 2026-10-24 end of day (5:53pm ET): 4 sent total (Thais Guevara, Lise Freeman 12:44pm; Abraham Elbaz, Dror Gerges 4:05pm) vs
  Rodrigo's 25 target. 21 cards in To Review, every one rewritten in the cloud with the intro and his comments; 0 Approved; 1 Skip.
  Nothing more sends until Rodrigo drags cards to Approved (window reopens 8:30am ET). Comments on the remaining 8 never reached
  Asana (none recorded), so those got the intro only.
- Next on the Mac: `git pull`, then `.venv/bin/python -m titan.cli rewrite-pending --all` (the 30 new cards get intro + analysis),
  then `.venv/bin/python -m titan.cli approvals` (applies the held feedback with the intro), then
  `.venv/bin/python -m titan.cli rewrite-pending --all` (intro on the rest). Add CLAUDE_CODE_OAUTH_TOKEN (`claude setup-token`)
  to .env so the cron rewrites work. Then Rodrigo re-approves; 2 emails go out per 20-min tick until 5:30pm ET.
