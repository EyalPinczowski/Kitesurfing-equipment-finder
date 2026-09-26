import sqlite3

import pytest

from kitefinder.db import SCHEMA_VERSION, Database, normalize_url
from kitefinder.models import OwnedItem, Profile, RecItem, Recommendation, ValidationError

EXPECTED_TABLES = {
    "profile",
    "owned_equipment",
    "sites",
    "recommendations",
    "recommendation_items",
    "runs",
    "raw_posts",
    "listings",
    "listing_images",
    "image_assessments",
    "matches",
    "notifications",
    "user_marks",
    "llm_cache",
}


def _insert_listing(db):
    """Minimal raw post + listing so marks on listings have a valid target."""
    with db.conn:
        rp = db.conn.execute(
            "INSERT INTO raw_posts (source, source_id, content_hash, first_seen_at, last_seen_at) "
            "VALUES ('web', 'x1', 'h', 't', 't')"
        ).lastrowid
        return db.conn.execute(
            "INSERT INTO listings (raw_post_id, type, created_at) VALUES (?, 'kite', 't')", (rp,)
        ).lastrowid


# --- schema / migrations ----------------------------------------------------------------------


def test_fresh_db_has_full_schema(db):
    tables = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXPECTED_TABLES <= tables
    assert db.schema_version == SCHEMA_VERSION


def test_wal_mode_and_foreign_keys(db):
    assert db.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_reopen_keeps_data_and_does_not_remigrate(tmp_path, profile):
    path = tmp_path / "k.db"
    with Database(path) as d1:
        d1.save_profile(profile)
    with Database(path) as d2:
        assert d2.schema_version == SCHEMA_VERSION
        assert d2.get_profile() == profile


def test_refuses_newer_schema(tmp_path):
    path = tmp_path / "future.db"
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()
    with pytest.raises(RuntimeError, match="newer"):
        Database(path)


def test_in_memory_db_works():
    with Database(":memory:") as d:
        assert d.schema_version == SCHEMA_VERSION


# --- profile ----------------------------------------------------------------------------------


def test_profile_roundtrip_including_hebrew(db, profile):
    profile.spots = ["בת גלים", "Sdot Yam"]
    db.save_profile(profile)
    assert db.get_profile() == profile


def test_profile_update_overwrites_single_row(db, profile):
    db.save_profile(profile)
    profile.weight_kg = 75
    db.save_profile(profile)
    assert db.get_profile().weight_kg == 75
    assert db.conn.execute("SELECT COUNT(*) FROM profile").fetchone()[0] == 1


def test_missing_profile_is_none(db):
    assert db.get_profile() is None


@pytest.mark.parametrize(
    "change, msg",
    [
        ({"weight_kg": 10}, "weight"),
        ({"weight_kg": 200}, "weight"),
        ({"waist_cm": 20}, "waist"),
        ({"wind_min_kn": 30, "wind_max_kn": 20}, "below"),
        ({"wind_min_kn": 2}, "minimum wind"),
        ({"wind_max_kn": 80}, "maximum wind"),
        ({"skill": "pro"}, "skill"),
        ({"style": "wave"}, "style"),
        ({"condition_pref": "refurb"}, "condition"),
        ({"budget_ils": -1}, "budget"),
        ({"travel_km": -5}, "travel"),
    ],
)
def test_invalid_profile_rejected(db, profile, change, msg):
    for k, v in change.items():
        setattr(profile, k, v)
    with pytest.raises(ValidationError, match=msg):
        db.save_profile(profile)
    assert db.get_profile() is None


def test_profile_from_dict_ignores_unknown_keys():
    p = Profile.from_dict(
        {"weight_kg": 70, "waist_cm": 80, "wind_min_kn": 10, "wind_max_kn": 20, "x": 1}
    )
    assert p.weight_kg == 70


# --- owned equipment --------------------------------------------------------------------------


def test_owned_add_list_remove(db):
    kite = OwnedItem("kite", "North", "Orbit", 12, 2022)
    board = OwnedItem("board", "Duotone", "Jaime", 138, 2021)
    kid = db.add_owned(kite)
    db.add_owned(board)
    assert {i.type for i in db.list_owned()} == {"kite", "board"}
    assert [i.size for i in db.list_owned("kite")] == [12]
    assert db.remove_owned(kid) is True
    assert db.remove_owned(kid) is False
    assert [i.type for i in db.list_owned()] == ["board"]


def test_owned_sorted_by_type_then_size(db):
    for s in (12, 7, 9):
        db.add_owned(OwnedItem("kite", size=s))
    assert [i.size for i in db.list_owned("kite")] == [7, 9, 12]


@pytest.mark.parametrize(
    "item",
    [OwnedItem("surfski"), OwnedItem("kite", size=-3), OwnedItem("kite", year=1980)],
)
def test_owned_invalid_rejected(db, item):
    with pytest.raises(ValidationError):
        db.add_owned(item)
    assert db.list_owned() == []


# --- sites ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://Shop.co.il/kites/", "https://shop.co.il/kites"),
        ("shop.co.il/kites", "https://shop.co.il/kites"),
        ("http://shop.co.il/used?page=1#top", "http://shop.co.il/used?page=1"),
        ("  https://shop.co.il  ", "https://shop.co.il"),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


@pytest.mark.parametrize("bad", ["ftp://x.com", "not a url", "https://", "localhost"])
def test_normalize_url_rejects_garbage(bad):
    with pytest.raises(ValidationError):
        normalize_url(bad)


def test_sites_dedupe_and_remove(db):
    sid, created = db.add_site("https://shop.co.il/kites/")
    assert created
    sid2, created2 = db.add_site("HTTPS://shop.co.il/kites")
    assert (sid2, created2) == (sid, False)
    assert len(db.list_sites()) == 1
    db.record_site_check(sid, 14)
    assert db.list_sites()[0]["last_listing_count"] == 14
    assert db.remove_site(sid)
    assert db.list_sites() == []


def test_sites_enabled_only(db):
    a, _ = db.add_site("a.co.il")
    db.add_site("b.co.il")
    with db.conn:
        db.conn.execute("UPDATE sites SET enabled = 0 WHERE id = ?", (a,))
    assert [s["url"] for s in db.list_sites(enabled_only=True)] == ["https://b.co.il"]


# --- recommendations & history ----------------------------------------------------------------


def _rec(profile, sizes=(9, 12)):
    items = [RecItem("kite", s, s - 1, s + 1, 12, 25, f"covers wind for {s}m") for s in sizes]
    items.append(RecItem("board", 139, 136, 142, reason="twin tip for 80kg"))
    return Recommendation(profile=profile, items=items, explanation="two-kite quiver")


def test_recommendation_roundtrip(db, profile):
    rec = _rec(profile)
    rid = db.save_recommendation(rec)
    got = db.get_recommendation(rid)
    assert got.profile == profile
    assert got.explanation == "two-kite quiver"
    assert [(i.type, i.size) for i in got.items] == [("kite", 9), ("kite", 12), ("board", 139)]
    assert all(i.id and i.recommendation_id == rid for i in got.items)
    assert got.created_at


def test_history_keeps_profile_snapshot(db, profile):
    """Changing the profile later must not rewrite old recommendations."""
    rid = db.save_recommendation(_rec(profile))
    profile.weight_kg = 95
    db.save_profile(profile)
    assert db.get_recommendation(rid).profile.weight_kg == 80


def test_history_order_limit_and_latest(db, profile):
    ids = [db.save_recommendation(_rec(profile, (s,))) for s in (7, 9, 12)]
    single = Recommendation(profile=profile, items=[RecItem("kite", 10)], kind="single")
    db.save_recommendation(single)
    assert [r.id for r in db.list_recommendations(limit=3)] == [single.id, ids[2], ids[1]]
    assert db.latest_recommendation().id == ids[2]  # singles are not "the set"


def test_missing_recommendation(db):
    assert db.get_recommendation(999) is None
    assert db.latest_recommendation() is None


# --- favorites / dismissals -------------------------------------------------------------------


def test_mark_listing_favorite_then_dismiss(db):
    lid = _insert_listing(db)
    db.set_mark("listing", lid, "favorite", "great price")
    assert db.get_mark("listing", lid) == "favorite"
    assert [m["target_id"] for m in db.list_marks("favorite")] == [lid]
    db.set_mark("listing", lid, "dismissed")
    assert db.is_dismissed("listing", lid)
    assert db.list_marks("favorite") == []
    assert db.conn.execute("SELECT COUNT(*) FROM user_marks").fetchone()[0] == 1


def test_mark_rec_item(db, profile):
    db.save_recommendation(rec := _rec(profile))
    item_id = rec.items[0].id
    db.set_mark("rec_item", item_id, "dismissed")
    assert db.is_dismissed("rec_item", item_id)
    assert db.list_marks("dismissed", kind="listing") == []
    assert len(db.list_marks("dismissed", kind="rec_item")) == 1


def test_clear_mark(db):
    lid = _insert_listing(db)
    db.set_mark("listing", lid, "favorite")
    assert db.clear_mark("listing", lid)
    assert not db.clear_mark("listing", lid)
    assert db.get_mark("listing", lid) is None


@pytest.mark.parametrize(
    "kind, status, msg",
    [
        ("post", "favorite", "kind"),
        ("listing", "love", "status"),
        ("listing", "favorite", "no listing"),
    ],
)
def test_mark_validation(db, kind, status, msg):
    with pytest.raises(ValidationError, match=msg):
        db.set_mark(kind, 12345, status)


def test_deleting_listing_cascades_images(db):
    lid = _insert_listing(db)
    with db.conn:
        db.conn.execute("INSERT INTO listing_images (listing_id, url) VALUES (?, 'u')", (lid,))
        db.conn.execute("DELETE FROM listings WHERE id = ?", (lid,))
    assert db.conn.execute("SELECT COUNT(*) FROM listing_images").fetchone()[0] == 0


# --- backup -----------------------------------------------------------------------------------


def test_backup_to_dir_and_file(db, profile, tmp_path):
    db.save_profile(profile)
    out_dir = tmp_path / "backups"
    out_dir.mkdir()
    b1 = db.backup(out_dir)
    assert b1.parent == out_dir and b1.suffix == ".db"
    b2 = db.backup(tmp_path / "nested" / "copy.db")
    for path in (b1, b2):
        with Database(path) as copy:
            assert copy.get_profile() == profile


# --- regressions from the step-1 self-review ---------------------------------------------------


def test_failed_migration_leaves_nothing_half_built(tmp_path, monkeypatch):
    import kitefinder.db as dbmod

    broken = "CREATE TABLE a (x INTEGER); CREATE TABLE b (y INTEGER); THIS IS NOT SQL;"
    monkeypatch.setattr(dbmod, "MIGRATIONS", [broken])
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", 1)
    path = tmp_path / "half.db"
    with pytest.raises(sqlite3.OperationalError):
        Database(path)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0
    conn.close()
    # after fixing the migration, the same file opens cleanly
    monkeypatch.setattr(dbmod, "MIGRATIONS", ["CREATE TABLE a (x INTEGER);"])
    with Database(path) as d:
        assert d.schema_version == 1


def test_query_matches_without_rec_item_are_unique(db):
    lid = _insert_listing(db)
    sql = (
        "INSERT INTO matches (listing_id, rec_item_id, query, score, created_at) "
        "VALUES (?, NULL, 'kite 12m', 0.9, 't')"
    )
    with db.conn:
        db.conn.execute(sql, (lid,))
    with pytest.raises(sqlite3.IntegrityError):
        with db.conn:
            db.conn.execute(sql, (lid,))


def test_backup_to_folder_that_does_not_exist_yet(db, tmp_path):
    out = db.backup(tmp_path / "new-folder")
    assert out.parent == tmp_path / "new-folder" and out.suffix == ".db"


def test_seed_sites_added_once_and_removal_is_respected(db):
    seeds = ["https://kitelab.co.il", "https://www.laguna.co.il/product-category/kites/"]
    assert db.seed_sites(seeds) == [
        "https://kitelab.co.il",
        "https://www.laguna.co.il/product-category/kites",
    ]
    assert db.seed_sites(seeds) == []
    first = db.list_sites()[0]["id"]
    db.remove_site(first)
    assert db.seed_sites(seeds) == []  # removed seed stays removed
    assert [s["url"] for s in db.list_sites()] == [
        "https://www.laguna.co.il/product-category/kites"
    ]


def test_seed_site_already_added_by_user_is_not_duplicated(db):
    db.add_site("kitelab.co.il")
    assert db.seed_sites(["https://kitelab.co.il/"]) == []
    assert len(db.list_sites()) == 1


def test_upgrade_from_v2_keeps_data(tmp_path, monkeypatch):
    import kitefinder.db as dbmod

    path = tmp_path / "old.db"
    real = list(dbmod.MIGRATIONS)
    monkeypatch.setattr(dbmod, "MIGRATIONS", real[:2])
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", 2)
    with Database(path) as old:
        with old.conn:
            old.conn.execute(
                "INSERT INTO owned_equipment (type, size, created_at) VALUES ('board', 138, 't')"
            )
    monkeypatch.setattr(dbmod, "MIGRATIONS", real)
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", len(real))
    with Database(path) as new:
        (item,) = new.list_owned()
        assert (item.type, item.size, item.subtype) == ("board", 138, "")


def test_rec_item_subtype_and_unit_roundtrip(db, profile):
    rec = Recommendation(
        profile=profile, items=[RecItem("board", 100, subtype="foilboard", unit="L")]
    )
    db.save_recommendation(rec)
    (item,) = db.get_recommendation(rec.id).items
    assert (item.subtype, item.unit) == ("foilboard", "L")


def test_v4_converts_old_bar_and_foil_units(tmp_path, monkeypatch):
    import kitefinder.db as dbmod

    path = tmp_path / "v3.db"
    real = list(dbmod.MIGRATIONS)
    monkeypatch.setattr(dbmod, "MIGRATIONS", real[:3])
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", 3)
    with Database(path) as old:
        with old.conn:
            for t, size in (("bar", 0.5), ("bar", 52), ("foil", 0.15), ("foil", 1500), ("kite", 3)):
                old.conn.execute(
                    "INSERT INTO owned_equipment (type, size, created_at) VALUES (?, ?, 't')",
                    (t, size),
                )
    monkeypatch.setattr(dbmod, "MIGRATIONS", real)
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", len(real))
    with Database(path) as new:
        got = sorted((i.type, round(i.size, 6)) for i in new.list_owned())
    assert got == [("bar", 50), ("bar", 52), ("foil", 1500), ("foil", 1500), ("kite", 3)]


# --- step 2b: active set + restore --------------------------------------------------------------


def test_variant_roundtrip_and_latest_prefers_minimum(db, profile):
    m = Recommendation(profile=profile, items=[], variant="minimum")
    c = Recommendation(profile=profile, items=[], variant="comfortable")
    db.save_recommendation(m)
    db.save_recommendation(c)
    assert db.get_recommendation(c.id).variant == "comfortable"
    assert db.latest_recommendation().id == m.id
    db.set_active_recommendation(c.id)
    assert db.latest_recommendation().id == c.id
    db.set_active_recommendation(None)
    assert db.latest_recommendation().id == m.id


def test_active_set_that_was_deleted_falls_back(db, profile):
    m = Recommendation(profile=profile, items=[])
    c = Recommendation(profile=profile, items=[], variant="comfortable")
    db.save_recommendation(m)
    db.save_recommendation(c)
    db.set_active_recommendation(c.id)
    with db.conn:
        db.conn.execute("DELETE FROM recommendations WHERE id = ?", (c.id,))
    assert db.latest_recommendation().id == m.id


def test_restore_roundtrip_with_safety_backup(db, profile, tmp_path):
    db.save_profile(profile)
    db.add_owned(OwnedItem("kite", size=12))
    snap = db.backup(tmp_path / "snap.db")
    profile.weight_kg = 99
    db.save_profile(profile)
    db.add_owned(OwnedItem("kite", size=9))
    safety = db.restore(snap)
    assert db.get_profile().weight_kg == 80
    assert [i.size for i in db.list_owned()] == [12]
    assert safety.parent == db.path.parent / "backups"
    with Database(safety) as before:
        assert before.get_profile().weight_kg == 99  # nothing lost: the replaced DB is kept


def test_restore_rejects_non_kitefinder_files(db, profile, tmp_path):
    db.save_profile(profile)
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is not sqlite at all" * 100)
    other = tmp_path / "other.db"
    c = sqlite3.connect(other)
    c.execute("CREATE TABLE x (y)")
    c.close()
    for bad, msg in (
        (junk, "not a kitefinder backup"),
        (other, "not a kitefinder backup"),
        (tmp_path / "missing.db", "no such file"),
        (db.path, "live database"),
    ):
        with pytest.raises(ValidationError, match=msg):
            db.restore(bad)
    assert db.get_profile() == profile
    assert not (db.path.parent / "backups").exists()  # no safety copy for a rejected file


def test_restore_rejects_newer_schema(db, tmp_path):
    newer = tmp_path / "newer.db"
    with Database(newer) as d:
        d.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(ValidationError, match="newer version"):
        db.restore(newer)


def test_restore_old_backup_is_migrated(db, tmp_path, monkeypatch):
    import kitefinder.db as dbmod

    old_path = tmp_path / "old.db"
    real = list(dbmod.MIGRATIONS)
    monkeypatch.setattr(dbmod, "MIGRATIONS", real[:2])
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", 2)
    with Database(old_path) as old:
        with old.conn:
            old.conn.execute(
                "INSERT INTO owned_equipment (type, size, created_at) VALUES ('bar', 0.5, 't')"
            )
    monkeypatch.setattr(dbmod, "MIGRATIONS", real)
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", len(real))
    db.restore(old_path)
    assert db.schema_version == len(real)
    assert db.list_owned()[0].size == 50  # v4 unit conversion ran on the restored data


def test_restore_of_non_sqlite_file_closes_it(db, tmp_path, monkeypatch):
    import kitefinder.db as dbmod

    opened = []
    real_connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(dbmod.sqlite3, "connect", tracking_connect)
    notes = tmp_path / "notes.txt"
    notes.write_text("not a database " * 200)
    with pytest.raises(ValidationError):
        db.restore(notes)
    (conn,) = opened
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")


def test_prices_and_budget_roundtrip(db, profile):
    rec = Recommendation(
        profile=profile,
        items=[RecItem("kite", 9, est_price_ils=2800)],
        variant="one_kite",
        price_condition="new",
        budget_ils=9000,
    )
    db.save_recommendation(rec)
    got = db.get_recommendation(rec.id)
    assert (got.variant, got.price_condition, got.budget_ils) == ("one_kite", "new", 9000)
    assert got.items[0].est_price_ils == 2800


# --- listings ---------------------------------------------------------------------------------


def test_add_listing_roundtrip_and_anonymous_posts_do_not_collide(db):
    from kitefinder.models import Listing

    a = Listing("kite", 2600, "North", "Orbit", 13, year=2022, is_new=False, location="Haifa",
                description="nice", source="yad2", url="https://yad2.co.il/i/2", seller="Noa")  # fmt: skip
    db.add_listing(a)
    for _ in range(3):  # same source, no URL, same second
        db.add_listing(Listing("harness", 600, "ION", size_label="M", source="facebook"))
    got = db.candidate_listings()
    assert len(got) == 4
    first = got[0]
    assert (first.brand, first.size, first.is_new, first.source, first.url, first.seller) == (
        "North",
        13,
        False,
        "yad2",
        "https://yad2.co.il/i/2",
        "Noa",
    )
    assert got[1].size_label == "M" and got[1].is_new is None


def test_candidates_exclude_sold_and_dismissed(db):
    from kitefinder.models import Listing

    keep = db.add_listing(Listing("kite", 1, size=9, url="u1"))
    db.add_listing(Listing("kite", 1, size=9, url="u2", sold=True))
    gone = db.add_listing(Listing("kite", 1, size=9, url="u3"))
    fav = db.add_listing(Listing("kite", 1, size=9, url="u4"))
    db.set_mark("listing", gone, "dismissed")
    db.set_mark("listing", fav, "favorite")
    assert [x.id for x in db.candidate_listings()] == [keep, fav]


def test_multi_item_post_shares_raw_post(db):
    from kitefinder.models import Listing

    db.add_listing(Listing("kite", 1, size=9, source="facebook"), source_id="post-1", item_index=0)
    db.add_listing(Listing("kite", 1, size=12, source="facebook"), source_id="post-1", item_index=1)
    assert db.conn.execute("SELECT COUNT(*) FROM raw_posts").fetchone()[0] == 1
    assert len(db.candidate_listings()) == 2


def test_listing_flags_method_bundle_roundtrip_and_images(db):
    from kitefinder.models import Listing

    lid = db.add_listing(Listing("kite", None, "North", size=12, url="p1", flags=["sold_as_bundle"],
                                 extracted_by="gemini", bundle_price_ils=4500))  # fmt: skip
    (got,) = db.candidate_listings()
    assert (got.flags, got.extracted_by, got.bundle_price_ils) == (
        ["sold_as_bundle"],
        "gemini",
        4500,
    )
    db.add_listing_images(lid, ["https://img/1.jpg", "https://img/2.jpg"])
    assert db.listing_images(lid) == ["https://img/1.jpg", "https://img/2.jpg"]
    assert db.get_assessment(lid) is None
    db.save_assessment(lid, 7.0, ["uv_faded"], "tired cloth")
    db.save_assessment(lid, 6.5, ["uv_faded", "tear"], "small tear")  # replaces
    assert db.get_assessment(lid) == {
        "score": 6.5,
        "flags": ["uv_faded", "tear"],
        "verdict": "small tear",
    }


def test_same_post_saved_twice_updates_items(db):
    """Review regression: re-saving a post crashed on UNIQUE(raw_post_id, item_index)."""
    from kitefinder.models import Listing

    first = db.add_listing(Listing("kite", 3000, size=12, source="facebook"), source_id="p1")
    again = db.add_listing(
        Listing("kite", 2500, size=12, source="facebook"), source_id="p1"
    )  # price drop
    assert first == again
    (listing,) = db.candidate_listings()
    assert listing.price_ils == 2500


# --- raw posts from collectors ------------------------------------------------------------------


def test_upsert_raw_post_new_seen_changed(db):
    from kitefinder.collectors.base import RawPost

    p = RawPost("yad2", "a1", "קייט 12 מטר 3200", url="u", author="x", image_urls=["i1"])
    rid, state = db.upsert_raw_post(p)
    assert state == "new" and db.known_source_ids("yad2") == {"a1"}
    assert db.upsert_raw_post(p) == (rid, "seen")
    db.set_stage(rid, "extracted", "ok")
    edited = RawPost("yad2", "a1", "קייט 12 מטר 2900 (ירד מחיר)", url="u", image_urls=["i1"])
    assert db.upsert_raw_post(edited) == (rid, "changed")
    (row,) = db.raw_posts_in_stage("fetched")
    assert (
        row["text"].endswith("(ירד מחיר)")
        and row["stage_reason"] == "changed"
        and row["image_urls"] == ["i1"]
    )
    assert db.raw_posts_in_stage("extracted") == []
    db.upsert_raw_post(RawPost("facebook", "a1", "other source, same id"))
    assert (
        db.known_source_ids("yad2") == {"a1"}
        and len(db.raw_posts_in_stage("fetched", limit=5)) == 2
    )


def test_upgrade_to_v9_keeps_raw_posts(tmp_path, monkeypatch):
    import kitefinder.db as dbmod

    path = tmp_path / "v8.db"
    real = list(dbmod.MIGRATIONS)
    monkeypatch.setattr(dbmod, "MIGRATIONS", real[:8])
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", 8)
    with Database(path) as old:
        with old.conn:
            old.conn.execute("INSERT INTO raw_posts (source, source_id, content_hash, first_seen_at, "
                             "last_seen_at) VALUES ('yad2', 'x', 'h', 't', 't')")  # fmt: skip
    monkeypatch.setattr(dbmod, "MIGRATIONS", real)
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", len(real))
    with Database(path) as new:
        (row,) = new.raw_posts_in_stage("fetched")
        assert row["hints"] == {}
