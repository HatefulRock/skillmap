"""One weekly run: collect every source, store, re-extract if the taxonomy changed, aggregate."""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import httpx
import yaml

from .archive import Archive, pack, unpack
from .extract.skills import Taxonomy
from .http import PoliteClient
from .models import RawPosting, normalise_company
from .normalise import normalise
from .sources import (
    adzuna,
    amazon,
    ashby,
    greenhouse,
    hn,
    jsonld,
    lever,
    mcf,
    microsoft,
    smartrecruiters,
    workable,
    workday,
)
from .store import Store

log = logging.getLogger("skillmap")
CONFIG = Path(__file__).resolve().parents[1] / "config"
BOARDS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workable": workable,
          "smartrecruiters": smartrecruiters, "workday": workday, "amazon": amazon, "microsoft": microsoft,
          "jsonld": jsonld}
# Platforms whose list gives no text: their collectors fetch text only for postings not stored yet.
NEEDS_KNOWN = {"smartrecruiters", "workday", "microsoft", "jsonld"}


# After this many boards in a row on one platform fail with "429 Too Many Requests", the rest of
# that platform's boards are skipped for this run instead of each waiting out its retries.
RATE_LIMITED_BOARDS = 5


@dataclass
class SourceResult:
    name: str
    fetched: int = 0
    inserted: int = 0
    error: str | None = None
    rate_limited: bool = False


@dataclass
class Budget:
    """When collection must stop so the run still has time to aggregate and publish. A source
    cut short keeps what it saved; the next run picks up the rest."""
    deadline: float | None = None
    clock: Callable[[], float] = time.monotonic

    def spent(self) -> bool:
        return self.deadline is not None and self.clock() > self.deadline


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
        return [{k: v for k, v in vars(r).items() if k != "rate_limited"} for r in self.grouped()]

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
            report: RunReport, budget: Budget | None = None) -> None:
    result = SourceResult(name)
    batch = []
    budget = budget or Budget()
    try:
        if budget.spent():
            result.error = "skipped: out of time this run"
        else:
            for raw in produce():
                result.fetched += 1
                batch.append((normalise(raw, taxonomy, today), raw))
                if len(batch) >= 1000:
                    result.inserted += store.save(batch, taxonomy.version)
                    batch = []
                if result.fetched % 100 == 0 and budget.spent():
                    result.error = "stopped: out of time this run (the rest is read next run)"
                    break
    except Exception as exc:  # one broken source must not sink the run
        result.error = f"{type(exc).__name__}: {str(exc)[:160]}"
        result.rate_limited = isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429
        log.warning("source %s failed: %s", name, result.error)
    finally:
        if batch:
            result.inserted += store.save(batch, taxonomy.version)
    store.log_run(name, result.fetched, result.inserted, result.error)
    report.results.append(result)
    log.info("%s: fetched %d, new %d%s", name, result.fetched, result.inserted,
             f" ({result.error})" if result.error else "")


def collect(store: Store, client: PoliteClient, taxonomy: Taxonomy, today: date, sources: set[str],
            board_limit: int | None = None, hn_since: date | None = None,
            platforms: set[str] | None = None, budget: Budget | None = None) -> RunReport:
    report = RunReport()
    budget = budget or Budget()

    if "boards" in sources:
        boards = [b for b in load_boards() if not platforms or b["ats"] in platforms][:board_limit]
        known = {ats: store.known_ids(ats) for ats in NEEDS_KNOWN if any(b["ats"] == ats for b in boards)}
        limited: dict[str, int] = {}  # boards in a row rate-limited, per platform
        for board in boards:
            ats = board["ats"]
            if limited.get(ats, 0) >= RATE_LIMITED_BOARDS:
                report.results.append(SourceResult(f"{ats}:{board['token']}", error="skipped: platform rate-limiting"))
                continue
            module = BOARDS[ats]
            extra = {"known": known[ats]} if ats in known else {}
            if board.get("links"):
                extra["links"] = board["links"]
            _ingest(store, taxonomy, today, f"{board['ats']}:{board['token']}",
                    lambda b=board, m=module, x=extra: m.collect(client, b["token"], b["company"], b.get("sector"),
                                                                 b.get("region"), **x),
                    report, budget)
            limited[ats] = limited.get(ats, 0) + 1 if report.results[-1].rate_limited else 0
            if limited[ats] == RATE_LIMITED_BOARDS:
                log.warning("%s keeps answering 429: skipping its remaining boards this run", ats)

    settings = load_yaml("sources.yaml")
    if "adzuna" in sources:
        app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
        if app_id and app_key:
            cfg = settings["adzuna"]
            for country in cfg["countries"]:
                _ingest(store, taxonomy, today, f"adzuna:{country}",
                        lambda c=country: adzuna.collect(client, app_id, app_key, c, cfg["queries"], cfg["max_days_old"],
                                                         cfg["max_pages"]),
                        report, budget)
        else:
            report.results.append(SourceResult("adzuna", error="skipped: ADZUNA_APP_ID / ADZUNA_APP_KEY not set"))

    if "mcf" in sources:
        cfg = settings["mcf"]
        known = store.known_ids("mcf")
        # Until one run has finished: the last 60 days. After that: the week since the last run,
        # with a few days' slack.
        since = today - timedelta(days=10 if store.finished_before("mcf") else 60)
        _ingest(store, taxonomy, today, "mcf",
                lambda: mcf.collect(client, cfg["queries"], since, cfg["max_pages"], known,
                                    cfg.get("categories"), cfg.get("max_category_pages", 80)), report, budget)

    if "hn" in sources:
        if hn_since is None:
            has_history = store.finished_before("hn")  # a backfill cut short is redone
            hn_since = (today - timedelta(days=40)) if has_history else date.fromisoformat(
                str(settings["hn"]["backfill_from"]))
        _ingest(store, taxonomy, today, "hn", lambda: hn.collect(client, hn_since), report, budget)

    return report


def reextract(store: Store, taxonomy: Taxonomy, today: date, archive: Archive | None = None) -> int:
    """Re-run extraction on postings stored under an older taxonomy version: those whose text is
    in an archive file (one file at a time), then those whose text is in the database."""
    count = 0

    def run(raws: Iterable[RawPosting], texts: dict[str, str] | None = None) -> None:
        """A posting whose text is missing keeps its extraction: re-extracting an empty text
        would wipe its skills. Its text comes back if a collector sees the posting again."""
        nonlocal count
        batch: list = []
        missing: list[str] = []
        textless: list[str] = []
        for raw in raws:
            if texts is not None:
                if raw.posting_id not in texts:
                    missing.append(raw.posting_id)
                    continue
                raw.description = texts[raw.posting_id]
            posting = normalise(raw, taxonomy, today)
            if not raw.description and posting.role_family != "non_tech":
                textless.append(raw.posting_id)
                continue
            batch.append(posting)
            if len(batch) >= 1000:
                store.update_extraction(batch, taxonomy.version)
                count += len(batch)
                batch = []
        if batch:
            store.update_extraction(batch, taxonomy.version)
            count += len(batch)
        if missing:
            log.warning("%d postings point to an archive file that doesn't hold their text", len(missing))
            store.forget_archive(missing)
        for start in range(0, len(textless), 1000):
            store.mark_current(textless[start:start + 1000], taxonomy.version)

    if archive is not None:
        for name in store.stale_archives(taxonomy.version):
            try:
                texts = unpack(archive.get(name))
            except Exception as exc:  # one unreadable file must not stop the run; retried next run
                log.warning("archive %s unreadable, its postings keep their old extraction: %s", name, exc)
                continue
            run(store.stale_extractions(taxonomy.version, name), texts)
    run(store.stale_extractions(taxonomy.version))
    return count


def check_archives(store: Store, archive: Archive) -> int:
    """Clear the archive pointer of every posting whose text isn't in the file it points to (a
    file overwritten or deleted), so its text is fetched again when a collector next sees it.
    Returns how many pointers were cleared."""
    cleared = 0
    for name in store.archive_names():
        try:
            held = set(unpack(archive.get(name)))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (400, 404):  # Supabase answers 400 for a missing object
                log.warning("archive %s unreadable, not checked: %s", name, exc)
                continue
            held = set()
        except FileNotFoundError:
            held = set()
        except Exception as exc:
            log.warning("archive %s unreadable, not checked: %s", name, exc)
            continue
        missing = [pid for pid in store.archive_ids(name) if pid not in held]
        if missing:
            log.warning("archive %s lacks the text of %d postings that point to it", name, len(missing))
            store.forget_archive(missing)
            cleared += len(missing)
    return cleared


def archive_texts(store: Store, archive: Archive, today: date, per_file: int = 20_000) -> int:
    """Move every posting text still in the database into archive files, then clear it there.
    Each file is written before its postings are marked, so a failure leaves text in place."""
    moved = 0
    run_id = uuid.uuid4().hex[:8]
    for part in range(1000):
        items = store.texts_to_archive(per_file)
        if not items:
            break
        # Unique per run: two runs on one day must never write the same file (an overwritten
        # file loses the text of every posting it held).
        name = f"texts/{today.isoformat()}-{run_id}-{part:03d}.jsonl.gz"
        archive.put(name, pack(items))
        store.mark_archived([pid for pid, _ in items], name)
        moved += len(items)
    return moved
