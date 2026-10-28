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
- Full list of build-time judgment calls: `DECISIONS.md`. Open items that need Rodrigo: `BLOCKERS.md`.

## Still open (ask once, then record the answer here)
- (none)

## Current status
- 2026-10-28 ~2pm ET, RESUME HERE: **comp engine rebuilt + daily comp loop** (Rodrigo: "the comps are just incorrect … fix the engine …
  run a loop daily"). Built from the real misses on the cards: townhouse vs pool houses (Castro), split layout + waterfront lot sales
  (Carlton), $3.1M new build next to a 1942 duplex (Dipietro), unpermitted 4-bed in 1,166 sf (McCarthy). New `titan/comps_engine.py`
  (product match, exclusions, similarity ranking, layout discounts, grade A–D) used by valuation and the snapshot; `titan/comps_loop.py`
  runs inside run-daily (step `comps`, after value, 5:30am on the Mac) → back-test on real sales, tuning, re-value the board, Claude
  review (15/day), "Comps" section in the 7am report. **Nothing to install: the Mac pulls the code on its 10-min self-update; the first
  loop runs tomorrow 5:30am** (`make comps-loop` on the Mac runs one now; `make comps-check` only measures). Grades and "Comp check"
  appear on cards at their next rewrite / numbers snapshot. 281 tests. Next: read tomorrow's Comps section; if the back-test error is
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
