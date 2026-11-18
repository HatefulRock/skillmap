"""Microsoft's careers site (apply.careers.microsoft.com), the JSON its own search page loads.
The site's robots.txt allows /api/pcsx, which this reads.

The search gives ten postings a page with title and place but no text, so text is a second
request, made only for new technical titles (at most `max_new_texts` a run). The site answers
"429 Too Many Requests" quickly, so cli.py reads it at one request every few seconds, and only
technical searches are run, newest first.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from ..extract import roles
from ..http import PoliteClient
from ..models import RawPosting

HOST = "https://apply.careers.microsoft.com"
QUERIES = ("software engineer", "machine learning", "data scientist", "data engineer", "AI engineer",
           "cloud solution architect", "research scientist")
PAGE = 10


def parse(job: dict, detail: dict | None = None, sector: str | None = None) -> RawPosting:
    info = {**job, **(detail or {})}
    places = info.get("standardizedLocations") or []
    first = places[0] if places else ""
    code = first.rsplit(",", 1)[-1].strip() if first else ""
    posted = info.get("postedTs") or info.get("creationTs")
    return RawPosting(
        source="microsoft",
        native_id=str(info.get("displayJobId") or info["id"]),
        company="Microsoft",
        title=info.get("name", ""),
        description=info.get("jobDescription") or "",
        url=f"{HOST}{info.get('positionUrl') or '/careers/job/' + str(info['id'])}",
        location="; ".join(info.get("locations") or []),
        country_hint=code if len(code) == 2 and code.isupper() else None,
        remote_hint=True if (info.get("workLocationOption") or "").lower() == "remote" else None,
        posted_on=datetime.fromtimestamp(posted, tz=UTC).date() if posted else None,
        sector_hint=sector,
    )


def collect(client: PoliteClient, token: str = "microsoft.com", company: str = "Microsoft", sector: str | None = None,
            region: str | None = None, known: set[str] | None = None, pages_per_query: int = 15,
            max_new_texts: int = 150) -> Iterator[RawPosting]:
    known, seen, fetched = known or set(), set(), 0
    for query in QUERIES:
        for page in range(pages_per_query):
            payload = client.get_json(f"{HOST}/api/pcsx/search", params={
                "domain": token, "query": query, "location": "", "start": page * PAGE, "sort_by": "timestamp"})
            jobs = (payload.get("data") or {}).get("positions") or []
            for job in jobs:
                job_id = str(job.get("displayJobId") or job.get("id"))
                if job_id in seen:
                    continue
                seen.add(job_id)
                detail = None
                if job_id not in known and roles.classify(job.get("name", "")):
                    if fetched >= max_new_texts:
                        continue
                    detail = client.get_json(f"{HOST}/api/pcsx/position_details", params={
                        "position_id": job["id"], "domain": token, "hl": "en"}).get("data")
                    fetched += 1
                yield parse(job, detail, sector)
            if len(jobs) < PAGE:
                break
