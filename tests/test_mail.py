import json

import httpx
import pytest

from skillmap import mail

TARGETS = {"role_families": ["ai_ml", "backend"], "countries": ["SG"], "regions": ["Southeast Asia"], "remote": True}
RATINGS = {"Python": 5, "RAG": 5, "LangGraph": 4, "SQL": 3, "Docker": 2}


def post(pid, skills, *, country="SG", region="Southeast Asia", remote=False, family="ai_ml", title="AI Engineer",
         company="Acme", first_seen="2026-11-18"):
    return {"posting_id": pid, "title": title, "company": company, "url": f"https://x/{pid}", "country": country,
            "region": region, "remote": remote, "role_family": family, "skills": skills, "first_seen": first_seen}


def market(as_of, edge=None, skills=None):
    return {"meta": {"as_of": as_of, "windows": {"current_days": 30}},
            "current": {"n": 100, "skills": skills or []},
            "insights": {"edge": {"families": {r: {"skills": s} for r, s in (edge or {}).items()}}}}


def test_openings_are_new_fitting_and_split_by_place():
    live = [
        post("a", ["Python", "RAG", "LangGraph"]),                                   # fits, in SG
        post("b", ["Python", "RAG", "LangGraph"], title="Old one"),                   # seen last week
        post("c", ["Python", "RAG", "Docker"]),                                      # only 2 skills I rate 3+
        post("d", ["Python", "RAG", "SQL"], country="DE", region="Europe", remote=True, title="Remote AI"),
        post("e", ["Python", "RAG", "SQL"], country="PL", region="Europe", remote=True, title="Remote AI"),  # same ad again
        post("f", ["Python", "RAG", "SQL"], country="US", region="North America"),   # not a target place
        post("g", ["Python", "RAG", "SQL"], family="frontend"),                      # not a target role
    ]
    got = mail.openings(live, {"b"}, TARGETS, RATINGS, "2026-11-12")
    assert [r["posting_id"] for r in got["near"]] == ["a"]
    assert [r["posting_id"] for r in got["remote"]] == ["d"]
    assert got["near"][0]["mine"] == ["Python", "RAG", "LangGraph"]


def test_without_last_weeks_snapshot_new_means_first_seen_this_week():
    live = [post("a", ["Python", "RAG", "SQL"], first_seen="2026-11-01"), post("b", ["Python", "RAG", "SQL"])]
    got = mail.openings(live, set(), TARGETS, RATINGS, "2026-11-12")
    assert [r["posting_id"] for r in got["near"]] == ["b"]


def test_new_edge_skills_and_first_week():
    now = market("2026-11-19", {"ai_ml": [{"name": "vLLM"}, {"name": "Python", "mine": 5}, {"name": "Ray"}]})
    before = market("2026-11-12", {"ai_ml": [{"name": "Ray"}]})
    assert mail.new_edge(now, before)["ai_ml"] == ["vLLM"]
    assert mail.new_edge(now, None)["ai_ml"] == ["vLLM", "Ray"]


def test_role_risers():
    now = market("2026-11-19", skills=[{"name": "vLLM", "share": 0.02, "roles": {"ai_ml": 0.06}},
                                       {"name": "Ray", "share": 0.02, "roles": {"ai_ml": 0.035}}])
    before = market("2026-11-12", skills=[{"name": "vLLM", "share": 0.02, "roles": {"ai_ml": 0.04}},
                                          {"name": "Ray", "share": 0.02, "roles": {"ai_ml": 0.03}}])
    assert [(n, round(d, 3)) for n, _, d in mail.role_risers(now, before)] == [("vLLM", 0.02)]


def digest_doc():
    return {"date": "2026-11-19", "since": "2026-11-12", "note": ["a <b> line"], "risers": [], "edge": {"ai_ml": []},
            "first_week": False, "openings": {"near": [{**post("a", ["Python"]), "mine": ["Python"], "missing": []}],
                                              "remote": []}}


def test_html_escapes_and_subject_counts():
    d = digest_doc()
    assert "a &lt;b&gt; line" in mail.html(d)
    assert mail.subject(d).endswith("1 new opening that fit")


def test_send_retries_rate_limits_once_per_week():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "1"})
        return httpx.Response(200, json={"id": "em_1"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert mail.send(digest_doc(), "key", "me@example.com", client=client, sleep=lambda s: None) == "em_1"
    assert len(calls) == 2
    assert calls[1].headers["Idempotency-Key"] == "skillmap-digest-2026-11-19"
    assert json.loads(calls[1].content)["to"] == ["me@example.com"]


def test_send_does_not_retry_a_bad_request():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(422, text="bad from")))
    with pytest.raises(RuntimeError, match="422"):
        mail.send(digest_doc(), "key", "me@example.com", client=client, sleep=lambda s: None)


def test_run_without_a_key_writes_the_file_and_sends_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "skills-market.json").write_text(json.dumps(market("2026-11-19")))
    d = mail.run(tmp_path / "data", tmp_path / "prev", {"targets": TARGETS, "ratings": RATINGS}, tmp_path / "e.html")
    assert d["sent"] is None and (tmp_path / "e.html").exists()
