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
from statistics import median, quantiles

from .extract import roles, salary, sector

SOURCE_PRIORITY = ["greenhouse", "lever", "ashby", "workable", "smartrecruiters", "workday", "amazon", "microsoft", "jsonld", "mcf", "adzuna"]  # who wins a duplicate
MIN_GROUP = 30
CURRENT_DAYS = 30
SEGMENT_DAYS = 90
SALARY_DAYS = 180
TOP_CURRENT = 60
TOP_TREND = 30
TOP_SEGMENT = 40
TARGET_FAMILIES = ["ai_ml", "fde", "backend", "data"]
SENIORITY_ORDER = ["intern", "junior", "mid", "senior", "staff", "manager", "unspecified"]
TOP_GROUP_TREND = 12
PAY_MIN = 20  # postings with stated pay before a country gets a figure
PAY_CELL = 10  # ...and before a role × seniority cell does
PAY_COUNTRIES = 4


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
            merged["soft"] = sorted(set().union(*(r.get("soft") or [] for r in members)))
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


def trend_series(rows: list[dict], skills: list[str], today: date, step: int = 1) -> dict:
    """Share of postings live in each period that ask for each skill. `step` is the period length
    in months: 1 for the headline chart, 3 (quarters) for the thinner per-role and per-level lines."""
    if not rows:
        return {"months": [], "n": [], "shares": {}}
    first = min(r["first_seen"] for r in rows)
    months = [m for m in month_starts(first, today) if (m.month - 1) % step == 0 or step == 1]
    if step > 1:
        quarter_start = first.replace(day=1, month=first.month - (first.month - 1) % step)
        months = [quarter_start, *[m for m in months if m > quarter_start]]
    out = {"months": [m.isoformat()[:7] for m in months], "n": [], "shares": {s: [] for s in skills}}
    for month in months:
        last_month = month
        for _ in range(step - 1):
            last_month = (last_month.replace(day=28) + timedelta(days=4)).replace(day=1)
        group = active(rows, month, min(month_end(last_month), today))
        out["n"].append(len(group))
        month_shares = shares(group, skills) if len(group) >= MIN_GROUP else {}
        for skill in skills:
            out["shares"][skill].append(month_shares.get(skill))
    return out


SOFT_CATEGORY = "Soft skills"


def split_soft(rows: list[dict], categories: dict[str, str]) -> list[dict]:
    """Move soft skills out of `skills` into their own `soft` list. Every tech-skill chart, the
    watchlist and learn-next read `skills`, and "communication" in half the ads would crowd them;
    soft skills get their own block instead. Running it twice changes nothing."""
    soft = {name for name, category in categories.items() if category == SOFT_CATEGORY}
    out = []
    for r in rows:
        skills = r.get("skills") or []
        mine = sorted(set(r.get("soft") or []) | {s for s in skills if s in soft})
        out.append({**r, "skills": [s for s in skills if s not in soft],
                    "skills_nice": [s for s in r.get("skills_nice") or [] if s not in soft], "soft": mine})
    return out


def soft_block(window: list[dict], pay_rows: list[dict], names: list[str]) -> dict | None:
    """Soft skills on their own: overall share, by role family and seniority, and pay premium."""
    from . import insights

    if not names:
        return None
    rows = [{**r, "skills": r.get("soft") or []} for r in window]
    overall = shares(rows, names)
    ranked = sorted(names, key=lambda n: -overall[n])
    return {
        "n": len(rows),
        "skills": [{"name": n, "share": overall[n]} for n in ranked],
        "role_family": segment_block(rows, "role_family", ranked, roles.FAMILIES, roles.LABELS),
        "seniority": segment_block(rows, "seniority", ranked, SENIORITY_ORDER),
        "premium": insights.pay_premium([{**r, "skills": r.get("soft") or []} for r in pay_rows], ranked),
    }


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


def group_trends(market: list[dict], hn: list[dict], key: str, keys: list[str], today: date) -> dict:
    """Quarterly skill trends inside each role family (or seniority level): its own top skills,
    live market and HN kept apart as everywhere else."""
    out = {}
    recent = active(market, today - timedelta(days=SEGMENT_DAYS - 1), today)
    for value in keys:
        mine = [r for r in market if r.get(key) == value]
        theirs = [r for r in hn if r.get(key) == value]
        basis = [r for r in recent if r.get(key) == value] or active(theirs, today - timedelta(days=365), today)
        if len(basis) < MIN_GROUP:
            continue
        top = [name for name, _ in sorted(shares(basis).items(), key=lambda kv: (-kv[1], kv[0]))][:TOP_GROUP_TREND]
        out[value] = {
            "n": len(basis),
            "skills": top,
            "market": trend_series(mine, top, today, step=3),
            "hn": trend_series(theirs, top, today, step=3),
        }
    return out


def _pay_points(rows: list[dict]) -> list[tuple[dict, float, float]]:
    """(row, midpoint of the yearly range in its own currency, same in USD) for every posting
    that states pay in a period and currency we can convert."""
    points = []
    for r in rows:
        low = salary.annual(r.get("salary_min"), r.get("salary_period"))
        if not low or not r.get("salary_currency"):
            continue
        high = salary.annual(r.get("salary_max"), r.get("salary_period")) or low
        mid = (low + max(high, low)) / 2
        usd = salary.to_usd(mid, r["salary_currency"])
        if usd and 4_000 <= usd <= 1_500_000:
            points.append((r, mid, usd))
    return points


def _spread(values: list[float]) -> dict:
    p25, p50, p75 = quantiles(values, n=4) if len(values) >= 4 else (min(values), median(values), max(values))
    return {"median": round(p50, -2), "p25": round(p25, -2), "p75": round(p75, -2)}


PAY_LEVELS: dict[str, set[str] | None] = {
    "all": None, "junior": {"intern", "junior"}, "mid": {"mid"}, "senior": {"senior"}, "staff": {"staff"},
    "manager": {"manager"}, "mid_senior": {"mid", "senior"},
}


def _pay_countries(points: list[tuple[dict, float, float]]) -> list[dict]:
    """Per country with PAY_MIN+ ads: count, median and middle half in the ads' main currency
    (with how many ads use it) and in USD, busiest country first."""
    by_country: dict[str, list[tuple[dict, float, float]]] = defaultdict(list)
    for point in points:
        by_country[point[0].get("country") or "Unknown"].append(point)
    out = []
    for country, group in sorted(by_country.items(), key=lambda kv: -len(kv[1])):
        if country == "Unknown" or len(group) < PAY_MIN:
            continue
        currency = Counter(r["salary_currency"] for r, _, _ in group).most_common(1)[0][0]
        local = [mid for r, mid, _ in group if r["salary_currency"] == currency]
        out.append({"key": country, "n": len(group), "currency": currency, "n_local": len(local),
                    "local": _spread(local), "usd": _spread([usd for _, _, usd in group])})
    return out


def pay_block(rows: list[dict]) -> dict:
    """Advertised yearly pay wherever it is stated: per country (local currency and USD), and per
    role family × seniority within the best-covered countries (USD, so levels compare)."""
    points = _pay_points(rows)
    by_country: dict[str, list[tuple[dict, float, float]]] = defaultdict(list)
    for point in points:
        by_country[point[0].get("country") or "Unknown"].append(point)
    countries = _pay_countries(points)
    # The same table for one role family and/or level, keyed "family|level" ("all" for either),
    # so countries can be compared like for like: a country of mostly senior ads otherwise looks
    # better paid than one of mostly junior ads.
    filtered = {}
    for family in ["all", *roles.FAMILIES]:
        for level, members in PAY_LEVELS.items():
            if family == "all" and level == "all":
                continue
            subset = [p for p in points if (family == "all" or p[0]["role_family"] == family)
                      and (members is None or p[0]["seniority"] in members)]
            table = _pay_countries(subset)
            if table:
                filtered[f"{family}|{level}"] = table
    matrix = {}
    for entry in countries[:PAY_COUNTRIES]:
        cells: dict[str, dict[str, dict]] = {}
        for family in roles.FAMILIES:
            for level in SENIORITY_ORDER:
                values = [usd for r, _, usd in by_country[entry["key"]]
                          if r["role_family"] == family and r["seniority"] == level]
                if len(values) >= PAY_CELL:
                    cells.setdefault(family, {})[level] = {"n": len(values), **_spread(values)}
        matrix[entry["key"]] = cells
    return {
        "period": "year",
        "fx_note": "Converted to USD at fixed November 2026 rates",
        "n": len(points),
        "n_rows": len(rows),
        "countries": countries,
        "filtered": filtered,
        "role_seniority_usd": matrix,
    }


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
    """The heatmap: market demand per target role family next to the owner's own rating."""
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


def changes_block(market: list[dict], hn: list[dict], today: date) -> dict:
    """Everything about change over time beyond single-skill trend lines (see trends.py)."""
    from . import trends  # trends builds on this module's helpers

    regions = [k for k, _ in Counter(r.get("region") or "Unknown" for r in market).most_common(8)]
    full_text = [r for r in market if r["source"] != "adzuna"]
    since = min((r["first_seen"] for r in market), default=None)
    return {
        "movers": trends.movers(market, today, since=since),
        # Month on month: the first live comparison, about two months after collection starts.
        "movers_30d": trends.movers(market, today, days=30, since=since),
        "movers_by_role": trends.grouped_movers(market, today, "role_family", roles.FAMILIES, since=since),
        "movers_by_seniority": trends.grouped_movers(market, today, "seniority", SENIORITY_ORDER[:-1], since=since),
        "hn_movers": trends.movers(hn, today, days=365),
        "mix": {
            "role_family": {"market": trends.mix_series(market, "role_family", roles.FAMILIES, today),
                            "hn": trends.mix_series(hn, "role_family", roles.FAMILIES, today)},
            "seniority": {"market": trends.mix_series(market, "seniority", SENIORITY_ORDER, today),
                          "hn": trends.mix_series(hn, "seniority", SENIORITY_ORDER, today)},
            "sector": {"market": trends.mix_series(market, "sector", sector.SECTORS, today)},
            "region": {"market": trends.mix_series(market, "region", regions, today)},
        },
        "per_post": {"market": trends.per_post_series(full_text, today),
                     "hn": trends.per_post_series(hn, today, full_text=False)},
        "companies": trends.companies(market, today),
        "terms": trends.emerging_terms(full_text, today),
    }


def velocity_block(market: list[dict], today: date, skills: list[str], tracked_since: date | None = None) -> dict:
    from . import velocity

    return {"new_per_week": velocity.weekly_new(market, today, roles.FAMILIES, since=tracked_since),
            "time_open": velocity.time_open(market, today, roles.FAMILIES, skills)}


def insights_block(market: list[dict], window: list[dict], pay_rows: list[dict], today: date,
                   categories: dict[str, str], ratings: dict[str, int]) -> dict:
    from . import insights  # builds on this module's helpers

    premium = insights.pay_premium(pay_rows, sorted(categories))
    return {
        "pay_premium": premium,
        "role_dna": insights.role_dna(window),
        "seniority_ladder": insights.seniority_ladder(window),
        "stacks": insights.stacks(window),
        "requirement_creep": insights.requirement_creep([r for r in market if r["source"] != "adzuna"], today),
        "learn_next": insights.learn_next(window, ratings, premium, categories),
    }


def observed(rows: list[dict], first_runs: dict[str, date]) -> list[dict]:
    """Market postings as live only from when this census first read their source.

    A board lists only the ads still open, so an ad posted in 2023 and still up today says
    nothing about 2023's market; counting it there would build "history" from survivors. Each
    posting therefore counts as live from the later of its own start and its source's first run.
    Sources with no recorded run are left as they are."""
    out = []
    for r in rows:
        start = first_runs.get(r["source"])
        out.append({**r, "first_seen": max(r["first_seen"], start)} if start and r["first_seen"] < start else r)
    return out


def build(rows: list[dict], today: date, categories: dict[str, str], taxonomy_version: int,
          ratings: dict[str, int] | None = None, runs: list[dict] | None = None,
          first_runs: dict[str, date] | None = None) -> dict:
    soft_names = sorted(n for n, c in categories.items() if c == SOFT_CATEGORY)
    rows = split_soft(rows, categories)
    categories = {n: c for n, c in categories.items() if c != SOFT_CATEGORY}
    all_rows = dedupe(rows)
    tech = [r for r in all_rows if r["role_family"] != "non_tech"]
    first_runs = {s: d for s, d in (first_runs or {}).items() if d <= today}
    market = observed([r for r in tech if r["source"] != "hn"], first_runs)
    tracked_since = min((d for s, d in first_runs.items() if s != "hn"), default=None)
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
            # Live-market series and comparisons only cover time since this date (see observed()).
            "tracked_since": tracked_since.isoformat() if tracked_since else None,
            "hn_first": min((r["first_seen"] for r in hn), default=today).isoformat(),
            "runs": runs or [],
            "attribution": ["Job data: The Adzuna API", "Hacker News “Who is hiring?” via the HN Algolia API",
                            "MyCareersFuture (Singapore)",
                            "Company boards on Greenhouse, Lever, Ashby, Workable, SmartRecruiters and Workday",
                            "Amazon and Microsoft careers sites"],
        },
        "categories": sorted(set(categories.values())),
        "current": current,
        "trends": {
            "skills": trend_skills,
            "market": trend_series(market, trend_skills, today),
            "hn": trend_series(hn, trend_skills, today),
            "by_role": group_trends(market, hn, "role_family", roles.FAMILIES, today),
            "by_seniority": group_trends(market, hn, "seniority", SENIORITY_ORDER[:-1], today),
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
        "pay": pay_block(active(market, today - timedelta(days=SALARY_DAYS - 1), today)),
        "changes": changes_block(market, hn, today),
        "velocity": velocity_block(market, today, current_names[:TOP_TREND], tracked_since),
        "insights": insights_block(market, window, active(market, today - timedelta(days=SALARY_DAYS - 1), today), today,
                                   categories, ratings or {}),
        "soft_skills": soft_block(window, active(market, today - timedelta(days=SALARY_DAYS - 1), today), soft_names),
        "pairs": pairs_block(window, current_names[:20]),
        "me": me_block(window, ratings or {}, categories),
    }
