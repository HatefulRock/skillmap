"""Teamtailor careers sites' RSS feed ({company}.teamtailor.com/jobs.rss), which carries full ad text."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from email.utils import parsedate_to_datetime

from ..http import PoliteClient
from ..models import RawPosting

TT = "{https://teamtailor.com/locations}"


def board_url(token: str, region: str | None = None) -> str:
    return f"https://{token}.teamtailor.com/jobs.rss"


def _date(value: str | None):
    try:
        return parsedate_to_datetime(value).date() if value else None
    except (TypeError, ValueError):
        return None


def parse(xml: str, company: str, sector: str | None = None) -> Iterator[RawPosting]:
    if "<!DOCTYPE" in xml[:500] or "<!ENTITY" in xml:
        return
    root = ET.fromstring(xml.encode())
    for item in root.iter("item"):
        place = next(item.iter(f"{TT}location"), None)
        city = (place.findtext(f"{TT}city") or "").strip() if place is not None else ""
        country = (place.findtext(f"{TT}country") or "").strip() if place is not None else ""
        remote = (item.findtext("remoteStatus") or "").strip().lower()
        link = (item.findtext("link") or "").strip()
        yield RawPosting(
            source="teamtailor",
            native_id=(item.findtext("guid") or link).strip(),
            company=company,
            title=(item.findtext("title") or "").strip(),
            description=item.findtext("description") or "",
            url=link,
            location=", ".join(filter(None, [city, country])),
            remote_hint=True if remote == "fully" else None,
            posted_on=_date(item.findtext("pubDate")),
            sector_hint=sector,
        )


def collect(client: PoliteClient, token: str, company: str, sector: str | None = None,
            region: str | None = None) -> Iterator[RawPosting]:
    yield from parse(client.get_text(board_url(token)), company, sector)
