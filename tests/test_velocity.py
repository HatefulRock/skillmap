"""New postings per week and how long ads stay up."""

from datetime import date, timedelta

from skillmap import velocity

TODAY = date(2026, 11, 18)  # a Wednesday


def posting(posted, last_seen, family="backend", skills=("Python",)):
    return {"posted_on": posted, "first_seen": posted or TODAY, "last_seen": last_seen, "role_family": family,
            "skills": list(skills)}


def test_weekly_new_counts_by_posted_week_and_skips_undated():
    rows = [posting(TODAY, TODAY), posting(TODAY - timedelta(days=2), TODAY),  # this week (Mon 16th)
            posting(TODAY - timedelta(days=9), TODAY, "ai_ml"), posting(None, TODAY)]
    out = velocity.weekly_new(rows, TODAY, ["backend", "ai_ml"], weeks=3)
    assert out["weeks"] == ["2026-11-02", "2026-11-09", "2026-11-16"]
    assert out["n"] == [0, 1, 2]
    assert out["by_role"] == {"backend": [0, 0, 2], "ai_ml": [0, 1, 0]}


def test_time_open_for_closed_and_live_ads():
    closed = [posting(TODAY - timedelta(days=40), TODAY - timedelta(days=10)) for _ in range(30)]  # open 30 days
    quick = [posting(TODAY - timedelta(days=20), TODAY - timedelta(days=15), "ai_ml", ["RAG"]) for _ in range(30)]
    live = [posting(TODAY - timedelta(days=12), TODAY) for _ in range(30)]
    out = velocity.time_open(closed + quick + live, TODAY, ["backend", "ai_ml"], ["Python", "RAG"])
    assert out["closed"]["all"]["n"] == 60
    assert {g["key"]: g["median"] for g in out["closed"]["by_role"]} == {"backend": 30, "ai_ml": 5}
    assert [s["name"] for s in out["closed"]["by_skill"]] == ["RAG", "Python"]  # fastest first
    assert out["open_age"]["all"]["median"] == 12 and out["open_age"]["by_role"][0]["key"] == "backend"


def test_small_groups_are_left_out():
    rows = [posting(TODAY - timedelta(days=40), TODAY - timedelta(days=10)) for _ in range(5)]
    out = velocity.time_open(rows, TODAY, ["backend"], ["Python"])
    assert out["closed"]["by_role"] == [] and out["closed"]["by_skill"] == []
