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
        )


def collect(client: PoliteClient, token: str, company: str, sector: str | None = None,
            region: str | None = None) -> Iterator[RawPosting]:
    yield from parse(client.get_json(board_url(token, region)), company, sector)
