"""Derived insights: what a skill is worth, what sets roles and levels apart, which skills go
together, how requirements creep, and what to learn next for the configured target roles.

Pure functions of the stored rows (plus his self-ratings), like aggregate.py, which calls them.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date
from statistics import median

from .aggregate import MIN_GROUP, SENIORITY_ORDER, _pay_points, active, month_end, month_starts, shares
from .extract import roles

LADDER = ["junior", "mid", "senior", "staff", "manager"]
PREMIUM_MIN = 20  # postings with the skill, with stated pay, after matching
STRATUM_MIN = 3  # postings on each side of a (country, role, level) comparison


def pay_premium(rows: list[dict], skills: list[str]) -> dict:
    """How much more (or less) postings asking for a skill pay than comparable ones that don't.

    Postings are compared only within the same country, role family and seniority, so a skill
    common in senior US roles doesn't look valuable just for being senior and American. Within
    each stratum the gap is the difference in median log pay (with vs without); strata are
    averaged weighted by the smaller side. Reported as a percentage, with the matched count.
    """
    points = _pay_points(rows)
    strata: dict[tuple, list[tuple[set[str], float]]] = defaultdict(list)
    for r, _, usd in points:
        strata[(r.get("country"), r["role_family"], r["seniority"])].append((set(r["skills"]), math.log(usd)))
    out = []
    for skill in skills:
        total_w, total, n_with = 0.0, 0.0, 0
        for members in strata.values():
            yes = [v for s, v in members if skill in s]
            no = [v for s, v in members if skill not in s]
            if len(yes) < STRATUM_MIN or len(no) < STRATUM_MIN:
                continue
            weight = min(len(yes), len(no))
            total += weight * (median(yes) - median(no))
            total_w += weight
            n_with += len(yes)
        if n_with >= PREMIUM_MIN and total_w:
            out.append({"name": skill, "premium": round(math.exp(total / total_w) - 1, 3), "n": n_with})
    out.sort(key=lambda e: -e["premium"])
    return {"n_with_pay": len(points), "skills": out}


def role_dna(rows: list[dict], top: int = 8, min_share: float = 0.1) -> dict:
    """For each role family, the skills most over-represented in it compared with every other
    tech role (lift), among those at least `min_share` of its postings ask for."""
    out = {}
    for family in roles.FAMILIES:
        mine = [r for r in rows if r["role_family"] == family]
        rest = [r for r in rows if r["role_family"] != family]
        if len(mine) < MIN_GROUP or len(rest) < MIN_GROUP:
            continue
        a, b = shares(mine), shares(rest)
        ranked = sorted(
            ({"name": s, "share": round(v, 3), "elsewhere": round(b.get(s, 0.0), 3),
              "lift": round(v / b[s], 2) if b.get(s) else None}
             for s, v in a.items() if v >= min_share),
            key=lambda e: -(e["lift"] or 99),
        )
        common = sorted(({"name": s, "share": round(v, 3), "elsewhere": round(b.get(s, 0.0), 3)}
                         for s, v in a.items()), key=lambda e: (-e["share"], e["name"]))
        # "skills" is what sets the role apart (lift); "common" is its core stack, which lift hides
        # when every tech role shares it (Python, SQL).
        out[family] = {"n": len(mine), "label": roles.LABELS[family], "skills": ranked[:top], "common": common[:top]}
    return out


def seniority_ladder(rows: list[dict], top: int = 14) -> dict:
    """How asks change from junior to manager: the skills whose share moves most across levels,
    with each level's share, plus each level's typical years asked and skill count."""
    groups = {level: [r for r in rows if r["seniority"] == level] for level in LADDER}
    levels = [lvl for lvl in LADDER if len(groups[lvl]) >= MIN_GROUP]
    if len(levels) < 2:
        return {"levels": levels, "skills": [], "profile": {}}
    per_level = {lvl: shares(groups[lvl]) for lvl in levels}
    names = set().union(*(per_level[lvl] for lvl in levels))
    rows_out = []
    for name in names:
        values = [per_level[lvl].get(name, 0.0) for lvl in levels]
        if max(values) < 0.08:
            continue
        rows_out.append({"name": name, "shares": [round(v, 3) for v in values],
                         "change": round(values[-1 if levels[-1] != "manager" else -2] - values[0], 3)})
    rows_out.sort(key=lambda e: -abs(e["change"]))
    profile = {}
    for lvl in levels:
        years = [r["min_years"] for r in groups[lvl] if r.get("min_years")]
        profile[lvl] = {
            "n": len(groups[lvl]),
            "years_median": median(years) if years else None,
            "skills_mean": round(sum(len(r["skills"]) for r in groups[lvl]) / len(groups[lvl]), 2),
        }
    return {"levels": levels, "skills": rows_out[:top], "profile": profile}


def stacks(rows: list[dict], nodes: int = 32, edges: int = 70, min_joint: int = 15) -> dict:
    """A co-occurrence network: the most requested skills, joined where two are asked for
    together clearly more often than chance (lift ≥ 1.3) in enough postings."""
    base = shares(rows)
    picked = [s for s, _ in sorted(base.items(), key=lambda kv: -kv[1])[:nodes]]
    if not rows or not picked:
        return {"nodes": [], "edges": []}
    joint: Counter = Counter()
    keep = set(picked)
    for r in rows:
        mine = sorted(keep.intersection(r["skills"]))
        for i, a in enumerate(mine):
            for b in mine[i + 1:]:
                joint[(a, b)] += 1
    n = len(rows)
    candidates = []
    for (a, b), k in joint.items():
        if k < min_joint:
            continue
        lift = (k / n) / (base[a] * base[b])
        if lift >= 1.3:
            candidates.append({"a": a, "b": b, "n": k, "lift": round(lift, 2)})
    candidates.sort(key=lambda e: -(e["lift"] * math.log(e["n"])))
    return {"n": n, "nodes": [{"name": s, "share": round(base[s], 3)} for s in picked], "edges": candidates[:edges]}


def requirement_creep(rows: list[dict], today: date) -> dict:
    """Month by month within each level: median years asked, mean skills asked, and the share
    requiring a degree. Rising numbers at the same level are requirement creep."""
    if not rows:
        return {"months": [], "levels": {}}
    first = min(r["first_seen"] for r in rows)
    months = month_starts(first, today)
    out = {"months": [m.isoformat()[:7] for m in months], "levels": {}}
    for level in [lvl for lvl in SENIORITY_ORDER if lvl in LADDER]:
        mine = [r for r in rows if r["seniority"] == level]
        series = {"n": [], "years_median": [], "skills_mean": [], "degree": []}
        for m in months:
            group = active(mine, m, min(month_end(m), today))
            series["n"].append(len(group))
            if len(group) < MIN_GROUP:
                for key in ("years_median", "skills_mean", "degree"):
                    series[key].append(None)
                continue
            years = [r["min_years"] for r in group if r.get("min_years")]
            series["years_median"].append(median(years) if len(years) >= MIN_GROUP // 2 else None)
            series["skills_mean"].append(round(sum(len(r["skills"]) for r in group) / len(group), 2))
            series["degree"].append(round(sum(1 for r in group if r.get("degree")) / len(group), 3))
        if any(v is not None for v in series["skills_mean"]):
            out["levels"][level] = series
    return out


# A generic skill counts as covered by the best of the specific ones it stands for: an ad that
# only says "cloud" is met by someone strong on any one provider.
COVERED_BY = {"Cloud (unspecified)": ["AWS", "Azure (incl. Azure OpenAI)", "GCP / Vertex AI"]}
LEARN_FAMILIES = ["ai_ml", "fde"]  # the roles he is aiming at; backend and data dilute the list
COMFORTABLE = 3  # a rating that meets an ad's ask


def effective_rating(skill: str, ratings: dict[str, int]) -> int:
    return max([ratings.get(skill, 0), *(ratings.get(s, 0) for s in COVERED_BY.get(skill, []))])


def learn_next(rows: list[dict], ratings: dict[str, int], premium: dict, categories: dict[str, str],
               families: list[str] = LEARN_FAMILIES, top: int = 12) -> list[dict]:
    """Skills worth learning next for the target roles. Two parts: how often target ads ask for
    the skill, scaled by the gap to a comfortable rating, and how many ads it is the *last* gap
    in (every other skill they ask for is already rated 3+), which favours skills that complete
    jobs in his direction over common ones that come bundled with other gaps. A pay premium adds
    a bonus. Skills already rated 3+ are left out."""
    target = [r for r in rows if r["role_family"] in families and r["skills"]]
    if len(target) < MIN_GROUP:
        return []
    demand = shares(target)
    last_gap: Counter[str] = Counter()
    for r in target:
        gaps = {s for s in r["skills"] if effective_rating(s, ratings) < COMFORTABLE}
        if len(gaps) == 1:
            last_gap[gaps.pop()] += 1
    premiums = {e["name"]: e["premium"] for e in premium.get("skills", [])}
    out = []
    for skill, share in demand.items():
        rating = effective_rating(skill, ratings)
        if rating >= COMFORTABLE or share < 0.05:
            continue
        unlocks = last_gap[skill] / len(target)
        bonus = max(0.0, premiums.get(skill, 0.0))
        score = (share * (COMFORTABLE - rating) / COMFORTABLE + 10 * unlocks) * (1 + bonus)
        out.append({"name": skill, "category": categories.get(skill, "Other"), "demand": round(share, 3),
                    "unlocks": last_gap[skill], "n_target": len(target), "rating": ratings.get(skill),
                    "premium": premiums.get(skill), "score": round(score, 4)})
    out.sort(key=lambda e: -e["score"])
    return out[:top]
