PY := .venv/bin/python
.PHONY: eod-report comps-loop comps-check claude-login claude-check approvals-now setup test init-db run-daily check-replies report status install-cron webhook gmail-auth gmail-test doctor dashboard sync-dashboard demo clean-demo

setup:            ## create venv + install deps (python3.11)
	test -d .venv || uv venv --python 3.11 .venv || python3.11 -m venv .venv
	$(PY) -m pip install -q -r requirements.txt 2>/dev/null || uv pip install --python $(PY) -r requirements.txt
	$(PY) -m pip install -q -r requirements-scraper.txt 2>/dev/null || uv pip install --python $(PY) -r requirements-scraper.txt || echo "WARN: HomeHarvest (free scraper) did not install; the other sources still work"
	test -f .env || cp .env.example .env
	$(PY) -m titan.cli init-db

test:
	$(PY) -m pytest -q

init-db:
	$(PY) -m titan.cli init-db

run-daily:        ## 5:30am: scrape → score → value → rank → enrich → outreach
	$(PY) -m titan.cli run-daily

check-replies:    ## every 15 min: read mailbox, act on replies, flush queued texts
	$(PY) -m titan.cli check-replies

report:           ## 7:00am: reports/YYYY-MM-DD.md + email
	$(PY) -m titan.cli report

eod-report:       ## the end-of-day report now (moves off Fresh Leads, deletes, calls, emails, the 25-a-day goal); 7pm ET on its own
	$(PY) -m titan.cli end-of-day

status:
	$(PY) -m titan.cli status

install-cron:
	$(PY) -m titan.cli install-cron

cron-preview:
	$(PY) -m titan.cli install-cron --dry-run

webhook:          ## Twilio inbound endpoint on :8080 (expose with ngrok or the VPS IP)
	$(PY) -m titan.cli webhook

gmail-auth:       ## one-time OAuth for the outreach mailbox
	$(PY) -m titan.cli gmail-auth

gmail-test:       ## one test draft in the outreach mailbox, to itself (sends nothing)
	$(PY) -m titan.cli gmail-test

dashboard:        ## open the dashboard in your browser from this machine (live, no password, no Vercel)
	$(PY) -m titan.cli dashboard

sync-dashboard:   ## push the dashboard snapshot to Vercel Blob now (also runs after every run-daily / check-replies / report)
	$(PY) -m titan.cli sync-dashboard

mls-shortlist:    ## rank an MLS hot sheet: make mls-shortlist CSV=~/Downloads/hotsheet.csv
	$(PY) -m titan.cli mls-shortlist "$(CSV)"

scrape-test:      ## quick live check of the free scraper: make scrape-test (or LOC="Hollywood, FL")
	$(PY) -m titan.cli scrape-test "$(or $(LOC),33023)"

automate:         ## daily 5:30am run + 7am report email, and wake the Mac at 5:25 (asks for your Mac password once)
	$(PY) -m titan.cli install-cron
	sudo pmset repeat wakeorpoweron MTWRFSU 05:25:00
	@echo "Done. Keep the Mac plugged in. Check: crontab -l ; pmset -g sched"

claude-login:     ## log the background jobs in to Claude (token saved in .env, tested like cron); fixes "Not logged in" on Asana
	env -u ANTHROPIC_API_KEY $(PY) -m titan.cli claude-login

claude-check:     ## is the background jobs' Claude login working? (tests the token in .env like cron)
	env -u ANTHROPIC_API_KEY $(PY) -m titan.cli claude-login --test-only

approvals-now:    ## run one approvals round now (rewrites commented cards, sends approved ones in the window)
	$(PY) -m titan.cli approvals

comps-loop:       ## comp-quality loop now: back-test the comp rules on real sales, tune, re-value the board, Claude review
	$(PY) -m titan.cli comps-loop

comps-check:      ## measure comp accuracy only (no tuning, no Claude, nothing posted)
	$(PY) -m titan.cli comps-loop --no-tune --no-claude --no-asana

score-test:       ## score 3 stored listings through Claude Code (your plan, no API spend)
	env -u ANTHROPIC_API_KEY $(PY) -m titan.cli score-test

doctor:           ## check every credential + setting the live run needs; prints pass/fail and the fix for each
	$(PY) -m titan.cli doctor

demo:             ## full pipeline on fixture data, no credentials needed, into demo_titan_leads.db
	TITAN_DB=demo_titan_leads.db $(PY) -m titan.cli run-daily --fixture tests/fixtures/listings_sample.json --comps-fixture tests/fixtures/sold_comps_sample.json --offline
	TITAN_DB=demo_titan_leads.db $(PY) -m titan.cli report --no-email --print
	TITAN_DB=demo_titan_leads.db $(PY) -m titan.cli status

clean-demo:
	rm -f demo_titan_leads.db demo_titan_leads.db-*
