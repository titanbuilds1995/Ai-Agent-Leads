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
  `daily_email_cap`, then moves the card to Sent. Nothing sends from To Review. **A new comment on an Approved card holds
  the send**: the card goes back to To Review and the feedback is applied at once. "send me a test" (+ optional address) on
  any card sends a [TEST] copy to Rodrigo (REPORT_TO) from Charles@; `titan.cli test-email <outreach_id>` does it by hand.
  **Every email opens with a short intro** (Rodrigo, Asana 2026-10-24, later: "they don't really know who we are"): first paragraph =
  Charles with Titan Realty Investments, this listing came up / stood out (protocol §0, `INTRO_RE` requires the company name; this
  replaced the earlier no-self-intro rule). **Tightened 2026-10-25 (Rodrigo, Eugene Schroeder card): the name says it all. Never
  "we're investors who buy here regularly", never "genuinely interested" (reads as AI), and since we wholesale, never state that we
  are the buyer: "we're looking for properties in X", "what we look for", "our side"; never "we buy" / "we'd buy" / "serious buyers"
  (protocol §0b, voice §17, `BUYER_RE` rejects them).** **Third column "Needs Revision" (Rodrigo, 2026-10-26):** a card he
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
- **Reply-to:** Charles@titanrealtyinvestments.com. Postal address: 13075 Marcella Blvd, Loxahatchee, FL.
- Texting waits on Twilio A2P 10DLC approval. **Channel = EMAIL_FIRST** until then (Rodrigo, Asana 2026-10-23).
- **Sender mailbox = Charles@titanrealtyinvestments.com** (`GMAIL_SENDER`). Signature is exactly `operator.signature_lines`:
  Charles Grant / Junior Acquisitions Agent / Titan Realty Investments / Office Line: +1 (561) 405 7379. **No opt-out footer**
  (Rodrigo, 2026-10-24: it reads as marketing). "Remove me"/stop replies are still caught and honored (do-not-contact).
- **Scoring runs on the Claude Code plan** (`scoring.provider: claude_code`): `claude -p` on the Mac, 20 listings per call,
  API key stripped from the child env so the plan is billed; plan limit → rest by rules, Claude next run. Paid API only with
  `scoring.allow_paid_api: true`. Check with `make score-test`.
- **Charles's Gmail is connected on the Mac** (Rodrigo, 2026-10-24) — don't ask again. **Never send from any other mailbox**
  (Rodrigo, 2026-10-26, after Lise got a reply from rodrigo@ signed Charles): `GmailClient` reads the logged-in profile and refuses
  to start unless it equals `GMAIL_SENDER`. If the token on the Mac is re-authorised as the wrong account, every send stops until
  `rm secrets/gmail_token.json && make gmail-auth` as Charles.
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
- Full list of build-time judgment calls: `DECISIONS.md`. Open items that need Rodrigo: `BLOCKERS.md`.

## Still open (ask once, then record the answer here)
- (none)

## Current status
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
