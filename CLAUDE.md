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
- **Draft mode only** (`DRAFT_FOR_APPROVAL`): nothing sends until Rodrigo clicks send in Gmail (label `Titan/Review`).
- **Voice:** `docs/style/RODRIGO_WRITING_STYLE_GUIDE.md` is injected into every drafting prompt; few-shot examples in
  `config.yaml → outreach.example_emails` are his real emails. Signature: Rodrigo Gayoso / Principal | Titan Realty / (561) 405-7379.
- **Terms:** as-is cash, 14-day close, $5,000 EMD, **always keep a 7-day inspection period** (never promise to waive it).
- **Reply-to:** Charles@titanrealtyinvestments.com. Postal address: 13075 Marcella Blvd, Loxahatchee, FL.
- Texting waits on Twilio A2P 10DLC approval.
- **Scoring in cloud sessions:** no Anthropic key needed. The engine scores with the rules fallback, then Claude (this session)
  reads the remarks and confirms or corrects the scores. Gmail drafts can be made through the session's Gmail connector.
- Full list of build-time judgment calls: `DECISIONS.md`. Open items that need Rodrigo: `BLOCKERS.md`.

## Still open (ask once, then record the answer here)
- Price range ($150k–$750k) and offer formula (ARV × 0.70 − repairs − $15,000) are still build defaults; confirm with Rodrigo.
- `outreach.channel` is `SMS_FIRST` in config; with Twilio not approved it should likely be `EMAIL_FIRST`.
- Which Gmail account sends (`GMAIL_SENDER`, `make gmail-auth`).

## Current status
- 2026-10-23 evening (cloud, while Rodrigo was at the gym; all merged into the default branch, 115 tests pass):
  HomeHarvest (free Realtor.com scraper, pinned 0.8.18, code reviewed) is the default source + sold comps; it only works
  from the Mac (realtor.com blocks cloud IPs). MLS CSV reader (`raw/mls/`), `make mls-shortlist CSV=…`, Zillow fixed (mapBounds).
- Rodrigo's hot sheet (3,554 rows) → 779 pass filters → top-100 MLS #s sent to him. It lacks agent email/remarks/city.
- Next: Rodrigo exports those MLS #s from Matrix WITH City, Zip, Public Remarks, List Agent name/email/phone, DOM, Original
  List Price → score + value + Gmail drafts. On the Mac: `git pull && make setup && make run-daily`. Asana: connect the
  official connector (custom-URL connector fails: "automatic client registration not supported"); Make has a working one.
- Confirm with Rodrigo: hot-sheet % changes are read as price cuts.
