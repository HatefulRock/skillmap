"""Company careers sites that publish schema.org JobPosting markup.

Many employers run their own careers site rather than a job-board platform, but most mark up
each job page with JSON-LD (`<script type="application/ld+json">` holding a `JobPosting`),
because search engines list jobs from it. This reader takes, per company, a listing page or a
sitemap (`token`) and a pattern for job-page links (`links`), then reads the markup on each
job page it has not stored yet. It honours the site's robots.txt and reads at most
`max_new_texts` new job pages a run, so a large site fills in over several weeks.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from datetime import date
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from ..http import USER_AGENT, PoliteClient
from ..models import RawPosting

LD_JSON = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)
HREF = re.compile(r'href=["\']([^"\'#]+)["\']', re.I)
LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
DEFAULT_LINKS = r"/jobs?/|/careers?/.+|/positions?/|/opening|/vacanc"


def native_id(url: str) -> str:
    """Stable id from the job page's address, so stored jobs are known before fetching them."""
    parsed = urlparse(url)
    return hashlib.sha1(f"{parsed.netloc}{parsed.path}".rstrip("/").lower().encode()).hexdigest()[:16]


def _site(host: str) -> str:
    """The registrable part of a host, so careers.google.com and www.google.com count as one site."""
    labels = host.lower().split(":")[0].split(".")
    keep = 3 if len(labels) > 2 and labels[-2] in {"co", "com", "ac", "gov", "org", "net"} and len(labels[-1]) == 2 else 2
    return ".".join(labels[-keep:])


def job_links(page: str, base: str, pattern: str) -> list[str]:
    """Job-page addresses on a listing page or in a sitemap, on the same site, in page order."""
    rule = re.compile(pattern, re.I)
    host = _site(urlparse(base).netloc)
    found = LOC.findall(page) or [urljoin(base, href) for href in HREF.findall(page)]
    out = []
    for url in found:
        url = url.split("?")[0]
        if _site(urlparse(url).netloc) == host and rule.search(urlparse(url).path) and url not in out and url != base:
            out.append(url)
    return out


def _walk(node: object) -> Iterator[dict]:
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        if "@graph" in node:
            yield from _walk(node["@graph"])
        kind = node.get("@type")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            yield node


def job_posting(page: str) -> dict | None:
    for block in LD_JSON.findall(page):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for node in _walk(data):
            return node
    return None


def _name(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "")
    return str(value or "")


def _location(job: dict) -> tuple[str, str | None, bool]:
    places = job.get("jobLocation") or []
    places = places if isinstance(places, list) else [places]
    parts, country = [], None
    for place in places:
        address = (place or {}).get("address") or {}
        if isinstance(address, str):
            parts.append(address)
            continue
        bits = [address.get("addressLocality"), address.get("addressRegion"), _name(address.get("addressCountry"))]
        parts.append(", ".join(b for b in bits if b))
        code = _name(address.get("addressCountry"))
        if not country and len(code) == 2:
            country = code.upper()
    remote = str(job.get("jobLocationType") or "").upper() == "TELECOMMUTE"
    return " | ".join(p for p in parts if p), country, remote


def _salary(job: dict) -> tuple[float | None, float | None, str | None, str | None]:
    pay = job.get("baseSalary") or {}
    if not isinstance(pay, dict):
        return None, None, None, None
    value = pay.get("value") or {}
    if not isinstance(value, dict):
        value = {"value": value}
    low = value.get("minValue") or value.get("value")
    high = value.get("maxValue") or low
    unit = {"YEAR": "year", "MONTH": "month", "HOUR": "hour", "WEEK": "week", "DAY": "day"}.get(
        str(value.get("unitText") or pay.get("unitText") or "").upper())
    try:
        low, high = (float(low), float(high)) if low else (None, None)
    except (TypeError, ValueError):
        return None, None, None, None
    return low, high, pay.get("currency") if low and unit else None, unit if low else None


def employer(job: dict) -> str:
    """The hiring company a job page names, for job boards listing many companies (company "*")."""
    org = job.get("hiringOrganization")
    if isinstance(org, list):
        org = org[0] if org else None
    return str(org.get("name") or "").strip() if isinstance(org, dict) else str(org or "").strip()


def parse(job: dict, url: str, company: str, sector: str | None = None) -> RawPosting:
    location, country, remote = _location(job)
    low, high, currency, period = _salary(job)
    try:
        posted = date.fromisoformat(str(job.get("datePosted") or "")[:10])
    except ValueError:
        posted = None
    return RawPosting(
        source="jsonld", native_id=native_id(url), company=employer(job) if company == "*" else company,
        title=str(job.get("title") or ""),
        description=str(job.get("description") or ""), url=url, location=location, country_hint=country,
        remote_hint=remote or None, posted_on=posted, sector_hint=sector,
        salary_min=low, salary_max=high, salary_currency=currency, salary_period=period,
    )


def _robots(client: PoliteClient, url: str) -> RobotFileParser:
    parsed = urlparse(url)
    robots = RobotFileParser()
    response = client.request("GET", f"{parsed.scheme}://{parsed.netloc}/robots.txt")
    robots.parse(response.text.splitlines() if response.status_code == 200 else [])
    return robots


def collect(client: PoliteClient, token: str, company: str, sector: str | None = None, region: str | None = None,
            known: set[str] | None = None, links: str | None = None, max_new_texts: int = 150) -> Iterator[RawPosting]:
    robots = _robots(client, token)
    if not robots.can_fetch(USER_AGENT, token):
        raise PermissionError(f"robots.txt disallows {token}")
    # A sitemap may list job pages on a sibling host (careers.google.com); each host has its own robots.txt.
    hosts = {urlparse(token).netloc: robots}
    listing = client.request("GET", token, headers={"Accept": "text/html,application/xml"})
    listing.raise_for_status()
    known, fetched = known or set(), 0
    for url in job_links(listing.text, token, links or DEFAULT_LINKS):
        if native_id(url) in known:
            # Still listed: refresh its last-seen day without reading the page again.
            yield RawPosting(source="jsonld", native_id=native_id(url), company=company, title="", description="",
                             url=url)
            continue
        host = urlparse(url).netloc
        if host not in hosts:
            hosts[host] = _robots(client, url)
        if fetched >= max_new_texts or not hosts[host].can_fetch(USER_AGENT, url):
            continue
        page = client.request("GET", url, headers={"Accept": "text/html"})
        fetched += 1
        if page.status_code != 200:
            continue
        job = job_posting(page.text)
        if job:
            yield parse(job, url, company, sector)
