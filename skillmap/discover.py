"""Find company job boards at scale.

Greenhouse, Lever, Ashby, Workable and SmartRecruiters each publish a company's open jobs at a
predictable address once you know the company's board name ("token"). Discovery takes a long
list of companies (every active Y Combinator company that is hiring or has 25+ people, plus
config/candidates.yaml), derives likely tokens from each name and website, asks each platform
whether that board exists, and keeps every board with open jobs. Each platform is probed in its
own thread with its own polite client, so the platforms are asked in parallel but each one only
sees one request at a time.

The result goes to config/boards.yaml, which the weekly run reads alongside the hand-curated
config/companies.yaml (the curated entry wins when both name a company).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml

from .http import PoliteClient
from .models import normalise_company

log = logging.getLogger("skillmap.discover")
CONFIG = Path(__file__).resolve().parents[1] / "config"
YC_URL = "https://raw.githubusercontent.com/yc-oss/api/main/companies/all.json"
ATS = ["greenhouse", "lever", "ashby", "workable", "smartrecruiters"]
# SmartRecruiters' API host asks crawlers to stay away in its robots.txt, so it is only probed
# when asked for explicitly (`--platforms ...,smartrecruiters`).
DEFAULT_ATS = ["greenhouse", "lever", "ashby", "workable"]
_DROP_WORDS = re.compile(r"\b(?:ai|labs?|inc|hq|technologies|technology|tech|app|io|the|co|group|holdings|global)\b")


@dataclass
class Candidate:
    name: str
    sector: str
    origin: str  # yc | curated
    website: str | None = None
    tokens: list[str] = field(default_factory=list)


@dataclass
class Board:
    company: str
    ats: str
    token: str
    sector: str
    jobs: int
    origin: str


def slug_variants(name: str, website: str | None = None, extra: Iterable[str] = ()) -> list[str]:
    """Likely board tokens, most likely first: "Scale AI" → scaleai, scale-ai, scale."""
    base = name.lower().replace("&", "and").replace("'", "").replace("’", "")
    compact = re.sub(r"[^a-z0-9]", "", base)
    hyphen = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    short = re.sub(r"[^a-z0-9]", "", _DROP_WORDS.sub(" ", base.replace(".", " ")))
    stem = ""
    if website:
        host = urlparse(website if "//" in website else f"https://{website}").netloc.lower().removeprefix("www.")
        stem = re.sub(r"[^a-z0-9-]", "", host.split(".")[0])
    out = [*extra, compact, hyphen, stem, short]
    seen: list[str] = []
    for token in out:
        if token and token not in seen and (len(token) >= 4 or token in extra):
            seen.append(token)
    return seen[:4]


def _yc_sector(company: dict) -> str:
    labels = {t.lower() for t in [*(company.get("tags") or []), *(company.get("industries") or [])]}
    if labels & {"crypto / web3", "web3", "crypto", "blockchain", "defi"}:
        return "crypto"
    if labels & {"fintech", "payments", "banking and exchange", "finance", "insurance", "lending and loan management",
                 "asset management", "trading"}:
        return "fintech"
    if labels & {"ai", "generative ai", "machine learning", "artificial intelligence", "ai assistant", "llm",
                 "computer vision", "nlp", "deep learning"}:
        return "ai"
    return "tech"


def yc_candidates(companies: list[dict]) -> list[Candidate]:
    return [
        Candidate(c["name"], _yc_sector(c), "yc", c.get("website"), [c["slug"]] if c.get("slug") else [])
        for c in companies
        if c.get("status") in ("Active", "Public") and (c.get("isHiring") or (c.get("team_size") or 0) >= 25)
    ]


def curated_candidates(config: dict) -> list[Candidate]:
    out = []
    for sector, entries in (config or {}).items():
        for entry in entries or []:
            if isinstance(entry, str):
                out.append(Candidate(entry, sector, "curated"))
            else:
                out.append(Candidate(entry["name"], sector, "curated", entry.get("website"), entry.get("tokens") or []))
    return out


def same_company(found: str | None, wanted: str) -> bool:
    """A board that names its company must name roughly the one we asked about."""
    if not found:
        return True
    a, b = normalise_company(found).replace(" ", ""), normalise_company(wanted).replace(" ", "")
    if a and b and (a in b or b in a):
        return True
    words = {w for w in normalise_company(found).split() if len(w) >= 4}
    return bool(words & {w for w in normalise_company(wanted).split() if len(w) >= 4})


# Each probe returns (open jobs, company name if the platform states one), or None for no board.
Probe = Callable[[PoliteClient, str], "tuple[int, str | None] | None"]


def _get(client: PoliteClient, url: str, **kwargs) -> object | None:
    response = client.request("GET", url, **kwargs)
    if response.status_code in (400, 401, 403, 404, 410, 422):
        return None
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return None


def probe_greenhouse(client: PoliteClient, token: str) -> tuple[int, str | None] | None:
    board = _get(client, f"https://boards-api.greenhouse.io/v1/boards/{token}")
    if not isinstance(board, dict):
        return None
    jobs = _get(client, f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
    return len((jobs or {}).get("jobs") or []) if isinstance(jobs, dict) else 0, board.get("name")


def probe_lever(client: PoliteClient, token: str) -> tuple[int, str | None] | None:
    jobs = _get(client, f"https://api.lever.co/v0/postings/{token}", params={"mode": "json", "limit": 500})
    return (len(jobs), None) if isinstance(jobs, list) else None


def probe_ashby(client: PoliteClient, token: str) -> tuple[int, str | None] | None:
    board = _get(client, f"https://api.ashbyhq.com/posting-api/job-board/{token}")
    if not isinstance(board, dict) or "jobs" not in board:
        return None
    return sum(1 for job in board["jobs"] if job.get("isListed") is not False), None


def probe_workable(client: PoliteClient, token: str) -> tuple[int, str | None] | None:
    board = _get(client, f"https://apply.workable.com/api/v1/widget/accounts/{token}")
    if not isinstance(board, dict) or "jobs" not in board:
        return None
    return len(board["jobs"]), board.get("name")


def probe_smartrecruiters(client: PoliteClient, token: str) -> tuple[int, str | None] | None:
    page = _get(client, f"https://api.smartrecruiters.com/v1/companies/{token}/postings", params={"limit": 1})
    if not isinstance(page, dict) or not page.get("totalFound"):
        return None  # unknown companies answer 200 with nothing in them
    first = (page.get("content") or [{}])[0]
    return int(page["totalFound"]), (first.get("company") or {}).get("name")


PROBES: dict[str, Probe] = {
    "greenhouse": probe_greenhouse, "lever": probe_lever, "ashby": probe_ashby, "workable": probe_workable,
    "smartrecruiters": probe_smartrecruiters,
}


def scan(ats: str, candidates: list[Candidate], client: PoliteClient) -> list[Board]:
    """Every candidate on one platform: the first token variant that answers wins."""
    probe, found = PROBES[ats], []
    for i, candidate in enumerate(candidates):
        for token in slug_variants(candidate.name, candidate.website, candidate.tokens):
            try:
                hit = probe(client, token)
            except (httpx.HTTPError, RuntimeError) as exc:
                log.debug("%s %s: %s", ats, token, exc)
                continue
            if hit and hit[0] > 0 and same_company(hit[1], candidate.name):
                found.append(Board(candidate.name, ats, token, candidate.sector, hit[0], candidate.origin))
                break
        if i and i % 250 == 0:
            log.info("%s: %d/%d candidates, %d boards", ats, i, len(candidates), len(found))
    return found


def choose(boards: list[Board]) -> list[Board]:
    """One board per company (the busiest: companies that moved platform leave stale boards),
    and each board once even if two candidate names led to it."""
    best: dict[str, Board] = {}
    for board in boards:
        key = normalise_company(board.company)
        if key not in best or board.jobs > best[key].jobs:
            best[key] = board
    seen, out = set(), []
    for board in sorted(best.values(), key=lambda b: (-b.jobs, b.company)):
        if (board.ats, board.token) not in seen:
            seen.add((board.ats, board.token))
            out.append(board)
    return sorted(out, key=lambda b: b.company.lower())


def discover(candidates: list[Candidate], platforms: list[str], make_client: Callable[[], PoliteClient]) -> list[Board]:
    def run(ats: str) -> list[Board]:
        client = make_client()
        try:
            return scan(ats, candidates, client)
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=len(platforms)) as pool:
        results = list(pool.map(run, platforms))
    return choose([board for found in results for board in found])


def load_candidates(client: PoliteClient, include_yc: bool = True) -> list[Candidate]:
    curated = curated_candidates(yaml.safe_load((CONFIG / "candidates.yaml").read_text()))
    yc = yc_candidates(client.get_json(YC_URL)) if include_yc else []
    # Curated names first: they carry a hand-picked sector, and win a name clash.
    out, seen = [], set()
    for candidate in [*curated, *yc]:
        key = normalise_company(candidate.name)
        if key and key not in seen:
            seen.add(key)
            out.append(candidate)
    return out


def write_boards(boards: list[Board], path: Path, today: date, n_candidates: int) -> None:
    by_ats = {ats: sum(1 for b in boards if b.ats == ats) for ats in ATS}
    header = (
        "# Generated by `skillmap discover` — do not edit by hand (add names to candidates.yaml instead).\n"
        f"# {len(boards)} boards with open jobs, found among {n_candidates} companies on {today.isoformat()}.\n"
        f"# {', '.join(f'{ats} {n}' for ats, n in by_ats.items())}.\n"
    )
    body = yaml.safe_dump(
        {"generated": today.isoformat(), "boards": [vars(b) for b in boards]}, sort_keys=False, allow_unicode=True
    )
    path.write_text(header + body)
