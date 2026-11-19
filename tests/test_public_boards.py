from datetime import date

from skillmap.sources import boards_public as bp


class Fake:
    """Answers get_json from a dict keyed by (url, frozen params)."""

    def __init__(self, pages):
        self.pages = pages
        self.per_host_interval = {}
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        return self.pages(url, params or {})


def test_himalayas_keeps_tech_ads_until_since_and_reads_pay_and_level():
    jobs = [
        {"guid": "f", "title": "Featured Old Backend Engineer", "pubDate": "1700000000"},
        {"guid": "a", "title": "Senior ML Engineer", "companyName": "Acme", "description": "<p>PyTorch</p>",
         "pubDate": "1795000000", "minSalary": 150000, "maxSalary": 180000, "currency": "USD",
         "salaryPeriod": "yearly", "seniority": ["Senior"], "locationRestrictions": ["Singapore"]},
        {"guid": "b", "title": "Account Executive", "companyName": "Acme", "pubDate": "1795000000"},
        {"guid": "c", "title": "Backend Engineer", "companyName": "Old", "pubDate": "1700000000"},
    ]
    client = Fake(lambda url, p: {"jobs": jobs if p["page"] == 1 else []})
    out = list(bp.himalayas(client, ["ml"], date(2026, 10, 1)))
    assert [r.native_id for r in out] == ["a"]
    a = out[0]
    assert (a.salary_min, a.salary_max, a.salary_currency, a.salary_period) == (150000, 180000, "USD", "year")
    assert a.seniority_hint == "senior" and a.remote_hint and a.location == "Singapore"
    assert len(client.calls) == 1  # the page ended on an old ad, so the query stopped


def test_jobicy_follows_the_cursor_and_remoteok_skips_the_notice():
    page1 = {"jobs": [{"id": 1, "jobTitle": "Data Engineer", "pubDate": "2026-11-10", "jobGeo": "USA",
                       "salaryMin": "", "salaryPeriod": "yearly"}], "hasMore": True, "nextCursor": "x"}
    page2 = {"jobs": [{"id": 2, "jobTitle": "Python Developer", "pubDate": "2026-11-09"}], "hasMore": False}
    client = Fake(lambda url, p: page2 if p.get("cursor") else page1)
    out = list(bp.jobicy(client, ["dev"], date(2026, 11, 1)))
    assert [r.native_id for r in out] == ["1", "2"] and out[0].salary_min is None
    notice = {"legal": "credit us"}
    client = Fake(lambda url, p: [notice, {"id": 9, "position": "Frontend Engineer", "date": "2026-11-17T00:00:00+00:00",
                                           "salary_min": 0, "salary_max": 0}])
    assert [r.title for r in bp.remoteok(client, date(2026, 11, 1))] == ["Frontend Engineer"]


def test_arbeitnow_stops_at_an_empty_page_and_reads_epoch_dates():
    jobs = [{"slug": "s1", "title": "DevOps Engineer (m/w/d)", "created_at": 1795000000, "remote": True,
             "location": "Berlin"}]
    client = Fake(lambda url, p: {"data": jobs if p["page"] == 1 else []})
    out = list(bp.arbeitnow(client, date(2026, 11, 1)))
    assert out[0].posted_on == date(2026, 11, 18) and out[0].remote_hint and len(client.calls) == 2
