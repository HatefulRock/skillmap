"""Requirements and offers beyond skills: degree asked for, visa sponsorship, equity.

Recorded on every posting from the first run so they can be trended later (requirement creep,
who sponsors visas); none of them can be reconstructed for postings that are already gone.
"""

from __future__ import annotations

import re

_PHD = re.compile(r"\b(?:ph\.?\s?d|doctora(?:te|l))\b", re.I)
_MASTER = re.compile(r"\b(?:master'?s|m\.?\s?sc?\.?|msc|mtech|m\.?eng)\b(?:\s+degree)?", re.I)
_BACHELOR = re.compile(r"\b(?:bachelor'?s|b\.?\s?sc?\.?|bsc|btech|b\.?eng|undergraduate degree|degree in)\b", re.I)
_DEGREE_REQUIRED = re.compile(r"\b(?:degree|diploma|ph\.?\s?d|master'?s|bachelor'?s)\b", re.I)
_OPTIONAL = re.compile(r"\b(?:or equivalent|equivalent (?:practical )?experience|not required|preferred|a plus|bonus|"
                       r"nice to have|no degree)\b", re.I)

_NO_VISA = re.compile(r"\b(?:(?:unable|not able|cannot|can ?not|do(?:es)? not|don'?t|won'?t|will not|no)\s+"
                      r"(?:to\s+)?(?:offer\s+|provide\s+)?(?:visa\s+)?sponsor(?:ship)?|"
                      r"without (?:the )?need (?:for|of) (?:visa )?sponsorship|"
                      r"must (?:be|have) (?:legally )?(?:authori[sz]ed|eligible|the right) to work|"
                      r"(?:singapore(?:an)?|us) citizens? (?:or|and|/) (?:permanent residents?|green card|prs?)\b(?: only)?)",
                      re.I)
_VISA = re.compile(r"\b(?:visa sponsorship (?:is )?(?:available|offered|provided)|(?:we|will|can|able to) "
                   r"(?:offer|provide|support)?\s*(?:visa )?sponsor(?:ship)?|relocation (?:and|&) visa|"
                   r"employment pass (?:sponsorship|support)|sponsor (?:an? )?(?:employment pass|work visa|h-?1b))\b",
                   re.I)
_EQUITY = re.compile(r"\b(?:equity|stock options?|esops?|rsus?|restricted stock|token (?:allocation|grants?)|"
                     r"share options?|ownership stake)\b", re.I)


def degree(text: str) -> str | None:
    """Highest degree a posting asks for: phd | master | bachelor | None (not stated).

    "or equivalent experience" next to the degree makes it optional, recorded as None, so the
    trend counts only firm requirements.
    """
    best = None
    for line in (text or "").splitlines():
        if not _DEGREE_REQUIRED.search(line) or _OPTIONAL.search(line):
            continue
        if _PHD.search(line):
            return "phd"
        if _MASTER.search(line):
            best = "master"
        elif _BACHELOR.search(line) and best is None:
            best = "bachelor"
    return best


def visa_sponsorship(text: str) -> bool | None:
    """True if the ad says it sponsors visas, False if it says it won't, None if silent."""
    if _NO_VISA.search(text or ""):
        return False
    if _VISA.search(text or ""):
        return True
    return None


def equity(text: str) -> bool:
    return bool(_EQUITY.search(text or ""))
