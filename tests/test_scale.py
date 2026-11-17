"""Board discovery, SmartRecruiters, salary from text, and the pay / per-role aggregates."""

from datetime import date, timedelta

import httpx
import pytest

from skillmap import aggregate, discover
from skillmap.extract import salary
from skillmap.extract.skills import Taxonomy
from skillmap.http import PoliteClient
from skillmap.models import RawPosting
from skillmap.normalise import normalise
from skillmap.runner import RunReport, SourceResult
from skillmap.sources import lever, smartrecruiters
from skillmap.store import MemoryStore

TODAY = date(2026, 11, 17)


def client_for(handler):
    client = PoliteClient(min_interval=0, transport=httpx.MockTransport(handler), retries=0)
    client.sleep = lambda s: None
    return client


# --- salary ---------------------------------------------------------------------------------

@pytest.mark.parametrize(("text", "country", "expected"), [
    ("The base salary range for this role is $180,000 - $240,000 USD.", "US", (180000, 240000, "USD", "year")),
    ("Salary: S$8,000 – S$12,000 per month", "SG", (8000, 12000, "SGD", "month")),
    ("SGD 6,000 - 9,000 monthly", None, (6000, 9000, "SGD", "month")),
    ("£80k–£100k + equity", "GB", (80000, 100000, "GBP", "year")),
    ("Compensation: 120k-160k EUR", None, (120000, 160000, "EUR", "year")),
    ("The annual range is 150.000 - 190.000 €", "DE", (150000, 190000, "EUR", "year")),
    ("Pay: $60 - $80 per hour", "US", (60, 80, "USD", "hour")),
    ("Senior Engineer | Remote | $150k-$200k + equity", None, (150000, 200000, "USD", "year")),
    ("Salary $7,000 - $10,000", "SG", (7000, 10000, "SGD", "month")),  # "$" in Singapore is SGD
])
def test_salary_ranges_are_read(text, country, expected):
    assert salary.from_text(text, country) == expected


@pytest.mark.parametrize("text", [
    "We raised $50M - $60M in our Series B",
    "Revenue grew from $2,000,000 to $3,000,000 last year",
    "5-10 years of experience with Python",
    "Founded 2015 - 2020 with 200 employees",
    "Competitive salary and equity",
])
def test_things_that_are_not_pay_are_ignored(text):
    assert salary.from_text(text, "US") is None


def test_annual_and_usd_conversion():
    assert salary.annual(10_000, "month") == 120_000
    assert salary.to_usd(100_000, "SGD") == pytest.approx(76_000)
    assert salary.to_usd(1, "XXX") is None


def test_normalise_reads_pay_from_text_but_prefers_the_source_field():
    tax = Taxonomy.load()
    text_only = RawPosting("greenhouse", "1", "Acme", "Backend Engineer", "<p>Pay range: $150,000 - $190,000</p>",
                           location="New York, NY")
    assert normalise(text_only, tax, TODAY).salary_currency == "USD"
    field = RawPosting("mcf", "2", "Acme", "Backend Engineer", "Pay range: $150,000 - $190,000", location="Singapore",
                       salary_min=6000, salary_max=9000, salary_currency="SGD", salary_period="month")
    posting = normalise(field, tax, TODAY)
    assert (posting.salary_min, posting.salary_currency, posting.salary_period) == (6000, "SGD", "month")


def test_lever_salary_range():
    job = {"id": "a", "text": "ML Engineer", "categories": {"location": "London"},
           "salaryRange": {"min": 90000, "max": 120000, "currency": "GBP", "interval": "per-year-salary"}}
    raw = next(lever.parse([job], "X"))
    assert (raw.salary_min, raw.salary_max, raw.salary_currency, raw.salary_period) == (90000, 120000, "GBP", "year")


def test_non_tech_text_is_not_stored():
    tax = Taxonomy.load()
    store = MemoryStore()
    items = [RawPosting("greenhouse", str(i), "Acme", title, "long text") for i, title in
             enumerate(["Account Executive", "Backend Engineer"])]
    store.save([(normalise(raw, tax, TODAY), raw) for raw in items], tax.version)
    assert store.data["greenhouse:0"]["description"] is None
    assert store.data["greenhouse:1"]["description"] == "long text"


# --- SmartRecruiters --------------------------------------------------------------------------

def sr_handler(details_seen):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/postings"):
            offset = int(request.url.params.get("offset", 0))
            titles = ["Machine Learning Engineer", "Sales Manager", "Senior Data Engineer"]
            content = [{"id": str(offset + i), "name": titles[(offset + i) % 3], "releasedDate": "2026-11-01T00:00:00Z",
                        "company": {"identifier": "Acme", "name": "Acme Corp"},
                        "location": {"city": "Singapore", "country": "sg", "remote": False},
                        "experienceLevel": {"id": "entry_level"}} for i in range(100 if offset == 0 else 20)]
            return httpx.Response(200, json={"totalFound": 120, "content": content})
        details_seen.append(path.rsplit("/", 1)[1])
        return httpx.Response(200, json={"jobAd": {"sections": {"qualifications": {"title": "Q", "text": "<p>PyTorch</p>"}}}})
    return handler


def test_smartrecruiters_pages_and_fetches_text_only_for_new_tech_postings():
    seen: list[str] = []
    client = client_for(sr_handler(seen))
    jobs = list(smartrecruiters.collect(client, "Acme", "Acme", known={"0"}))
    assert len(jobs) == 120
    sales = [j for j in jobs if j.title == "Sales Manager"]
    assert sales and all(j.description == "" for j in sales)
    assert "0" not in seen and "1" not in seen  # known, and non-tech
    first = next(j for j in jobs if j.native_id == "2")
    assert "PyTorch" in first.description and first.country_hint == "SG" and first.seniority_hint == "junior"


def test_smartrecruiters_leaves_postings_past_the_text_budget_for_next_run():
    seen: list[str] = []
    jobs = list(smartrecruiters.collect(client_for(sr_handler(seen)), "Acme", "Acme", max_new_texts=5))
    assert len(seen) == 5
    assert all(j.description for j in jobs if j.title != "Sales Manager")


# --- discovery -----------------------------------------------------------------------------

def test_slug_variants():
    assert discover.slug_variants("Scale AI") == ["scaleai", "scale-ai", "scale"]
    assert discover.slug_variants("Hudson River Trading", "https://www.hudsonrivertrading.com")[:2] == [
        "hudsonrivertrading", "hudson-river-trading"]
    assert discover.slug_variants("Crypto.com", "https://crypto.com", ["cryptocom"])[0] == "cryptocom"
    assert "x" not in discover.slug_variants("X")  # too short to trust


def test_same_company_guards_against_token_collisions():
    assert discover.same_company("Scale AI, Inc.", "Scale AI")
    assert discover.same_company(None, "Anything")
    assert not discover.same_company("Ramp Hospitality Group", "Mercury")


def test_yc_candidates_filter_and_sector():
    companies = [
        {"name": "PayCo", "slug": "payco", "status": "Active", "isHiring": True, "tags": ["Fintech"], "industries": []},
        {"name": "ChainCo", "slug": "chainco", "status": "Active", "team_size": 40, "tags": ["Crypto / Web3"]},
        {"name": "Dead", "slug": "dead", "status": "Inactive", "isHiring": True},
        {"name": "Tiny", "slug": "tiny", "status": "Active", "isHiring": False, "team_size": 3},
    ]
    found = discover.yc_candidates(companies)
    assert [(c.name, c.sector) for c in found] == [("PayCo", "fintech"), ("ChainCo", "crypto")]


def test_discover_finds_boards_on_each_platform_and_keeps_the_busiest():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://boards-api.greenhouse.io/v1/boards/acme/jobs"):
            return httpx.Response(200, json={"jobs": [{"id": 1}, {"id": 2}]})
        if url.startswith("https://boards-api.greenhouse.io/v1/boards/acme"):
            return httpx.Response(200, json={"name": "Acme"})
        if url.startswith("https://boards-api.greenhouse.io/v1/boards/other"):  # wrong company behind the token
            return httpx.Response(200, json={"name": "Someone Else Ltd", "jobs": [{"id": 1}]})
        if "api.lever.co/v0/postings/acme" in url:  # stale board with fewer jobs
            return httpx.Response(200, json=[{"id": "x"}])
        if "api.ashbyhq.com/posting-api/job-board/bravo" in url:
            return httpx.Response(200, json={"jobs": [{"id": "1"}, {"id": "2", "isListed": False}]})
        if "smartrecruiters.com" in url:
            return httpx.Response(200, json={"totalFound": 0, "content": []})
        return httpx.Response(404)

    candidates = [discover.Candidate("Acme", "ai", "curated"), discover.Candidate("Bravo", "tech", "yc"),
                  discover.Candidate("Other", "tech", "yc")]
    boards = discover.discover(candidates, discover.ATS, lambda: client_for(handler))
    assert [(b.company, b.ats, b.token, b.jobs) for b in boards] == [
        ("Acme", "greenhouse", "acme", 2), ("Bravo", "ashby", "bravo", 1)]


def test_write_boards_round_trips(tmp_path):
    import yaml
    boards = [discover.Board("Acme", "greenhouse", "acme", "ai", 3, "curated")]
    path = tmp_path / "boards.yaml"
    discover.write_boards(boards, path, TODAY, 10)
    data = yaml.safe_load(path.read_text())
    assert data["boards"][0]["token"] == "acme" and path.read_text().startswith("# Generated")


def test_run_report_rolls_boards_up_per_platform():
    report = RunReport([SourceResult("greenhouse:a", 10, 5), SourceResult("greenhouse:b", error="HTTPStatusError: 404"),
                        SourceResult("lever:c", error="boom"), SourceResult("mcf", 7, 7)])
    rolled = {r["name"]: r for r in report.as_dicts()}
    assert rolled["greenhouse"]["fetched"] == 10 and rolled["greenhouse"]["error"] is None
    assert rolled["lever"]["error"] == "all 1 boards failed"
    assert report.failed_boards() == ["greenhouse:b", "lever:c"]
    assert "2 boards failed" in report.summary()


# --- aggregates ----------------------------------------------------------------------------

def row(i, **over):
    base = {"posting_id": f"greenhouse:{i}", "source": "greenhouse", "company": f"Co{i}", "title": "Engineer",
            "url": "", "location": "", "country": "US", "city": None, "region": "North America", "remote": False,
            "seniority": "senior", "role_family": "ai_ml", "sector": "ai", "skills": ["Python", "PyTorch"],
            "skills_nice": [], "posted_on": None, "first_seen": TODAY - timedelta(days=10), "last_seen": TODAY,
            "dedup_key": f"k{i}", "salary_min": 150000 + i * 1000, "salary_max": 200000, "salary_currency": "USD",
            "salary_period": "year"}
    base.update(over)
    return base


def test_pay_block_by_country_and_role_seniority():
    rows = [row(i) for i in range(25)] + [
        row(100 + i, country="SG", salary_min=8000, salary_max=12000, salary_currency="SGD", salary_period="month")
        for i in range(22)] + [row(200 + i, country="GB", salary_min=None) for i in range(40)]
    pay = aggregate.pay_block(rows)
    countries = {c["key"]: c for c in pay["countries"]}
    assert set(countries) == {"US", "SG"}  # GB states no pay
    assert countries["SG"]["currency"] == "SGD" and countries["SG"]["local"]["median"] == 120000
    assert countries["SG"]["usd"]["median"] == pytest.approx(91200, abs=100)
    assert pay["role_seniority_usd"]["US"]["ai_ml"]["senior"]["n"] == 25
    assert pay["n"] == 47


def test_group_trends_per_role_use_quarters_and_skip_thin_groups():
    rows = [row(i, first_seen=date(2026, 7, 1)) for i in range(40)] + [row(99, role_family="quant")]
    trends = aggregate.group_trends(rows, [], "role_family", ["ai_ml", "quant"], TODAY)
    assert set(trends) == {"ai_ml"}
    series = trends["ai_ml"]["market"]
    assert series["months"] == ["2026-07", "2026-10"]
    assert series["shares"]["Python"] == [1.0, 1.0]
