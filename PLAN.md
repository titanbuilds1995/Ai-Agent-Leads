# PLAN — Titan On-Market Discount Lead Engine

Build order (each step must pass its acceptance test before the next):
scrape → store → score → value → rank → enrich → outreach drafts → reply classifier → reply responder → report → cron.

Legend: [x] done, [ ] pending, [~] done but only testable against fixtures until a credential arrives (see BLOCKERS.md).

## 0. Scaffold
- [x] `config.yaml` holds every tunable (counties, price range, formula, thresholds, caps, repair $/sqft, email terms, actor ids, model).
- [x] `.env.example` lists every credential; `.env` is git-ignored; nothing in the code prints a secret.
- [x] `titan/config.py` loads YAML + .env, validates required keys, exposes a single `Config` object.
- [x] `titan/db.py` creates `titan_leads.db` with tables `properties, agents, scores, valuations, outreach, threads, messages, do_not_contact, daily_runs, llm_usage`.
- Acceptance: `python -m titan.cli init-db` twice creates the schema once (idempotent); `pytest tests/test_db.py` passes.

## 1. Ingest (scrape)
- [~] `titan/ingest/apify.py`: run an actor synchronously with retry/backoff, page through dataset items, save raw JSON to `raw/` for debugging.
- [x] `titan/ingest/realtor.py`, `zillow.py`: map actor output (tolerant to field-name variants) to the common `Listing` dataclass.
- [x] `titan/ingest/redfin_csv.py`: fallback parser for Redfin "Download All" CSV.
- [x] `titan/ingest/normalize.py`: address normalization key, status flag detection (short sale / REO / auction / pre-foreclosure / probate / estate), price-history parsing, price-cut count, back-on-market.
- [x] Filters: target counties, price range, property types, new or price-reduced within `lookback_days`.
- Acceptance: `pytest tests/test_ingest.py` maps both actor shapes + CSV to identical `Listing`s; a failed actor does not stop the other actor or downstream scoring. Live acceptance (needs APIFY_TOKEN): `make run-daily` stores ≥ 1 listing per county.

## 2. Store (dedupe)
- [x] Property keyed on normalized address; re-run updates price/DOM/history, never duplicates.
- [x] Agents keyed on (normalized name, brokerage); phone normalized to E.164; office-line flag when the same number appears for ≥ `office_line_min_agents` agents at one brokerage.
- Acceptance: ingesting the same fixture twice leaves row counts unchanged and updates `last_seen`.

## 3. Motivation scoring
- [x] `titan/llm.py`: one module for all Claude calls, structured JSON output (`output_config.format` json_schema), retry wrapper, token + cost counter persisted to `llm_usage`, deterministic rules fallback when no key / API error / refusal.
- [x] `titan/scoring.py`: 0–100 score with cited phrases, positive + negative signals, structured boosts (DOM>60, 2+ cuts, back on market, vacant, flags).
- Acceptance: `pytest tests/test_scoring.py`: fixture "cash only as-is estate sale, price reduced" scores ≥ 65; "fully renovated turnkey multiple offers" scores ≤ 25; every score has ≥ 1 cited phrase when ≥ 40.

## 4. Valuation + offer price
- [x] `titan/valuation.py`: sold comps (0.5 mi, ±25% sqft, same type, last 6 months) → median $/sqft × subject sqft, condition adjustment; repair tier from remarks with $/sqft from config; MAO = ARV × pct − repairs − fee; spread = (list − MAO)/list. Vacant land handled by $/acre fallback.
- [x] Comps source: Apify sold-listings actor; fallback to listing's own estimate (Zestimate) when no comps; records `arv_method` and `comp_count`.
- Acceptance: `pytest tests/test_valuation.py`: known comps produce the expected ARV; repair tiers map to the configured $/sqft; MAO formula matches config.

## 5. Ranking
- [x] `titan/ranking.py`: HOT = motivation ≥ 65 and list ≤ MAO × 1.20 (or list ≤ MAO); WARM = motivation ≥ 50 and list ≤ MAO × 1.35; else COLD. Rules read from `config.yaml`.
- Acceptance: `pytest tests/test_ranking.py` covers each branch and boundary.

## 6. Agent contact enrichment
- [~] `titan/enrich.py`: chain (a) payload email → (b) brokerage website → (c) realtor.com / zillow / homes.com agent profiles → (d) Florida DBPR licensee search → (e) pattern inference from known emails at that brokerage, verified by MX + SMTP RCPT handshake (no mail sent). Confidence stored per hit.
- Acceptance: `pytest tests/test_enrich.py` with mocked HTTP/DNS covers each rung and the stop-at-first-hit rule; office-line detection flagged.

## 7a. SMS outreach (Twilio)
- [~] `titan/outreach/sms.py`: < 160 chars, varied wording, sender + company, one listing per text; respects daily SMS cap, 9am–6pm ET weekdays, ≤ 3 unanswered texts per agent; STOP → do_not_contact immediately.
- [~] `titan/webhook.py`: FastAPI endpoint for Twilio inbound; classifies and responds per §8; text-to-email handoff flow in `titan/replies/sms_handoff.py`.
- Acceptance: `pytest tests/test_sms.py`: window/cap/STOP/3-unanswered rules; handoff sends offer email when email on file, else asks for email, parses reply, sends, confirms by text, marks HOT_LEAD; 2 business days without email → NEEDS_CALL.

## 7. Email outreach
- [~] `titan/outreach/email_drafts.py`: plain-text, sender voice from examples, references property + one remark detail, local cash buyer in [county], asks if seller is open below list and what number works, 14-day as-is close; varied wording; per-agent batching; 7-day per-agent rule; daily cap; do-not-contact honored.
- [~] `titan/outreach/gmail_client.py`: drafts with label `Titan/Review` in DRAFT_FOR_APPROVAL; send + log thread id in AUTO modes.
- Acceptance: `pytest tests/test_outreach.py`: no HTML, no links other than address, cap enforced, same agent not emailed twice in 7 days, two listings for one agent produce one email, DNC respected, 20 drafts are pairwise non-identical.

## 8. Reply handling
- [x] `titan/replies/classifier.py`: INTERESTED / ASK_FOR_OFFER / COUNTER_WITH_NUMBER / NOT_INTERESTED / SOLD_OR_PENDING / QUESTION / OUT_OF_OFFICE / STOP (rules first, Claude for ambiguous when a key exists).
- [x] `titan/replies/responder.py`: ASK_FOR_OFFER → number at/just below MAO with terms; COUNTER within 10% of MAO → HOT_LEAD; else one bridge halfway then escalate; QUESTION → facts from listing/config only; INTERESTED → HOT_LEAD, stop auto-reply; STOP → DNC; SOLD → close; OOO → wait. Never beyond config terms.
- [~] `titan/replies/inbox.py`: every 15 min poll tracked threads; DRAFT mode saves replies as `Titan/Review` drafts.
- Acceptance: `pytest tests/test_replies.py` runs the 30-reply fixture (all 8 classes + tricky ones) and asserts class + action for each.

## 9. Morning report
- [~] `titan/report.py`: 7:00am ET email + `reports/YYYY-MM-DD.md`: top 10 HOT_LEADs (address, list, MAO, agent last message in one line, agent name/phone, link, the one thing to say), pipeline counts, cost, scrape-health warning (< 50% of usual), BLOCKERS.md contents.
- Acceptance: `pytest tests/test_report.py` renders from fixture DB; file written; email body plain text.

## 10. Ops
- [x] CLI: `init-db run-daily check-replies report status install-cron webhook gmail-auth`.
- [x] Makefile targets wired to the CLI; `make install-cron` writes cron lines (5:30 daily, */15 replies, 7:00 report, weekday SMS window).
- [x] Logs in `logs/`, rotating; every external call retried with backoff; a failed scrape never blocks scoring of stored listings.
- Acceptance: `make status` prints the funnel; `make install-cron --dry-run` prints valid crontab lines; `pytest` passes.

## 11. End-to-end + self-review
- [~] Full run in DRAFT_FOR_APPROVAL on live data (blocked on credentials → run on fixtures), REVIEW.md with findings, fix, rerun.
- [x] STATUS.md, DECISIONS.md, BLOCKERS.md, README with 10-line turn-on summary.
