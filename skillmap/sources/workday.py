"""Workday career sites (`<tenant>.<wdN>.myworkdayjobs.com/<site>`), used by most banks and large
corporates. The careers page itself loads its jobs from a public JSON endpoint, which is what
this reads.

A board's token is `<tenant>.<wdN>/<site>`, as in the careers URL: `db.wd3/DBWebsite` for
https://db.wd3.myworkdayjobs.com/DBWebsite.

The list endpoint gives title, place and a "posted N days ago" label but no text, so each
posting's text is a second request. As with SmartRecruiters, that request is only made for
technical titles not stored yet, at most `max_new_texts` per board per run. Large employers list
thousands of jobs, more than the list endpoint pages through, so past `max_postings` the board is
searched again with technical keywords to reach the postings that matter here.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

from ..extract import roles
from ..http import PoliteClient
from ..models import RawPosting
from ._util import dig, parse_date

PAGE = 20  # the most the endpoint returns per request
TECH_QUERIES = ("engineer", "developer", "data", "machine learning")
_AGO = re.compile(r"(\d+)\+? Days? Ago", re.I)
_MANY_PLACES = re.compile(r"^\d+ Locations?$", re.I)


def parts(token: str) -> tuple[str, str, str]:
    """`db.wd3/DBWebsite` → (host, tenant, site)."""
    host, site = token.split("/", 1)
    return f"{host}.myworkdayjobs.com", host.split(".", 1)[0], site


def posted(label: str, today: date) -> date | None:
    """"Posted Today", "Posted Yesterday", "Posted 3 Days Ago"; "30+ Days Ago" is too vague to use."""
    text = (label or "").lower()
    if "today" in text:
        return today
    if "yesterday" in text:
        return today - timedelta(days=1)
    m = _AGO.search(label or "")
    return today - timedelta(days=int(m.group(1))) if m and "+" not in (label or "") else None


def native_id(tenant: str, job: dict) -> str:
    bullets = job.get("bulletFields") or []
    req = bullets[0] if bullets else (job.get("externalPath") or "").rsplit("_", 1)[-1]
    return f"{tenant}:{req}"


def parse(job: dict, token: str, company: str, sector: str | None, today: date, detail: dict | None = None) -> RawPosting:
    host, tenant, site = parts(token)
    info = (detail or {}).get("jobPostingInfo") or {}
    place = info.get("location") or job.get("locationsText") or ""
    if _MANY_PLACES.match(place):
        place = ""
    country = dig(info, "country", "alpha2Code", default=None)
    return RawPosting(
        source="workday",
        native_id=native_id(tenant, job),
        company=dig(detail, "hiringOrganization", "name", default=None) or company,
        title=info.get("title") or job.get("title", ""),
        description=info.get("jobDescription") or "",
        url=info.get("externalUrl") or f"https://{host}/{site}{job.get('externalPath', '')}",
        location=", ".join(filter(None, [place, dig(info, "country", "descriptor", default=None)])),
        country_hint=country if isinstance(country, str) and len(country) == 2 else None,
        remote_hint=True if "remote" in (info.get("remoteType") or "").lower() else None,
        posted_on=parse_date(info.get("startDate")) or posted(job.get("postedOn", ""), today),
        sector_hint=sector,
    )


def listings(client: PoliteClient, token: str, max_postings: int) -> Iterator[dict]:
    host, tenant, site = parts(token)
    url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    seen: set[str] = set()

    def search(text: str, cap: int) -> Iterator[dict]:
        offset, total = 0, None
        while offset < cap:
            page = client.post_json(url, {"appliedFacets": {}, "limit": PAGE, "offset": offset, "searchText": text})
            jobs = page.get("jobPostings") or []
            if total is None:  # only the first page states the total
                total = page.get("total") or 0
            for job in jobs:
                path = job.get("externalPath")
                if path and path not in seen:
                    seen.add(path)
                    yield job
            offset += PAGE
            if len(jobs) < PAGE or offset >= total:
                break
        if text == "" and total and total > cap:
            for query in TECH_QUERIES:
                yield from search(query, cap // 3)

    yield from search("", max_postings)


def collect(client: PoliteClient, token: str, company: str, sector: str | None = None, region: str | None = None,
            known: set[str] | None = None, max_postings: int = 1000, max_new_texts: int = 150,
            today: date | None = None) -> Iterator[RawPosting]:
    host, tenant, site = parts(token)
    known, today, fetched = known or set(), today or datetime.now(UTC).date(), 0
    for job in listings(client, token, max_postings):
        detail = None
        if native_id(tenant, job).split(":", 1)[1] not in known and roles.classify(job.get("title", "")):
            if fetched >= max_new_texts:
                continue  # left for next run rather than stored without text
            detail = client.get_json(f"https://{host}/wday/cxs/{tenant}/{site}{job['externalPath']}")
            fetched += 1
        yield parse(job, token, company, sector, today, detail)
