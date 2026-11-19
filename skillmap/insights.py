"""Derived insights: what a skill is worth, what sets roles and levels apart, which skills go
together, how requirements creep, and what to learn next for the configured target roles.

Pure functions of the stored rows (plus his self-ratings), like aggregate.py, which calls them.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median, variance

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
    averaged weighted by the smaller side. Reported as a percentage, with the matched count, a
    90% interval (from the spread inside each stratum, so a premium resting on 25 ads shows as
    the guess it is), the share of paid ads asking for the skill, and the gap in US dollars a
    year at the matched ads' typical pay.
    """
    points = _pay_points(rows)
    strata: dict[tuple, list[tuple[set[str], float]]] = defaultdict(list)
    for r, _, usd in points:
        strata[(r.get("country"), r["role_family"], r["seniority"])].append((set(r["skills"]), math.log(usd)))
    counts = Counter(skill for r, _, _ in points for skill in set(r["skills"]))
    out = []
    for skill in skills:
        total_w, total, n_with, var, base = 0.0, 0.0, 0, 0.0, []
        for members in strata.values():
            yes = [v for s, v in members if skill in s]
            no = [v for s, v in members if skill not in s]
            if len(yes) < STRATUM_MIN or len(no) < STRATUM_MIN:
                continue
            weight = min(len(yes), len(no))
            total += weight * (median(yes) - median(no))
            total_w += weight
            n_with += len(yes)
            # Variance of a median is about pi/2 times that of a mean.
            var += weight ** 2 * (math.pi / 2) * (variance(yes) / len(yes) + variance(no) / len(no))
            base.extend(no)
        if n_with >= PREMIUM_MIN and total_w:
            gap, se = total / total_w, math.sqrt(var) / total_w
            premium = math.exp(gap) - 1
            out.append({"name": skill, "premium": round(premium, 3), "n": n_with,
                        "low": round(math.exp(gap - 1.645 * se) - 1, 3), "high": round(math.exp(gap + 1.645 * se) - 1, 3),
                        "share": round(counts[skill] / len(points), 4),
                        "usd": round(premium * math.exp(median(base)), -3)})
    out.sort(key=lambda e: -e["premium"])
    return {"n_with_pay": len(points), "skills": out}


PREMIUM_FAMILIES = ["ai_ml", "fde", "backend", "data", "infra", "frontend", "quant"]


def pay_premium_by_role(rows: list[dict], skills: list[str]) -> dict:
    """The same premium inside each role family that has enough paid ads: within AI/ML, what
    does PyTorch add over other AI/ML ads? Families too thin for any skill are left out."""
    out = {}
    for family in PREMIUM_FAMILIES:
        block = pay_premium([r for r in rows if r["role_family"] == family], skills)
        if block["skills"]:
            out[family] = {"label": roles.LABELS[family], **block}
    return out


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
COVERED_BY = {"Cloud (unspecified)": ["AWS", "Azure", "GCP"]}
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


EDGE_FAMILIES = ["ai_ml", "fde", "backend", "data", "infra", "frontend"]
EDGE_MAX_SHARE = 0.25  # past this a skill is the baseline everyone has, not an edge
EDGE_MIN_ADS = 15
EDGE_MIN_LIFT = 1.0  # at least as common in the role as across tech: role-relevant, not an outside skill
EDGE_TOP = 25
EDGE_SPLIT_MIN = 10  # ads that both name the skill and separate nice-to-haves, for the nice ratio
EDGE_LEVEL_MIN = 40  # ads on each side of the senior/junior comparison
EDGE_HN_MIN = 8  # HN posts naming the skill in the recent year, for momentum
EDGE_FRONTIER_MIN = 40  # frontier-company ads in the role, for the frontier ratio
EDGE_LAB_MIN = 5  # a frontier company's ads in the role before its asks count
EDGE_LABS_MIN = 3  # frontier companies that must name a skill, so no one vendor makes the signal
EDGE_NEAR_MIN = 2  # of my 3+ skills an ad must also name to count as next to my stack


def _clip(x: float, low: float, high: float) -> float:
    return max(low, min(high, x))


def hn_momentum(hn: list[dict], today: date) -> dict[str, dict]:
    """Share of Hacker News hiring posts naming each skill: the last year against the year before."""

    now = [r for r in hn if today - timedelta(days=364) <= r["first_seen"] <= today]
    before = [r for r in hn if today - timedelta(days=729) <= r["first_seen"] < today - timedelta(days=364)]
    if len(now) < 200 or len(before) < 200:
        return {}
    c_now = Counter(s for r in now for s in set(r["skills"]))
    c_before = Counter(s for r in before for s in set(r["skills"]))
    out = {}
    for skill, n in c_now.items():
        if n < EDGE_HN_MIN:
            continue
        share, share_before = n / len(now), c_before.get(skill, 0) / len(before)
        # A skill absent the year before counts as 1 post, so a new name reads as fast-rising, not infinite.
        out[skill] = {"share": round(share, 4), "share_before": round(share_before, 4),
                      "ratio": round(share / max(share_before, 1 / len(before)), 2)}
    return out


def edge_skills(window: list[dict], momentum: dict[str, dict], premium_by_role: dict, ratings: dict[str, int],
                family: str, frontier: frozenset[str] = frozenset()) -> dict | None:
    """Skills that set a candidate apart in one role family, rather than the baseline every ad asks for.

    Only skills in at most a quarter of the family's ads count. Each gets up to five signals, all
    relative so that common skills don't win by volume:
      specific  - how much more common in this family than across all tech ads
      nice      - how often ads that separate requirements list it as nice-to-have: wanted but
                  rarely found, compared with the family's average skill
      senior    - how much more common in senior and staff ads than in junior and mid ones
      pay       - the pay premium within the family (same country and level), when measured
      rising    - its share of Hacker News hiring posts this year against last year
      frontier  - how much more often frontier AI companies' ads name it than everyone else's
    Separately, `near` is the share of the skill's ads that also name at least two of the
    skills I rate 3+, against the role's average: a short step from my stack. It doesn't move
    the score, which is about the market, but the site can sort or mark by it.
    The edge score adds the signals on comparable scales (log ratios, clipped); a missing
    signal adds nothing, so a skill isn't punished for a thin sample.
    """
    fam = [r for r in window if r["role_family"] == family]
    if len(fam) < 100:
        return None
    n = len(fam)
    count = Counter(s for r in fam for s in set(r["skills"]))
    all_share = {s: c / len(window) for s, c in Counter(s for r in window for s in set(r["skills"])).items()}
    split = [r for r in fam if r.get("skills_nice")]
    named_split = Counter(s for r in split for s in set(r["skills"]))
    nice_split = Counter(s for r in split for s in set(r["skills_nice"]))
    base_nice = sum(nice_split.values()) / max(1, sum(named_split.values()))
    senior = [r for r in fam if r["seniority"] in ("senior", "staff")]
    junior = [r for r in fam if r["seniority"] in ("junior", "mid")]
    c_senior = Counter(s for r in senior for s in set(r["skills"]))
    c_junior = Counter(s for r in junior for s in set(r["skills"]))
    levels = len(senior) >= EDGE_LEVEL_MIN and len(junior) >= EDGE_LEVEL_MIN
    pay = {e["name"]: e for e in (premium_by_role.get(family) or {}).get("skills", [])}
    front = [r for r in fam if r["company"].lower() in frontier]
    rest = [r for r in fam if r["company"].lower() not in frontier]
    has_front = len(front) >= EDGE_FRONTIER_MIN and len(rest) >= EDGE_FRONTIER_MIN
    # Each frontier company counts once, whatever its ad volume, so one vendor asking for its
    # own product (Databricks and MLflow) can't make that product look like a frontier signal.
    by_lab: dict[str, list[dict]] = defaultdict(list)
    for r in front:
        by_lab[r["company"].lower()].append(r)
    labs = {k: v for k, v in by_lab.items() if len(v) >= EDGE_LAB_MIN}
    lab_share = {k: Counter(s for r in v for s in set(r["skills"])) for k, v in labs.items()}
    c_rest = Counter(s for r in rest for s in set(r["skills"]))
    mine = {s for s, v in ratings.items() if (v or 0) >= 3}
    near_ads = [r for r in fam if len(mine.intersection(r["skills"])) >= EDGE_NEAR_MIN]
    base_near = len(near_ads) / n
    c_near = Counter(s for r in near_ads for s in set(r["skills"]))

    out = []
    for skill, c in count.items():
        share = c / n
        if c < EDGE_MIN_ADS or share > EDGE_MAX_SHARE:
            continue
        e: dict = {"name": skill, "share": round(share, 4), "n": c, "mine": ratings.get(skill)}
        parts: dict[str, float] = {}
        lift = share / all_share[skill]
        if lift < EDGE_MIN_LIFT:
            continue  # senior infra ads ask for DNS too, but that isn't what sets an AI engineer apart
        e["specific"] = round(lift, 2)
        # Inside a role nearly every role-typical skill is "specific", so this weighs half.
        parts["specific"] = 0.5 * _clip(math.log2(lift), -1, 2)
        if named_split[skill] >= EDGE_SPLIT_MIN:
            ratio = nice_split[skill] / named_split[skill]
            e["nice"] = round(ratio, 3)
            parts["nice"] = _clip(2 * (ratio - base_nice), -1, 1.5)
        if levels and c_senior[skill] + c_junior[skill] >= EDGE_MIN_ADS:
            # Two pseudo-ads on each side, so 12 senior ads against 1 junior isn't a 12x lift.
            ratio = ((c_senior[skill] + 2) / len(senior)) / ((c_junior[skill] + 2) / len(junior))
            e["senior"] = round(ratio, 2)
            parts["senior"] = _clip(math.log2(ratio), -1, 1.5)
        if skill in pay:
            p = pay[skill]
            e.update(premium=p["premium"], premium_low=p.get("low"), premium_high=p.get("high"), usd=p.get("usd"))
            sure = (p.get("low") or 0) > 0 or (p.get("high") or 0) < 0
            parts["pay"] = _clip(p["premium"] * (6 if sure else 2), -1, 1.5)
        if skill in momentum:
            m = momentum[skill]
            e.update(rising=m["ratio"], hn_share=m["share"], hn_share_before=m["share_before"])
            parts["rising"] = _clip(math.log2(m["ratio"]), -1, 1.5)
        naming = sum(1 for k in labs if lab_share[k][skill])
        if has_front and len(labs) >= EDGE_LABS_MIN and naming >= EDGE_LABS_MIN:
            # Drop the keenest company, so a vendor's own product isn't carried by that vendor.
            shares_ = sorted(lab_share[k][skill] / len(labs[k]) for k in labs)[:-1]
            lab_mean = sum(shares_) / len(shares_)
            ratio = (lab_mean + 1 / len(front)) / ((c_rest[skill] + 1) / len(rest))
            e.update(frontier=round(ratio, 2), frontier_share=round(lab_mean, 4), frontier_labs=naming)
            parts["frontier"] = _clip(math.log2(ratio), -1, 1.5)
        if mine and base_near > 0 and skill not in mine:
            e["near"] = round((c_near[skill] / c) / base_near, 2)
        e["score"] = round(sum(parts.values()), 3)
        # The signals that carry it, strongest first, for the one-line "why".
        e["why"] = [k for k, v in sorted(parts.items(), key=lambda kv: -kv[1]) if v >= 0.4][:3]
        out.append(e)
    out.sort(key=lambda e: (-e["score"], e["name"]))
    return {"label": roles.LABELS[family], "n": n, "n_split": len(split), "base_nice": round(base_nice, 3),
            "n_senior": len(senior), "n_junior": len(junior), "n_frontier": len(front) if has_front else 0,
            "skills": out[:EDGE_TOP]}


def edge_block(window: list[dict], hn: list[dict], today: date, premium_by_role: dict,
               ratings: dict[str, int], frontier: frozenset[str] = frozenset()) -> dict:
    momentum = hn_momentum(hn, today)
    families = {f: block for f in EDGE_FAMILIES
                if (block := edge_skills(window, momentum, premium_by_role, ratings, f, frontier)) is not None}
    return {"max_share": EDGE_MAX_SHARE, "momentum_from_hn": bool(momentum), "families": families}
