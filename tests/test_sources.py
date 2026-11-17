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


def test_greenhouse_eu_host():
    assert greenhouse.board_url("polyai", "eu").startswith("https://boards-api.eu.greenhouse.io/")


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
