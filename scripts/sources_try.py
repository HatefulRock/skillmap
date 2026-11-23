"""Run a few collectors against the real sites with small limits and print what they parse.
python scripts/sources_try.py [NAME ...]   (tiktok, oracle, getonbrd, remotive, workingnomads, gem, apple)
Stores nothing."""

from __future__ import annotations

import itertools
import sys
from datetime import date, timedelta

from skillmap.http import PoliteClient
from skillmap.sources import apple, gem, oracle_hcm, tiktok
from skillmap.sources import boards_public as bp

client = PoliteClient(min_interval=1.5)
since = date.today() - timedelta(days=30)
runs = {
    "tiktok": lambda: tiktok.collect(client, max_postings=100),
    "oracle": lambda: oracle_hcm.collect(client, "jpmc.fa.oraclecloud.com/CX_1001", "JPMorgan Chase", pages_per_query=1,
                                         max_new_texts=3),
    "getonbrd": lambda: bp.getonbrd(client, ["python"], since, per_page=20, max_pages=1),
    "remotive": lambda: bp.remotive(client, ["software-development"], since),
    "workingnomads": lambda: bp.working_nomads(client, since),
    "gem": lambda: gem.collect(client, "gem", "Gem"),
    "apple": lambda: apple.collect(client, "en-us", "Apple", max_pages=15, max_new_texts=4),
}
if "raw" in sys.argv:
    data = client.post_json(tiktok.API, {"keyword": "", "limit": 2, "offset": 0}, headers=tiktok.HEADERS)["data"]
    print("tiktok count", data.get("count"), "city_info", data["job_post_list"][0].get("city_info"))
    print("tiktok job_post_info", data["job_post_list"][0].get("job_post_info"))
    body = client.get_json(bp.GETONBRD, params={"query": "python", "per_page": 1, "expand": '["company"]'})
    job = body["data"][0]
    print("getonbrd meta", body.get("meta"), "links", job.get("links"))
    print("getonbrd attrs", {k: (v if not isinstance(v, str) or len(v) < 80 else v[:80]) for k, v in job["attributes"].items()
                             if k not in ("description", "projects", "functions", "benefits", "desirable")})
for name in [a for a in sys.argv[1:] if a != "raw"] or list(runs):
    try:
        out = list(itertools.islice(runs[name](), 40))
    except Exception as exc:  # noqa: BLE001 - a diagnostic script reports every failure
        print(f"=== {name}: FAILED {type(exc).__name__}: {exc}")
        continue
    with_text = sum(1 for p in out if len(p.description) > 200)
    print(f"=== {name}: {len(out)} postings, {with_text} with text")
    for p in out[:4]:
        print(f"  - {p.native_id} | {p.company} | {p.title} | {p.location} | {p.country_hint} | {p.posted_on} | "
              f"{p.salary_min}-{p.salary_max} {p.salary_currency}/{p.salary_period} | {len(p.description)} chars")
