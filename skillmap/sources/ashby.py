"""Ashby public job posting API — documented, unauthenticated.

https://developers.ashbyhq.com/docs/public-job-posting-api
"""

from __future__ import annotations

from collections.abc import Iterator

from ..http import PoliteClient
from ..models import RawPosting
from ._util import dig, parse_date

COUNTRY_NAMES_TO_ISO = {"United States": "US", "Singapore": "SG", "United Kingdom": "GB"}


def board_url(token: str, region: str | None = None) -> str:
    return f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"


def _salary(job: dict) -> tuple[float | None, float | None, str | None, str | None]:
    for component in dig(job, "compensation", "summaryComponents", default=[]) or []:
        if component.get("compensationType") == "Salary" and component.get("minValue"):
            interval = (component.get("interval") or "").upper()
            period = "year" if "YEAR" in interval else "month" if "MONTH" in interval else None
            return component.get("minValue"), component.get("maxValue"), component.get("currencyCode"), period
    return None, None, None, None


def parse(payload: dict, company: str, sector: str | None = None) -> Iterator[RawPosting]:
    for job in payload.get("jobs", []):
        if job.get("isListed") is False:
            continue
        address = dig(job, "address", "postalAddress", default={}) or {}
        locations = [job.get("location") or "", *[loc.get("location", "") for loc in job.get("secondaryLocations") or []]]
        salary_min, salary_max, currency, period = _salary(job)
        workplace = (job.get("workplaceType") or "").lower()
        yield RawPosting(
            source="ashby",
            native_id=job.get("id") or job.get("jobUrl", ""),
            company=company,
            title=job.get("title", ""),
            description=job.get("descriptionPlain") or job.get("descriptionHtml") or "",
            url=job.get("jobUrl", ""),
            location="; ".join(filter(None, locations)) or ", ".join(
                filter(None, [address.get("addressLocality"), address.get("addressCountry")])
            ),
            country_hint=COUNTRY_NAMES_TO_ISO.get(address.get("addressCountry", "")),
            remote_hint=True if job.get("isRemote") and workplace == "remote" else None,
            posted_on=parse_date(job.get("publishedAt")),
            sector_hint=sector,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=currency,
            salary_period=period,
        )


def collect(client: PoliteClient, token: str, company: str, sector: str | None = None,
            region: str | None = None) -> Iterator[RawPosting]:
    yield from parse(client.get_json(board_url(token)), company, sector)
