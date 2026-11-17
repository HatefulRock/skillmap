"""Adzuna job search API — free developer key, personal research use allowed with attribution
("Job data: The Adzuna API"). https://developer.adzuna.com/

Adzuna returns a description *snippet* (a few hundred characters), not the full ad, so skills
found here are a lower bound. The dashboard reports skill share per source for that reason.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..http import PoliteClient
from ..models import RawPosting
from ._util import dig, parse_date

API = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
CURRENCY = {"sg": "SGD", "gb": "GBP", "us": "USD", "au": "AUD", "in": "INR", "de": "EUR", "fr": "EUR", "nl": "EUR"}


def parse(payload: dict, country: str) -> Iterator[RawPosting]:
    for job in payload.get("results", []):
        salary_min, salary_max = job.get("salary_min"), job.get("salary_max")
        yield RawPosting(
            source="adzuna",
            native_id=str(job["id"]),
            company=dig(job, "company", "display_name", default="") or "",
            title=job.get("title", ""),
            description=job.get("description", ""),
            url=job.get("redirect_url", ""),
            location=dig(job, "location", "display_name", default="") or "",
            country_hint=country.upper(),
            posted_on=parse_date(job.get("created")),
            # Adzuna salaries are annual; a figure it *predicted* rather than read off the ad is skipped.
            salary_min=salary_min if job.get("salary_is_predicted") in ("0", 0, None) else None,
            salary_max=salary_max if job.get("salary_is_predicted") in ("0", 0, None) else None,
            salary_currency=CURRENCY.get(country) if salary_min else None,
            salary_period="year" if salary_min else None,
        )


def collect(client: PoliteClient, app_id: str, app_key: str, country: str, queries: list[str],
            max_days_old: int = 3, max_pages: int = 10) -> Iterator[RawPosting]:
    for query in queries:
        for page in range(1, max_pages + 1):
            payload = client.get_json(
                API.format(country=country, page=page),
                params={
                    "app_id": app_id, "app_key": app_key, "what": query, "results_per_page": 50,
                    "max_days_old": max_days_old, "sort_by": "date", "content-type": "application/json",
                },
            )
            results = list(parse(payload, country))
            yield from results
            if len(results) < 50:
                break
