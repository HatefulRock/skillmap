"""Amazon's own careers search (amazon.jobs), the JSON the careers site itself loads.

Amazon lists tens of thousands of jobs, most of them warehouse and retail roles, so only the
technical job categories are read, newest first, up to `max_postings` per run. Each result
carries its full text, so no second request is needed.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime

from ..http import PoliteClient
from ..models import RawPosting

API = "https://www.amazon.jobs/en/search.json"
PAGE = 100
CATEGORIES = ("software-development", "machine-learning-science", "data-science", "research-science",
              "solutions-architect", "systems-quality-security-engineering")


def posted(value: str | None) -> date | None:
    try:
        return datetime.strptime((value or "").strip(), "%B %d, %Y").date()
    except ValueError:
        return None


def parse(job: dict, company: str = "Amazon", sector: str | None = None) -> RawPosting:
    text = "\n\n".join(filter(None, [
        job.get("description"),
        job.get("basic_qualifications") and f"Basic qualifications\n{job['basic_qualifications']}",
        job.get("preferred_qualifications") and f"Preferred qualifications\n{job['preferred_qualifications']}",
    ]))
    return RawPosting(
        source="amazon",
        native_id=str(job.get("id_icims") or job.get("id")),
        company=company,
        title=job.get("title", ""),
        description=text,
        url=f"https://www.amazon.jobs{job.get('job_path', '')}",
        location=job.get("normalized_location") or job.get("location") or "",
        posted_on=posted(job.get("posted_date")),
        sector_hint=sector,
    )


def collect(client: PoliteClient, token: str = "amazon", company: str = "Amazon", sector: str | None = None,
            region: str | None = None, max_postings: int = 3000) -> Iterator[RawPosting]:
    offset = 0
    while offset < max_postings:
        params = [("offset", offset), ("result_limit", PAGE), ("sort", "recent"),
                  *[("category[]", c) for c in CATEGORIES]]
        page = client.get_json(API, params=params)
        jobs = page.get("jobs") or []
        for job in jobs:
            yield parse(job, company, sector)
        offset += PAGE
        if len(jobs) < PAGE or offset >= (page.get("hits") or 0):
            break
