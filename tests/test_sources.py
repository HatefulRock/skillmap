import json
from datetime import date
from pathlib import Path

from skillmap.sources import ashby, greenhouse, hn, lever, mcf

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


def test_greenhouse_unescapes_content_and_uses_first_published():
    jobs = list(greenhouse.parse(load("greenhouse.json"), "OKX", "crypto"))
    assert [j.title for j in jobs] == ["Senior AI Engineer, Agents", "Accounting Manager"]
    assert "<li>5+ years of Python</li>" in jobs[0].description
    assert jobs[0].posted_on == date(2026, 10, 20)
    assert jobs[0].sector_hint == "crypto" and jobs[0].posting_id == "greenhouse:1"


def test_greenhouse_eu_boards_use_the_main_api_host():
    # Verified against a live EU board (bybit): boards-api.eu.greenhouse.io doesn't resolve.
    assert greenhouse.board_url("bybit", "eu") == "https://boards-api.greenhouse.io/v1/boards/bybit/jobs?content=true"


def test_lever_includes_requirement_lists():
    job = next(lever.parse(load("lever.json"), "X"))
    assert "3 years of Python" in job.description
    assert job.country_hint == "SG" and job.remote_hint is False
    assert job.posted_on == date(2025, 11, 1)


def test_ashby_skips_unlisted_and_reads_salary():
    jobs = list(ashby.parse(load("ashby.json"), "X"))
    assert len(jobs) == 1
    assert (jobs[0].salary_min, jobs[0].salary_currency, jobs[0].salary_period) == (200000, "USD", "year")
    assert jobs[0].country_hint == "US"


def test_hn_splits_header_and_drops_deleted():
    posts = list(hn.parse_thread(load("hn_item.json")))
    assert len(posts) == 1
    post = posts[0]
    assert (post.company, post.title, post.location) == ("Acme AI", "Senior Backend Engineer", "Singapore")


def test_hn_thread_title_filter():
    assert hn.THREAD_TITLE.match("Ask HN: Who is hiring? (November 2026)")
    assert not hn.THREAD_TITLE.match("Ask HN: Who wants to be hired? (November 2026)")


def test_mcf_maps_level_salary_and_skill_tags():
    job = mcf.parse_job(load("mcf.json")["results"][0])
    assert job.seniority_hint == "senior" and job.min_years_hint == 3
    assert (job.salary_min, job.salary_period, job.salary_currency) == (7000, "month", "SGD")
    assert "Skills: Python, Docker" in job.description
    assert job.country_hint == "SG" and job.posted_on == date(2026, 11, 10)


def test_mcf_reads_the_whole_category_then_new_keyword_hits():
    import httpx

    from skillmap.http import PoliteClient

    def job(i, day):
        return {"uuid": f"j{i}", "title": "Engineer", "metadata": {"newPostingDate": f"2026-{day}"}}

    calls = []

    def handler(request):
        body = json.loads(request.content)
        page = int(request.url.params["page"])
        calls.append(((body.get("categories") or [body.get("search")])[0], page))
        if body.get("categories"):
            # 150 open IT ads, most of them older than `since`: all are read.
            jobs = [job(i, "09-01") for i in range(page * 100, min(150, page * 100 + 100))]
        else:
            jobs = [job(0, "11-15"), job(500, "11-15"), job(501, "08-01")]
        return httpx.Response(200, json={"results": jobs})

    client = PoliteClient(min_interval=0, transport=httpx.MockTransport(handler), retries=0)
    client.sleep = lambda s: None
    got = [p.native_id for p in mcf.collect(client, ["python"], since=date(2026, 11, 1),
                                            categories=["Information Technology"])]
    assert len(got) == 151 and len(set(got)) == 151 and "j500" in got and "j501" not in got
    assert calls == [("Information Technology", 0), ("Information Technology", 1), ("python", 0)]


def test_mcf_category_failure_still_runs_the_keyword_searches():
    import httpx

    from skillmap.http import PoliteClient

    def handler(request):
        if json.loads(request.content).get("categories"):
            return httpx.Response(500)
        return httpx.Response(200, json={"results": [{"uuid": "k1", "title": "AI Engineer"}]})

    client = PoliteClient(min_interval=0, transport=httpx.MockTransport(handler), retries=0)
    client.sleep = lambda s: None
    assert [p.native_id for p in mcf.collect(client, ["llm"], categories=["Information Technology"])] == ["k1"]
