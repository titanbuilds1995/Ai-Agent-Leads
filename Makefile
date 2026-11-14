PY := .venv/bin/python
.PHONY: buyers-status buyers-area buyers-build clean-board clean-board-list site-1031 om-strays buyer-brief commercial-round commercial-sheets commercial commercial-sample commercial-offline follow-ups eod-report comps-loop comps-check claude-login claude-check approvals-now setup test init-db run-daily check-replies report status install-cron webhook gmail-auth gmail-test doctor dashboard sync-dashboard demo clean-demo

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

wake-evening:     ## wake the Mac at 6:55pm every day so the 7pm end-of-day report goes out (one-time, asks for your Mac password)
	sudo pmset repeat wakeorpoweron MTWRFSU 05:25:00 wakeorpoweron MTWRFSU 18:55:00
	@echo "Done. The Mac wakes at 5:25am and 6:55pm. Keep it plugged in; a closed MacBook lid only wakes if an external screen or keyboard is attached."

automate:         ## daily 5:30am run + 7am report email, and wake the Mac at 5:25 (asks for your Mac password once)
	$(PY) -m titan.cli install-cron
	sudo pmset repeat wakeorpoweron MTWRFSU 05:25:00
	@echo "(the Mac must also be awake at 7pm for the end-of-day report: add that wake with 'make wake-evening')"
	@echo "Done. Keep the Mac plugged in. Check: crontab -l ; pmset -g sched"

claude-login:     ## log the background jobs in to Claude (token saved in .env, tested like cron); fixes "Not logged in" on Asana
	env -u ANTHROPIC_API_KEY $(PY) -m titan.cli claude-login

claude-check:     ## is the background jobs' Claude login working? (tests the token in .env like cron)
	env -u ANTHROPIC_API_KEY $(PY) -m titan.cli claude-login --test-only

approvals-now:    ## run one approvals round now (rewrites commented cards, sends approved ones in the window)
	$(PY) -m titan.cli approvals

follow-ups:       ## Good morning follow-up (same thread) to every agent who never answered: cards in Needs Me
	$(PY) -m titan.cli follow-ups

books-review:     ## read every broker answer + the books on file for Rodrigo now (runs on every round on its own); make books-review ARGS=--all redoes them
	$(PY) -m titan.cli books-review $(ARGS)

clean-board:      ## Fresh Leads clean-up now: houses under contract / sold / off market move to Skip (runs every morning too)
	$(PY) -m titan.cli clean-board

clean-board-list: ## only print which Fresh Leads houses are no longer for sale (moves nothing)
	$(PY) -m titan.cli clean-board --list

backup-lane:      ## pending houses that fit our criteria: write the backup emails (Fresh Leads), draft check-ins, watch for back-on-market
	$(PY) -m titan.cli backup-lane

backup-list:      ## only print which pending houses are in the backup lane and who is ready for an email (changes nothing)
	$(PY) -m titan.cli backup-lane --list

learn-loop:       ## daily learning loop now: your skips / edits / approvals, the answers, the offers -> outreach order + the writer's playbook
	$(PY) -m titan.cli learn-loop $(ARGS)

learn-check:      ## print what the loop learned so far (scorecard, playbook, weights); runs nothing
	$(PY) -m titan.cli learn-loop --list

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

commercial-sample: ## 1031 buyer screen, cheap sanity check: ~15 LoopNet rows per asset family, 10 scored listings over 3+ counties printed (pennies on Apify)
	$(PY) -m titan.cli commercial --sample 15 --no-asana

commercial:       ## 1031 buyer screen, FULL statewide LoopNet pull → reports/commercial/<date>/ (master.xlsx, top25_1031.pdf, broker_contacts.csv) + Asana cards. Costs Apify credit.
	$(PY) -m titan.cli commercial --full --email

commercial-offline: ## re-score the last saved LoopNet rows without pulling: make commercial-offline RAW="raw/2026-11-08_*loopnet*.json"
	$(PY) -m titan.cli commercial --no-asana --from-raw $(RAW)

commercial-blast: ## queue the no-cap question for every in-box broker without a cap rate (one email per broker); the approvals rounds send it from Charles@
	$(PY) -m titan.cli commercial-blast --build --list

commercial-sheets: ## the top listings still missing their LoopNet sheet + the prompt for a Claude in Chrome session on this Mac (make commercial-sheets TOP=15)
	$(PY) -m titan.cli commercial-sheets --top $(or $(TOP),15)

commercial-round: ## one round of the 1031 board now: send the OM requests in "Approved · send OM request", read the brokers' answers, file the books
	$(PY) -m titan.cli approvals

om-strays:        ## find broker answers that came as a NEW email (not a reply) and put them on their 1031 cards; prints who
	$(PY) -m titan.cli om-strays $(ARGS)

site-1031:        ## republish the 1031 page's data from this Mac's database (no Apify spend): the process tab, deals, columns
	$(PY) -m titan.cli commercial-site

buyer-brief:      ## one-page PDF for the 1031 buyer and his agent: what we found, this week's plan, what we need from them → reports/commercial/
	$(PY) -m titan.cli buyer-brief


ADDR ?= 82 Tortuga Rd, Palm Springs, FL 33461
RADIUS ?= 1.5
MONTHS ?= 24
buyers-area:      ## free: every sale / listing around a deal + listing agents → reports/buyers/<street>/area_sales.csv (ADDR="..." RADIUS=1.5 MONTHS=24). Next: docs/prompts/cash_buyers_llc.md in Chrome
	$(PY) -m titan.buyers area "$(ADDR)" --radius $(RADIUS) --months $(MONTHS)

buyers-status:    ## how far the buyer jobs got (Tortuga LLC run + Facebook groups run)
	$(PY) -m titan.buyers status x

buyers-build:     ## rank the cash buyers (LLCs), the people behind them and their agents from what the Chrome step filled in (ADDR="..." PRICE=269900)
	$(PY) -m titan.buyers build "$(ADDR)" $(if $(PRICE),--price $(PRICE),)
