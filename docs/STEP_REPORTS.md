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
