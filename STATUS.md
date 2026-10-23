# STATUS — COMPLETE (build) · VERIFIED (fresh install) · BLOCKED (live run, needs Rodrigo's credentials)

Updated: 2026-10-23 04:40 UTC

## Done
- Every step in PLAN.md is implemented and covered by tests: **91 tests, all passing** (`make test`, ~2.5 s, no network, no API key).
- **Re-verified on a clean machine (2026-10-23)**: fresh clone → `make setup` (Python 3.11.17 venv via uv) → `make test` → `make demo` all pass.
  Demo on fixtures: 24 listings in → 16 kept (8 filtered) → 15 scored → 8 valued → 1 HOT / 2 WARM / 5 COLD → 2 text drafts in
  `reports/drafts/<date>/` → morning report. (0 email drafts in the demo: with `--offline` enrichment is skipped and the only fixture
  agent with an email has no HOT/WARM listing. Email drafting itself is covered by `tests/test_outreach.py`.)
- Self-review done twice (REVIEW.md); 7 fixes applied.
- Docs: README (10-line turn-on), DECISIONS (23 judgment calls), BLOCKERS, PLAN, **deploy/DEPLOY.md** (Mac go-live + VPS runbook).
- **2026-10-23 additions**
  - Ingest now self-diagnoses the first live scrape (BLOCKERS #2): each Apify source reports `raw_count` / `count` / `unmapped` in the
    run summary, and when the mapper rejects most items it logs the actor's real top-level field names, so the fix is a one-line
    addition to `titan/ingest/realtor.py` / `zillow.py`. Zillow mapper also accepts the nested `listingAddress.*` / `listingPrice.amount`
    shape that current docs for `maxcopell/zillow-scraper` describe. Numeric parsers ignore dict/list values instead of mangling them.
  - `make doctor` (`titan/doctor.py`): checks .env keys, config placeholders, DNS to Apify/Anthropic, Apify token, Anthropic key + model,
    Gmail token (and that it is the outreach mailbox), Twilio account + Messaging Service inbound URL, webhook /health; prints the fix per row.
  - `deploy/`: `titan-webhook.service` (systemd, binds 127.0.0.1:8080), `nginx-titan.conf` (TLS front, `/twilio/*` + `/health` only),
    `bootstrap_vps.sh` (re-runnable: python3.11, clone/pull, `make setup`, `make test`, install + start the service; never touches
    `.env`/`secrets/`), `DEPLOY.md`.

## Not verifiable without credentials (see BLOCKERS.md)
- Live Apify scrape, live Claude scoring, Gmail drafts/sends, Twilio sends/webhook. Each client is wired, retried with backoff, and
  degrades gracefully (missing creds are listed in the run summary and the report; the pipeline keeps going).
- The build and verification sessions ran in cloud containers with **no credentials, no browser and no route to apify.com**, so the
  actor output schemas could not be checked against the mappers; the diagnostic above replaces that check on the first live run.
- Items marked `[~]` in PLAN.md pass their fixture tests and need one live run to confirm field names / OAuth / 10DLC.

## Running
Nothing. Cron and the webhook service are installed by `deploy/bootstrap_vps.sh` + `make install-cron` on the VPS.

## Next (Rodrigo, interactive — deploy/DEPLOY.md parts A–B)
1. `.env` + `config.yaml → operator` (real cell, mailing address, your example emails). Paste secrets yourself; never into a chat.
2. `make gmail-auth` → `make run-daily` → read the summary (`raw_count` vs `count` per source) and the drafts once.
3. VPS: `bash deploy/bootstrap_vps.sh`, `scp .env secrets/`, nginx + certbot, Twilio inbound/status URLs, `make install-cron`.
4. Week-one path: `DRAFT_FOR_APPROVAL` → `AUTO_SEND_FIRST_TOUCH` (day 3) → `FULL_AUTO` (day 7).

## Observations from the verification run (not bugs, decide with Rodrigo)
- A **HOT-rated listing whose agent is an office line with no email gets no outreach and appears only in `make status`** (demo: 362 SW 38th
  Ave, Homestead, score 100, agent shares a Coldwell Banker number with 2 others). Options: list "HOT but uncontactable" in the 7am
  report for a manual call, or let enrichment (rungs b–e) find the email first — it will on live runs, where enrichment is not skipped.
- MLS (Matrix) CSV mapper (task step 8) was **not** built: no sample export was available and Matrix column names vary by MLS. When
  Rodrigo exports one, drop it in `raw/mls/` and add `titan/ingest/mls_csv.py` next to `redfin_csv.py` (same `parse_dir` shape),
  register it in `pipeline.run_sources`; MLS remarks + agent emails would replace most Apify spend.

## API cost so far
$0.00 — no Claude calls were possible without a key; the cost counter (`llm_usage` table, printed in every report) is tested with the rules provider.
