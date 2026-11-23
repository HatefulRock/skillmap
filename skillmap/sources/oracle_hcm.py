"""Oracle Recruiting Cloud careers sites (`{tenant}.fa.oraclecloud.com`), the public REST API
the careers pages load. Used by large banks and insurers (JPMorgan Chase, among others).

The token is "host/siteNumber", e.g. "jpmc.fa.oraclecloud.com/CX_1001". The list gives title,
place and date but no text, so text is a second request, made only for new technical titles
(at most `max_new_texts` a run). Only technical searches are run, newest first.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..extract import roles
from ..http import PoliteClient
from ..models import RawPosting
from ._util import parse_date

QUERIES = ("software engineer", "machine learning", "data scientist", "data engineer", "AI engineer", "developer")
PAGE = 25


def split_token(token: str) -> tuple[str, str]:
    host, _, site = token.partition("/")
    return host, site or "CX_1"


def parse(job: dict, detail: dict | None, host: str, site: str, company: str, sector: str | None = None) -> RawPosting:
    info = {**job, **(detail or {})}
    text = "\n\n".join(filter(None, [info.get("ExternalDescriptionStr"), info.get("ExternalResponsibilitiesStr"),
                                     info.get("ExternalQualificationsStr")])) or info.get("ShortDescriptionStr") or ""
    code = str(info.get("PrimaryLocationCountry") or "")
    workplace = str(info.get("WorkplaceTypeCode") or info.get("WorkplaceType") or "").lower()
    return RawPosting(
        source="oracle_hcm",
        native_id=f"{host.split('.')[0]}:{info.get('Id')}",
        company=company,
        title=info.get("Title") or "",
        description=text,
        url=f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{info.get('Id')}",
        location=info.get("PrimaryLocation") or "",
        country_hint=code if len(code) == 2 and code.isupper() else None,
        remote_hint=True if "remote" in workplace else None,
        posted_on=parse_date(info.get("PostedDate") or info.get("ExternalPostedStartDate")),
        sector_hint=sector,
    )


def collect(client: PoliteClient, token: str, company: str, sector: str | None = None, region: str | None = None,
            known: set[str] | None = None, pages_per_query: int = 8, max_new_texts: int = 150) -> Iterator[RawPosting]:
    host, site = split_token(token)
    base = f"https://{host}/hcmRestApi/resources/latest"
    known, seen, fetched = known or set(), set(), 0
    for query in QUERIES:
        for page in range(pages_per_query):
            finder = (f'findReqs;siteNumber={site},keyword="{query}",limit={PAGE},offset={page * PAGE},'
                      "sortBy=POSTING_DATES_DESC")
            payload = client.get_json(f"{base}/recruitingCEJobRequisitions",
                                      params={"onlyData": "true", "expand": "requisitionList", "finder": finder})
            items = payload.get("items") or [{}]
            jobs = items[0].get("requisitionList") or []
            for job in jobs:
                job_id = str(job.get("Id"))
                if job_id in seen:
                    continue
                seen.add(job_id)
                native = f"{host.split('.')[0]}:{job_id}"
                detail = None
                if native not in known and roles.classify(job.get("Title") or ""):
                    if fetched >= max_new_texts:
                        continue
                    found = client.get_json(f"{base}/recruitingCEJobRequisitionDetails", params={
                        "expand": "all", "onlyData": "true", "finder": f'ById;Id="{job_id}",siteNumber={site}'})
                    detail = (found.get("items") or [None])[0]
                    fetched += 1
                yield parse(job, detail, host, site, company, sector)
            if len(jobs) < PAGE:
                break
