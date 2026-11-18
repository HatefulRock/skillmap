"""Seniority from the title first, then stated years of experience, then the source's own level.

Levels: intern, junior, mid, senior, staff (staff/principal/lead IC), manager, unspecified.
"""

from __future__ import annotations

import re

LEVELS = ["intern", "junior", "mid", "senior", "staff", "manager"]

# Banks use corporate grades on individual-contributor titles ("Senior SRE - VP", "Data Engineer, AVP"):
# with an IC role word, the grade is a level, not a management job.
IC_WORD = re.compile(r"\b(?:engineer|developer|scientist|analyst|architect|sre|programmer|researcher|tech(?:nical)? lead)\b", re.I)
BANK_GRADES: list[tuple[str, re.Pattern[str]]] = [
    ("mid", re.compile(r"\b(?:avp|assistant vice president)\b", re.I)),
    ("staff", re.compile(r"\b(?:svp|senior vice president|executive director|managing director|director)\b", re.I)),
    ("senior", re.compile(r"\b(?:vp|vice president)\b", re.I)),
]

TITLE_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("intern", re.compile(r"\b(?:intern(?:ship)?|co-?op|apprentice|trainee|working student)\b", re.I)),
    ("manager", re.compile(r"\b(?:head of|director|vp|vice president|chief|cto|cio|engineering manager|"
                           r"manager,? (?:\w+ )?(?:engineering|data|ml|ai|machine learning)|(?:engineering|ml|ai|data science) manager)\b", re.I)),
    ("staff", re.compile(r"\b(?:staff|principal|distinguished|(?:technical|senior) fellow|tech(?:nical)? lead|lead (?:engineer|developer|"
                         r"scientist|ml|ai|data)|lead \w+ (?:engineer|developer|scientist)|"
                         # A solutions architect is a customer-facing role, not a level.
                         r"(?<!solutions )(?<!solution )architect)\b", re.I)),
    ("senior", re.compile(r"\b(?:senior|sr\.?|snr)\b|\b(?:engineer|developer|scientist) (?:iii|3)\b", re.I)),
    ("junior", re.compile(r"\b(?:junior|jr\.?|graduate|new grad|entry[- ]level|fresh(?:er)?|associate (?:engineer|"
                          r"developer|data|ml|software|ai)|early career)\b|\b(?:engineer|developer|scientist) (?:i|1)\b", re.I)),
    ("mid", re.compile(r"\b(?:mid[- ]level|intermediate)\b|\b(?:engineer|developer|scientist) (?:ii|2)\b", re.I)),
]

MANAGES = re.compile(r"\b(?:engineering manager|head of|manager)\b", re.I)

YEARS = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(?:\d{1,2}\s*)?\+?\s*years?(?:'|’)?\s*(?:of\s+)?"
    r"(?:(?:relevant|professional|industry|hands-on|working|commercial|related)\s+)*(?:experience|exp\b)",
    re.I,
)


def years_required(text: str) -> int | None:
    """Smallest minimum-years figure stated in the text (the bar to entry), if any."""
    values = [int(match.group(1)) for match in YEARS.finditer(text or "")]
    values = [v for v in values if 0 < v <= 20]
    return min(values) if values else None


def from_years(years: int) -> str:
    if years <= 1:
        return "junior"
    if years <= 4:
        return "mid"
    if years <= 7:
        return "senior"
    return "staff"


def classify(title: str, description: str = "", hint: str | None = None, min_years: int | None = None) -> str:
    title = title or ""
    if IC_WORD.search(title) and not TITLE_RULES[0][1].search(title) and not MANAGES.search(title):
        for level, rule in BANK_GRADES:
            if rule.search(title):
                # "Senior Engineer - AVP", "Lead Engineer, VP": the stronger of the two signals wins.
                named = next((lv for lv, r in TITLE_RULES[2:] if r.search(title)), None)
                return max(level, named or level, key=LEVELS.index)
    for level, rule in TITLE_RULES:
        if rule.search(title or ""):
            return level
    years = min_years if min_years else years_required(description)
    if years:
        return from_years(years)
    return hint or "unspecified"
