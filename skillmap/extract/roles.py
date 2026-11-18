"""Role family from the title. Order matters: the first family whose rule matches wins."""

from __future__ import annotations

import re

FAMILIES = ["fde", "ai_ml", "data", "backend", "frontend", "infra", "quant", "other_eng"]
LABELS = {
    "fde": "Forward deployed / solutions",
    "ai_ml": "AI / ML engineering",
    "data": "Data engineering & science",
    "backend": "Backend / Python",
    "frontend": "Frontend & mobile",
    "infra": "Infra, DevOps & security",
    "quant": "Quant & trading tech",
    "other_eng": "Other engineering",
}

RULES: list[tuple[str, re.Pattern[str]]] = [
    ("fde", re.compile(r"forward[- ]deployed|\bfde\b|solutions? (?:engineer|architect)|customer (?:engineer|success engineer)|"
                       r"deployment (?:engineer|strategist)|field engineer|implementation engineer|sales engineer|"
                       r"pre-?sales|technical account|partner engineer", re.I)),
    ("ai_ml", re.compile(r"\bai\b|\bml\b|machine learning|\bllm|gen ?ai|generative|deep learning|computer vision|\bnlp\b|"
                         r"applied scientist|research (?:engineer|scientist)|mlops|prompt engineer|agentic|agents? engineer|"
                         r"artificial intelligence|data scientist", re.I)),
    ("quant", re.compile(r"quant|trading|algorithmic|market maker|execution (?:engineer|developer)", re.I)),
    ("data", re.compile(r"data (?:engineer|analyst|platform|architect|infrastructure)|analytics engineer|\betl\b|"
                        r"business intelligence|\bbi (?:engineer|developer)|data scien", re.I)),
    ("infra", re.compile(r"devops|\bsre\b|site reliability|platform engineer|infrastructure|cloud (?:engineer|architect)|"
                         r"security|devsecops|network engineer|systems engineer|reliability", re.I)),
    ("frontend", re.compile(r"front[- ]?end|\bui\b|\bios\b|android|mobile|react|web developer", re.I)),
    ("backend", re.compile(r"back[- ]?end|python|software (?:engineer|developer)|\bswe\b|full[- ]?stack|"
                           r"(?:golang|go|java|node|rust|c\+\+) (?:engineer|developer)|api engineer|developer|"
                           r"programmer|founding engineer|member of technical staff", re.I)),
    ("other_eng", re.compile(r"engineer|scientist|technical lead|tech lead|cto\b", re.I)),
]


# Engineering outside software: these titles say "engineer" or "scientist" but their ads are about
# buildings, plants, vehicles or labs, and would dilute the software skill shares.
NOT_SOFTWARE = re.compile(
    r"\b(?:facilities|facility|mechanical|civil|structural|chemical|process engineer|manufacturing|commissioning|"
    r"electrical|hvac|plumbing|construction|maintenance|mechatronics|aerospace|vehicle|automotive|piping|"
    r"clinical|laboratory|lab scientist|biomedical|environmental|geotechnical|mining|petroleum|marine|"
    r"field service|service engineer|quality engineer|test technician|technician|sales engineer \(|"
    r"(?:vacuum|pumps?|valves?|compressors?|instrumentation|semiconductor equipment)\b)", re.I)
# Titles that name software work win over the list above ("Software Engineer, Vehicle Platform").
SOFTWARE = re.compile(r"software|\bml\b|machine learning|\bai\b|data|cloud|backend|frontend|full[- ]?stack|"
                      r"firmware|embedded|devops|platform|\bsre\b|security|solutions?|forward[- ]deployed|"
                      r"\bapi\b|developer|programmer", re.I)


def classify(title: str) -> str | None:
    """None means not a technical role; those postings are kept but left out of skill stats."""
    if NOT_SOFTWARE.search(title or "") and not SOFTWARE.search(title or ""):
        return None
    for family, rule in RULES:
        if rule.search(title or ""):
            return family
    return None
