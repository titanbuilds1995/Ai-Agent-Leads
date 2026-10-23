# DEPLOY — from "tests pass" to "running every morning"

Two machines: **the Mac** is where Rodrigo fills credentials, authorizes Gmail and reviews the first live run;
**the VPS** (the old claw-bot AWS instance) runs cron and the Twilio webhook. Code moves only through GitHub;
`.env` and `secrets/` move only through `scp`.

Everything below that touches a credential or sends anything is a human step. The engine stays in
`DRAFT_FOR_APPROVAL` until step C.

---

## A. Mac — go live locally (≈ 30 min, interactive)

Already verified on this branch: `make setup`, `make test` (91 passing), `make demo`.

**Run `make doctor` after every step below.** It checks each key, the config placeholders, the network path to Apify and Anthropic, the Gmail sign-in, Twilio and the webhook, and prints the fix for whatever is still wrong. When it ends with *ready for a live run* you are done with part A.

1. **Credentials → `.env`** (paste them yourself; never read them into a chat, never commit):
   | key | where it comes from |
   |---|---|
   | `APIFY_TOKEN` | console.apify.com → Settings → Integrations |
   | `ANTHROPIC_API_KEY` | console.anthropic.com (API key; separate from the Claude subscription) |
   | `GMAIL_OAUTH_JSON` | Google Cloud Console → APIs → Gmail API enabled → OAuth client, type *Desktop app* → download JSON → save as `secrets/gmail_oauth_client.json` |
   | `GMAIL_SENDER` | the dedicated outreach mailbox, e.g. `offers@titanrealtyoffers.com` |
   | `REPORT_TO` | where the 7am report goes |
   | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_MESSAGING_SERVICE_SID` | console.twilio.com → Account info; Messaging → Services |
   | `WEBHOOK_PUBLIC_URL` | the VPS URL from part B step 3, e.g. `https://hooks.titanrealtyoffers.com` (leave blank until then) |
2. **`config.yaml → operator`**: real `sender_phone` (the Twilio/outreach cell, E.164), real `mailing_address` (CAN-SPAM).
   **`outreach.example_emails`**: replace the three samples with 2–3 emails you actually sent to listing agents.
   Review with DECISIONS.md #1–#3: `market.price_min/max`, `formula.*`, `ranking.*`.
3. `make gmail-auth` — prints a URL; open it, pick the **outreach** mailbox, approve. Writes `secrets/gmail_token.json`.
4. **First live run**: `make run-daily`. Read the printed summary:
   - `ingest.sources.realtor` / `.zillow` now show `raw_count`, `count` (mapped) and `unmapped`. If `count` is 0 while
     `raw_count` is not, the log line *"mapped only 0 of N raw items"* lists the actor's real top-level keys; add them to
     `titan/ingest/realtor.py` / `zillow.py` (every field lookup is a `g(it, "name", "alt_name", …)` list; append the real
     name). The full payload is in the newest `raw/*.json`.
   - `score.claude` should be > 0 (otherwise the key is wrong and rules scored everything).
   - `value.valued` > 0 and `rank` counts.
5. `make report` → open `reports/<today>.md`; read 10 scored listings against their remarks; open Gmail label
   `Titan/Review` and `reports/drafts/<today>/`. Tune `config.yaml` (weights, thresholds, examples), not code, unless a
   draft breaks a hard rule (HTML, link, dollar figure, > 160 chars in a text).
6. Commit `config.yaml` changes (they contain no secrets) and push.

## B. VPS — install (≈ 20 min)

1. `ssh <user>@<vps>`, then:
   ```bash
   git clone https://github.com/titanbuilds1995/Ai-Agent-Leads.git ~/Ai-Agent-Leads
   cd ~/Ai-Agent-Leads
   TITAN_DIR=$PWD bash deploy/bootstrap_vps.sh        # python3.11 + uv, make setup, make test, systemd webhook
   ```
   The script is re-runnable. It stops short of cron until `.env` is real.
2. **From the Mac**, copy the credentials (never via git):
   ```bash
   scp .env <user>@<vps>:~/Ai-Agent-Leads/.env
   scp -r secrets <user>@<vps>:~/Ai-Agent-Leads/
   ssh <user>@<vps> 'chmod 600 ~/Ai-Agent-Leads/.env ~/Ai-Agent-Leads/secrets/*'
   ```
   The Gmail token refreshes itself from the VPS; no browser is needed there.
3. **TLS in front of the webhook** (nginx + certbot):
   ```bash
   sudo apt-get install -y nginx certbot python3-certbot-nginx
   sed "s/__WEBHOOK_HOST__/hooks.yourdomain.com/" deploy/nginx-titan.conf | sudo tee /etc/nginx/sites-available/titan
   sudo ln -sf /etc/nginx/sites-available/titan /etc/nginx/sites-enabled/titan && sudo nginx -t && sudo systemctl reload nginx
   sudo certbot --nginx -d hooks.yourdomain.com
   curl -fsS https://hooks.yourdomain.com/health
   ```
   Prerequisites: an A record for `hooks.yourdomain.com` → the VPS public IP; security group inbound 80 + 443 from
   anywhere (and 22 from your IP only). Port 8080 stays closed — uvicorn binds 127.0.0.1.
   *First-test shortcut*: open 8080 in the security group temporarily and use `http://<ip>:8080` as the URL; Twilio
   accepts http. Close it again once TLS is up.
4. Set `WEBHOOK_PUBLIC_URL=https://hooks.yourdomain.com` in the **VPS** `.env` (and the Mac's, for parity), then
   `sudo systemctl restart titan-webhook`.
5. **Twilio console** → Messaging → Services → your service → Integration:
   - *Incoming messages*: "Send a webhook", request URL `https://hooks.yourdomain.com/twilio/inbound`, HTTP POST.
   - *Delivery status callback*: `https://hooks.yourdomain.com/twilio/status`.
   - A2P 10DLC: Brand + Campaign must show **Approved** before any text leaves; until then texts fail with error 30034.
   Signature validation is on: a request that is not signed with your auth token for exactly `WEBHOOK_PUBLIC_URL/twilio/inbound`
   gets a 403, so the URL in Twilio and in `.env` must match character for character (scheme, host, no trailing slash).
6. **Cron**: `make install-cron` then `crontab -l`. Expected block (America/New_York):
   ```
   CRON_TZ=America/New_York
   30 5 * * *     run-daily
   */15 * * * *   check-replies
   0 7 * * *      report
   5 9 * * 1-5    run-daily --steps outreach
   ```
   Output goes to `logs/cron.log`; app log is `logs/titan.log`; webhook log is `journalctl -u titan-webhook -f`.
7. Smoke test: `make status` on the VPS, `curl https://hooks.yourdomain.com/health`, and text the Twilio number
   from your own phone — the inbound shows up in `journalctl -u titan-webhook` and in the `messages` table.

## C. Week one — turning the autopilot on

Mode lives in `config.yaml → outreach.mode` on the VPS (edit, no restart needed; cron reads it each run).

| day | mode | what you do |
|---|---|---|
| 1–2 | `DRAFT_FOR_APPROVAL` | read every draft in `Titan/Review` / `reports/drafts/`; send the good ones by hand; tune config |
| 3 | `AUTO_SEND_FIRST_TOUCH` | first touches go out on their own (inside 9–6 ET weekdays for texts); replies still land as drafts |
| 7 | `FULL_AUTO` | replies are sent from templates; INTERESTED / counters become HOT_LEADs in the 7am report |

Caps (`daily_email_cap`, `daily_sms_cap`) start at 25; raise them once bounce and STOP rates are known.

## D. Ongoing deploy flow

Develop on the Mac → push to GitHub → on the VPS: `cd ~/Ai-Agent-Leads && git pull && make test && sudo systemctl restart titan-webhook`.
Cron picks up code changes on the next run automatically. Back up `titan_leads.db` before schema-touching pulls
(`cp titan_leads.db backups/titan_leads.$(date +%F).db`).
