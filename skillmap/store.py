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
from datetime import UTC, date, datetime, timedelta
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


LOST = ("(skillmap.postings.description is null and skillmap.postings.text_archive is null "
        "and nullif(excluded.description, '') is not null)")


def stored_text(posting: Posting, raw: RawPosting) -> str | None:
    return raw.description if posting.role_family != "non_tech" else None


class Store(Protocol):
    def known_ids(self, source: str) -> set[str]: ...
    def non_tech_titles(self, source: str) -> dict[str, str]: ...
    def save(self, items: Iterable[tuple[Posting, RawPosting]], taxonomy_version: int) -> int: ...
    def stale_extractions(self, taxonomy_version: int, archive: str | None = None) -> Iterator[RawPosting]: ...
    def stale_archives(self, taxonomy_version: int) -> list[str]: ...
    def texts_to_archive(self, limit: int) -> list[tuple[str, str]]: ...
    def mark_archived(self, posting_ids: list[str], archive: str) -> None: ...
    def forget_archive(self, posting_ids: list[str]) -> None: ...
    def archive_names(self) -> list[str]: ...
    def archive_ids(self, archive: str) -> list[str]: ...
    def mark_current(self, posting_ids: list[str], taxonomy_version: int) -> None: ...
    def update_extraction(self, postings: list[Posting], taxonomy_version: int) -> None: ...
    def rows(self) -> list[dict]: ...
    def log_run(self, source: str, fetched: int, inserted: int, error: str | None) -> None: ...
    def finished_before(self, source: str) -> bool: ...
    def trim_text(self, max_bytes: int) -> int: ...
    def text_locations(self, posting_ids: list[str]) -> dict[str, tuple[str | None, str | None]]: ...
    def purge(self, source: str) -> int: ...
    def run_history(self, days: int) -> list[dict]: ...
    def first_runs(self) -> dict[str, date]: ...
    def skill_fingerprints(self, taxonomy_version: int) -> dict[str, str] | None: ...
    def save_skill_fingerprints(self, taxonomy_version: int, fingerprints: dict[str, str]) -> None: ...


def _raw_from_row(row: dict) -> RawPosting:
    return RawPosting(
        source=row["source"], native_id=row["posting_id"].split(":", 1)[1], company=row["company"],
        title=row["title"], description=row["description"] or "", url=row["url"], location=row["location"],
        country_hint=row["country_hint"], remote_hint=row["remote_hint"], posted_on=row["posted_on"],
        sector_hint=row["sector_hint"], seniority_hint=row["seniority_hint"], min_years_hint=row["min_years_hint"],
        salary_min=row["salary_min"], salary_max=row["salary_max"], salary_currency=row["salary_currency"],
        salary_period=row["salary_period"],
        prior=(row.get("taxonomy_version"), list(row.get("skills") or []), list(row.get("skills_nice") or [])),
    )


class MemoryStore:
    def __init__(self) -> None:
        self.data: dict[str, dict] = {}
        self.runs: list[dict] = []
        self.fingerprints: dict[int, dict[str, str]] = {}

    def known_ids(self, source: str) -> set[str]:
        return {pid.split(":", 1)[1] for pid, row in self.data.items() if row["source"] == source
                and (row["description"] or row["text_archive"] or row["role_family"] == "non_tech")}

    def non_tech_titles(self, source: str) -> dict[str, str]:
        return {pid.split(":", 1)[1]: row["title"] for pid, row in self.data.items()
                if row["source"] == source and row["role_family"] == "non_tech"}

    def save(self, items: Iterable[tuple[Posting, RawPosting]], taxonomy_version: int) -> int:
        inserted = 0
        for posting, raw in items:
            existing = self.data.get(posting.posting_id)
            if existing:
                existing["last_seen"] = max(existing["last_seen"], posting.last_seen)
                existing["first_seen"] = min(existing["first_seen"], posting.first_seen)
                text = stored_text(posting, raw)
                if text and not existing["description"] and not existing["text_archive"]:
                    existing.update(description=text, taxonomy_version=None)  # text lost earlier: re-extract
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

    def forget_archive(self, posting_ids: list[str]) -> None:
        for pid in posting_ids:
            self.data[pid]["text_archive"] = None

    def archive_names(self) -> list[str]:
        return sorted({r["text_archive"] for r in self.data.values() if r["text_archive"]})

    def archive_ids(self, archive: str) -> list[str]:
        return [pid for pid, r in self.data.items() if r["text_archive"] == archive]

    def skill_fingerprints(self, taxonomy_version: int) -> dict[str, str] | None:
        return self.fingerprints.get(taxonomy_version)

    def save_skill_fingerprints(self, taxonomy_version: int, fingerprints: dict[str, str]) -> None:
        self.fingerprints[taxonomy_version] = dict(fingerprints)

    def mark_current(self, posting_ids: list[str], taxonomy_version: int) -> None:
        for pid in posting_ids:
            self.data[pid]["taxonomy_version"] = taxonomy_version

    def update_extraction(self, postings: list[Posting], taxonomy_version: int) -> None:
        for posting in postings:
            row = self.data[posting.posting_id]
            for key in EXTRACTED:
                row[key] = getattr(posting, key)
            row["taxonomy_version"] = taxonomy_version

    def rows(self) -> list[dict]:
        return [{key: row[key] for key in POSTING_COLUMNS} for row in self.data.values()]

    def log_run(self, source: str, fetched: int, inserted: int, error: str | None) -> None:
        self.runs.append({"source": source, "fetched": fetched, "inserted": inserted, "error": error,
                          "ran_at": datetime.now(UTC)})

    def finished_before(self, source: str) -> bool:
        return any(r["source"] == source and r["error"] is None for r in self.runs)

    def trim_text(self, max_bytes: int) -> int:
        return 0

    def first_runs(self) -> dict[str, date]:
        out: dict[str, date] = {}
        for r in self.runs:
            platform, day = r["source"].split(":", 1)[0], r["ran_at"].date()
            out[platform] = min(out.get(platform, day), day)
        return out

    def run_history(self, days: int) -> list[dict]:
        since = datetime.now(UTC) - timedelta(days=days)
        return [r for r in self.runs if r["ran_at"] >= since]

    def purge(self, source: str) -> int:
        """Delete every posting and run record of one source; returns the postings deleted."""
        gone = [pid for pid, row in self.data.items() if row["source"] == source]
        for pid in gone:
            del self.data[pid]
        self.runs = [r for r in self.runs if r["source"].split(":", 1)[0] != source]
        return len(gone)

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
        # A tech posting whose text was lost is left out, so collectors that only fetch text for
        # new postings fetch it again.
        rows = self.conn.execute(
            "select native_id from skillmap.postings where source = %s and (description is not null "
            "or text_archive is not null or role_family = 'non_tech')", (source,)).fetchall()
        return {row["native_id"] for row in rows}

    def non_tech_titles(self, source: str) -> dict[str, str]:
        rows = self.conn.execute("select native_id, title from skillmap.postings where source = %s "
                                 "and role_family = 'non_tech'", (source,)).fetchall()
        return {row["native_id"]: row["title"] or "" for row in rows}

    def save(self, items: Iterable[tuple[Posting, RawPosting]], taxonomy_version: int) -> int:
        columns = [*POSTING_COLUMNS, "native_id", *HINT_COLUMNS]
        placeholders = ", ".join(["%s"] * len(columns))
        sql = (
            f"insert into skillmap.postings ({', '.join(columns)}) values ({placeholders}) "
            "on conflict (posting_id) do update set "
            "last_seen = greatest(skillmap.postings.last_seen, excluded.last_seen), "
            "first_seen = least(skillmap.postings.first_seen, excluded.first_seen), "
            # Text lost earlier (neither stored nor archived) comes back when the posting is seen again.
            f"description = case when {LOST} then excluded.description else skillmap.postings.description end, "
            f"taxonomy_version = case when {LOST} then null else skillmap.postings.taxonomy_version end "
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

    def forget_archive(self, posting_ids: list[str]) -> None:
        for start in range(0, len(posting_ids), 5000):
            self.conn.execute("update skillmap.postings set text_archive = null where posting_id = any(%s)",
                              (posting_ids[start:start + 5000],))

    def archive_names(self) -> list[str]:
        return [row["text_archive"] for row in self.conn.execute(
            "select distinct text_archive from skillmap.postings where text_archive is not null order by 1").fetchall()]

    def archive_ids(self, archive: str) -> list[str]:
        return [row["posting_id"] for row in self.conn.execute(
            "select posting_id from skillmap.postings where text_archive = %s", (archive,)).fetchall()]

    def skill_fingerprints(self, taxonomy_version: int) -> dict[str, str] | None:
        row = self.conn.execute("select fingerprints from skillmap.taxonomy_versions where version = %s",
                                (taxonomy_version,)).fetchone()
        return row["fingerprints"] if row else None

    def save_skill_fingerprints(self, taxonomy_version: int, fingerprints: dict[str, str]) -> None:
        from psycopg.types.json import Jsonb

        self.conn.execute(
            "insert into skillmap.taxonomy_versions (version, fingerprints) values (%s, %s) "
            "on conflict (version) do update set fingerprints = excluded.fingerprints", (taxonomy_version, Jsonb(fingerprints)))

    def mark_current(self, posting_ids: list[str], taxonomy_version: int) -> None:
        self.conn.execute("update skillmap.postings set taxonomy_version = %s where posting_id = any(%s)",
                          (taxonomy_version, posting_ids))

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

    def finished_before(self, source: str) -> bool:
        """Whether a run of this source ever completed without error (a backfill cut short
        by time or a failure has not, so the next run reads the full window again)."""
        return self.conn.execute(
            "select exists (select 1 from skillmap.runs where source = %s and error is null) as done", (source,),
        ).fetchone()["done"]

    def text_locations(self, posting_ids: list[str]) -> dict[str, tuple[str | None, str | None]]:
        """(text still in the store, archive file holding it) for each posting that has either."""
        return {row["posting_id"]: (row["description"], row["text_archive"]) for row in self.conn.execute(
            "select posting_id, description, text_archive from skillmap.postings where posting_id = any(%s) "
            "and (description is not null or text_archive is not null)", (posting_ids,),
        ).fetchall()}

    def first_runs(self) -> dict[str, date]:
        """The day each source (boards per platform) was first read."""
        rows = self.conn.execute(
            "select split_part(source, ':', 1) as platform, min(ran_at)::date as first "
            "from skillmap.runs group by 1").fetchall()
        return {row["platform"]: row["first"] for row in rows}

    def run_history(self, days: int) -> list[dict]:
        return self.conn.execute(
            "select source, fetched, inserted, error, ran_at from skillmap.runs "
            "where ran_at > now() - make_interval(days => %s) order by ran_at", (days,),
        ).fetchall()

    def purge(self, source: str) -> int:
        """Delete every posting and run record of one source in one transaction; returns the postings deleted."""
        with self.conn.transaction():
            deleted = self.conn.execute("delete from skillmap.postings where source = %s", (source,)).rowcount
            # Board runs are logged as "<platform>:<board>".
            self.conn.execute("delete from skillmap.runs where source = %s or source like %s", (source, source + ":%"))
        return deleted

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
