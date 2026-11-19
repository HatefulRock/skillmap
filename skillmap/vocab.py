"""How complete the skill taxonomy is, measured on stored ads.

Two checks on a sample of live tech postings:

* Reference terms (config/reference_terms.yaml): named technologies from outside lists (developer
  surveys, cloud services, AI tooling). Each one found in the ads either matches a skill or is a gap.
* Asked-for phrases: what follows "experience with", "proficiency in", "knowledge of", "such as"
  and the like, split into items. Items no skill matches are candidates for new skills.

Coverage is the share of (ad, term) mentions a skill matches. The report is counts only, by number
of companies, so a term one employer repeats in every ad doesn't look common.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from .archive import Archive
from .audit import load_texts, sample
from .extract.skills import Taxonomy
from .extract.text import to_plain
from .store import Store

SAMPLE = 4000
REFERENCE = Path(__file__).resolve().parent.parent / "config" / "reference_terms.yaml"
MIN_COMPANIES = 3

_CUE = re.compile(
    r"\b(?:experience (?:with|in|using|building)|hands-on (?:with|experience (?:with|in))|proficien\w* (?:in|with)"
    r"|knowledge of|familiar\w* with|expertise (?:in|with)|exposure to|background in|skills? in|fluency in"
    r"|such as|e\.g\.,?|including|like)\s+(.{3,160}?)(?:[.;:!?]|$)",
    re.I,
)
_INNER_CUE = re.compile(r"\b(?:such as|e\.g\.,?|including|like)\s+", re.I)
_TAIL = re.compile(r"\s+(?:is|are|was|would|will|can|to|for|in production|a plus|preferred|required|desired"
                   r"|nice to have|highly desirable|strongly preferred|experience|knowledge|skills)\b.*$", re.I)
_SPLIT = re.compile(r",|;|/|\(|\)|\band\b|\bor\b|\betc\b\.?|\bplus\b", re.I)
_LEAD = re.compile(r"^(?:the|a|an|one|two|more|other|modern|common|popular|similar|relevant|tools|frameworks"
                   r"|technologies|languages|platforms|at least|any|some|of|in|with|using|strong|solid|deep"
                   r"|working|practical|professional|good|excellent|proven|e\.g\.?|i\.e\.?)\s+", re.I)
# Asked-for items that are never a skill name: abstract nouns and ad boilerplate.
_GENERIC = {
    "experience", "skills", "knowledge", "tools", "technologies", "systems", "data", "software", "teams", "projects",
    "development", "engineering", "environment", "environments", "products", "platforms", "solutions", "services",
    "frameworks", "languages", "applications", "customers", "stakeholders", "business", "industry", "work", "others",
    "similar", "related fields", "related", "more", "it", "them", "this", "that", "these", "those", "years",
}


@dataclass(frozen=True)
class Term:
    name: str
    pattern: re.Pattern[str]
    group: str


def load_reference(path: Path = REFERENCE) -> list[Term]:
    raw = yaml.safe_load(path.read_text())
    out = []
    for group, names in raw.items():
        for name in names:
            # Written as it appears in ads; matched case-sensitively so "Go" isn't "go" and
            # "Spark" isn't "spark joy", with word edges that allow "Next.js" or "C++".
            out.append(Term(name, re.compile(rf"(?<![\w.+#-]){re.escape(name)}(?![\w+#])"), group))
    return out


def any_term(reference: list[Term]) -> re.Pattern[str]:
    """One pass over a text for every reference term, longest names first ("Spring Boot" before "Spring")."""
    names = sorted({t.name for t in reference}, key=len, reverse=True)
    return re.compile(rf"(?<![\w.+#-])({'|'.join(re.escape(n) for n in names)})(?![\w+#])")


def asked_items(text: str) -> list[str]:
    """Short items named after an asking cue ("experience with Kafka, Flink and dbt")."""
    items = []
    for match in _CUE.finditer(text):
        listed = _INNER_CUE.split(match.group(1))[-1]  # "tools such as Temporal": the names after the last cue
        for part in _SPLIT.split(listed):
            item = _TAIL.sub("", part.strip(" -–—:'\"*•"))
            for _ in range(3):
                item = _LEAD.sub("", item)
            words = item.split()
            if not 1 <= len(words) <= 3 or len(item) < 2 or len(item) > 40 or item.lower() in _GENERIC:
                continue
            if not re.search(r"[A-Za-z]", item):
                continue
            items.append(item)
    return items


def _key(item: str) -> str:
    return re.sub(r"\s+", " ", item.lower())


def covered(text: str, start: int, end: int, taxonomy: Taxonomy) -> bool:
    """Whether a skill matches the sentence around text[start:end] on (part of) those words, so a
    term a skill needs context for ("Go" in "Go or Rust") counts as covered where it is."""
    base = max(text.rfind(c, 0, start) for c in "\n.;!?") + 1
    ends = [i for i in (text.find(c, end) for c in "\n.;!?") if i != -1]
    sentence = text[base:min(ends) if ends else len(text)]
    a, b = start - base, end - base
    for skill in taxonomy.skills:
        hit = any(m.start() < b and m.end() > a for p in skill.patterns for m in p.finditer(sentence))
        if hit and not any(n.search(sentence) for n in skill.negatives):
            return True
    return False


def measure(texts: dict[str, tuple[str, str]], taxonomy: Taxonomy, reference: list[Term]) -> dict:
    """texts: posting id -> (company, plain text)."""
    ref_companies: dict[str, set[str]] = defaultdict(set)
    ref_mentions = Counter()
    ref_hit = Counter()
    asked_companies: dict[str, set[str]] = defaultdict(set)
    asked_spelling: dict[str, Counter] = defaultdict(Counter)
    asked_mentions = asked_hit = 0
    finder = any_term(reference)
    for company, text in texts.values():
        firsts: dict[str, re.Match[str]] = {}
        for m in finder.finditer(text):
            firsts.setdefault(m.group(1), m)
        for name, m in firsts.items():
            ref_companies[name].add(company)
            ref_mentions[name] += 1
            if taxonomy.known(name) or covered(text, m.start(1), m.end(1), taxonomy):
                ref_hit[name] += 1
        seen = set()
        for item in asked_items(text):
            key = _key(item)
            if key in seen:
                continue
            seen.add(key)
            asked_mentions += 1
            if taxonomy.known(item):
                asked_hit += 1
                continue
            asked_companies[key].add(company)
            asked_spelling[key][item] += 1

    reference_gaps = sorted(
        ((t.name, t.group, len(ref_companies[t.name]), ref_mentions[t.name]) for t in {t.name: t for t in reference}.values()
         if ref_mentions[t.name] and not ref_hit[t.name] and len(ref_companies[t.name]) >= MIN_COMPANIES),
        key=lambda g: (-g[2], g[0]))
    asked_gaps = sorted(
        ((asked_spelling[k].most_common(1)[0][0], len(c)) for k, c in asked_companies.items() if len(c) >= MIN_COMPANIES),
        key=lambda g: (-g[1], g[0]))
    total_ref = sum(ref_mentions.values())
    return {
        "ads": len(texts),
        "companies": len({c for c, _ in texts.values()}),
        "reference": {
            "terms": len(reference),
            "found": len(ref_mentions),
            "mentions": total_ref,
            "coverage": round(sum(ref_hit.values()) / total_ref, 4) if total_ref else None,
            "gaps": [{"term": n, "group": g, "companies": c, "ads": m} for n, g, c, m in reference_gaps],
        },
        "asked": {
            "mentions": asked_mentions,
            "coverage": round(asked_hit / asked_mentions, 4) if asked_mentions else None,
            "gaps": [{"item": i, "companies": c} for i, c in asked_gaps[:300]],
        },
    }


def run(store: Store, archive: Archive | None, taxonomy: Taxonomy, today: date, n: int = SAMPLE) -> dict:
    picked = sample(store, store.rows(), today, n, seed=f"vocab-{today.isoformat()}")
    texts = load_texts(picked, archive)
    by_id = {r["posting_id"]: r for r in picked}
    plain = {pid: (by_id[pid]["company"], f"{by_id[pid]['title']}\n{to_plain(t)}") for pid, t in texts.items()}
    return measure(plain, taxonomy, load_reference())


def summary(report: dict) -> str:
    ref, asked = report["reference"], report["asked"]
    lines = [
        f"{report['ads']:,} ads from {report['companies']:,} companies.",
        f"Reference terms: {ref['found']} of {ref['terms']} appear; a skill matches {ref['coverage']:.1%} of their "
        f"{ref['mentions']:,} mentions.",
        f"Asked-for items: a skill matches {asked['coverage']:.1%} of {asked['mentions']:,}.",
        "",
        f"Reference terms no skill matches ({MIN_COMPANIES}+ companies): companies, ads",
    ]
    lines += [f"  {g['term']} [{g['group']}]: {g['companies']}, {g['ads']}" for g in ref["gaps"]]
    lines += ["", "Most asked-for items no skill matches: companies"]
    lines += [f"  {g['item']}: {g['companies']}" for g in asked["gaps"][:200]]
    return "\n".join(lines)
