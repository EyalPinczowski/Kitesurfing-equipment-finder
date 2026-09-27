"""One run of the agent: collect → prefilter → extract → assess → match → (alerts in the bot).

Every post that enters a run ends in a terminal stage (prefilter_rejected / not_listing /
extracted / error) with a reason, and every source records whether its collection was
complete — `audit.py` checks both after the run.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from . import prefilter
from .collectors import facebook as fb
from .collectors import web, yad2
from .collectors.base import CollectorError, CollectResult, Fetch, HttpFetcher, LoginRequired
from .db import Database
from .llm.extract import extract_post
from .llm.gemini import GeminiClient, LLMError
from .matcher import match_recommendation, search

PARTIAL_BY_DESIGN = ("first page only",)
ASSESS_PER_RUN = 5  # photo checks per run (each costs one Gemini call)


@dataclass
class SourceReport:
    name: str
    kind: str = ""
    pages: int = 0
    expected: int | None = None
    got: int = 0
    new: int = 0
    changed: int = 0
    seen: int = 0
    stopped_because: str = ""
    complete: bool = True
    partial_by_design: bool = False
    errors: list[str] = field(default_factory=list)
    login_required: bool = False


@dataclass
class RunReport:
    run_id: int
    sources: list[SourceReport] = field(default_factory=list)
    stages: dict = field(default_factory=dict)  # stage -> posts that ended there this run
    listings: int = 0
    matches_new: int = 0
    assessed: int = 0
    errors: list[str] = field(default_factory=list)

    def to_stats(self) -> dict:
        return {
            "sources": [asdict(s) for s in self.sources],
            "stages": self.stages,
            "listings": self.listings,
            "matches_new": self.matches_new,
            "assessed": self.assessed,
        }


# --- fetchers ---------------------------------------------------------------------------------


class Fetchers:
    """HTTP fetchers per source, built lazily (Facebook needs the cookies file)."""

    def __init__(self, settings, overrides: dict[str, Fetch] | None = None):
        self.settings = settings
        self.overrides = overrides or {}
        self._cache: dict[str, Fetch] = {}

    def get(self, name: str) -> Fetch:
        if name in self.overrides:
            return self.overrides[name]
        if name not in self._cache:
            cfg = self.settings.sources
            if name == "facebook":
                fbc = cfg.get("facebook") or {}
                cookies = fb.load_cookies(
                    self.settings.fb_cookies_path or "secrets/fb_cookies.json"
                )
                self._cache[name] = HttpFetcher(
                    cookies, fbc.get("min_delay_s", 8), fbc.get("max_delay_s", 20)
                )
            else:
                self._cache[name] = HttpFetcher()
        return self._cache[name]

    def photo(self, url: str) -> bytes:
        fetch = self.overrides.get("photo")
        if fetch is not None:
            return fetch(url)  # type: ignore[return-value]
        fetcher = self.get("facebook" if "fbcdn" in url or "scontent" in url else "web")
        return fetcher.get_bytes(url)  # type: ignore[union-attr]


# --- collect ----------------------------------------------------------------------------------


def _store(db: Database, result: CollectResult, name: str, run_id: int) -> SourceReport:
    rep = SourceReport(
        name=name,
        kind=result.kind,
        pages=result.pages,
        expected=result.expected_count,
        got=len(result.counted_ids),
        stopped_because=result.stopped_because,
        complete=result.complete,
        partial_by_design=result.stopped_because in PARTIAL_BY_DESIGN and not result.errors,
        errors=list(result.errors),
    )
    for post in result.posts:
        _, state = db.upsert_raw_post(post, run_id)
        setattr(rep, state, getattr(rep, state) + 1)
    return rep


def _fb_group_url(db: Database, url: str, fetch: Fetch) -> str:
    """Share links are resolved once and remembered."""
    key = f"fb_group:{url}"
    cached = db.meta_get(key)
    if cached:
        return cached
    resolved = fb.resolve_group(url, fetch)
    db.meta_set(key, resolved)
    return resolved


def collect(db: Database, settings, fetchers: Fetchers, run_id: int,
            only: str | None = None) -> list[SourceReport]:  # fmt: skip
    cfg = settings.sources
    reports: list[SourceReport] = []

    if only in (None, "sites"):
        for site in db.list_sites(enabled_only=True):
            result = web.collect_site(site["url"], fetchers.get("web"))
            reports.append(_store(db, result, site["url"], run_id))
            db.record_site_check(site["id"], len(result.counted_ids))

    ycfg = cfg.get("yad2") or {}
    if only in (None, "yad2") and ycfg.get("enabled", True):
        for q in ycfg.get("queries") or []:
            result = yad2.collect_query(
                q, fetchers.get("yad2"), ycfg.get("search_url") or yad2.DEFAULT_SEARCH_URL
            )
            reports.append(_store(db, result, f"yad2: {q}", run_id))

    fcfg = cfg.get("facebook") or {}
    if only in (None, "facebook") and fcfg.get("enabled", True):
        try:
            fetch = fetchers.get("facebook")
        except LoginRequired as e:
            reports.append(
                SourceReport("facebook", complete=False, errors=[str(e)], login_required=True)
            )
            return reports
        known = db.known_source_ids("facebook")
        for url in fcfg.get("groups") or []:
            try:
                group = _fb_group_url(db, url, fetch)
            except CollectorError as e:
                rep = SourceReport(f"facebook group: {url}", complete=False, errors=[str(e)])
                rep.login_required = isinstance(e, LoginRequired)
                reports.append(rep)
                continue
            result = fb.collect_group(group, fetch, known)
            rep = _store(db, result, f"facebook group: {url}", run_id)
            rep.login_required = any("log in" in e.lower() for e in result.errors)
            reports.append(rep)
        location = fcfg.get("marketplace_location", "telaviv")
        for q in fcfg.get("marketplace_queries") or []:
            result = fb.collect_marketplace(q, fetch, location)
            rep = _store(db, result, f"marketplace: {q}", run_id)
            rep.login_required = any("log in" in e.lower() for e in result.errors)
            reports.append(rep)
    return reports


# --- process ----------------------------------------------------------------------------------


UPGRADE_PER_RUN = 30  # rules-read posts re-read by Gemini per run once the quota is back


def process(db: Database, client: GeminiClient | None, limit: int | None = None) -> Counter:
    """Every waiting post → prefilter → extraction → listings. Returns stage counts.

    Posts the rules had to read because Gemini was out of quota are re-read by Gemini on a
    later run (a few per run), so a busy day doesn't leave them rules-only for good.
    """
    stages: Counter = Counter()
    rows = db.raw_posts_in_stage("fetched", limit)
    if client is not None and client.calls_today() < client.rpd:
        waiting = db.raw_posts_in_stage("extracted")
        upgrades = [r for r in waiting if r["stage_reason"] in ("rules:quota", "rules:error")]
        upgrades = upgrades[:UPGRADE_PER_RUN]
        rows += upgrades
        stages["upgraded"] = 0
    for row in rows:
        pid = row["id"]
        try:
            decision = prefilter.check(row["text"])
            if not decision.keep:
                db.set_stage(pid, "prefilter_rejected", decision.reason)
                db.retire_listings(pid, 0)
                stages["prefilter_rejected"] += 1
                continue
            result = extract_post(
                row["text"],
                client,
                source=row["source"],
                url=row["url"],
                seller=row["author"],
                single_item=bool(row["hints"].get("single_item")),
            )
            if result.status != "listing":
                db.set_stage(pid, "not_listing", result.reason)
                db.retire_listings(pid, 0)
                stages["not_listing"] += 1
                continue
            for i, listing in enumerate(result.listings):
                lid = db.add_listing(listing, source_id=row["source_id"], item_index=i)
                db.set_listing_images(lid, row["image_urls"])
            db.retire_listings(pid, len(result.listings))
            fallback = next((f for f in result.flags if f.startswith("fallback: Gemini")), "")
            reason = result.method
            if fallback:  # re-read by Gemini on a later run, when the quota is back
                reason = "rules:quota" if "quota" in fallback else "rules:error"
            db.set_stage(pid, "extracted", reason)
            if row["stage_status"] == "extracted":  # an upgrade of a rules-read post
                stages["upgraded"] += 1
            else:
                stages["extracted"] += 1
        except Exception as e:  # one bad post must not stop the run  # noqa: BLE001
            db.set_stage(pid, "error", f"{type(e).__name__}: {e}"[:300])
            stages["error"] += 1
    return stages


# --- assess + match ---------------------------------------------------------------------------


def _conditions(db: Database, listings) -> dict[int, float]:
    out = {}
    for listing in listings:
        a = db.get_assessment(listing.id)
        if a and a["score"] is not None:
            out[listing.id] = a["score"]
    return out


def assess_candidates(db: Database, client: GeminiClient | None, fetchers: Fetchers,
                      listing_ids: list[int],
    limit: int | None = None,) -> int:  # fmt: skip
    """Photo condition for the best new candidates (not every listing: quota)."""
    from .llm import vision

    if client is None:
        return 0
    limit = ASSESS_PER_RUN if limit is None else limit  # read at call time (configurable)
    done = 0
    for lid in listing_ids:
        if done >= limit:
            break
        if db.get_assessment(lid) is not None:
            continue
        urls = db.listing_images(lid)[: vision.MAX_IMAGES]
        if not urls:
            continue
        photos = []
        for url in urls:
            try:
                photos.append(fetchers.photo(url))
            except CollectorError:
                continue
        listing = db.get_listing(lid)
        what = " ".join(
            str(x) for x in (listing.type, listing.brand, listing.model, listing.size or "") if x
        )
        try:
            result = vision.assess(client, photos, what)
        except LLMError:
            break  # quota or API trouble: try again next run
        if result.photos_used:
            db.save_assessment(lid, result.score, result.flags, result.verdict)
            done += 1
    return done


def match(
    db: Database, client: GeminiClient | None = None, fetchers: Fetchers | None = None
) -> tuple[int, int]:
    """Match candidates to the active set and watched searches. Returns (new matches, assessed)."""
    profile = db.get_profile()
    rec = db.latest_recommendation()
    listings = db.candidate_listings()
    pref = profile.condition_pref if profile else "both"
    min_year = profile.min_year if profile else None
    scored = (
        match_recommendation(rec, listings, _conditions(db, listings), pref, min_year)
        if rec
        else []
    )
    assessed = 0
    if fetchers is not None and scored:
        assessed = assess_candidates(db, client, fetchers, [s.listing.id for s in scored])
        if assessed:  # rescore with the new condition scores
            scored = match_recommendation(rec, listings, _conditions(db, listings), pref, min_year)
    new = 0
    matched: set[int] = set()
    keep: set[tuple] = set()
    for s in scored:
        _, created = db.upsert_match(s.listing.id, s.item.id, "", s.score, s.why)
        new += created
        matched.add(s.listing.id)
        keep.add((s.listing.id, s.item.id or 0, ""))
    for q in db.watches():
        for s in search(q, listings, _conditions(db, listings), pref, min_year):
            _, created = db.upsert_match(s.listing.id, None, q, s.score, s.why)
            new += created
            matched.add(s.listing.id)
            keep.add((s.listing.id, 0, q))
    db.prune_matches(keep)  # unsent matches that no longer fit (edited post, new set)
    db.set_listing_status(matched, "matched")
    db.set_listing_status([x.id for x in listings if x.id not in matched], "unmatched")
    with db.conn:  # sold and dismissed items are settled too: the ledger covers every listing
        db.conn.execute("UPDATE listings SET status = 'sold' WHERE sold = 1 AND status != 'gone'")
        db.conn.execute(
            "UPDATE listings SET status = 'dismissed' WHERE status NOT IN ('gone', 'sold') "
            "AND id IN (SELECT target_id FROM user_marks "
            "WHERE target_kind = 'listing' AND status = 'dismissed')"
        )
    return new, assessed


def alert_targets(db: Database) -> tuple[list[int], list[str]]:
    """What alerts are for: the active set's items and the watched searches."""
    rec = db.latest_recommendation()
    return ([i.id for i in rec.items] if rec else []), db.watches()


# --- one full run -----------------------------------------------------------------------------


def run(db: Database, settings, fetchers: Fetchers, client: GeminiClient | None,
        trigger: str = "manual", only: str | None = None,
        on_sources: Callable[[list[SourceReport]], None] | None = None) -> RunReport:  # fmt: skip
    run_id = db.start_run(trigger)
    report = RunReport(run_id)
    try:
        report.sources = collect(db, settings, fetchers, run_id, only)
        if on_sources:
            on_sources(report.sources)
        report.stages = dict(process(db, client))
        report.listings = len(db.candidate_listings())
        report.matches_new, report.assessed = match(db, client, fetchers)
    except Exception as e:  # record, never lose the run  # noqa: BLE001
        report.errors.append(f"{type(e).__name__}: {e}")
    errors = report.errors + [f"{s.name}: {e}" for s in report.sources for e in s.errors]
    db.finish_run(run_id, report.to_stats(), errors)
    return report
