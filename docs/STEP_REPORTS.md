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
