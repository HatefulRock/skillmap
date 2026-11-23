from datetime import date

from skillmap.sources import boards_public as bp
from skillmap.sources import gem, oracle_hcm, tiktok


class Fake:
    def __init__(self, answer):
        self.answer = answer
        self.per_host_interval = {}
        self.calls = []

    def get_json(self, url, params=None, **kw):
        self.calls.append((url, params))
        return self.answer(url, params or {})

    def post_json(self, url, payload, **kw):
        self.calls.append((url, payload, kw.get("headers")))
        return self.answer(url, payload)


def test_remotive_dedupes_across_categories_and_drops_old_and_non_tech():
    jobs = [{"id": 1, "title": "Senior Python Engineer", "company_name": "A", "publication_date": "2026-11-20T10:00:00",
             "candidate_required_location": "Europe"},
            {"id": 2, "title": "Account Executive", "publication_date": "2026-11-20T10:00:00"},
            {"id": 3, "title": "Data Engineer", "publication_date": "2026-01-01T10:00:00"}]
    client = Fake(lambda url, p: {"jobs": jobs})
    out = list(bp.remotive(client, ["software-development", "data"], date(2026, 11, 1)))
    assert [(r.native_id, r.location) for r in out] == [("1", "Remote, Europe")]
    assert len(client.calls) == 2


def test_working_nomads_keeps_new_tech_ads():
    client = Fake(lambda url, p: [
        {"url": "https://w/1", "title": "ML Engineer", "company_name": "B", "pub_date": "2026-11-21T00:00:00",
         "location": "Anywhere"},
        {"url": "https://w/2", "title": "Recruiter", "pub_date": "2026-11-21T00:00:00"}])
    out = list(bp.working_nomads(client, date(2026, 11, 1)))
    assert [(r.native_id, r.location, r.remote_hint) for r in out] == [("https://w/1", "Remote", True)]


def test_getonbrd_reads_company_text_and_stops_at_old_pages():
    def job(i, title, published):
        return {"id": f"j{i}", "attributes": {
            "title": title, "description": "<p>Python</p>", "projects": "RAG", "functions": "build", "desirable": "Go",
            "remote": True, "countries": ["Chile"], "published_at": published, "min_salary": 3000, "max_salary": 4000,
            "company": {"data": {"attributes": {"name": "Acme"}}}}, "links": {"public_url": f"https://g/{i}"}}
    new, old = 1795000000, 1700000000
    client = Fake(lambda url, p: {"data": [job(1, "Backend Developer", new), job(2, "Sales Manager", new),
                                           job(3, "Data Engineer", old)]} if p["page"] == 1 else {"data": []})
    out = list(bp.getonbrd(client, ["python"], date(2026, 11, 1), per_page=3))
    [a] = out
    assert (a.company, a.location, a.url, a.salary_min, a.salary_period) == ("Acme", "Remote, Chile", "https://g/1", 3000, "month")
    assert "RAG" in a.description and "Go" in a.description and a.posted_on == date(2026, 11, 18)


def test_gem_reads_a_greenhouse_shaped_list():
    [p] = gem.parse([{"id": 7, "title": "AI Engineer", "content": "LLMs", "absolute_url": "https://jobs.gem.com/x/7",
                      "location": {"name": "New York"}, "first_published_at": "2026-11-10T00:00:00Z"}], "X")
    assert (p.native_id, p.location, p.posted_on) == ("7", "New York", date(2026, 11, 10))
    assert list(gem.parse({"code": 404}, "X")) == []


def test_tiktok_pages_by_offset_keeps_tech_titles_and_reads_city_chain():
    def answer(url, payload):
        if payload["offset"] == 0:
            return {"data": {"count": 51, "job_post_list": [
                {"id": str(i), "title": "Backend Engineer" if i % 2 else "Account Manager", "description": "Go",
                 "requirement": "Kubernetes", "city_info": {"name": "Singapore", "parent": {"name": "Singapore",
                                                                                            "parent": {"name": "Asia"}}}}
                for i in range(50)]}}
        return {"data": {"count": 51, "job_post_list": [{"id": "x", "title": "ML Engineer", "city_info": None}]}}
    client = Fake(answer)
    out = list(tiktok.collect(client))
    assert len(out) == 26 and out[0].location == "Singapore, Asia" and "Kubernetes" in out[0].description
    assert client.calls[0][2] == {"website-path": "tiktok"} and len(client.calls) == 2


def test_oracle_fetches_text_only_for_new_tech_ads():
    listing = {"items": [{"requisitionList": [
        {"Id": "1", "Title": "Software Engineer III", "PrimaryLocationCountry": "US", "PostedDate": "2026-11-20"},
        {"Id": "2", "Title": "Private Client Advisor", "PrimaryLocationCountry": "US"},
        {"Id": "3", "Title": "Data Engineer", "PrimaryLocationCountry": "SG"}]}]}
    detail = {"items": [{"Id": "1", "ExternalDescriptionStr": "<p>Java and AWS</p>", "ExternalQualificationsStr": "Python"}]}

    def answer(url, params):
        return detail if "Details" in url else listing
    client = Fake(answer)
    out = list(oracle_hcm.collect(client, "jpmc.fa.oraclecloud.com/CX_1001", "JPMorgan Chase", known={"jpmc:3"}))
    assert [r.native_id for r in out] == ["jpmc:1", "jpmc:2", "jpmc:3"]
    assert "Python" in out[0].description and out[0].country_hint == "US" and out[2].description == ""
    details = [c for c in client.calls if "Details" in c[0]]
    assert len(details) == 1 and 'Id="1"' in details[0][1]["finder"]
    assert out[0].posted_on == date(2026, 11, 20)


def test_titles_in_other_languages_are_read_as_english():
    from skillmap.extract import roles

    cases = {
        "Ingeniero de Datos": "data", "Softwareentwickler (m/w/d)": "backend", "Java-Entwicklerin": "backend",
        "Científico de Datos": "ai_ml", "Ingénieur Machine Learning": "ai_ml", "Desenvolvedor Python Sênior": "backend",
        "Ingeniera de IA": "ai_ml", "KI-Entwickler": "ai_ml", "Ingegnere del Software": "backend",
        "Arquitecto de Soluciones": "fde", "Liderança Técnica": "other_eng", "データエンジニア": "data",
        "백엔드 개발자": "backend", "Ingénieur Sécurité Cloud": "infra",
        # non-software engineering stays out, in any language
        "Ingeniero Mecánico": None, "Elektroingenieur (m/w/d)": None, "Bauingenieur": None, "Técnico de Mantenimiento": None,
        # English titles keep their word order and family
        "Senior Solution Architect Security Specialist": "fde", "Senior Electrical Engineer Data Centers": "other_eng",
        "Asia Sales Lead": None,
    }
    assert {t: roles.classify(t) for t in cases} == cases


def test_skills_in_other_languages():
    from skillmap.extract.skills import Taxonomy

    tax = Taxonomy.load()
    skills, _ = tax.extract("Ingeniero de Datos", "Experiencia en aprendizaje automático, microservicios y pruebas unitarias.")
    assert {"Machine learning fundamentals", "Microservices", "Unit / integration testing"} <= set(skills)
    skills, _ = tax.extract("Data Scientist", "Erfahrung mit maschinellem Lernen und verteilten Systemen.")
    assert "Machine learning fundamentals" in skills


def test_a_retitled_non_tech_posting_is_fetched_again():
    from skillmap.runner import _known

    class S:
        def known_ids(self, source):
            return {"1", "2", "3"}

        def non_tech_titles(self, source):
            return {"2": "Softwareentwickler (m/w/d)", "3": "Buchhalter"}
    assert _known(S(), "workday") == {"1", "3"}


def test_jsonld_board_takes_the_employer_from_the_page():
    from skillmap.sources import jsonld

    job = {"title": "Backend Engineer", "hiringOrganization": {"@type": "Organization", "name": "PayPay Card"},
           "description": "Go", "jobLocation": {"address": {"addressLocality": "Tokyo", "addressCountry": "JP"}}}
    assert jsonld.parse(job, "https://www.tokyodev.com/companies/x/jobs/y", "*").company == "PayPay Card"
    assert jsonld.parse(job, "https://careers.example.com/1", "Example").company == "Example"


def test_chinese_titles_and_skills():
    from skillmap.extract import roles
    from skillmap.extract.skills import Taxonomy

    assert {t: roles.classify(t) for t in ["后端开发工程师", "算法工程师", "数据分析师", "产品经理"]} == {
        "后端开发工程师": "backend", "算法工程师": "ai_ml", "数据分析师": "data", "产品经理": None}
    skills, _ = Taxonomy.load().extract("算法工程师", "熟悉机器学习，有大模型微调经验。熟悉Python和PyTorch，了解微服务。")
    assert {"Machine learning fundamentals", "LLMs / GenAI (general)", "Fine-tuning", "Python", "PyTorch",
            "Microservices"} <= set(skills)


def test_dashboard_review_fixes():
    from skillmap.extract import roles, terms
    from skillmap.extract.skills import Taxonomy

    tax = Taxonomy.load()
    assert "NLP" not in tax.extract("Backend Engineer", "Salary 20 000 - 28 000 PLN net (B2B).")[0]
    skills, _ = tax.extract("Frontend Engineer", "Strong JS and React. Experience with RL for agents is a plus.")
    assert {"JavaScript", "Reinforcement learning"} <= set(skills)
    assert "JavaScript" not in tax.extract("Engineer", "We use Node.js and Next.js.")[0]
    assert roles.classify("Software Development Engineer II, AWS") == "backend"
    assert roles.classify("SDE II") == "backend"
    assert all(terms.ignored(t) for t in ["GmbH", "SE", "HYBRID", "TN", "AG"])


def test_hn_threads_from_both_accounts():
    from datetime import date

    from skillmap.sources import hn

    hits = {
        "whoishiring": [{"objectID": "3", "created_at": "2015-06-01T14:55:02Z", "title": "Ask HN: Who is hiring? (June 2015)"},
                        {"objectID": "1", "created_at": "2015-03-01T15:04:12Z", "title": "Ask HN: Who is hiring? (March 2015)"}],
        "_whoishiring": [{"objectID": "2", "created_at": "2015-04-01T13:38:36Z", "title": "Ask HN: Who is hiring? (April 2015)"},
                         {"objectID": "9", "created_at": "2015-04-01T13:38:36Z", "title": "Ask HN: Who wants to be hired? (April 2015)"}],
    }
    client = Fake(lambda url, p: {"hits": hits[p["tags"].split("author_", 1)[1]], "nbPages": 1})
    threads = hn.list_threads(client, date(2015, 4, 1))
    assert [t["id"] for t in threads] == ["3", "2"]


def test_pay_slips_are_fixed_or_dropped():
    from skillmap.extract.salary import clean, from_text

    assert clean(140, 245, "USD", "year") == (140_000, 245_000, "USD", "year")  # thousands
    assert clean(195, 415_000, "USD", "year")[0] == 195_000
    assert clean(180_000, 240_000, "SGD", "month")[3] == "year"  # yearly filed as monthly
    assert clean(290_000, 322_000, "USD", "week")[3] == "year"
    assert clean(5_200, 5_800, "EUR", "year")[3] == "month"  # monthly filed as yearly
    assert clean(6_000, 9_000, "SGD", "month") == (6_000, 9_000, "SGD", "month")
    assert clean(1, 1, "SGD", "month") is None
    assert clean(5_000_000, 5_000_000, "USD", "year") is None  # a research grant, not pay
    assert clean(45_000_000, 65_000_000, "CLP", "year")[2] == "CLP"
    assert from_text("Sueldo: $30,000 - $40,000 mensual", "MX")[2:] == ("MXN", "month")
    assert from_text("月薪 ¥30,000-¥40,000", "CN")[2] == "CNY"
    assert from_text("Gehalt: 60.000 € - 75.000 € jährlich", "DE") == (60_000, 75_000, "EUR", "year")


def test_amazon_pay_and_one_name_per_company():
    from skillmap.company_size import classify, display_name
    from skillmap.extract.salary import from_text
    from skillmap.sources import tiktok as tt

    text = "The base salary range for this position is listed below.\n\nUSA, WA, Seattle - 143,700.00 - 194,400.00 USD annually"
    assert from_text(text, "US") == (143_700, 194_400, "USD", "year")
    assert display_name("GOOGLE ASIA PACIFIC PTE. LTD.") == "Google"
    assert display_name("BYTEDANCE PTE. LTD.") == "ByteDance"
    assert display_name("SpaceXSI") == "SpaceX"
    assert display_name("AMAZON WEB SERVICES SINGAPORE PRIVATE LIMITED") == "Amazon"
    assert display_name("Microsoft") == "Microsoft"
    assert display_name("Some Startup") == "Some Startup"
    assert classify("GRABTAXI HOLDINGS PTE. LTD.") == "big_tech"
    assert classify("Roblox") == "midsize"
    job = {"id": 1, "title": "Software Engineer", "job_post_info": {"min_salary": "150000", "max_salary": "250000", "currency": "USD"}}
    assert (tt.parse(job).salary_min, tt.parse(job).salary_period) == (150_000, "year")


def test_eightfold_lists_then_reads_text_for_new_tech_ads():
    from skillmap.sources import eightfold

    def answer(url, params):
        if url.endswith("/jobs"):
            return {"count": 3, "positions": [
                {"id": 1, "name": "Senior Software Engineer, Streaming", "locations": ["Los Gatos, California"], "t_create": 1795392000},
                {"id": 2, "name": "Communications Manager", "location": "Seoul"},
                {"id": 3, "name": "Machine Learning Engineer", "location": "Remote, US", "work_location_option": "remote"}]}
        return {"job_description": f"<p>Text for {url.rsplit('/', 1)[1]}</p>"}

    client = Fake(answer)
    posts = list(eightfold.collect(client, "explore.jobs.netflix.net|netflix.com", "Netflix", known={"netflix:3"}))
    assert [p.native_id for p in posts] == ["netflix:1", "netflix:3"]
    assert posts[0].description == "<p>Text for 1</p>" and posts[0].posted_on.year == 2026
    assert posts[1].description == "" and posts[1].remote_hint
    assert sum(1 for c in client.calls if not c[0].endswith("/jobs")) == 1  # one text request, for the new ad


def test_apple_reads_search_pages_then_tech_detail_pages():
    import json as _json

    import httpx

    from skillmap.http import PoliteClient
    from skillmap.sources import apple

    def page(data: dict, body: str = "") -> str:
        literal = _json.dumps(_json.dumps(data))[1:-1]
        return f'<html>{body}<script>window.__staticRouterHydrationData = JSON.parse("{literal}");</script></html>'

    search = page({"loaderData": {}}, '<a href="/en-us/details/200644467-0836/senior-machine-learning-engineer-ads">x</a>'
                  '<a href="/en-us/details/114438158/us-specialist-seasonal">y</a>'
                  '<a href="/en-us/details/200000001-0836/software-engineer-maps">z</a>')
    job = {"jobsData": {"postingTitle": "Senior Machine Learning Engineer - Ads", "jobSummary": "Build ranking models.",
                        "minimumQualifications": "Python, PyTorch", "postingDate": "Nov 20, 2026",
                        "locations": [{"city": "Cupertino", "stateProvince": "California", "countryName": "United States",
                                       "countryID": "iso-country-USA"}]}}
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "/search" in request.url.path:
            return httpx.Response(200, text=search if request.url.params["page"] == "1" else "<html></html>")
        return httpx.Response(200, text=page({"loaderData": {"details": job}}))

    client = PoliteClient(min_interval=0, transport=httpx.MockTransport(handler))
    posts = list(apple.collect(client, "en-us", "Apple", known={"200000001-0836"}))
    assert [p.native_id for p in posts] == ["200644467-0836", "200000001-0836"]
    first = posts[0]
    assert first.title == "Senior Machine Learning Engineer - Ads" and "PyTorch" in first.description
    assert first.country_hint == "US" and first.posted_on.isoformat() == "2026-11-20"
    assert sum("/details/" in c for c in calls) == 1  # the retail role and the known role are not read
