"""SmartRecruiters Posting API — public and unauthenticated, the one companies' career pages use.

https://developers.smartrecruiters.com/docs/posting-api

The list endpoint gives title, location and level but no text, so each posting's text is a
second request. That request is only made for technical titles that haven't been stored yet:
non-tech postings are kept as metadata, and a posting already stored only needs its
last-seen date moved. At most `max_new_texts` texts are fetched per company per run; new tech
postings past that are left for the next run rather than stored without text.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..extract import roles
from ..http import PoliteClient
from ..models import RawPosting
from ._util import dig, parse_date

API = "https://api.smartrecruiters.com/v1/companies/{token}/postings"
PAGE = 100
LEVELS = {"internship": "intern", "entry_level": "junior", "associate": "junior", "director": "manager",
          "executive": "manager"}


def parse_listing(job: dict, company: str, sector: str | None = None, description: str = "") -> RawPosting:
    location = job.get("location") or {}
    country = (location.get("country") or "").upper() or None
    return RawPosting(
        source="smartrecruiters",
        native_id=str(job["id"]),
        company=dig(job, "company", "name", default="") or company,
        title=job.get("name", ""),
        description=description,
        url=f"https://jobs.smartrecruiters.com/{dig(job, 'company', 'identifier', default='')}/{job['id']}",
        location=location.get("fullLocation") or ", ".join(filter(None, [location.get("city"), location.get("country")])),
        country_hint=country if country and len(country) == 2 else None,
        remote_hint=True if location.get("remote") else None,
        posted_on=parse_date(job.get("releasedDate")),
        sector_hint=sector,
        seniority_hint=LEVELS.get(dig(job, "experienceLevel", "id", default="") or ""),
    )


def text_of(detail: dict) -> str:
    sections = dig(detail, "jobAd", "sections", default={}) or {}
    return "\n".join(
        f"{(sections.get(key) or {}).get('title', '')}\n{(sections.get(key) or {}).get('text', '')}"
        for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation")
    )


def collect(client: PoliteClient, token: str, company: str, sector: str | None = None, region: str | None = None,
            known: set[str] | None = None, max_postings: int = 3000, max_new_texts: int = 300) -> Iterator[RawPosting]:
    known = known or set()
    offset, fetched = 0, 0
    while offset < max_postings:
        page = client.get_json(API.format(token=token), params={"limit": PAGE, "offset": offset})
        jobs = page.get("content") or []
        for job in jobs:
            text = ""
            if str(job["id"]) not in known and roles.classify(job.get("name", "")):
                if fetched >= max_new_texts:
                    continue
                text = text_of(client.get_json(f"{API.format(token=token)}/{job['id']}"))
                fetched += 1
            yield parse_listing(job, company, sector, text)
        offset += PAGE
        if len(jobs) < PAGE or offset >= (page.get("totalFound") or 0):
            break
