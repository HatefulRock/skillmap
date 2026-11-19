"""The monthly accuracy check against Claude, with a stand-in client."""

import json
from datetime import date, timedelta
from types import SimpleNamespace

from skillmap import audit
from skillmap.archive import LocalArchive, pack
from skillmap.extract.skills import Taxonomy

TODAY = date(2026, 11, 17)
TAX = Taxonomy.load()


class FakeClient:
    def __init__(self, answers):
        self.answers, self.calls = answers, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        if answer is None:
            return SimpleNamespace(stop_reason="refusal", content=[])
        return SimpleNamespace(stop_reason="end_turn", content=[
            SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=json.dumps({"skills": answer}))])


class FakeStore:
    def __init__(self, rows, where):
        self._rows, self.where = rows, where

    def rows(self):
        return self._rows

    def text_locations(self, ids):
        return {pid: self.where[pid] for pid in ids if pid in self.where}


def row(i, **over):
    base = {"posting_id": f"greenhouse:{i}", "source": "greenhouse", "company": f"Co{i}", "title": "ML Engineer",
            "role_family": "ai_ml", "last_seen": TODAY - timedelta(days=3)}
    base.update(over)
    return base


def test_run_scores_regex_against_claude(tmp_path):
    archive = LocalArchive(tmp_path)
    archive.put("texts/a.jsonl.gz", pack([("greenhouse:2", "<p>You will write Go and SQL.</p>")]))
    rows = [row(1), row(2), row(3), row(4, role_family="non_tech"), row(5, last_seen=TODAY - timedelta(days=90)), row(6)]
    where = {"greenhouse:1": ("We need strong Python and PyTorch skills.", None), "greenhouse:2": (None, "texts/a.jsonl.gz"),
             "greenhouse:3": ("Python and Kubernetes.", None), "greenhouse:4": ("Python", None), "greenhouse:5": ("Python", None)}
    client = FakeClient([])
    store = FakeStore(rows, where)
    order = [r["posting_id"] for r in audit.sample(store, rows, TODAY, 10, "2026-11")]
    assert sorted(order) == ["greenhouse:1", "greenhouse:2", "greenhouse:3"]  # live tech with text only
    # Answers are consumed in the sample's order; line them up with it.
    by_id = {"greenhouse:2": ["Go", "SQL", "Made-up skill"], "greenhouse:1": ["Python"], "greenhouse:3": None}
    client.answers = [by_id[pid] for pid in order]
    report = audit.run(store, archive, TAX, client, TODAY, n=10)
    assert report["n"] == 2 and report["declined"] == 1 and report["taxonomy_version"] == TAX.version
    regex_1 = set(sum(TAX.extract("ML Engineer", where["greenhouse:1"][0]), []))
    assert "PyTorch" in regex_1
    pytorch = next(s for s in report["skills"] if s["name"] == "PyTorch")
    assert pytorch["fp"] == 1 and pytorch["extra"] == [{"posting_id": "greenhouse:1", "company": "Co1", "title": "ML Engineer"}]
    assert all(s["name"] != "Made-up skill" for s in report["skills"])
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"} and "Rust" in call["system"][0]["text"]
    assert "<p>" not in json.dumps([c["messages"] for c in client.calls])  # Claude reads plain text


def test_score_and_history(tmp_path):
    report = audit.score([(row(1), {"Python", "Go"}, {"Python", "SQL"}), (row(2), {"Python"}, {"Python"})])
    assert (report["tp"], report["fp"], report["fn"]) == (2, 1, 1)
    assert report["precision"] == 0.667 and report["recall"] == 0.667 and report["f1"] == 0.667
    assert [s["name"] for s in report["skills"]] == ["Go", "SQL", "Python"]
    report.update(date="2026-11-01", model=audit.MODEL, taxonomy_version=3, declined=0)
    path = tmp_path / "audit.json"
    audit.write(report, path)
    data = audit.write({**report, "date": "2026-12-01"}, path)
    assert [h["date"] for h in data["history"]] == ["2026-12-01", "2026-11-01"]
    assert "precision 67%" in audit.summary(report) and "SQL: 1 / 0" in audit.summary(report)


def test_headline_for_the_website(tmp_path):
    assert audit.headline(tmp_path / "missing.json") is None
    report = audit.score([(row(1), {"Python", "Go"}, {"Python"})])
    report.update(date="2026-11-03", model=audit.MODEL, taxonomy_version=3, declined=0)
    audit.write(report, tmp_path / "audit.json")
    head = audit.headline(tmp_path / "audit.json")
    assert head["history"][0]["precision"] == 0.5
    assert head["worst"] == [{"name": "Go", "tp": 0, "fp": 1, "fn": 0, "precision": 0.0, "recall": None}]
