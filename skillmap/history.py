"""Job ads from years back, out of Common Crawl's archive of company job boards.

Hacker News "Who is hiring?" posts go back to 2011, but they are short and lean to startups. The
live collectors only started in November 2026. Common Crawl has been saving public web pages
since 2008, including the Greenhouse and Lever job pages companies post their ads on, with the
full ad text. So for one crawl per half-year this reads the crawl's URL index for those job pages,
samples up to SAMPLE tech ads (at most PER_COMPANY per company, so a big employer can't fill a
half-year), fetches each page's archived copy with a byte-range request, and extracts the same
fields as the live pipeline.

Results go to data/history-ads.jsonl.gz (fields only, no text) and data/history-state.json (which
crawls are done, under which taxonomy version). The text goes to the private archive bucket, so a
taxonomy change re-extracts it without fetching again. Each run does what its time budget allows
and the next one carries on, so a rate limit or an outage only delays the backfill.

Limits, stated on the page: the sample is whatever companies' boards each crawl happened to
capture (more in recent years, and only companies that use Greenhouse or Lever), not a fixed
panel; the ads are counted when captured, not when posted.
"""

from __future__ import annotations

import gzip
import json
import logging
import random
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from .archive import Archive, pack, unpack
from .extract import roles, seniority
from .extract.skills import Taxonomy
from .extract.text import to_plain
from .http import PoliteClient

log = logging.getLogger(__name__)

COLLINFO = "https://index.commoncrawl.org/collinfo.json"
DATA = "https://data.commoncrawl.org/"
FIRST_YEAR = 2015  # Greenhouse and Lever pages are too few in earlier crawls
SAMPLE = 400  # tech ads per half-year
PER_COMPANY = 8
MAX_FETCHES = 1000  # page fetches per crawl, since many pages turn out not to be tech roles
INDEX_PAGES = 12  # index blocks (3,000 captures each) read per host, spread across the alphabet of board names
EMPTY_TRIES = 3  # runs a crawl may come back empty before it counts as done with no ads
MIN_PERIOD = 100  # a half-year with fewer tech ads gets no shares
INTERVALS = {"index.commoncrawl.org": 2.0, "data.commoncrawl.org": 0.3}

# (host, path pattern): the job page of one ad, with the board token and the job's id.
JOB_PAGES = {
    "boards.greenhouse.io": re.compile(r"^/([A-Za-z0-9_.-]+)/jobs/(\d+)/?$"),
    "job-boards.greenhouse.io": re.compile(r"^/([A-Za-z0-9_.-]+)/jobs/(\d+)/?$"),
    "jobs.lever.co": re.compile(r"^/([A-Za-z0-9_.-]+)/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?$"),
}
_SCRIPT = re.compile(r"<(script|style|noscript|head|svg|form)\b.*?</\1\s*>", re.I | re.S)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


@dataclass(frozen=True)
class Crawl:
    id: str  # CC-MAIN-2019-22
    cdx: str
    period: str  # 2019-01 or 2019-07: the half-year it stands for

    @property
    def day(self) -> date:
        year, week = map(int, self.id.split("-")[2:4])
        return date.fromisocalendar(year, max(1, min(week, 52)), 1)


def half_year(day: date) -> str:
    return f"{day.year}-{'01' if day.month <= 6 else '07'}"


def pick_crawls(collinfo: list[dict], today: date) -> list[Crawl]:
    """The last crawl of each half-year from FIRST_YEAR on, newest first; the running half-year is
    left out until it ends (the live data covers it)."""
    by_period: dict[str, Crawl] = {}
    for entry in collinfo:
        match = re.fullmatch(r"CC-MAIN-(\d{4})-(\d{2})", entry.get("id", ""))
        if not match or int(match.group(1)) < FIRST_YEAR:
            continue
        crawl = Crawl(entry["id"], entry["cdx-api"], "")
        period = half_year(crawl.day)
        if period == half_year(today):
            continue
        if period not in by_period or crawl.day > by_period[period].day:
            by_period[period] = Crawl(crawl.id, crawl.cdx, period)
    return sorted(by_period.values(), key=lambda c: c.period, reverse=True)


def job_key(url: str) -> tuple[str, str, str] | None:
    """(host, board token, job id) for a job page URL, else None (board index, apply form, embed)."""
    parsed = urlparse(url)
    host = parsed.netloc.lower().split(":")[0]
    pattern = JOB_PAGES.get(host)
    match = pattern.match(parsed.path) if pattern else None
    if not match or match.group(1).lower() in {"embed", "jobs", "api"}:
        return None
    return host, match.group(1).lower(), match.group(2)


def surt_prefix(host: str) -> str:
    """The index's sort key for a host's pages: boards.greenhouse.io -> io,greenhouse,boards)/"""
    return ",".join(reversed(host.split("."))) + ")/"


def _range(client: PoliteClient, url: str, start: int, end: int) -> bytes:
    response = client.request("GET", url, headers={"Range": f"bytes={start}-{end}"})
    if response.status_code not in (200, 206):
        raise RuntimeError(f"HTTP {response.status_code} for {url}")
    return response.content


def _size(client: PoliteClient, url: str) -> int:
    response = client.request("GET", url, headers={"Range": "bytes=0-0"})
    total = response.headers.get("Content-Range", "").rpartition("/")[2]
    if response.status_code != 206 or not total.isdigit():
        raise RuntimeError(f"no size for {url} (HTTP {response.status_code})")
    return int(total)


def _first_key_after(client: PoliteClient, url: str, pos: int, size: int) -> tuple[str, int]:
    """The sort key of the first whole line starting after byte `pos`, and where it starts."""
    chunk = _range(client, url, pos, min(size - 1, pos + 8191))
    cut = 0 if pos == 0 else chunk.find(b"\n") + 1
    line = chunk[cut:].split(b"\n", 1)[0].decode("utf-8", "replace")
    return line.split(" ", 1)[0], pos + cut


def cluster_blocks(client: PoliteClient, crawl: Crawl, prefix: str) -> list[tuple[str, int, int]]:
    """Index blocks (file, offset, length) holding captures whose key starts with `prefix`.

    Each crawl's index is ~300 sorted, gzip'd files, and cluster.idx names the first key of
    every block of 3,000 lines. A binary search over cluster.idx with byte-range reads finds the
    blocks for one host without the index server, which times out under load."""
    url = f"{DATA}cc-index/collections/{crawl.id}/indexes/cluster.idx"
    size = _size(client, url)
    lo, hi = 0, size  # find the last line whose key sorts before the prefix
    while hi - lo > 16384:
        mid = (lo + hi) // 2
        key, _ = _first_key_after(client, url, mid, size)
        if key < prefix:
            lo = mid
        else:
            hi = mid
    blocks: list[tuple[str, int, int]] = []
    pos, carry = lo, b""
    while pos < size:
        chunk = carry + _range(client, url, pos, min(size - 1, pos + 1_048_575))
        pos += 1_048_576
        lines = chunk.split(b"\n")
        carry = lines.pop()
        for raw in lines:
            fields = raw.decode("utf-8", "replace").split("\t")
            if len(fields) < 4:
                continue
            key = fields[0].split(" ", 1)[0]
            if key < prefix:  # the block before the prefix starts can still hold its first captures
                blocks = [(fields[1], int(fields[2]), int(fields[3]))]
                continue
            if not key.startswith(prefix):
                return blocks
            blocks.append((fields[1], int(fields[2]), int(fields[3])))
    return blocks


def index_lines(client: PoliteClient, crawl: Crawl, host: str, pages: int = INDEX_PAGES) -> list[dict]:
    """Successful HTML captures under `host` in this crawl, from up to `pages` index blocks spread
    evenly over the host's (the index is sorted by URL, so the first blocks would be boards a-c)."""
    prefix = surt_prefix(host)
    blocks = cluster_blocks(client, crawl, prefix)
    if not blocks:
        return []
    chosen = range(len(blocks)) if len(blocks) <= pages else sorted({int(i * len(blocks) / pages) for i in range(pages)})
    out = []
    for i in chosen:
        name, offset, length = blocks[i]
        try:
            raw = gzip.decompress(_range(client, f"{DATA}cc-index/collections/{crawl.id}/indexes/{name}",
                                         offset, offset + length - 1))
        except Exception as exc:  # noqa: BLE001 - a missing block only shrinks the sample
            log.warning("history %s %s block %s:%d: %s", crawl.id, host, name, offset, exc)
            continue
        for line in raw.decode("utf-8", "replace").splitlines():
            key, _, rest = line.partition(" ")
            if not key.startswith(prefix):
                continue
            try:
                cap = json.loads(rest.split(" ", 1)[1])
            except (IndexError, ValueError):
                continue
            if cap.get("status") == "200" and "html" in cap.get("mime", "") + cap.get("mime-detected", ""):
                cap["timestamp"] = rest.split(" ", 1)[0]
                out.append(cap)
    return out


def sample(captures: list[dict], seed: str) -> list[dict]:
    """One capture per job, shuffled with a fixed seed; the per-company cap is applied while
    fetching, once a page turns out to be a tech ad."""
    jobs: dict[tuple[str, str, str], dict] = {}
    for cap in captures:
        key = job_key(cap.get("url", ""))
        if key and key not in jobs:
            jobs[key] = {**cap, "key": key}
    out = sorted(jobs.values(), key=lambda c: c["key"])
    random.Random(seed).shuffle(out)
    return out


def parse_warc(record: bytes) -> str | None:
    """The HTML body of one gzip'd WARC response record."""
    raw = gzip.decompress(record)
    parts = raw.split(b"\r\n\r\n", 2)
    if len(parts) < 3 or b"WARC-Type: response" not in parts[0]:
        return None
    status = parts[1].split(b"\r\n", 1)[0]
    if b" 200" not in status:
        return None
    charset = re.search(rb"charset=([\w-]+)", parts[1], re.I)
    try:
        return parts[2].decode(charset.group(1).decode() if charset else "utf-8", errors="replace")
    except LookupError:
        return parts[2].decode("utf-8", errors="replace")


def page_title(html: str, host: str) -> str:
    """The job title from the page title: "Job Application for X at Y" (Greenhouse), "Y - X" (Lever)."""
    match = _TITLE.search(html)
    title = to_plain(match.group(1)) if match else ""
    if "greenhouse" in host:
        title = re.sub(r"^\s*Job Application for\s+", "", title)
        title = re.sub(r"\s+at\s+[^@]+$", "", title)
    elif " - " in title:
        title = title.split(" - ", 1)[1]
    return title.strip()


def page_text(html: str) -> str:
    return to_plain(_SCRIPT.sub(" ", html))


def fetch(client: PoliteClient, cap: dict) -> str | None:
    start = int(cap["offset"])
    end = start + int(cap["length"]) - 1
    response = client.request("GET", DATA + cap["filename"], headers={"Range": f"bytes={start}-{end}"})
    if response.status_code not in (200, 206):
        raise RuntimeError(f"HTTP {response.status_code}")
    return parse_warc(response.content)


def row_for(crawl: Crawl, cap: dict, title: str, text: str, taxonomy: Taxonomy) -> dict | None:
    family = roles.classify(title)
    if family is None or family == "non_tech":
        return None
    host, token, job = cap["key"]
    skills, nice = taxonomy.extract(title, text)
    stamp = cap.get("timestamp", "")
    return {
        "id": f"{host.split('.')[-2]}:{token}:{job}", "crawl": crawl.id, "period": crawl.period,
        "captured": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}" if len(stamp) >= 8 else None,
        "company": token, "title": title, "role_family": family,
        "seniority": seniority.classify(title, text), "skills": skills, "skills_nice": nice,
        "url": cap.get("url"),
    }


def collect_crawl(client: PoliteClient, crawl: Crawl, taxonomy: Taxonomy, deadline: float,
                  clock: Callable[[], float] = time.monotonic) -> tuple[list[dict], list[tuple[str, str]], bool]:
    """(rows, (id, text) pairs, finished). Not finished when the deadline came first."""
    captures = []
    for host in JOB_PAGES:
        try:
            captures += index_lines(client, crawl, host)
        except Exception as exc:  # noqa: BLE001 - one host down still leaves the others
            log.warning("history %s %s: %s", crawl.id, host, exc)
    rows: list[dict] = []
    texts: list[tuple[str, str]] = []
    per_company: Counter = Counter()
    fetched = failed = 0
    for cap in sample(captures, crawl.id):
        if len(rows) >= SAMPLE or fetched >= MAX_FETCHES:
            break
        if clock() > deadline:
            return rows, texts, False
        if per_company[cap["key"][1]] >= PER_COMPANY:
            continue
        fetched += 1
        try:
            html = fetch(client, cap)
        except Exception as exc:  # noqa: BLE001 - skip one page, stop if the archive is down
            failed += 1
            if failed >= 25 and failed > fetched / 2:
                raise RuntimeError(f"most fetches failing ({exc})") from exc
            continue
        if not html:
            continue
        title, text = page_title(html, cap["key"][0]), page_text(html)
        row = row_for(crawl, cap, title, text, taxonomy)
        if row is None:
            continue
        per_company[row["company"]] += 1
        rows.append(row)
        texts.append((row["id"], f"{title}\n{text}"))
    log.info("history %s (%s): %d captures, %d fetched, %d tech ads", crawl.id, crawl.period, len(captures),
             fetched, len(rows))
    return rows, texts, True


# Files -------------------------------------------------------------------------------------------

def load_rows(path: Path) -> list[dict]:
    try:
        with gzip.open(path, "rt") as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except (FileNotFoundError, OSError, ValueError):
        return []


def save_rows(path: Path, rows: list[dict]) -> None:
    with gzip.open(path, "wt") as fh:
        for row in sorted(rows, key=lambda r: (r["period"], r["id"])):
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return {"taxonomy_version": None, "crawls": {}}


def reextract(rows: list[dict], state: dict, archive: Archive | None, taxonomy: Taxonomy) -> int:
    """Re-extract stored ads after a taxonomy change, from the archived text. Crawls whose text
    can't be read keep their old skills and their old version, so a later run tries again."""
    if archive is None:
        return 0
    done = 0
    by_crawl = defaultdict(list)
    for row in rows:
        by_crawl[row["crawl"]].append(row)
    for crawl_id, info in state["crawls"].items():
        if info.get("taxonomy_version") == taxonomy.version or not info.get("archive"):
            continue
        try:
            texts = unpack(archive.get(info["archive"]))
        except Exception as exc:  # noqa: BLE001
            log.warning("history: text of %s unavailable: %s", crawl_id, exc)
            continue
        for row in by_crawl.get(crawl_id, []):
            text = texts.get(row["id"])
            if text is not None:
                title, _, body = text.partition("\n")
                row["skills"], row["skills_nice"] = taxonomy.extract(title, body)
                done += 1
        info["taxonomy_version"] = taxonomy.version
    return done


def run(client: PoliteClient, taxonomy: Taxonomy, out: Path, archive: Archive | None, today: date,
        budget_minutes: float, clock: Callable[[], float] = time.monotonic) -> dict:
    """Backfill crawls not done yet, newest half-years first, until the budget runs out."""
    deadline = clock() + budget_minutes * 60
    rows_path, state_path = out / "history-ads.jsonl.gz", out / "history-state.json"
    rows, state = load_rows(rows_path), load_state(state_path)
    state.setdefault("crawls", {})
    # A crawl that gave no ads (the index was down, say) is only settled after EMPTY_TRIES runs.
    state["crawls"] = {k: v for k, v in state["crawls"].items() if v.get("ads") or v.get("tries", 0) >= EMPTY_TRIES}
    tries: dict[str, int] = dict(state.get("empty") or {})
    redone = reextract(rows, state, archive, taxonomy)
    try:
        crawls = pick_crawls(client.get_json(COLLINFO), today)
    except Exception as exc:  # noqa: BLE001 - keep any re-extraction; the next run tries again
        log.warning("history: crawl list unavailable: %s", exc)
        crawls = []
    added = 0
    for crawl in crawls:
        if crawl.id in state["crawls"] or clock() > deadline:
            continue
        try:
            new, texts, finished = collect_crawl(client, crawl, taxonomy, deadline, clock)
        except Exception as exc:  # noqa: BLE001 - the next run tries this crawl again
            log.warning("history %s: %s", crawl.id, exc)
            continue
        if not finished:
            log.info("history: out of time during %s; the next run redoes it", crawl.id)
            break
        name = None
        if archive is not None and texts:
            name = f"history/{crawl.id}-{today.isoformat()}.jsonl.gz"
            try:
                archive.put(name, pack(texts))
            except Exception as exc:  # noqa: BLE001 - fields are kept; only re-extraction loses this crawl
                log.warning("history: could not archive %s: %s", crawl.id, exc)
                name = None
        rows = [r for r in rows if r["crawl"] != crawl.id] + new
        if not new and tries.get(crawl.id, 0) + 1 < EMPTY_TRIES:
            log.warning("history %s: no ads; the next run tries again", crawl.id)
            state.setdefault("empty", {})[crawl.id] = tries.get(crawl.id, 0) + 1
            state_path.write_text(json.dumps(state, indent=1, sort_keys=True))
            continue
        state["crawls"][crawl.id] = {"period": crawl.period, "ads": len(new), "archive": name,
                                     "taxonomy_version": taxonomy.version, "done": today.isoformat()}
        if not new:
            state["crawls"][crawl.id]["tries"] = tries.get(crawl.id, 0) + 1
        state.get("empty", {}).pop(crawl.id, None)
        added += 1
        # Saved after every crawl, so a cancelled run keeps what it finished.
        save_rows(rows_path, rows)
        state_path.write_text(json.dumps(state, indent=1, sort_keys=True))
    if redone:
        save_rows(rows_path, rows)
        state_path.write_text(json.dumps(state, indent=1, sort_keys=True))
    left = sum(1 for c in crawls if c.id not in state["crawls"])
    return {"crawls_added": added, "reextracted": redone, "crawls_left": left, "ads": len(rows)}


# The series the site draws -----------------------------------------------------------------------

def series(rows: list[dict], skills: list[str]) -> dict:
    """Share of each half-year's sampled tech ads naming each skill, like the HN series; with the
    number of ads and of companies behind each point. Thin half-years get no shares."""
    periods = sorted({r["period"] for r in rows})
    out = {"months": periods, "n": [], "companies": [], "shares": {s: [] for s in skills}}
    by_period = defaultdict(list)
    for row in rows:
        by_period[row["period"]].append(row)
    for period in periods:
        group = by_period[period]
        out["n"].append(len(group))
        out["companies"].append(len({r["company"] for r in group}))
        counts = Counter(s for r in group for s in set(r["skills"]))
        for skill in skills:
            out["shares"][skill].append(round(counts[skill] / len(group), 4) if len(group) >= MIN_PERIOD else None)
    return out

