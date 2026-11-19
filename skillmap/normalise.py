"""RawPosting → Posting: every extractor applied in one place."""

from __future__ import annotations

import re
from datetime import date

from .extract import location, perks, roles, salary, sector, seniority, terms
from .extract.skills import Taxonomy
from .extract.text import to_plain
from .models import Posting, RawPosting, dedup_key

REMOTE_TITLE = re.compile(r"(?:^|[(\[,|/–-])\s*(?:fully\s+)?remote\s*(?=$|[)\],|/–(-])", re.I)


def normalise(raw: RawPosting, taxonomy: Taxonomy, today: date, changed: set[str] | None = None) -> Posting:
    """`changed`: when re-extracting a stored posting, the skills whose patterns changed since its
    last extraction (None: match all). Skill matching is most of the cost of a re-extraction."""
    plain = to_plain(raw.description)
    if changed is not None and raw.prior is not None:
        skills, nice = taxonomy.extract_since(raw.title, raw.description, (raw.prior[1], raw.prior[2]), changed)
    else:
        skills, nice = taxonomy.extract(raw.title, raw.description)
    # "Backend Engineer (Remote)" often says so only in the title, with a region as the location.
    remote_hint = raw.remote_hint if raw.remote_hint is not None else (True if REMOTE_TITLE.search(raw.title) else None)
    country, city, region, remote = location.normalise(raw.location, raw.country_hint, remote_hint)
    family = roles.classify(raw.title)
    if raw.source == "hn" and family is None:
        # HN headers often name several roles at once ("Engineers, Designer"); fall back to the body.
        family = roles.classify(plain[:400])
    posted = raw.posted_on if raw.posted_on and raw.posted_on <= today else None
    # HN posts belong to their month's thread; company-board posts are live from when they were
    # published (when the board says so) until the day they stop appearing.
    first_seen = posted if raw.source == "hn" else (posted or today)
    last_seen = posted if raw.source == "hn" else today
    pay = (raw.salary_min, raw.salary_max, raw.salary_currency, raw.salary_period)
    if not (raw.salary_min and raw.salary_currency and raw.salary_period):
        # No salary field: many ads state the range in the text (US/EU pay-transparency laws, HN posts).
        pay = salary.from_text(plain, country) or (None, None, None, None)
    tech = family is not None
    return Posting(
        posting_id=raw.posting_id,
        source=raw.source,
        company=raw.company.strip(),
        title=raw.title.strip(),
        url=raw.url,
        location=raw.location,
        country=country,
        city=city,
        region=region,
        remote=remote,
        seniority=seniority.classify(raw.title, plain, raw.seniority_hint, raw.min_years_hint),
        role_family=family or "non_tech",
        sector=sector.classify(plain, raw.sector_hint),
        skills=skills,
        skills_nice=nice,
        posted_on=posted,
        first_seen=first_seen or today,
        last_seen=last_seen or today,
        dedup_key=dedup_key(raw.company, raw.title, country),
        salary_min=pay[0],
        salary_max=pay[1],
        salary_currency=pay[2],
        salary_period=pay[3],
        n_words=len(plain.split()),
        min_years=raw.min_years_hint or seniority.years_required(plain),
        # Only tech ads, and only from full text: Adzuna's snippets and HN one-liners would skew counts.
        terms=terms.candidates(plain, taxonomy.known) if tech and raw.source != "adzuna" else [],
        degree=perks.degree(plain),
        visa_sponsorship=perks.visa_sponsorship(plain),
        equity=perks.equity(plain),
    )
