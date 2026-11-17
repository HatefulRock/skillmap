"""Workable's public careers-widget API (the one companies embed on their own sites)."""

from __future__ import annotations

from collections.abc import Iterator

from ..http import PoliteClient
from ..models import RawPosting
from ._util import parse_date


def board_url(token: str, region: str | None = None) -> str:
    return f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=true"


def parse(payload: dict, company: str, sector: str | None = None) -> Iterator[RawPosting]:
    for job in payload.get("jobs", []):
        locations = job.get("locations") or []
        first = locations[0] if locations else {}
        yield RawPosting(
            source="workable",
            native_id=job.get("shortcode") or job.get("code") or job.get("url", ""),
            company=payload.get("name") or company,
            title=job.get("title", ""),
            description=job.get("description") or "",
            url=job.get("url") or job.get("shortlink", ""),
            location=", ".join(filter(None, [job.get("city") or first.get("city"), job.get("country") or first.get("country")])),
            country_hint=first.get("countryCode") or None,
            remote_hint=bool(job.get("telecommuting")) or None,
            posted_on=parse_date(job.get("published_on") or job.get("created_at")),
            sector_hint=sector,
        )


def collect(client: PoliteClient, token: str, company: str, sector: str | None = None,
            region: str | None = None) -> Iterator[RawPosting]:
    yield from parse(client.get_json(board_url(token)), company, sector)
