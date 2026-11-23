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
