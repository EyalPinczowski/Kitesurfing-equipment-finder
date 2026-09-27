# Step reports

Each build step ends with a self-review and a report on what was found.

## Step 1: skeleton, config, DB and migrations

**What was built**
- Project scaffolding: `pyproject.toml`, `requirements.txt`, `.env.example`, `.gitignore` (secrets, cookies and data are never committed), and `config/sources.yaml` with Israeli search terms in Hebrew and English.
- `kitefinder/models.py`: Profile, OwnedItem, Recommendation and RecItem, with range validation.
- `kitefinder/config.py`: reads `.env`, environment variables and the YAML sources file.
- `kitefinder/db.py`: SQLite in WAL mode with versioned, atomic migrations. The full schema is in place: profile, gear, sites, recommendation history (with a snapshot of the profile), raw posts, listings, images, condition assessments, matches, notifications, favorite/dismiss marks, the run ledger and the LLM cache. It also has a consistent live backup.
- `kitefinder/cli.py`: commands `profile set|show`, `gear add|list|rm`, `sites add|list|rm`, `history`, `fav`, `dismiss`, `unmark`, `favorites` and `backup`.
- `scripts/check.sh`: the step gate (ruff lint, format check, pytest, and coverage of at least 85%).

**Tests**: 78 passed, 99% coverage. They cover:
- The schema, WAL mode and foreign keys, reopening an existing DB, refusing a newer schema version, and that a failed migration leaves nothing half-built
- Profile round-trips (including Hebrew spot names), all 11 validation rules, gear, sites (URL normalization and de-duplication), and recommendation history ordering and snapshots
- Favorite/dismiss on listings and on recommended items, cascades, and backups
- Full expected output text of the CLI commands

**Issues found by the self-review and fixed**
1. A migration interrupted partway would leave tables built but the version at 0, so the DB could never open again. Migrations now run in an explicit BEGIN/COMMIT (regression test added).
2. The `matches` table's UNIQUE constraint didn't apply when `rec_item_id` was NULL, which would re-notify the same listing on every run. It is now a unique index on `COALESCE(rec_item_id, 0)` (regression test added).
3. `backup` to a folder that didn't exist yet wrote a file with no extension, and a permission error showed a raw traceback. It now creates the folder, and a permission error prints a friendly message with a `termux-setup-storage` hint (tests added).

**Suggested improvements**
| # | Suggestion | Impact | When |
|---|------------|--------|------|
| 1 | Store the profile as typed columns instead of a JSON blob, so SQL can filter on it. | Low | Later, only if needed |
| 2 | Add `kitefinder restore <file>` to go with `backup`. | Medium | Small, can do now |
| 3 | Automatic daily backup to `/sdcard/Download` from the daemon, keeping the last 7. | Medium | Step 7 |
| 4 | Accept imperial input (lb / inches) in the profile and convert it. | Low | Later |
| 5 | Drop `rich` from the plan: plain text keeps the output easy to test, and the Telegram bot is the main UI anyway. | Low | Already done |

### Step 1 follow-up: seed websites
- Added the Israeli shop URLs you gave (6 so far, including iks-surf.co.il) to `config/sources.yaml`. They load into the DB once, through a new `meta` table (migration v2), so a site you remove stays removed.
- Tests: 82 passed, 99% coverage.
- Note: this cloud environment's network policy blocks these hosts, so they haven't been fetched yet. Real page fixtures come in Step 4, once the domains are allowed.

## Step 2: sizing engine, quiver planning and reference charts

**What was built**
- `kitefinder/sizing/engine.py`:
  - Kite size ≈ weight × 2.2 / wind, adjusted for style (twin tip 1.0, surf 0.9, foil 0.65) and skill (0.95–1.05), then rounded to sizes kites are actually sold in (3–15 and 17 m²).
  - A usable wind range for each kite (0.85×–1.3× its ideal wind).
  - Twin-tip, surfboard and foil-board sizing, front-wing area, harness size from waist (both sizes are given when you're on a boundary) and bar width.
- `kitefinder/sizing/quiver.py`:
  - Covers your wind range with the fewest kites (a greedy plan that provably uses the minimum number), reusing the kites you own.
  - It stretches an owned kite up to 1.5 kn instead of buying a new kite for a sliver of the range.
  - It says clearly when a wind range can't be covered (too light or too strong).
  - It adds a board, bar and harness only when yours are missing or the wrong size.
  - Single-item recommendations pick the kite that covers the most of the requested range.
- `kitefinder/sizing/reference/*.yaml`: kite, twin-tip and harness size charts.
- CLI: `recommend` (full set), `recommend --item kite --wind 18-24` (single item), and `gear add --subtype`. Every recommendation is saved to history.
- DB migrations v3 (subtype/unit columns) and v4 (converts old bar and foil units).

**Tests**: 475 passed, 99.6% coverage.
- Family 1 (sizing against the charts): all 28 kite-chart cells, all twin-tip rows and all harness rows, plus consistency checks on the charts themselves.
- Properties over 150 rider profiles (5 weights × 5 wind ranges × 3 styles × 2 skills): the range is fully covered, with no duplicate sizes and at most 5 kites; recommendations never repeat a size you own; kite size never grows as wind increases, or shrinks as weight increases.
- Exact expected text of the explanation and the CLI output.

**Important caveat**: this environment can't reach the web, so the reference charts are typed in from widely published industry charts. They are marked `verified: false`, with an empty `sources` list, and the engine matches every chart cell within 1 m². Step 3's live suite (Gemini, once network is allowed) will read real manufacturer and school chart pages, compare them with these values, and record the source URLs.

**Issues the self-review found and fixed**
1. The explanation said "your kites already cover the whole wind range" when there were no kites at all, or when part of the range was uncovered.
2. The light-wind cutoff ignored owned kites bigger than 17 m², such as a 21 m² light-wind kite.
3. `--wind 0-0` crashed with a traceback, and `24-18` was accepted. Both now give a clear error.
4. The size charts wouldn't have been included by `pip install`. Fixed, checked by building a real wheel, and covered by a test.
5. Bar and foil units changed (m → cm, m² → cm²), so migration v4 converts any old values.
6. Found during testing: the single-kite pick for 18–24 kn was 8 m², which only starts working at 18.5 kn. It's now 9 m², which covers the whole range.

**Suggested improvements**
| # | Suggestion | Impact | When |
|---|------------|--------|------|
| 1 | Verify the charts live against brand and school pages and record their URLs. | High | Step 3 (needs network and Gemini) |
| 2 | Default wind ranges for Israeli spots (Bat Galim, Sdot Yam, Beit Yanai, Eilat…), used by the questionnaire. | High | Step 6 |
| 3 | Model-aware wind ranges: light-wind kites such as the Juice or Evo SLS cover more range than the generic rule assumes. The extractor can use model names from listings. | Medium | Step 5 |
| 4 | Offer two options: a "minimum" quiver (fewest kites) and a "comfortable" one (more overlap). | Medium | Small, can do now |
| 5 | Gusty-spot factor: Israeli winds are often gusty, so bias toward the smaller kite. | Medium | Small, can do now |
| 6 | Wetsuit sizing for Israeli sea temperatures, with a seasonal note. | Low | Later |
| 7 | `kitefinder restore <file>` (carried over from Step 1). | Medium | Small |

## Step 2b: Step 2 suggestions applied, plus typing areas instead of wind speeds

**What was built**
- **Areas instead of wind speed**: `profile set --areas "בת גלים, Sdot Yam"` (or a region such as `north`/`צפון`, `eilat`, `כנרת`), with an optional `--season summer|winter|all`.
  - The wind range is derived from the areas, and `profile show` says so: "(from your areas, summer)".
  - An explicit `--wind` always wins. `--wind areas` switches back, and `--areas ""` clears the areas.
  - Hebrew input works with or without niqqud, hyphens, `ת"א`/`ת״א` and spelling variants such as קרית/קריית.
  - Typos get suggestions ("did you mean Bat Galim?"). `kitefinder areas` lists every spot.
- `sizing/reference/spots_il.yaml`: 16 Israeli spots in 5 regions, each with Hebrew and English aliases, summer and winter wind ranges, a gusty flag and water type.
- **Gusty-spot bias**: kites are about 7% smaller. It's taken from your areas (Tel Aviv and Kinneret are marked gusty) or set with `--gusty/--no-gusty`.
- **Minimum vs comfortable quiver**: `recommend` shows and saves both. Comfortable is exactly one kite more than minimum, spread for the most margin. Its ranges are labelled "sweet spots". When no better quiver exists it says the two options are the same.
- **Active set**: `kitefinder use <id>` picks the set that searches will use. A fresh recommendation resets it.
- **`kitefinder restore <file>`**: checks the file first, keeps a safety copy of the current DB in `data/backups/`, and upgrades older backups to the current schema.
- DB migration v5 adds the variant column.

**Tests**: 844 passed, 99.6% coverage.
- Spots: every alias resolves, Hebrew variants, regions, seasons, a no-conflict check, and the exact text of `kitefinder areas`.
- Over the same 150-profile grid:
  - A gusty quiver never gets bigger kites and still covers the whole range.
  - Comfortable is always either minimum + 1 kite with identical coverage and a comfort margin at least as good, or identical to minimum.
- Restore: a round trip, the safety copy, rejection of junk, missing and newer-schema files with nothing changed, and migration of an older backup.

**Issues found during the self-check and fixed**
1. The first version of comfortable (a fixed narrow band) gave 4 kites for 80 kg at 12–25 kn, too expensive to be a useful option. It was redesigned as "minimum + 1 kite".
2. A narrow band left holes between small standard sizes (3 and 4 m²), and the plan silently stopped early, sometimes with 0 kites. Now a comfortable plan must cover exactly what minimum covers.
3. `recommend --option comfortable` still made the old minimum set the active one.
4. Found by the code review: `--areas ""` could no longer clear your spots.
5. Found by the code review: `restore` left the file open when it wasn't a database.
6. Wording: comfortable ranges read as the kite's full range; they're now labelled "sweet spots". Dor showed the neighbouring beach's Hebrew name.

**Caveat**: the spot wind ranges are typical values from general knowledge (`verified: false`), not measured statistics. They should be checked against wind statistics once network access is allowed, and you know these spots, so corrections are welcome.

**Suggested improvements**
| # | Suggestion | Impact | When |
|---|------------|--------|------|
| 1 | Review the spot wind ranges yourself; you ride here. Edit `spots_il.yaml` or tell me the numbers. | High | Any time |
| 2 | Add a month-based season (e.g. `--season auto` uses the current month). | Low | Small |
| 3 | Show kite prices next to each option, once listings exist, so the extra kite has a cost. | Medium | Step 5 |
| 4 | Offer the areas list as buttons in the Telegram questionnaire. | High | Step 6 |

## Step 2c: one-kite quiver and budget ("best set under ₪X")

**What was built**
- **One-kite quiver** (`--option one_kite`): the single kite that covers the most of your wind range. It uses a kite you own if that covers almost as much. It says where you'll be under- or overpowered.
- `recommend` now shows **all three options** (minimum, comfortable, one kite). Options identical to one already shown are listed once, with a note.
- **Price estimates**: every recommended item shows an estimated Israeli price (`sizing/reference/prices_il.yaml`, marked `verified: false`), along with the set's estimated total, used and/or new depending on your new/used preference. Gear you own costs nothing.
- **Budget**: with `profile set --budget`, each option is marked "✓ Fits" or "✗ ₪X over", and searches use the best option that fits.
- **`recommend --under 9000`** (or `--under` alone, to use your profile budget) returns the best set within that price.
  - Order of preference: comfortable, then minimum, then one kite. New is preferred over used when you accept both and it's affordable.
  - It adds a "For ₪X more: …" hint for the next option up.
  - If nothing fits, it says how much the cheapest *rideable* set costs. It never suggests a partial set, since a kite without a bar, harness and board can't be ridden.
- Foil riders get the foil parts they're actually missing: a complete foil, or just the front wing or mast.
- History lines show the variant and the estimated cost. Migration v6 adds the price and budget columns.

**Tests**: 1,114 passed, 99% coverage.
- The one-kite pick has the best overlap over the 150-profile grid.
- Price table checks, and exact price values.
- The budget pick is the best affordable option: 216 combinations of weight, budget and new/used preference, each checking that no better affordable option was skipped.
- Exact text of the `--under` output.

**Issues found during the self-check and fixed**
1. A "buy first" list for tight budgets suggested kite + harness without a bar, which isn't a rideable set. It was removed in favour of "the cheapest rideable set costs ₪X".
2. Found by the code review: a foil rider's set was priced with just a front wing, not a complete foil, so it looked cheaper than it is.
3. Found by the code review: a ₪0 budget was treated as "no budget".
4. My own test assumed "minimum quiver, new" beats "comfortable quiver, used". The rule is quiver quality first, then condition. That's deliberate: more kites means more riding days.

**Caveat**: prices are rough estimates, not market data. In Step 5, the median price of real collected listings replaces them wherever there are enough listings.

**Suggested improvements**
| # | Suggestion | Impact | When |
|---|------------|--------|------|
| 1 | Weight the one-kite pick toward your most common wind. Mediterranean summers are mostly 12–18 kn, so a slightly bigger single kite may suit you better than the overlap maths says. This uses the spot data. | Medium | Small |
| 2 | Real market prices from collected listings, instead of the estimate table. | High | Step 5 |
| 3 | Budget split hint, e.g. "spend more on the kite, buy the board used". | Low | Later |
| 4 | Let you set your own prices for items in the table. | Low | Small |

## Step 2d: build sets from the cheapest listings, with mixed or same brand

**What was built**
- `kitefinder/assemble.py` builds a recommended set from real listings. For each item it picks a matching listing, mixing sellers and sites (Facebook, Yad2, shop sites), to get the lowest total price.
  - **Matching rules**:
    - Kites must be within the recommended size range.
    - Boards must be within 2 cm, and of a compatible kind.
    - Bars match even when no width is stated; they're flagged as "unchecked".
    - Harness size labels must overlap the recommended ones. `M`, `S/M` and `M-L` all match `M/L`.
    - Foils must be the right part.
    - Your new/used preference is respected. Sold, dismissed and unpriced listings are skipped.
  - **Search**: an exact search that never uses one listing twice. When sets are incomplete, it ranks them by (items missing, value of what's missing), then price, then fewest sellers to pick up from, then sizes that were actually stated.
  - **Brand modes** (`--brands`):
    - `mixed` (the default): the cheapest listing of any brand for each item.
    - `same`: every item from one brand.
    - `kites_bar`: kites and bar from one brand, since bars usually only fly their own brand's kites. Board and harness can be any brand.
  - Brand names are recognised in English and Hebrew (דואוטון → Duotone, קברינה → Cabrinha, …).
  - **Warnings**: kites of different brands, or a bar of a different brand, trigger a compatibility warning plus the price of the compatible option. It also warns when a size isn't stated.
- **`kitefinder assemble`** works on the active set (or `--set N`). `assemble --under 9000` picks the best quiver (comfortable, then minimum, then one kite) that can actually be bought complete within the budget from real listings.
- DB: `add_listing` and `candidate_listings`, used by the Step 3–4 collectors. Migration v7 adds listing `subtype` and `size_label`. `Listing` fields after `size` must now be passed by name.

**Tests**: 1,229 passed, 99% coverage.
- The **exact search is checked against brute force** on 40 random markets, which confirms the cheapest set is always found.
- Matching rules per item type.
- Every brand mode.
- The full text of the `assemble` output.
- `--under`.

**Issues found during the self-check and fixed**
1. In `same` mode, a brand that had only a board beat one that had the kite, because it was cheaper. Incomplete sets are now ranked by the *value* of what's missing.
2. That change had broken the search's pruning shortcut, which was still comparing the old score layout. Fixed; the pruning is still provably safe.
3. Two listings with no URL from the same source collided in the DB.
4. `Listing` positional arguments were easy to mix up; they're now keyword-only.
5. Found by the code review: `kites_bar` returned nothing when there were no kites or bar to buy.
6. Found by the code review: `assemble --under` saved the chosen set without its budget or prices, and didn't make it active.
7. Found by the code review: negative prices were accepted.
8. Found by the code review: "M-L" harness sizes didn't match.
9. Two of my own expected totals in tests were wrong. The solver was right and found cheaper combinations, such as a 9 m² filling the 10 m² slot.

**Suggested improvements**
| # | Suggestion | Impact | When |
|---|------------|--------|------|
| 1 | Add pickup distance to the cost, using the travel limit from your profile. For example, prefer one seller in Haifa over three across the country for ₪100 more. | Medium | Step 5 (needs listing locations) |
| 2 | Kite–bar compatibility table by brand and year (e.g. Duotone bars and older North kites), instead of treating every brand pair as a warning. | Medium | Later |
| 3 | A shop "deal" alert when a new-item price drops below the used estimate. | Low | Step 5 |

## Step 2e: keep unpriced listings, and a minimum year

**What was built**
- **Unpriced listings are kept.**
  - They're ranked at the typical price **+15%** (`UNPRICED_MARGIN`), so a real price close to typical wins, but one far above typical loses to "ask the seller".
  - The typical price is the *new* one for new items or new-only riders, otherwise the used one.
  - The output shows "price not stated (typical ~₪X)", puts "~" on totals that include guesses, and adds a warning.
  - Budget lines say "≈ fits at typical prices — confirm with the sellers", and such a set never becomes the active set automatically.
- **Minimum year**: `profile set --min-year 2019` (0 clears it), overridden per search with `assemble --min-year`.
  - Listings with an older stated year are skipped.
  - Listings with no year are kept, but flagged "year not stated — ask the seller", and they lose ties to listings that state a year.
  - `profile show` has a "Minimum year" line, and the assemble headline shows "2019 or newer".

**Tests**: 1,251 passed, 99% coverage.

**Found by the code review and fixed**
1. Unpriced new items were costed at used prices.
2. An unknown price beat real prices just above typical.
3. `assemble --under` treated a total with guessed prices as a confirmed fit and made that set active.
4. The one-brand comparison line didn't mark estimates with "~".

## Step 3: Gemini client, post extraction, condition from photos

**What was built**
- `kitefinder/llm/gemini.py`: a free-tier Gemini REST client using plain `requests`, so Termux doesn't need the SDK or anything compiled.
  - The API key is sent in a header, never in the URL.
  - Answers are structured JSON (a response schema), and every answer is cached in SQLite, so the same post never costs a second call.
  - Pacing: at most 8 calls a minute and 200 a day by default, both configurable in `.env`.
  - Retries follow the server's suggested delay on 429, and also cover 5xx and network errors.
  - Errors are clear: `QuotaExceeded` or `LLMError`.
- `kitefinder/llm/extract.py`: the prompt and schema for reading a post (Hebrew or English).
  - It extracts every item, with type, subtype, brand, model, size, harness size, year, price, new/used, sold, location and a bundle price.
  - **Anti-hallucination check**: any size, price, year or bundle price the model returns that doesn't appear in the post text is dropped and flagged. The check knows the equivalent ways of writing a number (3.2k, 3,200, 5'4" → 163 cm, 6' → 183 cm, '21 or מודל 21 → 2021).
  - Implausible values are dropped too, such as a phone number as a price or a bar width as a kite size.
  - **Prompt-injection guard**: the post is marked as data, and it can't close its `<post>` block early.
  - Falls back to the rules when there's no key, the quota is used up, or Gemini or the network fails.
- `kitefinder/llm/rules.py`: an offline rule-based extractor that returns the same JSON shape as Gemini.
  - Hebrew prefixes (ה/ו/ב/ל…) are handled without false matches (כבר ≠ בר).
  - Plurals (קייטים, טרפזים), and multiple items on one line ("9 מטר ב-2400 ו-12 מטר ב-2900").
  - Model names imply the gear type ("Duotone Evo 10m"), but only when a brand or size is nearby.
  - "kite" used to describe another item ("משאבה לקייט", "kite surfboard") isn't counted as a kite; foil kites are recognised; foil parts are merged into the complete foil.
  - Bundle prices, and prices without a currency sign after a dash.
  - Sale, sold, "wanted" and lesson/trip wording.
- `kitefinder/llm/normalize.py`: shared parsers for prices, sizes (m², cm, feet, litres), years, gear types, harness sizes, brands (Hebrew too), and 35 Israeli cities (each spelling mapped to one canonical name).
- `kitefinder/llm/vision.py`: condition from up to 4 photos.
  - Photos are shrunk to 1024 px JPEG with Pillow, when it's installed.
  - The score is clamped to 1–10, flags come from a fixed list, and catalogue or stock photos get no score.
  - Contradictory "looks like new" plus damage flags are resolved in favour of the damage.
- CLI:
  - `kitefinder extract --text/--file [--save] [--rules] [--source] [--url]`: read a post by hand and optionally add it. This also serves as the "manual paste" source.
  - `kitefinder assess photo… [--listing N]`
  - `kitefinder llm status|models`
- DB migration v8 adds listing flags, the extraction method and the bundle price, plus image and assessment helpers. Saving the same post again now updates it.

**Tests**: 1,811 offline tests passed, 99% coverage. The 79 live tests skip without a key.
- **Labelled corpus**: 59 tuning posts plus 20 held-out posts, synthetic but written in the style of Israeli FB, Yad2 and shop posts.
- **Gemini path (offline)**: for every post, 4 variants of messy-but-correct model answers are served by a fake Gemini server through the real client. The cleaned result must equal the hand labels exactly: 316 checks.
- **Rules fallback**:
  - Tuning set: 59/59 posts fully right. This is a regression guard only, since the rules were tuned on it.
  - **Held-out set, never tuned on: 17/20 posts fully right**. Sale/not-sale 18/20, types 21/23, sizes 14/16, **prices 20/20**, brands 18/21, years 13/15.
  - The misses: a question containing "מוכר" ("someone selling…?"), a price written as "1800 כל אחד" with no currency sign, and a brand that has to be inferred from a model name.
- **Live suite** (`pytest -m live`, needs a key): runs the real model on all 79 labelled posts and requires the right sale decision, item count, types and sizes. With `KITEFINDER_RECORD=1` it also saves the answers for review. **Not run yet: no key in this environment.** Gemini itself is reachable from here.

**Issues found during the self-check and fixed**
1. The rules, first version: only 47/59 posts were classified correctly. The fixes were the rules for model names, plurals, modifiers and bundles described above.
2. Scoring 100% on the tuning set proved nothing, since it was overfitted. A held-out set was added and scored honestly.
3. A post containing `</post>` could break out of the data block (prompt injection).
4. "the edge of the board" produced a phantom kite, because "Edge" is a kite model.
5. Found by the code review: network errors skipped the rules fallback.
6. Found by the code review: saving a post twice crashed.
7. Found by the code review: "כבר" and "שבר" matched "bar".
8. Found by the code review: prices (ב 2000 ש"ח) and feet (5'10) were read as years.
9. Found by the code review: `assess` and `llm` crashed with tracebacks, and an unknown `--listing` used up quota first.
10. Found by the code review: whole-feet sizes were dropped.
11. Found by the code review: `GEMINI_MODEL`, `GEMINI_RPM` and `GEMINI_RPD` in `.env` were ignored.
12. Found by the code review: an empty photo check overwrote a saved assessment.
13. Found by the code review: `--rules` reported "no key".
14. Found by the code review: all photos were resized when only 4 were needed.

**Suggested improvements**
| # | Suggestion | Impact | When |
|---|------------|--------|------|
| 1 | Run the live suite with your key and review the recordings. That gives the real Gemini accuracy on Hebrew posts. | High | As soon as a key exists |
| 2 | Add real posts from your groups and the 6 sites to the corpus: more realistic than synthetic ones. | High | Step 4 (needs network) |
| 3 | Price per item from "X כל אחד" / "each" wording (a held-out miss). | Medium | Small |
| 4 | Batch several short posts into one Gemini call to stretch the free daily quota. | Medium | Step 5 |
| 5 | Verify the kite size charts live via Gemini (Step 2's open item). | Medium | With the key |
| 6 | Accept HEIC photos (iPhone). This needs a converter, since Pillow can't read them by default. | Low | Later |

## Step 4: collectors (shops, Yad2, Facebook) and the prefilter

**What was built**
- `collectors/base.py`:
  - `RawPost` (id, text, images, author, hints) and `CollectResult`, with **completeness**: expected count vs products seen, why the collector stopped, and errors.
  - A polite `HttpFetcher`: random pauses between requests, a mobile user agent, cookies.
  - Bot-wall detection, split into strong and weak signs so a shop that merely loads reCAPTCHA isn't flagged.
- `collectors/web.py`, for the shop sites:
  - WooCommerce category pages: product cards, sale prices, out-of-stock items, the "Showing 1–12 of 45 results" count in Hebrew and English, and pagination.
  - Product pages with size options (`data-product_variations`): one post per size, each with its own price and stock.
  - schema.org JSON-LD product lists, and a plain-text fallback for other sites.
  - Shop items are marked new unless the title says used or demo.
- `collectors/yad2.py`: reads the `__NEXT_DATA__` JSON by recognising ad-shaped objects, so it doesn't depend on fixed field names. It uses a total only when it sits next to the ads it counts, dedupes across pages, and reports bot walls and format changes.
- `collectors/facebook.py`:
  - Cookies in every common export format (Cookie-Editor JSON, a `{name: value}` object, and Netscape `cookies.txt` including `#HttpOnly_` lines). The `c_user` and `xs` cookies are required.
  - Share links (`/share/g/…`) resolved through the redirect or page metadata.
  - Mobile group pages: post ID, author, text, real photos (icons and emoji filtered out), and "הצג פוסטים נוספים" pagination. It stops after a streak of known posts, and detects login walls and "no posts, format may have changed".
  - Marketplace search JSON: title, price, city, photo and sold, first results page only, newest first.
- `prefilter.py`: a cheap recall-first gate before the LLM. It only rejects posts with no gear word, brand, model or size, and records the reason.
- DB:
  - `upsert_raw_post` returns new, changed or seen. An edited post, e.g. a price drop, goes back to extraction.
  - `known_source_ids`, `raw_posts_in_stage` and `set_stage`.
  - Migration v9 stores collector hints.
- Single-item mode for shop and Marketplace posts, so the title plus description gives one item.

**Tests**: 1,866 offline tests passed, 98% coverage.
- Family 3 (nothing missed), on saved pages:
  - Every product and size option is collected exactly once, with the expected count matched (5 of 5 products, 7 posts).
  - Repeats across pages are collapsed, and each stop reason is asserted.
  - A missing product, a pagination loop, `max pages`, a failed detail page and bot walls all make the result *incomplete*, never silently "complete".
  - Re-runs add nothing, and an edit is detected.
  - **Prefilter recall is 100%** on all 79 labelled posts.
- The sample pages are built by a checked-in generator (`tests/fixtures/pages/generate.py`).

**Issues found during the self-check and fixed**
1. The Facebook emoji image was stored as a listing photo.
2. A shop product's description produced a phantom second item; fixed with single-item mode.
3. A Marketplace "first page only" result claimed to be complete.
4. Found by the code review: pages that load reCAPTCHA were flagged as blocked.
5. Found by the code review: `cookies.txt` lost the HttpOnly `xs` cookie.
6. Found by the code review: a kite + bar package lost the bar.
7. Found by the code review: collector hints weren't stored.
8. Found by the code review: the Marketplace format-change alarm could never fire.
9. Found by the code review: size options without IDs overwrote each other.
10. Found by the code review: Yad2 could take a page count as the total and stop early while claiming completeness.
11. Found by the code review: the Hebrew word "עוד" was cut from the end of posts.
12. Found by the code review: `limit=0` returned everything.
13. Found by the code review: quadratic dedup.

**Not verifiable here**: every parser was built from the platforms' known formats, because this environment blocks the sites. See `docs/DECISIONS_FOR_APPROVAL.md` items 11–17. The first run on the phone is the real test, and each collector reports a format change loudly instead of returning nothing.

**Suggested improvements**
| # | Suggestion | Impact | When |
|---|------------|--------|------|
| 1 | Save one real page per source from the phone (`kitefinder collect --save-pages`, in Step 5) and add them as test fixtures. | High | First real run |
| 2 | Facebook group posts sorted by "new posts" instead of "recent activity", if the mobile site offers it. | Medium | After a real run |
| 3 | Fetch full Marketplace item pages for descriptions (sizes are often only there). This costs more requests. | Medium | Later |

## Step 5: pipeline, matching, search, audit ledger

**What was built**
- `pipeline.py`, one run: collect every configured source, then prefilter, extract, store listings, check photos and match.
  - Every post ends in a terminal stage with a reason.
  - Edited posts are re-read. Items that disappear from an edited post are retired, and they come back if it's edited again.
  - Sold, dismissed and gone listings are settled.
  - Facebook share links are resolved once and remembered. Missing or expired cookies are reported per source.
  - Posts read by the rules because the quota ran out are re-read by Gemini on later runs.
- `matcher.py`:
  - Each fitting (listing, item) pair is scored on price against the **market median of collected listings** (the estimate table is used until there are 3 comparable listings), size fit and photo condition, with a plain-language "why".
  - Typed searches: "kite 12m", "טרפז M", "twin tip 138", "foil 2000".
  - Watched searches get alerts too.
- `audit.py`, the ledger after every run:
  - fetched = off-topic + not for sale + with gear + failed (+ backlog)
  - every listing settled
  - unsent matches listed
  - per-source completeness: ✓ complete, ◐ partial by design, ⚠ may be missing posts, ✗ error
- DB: runs, matches (with pruning of unsent stale matches), notifications, one alert per listing, watches, meta, listing images and status.
- CLI:
  - `run [--source] [--save-pages DIR]`, `collect`, `process [--limit]` and `report [--run]`
  - `search '<query>'`, `watch add|list|rm`, `listings [--type]`
  - `add-url <link>`, used by share-to-Termux
  - `--save-pages` saves every page fetched, so the first real run on your phone gives us real test fixtures.
- Fixed while testing: site URLs keep their trailing slash (stripping it gave a 404 on WooCommerce). Duplicates are caught with a separate key.

**Tests**: 1,910 offline tests passed, 97% coverage. Family 3 over a whole run in a fake world (shop + Yad2 + Facebook group + Marketplace):
- 18 posts: 16 with gear, 1 wanted, 1 chat, 0 unaccounted.
- A re-run adds nothing.
- An edited price updates the same listing, and a removed item is retired.
- An extraction crash is recorded without stopping the run.
- A backlog is a note, not a loss.
- Missing cookies, a broken source and `max pages` are all flagged.
- Matches only for the active set and watches, one per listing.
- Exact report text, and the CLI commands end to end.

**Issues found during the self-check and fixed**
1. Sold listings stayed "new".
2. `ASSESS_PER_RUN` was frozen when the function was defined.
3. The trailing-slash 404.
4. Found by the code review: "foil 2000" lost its size.
5. Found by the code review: an early dismissal was flagged forever.
6. Found by the code review: quota-fallback posts stayed rules-only for good.
7. Found by the code review: old seed markers were ignored.
8. Found by the code review: stale matches could be alerted.
9. Found by the code review: matching was O(items × listings²).
10. Found by the code review: `add-url` didn't re-read known posts.
11. Found by the code review: `add-url` hid block reasons.
12. Found by the code review: a `collect` crash left the run unfinished.
13. Found by the code review: `--limit 0` printed everything.

**Suggested improvements**
| # | Suggestion | Impact | When |
|---|------------|--------|------|
| 1 | Re-alert on a real price drop (e.g. 10%+) for items you favorited. | Medium | Step 6 |
| 2 | Distance ranking from your home town, using the city names already extracted. | Medium | Later (needs a city-coordinates table) |
| 3 | Batch several posts per Gemini call to stretch the free quota. | Medium | Later |


## Step 6 — Telegram bot, questionnaire, alerts, Mini App

**What was built**
- `bot/telegram.py`: a small Bot API client (plain HTTPS). It covers long polling, messages with buttons, photo(s), button answers, the command menu and the Mini App menu button. It waits and retries on flood limits.
- `bot/questionnaire.py`, the `/setup` questionnaire. It asks in order:
  1. weight
  2. hip/waist
  3. level
  4. style
  5. where you ride: region buttons, typed spots (Hebrew or English), or a typed wind range
  6. season (skipped for a typed wind range)
  7. owned gear, as a loop (type → size → brand/model/year)
  8. budget
  9. new/used
  10. oldest model year
  11. travel distance
  12. **website links**

  Answers are checked one at a time and a bad answer is explained. Progress survives a restart, and `/cancel` stops it. It ends with a summary and a "See the recommendation" button.
- `bot/formatter.py`: the alert card. Every card has:
  - 🪁 the title
  - 💰 the price (or "price not stated — ask the seller"), with new/used and the comparison to the market or typical price
  - 📍 the location (or "location unknown")
  - 📝 the description
  - 🔍 the photo condition check
  - 🌐 the source and seller
  - ✅ why it fits
  - 🔔 the watched search, if that's why it matched
  - 🔁 "Also posted on …", which folds duplicates into one card

  Buttons: ✅ Favorite, ❌ Dismiss, 🔗 Open. The card is followed by up to 4 photos.
- `bot/app.py`:
  - Commands: `/setup /recommend /under /assemble /search /watch /unwatch /favorites /history /profile /gear /sites /run /report /help /cancel`. They reuse the CLI, so both always say the same thing.
  - Owner-only access, alert sending with every message recorded, and the Favorite/Dismiss buttons, which update the database and the buttons in place.
- `bot/miniapp.py` + `bot/web/`: the Telegram Mini App, with Listings (cards, favorite/dismiss), Options (the three quivers, best under a price, assembled sets mixed/same brand) and Profile (edit form). It is served from the phone. Every API call is checked with Telegram's `initData` signature and your user id.
- `bot/tunnel.py`: starts a free Cloudflare quick tunnel and reads its public address.

**Tests**: 1,967 offline tests passed, 97% coverage. Family 2 (full bot messages):
- Golden files in `tests/golden/` hold the **complete text and buttons** of every message: the whole `/setup` conversation, `/help`, every alert from a full run, the Favorite/Dismiss replies, and alert cards with and without photos and with a missing price or location. I reviewed each golden file by hand, and the review found 6 wording problems that are now fixed.
- Invariant checks:
  - every alert has a description, a price line, a location line and a condition line
  - it has photos exactly when the listing has images
  - every match is sent once
  - a second run sends nothing new
- Access and flow: privacy (other chats are refused), a restart mid-setup, typed labels, bad answers, and long answers split under Telegram's 4,096-character limit.
- The Mini App, through a real local server: static files, signature checks (valid, tampered, other bot, expired, wrong user, no owner yet, non-ASCII), every API route, bad input, and oversized bodies.
- The tunnel: address parsing, log draining, a silent cloudflared, and cloudflared missing.

**Issues found during the self-check and fixed**
1. A single photo was sent as an "album", which Telegram rejects (albums need 2–10), so single-photo alerts lost their photo.
2. Long polling timed out before Telegram's 50-second hold.
3. One card that Telegram refused blocked every alert after it. Such a card is now re-sent as plain text, or left pending if it still fails.
4. Empty replies would be rejected by Telegram.
5. Mini App crashes on bad input.
6. Typed button labels weren't accepted.
7. The golden review found duplicated notes and seller names, word order, stray separators, and "?" for an unknown size.
8. Found by the code review: a wind range above 50 kn blocked the end of setup.
9. Found by the code review: cloudflared stalled once its log pipe filled.
10. Found by the code review: the tunnel timeout never fired.
11. Found by the code review: a quote in `/sites` crashed polling.
12. Found by the code review: photos were resent or lost on failures and flood limits.
13. Found by the code review: a non-ASCII signature crashed a request.

**Suggested improvements**
| # | Suggestion | Impact | When |
|---|------------|--------|------|
| 1 | Re-alert on a real price drop (10%+) for favorited listings. | Medium | Later |
| 2 | A daily digest option (one message with the day's best matches) instead of one alert per listing. | Medium | Later |
| 3 | Host the Mini App page on GitHub Pages so it opens instantly; only the data would come from the phone. | Low | Later |
| 4 | Let the questionnaire edit a single answer (e.g. only the budget) instead of starting over. | Low | Later (the Mini App profile form already does this) |


## Step 7 — the always-on agent, Termux install, README, end-to-end

**What was built**
- `kitefinder/daemon.py` (`kitefinder daemon`, `--once`, `--no-miniapp`, `--port`):
  - Each source runs on its own interval from `config/sources.yaml` (sites 3 h, Yad2 1 h, Facebook 2 h), and a fully failed source is retried after 15 minutes.
  - It polls the bot, sends pending alerts, and tells you about source problems once, then again when they're fixed.
  - It makes a daily backup, keeping 7.
  - It runs the Mini App server and tunnel. If cloudflared dies, it restarts it and updates the bot's menu button; if the tunnel couldn't start, it tries again every 30 minutes.
  - No single failure stops it: each step in a round is guarded, Telegram outages back off from 5 s up to 5 minutes, and it stops at once on `sv stop` without leaving cloudflared behind.
- `install_termux.sh`:
  - Installs from Termux packages: Python, Pillow, cloudflared, termux-services and termux-api. Nothing needs compiling.
  - Installs the Python packages, creates `.env` from the example (permissions 600) and a private `secrets/` folder, installs share-to-Termux, and sets up the background service with a log and Termux:Boot autostart.
- `bin/termux-url-opener`: share any post or product link to Termux and it's read, matched and alerted.
- `README.md`: install, keys and cookies (step by step), start, daily use (every command), backups, troubleshooting, developer notes.

**Tests**: 1,994 offline tests passed, 97% coverage.
- **Family 4 (`tests/test_e2e.py`) goes through everything on a fresh install:**
  1. `/start` claims the bot, then the full questionnaire, then the recommendation button.
  2. The first scheduled round covers every source. The audit is "all accounted for" for every run, **every match is sent**, and every card has its 💰📍📝🔍🌐 lines and buttons.
  3. A Favorite and a Dismiss are applied.
  4. The next day, a Yad2 price drop, and every source runs again. **No repeated alerts**, the dismissed item stays hidden, and the updated price is stored.
  5. Backups exist, and `/favorites`, `/history` and `/report` give the right text.

  The golden files hold the setup and first-alert transcripts.
- **Daemon**: the schedule per source, retries, problem and fixed messages (including cookies that expire twice), disabled sources, backup rotation, `/run`, Telegram backoff, crash isolation, running without a bot, the wiring (commands, menu button, tunnel), tunnel restart and retry, a busy port, clean shutdown.
- **Termux scripts**, run with bash: share-to-Termux, a missing link, a notification that never answers, the installer refusing to run outside Termux, no pip upgrade, file permissions.

**Issues found during the self-check and fixed**
1. A lost network at startup crashed the service while registering the bot's commands.
2. A dead tunnel left the Mini App link broken for good.
3. A menu-button failure orphaned cloudflared.
4. Found by the code review: one broken shop re-scraped every shop every 15 minutes.
5. Found by the code review: an expired-cookies message was never cleared, so the next expiry went unreported.
6. Found by the code review: a busy Mini App port crashed the daemon.
7. Found by the code review: cloudflared was orphaned on stop.
8. Found by the code review: one failing step skipped backups and the tunnel check.
9. Found by the code review: `pip install --upgrade pip` would stop the installer on Termux.
10. Found by the code review: the share script could hang without the Termux:API app.
11. Found by the code review: a slow stop.

**Suggested improvements**
| # | Suggestion | Impact | When |
|---|------------|--------|------|
| 1 | Answer the bot while a search runs (searches in a worker thread with its own DB connection). | Medium | After the first real runs show how long they take |
| 2 | Quiet hours: hold alerts at night and send them in the morning. | Low | Later |
| 3 | A `kitefinder doctor` command that checks the keys, cookies, each site, cloudflared and battery settings in one go. | Medium | Later |
| 4 | Re-alert on price drops for favorites (carried over from Step 5). | Medium | Later |


## Step 8 — your approval decisions

You answered 13 approval items. Four keep the current behaviour (#13 Marketplace from Tel Aviv, #14 Facebook depth, #26 alert style, #34 backups inside Termux) and #30 is covered by #23. The other eight are changed:

| # | Your answer | What changed |
|---|---|---|
| 25 | Require `TELEGRAM_CHAT_ID` | The bot answers only that chat; with no chat id, nobody. The owner always comes from `.env`. |
| 23 | Re-alert any alerted listing on a 10%+ price drop | The card starts with "📉 Price dropped ₪X → ₪Y". Each drop is sent once. Only for listings that still fit your current set or a watched search; dismissed and sold ones are skipped, and cross-posted duplicates are one card. |
| 31 | Answer during searches | Searches run in a background thread with their own database connection and Gemini client. `/run` during a run says so, and the report comes when it ends. A crashed search is reported and retried after 15 minutes. `--once` still runs in the foreground. |
| 20 | Wait for Gemini | When the quota is out, posts wait and `/report` shows them. A post waits at most 48 h (e.g. a key with no quota at all); then the rules read it and Gemini re-reads it later. Without a key, the rules read everything, as before. |
| 18 | 50% size fit, 25% condition, 25% price | New score weights. Only the order of alerts changes. |
| 6 | Bigger one kite, for light wind | Sized a quarter of the way up your range and never smaller than before. For 80 kg at 12–25 kn: 12 m², 12.5–19 kn (was 9 m², 16.5–25.5 kn). The one-kite set now costs ~₪6,600 used (was ~₪6,200). |
| 5 | Newest gear first | Within a budget, a set bought new beats a used one; among real-listing sets, more new items and newer years win, with ties going to the fuller quiver. |
| 17 | Slower, safer requests | 4–10 s between shop and Yad2 pages, 15–40 s for Facebook. |

**Tests**: 2,010 offline tests passed, 97% coverage. The golden files were regenerated and reviewed: only the one-kite text and the order of alert cards changed, and the cards themselves are identical.

**Issues found and fixed**
1. A crashed background search restarted at every round, about every 25 s.
2. My first quota change held every post as "waiting" when there's no Gemini key.
3. Pre-existing: when no set could be completed, the "fullest set" fallback could pick one with nothing found.
4. Found by the code review: `--once` exited before its search finished.
5. Found by the code review: price drops ignored which set and searches you track.
6. Found by the code review: a match pruned mid-send crashed the batch.
7. Found by the code review: posts could wait for Gemini forever.
8. Found by the code review: duplicate price-drop cards.
