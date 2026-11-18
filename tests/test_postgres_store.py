"""PostgresStore against a real database. Runs when SKILLMAP_TEST_DATABASE_URL is set (CI sets it)."""

import os
from datetime import date

import pytest

from skillmap.extract.skills import Taxonomy
from skillmap.models import RawPosting
from skillmap.normalise import normalise

DSN = os.environ.get("SKILLMAP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="SKILLMAP_TEST_DATABASE_URL not set")
TAX = Taxonomy.load()


def raw(i, source="greenhouse"):
    return RawPosting(source=source, native_id=str(i), company="Acme", title="Senior AI Engineer",
                      description="Python and RAG", location="Singapore", posted_on=date(2026, 11, 1))


def test_round_trip():
    from skillmap.store import PostgresStore

    store = PostgresStore(DSN)
    store.conn.execute("truncate skillmap.postings, skillmap.runs")
    day1, day2 = date(2026, 11, 10), date(2026, 11, 11)
    items = [(normalise(raw(i), TAX, day1), raw(i)) for i in range(1203)]
    assert store.save(items, TAX.version) == 1203
    again = [(normalise(raw(i), TAX, day2), raw(i)) for i in range(1200, 1210)]
    assert store.save(again, TAX.version) == 7  # 3 known, 7 new

    rows = {r["posting_id"]: r for r in store.rows()}
    assert rows["greenhouse:0"]["last_seen"] == day1
    assert rows["greenhouse:1200"]["last_seen"] == day2
    assert rows["greenhouse:1200"]["first_seen"] == date(2026, 11, 1)
    assert rows["greenhouse:5"]["skills"] == ["Python", "RAG"]
    assert "description" not in rows["greenhouse:5"]
    assert store.known_ids("greenhouse") >= {"0", "1209"}

    stale = list(store.stale_extractions(TAX.version + 1))
    assert len(stale) == 1210 and stale[0].description == "Python and RAG"
    store.update_extraction([normalise(stale[0], TAX, day2)], TAX.version + 1)
    assert len(list(store.stale_extractions(TAX.version + 1))) == 1209
    store.log_run("greenhouse:acme", 10, 7, None)


def test_outputs_from_postgres(tmp_path):
    from skillmap.cli import write_outputs
    from skillmap.store import PostgresStore

    store = PostgresStore(DSN)
    store.conn.execute("truncate skillmap.postings, skillmap.runs")
    r = raw(1, "mcf")
    r.salary_min, r.salary_max, r.salary_currency, r.salary_period = 7000, 9000, "SGD", "month"
    store.save([(normalise(r, TAX, date(2026, 11, 10)), r)], TAX.version)
    data = write_outputs(store, TAX, date(2026, 11, 10), tmp_path)
    assert data["salary"]["median_monthly"] == 8000


def test_trim_text_drops_the_oldest_closed_postings_first():
    from skillmap.store import PostgresStore

    store = PostgresStore(DSN)
    store.conn.execute("truncate skillmap.postings, skillmap.runs")
    old, recent = date(2023, 1, 10), date.today()
    items = [(normalise(raw(i), TAX, old), raw(i)) for i in range(5)]
    items += [(normalise(raw(10 + i), TAX, recent), raw(10 + i)) for i in range(5)]
    items += [(normalise(raw(20, "hn"), TAX, old), raw(20, "hn"))]
    store.save(items, TAX.version)
    assert store.trim_text(0) == 5  # only the long-closed board postings, never HN
    texts = {row["posting_id"]: row["description"] for row in store.conn.execute(
        "select posting_id, description from skillmap.postings").fetchall()}
    assert texts["greenhouse:0"] is None and texts["greenhouse:10"] == "Python and RAG"
    assert store.trim_text(10**12) == 0


def test_archive_and_reextract_from_postgres(tmp_path):
    from skillmap.archive import LocalArchive
    from skillmap.runner import archive_texts, reextract
    from skillmap.store import PostgresStore

    store = PostgresStore(DSN)
    store.conn.execute("truncate skillmap.postings, skillmap.runs")
    day = date(2026, 11, 10)
    store.save([(normalise(raw(i), TAX, day), raw(i)) for i in range(7)], TAX.version)
    archive = LocalArchive(tmp_path)
    assert archive_texts(store, archive, day, per_file=5) == 7
    assert store.texts_to_archive(10) == []
    store.conn.execute("update skillmap.postings set taxonomy_version = 0, skills = '{}', terms = '{}'")
    assert store.stale_archives(TAX.version) == ["texts/2026-11-10-000.jsonl.gz", "texts/2026-11-10-001.jsonl.gz"]
    assert reextract(store, TAX, day, archive) == 7
    rows = store.conn.execute("select skills, n_words, min_years, degree from skillmap.postings").fetchall()
    assert all(r["skills"] == ["Python", "RAG"] and r["n_words"] == 3 for r in rows)


def test_purge_deletes_only_one_source():
    from skillmap.store import PostgresStore

    store = PostgresStore(DSN)
    store.conn.execute("truncate skillmap.postings, skillmap.runs")
    day = date(2026, 11, 10)
    store.save([(normalise(raw(i, s), TAX, day), raw(i, s)) for s in ("greenhouse", "workday") for i in range(3)],
               TAX.version)
    store.log_run("workday:db", 3, 3, None)
    store.log_run("workday", 3, 3, None)
    assert store.purge("workday") == 3
    assert {r["source"] for r in store.rows()} == {"greenhouse"}
    assert not store.finished_before("workday")
