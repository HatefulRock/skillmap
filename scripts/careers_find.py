"""Find the careers system behind employers we only see through job boards:
python scripts/careers_find.py [LIMIT] [MIN_ADS]

For each of the busiest such employers (discover.seen_candidates), tries a few likely careers
addresses (www.NAME.com/careers, careers.NAME.com, jobs.NAME.com, and .io/.ai/.co.uk) and reports
which applicant-tracking system the page links to (Workday tenant and site, Greenhouse board, ...)
and whether it carries schema.org JobPosting markup. One page per address, stops at the first that
answers. Prints a table and suggested companies.yaml lines; stores nothing."""

from __future__ import annotations

import re
import sys
import time
from urllib.parse import urlparse

from skillmap import discover
from skillmap.http import PoliteClient

MARKERS = [
    ("workday", re.compile(r"([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)")),
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)")),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([\w.-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)")),
    ("smartrecruiters", re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([\w-]+)")),
    ("workable", re.compile(r"apply\.workable\.com/([\w-]+)")),
    ("recruitee", re.compile(r"([\w-]+)\.recruitee\.com")),
    ("personio", re.compile(r"([\w-]+)\.jobs\.personio\.(?:de|com)")),
    ("teamtailor", re.compile(r"([\w-]+)\.teamtailor\.com")),
    ("eightfold", re.compile(r"([\w-]+)\.eightfold\.ai")),
    ("oracle_hcm", re.compile(r"([\w-]+)\.fa\.[\w.-]+\.oraclecloud\.com")),
    ("successfactors", re.compile(r"(career\d*\.successfactors\.\w+)")),
    ("icims", re.compile(r"([\w-]+)\.icims\.com")),
    ("taleo", re.compile(r"([\w-]+)\.taleo\.net")),
]
JOBPOSTING = re.compile(r'"@type"\s*:\s*"JobPosting"')
SKIP = {"www", "app", "cdn", "assets", "static", "api", "boards", "jobs", "careers"}


def guesses(name: str) -> list[str]:
    stems = sorted({s for s in discover.slug_variants(name) if "-" not in s}, key=len)[:2] or discover.slug_variants(name)[:1]
    urls = []
    for stem in stems:
        for tld in ("com", "io", "ai"):
            urls += [f"https://www.{stem}.{tld}/careers", f"https://careers.{stem}.{tld}", f"https://jobs.{stem}.{tld}"]
    return urls


def detect(text: str) -> list[tuple[str, str]]:
    found = []
    for ats, pattern in MARKERS:
        for match in pattern.finditer(text):
            token = f"{match.group(1)}.{match.group(2)}/{match.group(3)}" if ats == "workday" else match.group(1)
            if token.lower() not in SKIP and (ats, token) not in found:
                found.append((ats, token))
    return found[:3]


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    min_ads = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    candidates = discover.seen_candidates(min_ads=min_ads, limit=limit)
    print(f"{len(candidates)} employers seen only through job boards ({min_ads}+ tech ads)\n")
    client = PoliteClient(min_interval=0.3, timeout=6.0, retries=0)
    deadline = time.monotonic() + 75 * 60  # the probe job stops at 90 minutes
    suggestions = []
    for c in candidates:
        if time.monotonic() > deadline:
            print("stopped at the time limit", flush=True)
            break
        result = "no careers page found"
        for url in guesses(c.name):
            try:
                page = client.request("GET", url, headers={"Accept": "text/html"})
            except Exception:  # noqa: BLE001 - a guessed address that doesn't resolve is the common case
                continue
            if page.status_code != 200 or "html" not in page.headers.get("content-type", ""):
                continue
            final = str(page.url)
            found = detect(final + " " + page.text)
            ld = bool(JOBPOSTING.search(page.text))
            result = f"{urlparse(final).netloc}{urlparse(final).path[:40]} -> " + (
                ", ".join(f"{a}:{t}" for a, t in found) or "no known system") + (" + JobPosting JSON-LD" if ld else "")
            for ats, token in found[:1]:
                suggestions.append(f"- company: {c.name}\n  ats: {ats}\n  token: {token}\n  sector: {c.sector}")
            break
        print(f"{c.name[:40]:40} {result}", flush=True)
    print("\nSuggested companies.yaml entries (check each before adding):\n")
    print("\n".join(suggestions))


if __name__ == "__main__":
    main()
