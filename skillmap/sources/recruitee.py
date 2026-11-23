"""Recruitee's public careers API ({company}.recruitee.com/api/offers/)."""

from __future__ import annotations

from collections.abc import Iterator

from ..http import PoliteClient
from ..models import RawPosting
from ._util import parse_date


def board_url(token: str, region: str | None = None) -> str:
    return f"https://{token}.recruitee.com/api/offers/"


def parse(payload: dict, company: str, sector: str | None = None) -> Iterator[RawPosting]:
    for job in payload.get("offers") or []:
        if job.get("status") not in (None, "published"):
            continue
        text = "\n".join(filter(None, [job.get("description"), job.get("requirements")]))
        yield RawPosting(
            source="recruitee",
            native_id=str(job.get("id") or job.get("slug") or job.get("careers_url", "")),
            company=job.get("company_name") or company,
            title=job.get("title") or "",
            description=text,
            url=job.get("careers_url") or job.get("url") or "",
            location=", ".join(filter(None, [job.get("city"), job.get("country")])),
            country_hint=(job.get("country_code") or "").upper() or None,
            remote_hint=bool(job.get("remote")) or None,
            posted_on=parse_date(job.get("published_at") or job.get("created_at")),
            sector_hint=sector,
        )


def collect(client: PoliteClient, token: str, company: str, sector: str | None = None,
            region: str | None = None) -> Iterator[RawPosting]:
    yield from parse(client.get_json(board_url(token)), company, sector)
