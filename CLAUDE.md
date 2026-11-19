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
- **Always hand him the FIRST prompt for the Mac (Rodrigo, 2026-11-14: "always give me the first prompt that I need on my mac to
  tell it what I am talking about").** Whenever work has to run on his Mac, end the reply with one ready-to-paste opening prompt for
  Claude Code on the Mac: who he is / the repo, the deal or task in plain words, `git pull` first, the exact make targets and docs to
  follow, the rules that matter (read-only, nothing sent, nothing paid), and what to show him at the end. He should never have to
  explain the context himself.
- **Don't bombard anyone (Rodrigo, 2026-11-15, after Walter got three emails in an hour: "we sent out way too many emails to this
  guy … if they already said excellent or absolutely … we don't email them again. Even if I say so, make it bold and have me approve
  it … for any communication").** Before ANY email to someone outside our domain (engine or a cloud session using the Gmail
  connector), read the thread first. If their last message only acknowledges ours ("Excellent", "Absolutely", "Got it", "Thanks",
  "Will review"), or we already wrote them in the last 24 hours and they have not answered since, DO NOT SEND, even when Rodrigo
  asked for it: tell him in **bold** that this person already answered / was already emailed today, say why another email may not
  be the right move, and send only after he says so again knowing that. Get the facts right BEFORE the first send (verify the price,
  the numbers) and put everything in ONE email; never a stream of corrections. Engine: `titan/contact_guard.py` (Decisions).
- **No bulk-looking email from any of our mailboxes (Rodrigo, 2026-11-16: "a lot of emails that I've sent out to multiple people have
  gone to spam").** Flyers (HTML, pictures), "Reply STOP" / unsubscribe lines, prices or "opportunity" in the subject, the same text to many
  strangers, unverified lists: never, from rodrigo@, Charles@, a script or a cloud session. The domain's reputation is shared. Plain text,
  one person, verified address (`make verify-list FILE=…`). `GmailClient.send` refuses bulk-looking mail (`BulkContent`, no override).
  Rules and the why: `docs/DELIVERABILITY.md`.
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
- **Follow-up SEQUENCE to every silent agent (Rodrigo, 2026-11-02, extended 2026-11-16: "anybody that doesn't answer should be
  getting emails from us. 3 to 4 consecutive emails within a time frame of like 15 days … if they're still active … stress test the
  email … then send it")**: `titan/follow_up.py`, `make follow-ups`, run-daily step `follow_ups` (5:30am, before outreach). Day 0 first
  email; step 1 three days later ("Good morning <name>, Charles with Titan … I emailed you <the other day / last week / a few weeks ago>
  about X", one easy question); step 2 five days after step 1 (a new question the photos can't show, e.g. lived in or vacant, never a
  second ask for a call); step 3 six days after step 2 (the last note, door left open, "anything else you have listed in <city>").
  Config `outreach.follow_up` (`after_days` 3, `sequence_gaps` [5, 6], `max_steps` 3, `auto_send: true`, `max_per_run` 120); table
  `follow_up_steps` (old single follow-ups count as step 1). Before each step the house is looked up on realtor.com (Mac,
  `board_clean.homeharvest_lookup`): active → written; pending → backup lane, sequence stops; sold / pulled → EXCLUDED, stops, one comment;
  can't tell → waits for the next run. Every step goes through `second_read.run` against the emails already sent; only SEND goes to
  Approved (auto_send), anything else / reader unreachable → Needs Me. Stops for good on a real answer or anything else we send them.
  Same lead card (NEXT EMAIL), same Gmail thread, 25–60 words, no price. `ARGS=--dry-run` prints the emails, `--no-check` skips lookups.
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
- **Sending back ON (Rodrigo, 2026-11-16: "we're back onto our regular schedule, sending from 8 a.m. to 6 p.m.")**:
  `sending_paused: false`, send window 08:00–18:00 every day, `daily_email_cap` 120 (for the follow-up sequence). **1031 broker emails
  back on at MEDIUM pace (Rodrigo, 2026-11-17: "let's start that again, but slow it down … from high to medium, so if I get calls I can
  answer them")**: `commercial.outreach.blast_paused: false`, `per_round` 3 / `daily_cap` 60 (was 8 / 220; Nov 10 ran 102 in a day and
  overwhelmed him). OM-request cards go first, then the no-cap queue, inside the send window. Twilio campaign is ready but not in use
  (he said not to worry about it yet). The paragraph below is history.
- **(history) ALL SENDING PAUSED (Rodrigo, 2026-11-11 ~4pm ET: "pause everything right now, no more email sending out … we're sending out
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
- **Cash buyers around a deal (Rodrigo, 2026-11-14, 82 Tortuga Rd: "everyone buying cash in the area … look up the llc … website,
  emails or phone numbers … the agents that work with these investors … the actual owner of the llc and anyone affiliated")**:
  `titan/buyers/` + `docs/prompts/cash_buyers_llc.md`, all on the Mac (the cloud reaches none of these sites). (1) `make buyers-area
  ADDR="…"` (free HomeHarvest): every sale / listing within 1.5 mi, 24 months, with the listing agent's contact. (2) Claude in
  Chrome fills owner / buyer / seller (Property Appraiser + off-MLS LLC buys), cash (no mortgage at the Clerk), buyer's agent
  (Redfin "Bought with"), each LLC on Sunbiz (officers, registered agent, related LLCs by officer name), website / phone / email
  the company publishes, and the people (`people.csv`, business-published contact only, source link each). (3) `make buyers-build`
  ranks cash_buyers.csv / llc_people.csv / investor_agents.csv + summary.md in `reports/buyers/<street>/` (gitignored). No paid
  skip trace unless Rodrigo says so.
- **Contact guard: no email to someone who is done hearing from us (Rodrigo, 2026-11-15, Walter)**: `titan/contact_guard.py`, checked
  inside `GmailClient.send` for every email into an existing thread with an outside address: the thread is read; their last message is
  a short acknowledgement (`is_ack`: excellent / absolutely / thanks / got it / sounds good / will review…, ≤30 words, no question) or
  we wrote them in the last 24 h with no answer since → `ContactHold`, nothing sent. House cards: one bold "**HOLD, not sent: …**"
  comment, card to Needs Me. 1031 replies: the same comment, the draft waits. Only his "send anyway" written AFTER the warning
  (`confirmed_after_hold`; a newer warning needs a new OK) sends it (`send(..., confirmed=True)`); "send anyway" on a house card moves
  it to Approved (comment_router). Fresh first emails (no thread) are not checked. Other senders (pass emails, delay notes) just wait.
- **Deliverability (Rodrigo, 2026-11-16: his emails to many people went to spam; `titan/deliverability.py`, `docs/DELIVERABILITY.md`)**: the
  cause was ~65 identical 140–430 KB HTML flyers sent from rodrigo@ on Nov 4 / 6 / 9 (a script through smtp.gmail.com, then forwards by
  hand) to a scraped owner list: ~15% hard bounces (dead aol / bellsouth / cox / yahoo addresses), "Reply STOP", a `List-Unsubscribe`
  header, a price in the subject. DKIM for the domain passed; content, bounces and repetition did it, and Charles@ shares the domain's
  reputation. Built: (1) `bulk_markers` inside `GmailClient.send`: an email to an outside address that reads as bulk (flyer HTML / big
  picture / STOP-unsubscribe lines / ad subject / list headers / capitals) raises `BulkContent`, nothing overrides it; the approvals send
  posts "STOPPED, not sent" and moves the card to Needs Me. (2) Bounce circuit breaker (`bounce_breaker`, config `outreach.deliverability`:
  7 days, >5% bounces on 20+ sends): approvals stop sending, ONE "⛔ Sending stopped · too many bounces" card a day in Needs Rodrigo
  (`commercial_kv deliverability_breaker:<date>`). (3) `make deliverability` (Mac): the mailbox's sends per day, blasts (same subject to 5+
  people in a day), bounces with the dead addresses, verdict in plain words, plus SPF / DKIM / DMARC with the fix for each (the cloud
  cannot resolve DNS). (4) `make verify-list FILE=list.csv`: clean / dead / unknown split (syntax, duplicates, role mailboxes, dead domains,
  MX, SMTP RCPT through `enrich.default_mx/default_rcpt`) before anyone sends to a list. Not done: DNS of the domain not checked yet
  (needs the Mac); Google Postmaster Tools not set up (his Google Admin).
- **Emails sound like a person, and only say what their copy says (Rodrigo, 2026-11-18: "really robotic … 'with the tenant already in
  place, the house may work for us. What matters on our side is how long the lease runs' … it doesn't sound real … anything we say we have
  to make sure we're getting directly from the copy that they give us … sentences need to be coherent")**: `docs/style/HUMAN_EMAIL.md`
  (research + rules; printed AHEAD of the voice protocol by `compose.voice_text`, so first emails, rewrites, replies, follow-ups and the
  backup lane all get it; it wins over older examples). Every question = WHAT I SAW in the listing, WHAT I DIDN'T SEE, a complete
  question: "I saw there's a tenant in place, but I didn't see when the lease ends. Do you know how long it runs?" / "I saw the price
  hasn't changed in a while. Do you think the seller would negotiate on price if a serious offer came in?" ("a serious offer" is his
  wording and allowed; a figure or "our offer" still is not). Code in compose.py: `HUMAN_RE` / `human_problems()` reject "may work for
  us", "what matters on our side", "with the X already in place, …", "open to some room", "room from asking", "decide our number", "it
  comes down to price", "start listening" (first emails, rewrites, replies, follow-ups); `copy_problems()` (first emails only) rejects a
  tenant / lease the remarks never mention, "price came down" with no cut, "hasn't changed" with a cut, "on the market a while" under 30
  days, and any question the remarks already answer (lease end, roof year, AC year, plumbing replaced, vacant). The writer gets the
  rejection reasons on its retry (3 attempts). Fallback templates rewritten in that shape, new `tenant` angle, and `template_email` only
  uses an option the listing's copy backs (else `safe`). Every approvals tick rewrites up to 15 waiting FIRST emails that break it
  (same path as the photo rule; comment "Rewritten to sound like a person"). Older §0d examples like "The roof and the AC decide our
  number" are superseded.
- **Photo condition check: no fresh renovations in Fresh Leads (Rodrigo, 2026-11-18: "a lot of the fresh leads … are fresh renovations
  and properties that look really good … not going to sell at a discount … go in the listing and look at the pictures and not give me
  those listings")**: `titan/condition.py`, run-daily step `condition` (after board_clean), `make condition-check` (`ARGS=--all` = whole
  board, no daily cap), `make condition-list` (looks, prints, moves nothing), config `condition:`, table `condition_checks`. Per house,
  once: remarks that plainly say fully renovated / new construction (and nothing about work) → RENOVATED for free; otherwise up to 8 of
  the listing's own photos (front, kitchens, baths first; links are in `properties.raw`, a realtor.com address lookup on the Mac when
  not) go to Claude in ONE paid API call (`LLM.read_images`, about 1–3 cents a house) → RENOVATED / GOOD_SHAPE / DATED / NEEDS_WORK /
  CANT_TELL + confidence + one sentence. RENOVATED (high / medium) or GOOD_SHAPE (high only) → house EXCLUDED (`lead_note` "condition:
  …"), its Fresh Leads card to Skip with one "Condition check: …" comment, email never sent; not his vote (`learning.OFF_MARKET`).
  **He drags the card back to Fresh Leads → the house is kept for good** (`kept_at`). Outreach only drafts first emails for houses that
  were looked at (`condition.gate_outreach`); 250 looks a run. Photos not loading / Claude not answering → nothing stored, retried next
  run, stops after 5 in a row. Only Fresh Leads + not-yet-carded houses; Needs Me / Sent / Answered untouched. The email writer is NOT
  told what the photos show (emails still never mention photos).
- **No 55+ communities, no townhouses (Rodrigo, 2026-11-18: "not look for properties in 55 and older communities, and no townhouses
  ... clean out all of the properties I have on the board")**: flags `age_restricted` (55+ / 62+ / age-restricted / senior / active
  adult / HOPA, Kings Point, Century Village, Wynmoor, Huntington Lakes; realtor.com senior tags) and `townhouse` (remarks, type, realtor
  sub type) in `market.exclude_flags`; "townhouse" is out of `market.property_types`. Negations ("NOT a 55+ community", "no age
  restrictions", "not a townhouse") don't count. `exclude_flagged` rules stored houses out on every score run (follow-ups stop).
  `titan/product_clean.py`, run-daily step `product_clean` (after board_clean), `make product-clean` / `make product-clean-list`: cards
  whose houses are all ruled out and whose agent never answered (Fresh Leads, Needs Me, Needs Revision, Approved, Sent · no answer yet) →
  Skip with one "Rule check" comment (not his vote); an agent who answered keeps the card with one comment; he drags one back → kept for
  good (`product_keeps`). Same day the **photo condition check was switched ON** (`condition.daily` + `gate_outreach` true).
- **Links brokers send are opened (Rodrigo, 2026-11-18: "I thought we already worked on being able to log into links from the emails we get
  from agents … they gave us links")**: before this the engine only LISTED links (NEEDS YOU); Tammy Huston's Dropbox for Mariner Plaza sat
  unread. `titan/commercial/link_files.py`, stage `link_files` (after `om_replies` in check-replies and approvals), `make om-links` by hand:
  every document link in a broker's own words (quoted history ignored, Outlook safelinks unwrapped) is opened ONCE on the Mac: Dropbox file
  or folder (dl=1, a folder comes as a ZIP), Google Drive file, Docs / Sheets / Slides (exported), OneDrive / SharePoint, direct PDF / Excel
  URL. PDFs / spreadsheets / docs inside go to `data/listings/<id>/`, thread → BOOKS, "🔗 Opened the link" comment, regrade, and a fresh 🧠
  read when that answer was already read (no_reply). **Never logs in**: data rooms (Revere, Buildout, Crexi, DocSend, Box, ShareFile),
  Drive folders and sign-in pages get one "🔐" comment (open it in your browser, drop the files on the card). Network errors retry, 3
  tries. Table `commercial_link_files`. The cloud cannot reach Dropbox / Drive (proxy 403): this only works on the Mac.
- **Never "congrats", and the clean-up's pass email is second-read too (Rodrigo, 2026-11-18: "we need to stop messaging agents and
  telling them congrats on getting it done we sound like idiots. I thought we are double checking our work")**: the fixed 1031 pass
  said "congrats on getting it done" whenever the broker's email had the bare word "sold" or "leased" ("this is being sold as the
  building", "100% leased"). `DEAD_RE` now needs a plain statement that it is gone (under contract, in escrow, accepted an offer, has
  sold, no longer available); the pass never congratulates ("Thanks for the update on <street>" when it is gone). "congrat" in any
  drafted 1031 reply = NEEDS YOU (`COMMIT_RE`); in a house email it is rejected (`DIRECT_RE`). The gap: `closeout.clear_not_fit` sent
  the pass with NO second read (review_thread's pass was read; Nicholas Costello's was caught there, 11-17). Now `_second_read_ok`
  reads it against the thread first: reader unreachable = waits; objects = never sent, one "HOLD, not sent" comment, card to Needs Me,
  never re-read (`commercial_kv closeout_hold:<thread id>`).
- **1031 round two, with round one's lessons (Rodrigo, 2026-11-18: "look for more commercial properties tomorrow … with everything that
  we learned from this round")**: 208 broker answers on the 1031 boards were read (Nov 9–18): 91 said vacant / owner-user / lease-up, 28
  cap under 8 at asking, 16 already gone, 15 development, 14 gated (NDA / POF / call), 11 a business sold with the building; no card ever
  read FITS; every hotel's books fell from 8.3–10.2% to ~6–7.6% after a manager + tax reset; listings WITH a stated cap answered ~65% and
  sent books ~70%. Built: `titan/commercial/income.py` (in `scoring.in_box`, box step "income"): no-cap listings whose text reads vacant /
  owner-user / lease-up / build-to-suit (and no lease language) are out; a business sold with the building and no lease is out; development /
  land value is out; a hotel's stated cap is screened at `buyer_box.hotel_noi_factor` 0.75; `multifamily_min_units_by_county` (Miami-Dade 20).
  The no-cap question now asks "on the leases in place". No-cap hotels are not asked (`outreach.blast_skip_assets`). `commercial.schedule`
  takes a date: that morning's run-daily pulls LoopNet once (~$7, Apify Starter), posts cards, emails him the deals and builds the no-cap
  queue for brokers never emailed (`build_queue_after_pull`); a broker emailed in round one is never asked again here. **Stop means stop
  (Larry Starr, SVN, 11-18: "Don't contact me again. I will take action against you")**: the Not-a-fit clean-up had sent him the pass on
  11-16 after Rodrigo commented "Stop all communication … I will be emailing him directly". Now `GmailClient.send` refuses any email into a
  thread where they told us to stop (`contact_guard.told_us_to_stop`, `StopContact`, no override, not even "send anyway"); the clean-up
  sends no pass when Rodrigo's card comment says stop / he takes it (`closeout.HIS_STOP_RE`) or the broker said stop (address added to
  do-not-contact; the blast skips do-not-contact); and a rules-only read (Claude unreachable) never decides a pass on its own (Tyler Sluman,
  6805 33rd St E Sarasota, offered $3.65M net / 8 cap once leased and got a rules-only NOT A FIT + "congrats").
- **The bottom line: a last reader settles the numbers, then the offer is drafted (Rodrigo, 2026-11-19, Arkeshia Love / 4108 Greenwood
  Ave: "we need to add another layer, another model … what is the fault in our logic … why isn't the property selling at the price that it
  currently has … a last and final paragraph, bolded … all I need is a conclusion and what price we need to get it at and what's the
  difference … if I'm asking you for a number on this board it's because I want us to draft an email with an offer out at that price")**:
  `titan/verdict.py`, config `verdict:`, tables `verdicts` / `photo_reads`. The engine had said "we need to be at $521,000, the ask is
  fine" on a duplex nobody bought at $372,900 in 228 days. Now, after the NUMBERS snapshot and the 💬 read, a THIRD Claude call (purpose
  `verdict`, paid API) gets his comment, the snapshot, the comps, the 💬 read, the photo read and the rules' findings (`market_test`: a
  house that sat `sitting_days` 45+ at a price is not worth that price; $/sf spread; comps bigger than the house; ARV off the top half;
  borrowed ratio; "full rehab" against a light repair tier) and posts ONE comment, last on the card: "🏁 BOTTOM LINE" in bold: asking,
  worth as it sits, offer, ceiling, $ and % under asking, the conclusion paragraph, what was wrong in our numbers, why it has not sold.
  `settle()` holds every number under the asking price on a sitting listing whatever the model says. **Photos**: on the ask Claude looks
  at up to 12 listing photos for the work it needs (`photo_read`, one paid call, once per house, Mac only; not loading = goes on without).
  **Offer email**: at the bottom-line price, Lise model (terms list: cash, as is, 9-day inspection, $5k deposit, 30-day close; a
  paragraph that owns the number; Cc Rodrigo; nothing attached), second-read, on the SAME card: an unsent first email is replaced
  (variant `offer:verdict`, never regenerated, never autopilot), a lead in conversation gets it as NEXT EMAIL (a waiting answer is not
  overwritten). Always Needs Me: only his drag to Approved sends. **"offer 290k"** as a comment (the number alone) drafts the offer at HIS
  number on that card (`offer_comment`; replaces a waiting draft). Claude unreachable → rules-only bottom line (marked), no offer email.
  This replaces "a number reaches an agent only through approve-offer": the number still only goes when he drags the card.
  **Any comment that asks for an offer at ONE price drafts the offer (same day, Tamara Mason: "it did not read what I told it to do")**:
  "Let's send an offer at $250,000 … our terms 5k escrow, 15 day inspection, 30 day closing or as soon as the title is approved" used to
  go to the email rewriter, whose rules forbid a price: it dropped the offer, kept one side sentence and said "Revised", twice. Now
  `verdict.asked_price` catches it before the rewriter (not a question, a condition, two prices or a bare number), `parse_terms` reads the
  terms he states, and his comment is the writer's instruction. **His standard offer terms: $5,000 escrow, 15 day inspection, cash,
  30 day close** (`verdict.offer_terms`; the older 9-day inspection is superseded). Also fixed: a QUESTION using "price reduction" no longer
  fails the copy check (Caridad Casado's rewrite was refused every round); "The email we send: …" is read as his dictated email (Denise).
  **Offers read like a person wrote them (same day, Denise's card: "this email is absolutely horrible, it seems like an AI email … it
  needs to look like somebody sent it, like it's talking to them")**: the email was his own pasted text ("Offer summary", six label lines,
  an 11-item document checklist, "I'm reaching out" to an agent who had already answered). Now every drafted offer is PROSE
  (`verdict.terms_sentences`: "Our offer is $X, cash. We would take the property as is, put down a $5,000 escrow deposit and close in 30
  days. We would need a 15 day inspection period."), no list / labels / headings, at most one question, thanks first when they already
  wrote (this supersedes the Lise "terms as a short list"). `verdict.form_letter()` names what reads like a form; the offer writer is
  rejected on it, and a dictated email that trips it still goes on the card (his words) but with a bold warning. Rule + before/after in
  `docs/style/HUMAN_EMAIL.md` → "Offers and long emails". Not built: a richer signature / footer (waiting on his answer).
  **CORRECTION, same day: the terms ARE bullet points (Rodrigo: "each term needs to be a set bullet point, not how we have it now")**.
  I had read his "Bullet points." as a complaint and made offers prose; he meant he wants them. Every drafted offer: conversational
  opening, "Our offer:", then five "•" bullets, one term each (price / as is / inspection / escrow / closing; `verdict.offer_terms`),
  the paragraph that owns the number, the closing line. Still no headings, no document checklist, no other list (`form_letter` allows
  up to six bullets). **The card shows the price**: once a bottom line exists (the engine's, or a 🏁 comment written by hand, read by
  `verdict.learn_from_comments`), THE NUMBERS on the card starts "BOTTOM LINE: offer $X · do not go over $Y · $ and % under asking"
  and the engine's first ARV / MAO are no longer shown beside it (`verdict.card_lines`, `house_card._numbers`; Zaira Zavala: "the card
  there is the same as the older one was"). "final analysis" / "clear conclusion" / "the price we need to be at" now count as a
  numbers ask, so they get the bottom line and not another long read.
- Full list of build-time judgment calls: `DECISIONS.md`. Open items that need Rodrigo: `BLOCKERS.md`.

## Still open (ask once, then record the answer here)
- (none)

## Current status
- 2026-11-19 ~4:50pm ET, RESUME HERE: offers use BULLET terms (I had made them prose by mistake); the card's numbers section shows
  the bottom line (Decisions → The bottom line, CORRECTION paragraph). 9 tests in test_verdict; suite passes except the same 3. By hand:
  **Zaira Zavala** (890 E 52nd St) pinned 🏁 bottom line (offer $260,000, ceiling $275,000, $135,000 / 34% under $395,000; depends on
  the house being frame, unverified) + offer email posted as "Answer with: …" for the Mac to put on the card; the contact guard will hold
  it until he says "send anyway" (she already answered). **Arkeshia's $275,000 offer was SENT 3:47pm ET** (list terms, no bullet marks).
  **Tamara Mason**: her agent rejected $250,000; he dictated "$260,000 might be the most we can do …" and dragged the card to Approved at
  4:33pm ET: watch what actually went out. **Denise**: bullet version re-posted. Footer still waiting on his answer.
- 2026-11-19 ~3:45pm ET, RESUME HERE: Rodrigo called Denise's offer email "an AI email". Rewritten short and human and re-posted on her
  card as "Answer with: …" (the Mac's next round replaces the NEXT EMAIL; stays in Needs Me). Offers are prose from now on (Decisions →
  The bottom line, last paragraph); Tamara's and Arkeshia's card offers rewritten the same way by hand. 8 tests in test_verdict; suite
  passes except the same 3. **Open, his answer needed: the footer.** Proposed a fuller plain-text signature (email + address lines);
  a logo / designed footer is held because pictures and HTML are what hurt the domain's spam reputation on Nov 4–9.
- 2026-11-19 ~3:30pm ET, RESUME HERE: Rodrigo asked why cards sit in Needs Revision and why Tamara Mason's rewrite ignored him. Causes
  fixed in code (Decisions → The bottom line, last paragraph); 7 tests in test_verdict, suite passes except the same 3. By hand from the
  cloud: **Tamara Mason** offer at $250,000 on her card (Needs Me), **Arkeshia Love** offer updated to his terms (15 day inspection) and
  back in Needs Me, **Denise Gilbert** his 11-17 offer email re-posted as "Answer with: …" so the Mac's next round makes it the NEXT EMAIL
  (Needs Me). The Mac's database still has the old emails for outreach #64 and #174: the card text is what sends. **Caridad Casado**
  rewrites itself on the Mac's next round after it pulls. **Chevelle Young** (Warm Leads, Needs Revision, comps asked 11-12) is NOT
  explained: the code reads that column; needs the Mac's log (`grep -i chevelle logs/*` / the approvals log around the warm board).
- 2026-11-19 ~3pm ET, RESUME HERE: **bottom line + offer draft built** (Decisions → The bottom line). Tested on fixtures only (5
  tests; suite passes except the same 3 that need packages the cloud lacks); NEVER run live (no database or API key in the cloud). First
  real run: his next "comps" / "numbers" comment on a card after the Mac pulls. Watch: the 🏁 comment is the last one, the offer lands on
  the card in Needs Me, the photo line shows (photos load on the Mac only). **Arkeshia Love (4108 Greenwood Ave) done by hand from the
  cloud**: pinned 🏁 comment (offer $275,000, ceiling $295,000, $97,900 / 26% under the $372,900 ask) and the offer email is on her card
  in Needs Me (Cc Rodrigo); the Mac's database still holds the old question email for outreach #174, the card text is what sends.
  Open: he checks 523 48th St on the MLS (sold fixed up or as is?) before sending; the $100k rehab is an estimate.
- 2026-11-18 ~7pm ET (Mac), RESUME HERE: **first live run of the 55+ / townhouse clean-up and the photo check, done.**
  `make product-clean`: 4,282 houses ruled out (3,227 typed townhouse), 43 cards to Skip, 4 cards kept with a note (other house
  fine), 1 kept because the agent answered (Denise Gilbert, 275 Jacaranda Dr, townhouse, Needs Revision). `make condition-check
  ARGS=--all` ($2.88 + $0.21 list): 476 looked at, 162 renovated / 133 good shape / 148 dated; 10 Fresh Leads cards to Skip, 181
  houses ruled out before a card. Nothing waiting for a check. Nothing sent. Close call worth a glance: 18010 NW 48th Ct (one bath
  still old). From now on the 5:30am run does both.
- 2026-11-18 (evening), RESUME HERE: **55+ / townhouse filter built + photo condition check switched ON** (Decisions → No 55+
  communities). Rodrigo asked why good-shape houses are still on Fresh Leads: the photo check was on HOLD since 11-17. Now on. First run
  on the Mac: `git pull`, `make product-clean-list`, `make product-clean`, `make condition-check ARGS=--all` (~$10 for ~1,466 houses).
  Then every 5:30am run does both. 2 tests; suite passes except the same 3 that need packages the cloud lacks. Not run live (cloud has no DB).
- 2026-11-18 ~7:25am ET: morning check only. **No 7am report**; EOD Tue 0 of 25 (follow-ups never ran). Flags card: opendoor / beycome
  agents in HOT, review summaries misread street numbers as seller numbers (Juan Huice '$214,000'), land on Fresh Leads. Waiting on Rodrigo.
- 2026-11-17 ~11pm ET (Mac), RESUME HERE: **1031 round two RAN** (`make commercial` by hand after Rodrigo put a new Apify token in the
  Mac's .env; $5.68). 1,253 listings, 82 never seen before, 14 of those in the box; in-box went 543 → 214: the income screen took out
  290 (236 vacant / owner-user / lease-up, 25 development, 15 hotels under 8 after manager + tax, 10 small Miami-Dade multifamily, 4
  business with the building). 38 cards posted to New, all no-cap (NEEDS-OM); NO new listing shows a cap of 8+. 38 gone, 10 price
  drops. `make commercial-blast`: 23 brokers queued for the cap question (3 a round / 60 a day, from 8am); Larry Starr not in it.
  `commercial.schedule` set back to "manual" (the pull is done; no second charge on the 19th). Watch: the queue entry for
  dawn@pfeiferrealtygroup.com (Sanibel) has no street; three older 8+ listings have no card because they are SUSPECT (Mohammad Hasan
  x2, Michael Grant 9022 Midnight Pass Rd whose back-solved cap is 99.9 = bad data). The cloud's APIFY_TOKEN still needs the new token.
- 2026-11-17 ~10pm ET (Mac clock; the cloud's dates run a day ahead), RESUME HERE: **1031 round two has NOT run: the Mac's Apify token
  is refused** (`make doctor`: "Apify token fail, token rejected (401)"). Rodrigo regenerates it in console.apify.com and puts it in
  the Mac's `.env` himself, then `make doctor`, then `make commercial` once (or the 5:30am run on the date in `commercial.schedule`
  does it), then `make commercial-blast`; set the schedule back to "manual" after the pull so it is not paid twice. Tonight's
  `make commercial` attempt started no Apify run ($0), but the engine went on with 0 listings: it marked all 543 in-box listings gone,
  recorded a "full" run and emailed Rodrigo an empty deals email. Undone by hand in the database (543 back to active, the run row and
  its snapshots removed). Fixed in `commercial/run.py`: a pull that brings nothing stops with an error (nothing stored, marked gone,
  posted or emailed), and a pull with an empty family marks nothing gone. 1 new test, suite passes. Blast queue: 0 waiting (not
  rebuilt: stored listings have not been through the new income screen). Larry Starr is not in the queue.
- 2026-11-18 (evening), RESUME HERE: **1031 round two scheduled for tomorrow 2026-11-19** (Decisions → 1031 round two). The Mac's 5:30am
  run-daily pulls LoopNet (`commercial.schedule: "2026-11-19"`; the cloud's Apify token is refused, so only the Mac can pull), re-scores
  with the new income screen, posts cards to New, emails Rodrigo the deals, queues the no-cap question for new brokers (3 a round / 60 a
  day). Rodrigo drags the stated-cap cards (the best answerers) to "Approved · send OM request". Stop-means-stop + no pass from a
  rules-only read built after Larry Starr's threat. 3 tests; suite passes except the same 3 that need packages the cloud lacks. NOT run
  live. After tomorrow set `commercial.schedule` back to "manual" (it only fires on that date anyway). Open: reopen Tyler Sluman?
- 2026-11-18, RESUME HERE: **"congrats" removed + the Not-a-fit clean-up now goes through the second reader** (Decisions → Never
  "congrats"). Cause: the pass template read "being sold" / "leased" as a closed deal, and the clean-up's pass skipped the second read.
  3 new tests; suite passes except the same 3 that need packages the cloud lacks. Not checked: how many brokers already got it (Charles's
  Sent folder, Mac): `sqlite3 titan_leads.db "SELECT to_address, sent_at FROM outreach WHERE body LIKE '%congrat%' AND status='sent'"`.
- 2026-11-18, RESUME HERE: **broker links are now opened** (Decisions → Links brokers send are opened). Tested on fixtures (4 tests; suite
  passes except the same 3 that need packages the cloud lacks). Never run live: the cloud is blocked from Dropbox. First real run on the
  Mac: `make om-links` (prints every link and what happened). Known waiting: Tammy Huston's Mariner Plaza Dropbox (Nov 13, NAI Talcor
  thread filed under 415 Richard Jackson Blvd's listing, since that is the thread she answered in). Revere CRE data room (Sleep Inn) needs
  his login: stays his.
- 2026-11-17 ~8:40pm ET (Mac), RESUME HERE: **photo condition check tried live, list only** (`make condition-list ARGS="--limit 20"`,
  nothing moved, $0.13). First try: every photo failed (the Mac's Python has no root certificates: CERTIFICATE_VERIFY_FAILED); fixed
  in `condition._download` with certifi. Second try: 20 looked at (19 by photos, 8 each, the 640x480 size loads; 1 from the remarks):
  6 RENOVATED (would leave), 4 GOOD_SHAPE medium (stay), 9 DATED, 1 can't tell (renderings on a land listing). **HOLD until Rodrigo
  says go:** `condition.daily: false` (run-daily skips the step) and `gate_outreach: false`; when he says go, set both true and run
  `make condition-check`. The 20 looks are saved, so they are not paid for again. Open: 2 of the 6 are "medium" calls with rental-grade
  finishes (405 NW 64th St, 11801 SW 180th St); a 2021 build is called RENOVATED; 1,466 houses still waiting (about $10 for all).
- 2026-11-18 (later), RESUME HERE: **photo condition check built** (Decisions → Photo condition check). Tested on fixtures only (5
  tests; suite passes except the same 3 that need Google / Anthropic packages the cloud lacks). NEVER run live: the cloud has no
  database, no API key and cannot reach realtor.com's pictures. First real run is on the Mac: `make condition-list ARGS="--limit 20"`
  (looks at 20, prints, moves nothing), then `make condition-check`. Unverified until then: that the stored October photo links still
  load, which picture size loads (three are tried), and how strict Claude's eye is (tune `condition.drop`). Not built: the verdict
  line on the house card; a look at houses already in Sent (their follow-ups still go).
- 2026-11-17 ~8pm ET, RESUME HERE: **scoring's switch-off no longer reaches the email writer.** A plan limit, 5 scoring failures or a
  missing `claude` CLI set `llm.provider = "rules"` for the run, and every first email / rewrite / reply after that fell back to
  templates even though communication runs on the paid API. Now `LLM.scoring_available` (the plan; scoring.py uses it) is separate
  from `LLM.available` (true whenever the paid-API writer can run); `structured()` sends non-score purposes to the API first. Only
  `scoring.provider: rules` / `TITAN_LLM=rules` turns Claude off altogether. 1 new test, suite passes on the Mac. Watch the next
  run-daily: new Fresh Leads cards should be Claude-written, not templates. Also today: `make follow-ups` ran, nothing to send (only
  36 first emails ever sent; 650 wait in Fresh Leads; Stefania Anchorena is due but her listing lookup keeps coming back "can't tell").
- 2026-11-17 evening (Mac), RESUME HERE: **human-email rules tried with the live writer** (dry run, 5 HOT houses, read-only, nothing
  posted or sent, $0.04): 5 of 5 written by Claude, no rejections, no template fallback. BUT the real morning runs wrote **templates
  only** (Nov 14: 31, Nov 16: 100, Nov 17: 118 cards, 0 by Claude). Cause found, NOT fixed yet (waiting on Rodrigo's OK):
  `scoring.py` sets `llm.provider = "rules"` after 5 failed plan logins (the Mac's Claude login answers 401 / "Not logged in"), and
  run-daily hands that same switched-off client to outreach, so first emails never reach the paid API. Fix = keep the scoring
  switch-off away from communication calls (`_always_api`), and `claude` → /login on the Mac. Still open in the drafts: "new listing"
  said about houses first seen Oct 23–24 (stored days-on-market is frozen since ingest was paused), "back on the market" taken from
  the scraper flag with no history behind it, and 2–3 questions in one email. All 10 HOT houses already have an email on file.
- 2026-11-18, RESUME HERE: **human-email rules built** (Decisions → Emails sound like a person). Tested on fixtures (2 new tests;
  suite passes except 3 that need Google / Anthropic packages this container lacks, same before the change). NOT tried with the live
  Claude writer (no API key in the cloud): the first real run is the Mac's next approvals round, which also starts rewriting waiting
  Fresh Leads cards that break the rules, 15 a round. Watch: the new cards' wording, and how many fall back to templates
  (`claude first touch rejected` in logs = the checks are too strict somewhere). Also fixed a stale test for yesterday's blast setting.
- 2026-11-17 ~12:30pm ET, RESUME HERE: Rodrigo said his messages "paused and don't go". Checked: house sending works (8 went Mon,
  Denise's reply handled today at noon; Approved column empty, nothing stuck). What was still held was the **1031 no-cap blast queue**:
  now ON at medium pace (`blast_paused: false`, 3 per round / 60 a day; Decisions → Sending back ON). The Mac is 1 commit behind
  ("Not possible to fast-forward": a local commit on the Mac, likely titan.sh's CLAUDE.md auto-merge); the cron pull is `--ff-only`,
  so he runs `git pull --no-edit` once at the Mac. The 7am run-daily did not report today, so the follow-up sequence has not had its
  first real run yet: `make follow-ups` on the Mac. Watch: first blast sends (3 every 20 min from the next approvals round), calls to him.
- 2026-11-17 ~7:25am ET: morning check only. **No 7am report** (Mac likely asleep; Mon EOD arrived: 8 of 25, sending back on).
  EOD email went out twice (00:28 / 00:29Z): overlapping runs, worth a once-per-day claim. Walter's flood question still open.
- 2026-11-16 ~1pm ET, RESUME HERE: **spam diagnosis + deliverability guards built** (Decisions → Deliverability). Cause found in rodrigo@'s
  own headers (flyer blasts Nov 4/6/9, ~15% bounces, scraped list). Rodrigo runs `make deliverability` on the Mac once to see the DNS
  (SPF / DKIM / DMARC) and Charles's mailbox numbers; sets up Google Postmaster Tools. 6 new tests, 428 total, all pass. Open: whether
  "Rodrigo at XR Holding" is a second mailbox on another domain (only rodrigo@titanrealtyinvestments.com was readable from here).
- 2026-11-16 ~10am ET, RESUME HERE: **sending back on 8am–6pm** + **follow-up sequence built** (Decisions → Follow-up SEQUENCE). First real
  run: Rodrigo runs `make follow-ups` on the Mac now; after that run-daily does it every morning. Tested on fixtures (8 tests, all pass);
  never run on the real board. Watch: how many come back "can't tell" (realtor.com lookups), how many the second reader holds in Needs Me,
  and the 120/day cap (oldest first; the rest go tomorrow). 1031 blast still held. Contacts workbook now has a "Tortuga investor agents" tab.
- 2026-11-16: Rodrigo asked to see every contact from the Facebook run + the owners around Tortuga. The lists live only on
  the Mac (gitignored), so built `titan contacts` / `make buyers-contacts`: one workbook reports/buyers/ALL_CONTACTS.xlsx (FB investors,
  FB deals, Tortuga people / LLCs / owners / agents), opens it and emails it to REPORT_TO from Charles@ (`make buyers-contacts-email`;
  `titan contacts` = emails). Only to Rodrigo, never to a contact. Tested on fixtures.
- 2026-11-16 ~7:25am ET: morning check only. **No 7am report** and no board activity since Nov 15 morning (Mac asleep; EOD Sun did
  arrive). Walter asked Sun 11:13am "flood zone … is this east of I-75?" about 331 John Ringling: unanswered; Needs Rodrigo card with
  facts (west of I-75, Lido Key, likely FEMA AE, flood policy in Larry's package) and a one-line answer for him to send if he wants.
- 2026-11-15 ~11:30am ET: **contact guard built** (Rules for every session + Decisions → Contact guard), after the three emails to
  Walter in an hour. Engine holds any email into a thread whose person already acknowledged us or got an email from us in the last
  24 h, with a bold warning; only "send anyway" after it sends. 5 tests, all pass. Not live-tested (the Mac picks it up on its
  10-min update; sending is still paused anyway).
- 2026-11-15 ~11am ET: **331 John Ringling Blvd (St. Armands, Sarasota; Larry Starr, SVN) sent to Walter** on Rodrigo's word, from
  rodrigo@ in the "Alexander Oaks deal sheet" thread (cc Cat, Taylor): 2-page flyer (rent roll, 3% bumps, NNN, roof = landlord) + both
  executed leases + rent schedule. No price or cap in it (ask/LOI never shown to the investor); OM not attached (its disclaimer bars
  third parties). Flyer copy in reports/commercial/ (gitignored). Next: Walter's answer; Larry still owes the seller's reply to the LOI.
  Follow-up sent same thread on Rodrigo's word: asking is $4.9M per the listing (the OM's $6.65M is old), "in negotiation with the
  seller", cap at asking 7.7% yr 1 / 8.0% yr 2 / 8.2% yr 3; updated flyer shows the asking + cap row.
  Real LoopNet asking (Rodrigo checked): $4,999,000, not $4.9M; then asked to send the correction: sent same thread with the corrected flyer ($4,999,000; cap 7.6% / 7.8% / 8.0% yrs 1–3) + both leases + rent schedule.
- 2026-11-15 ~7:20am ET: morning check only. No 7am report yet (Mac is up: 5:30am clean-up moved 5 cards to Skip, EOD sent 9pm Sat;
  last Sunday's report came 11:21am). Needs Rodrigo card posted. Sending still paused; no new comments or agent replies.
- 2026-11-14 afternoon: BOTH jobs running live on the Mac in Chrome (`titan tortuga`: 848 houses pulled, now owners / Sunbiz; `titan fb`: restarted from 32 contacts / 30 deals). Next: he sends summary.md + the top FB buyers, then pick who to call for Tortuga; skip trace still his call.
- 2026-11-14: one-word launcher `scripts/titan.sh` (alias titan): `titan fb`, `titan tortuga`, `titan status`; saved job prompts in docs/prompts/start/. He kept pasting prompts into plain bash.
- 2026-11-14: `make buyers-status` added (how far the Tortuga LLC run and the Facebook run got; the cloud cannot see the Mac).
- 2026-11-14: rule added: every Mac step comes with a ready-to-paste opening prompt (Rules for every session). Gave the
  one for the Tortuga cash-buyer run.
- 2026-11-14, RESUME HERE: **cash-buyer LLC system built** (Decisions → Cash buyers around a deal), tested on fixtures only (2
  tests). First real run is on the Mac: `make buyers-area`, then the Chrome prompt, then `make buyers-build PRICE=269900`. Open
  for Rodrigo: a paid skip trace for the owners' personal cells / emails (top 25 LLC people, he decides). Supersedes the "WAIT for
  instructions" line below.
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
