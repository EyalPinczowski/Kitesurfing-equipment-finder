"""The run ledger: prove nothing fell through the cracks.

After every run:
  1. every post collected this run (new or changed) is in a terminal stage — the counts must
     add up: fetched = prefilter_rejected + not_listing + extracted + error (+ backlog)
  2. every collected listing that can still be bought is matched or unmatched (never "new")
  3. every match for the active set / watched searches has been alerted (after the bot ran)
  4. every source says whether it collected everything, and why not
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .db import Database

TERMINAL = ("prefilter_rejected", "not_listing", "extracted", "error")


@dataclass
class Audit:
    run_id: int | None
    fetched: int = 0
    stages: dict = field(default_factory=dict)
    backlog: int = 0  # posts still waiting (quota / limit): not lost, processed next run
    unaccounted: int = 0  # the ledger doesn't add up (must be 0)
    listings_unsettled: int = 0  # listings still "new" after matching (must be 0)
    pending_alerts: int = 0
    sources: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def audit(db: Database, run_id: int | None = None, alerts_sent: bool = False) -> Audit:
    run = db.get_run(run_id)
    if run is None:
        return Audit(None, problems=["no run yet — run: kitefinder run"])
    a = Audit(run["id"])
    rows = db.conn.execute(
        "SELECT stage_status, COUNT(*) AS n FROM raw_posts WHERE run_id = ? GROUP BY stage_status",
        (run["id"],),
    ).fetchall()
    counts = {r["stage_status"]: r["n"] for r in rows}
    a.fetched = sum(counts.values())
    a.stages = {s: counts.get(s, 0) for s in TERMINAL}
    a.backlog = counts.get("fetched", 0)
    a.unaccounted = a.fetched - sum(a.stages.values()) - a.backlog
    if a.unaccounted:
        a.problems.append(f"{a.unaccounted} posts are in an unknown stage")
    if a.backlog:
        a.notes.append(f"{a.backlog} posts wait for the next run (daily Gemini budget or limit)")
    upgrades = db.conn.execute(
        "SELECT COUNT(*) FROM raw_posts WHERE stage_status = 'extracted' "
        "AND stage_reason IN ('rules:quota', 'rules:error')"
    ).fetchone()[0]
    if upgrades:
        a.notes.append(
            f"{upgrades} posts were read by the offline rules; Gemini re-reads them later"
        )
    if a.stages["error"]:
        reasons = db.conn.execute(
            "SELECT stage_reason FROM raw_posts WHERE run_id = ? AND stage_status = 'error' "
            "LIMIT 3",
            (run["id"],),
        ).fetchall()
        a.problems.append(
            f"{a.stages['error']} posts failed: " + "; ".join(r["stage_reason"] for r in reasons)
        )

    a.listings_unsettled = db.conn.execute(
        "SELECT COUNT(*) FROM listings WHERE status = 'new' AND sold = 0"
    ).fetchone()[0]
    if a.listings_unsettled:
        a.problems.append(f"{a.listings_unsettled} listings were never matched")

    from .pipeline import alert_targets

    items, queries = alert_targets(db)
    a.pending_alerts = len(db.pending_matches(items, queries))
    if a.pending_alerts and alerts_sent:
        a.problems.append(f"{a.pending_alerts} matches were not alerted")
    elif a.pending_alerts:
        a.notes.append(f"{a.pending_alerts} matches waiting to be sent")

    for s in run["stats"].get("sources", []):
        a.sources.append(s)
        if s["login_required"]:
            a.problems.append(f"{s['name']}: Facebook cookies expired — export them again")
        elif s["errors"]:
            a.problems.append(f"{s['name']}: {s['errors'][0]}")
        elif not s["complete"] and not s["partial_by_design"]:
            a.problems.append(f"{s['name']}: may be missing posts ({s['stopped_because']})")
    for e in run["errors"]:
        if not any(e.endswith(p.split(": ", 1)[-1]) for p in a.problems):
            a.problems.append(e)
    return a


def format_audit(a: Audit) -> str:
    if a.run_id is None:
        return "\n".join(a.problems)
    lines = [f"Run #{a.run_id}: {'✓ all accounted for' if a.ok else '⚠ needs attention'}"]
    s = a.stages
    lines.append(
        f"Posts: {a.fetched} new/changed → {s.get('extracted', 0)} with gear, "
        f"{s.get('not_listing', 0)} not for sale, {s.get('prefilter_rejected', 0)} off-topic, "
        f"{s.get('error', 0)} failed" + (f", {a.backlog} waiting" if a.backlog else "")
    )
    for src in a.sources:
        if src["errors"]:
            state = "✗"
        elif src["complete"]:
            state = "✓"
        else:
            state = "◐" if src["partial_by_design"] else "⚠"
        expected = f"/{src['expected']}" if src["expected"] is not None else ""
        lines.append(
            f"{state} {src['name']}: {src['got']}{expected} items, {src['new']} new, "
            f"{src['changed']} changed ({src['stopped_because'] or 'stopped'})"
        )
    lines.extend(f"⚠ {p}" for p in a.problems)
    lines.extend(f"• {n}" for n in a.notes)
    return "\n".join(lines)
