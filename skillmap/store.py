"""Where postings live.

`PostgresStore` is the real one (a Supabase project; the connection string comes from the
DATABASE_URL secret). `MemoryStore` has the same interface and backs the tests and dry runs.
Full description text is stored so extraction can be re-run when the taxonomy improves, but it
never leaves the database: aggregation reads every column except it.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import asdict, fields
from datetime import UTC, date
from pathlib import Path
from typing import Protocol

from .models import Posting, RawPosting

SCHEMA = (Path(__file__).resolve().parents[1] / "sql" / "schema.sql")
POSTING_COLUMNS = [f.name for f in fields(Posting)]
HINT_COLUMNS = ["description", "seniority_hint", "min_years_hint", "sector_hint", "country_hint", "remote_hint",
                "taxonomy_version"]


class Store(Protocol):
    def known_ids(self, source: str) -> set[str]: ...
    def save(self, items: Iterable[tuple[Posting, RawPosting]], taxonomy_version: int) -> int: ...
    def stale_extractions(self, taxonomy_version: int) -> Iterator[RawPosting]: ...
    def update_extraction(self, postings: list[Posting], taxonomy_version: int) -> None: ...
    def rows(self) -> list[dict]: ...
    def log_run(self, source: str, fetched: int, inserted: int, error: str | None) -> None: ...


def _raw_from_row(row: dict) -> RawPosting:
    return RawPosting(
        source=row["source"], native_id=row["posting_id"].split(":", 1)[1], company=row["company"],
        title=row["title"], description=row["description"] or "", url=row["url"], location=row["location"],
        country_hint=row["country_hint"], remote_hint=row["remote_hint"], posted_on=row["posted_on"],
        sector_hint=row["sector_hint"], seniority_hint=row["seniority_hint"], min_years_hint=row["min_years_hint"],
        salary_min=row["salary_min"], salary_max=row["salary_max"], salary_currency=row["salary_currency"],
        salary_period=row["salary_period"],
    )


class MemoryStore:
    def __init__(self) -> None:
        self.data: dict[str, dict] = {}
        self.runs: list[dict] = []

    def known_ids(self, source: str) -> set[str]:
        return {pid.split(":", 1)[1] for pid, row in self.data.items() if row["source"] == source}

    def save(self, items: Iterable[tuple[Posting, RawPosting]], taxonomy_version: int) -> int:
        inserted = 0
        for posting, raw in items:
            existing = self.data.get(posting.posting_id)
            if existing:
                existing["last_seen"] = max(existing["last_seen"], posting.last_seen)
                existing["first_seen"] = min(existing["first_seen"], posting.first_seen)
                continue
            row = asdict(posting)
            row.update(description=raw.description, seniority_hint=raw.seniority_hint, min_years_hint=raw.min_years_hint,
                       sector_hint=raw.sector_hint, country_hint=raw.country_hint, remote_hint=raw.remote_hint,
                       taxonomy_version=taxonomy_version)
            self.data[posting.posting_id] = row
            inserted += 1
        return inserted

    def stale_extractions(self, taxonomy_version: int) -> Iterator[RawPosting]:
        for row in list(self.data.values()):
            if row["taxonomy_version"] != taxonomy_version:
                yield _raw_from_row(row)

    def update_extraction(self, postings: list[Posting], taxonomy_version: int) -> None:
        for posting in postings:
            row = self.data[posting.posting_id]
            for key in ("seniority", "role_family", "sector", "skills", "skills_nice", "country", "city", "region", "remote"):
                row[key] = getattr(posting, key)
            row["taxonomy_version"] = taxonomy_version

    def rows(self) -> list[dict]:
        return [{key: row[key] for key in POSTING_COLUMNS} for row in self.data.values()]

    def log_run(self, source: str, fetched: int, inserted: int, error: str | None) -> None:
        self.runs.append({"source": source, "fetched": fetched, "inserted": inserted, "error": error})


class PostgresStore:
    def __init__(self, dsn: str):
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=True, prepare_threshold=None)
        self.conn.execute(SCHEMA.read_text())

    def known_ids(self, source: str) -> set[str]:
        rows = self.conn.execute("select native_id from skillmap.postings where source = %s", (source,)).fetchall()
        return {row["native_id"] for row in rows}

    def save(self, items: Iterable[tuple[Posting, RawPosting]], taxonomy_version: int) -> int:
        columns = [*POSTING_COLUMNS, "native_id", *HINT_COLUMNS]
        placeholders = ", ".join(["%s"] * len(columns))
        sql = (
            f"insert into skillmap.postings ({', '.join(columns)}) values ({placeholders}) "
            "on conflict (posting_id) do update set "
            "last_seen = greatest(skillmap.postings.last_seen, excluded.last_seen), "
            "first_seen = least(skillmap.postings.first_seen, excluded.first_seen) "
            "returning (xmax = 0) as inserted"
        )
        inserted = 0
        batch: list[tuple] = []

        def flush() -> None:
            nonlocal inserted
            if not batch:
                return
            with self.conn.cursor() as cursor:
                cursor.executemany(sql, batch, returning=True)
                while True:
                    row = cursor.fetchone()
                    inserted += bool(row and row["inserted"])
                    if not cursor.nextset():
                        break
            batch.clear()

        for posting, raw in items:
            values = asdict(posting)
            batch.append((
                *[values[column] for column in POSTING_COLUMNS], raw.native_id, raw.description, raw.seniority_hint,
                raw.min_years_hint, raw.sector_hint, raw.country_hint, raw.remote_hint, taxonomy_version,
            ))
            if len(batch) >= 500:
                flush()
        flush()
        return inserted

    def stale_extractions(self, taxonomy_version: int) -> Iterator[RawPosting]:
        # Ids first, then rows in chunks: the caller writes back between chunks, and a
        # server-side cursor can't stay open across those writes in autocommit mode.
        ids = [row["posting_id"] for row in self.conn.execute(
            "select posting_id from skillmap.postings where taxonomy_version is distinct from %s order by posting_id",
            (taxonomy_version,),
        ).fetchall()]
        for start in range(0, len(ids), 1000):
            chunk = self.conn.execute(
                "select * from skillmap.postings where posting_id = any(%s) order by posting_id", (ids[start:start + 1000],)
            ).fetchall()
            for row in chunk:
                yield _raw_from_row(row)

    def update_extraction(self, postings: list[Posting], taxonomy_version: int) -> None:
        with self.conn.cursor() as cursor:
            cursor.executemany(
                "update skillmap.postings set seniority=%s, role_family=%s, sector=%s, skills=%s, skills_nice=%s, "
                "country=%s, city=%s, region=%s, remote=%s, taxonomy_version=%s where posting_id=%s",
                [(p.seniority, p.role_family, p.sector, p.skills, p.skills_nice, p.country, p.city, p.region, p.remote,
                  taxonomy_version, p.posting_id) for p in postings],
            )

    def rows(self) -> list[dict]:
        return self.conn.execute(f"select {', '.join(POSTING_COLUMNS)} from skillmap.postings").fetchall()

    def log_run(self, source: str, fetched: int, inserted: int, error: str | None) -> None:
        self.conn.execute(
            "insert into skillmap.runs (source, fetched, inserted, error) values (%s, %s, %s, %s)",
            (source, fetched, inserted, error),
        )


def today_utc() -> date:
    from datetime import datetime

    return datetime.now(UTC).date()
