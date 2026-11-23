"""Eightfold careers sites (`/api/apply/v2/jobs`), the public JSON the careers pages load.
Used by Netflix among others; robots.txt there allows /api/apply.

The token is "host|domain", e.g. "explore.jobs.netflix.net|netflix.com". The list gives title,
place and date but no text, so text is a second request, made only for new technical titles
(at most `max_new_texts` a run).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from ..extract import roles
from ..http import PoliteClient
from ..models import RawPosting

PAGE = 10


def split_token(token: str) -> tuple[str, str]:
    host, _, domain = token.partition("|")
    return host, domain or host


def parse(job: dict, detail: dict | None, host: str, company: str, sector: str | None = None) -> RawPosting:
    info = {**job, **(detail or {})}
    created = info.get("t_create")
    places = info.get("locations") or [info.get("location") or ""]
    option = str(info.get("work_location_option") or info.get("location_flexibility") or "").lower()
    return RawPosting(
        source="eightfold",
        native_id=f"{host.split('.')[-2]}:{info.get('id')}",
        company=company,
        title=info.get("name") or info.get("posting_name") or "",
        description=info.get("job_description") or "",
        url=info.get("canonicalPositionUrl") or f"https://{host}/careers/job/{info.get('id')}",
        location="; ".join(str(p) for p in places[:3] if p),
        remote_hint=True if "remote" in option else None,
        posted_on=datetime.fromtimestamp(created, UTC).date() if isinstance(created, (int, float)) and created else None,
        sector_hint=sector,
    )


def collect(client: PoliteClient, token: str, company: str, sector: str | None = None, region: str | None = None,
            known: set[str] | None = None, max_postings: int = 3000, max_new_texts: int = 200) -> Iterator[RawPosting]:
    host, domain = split_token(token)
    base = f"https://{host}/api/apply/v2/jobs"
    known, fetched, start = known or set(), 0, 0
    while start < max_postings:
        page = client.get_json(base, params={"domain": domain, "start": start, "num": PAGE, "sort_by": "new"})
        jobs = page.get("positions") or []
        for job in jobs:
            native = f"{host.split('.')[-2]}:{job.get('id')}"
            if not roles.classify(job.get("name") or ""):
                continue  # non-tech: never stored, so no text needed
            detail = None
            if native not in known and fetched < max_new_texts:
                detail = client.get_json(f"{base}/{job.get('id')}", params={"domain": domain})
                fetched += 1
            elif native not in known:
                continue
            yield parse(job, detail, host, company, sector)
        start += PAGE
        if len(jobs) < PAGE or start >= (page.get("count") or 0):
            break
