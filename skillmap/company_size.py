"""Company size tiers, for "what do startups ask for compared with big tech" and pay by size.

Known companies come from config/company_sizes.yaml. The rest are sorted by rule: recruitment
agencies and consultancies by their names, then companies hiring through the job boards startups
use count as startups. Anything else stays unclassified rather than guessed.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from .models import normalise_company

CONFIG = Path(__file__).resolve().parents[1] / "config" / "company_sizes.yaml"
SIZES = ["startup", "midsize", "big_tech", "enterprise", "services", "agency"]
LABELS = {
    "startup": "Startups",
    "midsize": "Mid-size tech",
    "big_tech": "Big tech",
    "enterprise": "Large non-tech",
    "services": "IT services and consultancies",
    "agency": "Recruitment agencies",
    "unknown": "Not classified",
}
# Boards companies set up themselves, which mostly means younger tech companies; the big ones
# that use them are on the list.
STARTUP_SOURCES = {"greenhouse", "lever", "ashby", "workable", "recruitee", "personio", "teamtailor", "gem"}
AGENCY = re.compile(
    r"\b(?:recruit\w*|staffing|manpower|personnel|talents?|human (?:resources?|capital|union)|hr|employment|"
    r"headhunt\w*|placement|careers? connex|michael page|hays|randstad|adecco|allegis|robert walters|"
    r"kelly services|persol|morgan mckinley|kerry consulting|argyll scott|jobster|wecruit|jobline|talentsis|actcareers|remotejobsone|lifelancer)\b")
SERVICES = re.compile(r"\b(?:consult\w*|infotech|it services|outsourc\w*|software services)\b")
_NOISE = re.compile(r"^(?:careers? at|jobs at)\s+|\s+(?:careers?|jobs|internal|external|job posting)$")


@lru_cache(maxsize=1)
def _known() -> dict[str, str]:
    data = yaml.safe_load(CONFIG.read_text()) or {}
    return {normalise_company(str(name)): tier for tier, names in data.items() for name in names or []}


def _clean(company: str) -> str:
    name = normalise_company(company)
    previous = None
    while previous != name:
        previous, name = name, _NOISE.sub("", name).strip()
    return name


def classify(company: str, sources: set[str] | frozenset[str] = frozenset()) -> str:
    name = _clean(company or "")
    if not name:
        return "unknown"
    known = _known()
    words = name.split()
    # Longest listed prefix of the name wins: "google asia pacific" is Google.
    for n in range(len(words), 0, -1):
        tier = known.get(" ".join(words[:n]))
        if tier:
            return tier
    if AGENCY.search(name):
        return "agency"
    if SERVICES.search(name):
        return "services"
    if sources & STARTUP_SOURCES:
        return "startup"
    return "unknown"
