"""The one record every source is normalised into.

`RawPosting` is what a collector yields: the posting as the source describes it, including the
full description text. `Posting` is what gets stored: metadata plus everything extracted from
the text. The description itself is never stored, so the published data set never republishes
anyone's job ad.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date


@dataclass
class RawPosting:
    source: str  # greenhouse | lever | ashby | workable | adzuna | hn | mcf
    native_id: str
    company: str
    title: str
    description: str  # plain text or HTML; extraction strips tags
    url: str = ""
    location: str = ""  # free text as the source gives it
    country_hint: str | None = None  # ISO-3166 alpha-2 when the source states it
    remote_hint: bool | None = None
    posted_on: date | None = None
    sector_hint: str | None = None  # from the curated company list
    seniority_hint: str | None = None  # e.g. MyCareersFuture's position level
    min_years_hint: int | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None  # month | year

    @property
    def posting_id(self) -> str:
        return f"{self.source}:{self.native_id}"


@dataclass
class Posting:
    posting_id: str
    source: str
    company: str
    title: str
    url: str
    location: str
    country: str | None
    city: str | None
    region: str | None
    remote: bool
    seniority: str
    role_family: str
    sector: str
    skills: list[str] = field(default_factory=list)
    skills_nice: list[str] = field(default_factory=list)  # only seen under "nice to have"
    posted_on: date | None = None
    first_seen: date | None = None
    last_seen: date | None = None
    dedup_key: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None


_NON_WORD = re.compile(r"[^a-z0-9]+")
_COMPANY_SUFFIX = re.compile(r"\b(pte|ltd|limited|inc|llc|gmbh|sa|plc|corp|corporation|co|group|holdings)\b")


def normalise_company(name: str) -> str:
    name = _COMPANY_SUFFIX.sub(" ", name.lower())
    return _NON_WORD.sub(" ", name).strip()


def dedup_key(company: str, title: str, country: str | None) -> str:
    """Same company, same title, same country: one job, however many boards list it.

    MyCareersFuture and Adzuna frequently carry the same ad as a company's own board, and counting
    it twice would inflate whatever that company asks for.
    """
    basis = "|".join([normalise_company(company), _NON_WORD.sub(" ", title.lower()).strip(), country or ""])
    return hashlib.sha1(basis.encode()).hexdigest()[:16]
