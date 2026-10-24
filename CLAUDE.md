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
  Charles with Titan Realty Investments, investors who buy in this area and like it, this listing stood out (protocol §0, `INTRO_RE`
  requires it; this replaced the earlier no-self-intro rule). A "couldn't rewrite" comment does NOT consume feedback (retried next
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
- **Charles's Gmail is connected on the Mac** (Rodrigo, 2026-10-24) — don't ask again.
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
- **Targeting (Rodrigo, 2026-10-24):** scrape every active listing (free; `ingest.homeharvest.scope: all_active`, $50k price
  bands), then `titan/targeting.py` lanes: **SITTING** (60+ days; ideal = with price drops / back on market) and
  **FRESH_DEAL** (≤14 days and list ≤ internal high +5% on real comps; lock it in fast); the rest WATCH. Ranking, Claude
  scoring order (cap `scoring.max_claude_calls_per_run`) and outreach order follow the lanes. Outreach stays focused on HOT.
- Full list of build-time judgment calls: `DECISIONS.md`. Open items that need Rodrigo: `BLOCKERS.md`.

## Still open (ask once, then record the answer here)
- (none)

## Current status
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
- Next on the Mac: `git pull`, then `.venv/bin/python -m titan.cli approvals` (applies the held feedback with the intro), then
  `.venv/bin/python -m titan.cli rewrite-pending --all` (intro on the rest). Add CLAUDE_CODE_OAUTH_TOKEN (`claude setup-token`)
  to .env so the cron rewrites work. Then Rodrigo re-approves; 2 emails go out per 20-min tick until 5:30pm ET.
