"""Try candidate job sources from a runner: robots.txt verdict, HTTP status, and the shape of the
answer (top-level keys, item count, the first item's keys and a sample of its text). Stores nothing.
python scripts/source_check.py [NAME ...]   (default: all)"""

from __future__ import annotations

import json
import sys
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from skillmap.http import USER_AGENT

# name: (method, url, headers, json body)
CANDIDATES: dict[str, tuple[str, str, dict, object]] = {
    "getonbrd": ("GET", "https://www.getonbrd.com/api/v0/search/jobs?query=python&per_page=2", {}, None),
    "jobtech_se": ("GET", "https://jobsearch.api.jobtechdev.se/search?q=python&limit=2", {}, None),
    "arbeitsagentur_de": ("GET", "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs?was=python&size=2",
                          {"X-API-Key": "jobboerse-jobsuche"}, None),
    "remotive": ("GET", "https://remotive.com/api/remote-jobs?limit=2", {}, None),
    "wanted_kr": ("GET", "https://www.wanted.co.kr/api/v4/jobs?country=kr&limit=2&job_sort=job.latest_order", {}, None),
    "google": ("GET", "https://careers.google.com/api/v3/search/?q=engineer&page_size=2", {}, None),
    "tiktok": ("POST", "https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts",
               {"website-path": "tiktok"}, {"keyword": "", "limit": 2, "offset": 0}),
    "eightfold_qualcomm": ("GET", "https://careers.qualcomm.com/api/apply/v2/jobs?domain=qualcomm.com&num=2", {}, None),
    "eightfold_amex": ("GET", "https://aexp.eightfold.ai/api/apply/v2/jobs?domain=aexp.com&num=2", {}, None),
    "oracle_jpmc": ("GET", "https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
                    "?onlyData=true&expand=requisitionList&finder=findReqs;siteNumber=CX_1001,limit=2", {}, None),
    "breezy": ("GET", "https://salesflare.breezy.hr/json", {}, None),
    "pinpoint": ("GET", "https://pinpoint.pinpointhq.com/postings.json", {}, None),
    "bamboohr": ("GET", "https://bamboohr.bamboohr.com/careers/list", {}, None),
    "rippling": ("GET", "https://ats.rippling.com/api/v2/board/rippling/jobs?page=0&pageSize=2", {}, None),
    "gem": ("GET", "https://api.gem.com/job_board/v0/gem/job_posts/", {}, None),
    "japan_dev": ("GET", "https://japan-dev.com/api/v1/jobs?page=1", {}, None),
    "workingnomads": ("GET", "https://www.workingnomads.com/api/exposed_jobs/", {}, None),
    "jobvite": ("GET", "https://jobs.jobvite.com/logitech/jobs/feed", {}, None),
    "tiktok_sg": ("POST", "https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts",
                  {"website-path": "tiktok"}, {"keyword": "engineer", "limit": 1, "offset": 0, "location_code_list": []}),
    "bytedance": ("POST", "https://jobs.bytedance.com/api/v1/search/job/posts",
                  {"website-path": "en"}, {"keyword": "", "limit": 1, "offset": 0}),
    "oracle_jpmc_detail": ("GET", "https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
                           "?expand=all&onlyData=true&finder=ById;Id=%22210580374%22,siteNumber=CX_1001", {}, None),
}


def robots_ok(client: httpx.Client, url: str) -> str:
    parts = urlparse(url)
    try:
        r = client.get(f"{parts.scheme}://{parts.netloc}/robots.txt")
    except httpx.HTTPError as exc:
        return f"robots ? ({type(exc).__name__})"
    rp = RobotFileParser()
    rp.parse(r.text.splitlines() if r.status_code == 200 else [])
    return "robots ok" if rp.can_fetch(USER_AGENT, url) else "ROBOTS DISALLOW"


def shape(data: object) -> str:
    items, where = None, ""
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                items, where = value, key
                break
            if isinstance(value, dict):
                for k2, v2 in value.items():
                    if isinstance(v2, list) and v2 and isinstance(v2[0], dict):
                        items, where = v2, f"{key}.{k2}"
                        break
    head = f"keys={list(data)[:12]}" if isinstance(data, dict) else f"list[{len(data)}]"
    if not items:
        return head
    first = items[0]
    text = max((str(v) for v in first.values() if isinstance(v, str)), key=len, default="")
    return f"{head} items@{where or '.'}={len(items)} first_keys={list(first)[:25]} longest_text={len(text)}c: {text[:160]!r}"


dump = "--dump" in sys.argv
names = [a for a in sys.argv[1:] if a != "--dump"] or list(CANDIDATES)
with httpx.Client(headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=30,
                  follow_redirects=True) as client:
    for name in names:
        method, url, headers, body = CANDIDATES[name]
        print(f"=== {name}: {robots_ok(client, url)}")
        try:
            r = client.request(method, url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            print("   ", type(exc).__name__, exc)
            continue
        print("   ", r.status_code, r.headers.get("content-type"), len(r.content), "bytes")
        try:
            data = r.json()
            print("   ", shape(data))
            if dump:
                print(json.dumps(data, ensure_ascii=False)[:3500])
        except (json.JSONDecodeError, ValueError):
            print("   ", repr(r.text[:300]))
