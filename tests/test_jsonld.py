"""Careers sites with schema.org JobPosting markup."""

import json

import httpx
import pytest

from skillmap.http import PoliteClient
from skillmap.sources import jsonld

LISTING = """<html><body>
<a href="/careers/jobs/123-ml-engineer">ML Engineer</a>
<a href="https://acme.example/careers/jobs/456-data-engineer?src=list">Data Engineer</a>
<a href="/careers/jobs/123-ml-engineer">again</a>
<a href="/about">About</a><a href="https://other.example/careers/jobs/9">elsewhere</a>
</body></html>"""


def page(title, extra=None):
    job = {"@context": "https://schema.org", "@type": "JobPosting", "title": title,
           "description": "<p>Python, PyTorch and Kubernetes.</p>", "datePosted": "2026-11-10",
           "jobLocation": {"@type": "Place", "address": {"addressLocality": "Singapore", "addressCountry": "SG"}},
           "baseSalary": {"currency": "SGD", "value": {"minValue": 8000, "maxValue": 12000, "unitText": "MONTH"}},
           **(extra or {})}
    return f'<html><script type="application/ld+json">{json.dumps({"@graph": [{"@type": "Organization"}, job]})}</script></html>'


def client_for(handler):
    client = PoliteClient(min_interval=0, transport=httpx.MockTransport(handler), retries=0)
    client.sleep = lambda s: None
    return client


def test_links_from_listing_and_sitemap():
    base = "https://acme.example/careers"
    assert jsonld.job_links(LISTING, base, r"/careers/jobs/") == [
        "https://acme.example/careers/jobs/123-ml-engineer", "https://acme.example/careers/jobs/456-data-engineer"]
    sitemap = "<urlset><url><loc>https://acme.example/jobs/1</loc></url><url><loc>https://acme.example/blog</loc></url></urlset>"
    assert jsonld.job_links(sitemap, "https://acme.example/sitemap.xml", jsonld.DEFAULT_LINKS) == ["https://acme.example/jobs/1"]


def test_parse_job_posting():
    job = jsonld.job_posting(page("Senior ML Engineer"))
    raw = jsonld.parse(job, "https://acme.example/careers/jobs/1", "Acme")
    assert raw.title == "Senior ML Engineer" and raw.country_hint == "SG" and raw.location == "Singapore, SG"
    assert (raw.salary_min, raw.salary_max, raw.salary_currency, raw.salary_period) == (8000, 12000, "SGD", "month")
    assert raw.posted_on.isoformat() == "2026-11-10"
    assert jsonld.job_posting("<html>no markup</html>") is None


def test_collect_reads_only_new_pages_and_respects_robots():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if url.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nDisallow: /careers/jobs/456")
        if url.endswith("/careers"):
            return httpx.Response(200, text=LISTING)
        return httpx.Response(200, text=page("ML Engineer"))

    known = {jsonld.native_id("https://acme.example/careers/jobs/999")}
    out = list(jsonld.collect(client_for(handler), "https://acme.example/careers", "Acme", known=known,
                              links=r"/careers/jobs/"))
    assert [r.title for r in out] == ["ML Engineer"]  # 456 is disallowed by robots.txt
    assert not any("456" in c for c in calls)

    known = {jsonld.native_id("https://acme.example/careers/jobs/123-ml-engineer")}
    out = list(jsonld.collect(client_for(handler), "https://acme.example/careers", "Acme", known=known,
                              links=r"/careers/jobs/"))
    assert [r.title for r in out] == [""]  # known: last-seen refreshed, page not read again


def test_collect_refuses_a_disallowed_listing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="User-agent: *\nDisallow: /")

    with pytest.raises(PermissionError):
        list(jsonld.collect(client_for(handler), "https://acme.example/careers", "Acme"))
