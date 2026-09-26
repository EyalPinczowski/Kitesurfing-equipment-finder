"""SQLite storage: schema migrations and data-access helpers.

The DB is a single local file (WAL mode) so it is always available, even offline.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from .models import (
    MARK_KINDS,
    MARK_STATUSES,
    OwnedItem,
    Profile,
    RecItem,
    Recommendation,
    ValidationError,
)

# Each entry upgrades the schema by one version (tracked in PRAGMA user_version).
MIGRATIONS: list[str] = [
    """
    CREATE TABLE profile (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE owned_equipment (
        id INTEGER PRIMARY KEY,
        type TEXT NOT NULL,
        brand TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        size REAL,
        year INTEGER,
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE TABLE sites (
        id INTEGER PRIMARY KEY,
        url TEXT NOT NULL UNIQUE,
        label TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,
        last_checked_at TEXT,
        last_listing_count INTEGER,
        created_at TEXT NOT NULL
    );
    CREATE TABLE recommendations (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL DEFAULT 'set',
        profile_snapshot TEXT NOT NULL,
        explanation TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE TABLE recommendation_items (
        id INTEGER PRIMARY KEY,
        recommendation_id INTEGER NOT NULL REFERENCES recommendations(id) ON DELETE CASCADE,
        type TEXT NOT NULL,
        size REAL,
        size_min REAL,
        size_max REAL,
        wind_min_kn REAL,
        wind_max_kn REAL,
        reason TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE runs (
        id INTEGER PRIMARY KEY,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        trigger TEXT NOT NULL DEFAULT 'manual',
        stats TEXT NOT NULL DEFAULT '{}',
        errors TEXT NOT NULL DEFAULT '[]'
    );
    CREATE TABLE raw_posts (
        id INTEGER PRIMARY KEY,
        source TEXT NOT NULL,
        source_id TEXT NOT NULL,
        url TEXT NOT NULL DEFAULT '',
        author TEXT NOT NULL DEFAULT '',
        text TEXT NOT NULL DEFAULT '',
        image_urls TEXT NOT NULL DEFAULT '[]',
        posted_at TEXT,
        content_hash TEXT NOT NULL,
        stage_status TEXT NOT NULL DEFAULT 'fetched',
        stage_reason TEXT NOT NULL DEFAULT '',
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        run_id INTEGER REFERENCES runs(id),
        UNIQUE (source, source_id)
    );
    CREATE TABLE listings (
        id INTEGER PRIMARY KEY,
        raw_post_id INTEGER NOT NULL REFERENCES raw_posts(id) ON DELETE CASCADE,
        item_index INTEGER NOT NULL DEFAULT 0,
        type TEXT NOT NULL,
        brand TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        size REAL,
        year INTEGER,
        price_ils INTEGER,
        is_new INTEGER,
        location TEXT NOT NULL DEFAULT '',
        contact TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        sold INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'new',
        created_at TEXT NOT NULL,
        UNIQUE (raw_post_id, item_index)
    );
    CREATE TABLE listing_images (
        id INTEGER PRIMARY KEY,
        listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
        url TEXT NOT NULL,
        local_path TEXT
    );
    CREATE TABLE image_assessments (
        id INTEGER PRIMARY KEY,
        listing_id INTEGER NOT NULL UNIQUE REFERENCES listings(id) ON DELETE CASCADE,
        score REAL,
        flags TEXT NOT NULL DEFAULT '[]',
        verdict TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE TABLE matches (
        id INTEGER PRIMARY KEY,
        listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
        rec_item_id INTEGER REFERENCES recommendation_items(id) ON DELETE SET NULL,
        query TEXT NOT NULL DEFAULT '',
        score REAL NOT NULL,
        why TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE TABLE notifications (
        id INTEGER PRIMARY KEY,
        match_id INTEGER REFERENCES matches(id) ON DELETE SET NULL,
        channel TEXT NOT NULL DEFAULT 'telegram',
        payload TEXT NOT NULL,
        sent_at TEXT NOT NULL
    );
    CREATE TABLE user_marks (
        id INTEGER PRIMARY KEY,
        target_kind TEXT NOT NULL,
        target_id INTEGER NOT NULL,
        status TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE (target_kind, target_id)
    );
    CREATE TABLE llm_cache (
        key TEXT PRIMARY KEY,
        response TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_raw_posts_status ON raw_posts(stage_status);
    CREATE INDEX idx_listings_type_size ON listings(type, size);
    -- COALESCE: SQLite treats NULLs as distinct, so a plain UNIQUE would allow duplicate
    -- query-only matches (rec_item_id NULL) and re-notify the same listing every run.
    CREATE UNIQUE INDEX idx_matches_unique
        ON matches(listing_id, COALESCE(rec_item_id, 0), query);
    """,
    """
    CREATE TABLE meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    """
    ALTER TABLE owned_equipment ADD COLUMN subtype TEXT NOT NULL DEFAULT '';
    ALTER TABLE recommendation_items ADD COLUMN subtype TEXT NOT NULL DEFAULT '';
    ALTER TABLE recommendation_items ADD COLUMN unit TEXT NOT NULL DEFAULT '';
    """,
    # v4: bars moved from metres to cm, foil wings from m² to cm² (values that are clearly in
    # the old unit are converted; a real bar is never < 5 cm, a front wing never < 1 cm²).
    """
    UPDATE owned_equipment SET size = size * 100 WHERE type = 'bar' AND size < 5;
    UPDATE owned_equipment SET size = size * 10000 WHERE type = 'foil' AND size < 1;
    """,
    """
    ALTER TABLE recommendations ADD COLUMN variant TEXT NOT NULL DEFAULT 'minimum';
    """,
    """
    ALTER TABLE recommendation_items ADD COLUMN est_price_ils INTEGER;
    ALTER TABLE recommendations ADD COLUMN price_condition TEXT NOT NULL DEFAULT 'used';
    ALTER TABLE recommendations ADD COLUMN budget_ils INTEGER;
    """,
]

SCHEMA_VERSION = len(MIGRATIONS)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_url(url: str) -> str:
    """Lower-case scheme/host, drop fragments and trailing slashes so duplicates collapse."""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or "." not in parts.netloc:
        raise ValidationError(f"not a valid web address: {url}")
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.migrate()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- schema -----------------------------------------------------------------------------

    @property
    def schema_version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def migrate(self) -> None:
        current = self.schema_version
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema v{current} is newer than this code (v{SCHEMA_VERSION})"
            )
        for version in range(current, SCHEMA_VERSION):
            # executescript() commits as it goes, so wrap the script in an explicit transaction:
            # a crash mid-migration must leave the DB at the old version with nothing half-built.
            script = f"BEGIN;\n{MIGRATIONS[version]}\nPRAGMA user_version = {version + 1};\nCOMMIT;"
            try:
                self.conn.executescript(script)
            except BaseException:
                if self.conn.in_transaction:
                    self.conn.rollback()
                raise

    # --- profile ----------------------------------------------------------------------------

    def save_profile(self, profile: Profile) -> None:
        profile.validate()
        with self.conn:
            self.conn.execute(
                "INSERT INTO profile (id, data, updated_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "data = excluded.data, updated_at = excluded.updated_at",
                (json.dumps(profile.to_dict(), ensure_ascii=False), now_iso()),
            )

    def get_profile(self) -> Profile | None:
        row = self.conn.execute("SELECT data FROM profile WHERE id = 1").fetchone()
        return Profile.from_dict(json.loads(row["data"])) if row else None

    # --- owned equipment --------------------------------------------------------------------

    def add_owned(self, item: OwnedItem) -> int:
        item.validate()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO owned_equipment "
                "(type, subtype, brand, model, size, year, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item.type,
                    item.subtype,
                    item.brand,
                    item.model,
                    item.size,
                    item.year,
                    item.notes,
                    now_iso(),
                ),
            )
        item.id = cur.lastrowid
        return item.id

    def list_owned(self, type: str | None = None) -> list[OwnedItem]:
        sql = "SELECT id, type, subtype, brand, model, size, year, notes FROM owned_equipment"
        args: tuple = ()
        if type:
            sql += " WHERE type = ?"
            args = (type,)
        rows = self.conn.execute(sql + " ORDER BY type, size", args).fetchall()
        return [OwnedItem(**dict(r)) for r in rows]

    def remove_owned(self, item_id: int) -> bool:
        with self.conn:
            cur = self.conn.execute("DELETE FROM owned_equipment WHERE id = ?", (item_id,))
        return cur.rowcount > 0

    # --- sites ------------------------------------------------------------------------------

    def add_site(self, url: str, label: str = "") -> tuple[int, bool]:
        """Returns (site_id, created). Adding a known URL again is a no-op."""
        norm = normalize_url(url)
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO sites (url, label, created_at) VALUES (?, ?, ?)",
                (norm, label, now_iso()),
            )
        if cur.rowcount:
            return cur.lastrowid, True
        row = self.conn.execute("SELECT id FROM sites WHERE url = ?", (norm,)).fetchone()
        return row["id"], False

    def list_sites(self, enabled_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM sites"
        if enabled_only:
            sql += " WHERE enabled = 1"
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY id").fetchall()]

    def remove_site(self, site_id: int) -> bool:
        with self.conn:
            cur = self.conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
        return cur.rowcount > 0

    def seed_sites(self, urls: list[str]) -> list[str]:
        """Adds config seed URLs the first time each one is seen; returns the URLs added.

        Seeds are remembered in `meta`, so a seed the user removed is not re-added on restart.
        """
        added = []
        for url in urls:
            norm = normalize_url(url)
            key = f"seeded_site:{norm}"
            if self.conn.execute("SELECT 1 FROM meta WHERE key = ?", (key,)).fetchone():
                continue
            _, created = self.add_site(norm)
            with self.conn:
                self.conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (key, now_iso()))
            if created:
                added.append(norm)
        return added

    def record_site_check(self, site_id: int, listing_count: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE sites SET last_checked_at = ?, last_listing_count = ? WHERE id = ?",
                (now_iso(), listing_count, site_id),
            )

    # --- recommendations --------------------------------------------------------------------

    def save_recommendation(self, rec: Recommendation) -> int:
        created = now_iso()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO recommendations (kind, variant, price_condition, budget_ils, "
                "profile_snapshot, explanation, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    rec.kind,
                    rec.variant,
                    rec.price_condition,
                    rec.budget_ils,
                    json.dumps(rec.profile.to_dict(), ensure_ascii=False),
                    rec.explanation,
                    created,
                ),
            )
            rec.id = cur.lastrowid
            rec.created_at = created
            for item in rec.items:
                c = self.conn.execute(
                    "INSERT INTO recommendation_items (recommendation_id, type, subtype, unit, "
                    "size, size_min, size_max, wind_min_kn, wind_max_kn, reason, est_price_ils) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        rec.id,
                        item.type,
                        item.subtype,
                        item.unit,
                        item.size,
                        item.size_min,
                        item.size_max,
                        item.wind_min_kn,
                        item.wind_max_kn,
                        item.reason,
                        item.est_price_ils,
                    ),
                )
                item.id = c.lastrowid
                item.recommendation_id = rec.id
        return rec.id

    def _items_for(self, rec_id: int) -> list[RecItem]:
        rows = self.conn.execute(
            "SELECT id, recommendation_id, type, subtype, unit, size, size_min, size_max, "
            "wind_min_kn, wind_max_kn, reason, est_price_ils FROM recommendation_items "
            "WHERE recommendation_id = ? ORDER BY id",
            (rec_id,),
        ).fetchall()
        return [RecItem(**dict(r)) for r in rows]

    def _rec_from_row(self, row: sqlite3.Row) -> Recommendation:
        return Recommendation(
            id=row["id"],
            kind=row["kind"],
            variant=row["variant"],
            price_condition=row["price_condition"],
            budget_ils=row["budget_ils"],
            profile=Profile.from_dict(json.loads(row["profile_snapshot"])),
            explanation=row["explanation"],
            created_at=row["created_at"],
            items=self._items_for(row["id"]),
        )

    def get_recommendation(self, rec_id: int) -> Recommendation | None:
        row = self.conn.execute("SELECT * FROM recommendations WHERE id = ?", (rec_id,)).fetchone()
        return self._rec_from_row(row) if row else None

    def latest_recommendation(self) -> Recommendation | None:
        """The active set searches use: the one picked with `use`, else the newest minimum set."""
        active = self.conn.execute("SELECT value FROM meta WHERE key = 'active_rec'").fetchone()
        if active:
            rec = self.get_recommendation(int(active["value"]))
            if rec is not None and rec.kind == "set":
                return rec
        row = self.conn.execute(
            "SELECT * FROM recommendations WHERE kind = 'set' "
            "ORDER BY (variant = 'minimum') DESC, id DESC LIMIT 1"
        ).fetchone()
        return self._rec_from_row(row) if row else None

    def set_active_recommendation(self, rec_id: int | None) -> None:
        """Pick the set searches use; None goes back to 'newest minimum set'."""
        with self.conn:
            if rec_id is None:
                self.conn.execute("DELETE FROM meta WHERE key = 'active_rec'")
                return
            rec = self.get_recommendation(rec_id)
            if rec is None or rec.kind != "set":
                raise ValidationError(f"no saved set with id {rec_id} (see: kitefinder history)")
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES ('active_rec', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(rec_id),),
            )

    def list_recommendations(self, limit: int = 20) -> list[Recommendation]:
        rows = self.conn.execute(
            "SELECT * FROM recommendations ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._rec_from_row(r) for r in rows]

    # --- favorites / dismissals -------------------------------------------------------------

    def _target_exists(self, kind: str, target_id: int) -> bool:
        table = "listings" if kind == "listing" else "recommendation_items"
        sql = f"SELECT 1 FROM {table} WHERE id = ?"  # table name comes from a fixed whitelist
        return self.conn.execute(sql, (target_id,)).fetchone() is not None

    def set_mark(self, kind: str, target_id: int, status: str, note: str = "") -> None:
        if kind not in MARK_KINDS:
            raise ValidationError(f"kind must be one of {', '.join(MARK_KINDS)}")
        if status not in MARK_STATUSES:
            raise ValidationError(f"status must be one of {', '.join(MARK_STATUSES)}")
        if not self._target_exists(kind, target_id):
            raise ValidationError(f"no {kind} with id {target_id}")
        with self.conn:
            self.conn.execute(
                "INSERT INTO user_marks (target_kind, target_id, status, note, created_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(target_kind, target_id) DO UPDATE SET "
                "status = excluded.status, note = excluded.note, created_at = excluded.created_at",
                (kind, target_id, status, note, now_iso()),
            )

    def clear_mark(self, kind: str, target_id: int) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "DELETE FROM user_marks WHERE target_kind = ? AND target_id = ?", (kind, target_id)
            )
        return cur.rowcount > 0

    def get_mark(self, kind: str, target_id: int) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM user_marks WHERE target_kind = ? AND target_id = ?",
            (kind, target_id),
        ).fetchone()
        return row["status"] if row else None

    def is_dismissed(self, kind: str, target_id: int) -> bool:
        return self.get_mark(kind, target_id) == "dismissed"

    def list_marks(self, status: str, kind: str | None = None) -> list[dict]:
        sql = "SELECT * FROM user_marks WHERE status = ?"
        args: list = [status]
        if kind:
            sql += " AND target_kind = ?"
            args.append(kind)
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY id DESC", args).fetchall()]

    # --- maintenance ------------------------------------------------------------------------

    def restore(self, src: Path | str) -> Path:
        """Replace the live DB with a backup; returns the safety copy of what was replaced.

        The file is checked first and nothing changes if it is not a usable kitefinder DB.
        """
        src = Path(src).expanduser()
        if not src.is_file():
            raise ValidationError(f"no such file: {src}")
        if str(self.path) != ":memory:" and src.resolve() == self.path.resolve():
            raise ValidationError("that is the live database; pick a backup file")
        source = sqlite3.connect(f"file:{quote(str(src))}?mode=ro", uri=True)
        try:
            try:
                version = source.execute("PRAGMA user_version").fetchone()[0]
                has_profile = source.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'profile'"
                ).fetchone()
            except sqlite3.DatabaseError as e:
                raise ValidationError(f"{src.name} is not a kitefinder backup ({e})") from e
            if not has_profile:
                raise ValidationError(f"{src.name} is not a kitefinder backup")
            if version > SCHEMA_VERSION:
                raise ValidationError(
                    f"{src.name} was made by a newer version (schema v{version}); update first"
                )
            safety_dir = (
                self.path.parent / "backups" if str(self.path) != ":memory:" else src.parent
            )
            safety = self.backup(safety_dir / f"before-restore-{now_iso().replace(':', '')}.db")
            source.backup(self.conn)
        finally:
            source.close()
        self.migrate()
        return safety

    def backup(self, dest: Path | str) -> Path:
        """Consistent copy of the live DB (safe while the daemon is writing)."""
        dest = Path(dest).expanduser()
        if dest.is_dir() or dest.suffix != ".db":  # an existing or yet-to-exist folder
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = dest / f"kitefinder-{stamp}.db"
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        target = sqlite3.connect(str(tmp))
        try:
            self.conn.backup(target)
        finally:
            target.close()
        shutil.move(str(tmp), str(dest))
        return dest
