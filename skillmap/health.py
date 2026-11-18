"""Run health: what broke this run, compared with earlier runs.

A company board can quietly start returning nothing (a renamed board, a changed API, a site
that now blocks us) and the published numbers just shrink. After each run this compares every
platform and source with its own recent history and lists what looks wrong; the refresh
workflow turns that list into a GitHub issue, and closes the issue once a run is clean.

Checks:
- a platform whose boards are read in full every run (Greenhouse, Workday, ...) fetched less
  than half its median of the previous runs;
- a source that failed outright this run (skips for missing keys or time are not failures);
- a board that failed in each of its last three runs.

Before publishing, `publish_check` compares the new numbers with the published ones: a sudden
collapse in live postings, or a top skill whose share jumps by more than SHARE_JUMP in one run
under the same taxonomy, is far likelier a bug than the market. The run then keeps the old
files and reports why, until the change is confirmed with `--force-publish`.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime

# Incremental sources read "what is new since last run", so their counts swing by design.
INCREMENTAL = {"hn", "mcf", "adzuna"}
DROP_RATIO = 0.5
MIN_BASELINE = 50  # ignore drops on platforms too small for a ratio to mean much
FAILING_RUNS = 3
LIVE_DROP_RATIO = 0.5  # live postings below half the published count
SHARE_JUMP = 0.12      # a top skill moving more than 12 points in one run
TOP_SKILLS = 20


def _skipped(error: str | None) -> bool:
    return bool(error) and (error.startswith("skipped") or error.startswith("stopped: out of time"))


def _per_run(history: list[dict]) -> list[dict[str, dict]]:
    """Earlier runs, oldest first, as {source name: row}. Runs are told apart by day; when one
    day has several runs, its last row per source wins."""
    days: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in sorted(history, key=lambda r: r["ran_at"]):
        ran_at = row["ran_at"]
        day = ran_at.date().isoformat() if isinstance(ran_at, datetime) else str(ran_at)[:10]
        days[day][row["source"]] = row
    return [days[d] for d in sorted(days)]


def _platform(name: str) -> str:
    return name.split(":", 1)[0]


def check(current: list[dict], history: list[dict], compare_totals: bool = True) -> list[str]:
    """`current`: this run's rows (source, fetched, error), one per board or source.
    `history`: earlier runs' rows, each with `ran_at`. `compare_totals`: off for a run that read
    only some boards, whose totals would look like drops. Returns human-readable problems."""
    problems: list[str] = []
    runs = _per_run(history)

    # Whole platforms/sources: totals this run against the median of earlier runs.
    def totals(rows: dict[str, dict] | list[dict]) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for row in rows.values() if isinstance(rows, dict) else rows:
            if not _skipped(row["error"]):
                out[_platform(row["source"])] += row["fetched"]
        return out

    now = totals(current)
    before = [totals(run) for run in runs]
    for platform, count in sorted(now.items()):
        if platform in INCREMENTAL or not compare_totals:
            continue
        past = [t[platform] for t in before if t.get(platform)]
        if not past:
            continue
        baseline = statistics.median(past)
        if baseline >= MIN_BASELINE and count < baseline * DROP_RATIO:
            problems.append(f"{platform}: fetched {count:,}, against a median of {baseline:,.0f} over the "
                            f"last {len(past)} runs")

    # Whole sources that failed outright (the ones that are not one-board-per-row).
    for row in current:
        if ":" not in row["source"] and row["error"] and not _skipped(row["error"]):
            problems.append(f"{row['source']}: failed ({row['error']})")

    # Boards failing run after run.
    for row in current:
        name = row["source"]
        if ":" not in name or not row["error"] or _skipped(row["error"]):
            continue
        earlier = [run[name] for run in runs if name in run and not _skipped(run[name]["error"])]
        recent = earlier[-(FAILING_RUNS - 1):]
        if len(recent) == FAILING_RUNS - 1 and all(r["error"] for r in recent):
            problems.append(f"{name}: failed {FAILING_RUNS} runs in a row (latest: {row['error']})")
    return problems


def publish_check(previous: dict | None, data: dict) -> list[str]:
    """Why the new numbers look too different from the published ones to publish unseen."""
    if not previous or not previous.get("current", {}).get("n"):
        return []
    problems = []
    before, now = previous["current"]["n"], data["current"]["n"]
    if now < before * LIVE_DROP_RATIO:
        problems.append(f"live tech postings fell from {before:,} to {now:,}")
    if previous.get("meta", {}).get("taxonomy_version") == data["meta"]["taxonomy_version"]:
        shares = {s["name"]: s["share"] for s in data["current"]["skills"]}
        for skill in previous["current"]["skills"][:TOP_SKILLS]:
            new = shares.get(skill["name"], 0.0)
            if abs(new - skill["share"]) > SHARE_JUMP:
                problems.append(f"{skill['name']} moved from {skill['share']:.0%} to {new:.0%} of live postings")
    return problems


def markdown(problems: list[str], run_url: str | None = None, held: list[str] | None = None) -> str:
    lines = []
    if held:
        lines += ["**Not published.** The new numbers differ too much from the published ones:", ""]
        lines += [f"- {p}" for p in held]
        lines += ["", "The site keeps last week's numbers. If the change is real, re-run the refresh with "
                  "`force_publish` ticked.", ""]
    if problems:
        lines += ["The weekly refresh found problems with these sources:", ""]
        lines += [f"- {p}" for p in problems]
        if not held:
            lines += ["", "The published numbers still include every source that worked."]
    if run_url:
        lines.append(f"Run: {run_url}")
    return "\n".join(lines)
