"""Lever Postings API — public, documented. https://github.com/lever/postings-api"""

from __future__ import annotations

from collections.abc import Iterator

from ..http import PoliteClient
from ..models import RawPosting
from ._util import parse_date


def board_url(token: str, region: str | None = None) -> str:
    host = "api.eu.lever.co" if region == "eu" else "api.lever.co"
    return f"https://{host}/v0/postings/{token}?mode=json"


def parse(payload: list, company: str, sector: str | None = None) -> Iterator[RawPosting]:
    for job in payload:
        categories = job.get("categories") or {}
        # Requirements usually live in `lists` (one block per heading), not in `descriptionPlain`.
        sections = [f"{block.get('text', '')}\n{block.get('content', '')}" for block in job.get("lists") or []]
        description = "\n".join(
            [job.get("descriptionPlain") or job.get("description") or "", *sections, job.get("additionalPlain") or ""]
        )
        workplace = (job.get("workplaceType") or "").lower()
        pay = job.get("salaryRange") or {}
        interval = (pay.get("interval") or "").lower()
        period = next((p for p in ("year", "month", "week", "day", "hour") if p in interval), None)
        has_pay = bool(pay.get("min") and pay.get("currency") and period)
        yield RawPosting(
            source="lever",
            native_id=job["id"],
            company=company,
            title=job.get("text", ""),
            description=description,
            url=job.get("hostedUrl", ""),
            location=categories.get("location") or ", ".join(categories.get("allLocations") or []),
            country_hint=(job.get("country") or None),
            remote_hint=True if workplace == "remote" else (False if workplace else None),
            posted_on=parse_date(job.get("createdAt")),
            sector_hint=sector,
            salary_min=pay.get("min") if has_pay else None,
            salary_max=pay.get("max") if has_pay else None,
            salary_currency=pay.get("currency") if has_pay else None,
            salary_period=period if has_pay else None,
        )


def collect(client: PoliteClient, token: str, company: str, sector: str | None = None,
            region: str | None = None) -> Iterator[RawPosting]:
    yield from parse(client.get_json(board_url(token, region)), company, sector)
