# BLOCKERS — things only Rodrigo can resolve

The build environment had **no credentials** (no `APIFY_TOKEN`, `ANTHROPIC_API_KEY`, Gmail OAuth JSON, or Twilio keys), so every step below was built and tested against fixtures, not live data. Nothing here blocks the code; each item blocks turning that step live.

1. **`.env` credentials.** Copy `.env.example` to `.env` and fill in: `APIFY_TOKEN`, `ANTHROPIC_API_KEY`, `GMAIL_OAUTH_JSON` (path to the OAuth client JSON for the *dedicated* outreach mailbox), `GMAIL_SENDER`, `REPORT_TO`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_MESSAGING_SERVICE_SID`, `WEBHOOK_PUBLIC_URL`.
2. **First live scrape needs a 10-minute look.** Run `make run-daily` once. The summary now prints, per source, `raw_count` (items the
   actor returned), `count` (items the mapper understood) and `unmapped`. If `count` is 0 or far below `raw_count`, the log has a line
   *"realtor: mapped only 0 of N raw items … Top-level keys of the first unmapped item: [...]"* — those are the actor's real field names;
   append them to the matching `g(it, "...")` lookup in `titan/ingest/realtor.py` / `zillow.py` (full payload in the newest `raw/*.json`).
   Actor ids and search URL templates are in `config.yaml → ingest.apify` if you prefer different actors. (Verification session 2026-10-23
   had no route to apify.com, so the schemas are still unconfirmed; the diagnostic makes the fix a one-liner.)
3. **Gmail OAuth consent** is interactive: run `make gmail-auth` once on a machine with a browser (it prints a URL), which writes `secrets/gmail_token.json`. Cron then refreshes the token on its own.
4. **Twilio A2P 10DLC registration** must be approved on the Messaging Service before texts deliver; the webhook URL (`<WEBHOOK_PUBLIC_URL>/twilio/inbound`) must be set as the service's inbound request URL and the webhook must be running — on the VPS that is the `titan-webhook` systemd service behind nginx/TLS (`deploy/DEPLOY.md` part B). `WEBHOOK_PUBLIC_URL` in `.env` must match the URL in Twilio exactly or signature validation returns 403. Until then `outreach.channel` should stay `EMAIL_FIRST`. Brand registered 2026-10-26; campaign text and the opt-in form are in `docs/TWILIO_CAMPAIGN.md` (texts go only to agents with a consent record).
5. **Operator inputs.** Price range, formula numbers and EMD are still the defaults from DECISIONS.md #1–#2. Phone and title come from the style guide ((561) 405-7379, Principal). Reply-to is `Charles@titanrealtyinvestments.com` and the postal address is 13075 Marcella Blvd, Loxahatchee, FL (set 2026-10-23). Still to confirm: which Gmail account `GMAIL_SENDER` / `make gmail-auth` should use.
6. **Florida DBPR licensee lookup** (enrichment rung d) hits `myfloridalicense.com` with a plain GET; the site sometimes requires a session cookie. If `make run-daily` logs many `rung_dbpr` warnings, set `enrichment.dbpr_enabled: false`; the other four rungs still run.
7. **VPS access.** Parts B–D of `deploy/DEPLOY.md` (ssh, `scp .env secrets/`, nginx + certbot, security group, Twilio console) need
   Rodrigo's ssh key, a DNS name for the webhook host, and the Twilio console login. The cloud sessions had none of these.
8. **MLS (Matrix) CSV export** — optional, high value: remarks + agent emails in one file. Needs one real export from Rodrigo to write
   the column mapper against (see STATUS.md → Observations).
