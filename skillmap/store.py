"""Where postings live.

`PostgresStore` is the real one (a Supabase project; the connection string comes from the
DATABASE_URL secret). `MemoryStore` has the same interface and backs the tests and dry runs.
Full description text is stored for tech roles so extraction can be re-run when the taxonomy
improves, but it never leaves the database: aggregation reads every column except it. Non-tech
postings (sales, legal, ops) keep their metadata only: they never enter the skill statistics, and
skipping their text roughly halves the database.
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
# Everything re-extraction rewrites.
EXTRACTED = ["seniority", "role_family", "sector", "skills", "skills_nice", "country", "city", "region", "remote",
             "salary_min", "salary_max", "salary_currency", "salary_period", "n_words", "min_years", "terms", "degree",
             "visa_sponsorship", "equity"]
HINT_COLUMNS = ["description", "seniority_hint", "min_years_hint", "sector_hint", "country_hint", "remote_hint",
                "taxonomy_version"]


def stored_text(posting: Posting, raw: RawPosting) -> str | None:
    return raw.description if posting.role_family != "non_tech" else None


class Store(Protocol):
    def known_ids(self, source: str) -> set[str]: ...
    def save(self, items: Iterable[tuple[Posting, RawPosting]], taxonomy_version: int) -> int: ...
    def stale_extractions(self, taxonomy_version: int, archive: str | None = None) -> Iterator[RawPosting]: ...
    def stale_archives(self, taxonomy_version: int) -> list[str]: ...
    def texts_to_archive(self, limit: int) -> list[tuple[str, str]]: ...
    def mark_archived(self, posting_ids: list[str], archive: str) -> None: ...
    def update_extraction(self, postings: list[Posting], taxonomy_version: int) -> None: ...
    def rows(self) -> list[dict]: ...
    def log_run(self, source: str, fetched: int, inserted: int, error: str | None) -> None: ...
    def trim_text(self, max_bytes: int) -> int: ...
    def text_locations(self, posting_ids: list[str]) -> dict[str, tuple[str | None, str | None]]: ...


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
            row.update(description=stored_text(posting, raw), text_archive=None, seniority_hint=raw.seniority_hint, min_years_hint=raw.min_years_hint,
                       sector_hint=raw.sector_hint, country_hint=raw.country_hint, remote_hint=raw.remote_hint,
                       taxonomy_version=taxonomy_version)
            self.data[posting.posting_id] = row
            inserted += 1
        return inserted

    def stale_extractions(self, taxonomy_version: int, archive: str | None = None) -> Iterator[RawPosting]:
        for row in list(self.data.values()):
            if row["taxonomy_version"] != taxonomy_version and row["text_archive"] == archive:
                yield _raw_from_row(row)

    def stale_archives(self, taxonomy_version: int) -> list[str]:
        return sorted({r["text_archive"] for r in self.data.values()
                       if r["text_archive"] and r["taxonomy_version"] != taxonomy_version})

    def texts_to_archive(self, limit: int) -> list[tuple[str, str]]:
        rows = [(pid, r["description"]) for pid, r in sorted(self.data.items()) if r["description"] is not None]
        return rows[:limit]

    def mark_archived(self, posting_ids: list[str], archive: str) -> None:
        for pid in posting_ids:
            self.data[pid].update(description=None, text_archive=archive)

    def update_extraction(self, postings: list[Posting], taxonomy_version: int) -> None:
        for posting in postings:
            row = self.data[posting.posting_id]
            for key in EXTRACTED:
                row[key] = getattr(posting, key)
            row["taxonomy_version"] = taxonomy_version

    def rows(self) -> list[dict]:
        return [{key: row[key] for key in POSTING_COLUMNS} for row in self.data.values()]

    def log_run(self, source: str, fetched: int, inserted: int, error: str | None) -> None:
        self.runs.append({"source": source, "fetched": fetched, "inserted": inserted, "error": error})

    def trim_text(self, max_bytes: int) -> int:
        return 0

    def text_locations(self, posting_ids: list[str]) -> dict[str, tuple[str | None, str | None]]:
        """(text still in the store, archive file holding it) for each posting that has either."""
        out = {}
        for pid in posting_ids:
            row = self.data.get(pid)
            if row and (row["description"] or row["text_archive"]):
                out[pid] = (row["description"], row["text_archive"])
        return out


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
                *[values[column] for column in POSTING_COLUMNS], raw.native_id, stored_text(posting, raw), raw.seniority_hint,
                raw.min_years_hint, raw.sector_hint, raw.country_hint, raw.remote_hint, taxonomy_version,
            ))
            if len(batch) >= 500:
                flush()
        flush()
        return inserted

    def stale_extractions(self, taxonomy_version: int, archive: str | None = None) -> Iterator[RawPosting]:
        # Ids first, then rows in chunks: the caller writes back between chunks, and a
        # server-side cursor can't stay open across those writes in autocommit mode.
        ids = [row["posting_id"] for row in self.conn.execute(
            "select posting_id from skillmap.postings where taxonomy_version is distinct from %s "
            "and text_archive is not distinct from %s order by posting_id",
            (taxonomy_version, archive),
        ).fetchall()]
        for start in range(0, len(ids), 1000):
            chunk = self.conn.execute(
                "select * from skillmap.postings where posting_id = any(%s) order by posting_id", (ids[start:start + 1000],)
            ).fetchall()
            for row in chunk:
                yield _raw_from_row(row)

    def stale_archives(self, taxonomy_version: int) -> list[str]:
        return [row["text_archive"] for row in self.conn.execute(
            "select distinct text_archive from skillmap.postings where text_archive is not null "
            "and taxonomy_version is distinct from %s order by 1", (taxonomy_version,),
        ).fetchall()]

    def texts_to_archive(self, limit: int) -> list[tuple[str, str]]:
        return [(row["posting_id"], row["description"]) for row in self.conn.execute(
            "select posting_id, description from skillmap.postings where description is not null "
            "order by posting_id limit %s", (limit,),
        ).fetchall()]

    def mark_archived(self, posting_ids: list[str], archive: str) -> None:
        self.conn.execute(
            "update skillmap.postings set description = null, text_archive = %s where posting_id = any(%s)",
            (archive, posting_ids),
        )

    def update_extraction(self, postings: list[Posting], taxonomy_version: int) -> None:
        assignments = ", ".join(f"{column}=%s" for column in EXTRACTED)
        with self.conn.cursor() as cursor:
            cursor.executemany(
                f"update skillmap.postings set {assignments}, taxonomy_version=%s where posting_id=%s",
                [(*[getattr(p, column) for column in EXTRACTED], taxonomy_version, p.posting_id) for p in postings],
            )

    def rows(self) -> list[dict]:
        return self.conn.execute(f"select {', '.join(POSTING_COLUMNS)} from skillmap.postings").fetchall()

    def log_run(self, source: str, fetched: int, inserted: int, error: str | None) -> None:
        self.conn.execute(
            "insert into skillmap.runs (source, fetched, inserted, error) values (%s, %s, %s, %s)",
            (source, fetched, inserted, error),
        )

    def text_locations(self, posting_ids: list[str]) -> dict[str, tuple[str | None, str | None]]:
        """(text still in the store, archive file holding it) for each posting that has either."""
        return {row["posting_id"]: (row["description"], row["text_archive"]) for row in self.conn.execute(
            "select posting_id, description, text_archive from skillmap.postings where posting_id = any(%s) "
            "and (description is not null or text_archive is not null)", (posting_ids,),
        ).fetchall()}

    def text_bytes(self) -> int:
        return self.conn.execute("select pg_total_relation_size('skillmap.postings') as n").fetchone()["n"]

    def trim_text(self, max_bytes: int) -> int:
        """Keep the table under the free plan's size: drop the stored text (never the extracted
        fields) of the postings that went offline longest ago, a year's worth at a time.

        HN posts are short and are the long history, so they go last. Dropped text only means
        those postings keep their current extraction if the taxonomy changes.
        """
        trimmed = 0
        for days in (730, 548, 365, 270, 180, 120, 90):
            if self.text_bytes() <= max_bytes:
                break
            cursor = self.conn.execute(
                "update skillmap.postings set description = null where description is not null and source <> 'hn' "
                "and last_seen < current_date - %s::int",
                (days,),
            )
            trimmed += cursor.rowcount
            self.conn.execute("vacuum skillmap.postings")
        return trimmed


def today_utc() -> date:
    from datetime import datetime

    return datetime.now(UTC).date()
