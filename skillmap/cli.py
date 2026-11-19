"""`skillmap run` — the weekly job. `skillmap aggregate` — rebuild outputs from stored data only.
`skillmap discover` — find company job boards and write config/boards.yaml.
`skillmap audit` — the monthly check of skill extraction against Claude."""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

from . import aggregate, audit, digest, discover, exports, health, heatmap, mail, vocab, watchlist
from .archive import from_env as open_archive
from .extract.skills import Taxonomy
from .http import PoliteClient
from .runner import BOARDS, Budget, archive_texts, check_archives, collect, load_boards, load_yaml, reextract
from .store import MemoryStore, PostgresStore, Store, today_utc

ALL_SOURCES = {"boards", "adzuna", "mcf", "hn", "public"}
# Supabase's free plan allows 500 MB; stored text is trimmed (oldest offline postings first) past this.
MAX_TABLE_BYTES = 420 * 1024 * 1024
HOST_INTERVALS = {"api.smartrecruiters.com": 0.4, "hn.algolia.com": 0.5,
                  "apply.careers.microsoft.com": 4.0}  # answers 429 after a few quick requests
HEALTH_DAYS = 70  # earlier runs the health check compares with (about ten weekly runs)
# Workable answers "429 Too Many Requests" to discovery at the default pace.
DISCOVER_INTERVALS = {"workable": 1.5}


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


def write_outputs(store: Store, taxonomy: Taxonomy, today: date, out: Path, runs: list[dict] | None = None,
                  readme: Path | None = None, guard: bool = False) -> tuple[dict, list[str]]:
    """Build and write every output. With `guard`, nothing is written when the new numbers fail
    `health.publish_check` against the published file; the reasons are returned instead."""
    rows = aggregate.split_soft(store.rows(), taxonomy.categories)
    profile = load_yaml("my_skills.yaml") or {}
    ratings = profile.get("ratings") or {}
    data = aggregate.build(rows, today, taxonomy.categories, taxonomy.version, ratings, runs, store.first_runs(),
                           groups=taxonomy.groups,
                           frontier=frozenset(c.lower() for c in load_yaml("frontier.yaml")["companies"]))
    out.mkdir(parents=True, exist_ok=True)
    # The weekly note compares with the previous run's file, so read it before overwriting.
    previous_path = out / "skills-market.json"
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else None
    held = health.publish_check(previous, data) if guard else []
    if held:
        return data, held
    notes = digest.add_note(digest.load_notes(out / "digest.json"), digest.build_note(data, previous))
    data["digest"] = notes[:8]
    (out / "digest.json").write_text(json.dumps(notes, indent=1, ensure_ascii=False))
    (out / "weekly.md").write_text(digest.markdown(notes[0]))
    (out / "feed.xml").write_text(digest.rss(notes))
    if readme:
        digest.update_readme(readme, notes[0])
    data["audit"] = audit.headline(out / "audit.json")
    exports.write_csvs(data, out / "csv")
    watch = watchlist.build(rows, load_boards(), profile.get("targets") or {}, ratings, today)
    (out / "watchlist.json").write_text(json.dumps(watch, indent=1, ensure_ascii=False))
    (out / "skills-market.json").write_text(json.dumps(data, separators=(",", ":"), default=_json_default))
    (out / "skills-heatmap.svg").write_text(heatmap.render(data["me"], today.isoformat()))
    # Live tech postings without description text: the census spreadsheet reads this file.
    live = [r for r in aggregate.dedupe(rows) if r["role_family"] != "non_tech" and r["source"] != "hn"
            and (today - r["last_seen"]).days <= aggregate.CURRENT_DAYS]
    with gzip.open(out / "postings-live.jsonl.gz", "wt") as fh:
        for row in sorted(live, key=lambda r: r["posting_id"]):
            fh.write(json.dumps(row, default=_json_default) + "\n")
    return data, []


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="skillmap")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="collect, store, extract and aggregate")
    run.add_argument("--sources", default=",".join(sorted(ALL_SOURCES)))
    run.add_argument("--board-limit", type=int, help="only the first N company boards (smoke tests)")
    run.add_argument("--platforms", help="only company boards on these platforms, comma-separated (e.g. workday,amazon)")
    run.add_argument("--budget", type=float, default=270.0,
                     help="minutes of collecting before the run stops reading and publishes what it has")
    run.add_argument("--hn-since", type=date.fromisoformat, help="override the HN start month")
    run.add_argument("--out", type=Path, default=Path("data"))
    run.add_argument("--readme", type=Path, help="README whose weekly section gets the new note")
    run.add_argument("--health", type=Path, help="write problems found by the run-health check here (none: no file)")
    run.add_argument("--check-archives", action="store_true",
                     help="first find postings whose archived text is missing, so collectors fetch it again")
    run.add_argument("--force-publish", action="store_true",
                     help="publish even when the new numbers differ sharply from the published ones")
    agg = sub.add_parser("aggregate", help="rebuild outputs from stored postings")
    agg.add_argument("--out", type=Path, default=Path("data"))
    agg.add_argument("--readme", type=Path, help="README whose weekly section gets the new note")
    disc = sub.add_parser("discover", help="find company job boards; writes config/boards.yaml")
    disc.add_argument("--platforms", default=",".join(discover.DEFAULT_ATS))
    disc.add_argument("--limit", type=int, help="only the first N candidate companies (smoke tests)")
    disc.add_argument("--no-yc", action="store_true", help="only config/candidates.yaml")
    disc.add_argument("--no-crawl", action="store_true", help="skip board tokens from the Common Crawl URL index")
    disc.add_argument("--interval", type=float, default=0.4, help="seconds between requests to one platform")
    disc.add_argument("--budget", type=float, default=150.0,
                      help="minutes before each platform stops probing and the boards found so far are written")
    disc.add_argument("--out", type=Path, default=discover.CONFIG / "boards.yaml")
    aud = sub.add_parser("audit", help="score the skill extraction against Claude on a sample; needs ANTHROPIC_API_KEY")
    aud.add_argument("--sample", type=int, default=audit.SAMPLE)
    aud.add_argument("--out", type=Path, default=Path("data") / "audit.json")
    voc = sub.add_parser("vocab", help="how complete the skill list is: tech terms in stored ads no skill matches")
    voc.add_argument("--sample", type=int, default=vocab.SAMPLE)
    voc.add_argument("--out", type=Path, help="also write the full report as JSON")
    em = sub.add_parser("email", help="build the weekly email from published files and send it (RESEND_API_KEY, DIGEST_TO)")
    em.add_argument("--data", type=Path, default=Path("data"))
    em.add_argument("--previous", type=Path, required=True, help="folder with last run's skills-market.json and postings")
    em.add_argument("--html", type=Path, help="also write the email here")
    purge = sub.add_parser("purge", help="delete every stored posting of one source (maintenance)")
    purge.add_argument("--source", required=True, help="the source column value, e.g. workday")
    purge.add_argument("--confirm", required=True, help="the same source name again, as a safety check")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "discover":
        run_discover(args)
        return
    if args.command == "email":
        sent = mail.run(args.data, args.previous, load_yaml("my_skills.yaml") or {}, args.html)
        print(f"{mail.subject(sent)}: {'sent ' + sent['sent'] if sent['sent'] else 'not sent'}")
        return
    taxonomy = Taxonomy.load()
    store = open_store()
    today = today_utc()

    if args.command == "purge":
        if args.confirm != args.source:
            parser.error("--confirm must repeat --source exactly")
        known = {name.split(":", 1)[0] for name in BOARDS} | {s for s in ALL_SOURCES if s != "boards"}
        if args.source not in known:
            parser.error(f"unknown source: {args.source}")
        if not os.environ.get("DATABASE_URL"):
            parser.error("purge needs DATABASE_URL")
        print(f"deleted {store.purge(args.source)} {args.source} postings")
        return

    if args.command == "audit":
        import anthropic

        report = audit.run(store, open_archive(), taxonomy, anthropic.Anthropic(), today, args.sample)
        audit.write(report, args.out)
        print(audit.summary(report))
        return

    if args.command == "vocab":
        report = vocab.run(store, open_archive(), taxonomy, today, args.sample)
        if args.out:
            args.out.write_text(json.dumps(report, indent=1, ensure_ascii=False))
        print(vocab.summary(report))
        return

    runs = None
    if args.command == "run":
        sources = {s.strip() for s in args.sources.split(",") if s.strip()}
        unknown = sources - ALL_SOURCES
        if unknown:
            parser.error(f"unknown sources: {', '.join(sorted(unknown))}")
        history = store.run_history(HEALTH_DAYS)  # before this run logs its own rows
        checked = open_archive() if args.check_archives and os.environ.get("DATABASE_URL") else None
        if checked is not None:
            logging.info("cleared %d pointers to missing archived text", check_archives(store, checked))
        client = PoliteClient(min_interval=1.0, per_host_interval=HOST_INTERVALS)
        try:
            platforms = {p.strip() for p in (args.platforms or "").split(",") if p.strip()} or None
            budget = Budget(time.monotonic() + args.budget * 60)
            report = collect(store, client, taxonomy, today, sources, args.board_limit, args.hn_since, platforms, budget)
        finally:
            client.close()
        print(report.summary())
        runs = report.as_dicts()
        current = [{"source": r.name, "fetched": r.fetched, "error": r.error} for r in report.results]
        # Platform totals are only comparable when this run read everything.
        full = sources == ALL_SOURCES and not args.board_limit and not platforms
        problems = health.check(current, history, compare_totals=full)
        for problem in problems:
            logging.warning("health: %s", problem)
        if args.health:
            args.health.unlink(missing_ok=True)
            args.health.with_suffix(".ok").unlink(missing_ok=True)
        archive = open_archive() if os.environ.get("DATABASE_URL") else None
        refreshed = reextract(store, taxonomy, today, archive)
        if refreshed:
            logging.info("re-extracted %d postings for taxonomy v%d", refreshed, taxonomy.version)
        if archive is not None:
            moved = archive_texts(store, archive, today)
            logging.info("moved the text of %d postings to file storage", moved)
        else:
            logging.warning("SUPABASE_SECRET_KEY not set: posting text stays in the database")
        trimmed = store.trim_text(MAX_TABLE_BYTES)
        if trimmed:
            logging.info("dropped stored text of %d long-closed postings to stay under the size limit", trimmed)

    guard = args.command == "run" and not args.force_publish
    data, held = write_outputs(store, taxonomy, today, args.out, runs, readme=args.readme, guard=guard)
    for reason in held:
        logging.warning("not published: %s", reason)
    if args.command == "run" and args.health:
        if problems or held:
            args.health.write_text(health.markdown(problems, os.environ.get("RUN_URL"), held))
        elif full:
            args.health.with_suffix(".ok").write_text("")
    totals = data["meta"]["totals"]
    print(f"postings {totals['postings']} · tech {totals['tech']} · live now {data['current']['n']}")


def run_discover(args: argparse.Namespace) -> None:
    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    unknown = set(platforms) - set(discover.ATS)
    if unknown:
        raise SystemExit(f"unknown platforms: {', '.join(sorted(unknown))}")
    # The crawl index asks for gentle use and answers 503 when busy.
    client = PoliteClient(min_interval=0.5, per_host_interval={"index.commoncrawl.org": 2.0}, retries=3,
                          max_retry_after=60.0)
    # Boards the named companies will find again anyway; crawl-found ones are re-probed each time,
    # since boards.yaml is rewritten from this run's results.
    known = {(b["ats"], str(b["token"]).lower()) for b in load_boards()
             if b.get("ats") and b.get("token") and b.get("origin") != "commoncrawl"}
    try:
        candidates = discover.load_candidates(client, include_yc=not args.no_yc,
                                              crawl_platforms=[] if args.no_crawl else platforms,
                                              known=known)[: args.limit]
    finally:
        client.close()
    logging.info("probing %d companies on %s", len(candidates), ", ".join(platforms))
    deadline = time.monotonic() + args.budget * 60

    def make_client(ats: str) -> PoliteClient:
        interval = max(args.interval, DISCOVER_INTERVALS.get(ats, 0.0))
        return PoliteClient(min_interval=interval, retries=2, max_retry_after=30.0)

    boards, unfinished = discover.discover(candidates, platforms, make_client, deadline)
    if unfinished:
        boards = discover.carry_over(boards, discover.load_previous(args.out), unfinished)
    discover.write_boards(boards, args.out, today_utc(), len(candidates), unfinished)
    jobs = sum(b.jobs for b in boards)
    print(f"{len(boards)} boards with {jobs} open jobs, from {len(candidates)} companies → {args.out}")
    if unfinished:
        print(f"  cut short (time or rate limit), earlier boards kept: {', '.join(unfinished)}")
    for ats in discover.ATS:
        found = [b for b in boards if b.ats == ats]
        print(f"  {ats:<16} {len(found):>5} boards {sum(b.jobs for b in found):>7} jobs")


if __name__ == "__main__":
    main()
