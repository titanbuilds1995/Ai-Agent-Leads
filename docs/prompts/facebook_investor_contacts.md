# Investor contacts from the Facebook groups (run on the Mac, Claude in Chrome)

Rodrigo, 2026-11-14: collect the phone numbers and emails other investors post in the local investor /
cash buyer groups, to build our West Palm buyers list. Runs in Rodrigo's own logged-in Chrome.

## Task
Use Claude in Chrome. Go through the Facebook (and Instagram) investor, wholesale and cash buyer groups / pages
I'm logged into that cover Palm Beach, Broward or Miami-Dade.

1. Read the posts from the last 30 days where someone advertises a deal or says they are buying.
2. For each person or company, record ONLY what they posted publicly for business, in the post, its comments
   or their public profile / bio / page: name, company, phone, email, website, areas they buy in, property
   type, price range, whether they are a buyer, a wholesaler or both, the group name, the post link and date.
3. Save to `reports/buyers/fb_investors.csv` (create it if missing). Append only people who are not already in
   it (match on phone, email, or name + company). Keep the file sorted with West Palm / Palm Beach buyers first.
4. At the end print: groups read, posts read, new contacts added, total contacts, and the 10 most active buyers.

## Rules
- Read only. Never message, comment, like, react, join, friend or follow anyone.
- Skip private profiles, group member lists and anything not posted for business.
- Normal reading pace (pause a few seconds between pages, stop at 200 posts per run).
  Meta does not allow automated scraping, so a slow pace keeps the account safe.
- If Facebook shows a warning, a captcha or "you're going too fast", stop and tell Rodrigo.
- `reports/` is gitignored: this list never goes to GitHub.

## Keep it running
Run it again each morning ("run docs/prompts/facebook_investor_contacts.md"); it only adds new people.
Emailing this list as a group is marketing email: business address + an opt-out line (CAN-SPAM).
