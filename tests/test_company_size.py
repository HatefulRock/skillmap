from datetime import date

from skillmap import aggregate, company_size
from skillmap.company_size import classify


def test_listed_companies_match_by_name_or_name_start():
    assert classify("GOOGLE ASIA PACIFIC PTE. LTD.") == "big_tech"
    assert classify("TIKTOK PTE. LTD.", {"mcf"}) == "big_tech"
    assert classify("Careers at Tide", {"greenhouse"}) == "midsize"
    assert classify("Walmart - Internal", {"workday"}) == "enterprise"
    assert classify("Accenture Federal Services") == "services"
    assert classify("Anthropic", {"greenhouse"}) == "midsize"


def test_unlisted_companies_are_sorted_by_rule_or_left_unclassified():
    assert classify("RECRUIT EXPRESS PTE LTD", {"mcf"}) == "agency"
    assert classify("MANPOWER STAFFING SERVICES (SINGAPORE) PTE LTD", {"mcf"}) == "agency"
    assert classify("SCIENTEC CONSULTING PTE. LTD.", {"mcf"}) == "services"
    assert classify("Tiny New Co", {"ashby"}) == "startup"
    assert classify("Tiny New Co", {"mcf"}) == "unknown"
    assert classify("", {"ashby"}) == "unknown"


def test_every_listed_tier_has_a_label():
    assert set(company_size._known().values()) <= set(company_size.SIZES)
    assert set(company_size.SIZES) | {"unknown"} == set(company_size.LABELS)


def row(i, company, source, usd, level="senior", country="US"):
    return {"company": company, "source": source, "country": country, "seniority": level, "role_family": "swe",
            "salary_min": usd, "salary_max": usd, "salary_period": "year", "salary_currency": "USD",
            "size": classify(company, {source})}


def test_pay_by_size_compares_within_a_country_and_lists_companies_with_enough_ads():
    rows = [row(i, "Google", "greenhouse", 200_000) for i in range(25)]
    rows += [row(i, "Tiny New Co", "ashby", 150_000) for i in range(25)]
    rows += [row(i, "OCBC", "mcf", 80_000, country="SG") for i in range(25)]
    rows += [row(i, "Few Ads Co", "ashby", 999_000) for i in range(5)]
    pay = aggregate.pay_block(rows)
    us = {r["key"]: r["usd"]["median"] for r in pay["by_size"]["US|all"]}
    assert us == {"startup": 150_000, "big_tech": 200_000}
    assert {r["key"] for r in pay["by_size"]["all|all"]} == {"startup", "big_tech", "enterprise"}
    companies = [(c["company"], c["size"], c["country"], c["n"]) for c in pay["by_company"]]
    assert companies == [("Google", "big_tech", "US", 25), ("Tiny New Co", "startup", "US", 25), ("OCBC", "enterprise", "SG", 25)]


def test_top_skills_carry_their_share_by_company_size():
    today = date(2026, 11, 23)
    base = {"posted_on": today, "first_seen": today, "last_seen": today, "role_family": "swe"}
    rows = [{**base, "skills": ["Python", "Go"] if i < 40 else ["Python"], "size": "startup" if i < 40 else "big_tech"}
            for i in range(80)]
    block = aggregate.current_block(rows, today, {"Python": "Languages", "Go": "Languages"})
    assert block["n_sizes"] == {"startup": 40, "big_tech": 40}
    go = next(s for s in block["skills"] if s["name"] == "Go")
    assert go["sizes"] == {"startup": 1.0}
