"""Cash buyers around a deal (Rodrigo, 2026-11-14, 82 Tortuga Rd): who buys near it (LLCs), who is behind each LLC, and
which agents work with them. Three steps, all on the Mac (the cloud cannot reach realtor.com, the appraiser or Sunbiz):

1. `area.pull`   free HomeHarvest pull: every sale and listing within a radius, with the listing agent's contact.
2. Claude in Chrome (docs/prompts/cash_buyers_llc.md) fills in the owner / buyer / seller from the Property Appraiser,
   the buyer's agent, and looks up each LLC on Sunbiz + the web.
3. `build.build` joins it all into ranked lists: cash_buyers.csv, llc_people.csv, investor_agents.csv.

Everything lands in reports/buyers/<slug>/ (gitignored: names, phones and emails never go to GitHub)."""
