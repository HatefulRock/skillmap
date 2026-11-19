"""Public job-board APIs that ask only for a credit link: Himalayas, Jobicy, RemoteOK (remote
jobs worldwide) and Arbeitnow (Europe, mostly Germany).

They republish ads from many companies, so many of their tech ads are also on a company board we
already read; `aggregate.dedupe` merges those by company, title and country. Only tech titles
are kept: a non-tech ad from a board with ~100k of them would cost database space and say
nothing about skills. Each source stops at ads older than `since`, so a weekly run reads only
the new week; the first run reads back `since` days.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import date

from ..extract import roles
from ..http import PoliteClient
from ..models import RawPosting
from ._util import parse_date

log = logging.getLogger(__name__)

HIMALAYAS = "https://himalayas.app/jobs/api/search"
JOBICY = "https://jobicy.com/api/v2/remote-jobs"
REMOTEOK = "https://remoteok.com/api"
ARBEITNOW = "https://www.arbeitnow.com/api/job-board-api"
HOST_INTERVAL = {"himalayas.app": 1.0, "jobicy.com": 2.0, "remoteok.com": 2.0, "www.arbeitnow.com": 1.0}

PERIOD = {"hourly": "hour", "hour": "hour", "daily": "day", "weekly": "week", "monthly": "month", "month": "month",
          "yearly": "year", "annual": "year", "annually": "year", "year": "year"}
LEVEL = {"entry": "junior", "entry-level": "junior", "junior": "junior", "intern": "intern", "internship": "intern",
         "mid": "mid", "mid-level": "mid", "senior": "senior", "lead": "staff", "staff": "staff", "principal": "staff",
         "manager": "manager", "director": "manager", "executive": "manager"}


def _epoch(value: object) -> date | None:
    """Seconds since 1970 (these APIs) rather than the milliseconds parse_date expects."""
    try:
        return parse_date(float(value) * 1000)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return parse_date(value)


def _level(value: object) -> str | None:
    values = value if isinstance(value, list) else [value]
    for v in values:
        key = str(v or "").strip().lower()
        if key in LEVEL:
            return LEVEL[key]
    return None


def _pay(low: object, high: object, currency: object, period: object) -> dict:
    try:
        low_f = float(low) if low not in (None, "", 0, "0") else None
        high_f = float(high) if high not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        return {}
    unit = PERIOD.get(str(period or "").lower())
    if not low_f or not currency or not unit:
        return {}
    return {"salary_min": low_f, "salary_max": high_f or low_f, "salary_currency": str(currency).upper(),
            "salary_period": unit}


def himalayas_job(job: dict) -> RawPosting:
    places = job.get("locationRestrictions") or []
    return RawPosting(
        source="himalayas",
        native_id=str(job.get("guid") or job.get("applicationLink")),
        company=job.get("companyName") or "",
        title=job.get("title") or "",
        description=job.get("description") or "",
        url=job.get("applicationLink") or job.get("guid") or "",
        # A remote ad open to one country is that country's market; to several, no one country.
        location=places[0] if len(places) == 1 else ("Remote" if not places else "Remote, " + ", ".join(places[:3])),
        remote_hint=True,
        posted_on=_epoch(job.get("pubDate")),
        seniority_hint=_level(job.get("seniority")),
        **_pay(job.get("minSalary"), job.get("maxSalary"), job.get("currency"), job.get("salaryPeriod")),
    )


def himalayas(client: PoliteClient, queries: list[str], since: date, max_pages: int = 50) -> Iterator[RawPosting]:
    """Search results, newest first (20 a page): each query stops at the first ad before `since`."""
    seen: set[str] = set()
    for query in queries:
        for page in range(1, max_pages + 1):
            jobs = client.get_json(HIMALAYAS, params={"q": query, "sort": "recent", "page": page}).get("jobs") or []
            stop = not jobs
            for job in jobs:
                posting = himalayas_job(job)
                if posting.posted_on and posting.posted_on < since:
                    stop = True
                    continue
                if posting.native_id in seen or not roles.classify(posting.title):
                    continue
                seen.add(posting.native_id)
                yield posting
            if stop:
                break


def jobicy_job(job: dict) -> RawPosting:
    return RawPosting(
        source="jobicy",
        native_id=str(job.get("id")),
        company=job.get("companyName") or "",
        title=job.get("jobTitle") or "",
        description=job.get("jobDescription") or "",
        url=job.get("url") or "",
        location=f"Remote, {job.get('jobGeo')}" if job.get("jobGeo") else "Remote",
        remote_hint=True,
        posted_on=parse_date(job.get("pubDate")),
        seniority_hint=_level(job.get("jobLevel")),
        **_pay(job.get("salaryMin"), job.get("salaryMax"), job.get("salaryCurrency"), job.get("salaryPeriod")),
    )


def jobicy(client: PoliteClient, industries: list[str], since: date, max_pages: int = 10) -> Iterator[RawPosting]:
    for industry in industries:
        cursor = None
        for _ in range(max_pages):
            params = {"count": 100, "industry": industry, **({"cursor": cursor} if cursor else {})}
            payload = client.get_json(JOBICY, params=params)
            stop = False
            for job in payload.get("jobs") or []:
                posting = jobicy_job(job)
                if posting.posted_on and posting.posted_on < since:
                    stop = True
                    continue
                if roles.classify(posting.title):
                    yield posting
            cursor = payload.get("nextCursor")
            if stop or not payload.get("hasMore") or not cursor:
                break


def remoteok_job(job: dict) -> RawPosting:
    return RawPosting(
        source="remoteok",
        native_id=str(job.get("id")),
        company=job.get("company") or "",
        title=job.get("position") or "",
        description=job.get("description") or "",
        url=job.get("url") or "",
        location=f"Remote, {job['location']}" if job.get("location") else "Remote",
        remote_hint=True,
        posted_on=parse_date(job.get("date")),
        **_pay(job.get("salary_min"), job.get("salary_max"), "USD", "year"),
    )


def remoteok(client: PoliteClient, since: date) -> Iterator[RawPosting]:
    # The first element is the API's legal notice, not a job.
    for job in client.get_json(REMOTEOK)[1:]:
        posting = remoteok_job(job)
        if (not posting.posted_on or posting.posted_on >= since) and roles.classify(posting.title):
            yield posting


def arbeitnow_job(job: dict) -> RawPosting:
    return RawPosting(
        source="arbeitnow",
        native_id=str(job.get("slug")),
        company=job.get("company_name") or "",
        title=job.get("title") or "",
        description=job.get("description") or "",
        url=job.get("url") or "",
        location=job.get("location") or "",
        country_hint=None,
        remote_hint=True if job.get("remote") is True else None,
        posted_on=_epoch(job.get("created_at")),
    )


def arbeitnow(client: PoliteClient, since: date, max_pages: int = 40) -> Iterator[RawPosting]:
    for page in range(1, max_pages + 1):
        jobs = client.get_json(ARBEITNOW, params={"page": page}).get("data") or []
        if not jobs:
            break
        stop = False
        for job in jobs:
            posting = arbeitnow_job(job)
            if posting.posted_on and posting.posted_on < since:
                stop = True
                continue
            if roles.classify(posting.title):
                yield posting
        if stop:
            break


def collect(client: PoliteClient, cfg: dict, name: str, since: date) -> Iterator[RawPosting]:
    """One board's new tech ads, so each board is logged and fails on its own."""
    client.per_host_interval.update(HOST_INTERVAL)
    if name == "himalayas":
        yield from himalayas(client, cfg["himalayas"]["queries"], since, cfg["himalayas"].get("max_pages", 50))
    elif name == "jobicy":
        yield from jobicy(client, cfg["jobicy"]["industries"], since)
    elif name == "remoteok":
        yield from remoteok(client, since)
    elif name == "arbeitnow":
        yield from arbeitnow(client, since)
    else:
        raise ValueError(f"unknown public board {name!r}")
