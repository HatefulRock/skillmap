"""Candidate tech terms that the skill taxonomy doesn't know yet.

The taxonomy only finds what it has a pattern for, so a new framework or protocol is invisible
until someone adds it. This picks out tokens that look like product or technology names —
CamelCase (LangGraph), short acronyms (MCP, A2A), names with digits or dots (vLLM, GPT-5,
Next.js) — and drops anything the taxonomy already matches or that is ordinary job-ad
vocabulary. Counted month by month, a term that suddenly appears in many postings is a skill
worth adding.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_TOKEN = re.compile(
    r"(?<![\w.#+/'’-])("
    r"[A-Z][a-z]+(?:[A-Z][a-z0-9]*)+"            # CamelCase: LangGraph, DeepSeek, DuckDB
    r"|[A-Z]{2,}[a-z]+[A-Za-z]*"                 # leading capitals: DSPy, LLMOps, MLflow
    r"|[a-z]+[A-Z][A-Za-z0-9]+"                  # camelStart: vLLM, nanoGPT, iOS
    r"|[A-Z][0-9A-Z]{1,5}(?:-[A-Z0-9]+)?"        # acronyms: MCP, A2A, DPO, ONNX, H100
    r"|[A-Za-z]+[-.]?[0-9][A-Za-z0-9.]*"         # with digits: GPT-5, Llama3, S3, k8s
    r"|[A-Za-z]+\.(?:js|ai|io|py|rs)"            # dotted names: Next.js, Pydantic.ai
    r")(?![\w#+])"
)

# Job-ad vocabulary that matches the shapes above but is never a skill.
STOP = {
    "AI", "ML", "US", "USA", "UK", "EU", "EEO", "EEOC", "HR", "PTO", "CEO", "CTO", "CFO", "COO", "VP", "SVP", "IC", "OTE",
    "APAC", "EMEA", "LATAM", "NYC", "SF", "LA", "SG", "HK", "UAE", "GDPR", "ADA", "LLC", "INC", "LTD", "PTE", "AND", "THE",
    "OR", "OF", "TO", "IN", "ON", "AT", "FOR", "WITH", "ALL", "OUR", "YOU", "WE", "IT", "IS", "BA", "BS", "MS", "PHD",
    "MBA", "BSC", "MSC", "B2B", "B2C", "SaaS", "FAQ", "KPI", "KPIs", "OKR", "OKRs", "ROI", "PR", "PRs", "QA", "UI", "UX",
    "API", "APIs", "SDK", "SDKs", "CV", "CVs", "DEI", "ESG", "WFH", "FTE", "H1B", "E-Verify", "LinkedIn", "GitHub",
    "YouTube", "TikTok", "WhatsApp", "PayPal", "JavaScript", "TypeScript", "OpenAI", "DeepMind", "YC", "IPO", "ARR",
    "MRR", "GTM", "SEO", "CRM", "ERP", "POC", "PoC", "PoCs", "MVP", "SLA", "SLAs", "SOC2", "SOC", "ISO", "PII", "HIPAA",
    "PCI", "NDA", "Q1", "Q2", "Q3", "Q4", "H1", "H2", "FY", "W2", "W-2", "K-12", "COVID-19", "COVID", "N/A", "TBD",
    "CS", "PM", "PMs", "ABOUT", "ROLE", "NICE", "WHAT", "WHO", "WHY", "HOW", "HAVE", "ARE", "WORK", "DO", "YOUR", "WILL",
    # Too generic or ambiguous to be a skill: operating system, IP (often intellectual property),
    # SDLC, and US state codes in locations.
    "OS", "IP", "SDLC", "CA", "NY", "TX", "WA", "MA", "IL", "NJ", "VA", "GA", "NC", "FL", "DC", "AZ", "CO", "UT", "PA", "OH",
    "MN", "MI", "MD", "CT", "ET", "PT", "PST", "EST", "CET", "GMT", "UTC",
    # Formats, protocols and numbering every ad mentions in passing ("JSON", "ID", "Level II").
    "JSON", "XML", "YAML", "CSV", "PDF", "HTML", "HTTP", "HTTPS", "URL", "URLs", "ID", "IDs", "II", "III", "IV",
    "USD", "SGD", "GBP", "EUR", "CAD", "AUD", "HKD", "INR", "JPY", "CNY", "KRW", "AED", "CHF", "OKX", "ETH", "BTC",
    # Benefits, education and business words ("FSA/HSA", "STEM degree", "SME", "RFP", "SOPs"), company and role
    # names, and hardware or acronyms too generic or ambiguous to be a skill (CPU, DB, CD, AD).
    "STEM", "FSA", "HSA", "FMLA", "PPO", "HMO", "EPO", "EAP", "RSU", "ESPP", "DOE", "STD", "HQ", "SME", "RFP",
    "RFI", "SOP", "SOW", "FDE", "NVIDIA", "CPU", "DB", "CD", "AD", "TBA", "ASAP", "AM", "EOD",
    # "AI" in German, French and Spanish ads; employers and agencies; life-science words.
    "KI", "IA", "IBM", "NASA", "DoD", "DOD", "NATO", "FDA", "DNA", "RNA", "GMP", "OSHA", "EHS",
}


def ignored(term: str) -> bool:
    """A word that is never a skill, singular or plural ("RFPs", "UIs"; a capital S is part of the name: CSS, AWS)."""
    return any(t in STOP or t.upper() in STOP for t in (term, term[:-1] if term.endswith("s") and len(term) > 2 else term))
MAX_TERMS = 40
# Three or more capitalised words in a row are a shouted heading ("WHAT YOU WILL DO"), not names.
_HEADING = re.compile(r"\b[A-Z][A-Z'’]+(?: [A-Z][A-Z'’]*){2,}\b")


def candidates(text: str, known: Callable[[str], bool]) -> list[str]:
    """Distinct unknown tech-looking terms in `text`, in order of first appearance."""
    out: list[str] = []
    seen: set[str] = set()
    headings = [m.span() for m in _HEADING.finditer(text or "")]
    for match in _TOKEN.finditer(text or ""):
        if any(a <= match.start() < b for a, b in headings):
            continue
        term = match.group(1).rstrip(".")
        if term in seen or ignored(term) or len(term) < 2 or term.isdigit():
            continue
        if re.fullmatch(r"\d+[A-Za-z]{0,2}|[A-Z]\d|[A-Z]\d{1,2}[A-Z]?\d?", term) and not re.fullmatch(r"[A-Z]\d[A-Z]", term):
            continue  # "5x", "10k", grade and level codes (L5, E6, P3), but keep A2A-style names
        seen.add(term)
        if not known(term):
            out.append(term)
            if len(out) >= MAX_TERMS:
                break
    return out
