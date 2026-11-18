"""Workday career sites and Amazon's careers search."""

import json
from datetime import date

import httpx

from skillmap.http import PoliteClient
from skillmap.sources import amazon, workday

TODAY = date(2026, 11, 18)


def client_for(handler):
    client = PoliteClient(min_interval=0, transport=httpx.MockTransport(handler), retries=0)
    client.sleep = lambda s: None
    return client


def wd_job(i, title, posted="Posted 3 Days Ago", place="Singapore"):
    return {"title": title, "externalPath": f"/job/Singapore/{title.replace(' ', '-')}_R{i}",
            "locationsText": place, "postedOn": posted, "bulletFields": ["Singapore"]}


def test_workday_token_and_dates():
    assert workday.parts("db.wd3/DBWebsite") == ("db.wd3.myworkdayjobs.com", "db", "DBWebsite")
    assert workday.posted("Posted Today", TODAY) == TODAY
    assert workday.posted("Posted Yesterday", TODAY) == date(2026, 11, 17)
    assert workday.posted("Posted 3 Days Ago", TODAY) == date(2026, 11, 15)
    assert workday.posted("Posted 30+ Days Ago", TODAY) is None


def test_workday_fetches_text_only_for_new_tech_postings():
    jobs = [wd_job(1, "Machine Learning Engineer"), wd_job(2, "Relationship Manager", place="2 Locations"),
            wd_job(3, "Data Engineer"), wd_job(4, "Software Engineer")]
    details, bodies = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST" and url == "https://db.wd3.myworkdayjobs.com/wday/cxs/db/DBWebsite/jobs":
            body = json.loads(request.content)
            bodies.append(body)
            return httpx.Response(200, json={"total": len(jobs), "jobPostings": jobs[body["offset"]:body["offset"] + 20]})
        if "/wday/cxs/db/DBWebsite/job/" in url:
            details.append(url)
            return httpx.Response(200, json={
                "jobPostingInfo": {"title": "Machine Learning Engineer", "jobDescription": "<p>PyTorch and Python</p>",
                                   "location": "Singapore", "startDate": "2026-11-10",
                                   "country": {"descriptor": "Singapore", "alpha2Code": "SG"},
                                   "externalUrl": "https://db.wd3.myworkdayjobs.com/DBWebsite/job/x"},
                "hiringOrganization": {"name": "Deutsche Bank AG"}})
        return httpx.Response(404)

    out = list(workday.collect(client_for(handler), "db.wd3/DBWebsite", "Deutsche Bank", "fintech",
                               known={"R3"}, max_new_texts=1, today=TODAY))
    # R1: new tech, text fetched. R2: not tech, metadata only. R3: already stored. R4: over the text budget, skipped.
    assert [p.native_id for p in out] == ["db:R1", "db:R2", "db:R3"]
    assert len(details) == 1 and bodies[0] == {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
    ml, rm, de = out
    assert (ml.company, ml.description, ml.country_hint, ml.posted_on) == ("Deutsche Bank", "<p>PyTorch and Python</p>",
                                                                          "SG", date(2026, 11, 10))
    assert (rm.location, rm.description, rm.posted_on, rm.company) == ("", "", date(2026, 11, 15), "Deutsche Bank")
    assert rm.url == "https://db.wd3.myworkdayjobs.com/DBWebsite/job/Singapore/Relationship-Manager_R2"


def test_workday_searches_tech_keywords_past_the_page_limit():
    searches = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        searches.append((body["searchText"], body["offset"]))
        n = body["offset"]
        tag = body["searchText"] or "all"
        page = [wd_job(f"{tag}{n + i}", "Teller") for i in range(20)]
        return httpx.Response(200, json={"total": 5000 if body["offset"] == 0 else 0, "jobPostings": page})

    out = list(workday.listings(client_for(handler), "big.wd1/External", max_postings=40))
    assert searches[:2] == [("", 0), ("", 20)]
    assert {s for s, _ in searches[2:]} == set(workday.TECH_QUERIES)
    assert len(out) == 40 + 20 * len(workday.TECH_QUERIES)  # each keyword search capped at a third, one page here


def test_amazon_reads_tech_categories_with_text():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        offset = int(request.url.params["offset"])
        jobs = [{"id_icims": str(offset + i), "title": "Software Development Engineer", "description": "Build things",
                 "basic_qualifications": "- 3+ years of Java", "preferred_qualifications": "- AWS",
                 "normalized_location": "Singapore, SGP", "posted_date": "November 10, 2026",
                 "job_path": f"/en/jobs/{offset + i}/sde"} for i in range(100 if offset == 0 else 30)]
        return httpx.Response(200, json={"hits": 130, "jobs": jobs})

    out = list(amazon.collect(client_for(handler)))
    assert len(out) == 130 and len(calls) == 2
    assert calls[0].params.get_list("category[]") == list(amazon.CATEGORIES)
    first = out[0]
    assert (first.source, first.native_id, first.company, first.posted_on) == ("amazon", "0", "Amazon", date(2026, 11, 10))
    assert "Basic qualifications\n- 3+ years of Java" in first.description
    assert first.url == "https://www.amazon.jobs/en/jobs/0/sde" and first.location == "Singapore, SGP"
