"""End to end against a fake network: collectors → store → re-extraction → aggregates → SVG."""

import json
import xml.etree.ElementTree as ET
from datetime import date, timedelta

import httpx

from skillmap import aggregate, heatmap
from skillmap.cli import write_outputs
from skillmap.extract.skills import Taxonomy
from skillmap.http import PoliteClient
from skillmap.runner import collect, reextract
from skillmap.store import MemoryStore

TODAY = date(2026, 11, 17)
TAX = Taxonomy.load()


def gh_jobs(n):
    titles = ["Senior AI Engineer", "Forward Deployed Engineer", "Python Developer", "Data Engineer"]
    bodies = ["Python, LangGraph, RAG and Docker", "Python, SQL and customer-facing PoCs", "Python, FastAPI, PostgreSQL",
              "Python, Spark SQL, Airflow"]
    return {"jobs": [
        {"id": i, "title": titles[i % 4], "location": {"name": "Singapore"}, "content": bodies[i % 4],
         "first_published": (TODAY - timedelta(days=i % 20)).isoformat(), "absolute_url": f"https://x/{i}"}
        for i in range(n)
    ]}


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "boards-api.greenhouse.io/v1/boards/good/" in url:
        return httpx.Response(200, json=gh_jobs(120))
    if "boards-api.greenhouse.io/v1/boards/dead/" in url:
        return httpx.Response(404, json={"status": 404})
    if "search_by_date" in url:
        return httpx.Response(200, json={"nbPages": 1, "hits": [
            {"objectID": "500", "title": "Ask HN: Who is hiring? (October 2026)", "created_at": "2026-10-01T15:00:00Z"},
            {"objectID": "501", "title": "Ask HN: Who wants to be hired? (October 2026)", "created_at": "2026-10-01T15:00:00Z"},
        ]})
    if "/items/500" in url:
        return httpx.Response(200, json={"children": [
            {"id": 9000 + i, "created_at": "2026-10-02T10:00:00Z", "text": f"Co{i} | Backend Engineer | Remote<p>Python and Go"}
            for i in range(40)
        ]})
    if "mycareersfuture" in url:
        return httpx.Response(200, json={"results": [
            {"uuid": f"m{i}", "title": "AI Engineer", "description": "LLM apps with Python",
             "postedCompany": {"name": f"SG Co {i}"}, "salary": {"minimum": 6000 + 100 * i, "maximum": 9000,
             "type": {"salaryType": "Monthly"}}, "metadata": {"newPostingDate": (TODAY - timedelta(days=2)).isoformat()}}
            for i in range(35)
        ]})
    return httpx.Response(404)


def make_client():
    client = PoliteClient(min_interval=0, transport=httpx.MockTransport(handler), retries=0)
    client.sleep = lambda s: None
    return client


def test_full_run(tmp_path, monkeypatch):
    from skillmap import runner

    real = runner.load_yaml

    def fake_yaml(name):
        if name == "companies.yaml":
            return {"companies": [
                {"company": "Good", "ats": "greenhouse", "token": "good", "sector": "ai"},
                {"company": "Dead", "ats": "greenhouse", "token": "dead", "sector": "ai"},
            ]}
        return real(name)

    monkeypatch.setattr(runner, "load_yaml", fake_yaml)
    store = MemoryStore()
    report = collect(store, make_client(), TAX, TODAY, {"boards", "hn", "mcf", "adzuna"}, hn_since=date(2026, 10, 1))
    by_name = {r.name: r for r in report.results}
    assert by_name["greenhouse:good"].inserted == 120
    assert by_name["greenhouse:dead"].error.startswith("HTTPStatusError")  # reported, run continues
    assert by_name["hn"].inserted == 40
    assert by_name["mcf"].inserted == 35
    assert "skipped" in by_name["adzuna"].error

    # Second run sees the same jobs: nothing new, last_seen moves on.
    report2 = collect(store, make_client(), TAX, TODAY + timedelta(days=1), {"boards"})
    assert report2.results[0].inserted == 0
    assert store.data["greenhouse:0"]["last_seen"] == TODAY + timedelta(days=1)

    # A taxonomy bump re-extracts everything once.
    TAX_V2 = Taxonomy.load()
    TAX_V2.version = TAX.version + 1
    assert reextract(store, TAX_V2, TODAY) == len(store.data)
    assert reextract(store, TAX_V2, TODAY) == 0

    data, _ = write_outputs(store, TAX, TODAY, tmp_path)
    assert data["current"]["n"] == 155
    top = {s["name"]: s for s in data["current"]["skills"]}
    assert top["Python"]["share"] == 1.0
    assert data["trends"]["hn"]["shares"]["Go"][-2] == 1.0  # October HN thread
    families = {s["key"]: s for s in data["segments"]["role_family"]["segments"]}
    assert families["ai_ml"]["n"] == 65 and families["ai_ml"]["shares"]["Python"] == 1.0
    assert families["fde"]["n"] == 30 and families["fde"]["shares"]["SQL"] == 1.0
    assert data["salary"]["n"] == 35 and data["salary"]["median_monthly"] > 6000
    assert json.loads((tmp_path / "skills-market.json").read_text())["meta"]["as_of"] == "2026-11-17"
    ET.fromstring((tmp_path / "skills-heatmap.svg").read_text())  # well-formed
    assert (tmp_path / "postings-live.jsonl.gz").exists()
    # A run whose numbers jump far from the published file keeps the old files when guarded.
    published = json.loads((tmp_path / "skills-market.json").read_text())
    published["current"]["n"] *= 3
    (tmp_path / "skills-market.json").write_text(json.dumps(published))
    _, held = write_outputs(store, TAX, TODAY, tmp_path, guard=True)
    assert held and "fell from" in held[0]
    assert json.loads((tmp_path / "skills-market.json").read_text())["current"]["n"] == published["current"]["n"]
    _, held = write_outputs(store, TAX, TODAY, tmp_path)  # forced
    assert not held and json.loads((tmp_path / "skills-market.json").read_text())["current"]["n"] == 155


def test_dedupe_prefers_company_board():
    rows = [
        {"posting_id": "mcf:1", "source": "mcf", "dedup_key": "k", "first_seen": date(2026, 11, 1),
         "last_seen": date(2026, 11, 10), "skills": ["Python"]},
        {"posting_id": "greenhouse:1", "source": "greenhouse", "dedup_key": "k", "first_seen": date(2026, 11, 3),
         "last_seen": date(2026, 11, 12), "skills": ["SQL"]},
    ]
    [merged] = aggregate.dedupe(rows)
    assert merged["posting_id"] == "greenhouse:1"
    assert (merged["first_seen"], merged["last_seen"]) == (date(2026, 11, 1), date(2026, 11, 12))
    assert merged["skills"] == ["Python", "SQL"]


def test_heatmap_escapes_names():
    svg = heatmap.render({"families": [{"key": "ai_ml", "label": "AI", "n": 1}],
                          "rows": [{"name": "C++ & <stuff>", "demand": {"ai_ml": 0.5}, "rating": 3}]}, "2026-11-17")
    ET.fromstring(svg)


def test_small_segments_withhold_shares():
    rows = [{"seniority": "senior", "skills": ["Python"]}] * 29 + [{"seniority": "junior", "skills": ["Go"]}] * 30
    block = aggregate.segment_block(rows, "seniority", ["Python", "Go"], aggregate.SENIORITY_ORDER)
    by_key = {s["key"]: s for s in block["segments"]}
    assert by_key["senior"]["n"] == 29 and by_key["senior"]["shares"] is None
    assert by_key["junior"]["shares"] == {"Python": 0.0, "Go": 1.0}
    assert [s["key"] for s in block["segments"]] == ["junior", "senior"]

