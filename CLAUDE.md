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

## Decisions already made
- **Channel: `EMAIL_FIRST`** until Twilio 10DLC is approved (set 2026-10-23; goal: agent email conversations this weekend).
- **Sending mailbox: Rodrigo's "Charles" account** (2026-10-23; exact address goes in `.env` GMAIL_SENDER on the Mac).
- **Draft mode only** (`DRAFT_FOR_APPROVAL`): nothing sends until Rodrigo clicks send in Gmail (label `Titan/Review`).
- **Voice:** `docs/style/RODRIGO_WRITING_STYLE_GUIDE.md` is injected into every drafting prompt; few-shot examples in
  `config.yaml → outreach.example_emails` are his real emails. Signature: Rodrigo Gayoso / Principal | Titan Realty / (561) 405-7379.
- **Terms:** as-is cash, 14-day close, $5,000 EMD, **always keep a 7-day inspection period** (never promise to waive it).
- **Reply-to:** Charles@titanrealtyinvestments.com. Postal address: 13075 Marcella Blvd, Loxahatchee, FL.
- Texting waits on Twilio A2P 10DLC approval. **Channel = EMAIL_FIRST** until then (Rodrigo, Asana 2026-10-23).
- **Sender mailbox = Charles@titanrealtyinvestments.com** (`GMAIL_SENDER`); every email ends with the footer in
  `operator.email_footer` (business name + address + "reply 'remove me'" opt-out, which the STOP classifier catches).
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
- Full list of build-time judgment calls: `DECISIONS.md`. Open items that need Rodrigo: `BLOCKERS.md`.

## Still open (ask once, then record the answer here)
- Offer formula still subtracts repairs + the $15k fee after the market ratio (conservative). The flip ratios already
  reflect what investors paid as-is, so Rodrigo may want repairs/fee dropped or reduced: asked on Asana 2026-10-23.

## Current status
- Merged branch claude/gallant-planck-8fc2qd (Rodrigo OK'd on Asana 2026-10-24): `make gmail-test` (one draft to itself),
  `make gmail-auth` re-prompts on a dead token, engine reads Charles's Gmail token from env `GMAIL_TOKEN_JSON_CONTENT`.
  Notes from that session: this cloud session's Gmail connector is rodrigo@, not Charles; the SQLite DB does not survive
  between cloud sessions (dedup/cooldown state) — persist it before running daily from the cloud.
- 2026-10-23 night (cloud): free scraper confirmed on the Mac (`make scrape-test`: 19/19 read, 17 with agent email).
  Offers use the WhatsApp engine's ZIP investor ratios; sold comps are pulled per subject ZIP; every run ends with a
  plain-English summary. EMAIL_FIRST, Charles@ mailbox + footer. All merged into the default branch; 124 tests pass.
- Rodrigo started the first full Mac run (ingest,score,value,enrich). Next: he uploads reports/dashboard.json → review
  HOT/WARM leads → Gmail drafts in Charles@ (needs Charles's mailbox connected, or `make gmail-auth` on the Mac as Charles).
- Waiting on Rodrigo (Asana): repairs/fee after the ratio, signature name, merge of branch claude/gallant-planck-8fc2qd,
  making titan-whatsapp-sales private.
