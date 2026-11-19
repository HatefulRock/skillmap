"""MyCareersFuture (Singapore's national job portal), via the search API its own site uses.

It is included for Singapore coverage. It is not a documented public API, so this
collector is deliberately gentle: one request every 3 seconds and only the fields the dashboard
needs. Two passes: every open ad in the "Information Technology" category (~5,000, ~50 pages),
read in full each run so ads that stay open keep counting as live; then keyword searches,
newest first and stopping at `since`, for tech roles filed under other categories.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import date

from ..http import PoliteClient
from ..models import RawPosting
from ._util import dig, parse_date

log = logging.getLogger(__name__)

SEARCH = "https://api.mycareersfuture.gov.sg/v2/search"
JOBS = "https://api.mycareersfuture.gov.sg/v2/jobs"
HOST_INTERVAL = {"api.mycareersfuture.gov.sg": 3.0}

# MCF position levels → dashboard seniority, used as a hint the title rules can override.
POSITION_LEVEL = {
    "fresh/entry level": "junior",
    "non-executive": "junior",
    "junior executive": "junior",
    "executive": "mid",
    "professional": "mid",
    "senior executive": "senior",
    "manager": "manager",
    "middle management": "manager",
    "senior management": "manager",
}


def parse_job(job: dict) -> RawPosting:
    levels = [str(level.get("position", "")).lower() for level in job.get("positionLevels") or []]
    salary_type = str(dig(job, "salary", "type", "salaryType", default="") or "").lower()
    skills = ", ".join(skill.get("skill", "") for skill in job.get("skills") or [])
    description = job.get("description") or ""
    if skills:
        # MCF's own skill tags are appended so they count, but only as listed by the employer.
        description = f"{description}\nSkills: {skills}"
    address = job.get("address") or {}
    posted = dig(job, "metadata", "newPostingDate") or dig(job, "metadata", "originalPostingDate") or dig(
        job, "metadata", "createdAt"
    )
    return RawPosting(
        source="mcf",
        native_id=job.get("uuid") or str(job.get("id")),
        company=dig(job, "postedCompany", "name", default="") or "",
        title=job.get("title", ""),
        description=description,
        url=dig(job, "metadata", "jobDetailsUrl", default="") or "",
        location=", ".join(filter(None, [address.get("street"), address.get("postalCode"), "Singapore"])),
        country_hint="SG",
        remote_hint=True if address.get("isOverseas") is False and re.search(r"\bremote\b", job.get("title", ""), re.I) else None,
        posted_on=parse_date(posted),
        seniority_hint=next((POSITION_LEVEL[level] for level in levels if level in POSITION_LEVEL), None),
        min_years_hint=job.get("minimumYearsExperience"),
        salary_min=dig(job, "salary", "minimum"),
        salary_max=dig(job, "salary", "maximum"),
        salary_currency="SGD" if dig(job, "salary", "minimum") else None,
        salary_period="month" if "month" in salary_type else ("year" if "annual" in salary_type else None),
    )


def category_page(client: PoliteClient, category: str, page: int, limit: int = 100) -> dict:
    return client.post_json(SEARCH, {"sessionId": "", "categories": [category], "sortBy": ["new_posting_date"]},
                            params={"limit": limit, "page": page})


def search_page(client: PoliteClient, query: str, page: int, limit: int = 100) -> dict:
    try:
        return client.post_json(
            SEARCH, {"search": query, "sortBy": ["new_posting_date"]}, params={"limit": limit, "page": page}
        )
    except Exception:
        # Older read path, kept as a fallback in case the POST search changes shape.
        return client.get_json(JOBS, params={"search": query, "limit": limit, "page": page, "sortBy": "new_posting_date"})


def collect(client: PoliteClient, queries: list[str], since: date | None = None, max_pages: int = 20,
            known_ids: set[str] | None = None, categories: list[str] | None = None,
            max_category_pages: int = 80) -> Iterator[RawPosting]:
    known_ids = known_ids or set()
    client.per_host_interval.update(HOST_INTERVAL)
    seen_this_run: set[str] = set()
    for category in categories or []:
        # The whole category, not just since the last run: a re-read is what keeps an ad live.
        for page in range(max_category_pages):
            try:
                results = category_page(client, category, page).get("results") or []
            except Exception as error:  # the keyword searches below still cover the main roles
                log.warning("mcf category %r page %d failed: %s", category, page, error)
                break
            for job in results:
                posting = parse_job(job)
                if posting.native_id not in seen_this_run:
                    seen_this_run.add(posting.native_id)
                    yield posting
            if len(results) < 100:
                break
    for query in queries:
        for page in range(max_pages):
            payload = search_page(client, query, page)
            results = payload.get("results") or []
            stop = False
            for job in results:
                posting = parse_job(job)
                if posting.native_id in seen_this_run:
                    continue
                seen_this_run.add(posting.native_id)
                if since and posting.posted_on and posting.posted_on < since:
                    stop = True
                    break
                yield posting
            if stop or len(results) < 100:
                break
