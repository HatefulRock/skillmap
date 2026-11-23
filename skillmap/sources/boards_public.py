"""Public job-board APIs that ask only for a credit link: Himalayas, Jobicy, RemoteOK, Remotive and
Working Nomads (remote jobs worldwide) and Arbeitnow (Europe, mostly Germany).

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
from ._util import dig, parse_date

log = logging.getLogger(__name__)

HIMALAYAS = "https://himalayas.app/jobs/api/search"
JOBICY = "https://jobicy.com/api/v2/remote-jobs"
REMOTEOK = "https://remoteok.com/api"
ARBEITNOW = "https://www.arbeitnow.com/api/job-board-api"
# Remotive asks for at most a few requests a day; one per category per weekly run is well under.
REMOTIVE = "https://remotive.com/api/remote-jobs"
WORKING_NOMADS = "https://www.workingnomads.com/api/exposed_jobs/"
GETONBRD = "https://www.getonbrd.com/api/v0/search/jobs"
HOST_INTERVAL = {"himalayas.app": 1.0, "jobicy.com": 2.0, "remoteok.com": 2.0, "www.arbeitnow.com": 1.0,
                 "remotive.com": 5.0, "www.workingnomads.com": 2.0, "www.getonbrd.com": 1.0}

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


def himalayas(client: PoliteClient, queries: list[str], since: date, max_pages: int = 150) -> Iterator[RawPosting]:
    """Search results, newest first (20 a page, 250 pages at most): each query stops at a page ending before `since`."""
    seen: set[str] = set()
    for query in queries:
        for page in range(1, max_pages + 1):
            jobs = client.get_json(HIMALAYAS, params={"q": query, "sort": "recent", "page": page}).get("jobs") or []
            postings = [himalayas_job(job) for job in jobs]
            for posting in postings:
                if (posting.posted_on and posting.posted_on < since) or posting.native_id in seen:
                    continue
                if roles.classify(posting.title):
                    seen.add(posting.native_id)
                    yield posting
            # Featured ads sit at the top of page 1 whatever their age, so the page's last ad
            # says whether the rest are older.
            if not postings or (postings[-1].posted_on and postings[-1].posted_on < since):
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


def remotive_job(job: dict) -> RawPosting:
    where = str(job.get("candidate_required_location") or "").strip()
    return RawPosting(
        source="remotive",
        native_id=str(job.get("id")),
        company=job.get("company_name") or "",
        title=job.get("title") or "",
        description=job.get("description") or "",
        url=job.get("url") or "",
        location=f"Remote, {where}" if where and where.lower() not in ("worldwide", "anywhere") else "Remote",
        remote_hint=True,
        posted_on=parse_date(job.get("publication_date")),
    )


def remotive(client: PoliteClient, categories: list[str], since: date) -> Iterator[RawPosting]:
    seen: set[str] = set()
    for category in categories:
        for job in client.get_json(REMOTIVE, params={"category": category}).get("jobs") or []:
            posting = remotive_job(job)
            if posting.native_id in seen or (posting.posted_on and posting.posted_on < since):
                continue
            if roles.classify(posting.title):
                seen.add(posting.native_id)
                yield posting


def working_nomads_job(job: dict) -> RawPosting:
    where = str(job.get("location") or "").strip()
    return RawPosting(
        source="workingnomads",
        native_id=str(job.get("url") or ""),
        company=job.get("company_name") or "",
        title=job.get("title") or "",
        description=job.get("description") or "",
        url=job.get("url") or "",
        location=f"Remote, {where}" if where and where.lower() not in ("anywhere", "worldwide") else "Remote",
        remote_hint=True,
        posted_on=parse_date(job.get("pub_date")),
    )


def working_nomads(client: PoliteClient, since: date) -> Iterator[RawPosting]:
    for job in client.get_json(WORKING_NOMADS) or []:
        posting = working_nomads_job(job)
        if posting.native_id and (not posting.posted_on or posting.posted_on >= since) and roles.classify(posting.title):
            yield posting


def getonbrd_job(job: dict) -> RawPosting:
    a = job.get("attributes") or {}
    company = dig(a, "company", "data", "attributes", "name") or ""
    countries = [str(c) for c in a.get("countries") or [] if c]
    remote = bool(a.get("remote")) or str(a.get("remote_modality") or "").startswith("fully_remote")
    where = ", ".join(countries) or str(a.get("city") or "")
    low, high = a.get("min_salary"), a.get("max_salary")
    return RawPosting(
        source="getonbrd",
        native_id=str(job.get("id")),
        company=str(company),
        title=a.get("title") or "",
        description="\n\n".join(str(a[k]) for k in ("description", "projects", "functions", "desirable") if a.get(k)),
        url=dig(job, "links", "public_url") or f"https://www.getonbrd.com/jobs/{job.get('id')}",
        location=(f"Remote, {where}" if where else "Remote") if remote else where,
        remote_hint=True if remote else None,
        posted_on=parse_date(a["published_at"] * 1000) if isinstance(a.get("published_at"), (int, float))
        else parse_date(a.get("published_at")),
        salary_min=float(low) if isinstance(low, (int, float)) and low > 0 else None,
        salary_max=float(high) if isinstance(high, (int, float)) and high > 0 else None,
        salary_currency="USD" if isinstance(low, (int, float)) and low > 0 else None,
        salary_period="month" if isinstance(low, (int, float)) and low > 0 else None,
    )


def getonbrd(client: PoliteClient, queries: list[str], since: date, per_page: int = 100,
             max_pages: int = 10) -> Iterator[RawPosting]:
    """Get on Board (Latin America): a search per query, newest first, until ads predate `since`."""
    seen: set[str] = set()
    for query in queries:
        for page in range(1, max_pages + 1):
            body = client.get_json(GETONBRD, params={"query": query, "per_page": per_page, "page": page,
                                                     "expand": '["company"]'})
            jobs = body.get("data") or []
            old = 0
            for job in jobs:
                posting = getonbrd_job(job)
                if posting.posted_on and posting.posted_on < since:
                    old += 1
                    continue
                if posting.native_id not in seen and roles.classify(posting.title):
                    seen.add(posting.native_id)
                    yield posting
            total = dig(body, "meta", "total_pages")
            if len(jobs) < per_page or old == len(jobs) or (isinstance(total, int) and page >= total):
                break


def collect(client: PoliteClient, cfg: dict, name: str, since: date) -> Iterator[RawPosting]:
    """One board's new tech ads, so each board is logged and fails on its own."""
    client.per_host_interval.update(HOST_INTERVAL)
    if name == "himalayas":
        yield from himalayas(client, cfg["himalayas"]["queries"], since, cfg["himalayas"].get("max_pages", 150))
    elif name == "jobicy":
        yield from jobicy(client, cfg["jobicy"]["industries"], since)
    elif name == "remoteok":
        yield from remoteok(client, since)
    elif name == "arbeitnow":
        yield from arbeitnow(client, since)
    elif name == "remotive":
        yield from remotive(client, (cfg.get("remotive") or {}).get("categories") or ["software-development"], since)
    elif name == "workingnomads":
        yield from working_nomads(client, since)
    elif name == "getonbrd":
        yield from getonbrd(client, (cfg.get("getonbrd") or {}).get("queries") or ["python"], since)
    else:
        raise ValueError(f"unknown public board {name!r}")
