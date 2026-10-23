# Titan On-Market Discount Lead Engine

**To turn it on (10 lines):**
1. `make setup` — creates `.venv` (Python 3.11), installs deps, copies `.env.example` → `.env`, creates `titan_leads.db`.
2. Fill `.env`: `APIFY_TOKEN`, `ANTHROPIC_API_KEY`, `GMAIL_OAUTH_JSON`, `GMAIL_SENDER`, `REPORT_TO`, `TWILIO_*`, `WEBHOOK_PUBLIC_URL`. Never commit it. `make doctor` tells you what is still missing or wrong.
3. Edit `config.yaml → operator` (your real cell, mailing address) and paste 2–3 of your real agent emails into `outreach.example_emails`.
4. `make gmail-auth` once (prints a URL, authorize the **outreach** mailbox, token lands in `secrets/`).
5. `make run-daily` — first live scrape → score → value → rank → enrich → drafts. Check `raw/` and the printed summary (see BLOCKERS.md #2).
6. `make report` and open `reports/YYYY-MM-DD.md`; drafts are in Gmail under `Titan/Review` and in `reports/drafts/<date>/` (texts).
7. `make webhook` on the always-on machine (port 8080) and point the Twilio Messaging Service inbound URL at `<WEBHOOK_PUBLIC_URL>/twilio/inbound`.
8. `make install-cron` — 5:30 run-daily, every 15 min check-replies, 7:00 report, 9:05 weekday outreach pass (all ET).
9. **Flip modes** in `config.yaml → outreach.mode`: `DRAFT_FOR_APPROVAL` (default) → `AUTO_SEND_FIRST_TOUCH` (day 2–3) → `FULL_AUTO` (day 7). Channel: `outreach.channel` = `SMS_FIRST` | `EMAIL_FIRST` | `BOTH`. Both can be overridden per run with `TITAN_MODE=... TITAN_CHANNEL=...`.
10. `make status` any time for the funnel; `make test` runs the 91 offline tests; `make demo` runs the whole pipeline on fixture data with no credentials.

VPS install (systemd webhook + nginx/TLS + cron) and the Mac go-live checklist: **`deploy/DEPLOY.md`**.

---

## What it does

Every morning it pulls new and price-reduced listings in Miami-Dade, Broward and Palm Beach (Realtor.com + Zillow via Apify, Redfin CSV fallback), stores them once per address in SQLite, scores seller motivation 0–100 with Claude (cited phrases, rules fallback), estimates ARV from sold comps, prices repairs by tier, computes MAO, ranks HOT / WARM / COLD, finds the listing agent's email (5-rung chain with SMTP verification), and texts or emails the agent a short first touch in Rodrigo's voice. Replies are classified into 8 classes and answered strictly from `config.yaml` terms; anything positive becomes a HOT_LEAD and lands in the 7am report with the one thing to say on the call.

```
titan/
  config.py            config.yaml + .env → one Config object
  db.py                SQLite schema, idempotent upserts, funnel
  llm.py               the only Claude caller: JSON schema output, retry, cost counter, rules fallback
  ingest/              apify.py (actor runner) · realtor.py · zillow.py · redfin_csv.py · normalize.py · pipeline.py
  scoring.py           motivation score + cited phrases
  valuation.py         comps → ARV, repair tier, MAO, spread, offer price
  ranking.py           HOT / WARM / COLD rules
  enrich.py            agent email chain (payload → brokerage site → portals → DBPR → pattern + MX/SMTP)
  outreach/            compose.py (drafts + validators) · send.py (plan, caps, modes, queue) · gmail_client.py · twilio_client.py
  replies/             classifier.py · responder.py · inbox.py (poller, handoff, webhook entry)
  webhook.py           FastAPI: /twilio/inbound, /twilio/status, /health
  report.py, status.py, cli.py
tests/                 91 tests; fixtures/listings_sample.json (24 listings, both actor shapes), replies.json (30 replies)
```

## Commands

| make target | what |
|---|---|
| `run-daily` | ingest → score → value → rank → enrich → outreach (each step isolated; a dead scrape never blocks scoring) |
| `check-replies` | poll tracked Gmail threads, act on replies, flush texts queued outside 9–6 ET, time out stale email handoffs |
| `report` | write `reports/YYYY-MM-DD.md` and email it to `REPORT_TO` |
| `status` | funnel + recent steps |
| `install-cron` / `cron-preview` | write (or print) the crontab block |
| `webhook` | Twilio inbound endpoint |
| `gmail-auth` | one-time OAuth |
| `doctor` | check every credential, setting and network path the live run needs; prints ok / warn / fail and the fix for each |
| `demo` / `clean-demo` | full pipeline on fixtures into `demo_titan_leads.db` |
| `test` | pytest |

`python -m titan.cli run-daily --steps score,value,outreach --force` re-runs a subset. `--fixture` / `--comps-fixture` use local JSON instead of Apify.

## Modes and channels

| mode | first touch (email / sms) | 7a written offer after a positive text | reply to an agent |
|---|---|---|---|
| `DRAFT_FOR_APPROVAL` | Gmail draft under `Titan/Review` / text saved in `reports/drafts/` | Gmail draft (the "just sent it" text is **not** sent) | Gmail draft / text saved |
| `AUTO_SEND_FIRST_TOUCH` | sent | sent, then confirmation text | Gmail draft / text saved |
| `FULL_AUTO` | sent | sent | sent |

Hard rules that hold in every mode: daily caps (`daily_email_cap`, `daily_sms_cap`), one email per agent per 7 days with their listings batched, one text per agent per day, texts only 9am–6pm ET Mon–Fri, max 3 unanswered texts per agent, office lines never texted, STOP/unsubscribe → `do_not_contact` instantly on every channel, no HTML/links/dollar figures in first touches, replies never promise beyond `outreach.terms`.

## Reply rules (section 8)

| class | action |
|---|---|
| INTERESTED | HOT_LEAD, auto-reply off, Rodrigo takes over |
| ASK_FOR_OFFER | reply with offer at MAO × (1 − `offer_below_mao_pct`) + terms (email); text → 7a handoff |
| COUNTER_WITH_NUMBER | within `counter_accept_within_pct` of MAO → HOT_LEAD; else one bridge halfway, then escalate (HOT_LEAD, note) |
| QUESTION | answered only from config facts (inspection, closing costs, POF, EMD, timeline, cash, who we are); anything else → NEEDS_CALL |
| NOT_INTERESTED | polite close (email), thread closed |
| SOLD_OR_PENDING | closed |
| OUT_OF_OFFICE | wait |
| STOP | do_not_contact, cancel queued outreach, no reply |

## Tuning

Everything numeric is in `config.yaml`: counties, price range, property types, lookback, `formula` (ARV ×, fee, EMD, close days, offer discount, counter tolerance), `repairs` $/sqft per tier, `valuation` comp rules, `ranking` thresholds, `scoring` weights + model, `outreach` caps/window/mode/channel/examples, `enrichment` knobs, actor ids, cron hour, model pricing for the cost counter.

## Files the build left for you
`PLAN.md` (task list with acceptance tests), `DECISIONS.md` (every judgment call), `BLOCKERS.md` (what needs you), `STATUS.md`, `REVIEW.md` (self-review findings and fixes), `deploy/` (DEPLOY.md runbook, systemd unit, nginx site, VPS bootstrap script).
