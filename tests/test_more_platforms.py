from datetime import date

import httpx

from skillmap import discover
from skillmap.http import PoliteClient
from skillmap.sources import personio, recruitee, teamtailor

RECRUITEE = {"offers": [
    {"id": 7, "title": "Data Engineer", "description": "<p>Python and Airflow</p>", "requirements": "<ul><li>SQL</li></ul>",
     "careers_url": "https://bunq.recruitee.com/o/data-engineer", "city": "Amsterdam", "country": "Netherlands",
     "country_code": "nl", "remote": False, "published_at": "2026-11-02 10:00:00 UTC", "company_name": "bunq",
     "status": "published"},
    {"id": 8, "title": "Old", "status": "archived"},
]}

PERSONIO = """<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
<position>
    <id>2885178</id>
    <subcompany>Personio SE</subcompany>
    <office>Berlin</office>
    <name>Backend Engineer (d/f/m)</name>
    <jobDescriptions>
        <jobDescription><name>The Role</name><value><![CDATA[<p>Kotlin and AWS</p>]]></value></jobDescription>
        <jobDescription><name>You bring</name><value><![CDATA[<ul><li>PostgreSQL</li></ul>]]></value></jobDescription>
    </jobDescriptions>
    <createdAt>2026-10-30T09:12:00+00:00</createdAt>
</position>
</workzag-jobs>"""

TEAMTAILOR = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:tt="https://teamtailor.com/locations">
  <channel>
    <title>Storytel</title>
    <item>
      <title>Senior Data Engineer</title>
      <description>&lt;p&gt;BigQuery, Terraform, Airflow&lt;/p&gt;</description>
      <pubDate>Mon, 16 Nov 2026 08:00:00 +0100</pubDate>
      <link>https://storytel.teamtailor.com/jobs/123-senior-data-engineer</link>
      <guid>https://storytel.teamtailor.com/jobs/123-senior-data-engineer</guid>
      <remoteStatus>hybrid</remoteStatus>
      <tt:locations><tt:location><tt:city>Stockholm</tt:city><tt:country>Sweden</tt:country></tt:location></tt:locations>
    </item>
  </channel>
</rss>"""


def test_recruitee_joins_description_and_requirements_and_skips_unpublished():
    jobs = list(recruitee.parse(RECRUITEE, "Bunq", "fintech"))
    assert len(jobs) == 1
    job = jobs[0]
    assert job.posting_id == "recruitee:7" and job.country_hint == "NL" and job.remote_hint is None
    assert "Airflow" in job.description and "SQL" in job.description
    assert job.posted_on == date(2026, 11, 2) and job.location == "Amsterdam, Netherlands"


def test_personio_reads_every_description_section():
    job = next(personio.parse(PERSONIO, "Personio", None, "personio"))
    assert (job.posting_id, job.title, job.location) == ("personio:2885178", "Backend Engineer (d/f/m)", "Berlin")
    assert "Kotlin" in job.description and "PostgreSQL" in job.description
    assert job.url == "https://personio.jobs.personio.de/job/2885178" and job.posted_on == date(2026, 10, 30)


def test_teamtailor_reads_the_rss_feed():
    job = next(teamtailor.parse(TEAMTAILOR, "Storytel"))
    assert job.title == "Senior Data Engineer" and "BigQuery" in job.description
    assert job.location == "Stockholm, Sweden" and job.posted_on == date(2026, 11, 16)


def test_feeds_with_entities_are_refused():
    evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>'
    assert list(teamtailor.parse(evil, "X")) == []
    assert list(personio.parse(evil.replace("rss", "workzag-jobs"), "X")) == []


def client_for(routes):
    def handler(request):
        host = request.url.host
        if host in routes:
            status, body = routes[host]
            return httpx.Response(status, json=body) if isinstance(body, dict) else httpx.Response(status, text=body)
        return httpx.Response(404, text="<!DOCTYPE html><html>not found</html>")
    client = PoliteClient(min_interval=0, transport=httpx.MockTransport(handler), retries=0)
    client.sleep = lambda s: None
    return client


def test_new_platform_probes_find_boards_and_reject_missing_ones():
    client = client_for({"bunq.recruitee.com": (200, RECRUITEE), "personio.jobs.personio.de": (200, PERSONIO),
                         "storytel.teamtailor.com": (200, TEAMTAILOR)})
    assert discover.probe_recruitee(client, "bunq") == (2, "bunq")
    assert discover.probe_personio(client, "personio") == (1, "Personio SE")
    assert discover.probe_teamtailor(client, "storytel") == (1, "Storytel")
    for probe in (discover.probe_recruitee, discover.probe_personio, discover.probe_teamtailor):
        assert probe(client, "nobody") is None
    assert {"recruitee", "personio", "teamtailor"} <= set(discover.DEFAULT_ATS) <= set(discover.PROBES)


def test_crawl_tokens_read_the_subdomain_on_wildcard_hosts():
    lines = ['{"url": "https://bunq.recruitee.com/o/data-engineer"}',
             '{"url": "https://recruitee.com/pricing"}',
             '{"url": "https://www.recruitee.com/"}',
             '{"url": "https://a.b.recruitee.com/"}',
             '{"url": "https://acme.jobs.personio.de/job/1"}']
    assert discover.crawl_tokens(lines, "*.recruitee.com") == {"bunq"}
    assert discover.crawl_tokens(lines, "*.jobs.personio.de") == {"acme"}
