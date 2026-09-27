"""Family 3 (nothing missed) across a whole run: every post ends in a known stage, every
listing is matched or not, every match is alertable, and every source reports completeness."""

import pytest
from fakes import FakeTransport, gemini_reply
from world import CATEGORY, World

from kitefinder import audit, matcher, pipeline
from kitefinder.llm import gemini as gm
from kitefinder.models import Listing, OwnedItem, Profile, RecItem, Recommendation
from kitefinder.sizing import quiver


@pytest.fixture
def world():
    return World()


@pytest.fixture
def ready(db, world):
    """A rider with an active set, a watched search and the shop as a site."""
    profile = Profile(80, 86, 12, 25)
    db.save_profile(profile)
    db.save_recommendation(quiver.recommend_set(profile, [], "minimum"))
    db.add_site(CATEGORY)
    return db


def run(db, world, client=None, **kw):
    return pipeline.run(db, world.settings(), world.fetchers(), client, **kw)


# --- the ledger -------------------------------------------------------------------------------


def test_first_run_accounts_for_every_post(ready, world):
    report = run(ready, world)
    a = audit.audit(ready, report.run_id)
    assert a.fetched == 18  # 7 shop + 4 yad2 + 5 group + 2 marketplace
    assert a.stages == {"prefilter_rejected": 1, "not_listing": 1, "extracted": 16, "error": 0}
    assert a.unaccounted == 0 and a.backlog == 0 and a.listings_unsettled == 0
    assert ready.raw_posts_in_stage("fetched") == []
    names = [s["name"] for s in a.sources]
    assert names == [
        CATEGORY,
        "yad2: קייט",
        "facebook group: https://www.facebook.com/groups/123456/",
        "marketplace: kite",
    ]
    by = {s["name"]: s for s in a.sources}
    assert by[CATEGORY]["got"] == by[CATEGORY]["expected"] == 5 and by[CATEGORY]["complete"]
    assert by["marketplace: kite"]["partial_by_design"]
    assert a.ok, a.problems
    assert a.pending_alerts > 0 and "matches waiting to be sent" in a.notes[0]


def test_rerun_adds_nothing(ready, world):
    run(ready, world)
    counts = [
        ready.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("raw_posts", "listings", "matches")
    ]
    second = run(ready, world)
    a = audit.audit(ready, second.run_id)
    assert a.fetched == 0 and a.ok
    assert [
        ready.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("raw_posts", "listings", "matches")
    ] == counts
    assert all(s["new"] == 0 and s["changed"] == 0 for s in a.sources)


def test_edited_post_is_read_again_and_listing_updated(ready, world):
    run(ready, world)
    (lid,) = [
        x.id for x in ready.candidate_listings() if x.source == "facebook" and x.price_ils == 3200
    ]
    url = "https://m.facebook.com/groups/123456/"
    world.texts[url] = world.texts[url].replace('3200 ש"ח', '2900 ש"ח')
    report = run(ready, world)
    a = audit.audit(ready, report.run_id)
    assert a.fetched == 1 and a.stages["extracted"] == 1 and a.ok
    assert ready.get_listing(lid).price_ils == 2900  # same listing, new price


def test_item_removed_from_edited_post_is_retired(ready, world):
    run(ready, world)
    url = "https://m.facebook.com/groups/123456/"
    world.texts[url] = world.texts[url].replace("מוכר קייט North Orbit 12 מטר 2021", "איזה יום היה")
    run(ready, world)
    assert not [
        x for x in ready.candidate_listings() if x.source == "facebook" and x.price_ils == 3200
    ]
    gone = ready.conn.execute("SELECT COUNT(*) FROM listings WHERE status = 'gone'").fetchone()[0]
    assert gone == 1


def test_extraction_error_is_recorded_and_run_continues(ready, world, monkeypatch):
    real = pipeline.extract_post

    def flaky(text, *a, **k):
        if "Evo 10m" in text:
            raise RuntimeError("boom")
        return real(text, *a, **k)

    monkeypatch.setattr(pipeline, "extract_post", flaky)
    a = audit.audit(ready, run(ready, world).run_id)
    assert a.stages["error"] == 1 and a.stages["extracted"] == 15
    assert not a.ok and "1 posts failed: RuntimeError: boom" in a.problems[0]


def test_backlog_is_a_note_not_a_loss(ready, world):
    run_id = ready.start_run()
    pipeline.collect(ready, world.settings(), world.fetchers(), run_id)
    pipeline.process(ready, None, limit=5)
    a = audit.audit(ready, run_id)
    assert a.backlog == 13 and a.unaccounted == 0
    assert any("13 posts wait for the next run" in n for n in a.notes)
    pipeline.process(ready, None)
    assert audit.audit(ready, run_id).backlog == 0


def test_missing_cookies_reported_as_login_problem(ready, world, tmp_path):
    settings = world.settings()
    settings.fb_cookies_path = tmp_path / "missing.json"
    fetchers = world.fetchers(settings)
    del fetchers.overrides["facebook"]
    report = pipeline.run(ready, settings, fetchers, None)
    a = audit.audit(ready, report.run_id)
    fbrep = [s for s in a.sources if s["name"] == "facebook"][0]
    assert fbrep["login_required"] and any("cookies expired" in p for p in a.problems)


def test_broken_source_and_incomplete_source_are_problems(ready, world, monkeypatch):
    del world.texts["https://y2.example/?q=%D7%A7%D7%99%D7%99%D7%98&p=1"]
    monkeypatch.setattr(pipeline.fb, "MAX_GROUP_PAGES", 1)
    monkeypatch.setattr(pipeline.fb.collect_group, "__defaults__", (frozenset(), 1))
    a = audit.audit(ready, run(ready, world).run_id)
    assert any(p.startswith("yad2: קייט: yad2: HTTP 404") for p in a.problems)
    assert any("may be missing posts (max pages)" in p for p in a.problems)


def test_unsettled_listing_and_unsent_alerts_are_problems(ready, world):
    run(ready, world)
    ready.add_listing(Listing("kite", 2000, size=12, url="manual-1"))  # never matched
    a = audit.audit(ready, alerts_sent=True)
    assert "1 listings were never matched" in a.problems
    assert any("matches were not alerted" in p for p in a.problems)


def test_no_run_yet(db):
    assert audit.format_audit(audit.audit(db)) == "no run yet — run: kitefinder run"


def test_format_audit_full_text(ready, world):
    text = audit.format_audit(audit.audit(ready, run(ready, world).run_id))
    lines = text.splitlines()
    assert lines[0] == "Run #1: ✓ all accounted for"
    assert lines[1] == "Posts: 18 new/changed → 16 with gear, 1 not for sale, 1 off-topic, 0 failed"
    assert lines[2] == f"✓ {CATEGORY}: 5/5 items, 7 new, 0 changed (last page)"
    assert lines[3] == "✓ yad2: קייט: 4/4 items, 4 new, 0 changed (last page)"
    assert lines[5] == "◐ marketplace: kite: 2 items, 2 new, 0 changed (first page only)"
    assert lines[-1].startswith("• ") and lines[-1].endswith("matches waiting to be sent")


# --- matching and alerts ----------------------------------------------------------------------


def test_matches_are_for_the_active_set_and_watches_only(ready, world):
    run(ready, world)
    items, queries = pipeline.alert_targets(ready)
    before = ready.pending_matches(items, queries)
    profile = ready.get_profile()
    ready.save_recommendation(
        quiver.recommend_set(
            profile, [OwnedItem("kite", size=13), OwnedItem("kite", size=9)], "minimum"
        )
    )
    ready.set_active_recommendation(None)
    items, queries = pipeline.alert_targets(ready)
    assert len(ready.pending_matches(items, queries)) < len(
        before
    )  # old kite slots no longer alert
    ready.add_watch("kite 9m")
    pipeline.match(ready)
    watched = [
        m for m in ready.pending_matches(*pipeline.alert_targets(ready)) if m["query"] == "kite 9m"
    ]
    assert watched and all(ready.get_listing(m["listing_id"]).size in (8, 9, 10) for m in watched)


def test_one_alert_per_listing_and_dismissed_excluded(ready, world):
    run(ready, world)
    pending = ready.pending_matches(*pipeline.alert_targets(ready))
    ids = [m["listing_id"] for m in pending]
    assert len(ids) == len(set(ids))
    ready.set_mark("listing", ids[0], "dismissed")
    assert ids[0] not in [
        m["listing_id"] for m in ready.pending_matches(*pipeline.alert_targets(ready))
    ]
    ready.record_notification(pending[1]["id"], {"text": "sent"})
    left = [m["listing_id"] for m in ready.pending_matches(*pipeline.alert_targets(ready))]
    assert ids[1] not in left


def test_listing_statuses_after_matching(ready, world):
    run(ready, world)
    rows = dict(
        ready.conn.execute("SELECT status, COUNT(*) FROM listings GROUP BY status").fetchall()
    )
    assert (
        set(rows) <= {"matched", "unmatched", "sold"} and rows["matched"] > 0 and rows["sold"] == 3
    )
    sold = ready.conn.execute(
        "SELECT r.source_id FROM listings l JOIN raw_posts r ON r.id = l.raw_post_id "
        "WHERE l.status = 'sold' ORDER BY r.source_id"
    ).fetchall()
    assert [r[0] for r in sold] == [
        "https://shop.example.co.il/product/duotone-rebel-sls-2024/#v9003",  # sold-out size
        "https://shop.example.co.il/product/north-atmos-2023/",  # sold-out board
        "mp-555002",  # sold on Marketplace
    ]


def test_assessment_only_for_top_candidates_with_photos(ready, world, monkeypatch):
    monkeypatch.setattr(pipeline, "ASSESS_PER_RUN", 2)
    answer = {"score": 8, "flags": ["dings_scratches"], "verdict": "small dings"}
    transport = FakeTransport(lambda body: (200, gemini_reply(answer), {}))
    client = gm.GeminiClient("K", db=ready, transport=transport, sleep=lambda s: None)
    run(ready, world)  # posts read by the offline rules; this client only judges photos
    _, assessed = pipeline.match(ready, client, world.fetchers())
    vision_calls = [r for r in transport.requests if "inlineData" in str(r["body"])]
    assert assessed == 2 and len(vision_calls) == 2
    scored = [x for x in ready.candidate_listings() if ready.get_assessment(x.id)]
    assert len(scored) == 2 and all(ready.get_assessment(x.id)["score"] == 8 for x in scored)
    assert world.photos  # photos were downloaded through the fetcher


def test_assessment_stops_on_quota(ready, world):
    client = gm.GeminiClient("K", db=ready, rpd=1, transport=FakeTransport(lambda b: (200, gemini_reply({}), {})),
                             sleep=lambda s: None)  # fmt: skip
    ready.add_listing(Listing("kite", 2000, size=13, url="x"))
    lid = ready.candidate_listings()[0].id
    ready.set_listing_images(lid, ["https://scontent.x/1.jpg"])
    client.calls_today = lambda: 99  # budget already used
    assert pipeline.assess_candidates(ready, client, world.fetchers(), [lid]) == 0


# --- matcher ------------------------------------------------------------------------------------

KITE13 = RecItem("kite", 13, 12, 14, unit="m²", id=1)


def used(price, size=13, **kw):
    return Listing("kite", price, "North", size=size, is_new=False, **kw)


def test_market_median_needs_three_listings():
    two = [used(3000, id=1), used(3600, id=2)]
    assert matcher.market_price(KITE13, two, "used") is None
    three = two + [used(4200, id=3), Listing("kite", 7000, size=13, is_new=True, id=4)]
    assert matcher.market_price(KITE13, three, "used") == 3600  # the new one is not comparable
    assert matcher.typical_price(KITE13, two, "used") == (3300, "estimate")
    assert matcher.typical_price(KITE13, three, "used") == (3600, "market")
    assert matcher.typical_price(RecItem("wetsuit", None), [], "used") == (None, "")


def test_score_prefers_cheaper_better_condition_and_stated_facts():
    pool = [used(3300, id=9)]
    cheap = matcher.score_listing(KITE13, used(2500, id=1), pool)
    pricey = matcher.score_listing(KITE13, used(4500, id=2), pool)
    assert cheap.score > pricey.score
    assert "₪800 below the typical ₪3,300" in cheap.why and "₪1,200 above" in pricey.why
    good = matcher.score_listing(KITE13, used(3300, id=3), pool, condition_score=9)
    worn = matcher.score_listing(KITE13, used(3300, id=3), pool, condition_score=3)
    assert good.score > worn.score and "about the typical ₪3,300" in good.why
    unpriced = matcher.score_listing(KITE13, used(None, id=4), pool)
    assert "no price stated" in unpriced.why and unpriced.score < good.score
    no_year = matcher.score_listing(KITE13, used(3300, id=5), pool, min_year=2019)
    assert "year not stated" in no_year.why and no_year.score < good.score
    assert matcher.score_listing(KITE13, used(3300, size=9, id=6), pool) is None


def test_match_recommendation_keeps_best_item_per_listing():
    rec = Recommendation(
        profile=None, items=[RecItem("kite", 10, 9, 11, id=1), RecItem("kite", 8, 7, 9, id=2)]
    )
    out = matcher.match_recommendation(rec, [used(2500, size=9, id=1), used(2000, size=8, id=2)])
    assert [(s.listing.id, s.item.id) for s in sorted(out, key=lambda s: s.listing.id)] == [
        (1, 1),
        (2, 2),
    ]


@pytest.mark.parametrize(
    "query, t, lo, hi, sub",
    [("kite 12m", "kite", 11, 13, ""), ("קייט 9 מטר", "kite", 8, 10, ""), ("twin tip 138", "board", 135, 141, "twintip"),
     ("board", "board", 0, 1e9, ""), ("kite 2021", "kite", 0, 1e9, ""), ("bar 50cm", "bar", 45, 55, "")],
)  # fmt: skip
def test_parse_query(query, t, lo, hi, sub):
    item = matcher.parse_query(query)
    assert (item.type, item.size_min, item.size_max, item.subtype) == (t, lo, hi, sub)


def test_parse_query_harness_and_errors():
    assert matcher.parse_query("טרפז M").subtype == "M"
    assert matcher.parse_query("harness").subtype == "XS/S/M/L/XL/XXL"
    with pytest.raises(Exception, match="say what you're looking for"):
        matcher.parse_query("something cheap")


def test_search_any_size_wording():
    (s,) = matcher.search("kite", [used(3000, size=17, id=1)])
    assert s.why.startswith("a kite (any size)")


# --- regressions from the step-5 review --------------------------------------------------------


def test_foil_search_keeps_area_that_looks_like_a_year():
    item = matcher.parse_query("foil 2000")
    assert (item.size, item.size_min, item.size_max) == (2000, 1700, 2300)
    assert matcher.parse_query("kite 12m 2021").size == 12  # years still ignored elsewhere


def test_dismissed_before_matching_is_settled(ready, world):
    lid = ready.add_listing(Listing("kite", 2000, size=13, url="early"))
    ready.set_mark("listing", lid, "dismissed")
    a = audit.audit(ready, run(ready, world).run_id)
    assert ready.listing_status(lid) == "dismissed" and a.listings_unsettled == 0 and a.ok


def quota_client(db, calls_left):
    """A Gemini client whose extraction answers are a plain kite, with `calls_left` quota."""

    def responder(body):
        return 200, gemini_reply({"is_sale_post": True, "items": [
            {"type": "kite", "size": 12, "sold": False, "brand": "North"}]}), {}  # fmt: skip

    return gm.GeminiClient(
        "K", db=db, rpd=calls_left, transport=FakeTransport(responder), sleep=lambda s: None
    )


def test_when_the_quota_runs_out_posts_wait_for_gemini(ready, world):
    """Your choice (#20): no rules guesses on quota days — the posts wait, nothing is lost."""
    client = quota_client(ready, 3)
    run(ready, world, client)
    waiting = ready.conn.execute(
        "SELECT COUNT(*) FROM raw_posts WHERE stage_status = 'fetched'").fetchone()[0]  # fmt: skip
    assert waiting > 0
    assert ready.conn.execute(
        "SELECT COUNT(*) FROM raw_posts WHERE stage_reason LIKE 'rules:%'").fetchone()[0] == 0  # fmt: skip
    a = audit.audit(ready)
    assert a.ok and a.backlog == waiting and a.unaccounted == 0
    assert any("wait for the next run" in n for n in a.notes)
    still_out = pipeline.process(ready, client)  # same day: they keep waiting
    assert still_out["extracted"] == 0 and len(ready.raw_posts_in_stage("fetched")) == waiting
    client.rpd = 1000  # next day: quota back
    stages = pipeline.process(ready, client)
    assert stages["extracted"] + stages["not_listing"] + stages["prefilter_rejected"] == waiting
    assert ready.raw_posts_in_stage("fetched") == []


def test_legacy_seed_marker_still_respected(db):
    with db.conn:
        db.conn.execute("INSERT INTO meta (key, value) VALUES "
                        "('seeded_site:https://www.laguna.co.il/product-category/kites', 't')")  # fmt: skip
    assert db.seed_sites(["https://www.laguna.co.il/product-category/kites/"]) == []
    assert db.list_sites() == []  # the user had removed it; it stays removed


def test_edited_post_that_no_longer_fits_is_not_alerted(ready, world):
    run(ready, world)
    url = "https://m.facebook.com/groups/123456/"
    (lid,) = [x.id for x in ready.candidate_listings() if x.source == "facebook" and x.size == 12]
    assert lid in [m["listing_id"] for m in ready.pending_matches(*pipeline.alert_targets(ready))]
    world.texts[url] = world.texts[url].replace("North Orbit 12 מטר", "North Orbit 5 מטר")
    run(ready, world)
    assert lid not in [
        m["listing_id"] for m in ready.pending_matches(*pipeline.alert_targets(ready))
    ]


def test_market_price_computed_once_per_item(monkeypatch):
    calls = []
    real = matcher.market_price
    monkeypatch.setattr(matcher, "market_price", lambda *a: calls.append(1) or real(*a))
    rec = Recommendation(profile=None, items=[KITE13])
    matcher.match_recommendation(rec, [used(3000 + i, id=i) for i in range(1, 40)])
    assert len(calls) == 1  # all used: one condition, one item


def test_a_post_waits_for_gemini_at_most_two_days(ready, world):
    """Review fix: a key that never gets quota can't leave posts unread for good."""
    refuse = FakeTransport(lambda body: (429, {"error": {"message": "limit: 0"}}, {}))
    client = gm.GeminiClient("K", db=ready, transport=refuse, sleep=lambda s: None, max_retries=0)
    run(ready, world, client)  # Google answers 429 to every call: this key has no quota
    waiting = len(ready.raw_posts_in_stage("fetched"))
    assert waiting > 0
    with ready.conn:
        ready.conn.execute("UPDATE raw_posts SET first_seen_at = '2020-01-01T00:00:00+00:00'")
    stages = pipeline.process(ready, client)
    assert ready.raw_posts_in_stage("fetched") == []  # read by the rules after waiting
    assert stages["extracted"] > 0
    tagged = ready.conn.execute(
        "SELECT COUNT(*) FROM raw_posts WHERE stage_reason = 'rules:quota'").fetchone()[0]  # fmt: skip
    assert tagged == stages["extracted"]  # Gemini re-reads them when the quota is back
    client = quota_client(ready, 1000)  # a key with quota again
    assert pipeline.process(ready, client)["upgraded"] == min(tagged, pipeline.UPGRADE_PER_RUN)


def test_without_a_key_the_rules_read_everything_at_once(ready, world):
    run(ready, world, None)
    assert ready.raw_posts_in_stage("fetched") == []
    assert audit.audit(ready).backlog == 0
