"""Apple's careers site (jobs.apple.com). robots.txt is absent, so nothing is disallowed.

Pages are server-rendered: each carries its data as `window.__staticRouterHydrationData =
JSON.parse("...")`. The search pages (20 roles each, newest first) give detail links whose last
part is the title; only technical titles get a detail page read, at most `max_new_texts` a run.
The token is the locale path, e.g. "en-us".
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import datetime

from ..extract import roles
from ..http import PoliteClient
from ..models import RawPosting

BASE = "https://jobs.apple.com"
HYDRATION = re.compile(r'__staticRouterHydrationData\s*=\s*JSON\.parse\("((?:[^"\\]|\\.)*)"\)')
TEXT_FIELDS = ("jobSummary", "description", "responsibilities", "minimumQualifications",
               "preferredQualifications", "educationAndExperience", "additionalRequirements", "payAndBenefits")


def hydration(page: str) -> dict | None:
    match = HYDRATION.search(page)
    if not match:
        return None
    try:
        return json.loads(json.loads(f'"{match.group(1)}"'))
    except ValueError:
        return None


def jobs_data(data: object) -> dict | None:
    """The `jobsData` object anywhere in the hydration data."""
    if isinstance(data, dict):
        if isinstance(data.get("jobsData"), dict):
            return data["jobsData"]
        children = list(data.values())
    elif isinstance(data, list):
        children = data
    else:
        return None
    for child in children:
        found = jobs_data(child)
        if found:
            return found
    return None


def entries(data: object) -> list[dict]:
    """The roles on a search page: every object in the hydration data with an id, a posting title
    and the address slug (summary, locations and date included, but not the full text)."""
    out: list[dict] = []
    if isinstance(data, dict):
        if data.get("id") and data.get("postingTitle") and data.get("transformedPostingTitle"):
            out.append(data)
        else:
            for child in data.values():
                out.extend(entries(child))
    elif isinstance(data, list):
        for child in data:
            out.extend(entries(child))
    return out


def _date(text: str | None):
    for fmt in ("%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime((text or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


def parse(job: dict, number: str, url: str, company: str, sector: str | None = None) -> RawPosting:
    places = job.get("locations") or []
    location = "; ".join(", ".join(p for p in (pl.get("city") or pl.get("name"), pl.get("stateProvince"),
                                                pl.get("countryName")) if p) for pl in places[:3])
    country = next((pl.get("countryID", "").removeprefix("iso-country-") for pl in places if pl.get("countryID")), "")
    text = "\n\n".join(str(job[f]) for f in TEXT_FIELDS if job.get(f))
    return RawPosting(
        source="apple",
        native_id=number,
        company=company,
        title=job.get("postingTitle") or "",
        description=text,
        url=url,
        location=location,
        country_hint={"USA": "US", "GBR": "GB", "CAN": "CA", "IND": "IN", "CHN": "CN", "JPN": "JP", "DEU": "DE",
                      "FRA": "FR", "SGP": "SG", "IRL": "IE", "AUS": "AU", "ISR": "IL", "KOR": "KR",
                      "TWN": "TW", "ESP": "ES", "ITA": "IT", "NLD": "NL", "CHE": "CH", "SWE": "SE",
                      "DNK": "DK", "POL": "PL", "CZE": "CZ", "AUT": "AT", "MEX": "MX", "BRA": "BR"}.get(country),
        remote_hint=True if job.get("homeOffice") else None,
        posted_on=_date(job.get("postingDate") or job.get("postDateInGMT")),
        sector_hint=sector,
    )


def collect(client: PoliteClient, token: str, company: str, sector: str | None = None, region: str | None = None,
            known: set[str] | None = None, max_pages: int = 400, max_new_texts: int = 200) -> Iterator[RawPosting]:
    locale = token or "en-us"
    known, fetched = known or set(), 0
    for page_no in range(1, max_pages + 1):
        page = client.request("GET", f"{BASE}/{locale}/search", params={"sort": "newest", "page": page_no},
                              headers={"Accept": "text/html"})
        page.raise_for_status()
        found = entries(hydration(page.text))
        if not found:
            break
        for entry in found:
            number = str(entry["id"])
            if not roles.classify(entry["postingTitle"]):
                continue  # non-technical: never stored
            url = f"{BASE}/{locale}/details/{number}/{entry['transformedPostingTitle']}"
            if number in known:
                yield RawPosting(source="apple", native_id=number, company=company, title="", description="", url=url)
                continue
            job = entry
            if fetched < max_new_texts:
                detail = client.request("GET", url, headers={"Accept": "text/html"})
                fetched += 1
                full = jobs_data(hydration(detail.text)) if detail.status_code == 200 else None
                job = {**entry, **full} if full else entry
            yield parse(job, number, url, company, sector)
