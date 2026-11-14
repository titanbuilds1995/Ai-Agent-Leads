# Investor contacts + deals from the Facebook / Instagram groups (run on the Mac, Claude in Chrome)

Rodrigo, 2026-11-14: "get any deal in tri county where people are sharing their information, we could save this
for other deals too." Builds one reusable buyers list for Palm Beach, Broward and Miami-Dade, tagged so we can
pull the right people for any deal later. Runs in Rodrigo's own logged-in Chrome.

## Task
Use Claude in Chrome. Go through every Facebook (and Instagram) investor, wholesale and cash buyer group / page
I'm logged into that covers Palm Beach, Broward or Miami-Dade.

1. Read the posts from the last 30 days where someone advertises a deal, says they are buying, or asks for deals.
   Any property type, any price, anywhere in the three counties.
2. **Contacts** → `reports/buyers/fb_investors.csv`. One row per person or company, ONLY what they posted
   publicly for business (post, comments, public profile / bio / page):
   name, company, phone, email, website, role (buyer / wholesaler / both / agent / lender), counties, cities or
   ZIPs they work, property types (single family, condo, townhouse, multifamily, land, commercial), price range,
   strategy (flip / rental / BRRRR / new build / wholesale), group(s) seen in, last post link, last seen date,
   number of posts seen.
   Append only new people (match on phone, email, or name + company); for someone already in it, update
   last seen, post count, and add any new area / type.
3. **Deals** → `reports/buyers/fb_deals.csv`. One row per deal post: date, group, poster (name + company),
   address or area, city, ZIP, county, type, beds/baths, sqft, asking price, ARV and repairs if stated, terms
   (cash / assignment / EMD / closing), post link. Skip duplicates (same address or same post).
   This is market intel: what wholesalers ask vs ARV in each area.
4. At the end print: groups read, posts read, new contacts, total contacts, new deals, and the 10 most active
   buyers per county.

## Rules
- Read only. Never message, comment, like, react, join, friend or follow anyone.
- Skip private profiles, group member lists and anything not posted for business.
- Normal reading pace (a few seconds between pages, stop at 200 posts per run).
  Meta does not allow automated scraping, so a slow pace keeps the account safe.
- If Facebook shows a warning, a captcha or "you're going too fast", stop and tell Rodrigo.
- `reports/buyers/` is gitignored: these lists never go to GitHub.

## Using it for a deal
"From reports/buyers/fb_investors.csv, give me the buyers for <deal>": filter by county / city, type and price
range, most active first.

## Keep it running
Run it again each morning ("run docs/prompts/facebook_investor_contacts.md"); it only adds what is new.
Emailing this list as a group is marketing email: business address + an opt-out line (CAN-SPAM).
