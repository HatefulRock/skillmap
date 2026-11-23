"""Gem's public job board API (api.gem.com/job_board/v0/{company}/job_posts/), Greenhouse-shaped."""

from __future__ import annotations

from collections.abc import Iterator

from ..http import PoliteClient
from ..models import RawPosting
from ._util import parse_date


def board_url(token: str, region: str | None = None) -> str:
    return f"https://api.gem.com/job_board/v0/{token}/job_posts/"


def parse(payload: list, company: str, sector: str | None = None) -> Iterator[RawPosting]:
    for job in payload if isinstance(payload, list) else []:
        location = job.get("location") or {}
        offices = job.get("offices") or []
        place = (location.get("name") if isinstance(location, dict) else str(location or "")) or ", ".join(
            o.get("name", "") for o in offices[:3] if isinstance(o, dict))
        yield RawPosting(
            source="gem",
            native_id=str(job.get("id")),
            company=company,
            title=job.get("title") or "",
            description=job.get("content") or job.get("content_plain") or "",
            url=job.get("absolute_url") or "",
            location=place,
            remote_hint=True if str(job.get("location_type") or "").lower() == "remote" else None,
            posted_on=parse_date(job.get("first_published_at") or job.get("created_at")),
            sector_hint=sector,
        )


def collect(client: PoliteClient, token: str, company: str, sector: str | None = None,
            region: str | None = None) -> Iterator[RawPosting]:
    yield from parse(client.get_json(board_url(token)), company, sector)
