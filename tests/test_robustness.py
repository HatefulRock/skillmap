"""A run survives rate limits and slow sources: it skips, stops in time, and resumes later."""

from datetime import date

import httpx

from skillmap import runner
from skillmap.extract.skills import Taxonomy
from skillmap.http import PoliteClient
from skillmap.runner import Budget, collect
from skillmap.store import MemoryStore

TODAY = date(2026, 11, 18)
TAX = Taxonomy.load()


def client_for(handler):
    client = PoliteClient(min_interval=0, transport=httpx.MockTransport(handler), retries=0)
    client.sleep = lambda s: None
    return client


def jobs(n, prefix):
    return {"jobs": [{"id": f"{prefix}{i}", "title": "AI Engineer", "location": {"name": "Singapore"},
                      "content": "Python", "absolute_url": f"https://x/{i}"} for i in range(n)]}


def test_a_platform_that_keeps_rate_limiting_is_skipped_for_the_run(monkeypatch):
    boards = [{"company": f"G{i}", "ats": "greenhouse", "token": f"g{i}"} for i in range(12)]
    boards += [{"company": "L", "ats": "lever", "token": "ok"}]
    monkeypatch.setattr(runner, "load_boards", lambda: boards)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "greenhouse" in str(request.url):
            return httpx.Response(429)
        return httpx.Response(200, json=[{"id": "1", "text": "AI Engineer", "categories": {"location": "Singapore"},
                                          "descriptionPlain": "Python", "hostedUrl": "https://x"}])

    report = collect(MemoryStore(), client_for(handler), TAX, TODAY, {"boards"})
    # The report is sorted by name (sources run in parallel); boards ran in list order.
    greenhouse = sorted((r for r in report.results if r.name.startswith("greenhouse:")), key=lambda r: int(r.name[12:]))
    assert len(greenhouse) == 12 and sum("greenhouse" in c for c in calls) == runner.RATE_LIMITED_BOARDS
    assert all(r.error == "skipped: platform rate-limiting" for r in greenhouse[runner.RATE_LIMITED_BOARDS:])
    assert next(r for r in report.results if r.name == "lever:ok").inserted == 1  # other platforms carry on


def test_one_rate_limited_board_does_not_trip_the_breaker(monkeypatch):
    boards = [{"company": f"G{i}", "ats": "greenhouse", "token": f"g{i}"} for i in range(8)]
    monkeypatch.setattr(runner, "load_boards", lambda: boards)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        return httpx.Response(429) if "/g3/" in url or "/g5/" in url else httpx.Response(200, json=jobs(2, url[-20:]))

    report = collect(MemoryStore(), client_for(handler), TAX, TODAY, {"boards"})
    assert sum(1 for r in report.results if r.error) == 2


def test_out_of_time_stops_reading_and_keeps_what_was_saved(monkeypatch):
    boards = [{"company": f"G{i}", "ats": "greenhouse", "token": f"g{i}"} for i in range(3)]
    monkeypatch.setattr(runner, "load_boards", lambda: boards)
    ticks = iter(range(10_000))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=jobs(250, str(request.url)[-12:]))

    store = MemoryStore()
    # The clock moves one tick per check: the first board stops part-way, the others are skipped.
    report = collect(store, client_for(handler), TAX, TODAY, {"boards"}, budget=Budget(1.5, lambda: next(ticks)))
    first, *rest = report.results
    assert first.error.startswith("stopped: out of time") and first.inserted == 200
    assert all(r.error == "skipped: out of time this run" for r in rest)
    assert len(store.data) == 200


def test_a_backfill_cut_short_is_redone_next_run():
    store = MemoryStore()
    store.log_run("hn", 500, 500, "stopped: out of time this run (the rest is read next run)")
    assert not store.finished_before("hn")
    store.log_run("hn", 900, 400, None)
    assert store.finished_before("hn") and not store.finished_before("mcf")


def test_purge_removes_one_source_and_its_runs():
    store = MemoryStore()
    collect_rows = [("workday", "1"), ("workday", "2"), ("greenhouse", "1")]
    for source, nid in collect_rows:
        store.data[f"{source}:{nid}"] = {"source": source}
    store.log_run("workday:db.wd3/DBWebsite", 2, 2, None)
    store.log_run("greenhouse", 1, 1, None)
    assert store.purge("workday") == 2
    assert list(store.data) == ["greenhouse:1"]
    assert [r["source"] for r in store.runs] == ["greenhouse"]


def test_purge_command_refuses_without_a_matching_confirmation(monkeypatch, capsys):
    import pytest

    from skillmap import cli

    monkeypatch.setenv("DATABASE_URL", "")
    for argv in (["purge", "--source", "workday", "--confirm", "greenhouse"],
                 ["purge", "--source", "nope", "--confirm", "nope"],
                 ["purge", "--source", "workday", "--confirm", "workday"]):  # no database configured
        with pytest.raises(SystemExit):
            cli.main(argv)


def test_sites_are_collected_in_parallel_but_each_site_stays_sequential(monkeypatch):
    import threading
    import time
    boards = [{"company": f"G{i}", "ats": "greenhouse", "token": f"g{i}"} for i in range(3)]
    boards += [{"company": f"L{i}", "ats": "lever", "token": f"l{i}"} for i in range(3)]
    monkeypatch.setattr(runner, "load_boards", lambda: boards)
    active: dict[str, int] = {"greenhouse": 0, "lever": 0}
    peak = {"same_site": 0, "both": 0}
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        site = "greenhouse" if "greenhouse" in str(request.url) else "lever"
        with lock:
            active[site] += 1
            peak["same_site"] = max(peak["same_site"], active[site])
            peak["both"] = max(peak["both"], int(all(active.values())))
        time.sleep(0.05)
        with lock:
            active[site] -= 1
        return httpx.Response(200, json=jobs(1, str(request.url)[-12:]) if site == "greenhouse" else [])

    collect(MemoryStore(), client_for(handler), TAX, TODAY, {"boards"})
    assert peak == {"same_site": 1, "both": 1}
