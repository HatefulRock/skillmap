"""The run-health check flags drops and repeated failures, and stays quiet otherwise."""

from datetime import UTC, datetime, timedelta

from skillmap import health

T0 = datetime(2026, 10, 1, 2, tzinfo=UTC)


def history(*runs):
    rows = []
    for week, run in enumerate(runs):
        for source, fetched, error in run:
            rows.append({"source": source, "fetched": fetched, "error": error, "ran_at": T0 + timedelta(weeks=week)})
    return rows


def row(source, fetched, error=None):
    return {"source": source, "fetched": fetched, "error": error}


def test_quiet_when_nothing_changed():
    past = history([("greenhouse:a", 100, None), ("hn", 5000, None)], [("greenhouse:a", 110, None), ("hn", 40, None)])
    assert health.check([row("greenhouse:a", 105), row("hn", 30)], past) == []


def test_a_platform_that_halves_is_flagged_but_incremental_sources_are_not():
    past = history([("greenhouse:a", 400, None), ("mcf", 2000, None)], [("greenhouse:a", 420, None), ("mcf", 300, None)])
    problems = health.check([row("greenhouse:a", 100), row("mcf", 50)], past)
    assert len(problems) == 1 and problems[0].startswith("greenhouse: fetched 100")


def test_small_platforms_and_first_runs_are_not_flagged():
    assert health.check([row("workable:x", 2)], history([("workable:x", 30, None)])) == []
    assert health.check([row("workday:new", 0)], []) == []


def test_a_board_failing_three_runs_in_a_row():
    err = "HTTPStatusError: 404"
    past = history([("lever:x", 0, err), ("lever:y", 5, None)], [("lever:x", 0, err), ("lever:y", 0, err)])
    problems = health.check([row("lever:x", 0, err), row("lever:y", 0, err)], past)
    assert problems == [f"lever:x: failed 3 runs in a row (latest: {err})"]


def test_skips_are_not_failures():
    past = history([("adzuna", 0, "skipped: ADZUNA_APP_ID / ADZUNA_APP_KEY not set")])
    current = [row("adzuna", 0, "skipped: ADZUNA_APP_ID / ADZUNA_APP_KEY not set"),
               row("greenhouse:a", 0, "skipped: out of time this run")]
    assert health.check(current, past) == []


def test_a_source_that_fails_outright():
    assert health.check([row("hn", 0, "ConnectError: boom")], []) == ["hn: failed (ConnectError: boom)"]


def test_several_runs_on_one_day_count_once():
    day = [{"source": "ashby:a", "fetched": 300, "error": None, "ran_at": T0},
           {"source": "ashby:a", "fetched": 310, "error": None, "ran_at": T0 + timedelta(hours=3)}]
    assert health.check([row("ashby:a", 100)], day) == ["ashby: fetched 100, against a median of 310 over the last 1 runs"]


def test_partial_runs_do_not_compare_totals():
    past = history([("greenhouse:a", 400, None)])
    assert health.check([row("greenhouse:a", 10)], past, compare_totals=False) == []
