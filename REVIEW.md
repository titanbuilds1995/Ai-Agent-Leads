# REVIEW — self-review of the fixture end-to-end run (DRAFT_FOR_APPROVAL, 2026-10-22)

Read as a skeptical investor: 15 scored listings, 8 valuations, 3 email drafts, 2 text drafts. Live data was not available (no credentials), so this reviewed the fixture run; redo the same read on the first live `reports/` file.

## Round 1 — issues found and fixed

| # | Issue | Fix |
|---|-------|-----|
| 1 | Email quoted a cited phrase with padding cut mid-word: *"out of state. Home needs some updating but has good bones. Pric"*. Looks machine-made. | `scoring._snippet` now returns the matched words only, extended to word boundaries (max 8 words). Drafts now say *the remarks mention "needs some updating"*. |
| 2 | The quoted detail defaulted to the heaviest-weighted signal, usually "CASH ONLY", which is a weak conversation opener. | `compose._detail` prefers needs-work / damage / estate / price-reduced / life-event phrases before cash and as-is; all-caps phrases are lower-cased. |
| 3 | Vacant land got ARV $75k from a $150k/acre default against a $165k list, so MAO $32.5k and a 80% "spread". A guessed ARV should never look like a deal. | `ranking.arv_methods_for_hot_warm` (config) limits HOT/WARM to `comps`, `comps_wide`, `estimate`; `land_default` and `list_price` ARVs are always COLD. Method and comp count are now printed in the report. |
| 4 | Template 3 opened with a fragment ("Rodrigo with Titan Realty. Your listing…"). | "…Titan Realty here." |
| 5 | Report counters for "today" compared UTC timestamps to an ET date; at 11pm ET everything showed 0. | `et_day_bounds()` converts the ET day to a UTC window. |
| 6 | Texts and signatures showed `+13055550100`. | `pretty_phone` → `(305) 555-0100`. |
| 7 | Street displayed as stored: `107 N.W. 11th Street`. | `display_street()` normalizes directionals/casing at ingest; the dedupe key is unchanged. |

## Round 2 — read again after fixes: nothing worth changing in code

- Scores: estate/cash/as-is/TLC/74-DOM/2-cuts → 100; probate + open permits → 65; renovated + multiple offers + highest-and-best → 0; "as-is" on a clean starter → 26. Ordering matches gut.
- Valuations: comps path used where comps exist (Hialeah, Plantation: 8 comps, median $/sqft), Zestimate elsewhere, repair tier heavy on water damage / open permits, moderate on TLC. MAO formula verified against config.
- Drafts: all three emails pass the validator, differ in wording, name the county, quote one real remark, ask for a number, state the 14-day as-is terms, and carry a CAN-SPAM-ready signature (placeholder address until Rodrigo fills `operator.mailing_address`). Texts are 136–153 chars.

## Observations for Rodrigo (not code bugs)

1. **Motivation 95 but COLD** (Hollywood handyman special, list $329k, MAO $221k): the seller is clearly motivated but 48% above MAO. If you want these in the report as "motivated but overpriced", raise `ranking.warm_max_list_over_mao_pct` or add a third band. Right now they are visible via `make status` / the DB only.
2. **Half the fixture valuations rest on the Zestimate** because the fixture only has comps near Miami. Live, the sold-comps actor runs per county daily; watch `arv_method` in the report for the first week.
3. **Repair tiers from remarks are coarse** ($/sqft per tier in `config.repairs`). A listing that says "needs a new roof" lands in heavy ($60/sqft); if your rehab numbers differ, change the four numbers, not the code.
4. **One text per agent per day, one email per agent per 7 days.** An agent with 3 HOT listings gets one text about the best one; the others ride the next email. If you'd rather text per listing, that is `plan()` in `titan/outreach/send.py`.
