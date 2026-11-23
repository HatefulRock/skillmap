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


# How a listed company is shown when an ad names a local legal entity ("GOOGLE ASIA PACIFIC PTE.
# LTD.") or a board's internal name ("SpaceXSI"); otherwise the listed name is title-cased.
DISPLAY = {
    "aws": "Amazon", "amazon": "Amazon", "google": "Google", "alphabet": "Google", "google deepmind": "Google DeepMind",
    "deepmind": "Google DeepMind", "youtube": "YouTube", "meta": "Meta", "facebook": "Meta", "nvidia": "NVIDIA",
    "amd": "AMD", "advanced micro devices": "AMD", "ibm": "IBM", "sap": "SAP", "servicenow": "ServiceNow",
    "hp": "HP", "hewlett packard enterprise": "Hewlett Packard Enterprise", "linkedin": "LinkedIn", "github": "GitHub",
    "paypal": "PayPal", "tiktok": "TikTok", "bytedance": "ByteDance", "spacex": "SpaceX", "spacexsi": "SpaceX",
    "x": "X", "ebay": "eBay", "asml": "ASML", "tsmc": "TSMC", "arm": "Arm", "grab": "Grab", "grabtaxi": "Grab",
    "sea": "Sea", "booking com": "Booking.com", "booking": "Booking.com", "micron semiconductor": "Micron",
    "jpmorgan chase": "JPMorgan Chase", "jpmorgan": "JPMorgan Chase", "jp morgan": "JPMorgan Chase",
}


def _listed(company: str) -> str | None:
    """The longest listed name the company's name starts with: "google asia pacific" is "google"."""
    words = _clean(company or "").split()
    known = _known()
    for n in range(len(words), 0, -1):
        if " ".join(words[:n]) in known:
            return " ".join(words[:n])
    return None


def display_name(company: str) -> str:
    """One name per listed company, so its local entities count together; others unchanged."""
    key = _listed(company)
    if key is None:
        return company
    if key in DISPLAY:
        return DISPLAY[key]
    # The ad's own spelling when it is just the name ("Microsoft"), not a shouted legal entity.
    if _clean(company) == key and not company.isupper():
        return company.strip()
    return key.title()


def classify(company: str, sources: set[str] | frozenset[str] = frozenset()) -> str:
    name = _clean(company or "")
    if not name:
        return "unknown"
    key = _listed(name)
    if key:
        return _known()[key]
    if AGENCY.search(name):
        return "agency"
    if SERVICES.search(name):
        return "services"
    if sources & STARTUP_SOURCES:
        return "startup"
    return "unknown"
