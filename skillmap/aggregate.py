"""Stored postings → the one JSON file the website reads.

Everything here is a pure function of the rows, so it is tested without a database. Shares are
always "fraction of postings in the group that ask for the skill", never raw counts, so groups
of different sizes compare fairly. Groups under MIN_GROUP postings are reported with their size
and no shares, so a thin slice never shows a confident-looking number.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from statistics import median

from .extract import roles, sector

SOURCE_PRIORITY = ["greenhouse", "lever", "ashby", "workable", "mcf", "adzuna"]  # who wins a duplicate
MIN_GROUP = 30
CURRENT_DAYS = 30
SEGMENT_DAYS = 90
SALARY_DAYS = 180
TOP_CURRENT = 60
TOP_TREND = 30
TOP_SEGMENT = 40
TARGET_FAMILIES = ["ai_ml", "fde", "backend", "data"]
SENIORITY_ORDER = ["intern", "junior", "mid", "senior", "staff", "manager", "unspecified"]


def dedupe(rows: Iterable[dict]) -> list[dict]:
    """One row per job, merging the same ad seen on several sources.

    Rows sharing a dedup key (company + title + country) are only merged *across* sources: a
    company with five open "Software Engineer" seats on its own board keeps five rows. When a
    key spans sources, the highest-priority source's rows are kept and the others dropped; if
    that leaves a single row, it takes the widest live span and the union of skills found.
    HN posts are never merged: they are a separate population.
    """
    out: list[dict] = []
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        (out.append(row) if row["source"] == "hn" else groups[row["dedup_key"]].append(row))
    rank = {source: i for i, source in enumerate(SOURCE_PRIORITY)}
    for members in groups.values():
        best_source = min((r["source"] for r in members), key=lambda s: rank.get(s, 99))
        kept = [r for r in members if r["source"] == best_source]
        if len(kept) == 1 and len(members) > 1:
            merged = dict(kept[0])
            merged["first_seen"] = min(r["first_seen"] for r in members)
            merged["last_seen"] = max(r["last_seen"] for r in members)
            merged["skills"] = sorted(set().union(*(r["skills"] for r in members)))
            kept = [merged]
        out.extend(kept)
    return out


def active(rows: list[dict], start: date, end: date) -> list[dict]:
    return [r for r in rows if r["first_seen"] <= end and r["last_seen"] >= start]


def shares(rows: list[dict], skills: list[str] | None = None) -> dict[str, float]:
    n = len(rows)
    if n == 0:
        return {}
    counts = Counter(skill for r in rows for skill in set(r["skills"]))
    names = skills if skills is not None else list(counts)
    return {name: round(counts.get(name, 0) / n, 4) for name in names}


def month_starts(first: date, last: date) -> list[date]:
    months, cursor = [], first.replace(day=1)
    while cursor <= last:
        months.append(cursor)
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


def month_end(month: date) -> date:
    return (month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def current_block(rows: list[dict], today: date, categories: dict[str, str]) -> dict:
    now = active(rows, today - timedelta(days=CURRENT_DAYS - 1), today)
    before = active(rows, today - timedelta(days=2 * CURRENT_DAYS - 1), today - timedelta(days=CURRENT_DAYS))
    now_shares, before_shares = shares(now), shares(before)
    nice_counts = Counter(s for r in now for s in r.get("skills_nice") or [])
    ranked = sorted(now_shares.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_CURRENT]
    comparable = len(before) >= MIN_GROUP
    return {
        "n": len(now),
        "n_previous": len(before),
        "skills": [
            {
                "name": name,
                "category": categories.get(name, "Other"),
                "share": share,
                "count": round(share * len(now)),
                "nice_share": round(nice_counts.get(name, 0) / len(now), 4) if now else 0,
                "delta": round(share - before_shares.get(name, 0.0), 4) if comparable else None,
            }
            for name, share in ranked
        ],
    }


def trend_series(rows: list[dict], skills: list[str], today: date) -> dict:
    if not rows:
        return {"months": [], "n": [], "shares": {}}
    first = min(r["first_seen"] for r in rows)
    months = month_starts(first, today)
    out = {"months": [m.isoformat()[:7] for m in months], "n": [], "shares": {s: [] for s in skills}}
    for month in months:
        group = active(rows, month, min(month_end(month), today))
        out["n"].append(len(group))
        month_shares = shares(group, skills) if len(group) >= MIN_GROUP else {}
        for skill in skills:
            out["shares"][skill].append(month_shares.get(skill))
    return out


def segment_block(rows: list[dict], key: str, skills: list[str], order: list[str] | None = None,
                  labels: dict[str, str] | None = None, limit: int | None = None) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        value = r.get(key)
        groups["Remote" if key == "region" and r.get("remote") and not value else (value or "Unknown")].append(r)
    keys = [k for k in (order or []) if k in groups] + sorted(
        (k for k in groups if not order or k not in order), key=lambda k: -len(groups[k])
    )
    if limit:
        keys = keys[:limit]
    segments = []
    for k in keys:
        group = groups[k]
        segments.append({
            "key": k,
            "label": (labels or {}).get(k, k),
            "n": len(group),
            "shares": shares(group, skills) if len(group) >= MIN_GROUP else None,
        })
    return {"skills": skills, "segments": segments}


def salary_block(rows: list[dict], skills: list[str]) -> dict:
    """Singapore monthly SGD salaries as advertised (MyCareersFuture requires a range on every ad)."""
    sg = []
    for r in rows:
        if r.get("country") == "SG" and r.get("salary_currency") == "SGD" and r.get("salary_min"):
            factor = 1 / 12 if r.get("salary_period") == "year" else 1
            mid = (float(r["salary_min"]) + float(r.get("salary_max") or r["salary_min"])) / 2 * factor
            sg.append((mid, set(r["skills"])))
    overall = median([mid for mid, _ in sg]) if sg else None
    by_skill = []
    for skill in skills:
        values = [mid for mid, skills_of in sg if skill in skills_of]
        if len(values) >= MIN_GROUP // 3:
            by_skill.append({"name": skill, "median_monthly": round(median(values)), "n": len(values)})
    by_skill.sort(key=lambda item: -item["median_monthly"])
    return {"currency": "SGD", "period": "month", "n": len(sg), "median_monthly": round(overall) if overall else None,
            "skills": by_skill}


def pairs_block(rows: list[dict], skills: list[str], top: int = 5) -> dict:
    """For each skill: the skills most over-represented alongside it (lift = P(B|A) / P(B))."""
    n = len(rows)
    if n == 0:
        return {}
    base = shares(rows)
    out = {}
    for skill in skills:
        with_skill = [r for r in rows if skill in r["skills"]]
        if len(with_skill) < MIN_GROUP:
            continue
        conditional = shares(with_skill)
        candidates = [
            (other, conditional[other], conditional[other] / base[other])
            for other in conditional
            if other != skill and conditional[other] >= 0.15 and base.get(other)
        ]
        candidates.sort(key=lambda c: -c[2])
        out[skill] = [{"name": o, "share": round(s, 3), "lift": round(lift, 2)} for o, s, lift in candidates[:top]]
    return out


def me_block(rows: list[dict], ratings: dict[str, int], categories: dict[str, str]) -> dict:
    """The heatmap: market demand per target role family next to Pierre's own rating."""
    families = {f: [r for r in rows if r["role_family"] == f] for f in TARGET_FAMILIES}
    family_shares = {f: shares(group) for f, group in families.items()}
    target = [r for f in ("ai_ml", "fde", "backend") for r in families[f]]
    ranked = [name for name, _ in sorted(shares(target).items(), key=lambda kv: -kv[1])]
    picked = ranked[:24] + [s for s in ratings if s not in ranked[:24] and s in categories][:6]
    return {
        "families": [{"key": f, "label": roles.LABELS[f], "n": len(families[f])} for f in TARGET_FAMILIES],
        "rows": [
            {
                "name": skill,
                "category": categories.get(skill, "Other"),
                "demand": {f: family_shares[f].get(skill, 0.0) for f in TARGET_FAMILIES},
                "rating": ratings.get(skill),
            }
            for skill in picked
        ],
    }


def build(rows: list[dict], today: date, categories: dict[str, str], taxonomy_version: int,
          ratings: dict[str, int] | None = None, runs: list[dict] | None = None) -> dict:
    all_rows = dedupe(rows)
    tech = [r for r in all_rows if r["role_family"] != "non_tech"]
    market = [r for r in tech if r["source"] != "hn"]
    hn = [r for r in tech if r["source"] == "hn"]

    current = current_block(market, today, categories)
    current_names = [s["name"] for s in current["skills"]]
    recent_hn = active(hn, today - timedelta(days=365), today)
    hn_top = [name for name, _ in sorted(shares(recent_hn).items(), key=lambda kv: -kv[1])]
    trend_skills = list(dict.fromkeys(current_names[:TOP_TREND] + hn_top[:TOP_TREND]))[: TOP_TREND + 10]

    window = active(market, today - timedelta(days=SEGMENT_DAYS - 1), today)
    seg_skills = [name for name, _ in sorted(shares(window).items(), key=lambda kv: -kv[1])][:TOP_SEGMENT]

    return {
        "meta": {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "as_of": today.isoformat(),
            "taxonomy_version": taxonomy_version,
            "windows": {"current_days": CURRENT_DAYS, "segment_days": SEGMENT_DAYS, "salary_days": SALARY_DAYS},
            "min_group": MIN_GROUP,
            "totals": {
                "postings": len(all_rows),
                "tech": len(tech),
                "market_tech": len(market),
                "hn_tech": len(hn),
                "by_source": dict(Counter(r["source"] for r in rows)),
                "duplicates_merged": len(rows) - len(all_rows),
            },
            "first_seen": min((r["first_seen"] for r in market), default=today).isoformat(),
            "hn_first": min((r["first_seen"] for r in hn), default=today).isoformat(),
            "runs": runs or [],
            "attribution": ["Job data: The Adzuna API", "Hacker News “Who is hiring?” via the HN Algolia API",
                            "MyCareersFuture (Singapore)", "Company boards on Greenhouse, Lever, Ashby and Workable"],
        },
        "categories": sorted(set(categories.values())),
        "current": current,
        "trends": {
            "skills": trend_skills,
            "market": trend_series(market, trend_skills, today),
            "hn": trend_series(hn, trend_skills, today),
        },
        "segments": {
            "window_days": SEGMENT_DAYS,
            "n": len(window),
            "seniority": segment_block(window, "seniority", seg_skills, SENIORITY_ORDER),
            "role_family": segment_block(window, "role_family", seg_skills, roles.FAMILIES, roles.LABELS),
            "region": segment_block(window, "region", seg_skills),
            "country": segment_block(window, "country", seg_skills, limit=10),
            "sector": segment_block(window, "sector", seg_skills, sector.SECTORS, sector.LABELS),
        },
        "salary": salary_block(active(market, today - timedelta(days=SALARY_DAYS - 1), today), seg_skills),
        "pairs": pairs_block(window, current_names[:20]),
        "me": me_block(window, ratings or {}, categories),
    }
