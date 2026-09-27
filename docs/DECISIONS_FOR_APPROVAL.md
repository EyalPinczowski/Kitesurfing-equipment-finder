# Decisions waiting for your approval

These are choices I made on my own so the work could keep moving. Each one works today and
is tested, but it's a judgement call or can't be checked from the build environment. Reply
with the numbers you approve or want changed; nothing here is final until you do.

Status key: **needs check** = only you (or network access) can confirm it · **judgement** = a
design choice with a reasonable alternative.

## Data and sizing (Steps 2–2e)

1. **Kite, board and harness size charts are typed in from memory** (`kitefinder/sizing/reference/*.yaml`, `verified: false`). *needs check.* Once network access is allowed, the live check compares them with brand pages.
2. **Israeli spot wind ranges** (16 spots, summer and winter, gusty flags) come from general knowledge. *needs check.* You know these spots better than I do.
3. **Typical Israeli prices** used for estimates (`prices_il.yaml`): a used kite costs about 45% of new, bar ₪1,300 used, harness ₪700 used, and so on. *needs check.* These will be replaced by real market medians once listings exist.
4. **Gusty-spot factor 0.93**, i.e. about 7% smaller kites. *judgement.*
5. **Budget preference order**: comfortable, then minimum, then one kite. Quiver quality beats new versus used. *judgement.*
6. **One-kite pick = the kite covering the most of your wind range.** For 80 kg at 12–25 kn that's 9 m², underpowered below 16.5 kn. A bigger single kite may suit Mediterranean summers better. *judgement.*
7. **Unpriced listings are ranked at typical price +15%.** *judgement.*

## Reading posts (Step 3)

8. **Default Gemini model `gemini-2.5-flash`**, at most 8 calls a minute and 200 a day. *needs check.* Free-tier names and limits change; `kitefinder llm models` shows what your key allows.
9. **Rules fallback accuracy on held-out posts: 17/20 fully right.** *judgement.* Acceptable as a fallback? Gemini should do better; that's unmeasured until a key exists.
10. **The test corpus is synthetic**: posts I wrote in the style of Israeli groups. *needs check.* Real posts from your groups should be added.

## Collecting (Step 4)

11. **The page parsers were built from each platform's known format, not from your real pages**, because the sites are blocked here. *needs check.* This covers WooCommerce shops (likely laguna.co.il and yamitysb.co.il, from their `/product-category/` URLs), Yad2's embedded search data, and Facebook's mobile group pages and Marketplace data. The first real run on your phone will tell. Each parser reports "page format may have changed" instead of silently returning nothing.
12. **The Yad2 search URL** `https://www.yad2.co.il/products/all?text={query}&page={page}` is my best guess. *needs check.* It can be changed in `config/sources.yaml`.
13. **Facebook Marketplace location `telaviv`, newest-first, first results page only.** *needs check / judgement.* Marketplace loads further results with scripts, so coverage comes from several queries and frequent runs. The run report marks this as "partial by design".
14. **Facebook groups: stop after 10 already-known posts in a row, at most 5 pages per run.** *judgement.* This trades a little completeness for fewer requests and a lower ban risk. Group feeds are ordered by activity, so an old post with new comments can appear between new ones.
15. **Shop items count as new unless the title says used, demo or יד שנייה.** *judgement.*
16. **Out-of-stock shop sizes are kept and marked "sold out"**, not dropped. *judgement.* This keeps the audit exact.
17. **Requests are polite**: a random 2–6 s pause between pages (8–20 s for Facebook, set in `sources.yaml`) and a mobile browser user agent. *judgement.*

## Matching and runs (Step 5)

18. **Match score = 45% price against market/typical, 30% size fit, 25% photo condition.** *judgement.* There are small deductions when the size or year isn't stated. It only orders results: every listing that fits is kept.
19. **The "market" price is the median of at least 3 comparable collected listings** (same kind, size in range, new/used). Below 3, the estimate table is used. *judgement.*
20. **When the Gemini quota runs out mid-run, the remaining posts are read by the offline rules straight away** (so alerts aren't delayed), then **re-read by Gemini on later runs, 30 per run.** *judgement.* The alternative is to wait for the quota and alert later.
21. **Photo checks: at most 5 per run, and only for listings that match**, to save quota. *judgement.*
22. **The same item posted on several sources** (e.g. Yad2 and a Facebook group) is still alerted once per source. Step 6 will collapse likely duplicates (same type, brand, size and price) into one alert that lists all sources. *judgement.*
23. **One alert per listing**, even if it fits several items in your set. After an alert, a later price drop doesn't re-alert. *judgement.* Re-alerting on price drops is suggested for later.
