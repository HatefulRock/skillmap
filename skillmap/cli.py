"""`skillmap run` — the daily job. `skillmap aggregate` — rebuild outputs from stored data only."""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

from . import aggregate, heatmap
from .extract.skills import Taxonomy
from .http import PoliteClient
from .runner import collect, load_yaml, reextract
from .store import MemoryStore, PostgresStore, Store, today_utc

ALL_SOURCES = {"boards", "adzuna", "mcf", "hn"}


def open_store() -> Store:
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        return PostgresStore(dsn)
    logging.warning("DATABASE_URL not set: using an in-memory store (nothing is kept after this run)")
    return MemoryStore()


def _json_default(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):  # numeric columns from Postgres
        return float(value)
    raise TypeError(type(value))


def write_outputs(store: Store, taxonomy: Taxonomy, today: date, out: Path, runs: list[dict] | None = None) -> dict:
    rows = store.rows()
    ratings = (load_yaml("my_skills.yaml") or {}).get("ratings") or {}
    data = aggregate.build(rows, today, taxonomy.categories, taxonomy.version, ratings, runs)
    out.mkdir(parents=True, exist_ok=True)
    (out / "skills-market.json").write_text(json.dumps(data, separators=(",", ":"), default=_json_default))
    (out / "skills-heatmap.svg").write_text(heatmap.render(data["me"], today.isoformat()))
    # Live tech postings without description text: the census spreadsheet reads this file.
    live = [r for r in aggregate.dedupe(rows) if r["role_family"] != "non_tech" and r["source"] != "hn"
            and (today - r["last_seen"]).days <= aggregate.CURRENT_DAYS]
    with gzip.open(out / "postings-live.jsonl.gz", "wt") as fh:
        for row in sorted(live, key=lambda r: r["posting_id"]):
            fh.write(json.dumps(row, default=_json_default) + "\n")
    return data


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="skillmap")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="collect, store, extract and aggregate")
    run.add_argument("--sources", default=",".join(sorted(ALL_SOURCES)))
    run.add_argument("--board-limit", type=int, help="only the first N company boards (smoke tests)")
    run.add_argument("--hn-since", type=date.fromisoformat, help="override the HN start month")
    run.add_argument("--out", type=Path, default=Path("data"))
    agg = sub.add_parser("aggregate", help="rebuild outputs from stored postings")
    agg.add_argument("--out", type=Path, default=Path("data"))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    taxonomy = Taxonomy.load()
    store = open_store()
    today = today_utc()

    runs = None
    if args.command == "run":
        sources = {s.strip() for s in args.sources.split(",") if s.strip()}
        unknown = sources - ALL_SOURCES
        if unknown:
            parser.error(f"unknown sources: {', '.join(sorted(unknown))}")
        client = PoliteClient(min_interval=1.0)
        try:
            report = collect(store, client, taxonomy, today, sources, args.board_limit, args.hn_since)
        finally:
            client.close()
        print(report.summary())
        runs = report.as_dicts()
        refreshed = reextract(store, taxonomy, today)
        if refreshed:
            logging.info("re-extracted %d postings for taxonomy v%d", refreshed, taxonomy.version)

    data = write_outputs(store, taxonomy, today, args.out, runs)
    totals = data["meta"]["totals"]
    print(f"postings {totals['postings']} · tech {totals['tech']} · live now {data['current']['n']}")


if __name__ == "__main__":
    main()
