"""Change over time (trends.py), per-posting features, and the text archive."""

from datetime import date, timedelta

import httpx
import pytest

from skillmap import aggregate, trends
from skillmap.archive import LocalArchive, SupabaseArchive, pack, project_url, unpack
from skillmap.extract import perks, terms
from skillmap.extract.skills import Taxonomy
from skillmap.models import RawPosting
from skillmap.normalise import normalise
from skillmap.runner import archive_texts, reextract
from skillmap.store import MemoryStore

TODAY = date(2026, 11, 17)
TAX = Taxonomy.load()


def row(i, days_ago, skills, **over):
    seen = TODAY - timedelta(days=days_ago)
    base = {"posting_id": f"greenhouse:{i}", "source": "greenhouse", "company": f"Co{i % 7}", "title": "Engineer",
            "country": "US", "region": "North America", "remote": False, "seniority": "senior", "role_family": "ai_ml",
            "sector": "ai", "skills": skills, "skills_nice": [], "first_seen": seen, "last_seen": seen, "dedup_key": f"k{i}",
            "salary_min": None, "salary_currency": None, "salary_period": None, "n_words": 400, "min_years": 5,
            "terms": [], "degree": None, "visa_sponsorship": None, "equity": False}
    base.update(over)
    return base


def test_movers_rank_by_share_change_between_equal_windows():
    before = [row(i, 120, ["Python", "TensorFlow"]) for i in range(40)]
    now = [row(100 + i, 10, ["Python", "PyTorch"] if i % 2 else ["Python", "TensorFlow"]) for i in range(40)]
    block = trends.movers(before + now, TODAY)
    assert block["risers"][0] == {"name": "PyTorch", "now": 0.5, "before": 0.0, "delta": 0.5, "ratio": None}
    assert block["fallers"][0]["name"] == "TensorFlow" and block["fallers"][0]["delta"] == -0.5
    assert all(m["name"] != "Python" for m in block["risers"] + block["fallers"])
    assert trends.movers(now, TODAY) is None  # no comparable earlier window


def test_mix_series_and_per_post_shape():
    rows = [row(i, 5, ["Python"] * 1 + (["Go"] if i % 2 else []), role_family="ai_ml" if i % 4 else "fde",
                skills_nice=["Go"] if i % 2 else [], degree="phd" if i < 10 else None, visa_sponsorship=i % 5 == 0,
                equity=True) for i in range(40)]
    mix = trends.mix_series(rows, "role_family", ["ai_ml", "fde"], TODAY)
    assert mix["months"] == ["2026-11"] and mix["shares"]["fde"] == [0.25]
    shape = trends.per_post_series(rows, TODAY)
    assert shape["skills_mean"] == [1.5] and shape["nice_mean"] == [0.5] and shape["required_mean"] == [1.0]
    assert shape["degree_phd"] == [0.25] and shape["visa_yes"] == [0.2] and shape["equity"] == [1.0]
    assert shape["years_median"] == [5] and shape["words_median"] == [400]


def test_companies_growth_and_cards():
    rows = [row(i, 45, ["Python"], company="Shrinkco") for i in range(20)]
    rows += [row(100 + i, 40, ["Python"], company="Growco") for i in range(6)]
    rows += [row(200 + i, 5, ["Python", "Rust"], company="Growco", role_family="backend") for i in range(30)]
    block = trends.companies(rows, TODAY)
    assert block["largest"][0]["name"] == "Growco" and block["largest"][0]["delta"] == 24
    assert block["largest"][0]["families"][0] == {"key": "backend", "n": 30}
    assert [c["name"] for c in block["growing"]] == ["Growco"]
    assert [c["name"] for c in block["shrinking"]] == ["Shrinkco"]


def test_emerging_terms_count_companies_not_postings():
    rows = [row(i, 5, ["Python"], company="Spammy", terms=["FooDB"]) for i in range(50)]
    rows += [row(100 + i, 5, ["Python"], company=f"C{i}", terms=["BarAI"]) for i in range(20)]
    block = trends.emerging_terms(rows, TODAY)
    assert [t["term"] for t in block["common"]] == ["BarAI"]
    assert block["rising"][0] == {"term": "BarAI", "companies": 20, "companies_before": 0}


def test_build_includes_changes():
    rows = [row(i, i % 60, ["Python"]) for i in range(80)]
    data = aggregate.build(rows, TODAY, TAX.categories, TAX.version)
    assert set(data["changes"]) >= {"movers", "mix", "per_post", "companies", "terms", "hn_movers"}


# --- per-posting features -------------------------------------------------------------------

def test_unknown_terms_skip_known_skills_and_ad_vocabulary():
    text = ("Agents with LangGraph and MCP, served via vLLM. DSPy, Next.js, PyTorch, A2A protocol, GPT-5. "
            "DuckDB and ClickHouse. eBPF. L5 level. EEO employer in the USA. 5+ years, 10x. Q3 2026. SOC2. COVID-19.")
    found = terms.candidates(text, TAX.known)
    assert {"A2A", "DuckDB", "ClickHouse", "eBPF"} <= set(found)
    assert not {"LangGraph", "MCP", "PyTorch", "EEO", "USA", "L5", "10x", "Q3", "SOC2"} & set(found)


@pytest.mark.parametrize(("text", "expected"), [
    ("BS in Computer Science or equivalent experience", None),
    ("PhD in Machine Learning required", "phd"),
    ("Master's degree in CS", "master"),
    ("Bachelor's degree in Engineering", "bachelor"),
    ("Strong Python", None),
])
def test_degree(text, expected):
    assert perks.degree(text) == expected


@pytest.mark.parametrize(("text", "expected"), [
    ("We are unable to sponsor visas", False),
    ("Must be authorized to work in the US", False),
    ("Singapore Citizens or PRs only", False),
    ("Visa sponsorship is available", True),
    ("We can sponsor an Employment Pass", True),
    ("Python and SQL", None),
])
def test_visa(text, expected):
    assert perks.visa_sponsorship(text) is expected


def test_normalise_records_the_shape_of_the_ad():
    raw = RawPosting("greenhouse", "1", "Acme", "ML Engineer",
                     "<p>5+ years of experience with Python. We use DuckDB.</p><p>PhD required.</p><p>Salary plus equity.</p>")
    p = normalise(raw, TAX, TODAY)
    assert p.min_years == 5 and p.n_words > 5 and "DuckDB" in p.terms and p.degree == "phd" and p.equity


# --- text archive ---------------------------------------------------------------------------

def test_pack_round_trip():
    assert unpack(pack([("a:1", "héllo"), ("a:2", "")])) == {"a:1": "héllo", "a:2": ""}


def test_project_url_from_connection_strings():
    assert project_url("postgresql://postgres.abcd1234:pw@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres") \
        == "https://abcd1234.supabase.co"
    assert project_url("postgresql://postgres:pw@db.abcd1234.supabase.co:5432/postgres") == "https://abcd1234.supabase.co"
    assert project_url("postgresql://postgres:pw@localhost/postgres") is None


def test_archive_moves_text_out_and_reextraction_reads_it_back(tmp_path):
    store, archive = MemoryStore(), LocalArchive(tmp_path)
    raws = [RawPosting("greenhouse", str(i), "Acme", "Backend Engineer", "We use Python and Rust") for i in range(5)]
    store.save([(normalise(r, TAX, TODAY), r) for r in raws], TAX.version)
    assert archive_texts(store, archive, TODAY, per_file=3) == 5
    assert all(r["description"] is None and r["text_archive"].startswith("texts/2026-11-17-") for r in store.data.values())
    assert len(list(tmp_path.glob("texts/*.gz"))) == 2

    for r in store.data.values():  # pretend the taxonomy moved on and extraction was lost
        r.update(taxonomy_version=0, skills=[])
    assert reextract(store, TAX, TODAY, archive) == 5
    assert all(r["skills"] == ["Python", "Rust / Scala"] for r in store.data.values())


def test_supabase_archive_requests():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("apikey"), request.headers.get("authorization")))
        if request.url.path.endswith("/bucket"):
            return httpx.Response(409, json={"error": "Duplicate", "message": "The resource already exists"})
        if request.method == "GET":
            return httpx.Response(200, content=b"data")
        return httpx.Response(200, json={"Key": "x"})

    archive = SupabaseArchive("https://ref.supabase.co", "sb_secret_x", transport=httpx.MockTransport(handler))
    archive.put("texts/a.jsonl.gz", b"data")
    assert archive.get("texts/a.jsonl.gz") == b"data"
    assert seen[0][:2] == ("POST", "/storage/v1/bucket")
    assert seen[1][:2] == ("POST", "/storage/v1/object/skillmap-text/texts/a.jsonl.gz")
    assert seen[1][2] == "sb_secret_x" and seen[1][3] is None  # new-style keys aren't bearer tokens
