# Cash buyers around a deal: the LLCs, the people behind them, their agents (Mac, Claude in Chrome)

Rodrigo, 2026-11-14 (82 Tortuga Rd, Palm Springs): "look at everyone buying cash in the area … look up the llc and see if
there is a website … emails or phone numbers … another list of the agents that work with these investors … the phone and
email for the actual owner of the llc and anyone affiliated with the llc."

## Before you start (Terminal, in the repo folder)
```
make buyers-area ADDR="82 Tortuga Rd, Palm Springs, FL 33461"
```
Free (realtor.com through HomeHarvest). Writes `reports/buyers/82_tortuga_rd/area_sales.csv`: every house sold in the last
24 months, pending or for sale within 1.5 miles, nearest first, with the listing agent's name / phone / email and a
`signal` (INVESTOR_BUY = sold as-is / cash only / TLC; FLIP_RESALE = sold renovated). Wider: add `RADIUS=2.5 MONTHS=36`.
Re-running it keeps everything the Chrome step already filled in.

## Task (paste into Claude Code on the Mac, with Claude in Chrome)
Work in `reports/buyers/82_tortuga_rd/`. Read-only browsing, normal pace (a few seconds between pages). Save the CSVs
after every 10 rows so nothing is lost. If a site shows a captcha or blocks you, stop that site and say so.

1. **Owners (Palm Beach Property Appraiser, pbcgov.org/papa).** For each row of `area_sales.csv` with an empty
   `buyer_name` (sold rows first, nearest first, at most 150 a run), look the address up and fill in:
   - sold rows: `buyer_name` (the grantee on the latest sale = today's owner), `buyer_mailing`, `deed_date`, `deed_price`,
     and `seller_name` (the grantor on that sale, from the sales history).
   - for_sale / pending rows: `seller_name` = today's owner (an LLC here is an investor selling a flip).
   - `buyer_type` / `seller_type`: LLC, CORP, TRUST, PERSON or INSTITUTIONAL (Opendoor, Invitation Homes, Progress…).
   Then use the appraiser's sales search / map for **off-MLS buys**: sales in the last 24 months within about a mile of
   the deal where the buyer is an LLC / corporation. Add each as a new row (`status` = off_mls, address, city, zip,
   buyer / seller / deed columns; leave the listing columns blank).
2. **Cash or not (Clerk, mypalmbeachclerk.com Official Records).** For the LLC / CORP buyers only: search the buyer's
   name around the deed date. No mortgage recorded within 7 days of the deed → `cash` = yes. A mortgage → `cash` = no
   and the lender in `notes` (a hard-money lender is still an active investor: write "hard money: <lender>").
3. **Buyer's agent.** For sold rows bought by an LLC / CORP: open the sale on Redfin (or Zillow) and read "Bought with" /
   "Buyer's agent" → `buyer_agent`, `buyer_agent_office`; then that agent's phone and email from their realtor.com,
   Redfin or brokerage profile → `buyer_agent_phone`, `buyer_agent_email`. Fill the listing agent's phone / email the
   same way where `listing_agent_email` is blank.
4. **Each LLC on Sunbiz (search.sunbiz.org).** One row per distinct LLC / CORP buyer or seller in `llcs.csv`
   (columns: llc_name, doc_number, status, filed, principal_address, mailing_address, registered_agent, officers,
   related_entities, website, phone, email, social, notes, sources). `officers` as "Name (Title); Name (Title)" from the
   latest annual report. Then search Sunbiz by **officer / registered agent name** for each manager: their other LLCs go
   in `related_entities` (same people = same buyer; note any that also bought near the deal).
5. **Website, phone, email.** For each LLC: Google the exact name, then the managers' names with "real estate" / "homes"
   / the city. Fill `website`, `phone`, `email`, `social` from what the company publishes: its website (contact page,
   footer), Google Business profile, BBB, Facebook / Instagram business page, LinkedIn company page, BiggerPockets.
   Put the link you got each one from in `sources`.
6. **The people** → `people.csv` (name, llc_name, role, address, phone, email, website_or_profile, source, confidence):
   every manager / member / officer / registered agent, plus anyone the website or LinkedIn shows as a partner or
   acquisitions contact. Address from Sunbiz. Phone / email only where the person published it for business (company
   site, LinkedIn, brokerage or agent profile if they are licensed, press release, their own business pages), with the
   `source` link; `confidence` high (on their own site / profile) / medium (third party) / low (guess). Skip
   people-search sites (Whitepages, TruePeopleSearch, Spokeo…). A registered-agent company (e.g. a law firm or
   "Registered Agents Inc") is not a person to call: role "RA service".
7. Run `make buyers-build` and show me `summary.md`: top buyers, the agents who work with them, the people to call
   first, and what is still to look up.

## Rules
- Read only: never message, call, email, sign up or submit a form anywhere.
- `reports/buyers/` is gitignored: these names, phones and emails never go to GitHub.
- No paid lookups. Personal cell phones / emails for the owners need a paid skip trace: Rodrigo decides that (ask him).

## Using the lists
- `cash_buyers.csv`: ranked buying entities (buys near the deal, cash buys, flips they sold near it, last buy, distance,
  whether they buy in the deal's price range) with their Sunbiz people and contact.
- `llc_people.csv`: everyone behind those entities, in the entities' order, contact first.
- `investor_agents.csv`: agents who represented an LLC buyer, listed an LLC's flip, or sold to an LLC, with which LLCs.
- For another deal: `make buyers-area ADDR="<address>"`, the same Chrome step in that deal's folder, `make buyers-build ADDR=...`.
