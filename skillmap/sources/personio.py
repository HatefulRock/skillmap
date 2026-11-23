"""Personio's public job feed ({company}.jobs.personio.de/xml), the one its careers pages are built from."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator

from ..http import PoliteClient
from ..models import RawPosting
from ._util import parse_date


def board_url(token: str, region: str | None = None) -> str:
    return f"https://{token}.jobs.personio.de/xml?language=en"


def _text(node: ET.Element, tag: str) -> str:
    return (node.findtext(tag) or "").strip()


def parse(xml: str, company: str, sector: str | None = None, token: str = "") -> Iterator[RawPosting]:
    if "<!DOCTYPE" in xml[:500] or "<!ENTITY" in xml:
        return  # not a feed (an HTML page), and never expand entities
    root = ET.fromstring(xml.encode())
    for job in root.iter("position"):
        native = _text(job, "id")
        sections = [f"<h3>{_text(d, 'name')}</h3>{_text(d, 'value')}" for d in job.iter("jobDescription")]
        yield RawPosting(
            source="personio",
            native_id=native,
            company=_text(job, "subcompany") or company,
            title=_text(job, "name"),
            description="\n".join(sections),
            url=f"https://{token}.jobs.personio.de/job/{native}" if token else "",
            location=_text(job, "office"),
            posted_on=parse_date(_text(job, "createdAt")),
            sector_hint=sector,
        )


def collect(client: PoliteClient, token: str, company: str, sector: str | None = None,
            region: str | None = None) -> Iterator[RawPosting]:
    yield from parse(client.get_text(board_url(token)), company, sector, token)
