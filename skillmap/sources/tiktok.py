"""TikTok's careers site (lifeattiktok.com), the JSON its own search page loads.

One POST lists postings with their full text (`description` and `requirement`), so no second
request is needed. The list covers every TikTok office; only technical titles are kept, up to
`max_postings` a run.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..extract import roles
from ..http import PoliteClient
from ..models import RawPosting
from ._util import parse_date

API = "https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts"
HEADERS = {"website-path": "tiktok"}
PAGE = 50


def places(city: object) -> list[str]:
    """City, then the regions it sits in: {"name": "Singapore", "parent": {"name": ...}} → names."""
    names: list[str] = []
    while isinstance(city, dict) and len(names) < 5:
        name = str(city.get("en_name") or city.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
        city = city.get("parent")
    return names


def parse(job: dict, company: str = "TikTok", sector: str | None = None) -> RawPosting:
    text = "\n\n".join(filter(None, [job.get("description"), job.get("requirement")]))
    info = job.get("job_post_info") if isinstance(job.get("job_post_info"), dict) else {}
    return RawPosting(
        source="tiktok",
        native_id=str(job.get("id")),
        company=company,
        title=job.get("title") or "",
        description=text,
        url=f"https://lifeattiktok.com/search/{job.get('id')}",
        location=", ".join(places(job.get("city_info"))),
        posted_on=parse_date(info.get("publish_time") or job.get("publish_time")),
        sector_hint=sector,
    )


def collect(client: PoliteClient, token: str = "tiktok", company: str = "TikTok", sector: str | None = None,
            region: str | None = None, max_postings: int = 5000) -> Iterator[RawPosting]:
    offset = 0
    while offset < max_postings:
        data = client.post_json(API, {"keyword": "", "limit": PAGE, "offset": offset}, headers=HEADERS).get("data") or {}
        jobs = data.get("job_post_list") or []
        for job in jobs:
            if roles.classify(job.get("title") or ""):
                yield parse(job, company, sector)
        offset += PAGE
        if len(jobs) < PAGE or offset >= (data.get("count") or 0):
            break
