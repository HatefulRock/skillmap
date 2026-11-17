"""One daily run: collect every source, store, re-extract if the taxonomy changed, aggregate."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import yaml

from .extract.skills import Taxonomy
from .http import PoliteClient
from .models import RawPosting
from .normalise import normalise
from .sources import adzuna, ashby, greenhouse, hn, lever, mcf, workable
from .store import Store

log = logging.getLogger("skillmap")
CONFIG = Path(__file__).resolve().parents[1] / "config"
BOARDS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workable": workable}


@dataclass
class SourceResult:
    name: str
    fetched: int = 0
    inserted: int = 0
    error: str | None = None


@dataclass
class RunReport:
    results: list[SourceResult] = field(default_factory=list)

    def as_dicts(self) -> list[dict]:
        return [vars(r) for r in self.results]

    def summary(self) -> str:
        lines = [f"{'source':<32} {'fetched':>8} {'new':>8}  error"]
        for r in self.results:
            lines.append(f"{r.name:<32} {r.fetched:>8} {r.inserted:>8}  {r.error or ''}")
        return "\n".join(lines)


def load_yaml(name: str) -> dict:
    return yaml.safe_load((CONFIG / name).read_text())


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
        boards = load_yaml("companies.yaml")["companies"][:board_limit]
        for board in boards:
            module = BOARDS[board["ats"]]
            _ingest(store, taxonomy, today, f"{board['ats']}:{board['token']}",
                    lambda b=board, m=module: m.collect(client, b["token"], b["company"], b.get("sector"), b.get("region")),
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
        # First run: last 60 days. After that: a few days back, to catch anything a missed run skipped.
        since = today - timedelta(days=60 if not known else 4)
        _ingest(store, taxonomy, today, "mcf",
                lambda: mcf.collect(client, cfg["queries"], since, cfg["max_pages"], known), report)

    if "hn" in sources:
        if hn_since is None:
            has_history = bool(store.known_ids("hn"))
            hn_since = (today - timedelta(days=40)) if has_history else date.fromisoformat(
                str(settings["hn"]["backfill_from"]))
        _ingest(store, taxonomy, today, "hn", lambda: hn.collect(client, hn_since), report)

    return report


def reextract(store: Store, taxonomy: Taxonomy, today: date) -> int:
    """Re-run extraction on postings stored under an older taxonomy version."""
    count, batch = 0, []
    for raw in store.stale_extractions(taxonomy.version):
        batch.append(normalise(raw, taxonomy, today))
        if len(batch) >= 1000:
            store.update_extraction(batch, taxonomy.version)
            count += len(batch)
            batch = []
    if batch:
        store.update_extraction(batch, taxonomy.version)
        count += len(batch)
    return count
