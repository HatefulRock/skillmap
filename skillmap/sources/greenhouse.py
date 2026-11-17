"""Greenhouse Job Board API — public, documented, meant for embedding a company's job list.

https://developers.greenhouse.io/job-board.html
"""

from __future__ import annotations

import html
from collections.abc import Iterator

from ..http import PoliteClient
from ..models import RawPosting
from ._util import parse_date


def board_url(token: str, region: str | None = None) -> str:
    host = "boards-api.eu.greenhouse.io" if region == "eu" else "boards-api.greenhouse.io"
    return f"https://{host}/v1/boards/{token}/jobs?content=true"


def parse(payload: dict, company: str, sector: str | None = None) -> Iterator[RawPosting]:
    for job in payload.get("jobs", []):
        offices = [office.get("name", "") for office in job.get("offices") or [] if office.get("name")]
        location = (job.get("location") or {}).get("name") or ", ".join(offices)
        yield RawPosting(
            source="greenhouse",
            native_id=str(job["id"]),
            company=job.get("company_name") or company,
            title=job.get("title", ""),
            # `content` arrives HTML-escaped (&lt;p&gt;…), so unescape once before stripping tags.
            description=html.unescape(job.get("content") or ""),
            url=job.get("absolute_url", ""),
            location=location,
            posted_on=parse_date(job.get("first_published") or job.get("updated_at")),
            sector_hint=sector,
        )


def collect(client: PoliteClient, token: str, company: str, sector: str | None = None,
            region: str | None = None) -> Iterator[RawPosting]:
    yield from parse(client.get_json(board_url(token, region)), company, sector)
