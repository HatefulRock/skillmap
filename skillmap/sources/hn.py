"""Hacker News "Ask HN: Who is hiring?" threads, via the official Algolia HN Search API.

https://hn.algolia.com/api — one request lists the monthly threads, one request per thread
returns every top-level comment (one company's post each). History goes back to 2011, which is
what gives the trend charts real depth from the first run.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterator
from datetime import date

from ..extract import location
from ..http import PoliteClient
from ..models import RawPosting
from ._util import parse_date

SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
ITEM = "https://hn.algolia.com/api/v1/items/{id}"
THREAD_TITLE = re.compile(r"^Ask HN: Who is hiring\? \((\w+) (\d{4})\)$")
ROLE_WORDS = re.compile(
    r"engineer|developer|scientist|researcher|architect|designer|manager|lead|sre|devops|analyst|intern|cto|founding",
    re.I,
)
LOCATION_WORDS = re.compile(r"remote|onsite|on-site|hybrid|\b[A-Z][a-z]+,\s+(?:[A-Z]{2}|[A-Z][a-z]+)\b")


def _looks_like_location(part: str) -> bool:
    return bool(LOCATION_WORDS.search(part) or location.normalise(part)[0])


# The account that posts the threads; April and May 2015 came from "_whoishiring" instead.
AUTHORS = ("whoishiring", "_whoishiring")


def list_threads(client: PoliteClient, since: date | None = None) -> list[dict]:
    """Monthly "Who is hiring?" threads, newest first, as {id, month (first day), title}."""
    by_month: dict[date, dict] = {}
    for author in AUTHORS:
        for thread in _threads_by(client, author, since):
            by_month.setdefault(thread["month"], thread)
    return [by_month[m] for m in sorted(by_month, reverse=True)]


def _threads_by(client: PoliteClient, author: str, since: date | None) -> list[dict]:
    threads: list[dict] = []
    page = 0
    while True:
        payload = client.get_json(
            SEARCH, params={"tags": f"story,author_{author}", "hitsPerPage": 200, "page": page}
        )
        for hit in payload.get("hits", []):
            match = THREAD_TITLE.match(hit.get("title") or "")
            if not match:
                continue  # "Who wants to be hired?" and "Freelancer?" threads share the author
            created = parse_date(hit.get("created_at"))
            if created is None:
                continue
            month = created.replace(day=1)
            if since and month < since.replace(day=1):
                return threads
            threads.append({"id": hit["objectID"], "month": month, "title": hit["title"]})
        page += 1
        if page >= payload.get("nbPages", 0):
            return threads


def split_header(text: str) -> tuple[str, str, str]:
    """Most posts open with `Company | Role | Location | ...`. Returns (company, role, location)."""
    first_line = text.split("\n", 1)[0]
    parts = [part.strip() for part in first_line.split("|") if part.strip()]
    if len(parts) < 2:
        return (parts[0][:80] if parts else ""), first_line[:200], ""
    company = parts[0][:80]
    role = next((part for part in parts[1:] if ROLE_WORDS.search(part)), "")
    location = next((part for part in parts[1:] if part is not role and _looks_like_location(part)), "")
    return company, (role or first_line)[:200], location[:120]


def to_text(comment_html: str) -> str:
    text = re.sub(r"<p>", "\n", comment_html or "")
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def parse_thread(payload: dict) -> Iterator[RawPosting]:
    for comment in payload.get("children", []):
        text = to_text(comment.get("text") or "")
        if not text:
            continue  # deleted or flagged
        company, role, location = split_header(text)
        yield RawPosting(
            source="hn",
            native_id=str(comment["id"]),
            company=company,
            title=role,
            description=text,
            url=f"https://news.ycombinator.com/item?id={comment['id']}",
            location=location,
            remote_hint=True if re.search(r"\bremote\b", text.split("\n", 1)[0], re.I) else None,
            posted_on=parse_date(comment.get("created_at")),
        )


def collect(client: PoliteClient, since: date | None = None) -> Iterator[RawPosting]:
    for thread in list_threads(client, since):
        yield from parse_thread(client.get_json(ITEM.format(id=thread["id"])))
