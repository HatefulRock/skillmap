"""Derived insights, the weekly note, the CSV export and the Omnideck watchlist."""

import csv
import json
import re
from datetime import UTC, date, datetime, timedelta

import pytest

from skillmap import aggregate, digest, exports, insights, watchlist

TODAY = date(2026, 11, 17)


def row(i, skills=("Python",), **over):
    seen = TODAY - timedelta(days=over.pop("days_ago", 5))
    base = {"posting_id": f"greenhouse:{i}", "source": "greenhouse", "company": f"Co{i % 7}", "title": "Engineer",
            "url": "", "location": "", "country": "US", "city": None, "region": "North America", "remote": False,
            "seniority": "senior", "role_family": "ai_ml", "sector": "ai", "skills": list(skills), "skills_nice": [],
            "posted_on": None, "first_seen": seen, "last_seen": TODAY, "dedup_key": f"k{i}",
            "salary_min": None, "salary_max": None, "salary_currency": None, "salary_period": None,
            "n_words": 400, "min_years": 5, "terms": [], "degree": None, "visa_sponsorship": None, "equity": False}
    base.update(over)
    return base


def paid(i, skills, amount, **over):
    return row(i, skills, salary_min=amount, salary_max=amount, salary_currency="USD", salary_period="year", **over)


# --- insights ------------------------------------------------------------------------------

def test_pay_premium_compares_within_country_role_and_level():
    rows = []
    for level, base in (("senior", 200_000), ("mid", 120_000)):
        rows += [paid(len(rows) + i, ["Python", "Rust"], base * 1.2, seniority=level) for i in range(12)]
        rows += [paid(len(rows) + i, ["Python"], base, seniority=level) for i in range(12)]
    # Rust is also common among cheap juniors elsewhere, but no junior stratum has a comparison side.
    rows += [paid(1000 + i, ["Rust"], 60_000, seniority="junior") for i in range(10)]
    block = insights.pay_premium(rows, ["Rust", "Python"])
    rust = next(e for e in block["skills"] if e["name"] == "Rust")
    assert rust["premium"] == pytest.approx(0.2, abs=0.001) and rust["n"] == 24
    assert all(e["name"] != "Python" for e in block["skills"])  # never absent in a stratum
    assert block["n_with_pay"] == len(rows)


def test_pay_premium_needs_enough_matched_postings():
    rows = [paid(i, ["Python", "Rust"], 150_000) for i in range(5)] + [paid(50 + i, ["Python"], 100_000) for i in range(5)]
    assert insights.pay_premium(rows, ["Rust"])["skills"] == []


def test_role_dna_ranks_by_lift_against_other_roles():
    rows = [row(i, ["Python", "PyTorch"], role_family="ai_ml") for i in range(40)]
    rows += [row(100 + i, ["Python", "Go"], role_family="backend") for i in range(40)]
    dna = insights.role_dna(rows)
    assert dna["ai_ml"]["skills"][0] == {"name": "PyTorch", "share": 1.0, "elsewhere": 0.0, "lift": None}
    python = next(s for s in dna["backend"]["skills"] if s["name"] == "Python")
    assert python["lift"] == 1.0


def test_seniority_ladder_and_profile():
    rows = [row(i, ["Python"], seniority="junior", min_years=1) for i in range(30)]
    rows += [row(100 + i, ["Python", "Kubernetes"], seniority="senior", min_years=6) for i in range(30)]
    ladder = insights.seniority_ladder(rows)
    assert ladder["levels"] == ["junior", "senior"]
    assert ladder["skills"][0] == {"name": "Kubernetes", "shares": [0.0, 1.0], "change": 1.0}
    assert ladder["profile"]["senior"] == {"n": 30, "years_median": 6, "skills_mean": 2.0}
    assert insights.seniority_ladder(rows[:30])["skills"] == []


def test_stacks_keep_pairs_that_go_together_more_than_chance():
    rows = [row(i, ["PyTorch", "CUDA"]) for i in range(20)] + [row(100 + i, ["Go", "Kubernetes"]) for i in range(20)]
    rows += [row(200 + i, ["PyTorch", "Go"]) for i in range(2)]
    net = insights.stacks(rows)
    pairs = {(e["a"], e["b"]) for e in net["edges"]}
    assert pairs == {("CUDA", "PyTorch"), ("Go", "Kubernetes")}
    assert {n["name"] for n in net["nodes"]} == {"PyTorch", "CUDA", "Go", "Kubernetes"}


def test_requirement_creep_by_month_and_level():
    rows = [row(i, ["Python"], first_seen=date(2026, 10, 3), last_seen=date(2026, 10, 20), min_years=4) for i in range(30)]
    rows += [row(100 + i, ["Python", "Go"], first_seen=date(2026, 11, 2), min_years=6, degree="bachelor") for i in range(30)]
    creep = insights.requirement_creep(rows, TODAY)
    assert creep["months"] == ["2026-10", "2026-11"]
    senior = creep["levels"]["senior"]
    assert senior["years_median"] == [4, 6] and senior["skills_mean"] == [1.0, 2.0] and senior["degree"] == [0.0, 1.0]


def test_learn_next_weighs_demand_gap_and_premium():
    rows = [row(i, ["Python", "Go", "Rust"] if i % 2 else ["Python", "Go"], role_family="backend") for i in range(40)]
    premium = {"skills": [{"name": "Rust", "premium": 1.0, "n": 30}]}
    out = insights.learn_next(rows, {"Python": 5, "Go": 2}, premium, {"Go": "Languages"}, ["backend"])
    assert [e["name"] for e in out] == ["Rust", "Go"]  # 0.5 × 1 × 2 beats 1.0 × 0.75; Python is already strong
    assert out[1] == {"name": "Go", "category": "Languages", "demand": 1.0, "rating": 2, "premium": None, "score": 0.75}
    assert insights.learn_next(rows, {}, premium, {}, ["ai_ml"]) == []


# --- the weekly note -----------------------------------------------------------------------

def market(as_of, n, shares, pay=None, terms=()):
    return {"meta": {"as_of": as_of, "windows": {"current_days": 90}},
            "current": {"n": n, "skills": [{"name": k, "share": v} for k, v in shares.items()]},
            "changes": {"movers": {"days": 90, "risers": [{"name": "Rust", "delta": 0.03}], "fallers": []},
                        "terms": {"common": [{"term": t, "companies": 4} for t in terms]},
                        "companies": {"growing": [{"name": "Growco", "delta": 12}]}},
            "pay": {"countries": [{"key": "SG", "currency": "SGD", "local": {"median": m}, "n": 40} for m in [pay] if m]}}


def test_note_compares_with_last_week():
    before = market("2026-11-10", 1000, {"Python": 0.60, "Go": 0.20}, pay=120_000, terms=["DSPy"])
    now = market("2026-11-17", 1100, {"Python": 0.61, "Go": 0.23}, pay=126_000, terms=["DSPy", "MCP"])
    lines = digest.build_note(now, before)["lines"]
    assert lines[0] == "1,100 live tech postings in the last 90 days (+100 since 2026-11-10)."
    assert lines[1] == "Biggest moves since last week: Go 23% (+3.0 pp), Python 61% (+1.0 pp)."
    assert "New on the radar: MCP (4 companies)." in lines and "Hiring more: Growco (+12)." in lines
    assert lines[-1] == "Median advertised pay in SG: SGD 126,000 a year (+5%), from 40 ads."


def test_first_note_has_no_comparisons():
    note = digest.build_note(market("2026-11-17", 50, {"Python": 0.5}, terms=["MCP"]), None)
    assert note["lines"][0] == "50 live tech postings in the last 90 days."
    assert not any(line.startswith(("Biggest", "New on")) for line in note["lines"])


def test_notes_feed_and_readme(tmp_path):
    notes = digest.add_note([{"date": "2026-11-10", "lines": ["old"]}], {"date": "2026-11-17", "lines": ["a & b"]})
    notes = digest.add_note(notes, {"date": "2026-11-17", "lines": ["replaced"]})
    assert [n["date"] for n in notes] == ["2026-11-17", "2026-11-10"] and notes[0]["lines"] == ["replaced"]
    feed = digest.rss([{"date": "2026-11-17", "lines": ["a & b"]}])
    assert "<pubDate>Tue, 17 Nov 2026 00:00:00 +0000</pubDate>" in feed and "a &amp;amp; b" in feed
    readme = tmp_path / "README.md"
    readme.write_text(f"# x\n{digest.README_START}\nold\n{digest.README_END}\nrest\n")
    assert digest.update_readme(readme, notes[0])
    assert readme.read_text() == f"# x\n{digest.README_START}\n**Week of 2026-11-17**\n\n- replaced\n{digest.README_END}\nrest\n"
    assert not digest.update_readme(tmp_path / "missing.md", notes[0])
    assert digest.load_notes(tmp_path / "none.json") == []


# --- exports -------------------------------------------------------------------------------

def test_csv_export_covers_every_table(tmp_path):
    rows = [paid(i, ["Python", "PyTorch"] if i % 2 else ["Python"], 150_000 + i * 1000,
                 role_family="ai_ml" if i % 3 else "backend", seniority="senior" if i % 4 else "mid")
            for i in range(120)]
    data = aggregate.build(rows, TODAY, {"Python": "Languages", "PyTorch": "ML"}, 3, ratings={"Python": 4})
    json.dumps(data, default=str)
    written = exports.write_csvs(data, tmp_path)
    assert len(written) == 13 and all((tmp_path / name).exists() for name in written)
    with (tmp_path / "skills_current.csv").open() as fh:
        current = list(csv.DictReader(fh))
    assert {r["skill"] for r in current} == {"Python", "PyTorch"}
    assert next(r for r in current if r["skill"] == "Python")["share"] == "1.0"


# --- watchlist -----------------------------------------------------------------------------

BOARDS = [{"company": "Alpha AI", "ats": "greenhouse", "token": "alphaai", "sector": "ai"},
          {"company": "Beta", "ats": "ashby", "token": "beta", "sector": "fintech"},
          {"company": "Gamma", "ats": "workable", "token": "gamma"},
          {"company": "Delta", "ats": "lever", "token": "Delta.Labs"}]
TARGETS = {"role_families": ["ai_ml"], "countries": ["SG"], "regions": [], "remote": True}


def test_watchlist_ranks_boards_by_matching_postings():
    rows = [row(i, ["Python", "PyTorch"], company="Alpha AI", country="SG") for i in range(6)]
    rows += [row(10 + i, ["Go"], company="Beta", remote=True, source="ashby") for i in range(8)]
    rows += [row(20 + i, ["Python"], company="Beta", country="US", source="ashby") for i in range(5)]  # wrong place
    rows += [row(30 + i, ["Python"], company="Gamma", country="SG", source="workable") for i in range(9)]  # not watchable
    rows += [row(40 + i, ["Python"], company="Delta", country="SG", source="lever") for i in range(3)]
    rows += [row(50, ["Python"], company="Alpha AI", country="SG", last_seen=TODAY - timedelta(days=60))]  # gone
    out = watchlist.build(rows, BOARDS, TARGETS, {"Python": 4, "PyTorch": 3}, TODAY,
                          now=datetime(2026, 11, 17, 6, 0, 1, 500, tzinfo=UTC))
    assert out["version"] == 1 and out["generated_at"] == "2026-11-17T06:00:01Z"
    assert [b["company"] for b in out["boards"]] == ["Alpha AI", "Delta", "Beta"]  # 6×1.5, 3×1.5, 8×0.5
    alpha, delta, beta = out["boards"]
    assert (alpha["company"], alpha["matching"], alpha["skill_fit"], alpha["countries"]) == ("Alpha AI", 6, 1.0, ["SG"])
    assert (beta["matching"], beta["live_tech"], beta["skill_fit"], beta["countries"]) == (8, 13, 0.0, ["US"])
    assert delta["token"] == "Delta.Labs"
    # Tokens Omnideck would reject as slugs stay off the paste-ready lists.
    assert out["omnideck"] == {"watch_ashby": ["beta"], "watch_greenhouse": ["alphaai"], "watch_lever": []}
    assert all(re.fullmatch(r"[a-z0-9-]{1,64}", t) for ts in out["omnideck"].values() for t in ts)
