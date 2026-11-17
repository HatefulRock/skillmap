"""One weekly run: collect every source, store, re-extract if the taxonomy changed, aggregate."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import yaml

from .archive import Archive, pack, unpack
from .extract.skills import Taxonomy
from .http import PoliteClient
from .models import RawPosting, normalise_company
from .normalise import normalise
from .sources import adzuna, ashby, greenhouse, hn, lever, mcf, smartrecruiters, workable
from .store import Store

log = logging.getLogger("skillmap")
CONFIG = Path(__file__).resolve().parents[1] / "config"
BOARDS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workable": workable,
          "smartrecruiters": smartrecruiters}


@dataclass
class SourceResult:
    name: str
    fetched: int = 0
    inserted: int = 0
    error: str | None = None


@dataclass
class RunReport:
    results: list[SourceResult] = field(default_factory=list)

    def grouped(self) -> list[SourceResult]:
        """Company boards rolled up per platform: a run reads ~1,000 of them, too many to list.
        A platform only counts as failed if every one of its boards failed; individual dead
        boards are reported in `failed` instead."""
        out: dict[str, SourceResult] = {}
        failures: dict[str, list[str]] = {}
        for r in self.results:
            platform = r.name.split(":", 1)[0]
            if platform not in BOARDS:
                out[r.name] = r
                continue
            group = out.setdefault(platform, SourceResult(platform))
            group.fetched += r.fetched
            group.inserted += r.inserted
            if r.error:
                failures.setdefault(platform, []).append(r.name.split(":", 1)[1])
        for platform, failed in failures.items():
            boards = sum(1 for r in self.results if r.name.startswith(f"{platform}:"))
            if len(failed) == boards:
                out[platform].error = f"all {boards} boards failed"
        return list(out.values())

    def failed_boards(self) -> list[str]:
        return [r.name for r in self.results if r.error and r.name.split(":", 1)[0] in BOARDS]

    def as_dicts(self) -> list[dict]:
        return [vars(r) for r in self.grouped()]

    def summary(self) -> str:
        lines = [f"{'source':<32} {'fetched':>8} {'new':>8}  error"]
        for r in self.grouped():
            boards = sum(1 for x in self.results if x.name.startswith(f"{r.name}:"))
            name = f"{r.name} ({boards} boards)" if boards else r.name
            lines.append(f"{name:<32} {r.fetched:>8} {r.inserted:>8}  {r.error or ''}")
        failed = self.failed_boards()
        if failed:
            lines.append(f"\n{len(failed)} boards failed: {', '.join(failed[:60])}{' …' if len(failed) > 60 else ''}")
        return "\n".join(lines)


def load_yaml(name: str) -> dict:
    return yaml.safe_load((CONFIG / name).read_text())


def load_boards() -> list[dict]:
    """Hand-curated boards (companies.yaml) plus discovered ones (boards.yaml). A curated entry
    wins over a discovered one for the same company or the same board."""
    curated = load_yaml("companies.yaml")["companies"]
    discovered = (load_yaml("boards.yaml") or {}).get("boards") or [] if (CONFIG / "boards.yaml").exists() else []
    names = {normalise_company(b["company"]) for b in curated}
    ids = {(b["ats"], b["token"]) for b in curated}
    extra = [b for b in discovered if normalise_company(b["company"]) not in names and (b["ats"], b["token"]) not in ids]
    return [*curated, *extra]


def _ingest(store: Store, taxonomy: Taxonomy, today: date, name: str, produce: Callable[[], Iterable[RawPosting]],
            report: RunReport) -> None:
    result = SourceResult(name)
    batch = []
    try:
        for raw in produce():
            result.fetched += 1
            batch.append((normalise(raw, taxonomy, today), raw))
            if len(batch) >= 1000:
                result.inserted += store.save(batch, taxonomy.version)
                batch = []
    except Exception as exc:  # one broken source must not sink the run
        result.error = f"{type(exc).__name__}: {str(exc)[:160]}"
        log.warning("source %s failed: %s", name, result.error)
    finally:
        if batch:
            result.inserted += store.save(batch, taxonomy.version)
    store.log_run(name, result.fetched, result.inserted, result.error)
    report.results.append(result)
    log.info("%s: fetched %d, new %d", name, result.fetched, result.inserted)


def collect(store: Store, client: PoliteClient, taxonomy: Taxonomy, today: date, sources: set[str],
            board_limit: int | None = None, hn_since: date | None = None) -> RunReport:
    report = RunReport()

    if "boards" in sources:
        boards = load_boards()[:board_limit]
        sr_known = store.known_ids("smartrecruiters") if any(b["ats"] == "smartrecruiters" for b in boards) else set()
        for board in boards:
            module = BOARDS[board["ats"]]
            extra = {"known": sr_known} if board["ats"] == "smartrecruiters" else {}
            _ingest(store, taxonomy, today, f"{board['ats']}:{board['token']}",
                    lambda b=board, m=module, x=extra: m.collect(client, b["token"], b["company"], b.get("sector"),
                                                                 b.get("region"), **x),
                    report)

    settings = load_yaml("sources.yaml")
    if "adzuna" in sources:
        app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
        if app_id and app_key:
            cfg = settings["adzuna"]
            for country in cfg["countries"]:
                _ingest(store, taxonomy, today, f"adzuna:{country}",
                        lambda c=country: adzuna.collect(client, app_id, app_key, c, cfg["queries"], cfg["max_days_old"],
                                                         cfg["max_pages"]),
                        report)
        else:
            report.results.append(SourceResult("adzuna", error="skipped: ADZUNA_APP_ID / ADZUNA_APP_KEY not set"))

    if "mcf" in sources:
        cfg = settings["mcf"]
        known = store.known_ids("mcf")
        # First run: last 60 days. After that: the week since the last run, with a few days' slack.
        since = today - timedelta(days=60 if not known else 10)
        _ingest(store, taxonomy, today, "mcf",
                lambda: mcf.collect(client, cfg["queries"], since, cfg["max_pages"], known), report)

    if "hn" in sources:
        if hn_since is None:
            has_history = bool(store.known_ids("hn"))
            hn_since = (today - timedelta(days=40)) if has_history else date.fromisoformat(
                str(settings["hn"]["backfill_from"]))
        _ingest(store, taxonomy, today, "hn", lambda: hn.collect(client, hn_since), report)

    return report


def reextract(store: Store, taxonomy: Taxonomy, today: date, archive: Archive | None = None) -> int:
    """Re-run extraction on postings stored under an older taxonomy version: those whose text is
    in an archive file (one file at a time), then those whose text is in the database."""
    count = 0

    def run(raws: Iterable[RawPosting], texts: dict[str, str] | None = None) -> None:
        nonlocal count
        batch = []
        for raw in raws:
            if texts is not None:
                raw.description = texts.get(raw.posting_id, "")
            batch.append(normalise(raw, taxonomy, today))
            if len(batch) >= 1000:
                store.update_extraction(batch, taxonomy.version)
                count += len(batch)
                batch = []
        if batch:
            store.update_extraction(batch, taxonomy.version)
            count += len(batch)

    if archive is not None:
        for name in store.stale_archives(taxonomy.version):
            run(store.stale_extractions(taxonomy.version, name), unpack(archive.get(name)))
    run(store.stale_extractions(taxonomy.version))
    return count


def archive_texts(store: Store, archive: Archive, today: date, per_file: int = 20_000) -> int:
    """Move every posting text still in the database into archive files, then clear it there.
    Each file is written before its postings are marked, so a failure leaves text in place."""
    moved = 0
    for part in range(1000):
        items = store.texts_to_archive(per_file)
        if not items:
            break
        name = f"texts/{today.isoformat()}-{part:03d}.jsonl.gz"
        archive.put(name, pack(items))
        store.mark_archived([pid for pid, _ in items], name)
        moved += len(items)
    return moved
