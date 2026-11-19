"""What is changing: skill risers and fallers, the mix of roles and levels, the shape of the
average ad, companies hiring more or less, and terms that are new to the taxonomy.

Pure functions of the stored rows, like aggregate.py, which calls them. Every comparison is
between two equal windows of live postings, and a group thinner than MIN_GROUP in either
window is left out rather than shown with a misleading swing.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import mean, median

from .aggregate import MIN_GROUP, active, month_end, month_starts, shares

MOVER_DAYS = 90
PAY_MANDATORY = {"mcf"}
TOP_MOVERS = 12
TOP_COMPANIES = 40
TOP_TERMS = 25
MIN_TERM_POSTS = 15


def _windows(rows: list[dict], today: date, days: int) -> tuple[list[dict], list[dict]]:
    now = active(rows, today - timedelta(days=days - 1), today)
    before = active(rows, today - timedelta(days=2 * days - 1), today - timedelta(days=days))
    return now, before


def movers(rows: list[dict], today: date, days: int = MOVER_DAYS, min_share: float = 0.02,
           since: date | None = None) -> dict | None:
    """Skills whose share rose or fell most between the last `days` and the `days` before.

    With `since` (when collection started), nothing is returned until both windows lie wholly
    after it: a half-collected earlier window would compare a few days against a full month.

    Ranked by percentage-point change; `ratio` says how many times more (or less) common the
    skill is now. Skills under `min_share` in both windows are noise and skipped.
    """
    if since and today - timedelta(days=2 * days - 1) < since:
        return None
    now, before = _windows(rows, today, days)
    if len(now) < MIN_GROUP or len(before) < MIN_GROUP:
        return None
    a, b = shares(now), shares(before)
    changes = []
    for name in set(a) | set(b):
        s_now, s_before = a.get(name, 0.0), b.get(name, 0.0)
        if max(s_now, s_before) < min_share:
            continue
        changes.append({
            "name": name, "now": round(s_now, 4), "before": round(s_before, 4), "delta": round(s_now - s_before, 4),
            "ratio": round(s_now / s_before, 2) if s_before else None,
        })
    changes.sort(key=lambda c: -c["delta"])
    return {
        "days": days, "n_now": len(now), "n_before": len(before),
        "risers": [c for c in changes if c["delta"] > 0][:TOP_MOVERS],
        "fallers": [c for c in reversed(changes) if c["delta"] < 0][:TOP_MOVERS],
    }


def grouped_movers(rows: list[dict], today: date, key: str, keys: list[str], days: int = MOVER_DAYS,
                   since: date | None = None) -> dict:
    out = {}
    for value in keys:
        block = movers([r for r in rows if r.get(key) == value], today, days, since=since)
        if block:
            out[value] = block
    return out


def _periods(rows: list[dict], today: date) -> list[tuple[str, list[dict]]]:
    if not rows:
        return []
    first = min(r["first_seen"] for r in rows)
    return [(m.isoformat()[:7], active(rows, m, min(month_end(m), today))) for m in month_starts(first, today)]


def mix_series(rows: list[dict], key: str, keys: list[str], today: date) -> dict:
    """Month by month, the share of postings in each value of `key` (role family, seniority…)."""
    out = {"months": [], "n": [], "shares": {k: [] for k in keys}}
    for month, group in _periods(rows, today):
        out["months"].append(month)
        out["n"].append(len(group))
        counts = Counter(r.get(key) for r in group)
        for k in keys:
            out["shares"][k].append(round(counts.get(k, 0) / len(group), 4) if len(group) >= MIN_GROUP else None)
    return out


def _rate(group: list[dict], test) -> float:
    return round(sum(1 for r in group if test(r)) / len(group), 4)


def per_post_series(rows: list[dict], today: date, full_text: bool = True) -> dict:
    """The shape of the average ad, month by month: how many skills it asks for, how long it is,
    how many years it wants, and what it states or offers. `full_text` is False for sources whose
    text is a snippet, where lengths and counts would mean little."""
    metrics = ["skills_mean", "skills_median", "required_mean", "nice_mean", "nice_any", "words_median",
               "years_median", "remote", "pay_stated", "degree_any", "degree_phd", "visa_yes", "visa_no", "equity"]
    out = {"months": [], "n": [], **{m: [] for m in metrics}}
    for month, group in _periods(rows, today):
        out["months"].append(month)
        out["n"].append(len(group))
        if len(group) < MIN_GROUP:
            for m in metrics:
                out[m].append(None)
            continue
        counts = [len(r["skills"]) for r in group]
        nice = [len(r.get("skills_nice") or []) for r in group]
        years = [r["min_years"] for r in group if r.get("min_years")]
        values = {
            "skills_mean": round(mean(counts), 2),
            "skills_median": median(counts),
            "required_mean": round(mean(c - n for c, n in zip(counts, nice, strict=True)), 2),
            "nice_mean": round(mean(nice), 2),
            # Most ads don't split required from preferred; nice_mean only counts the ones that do.
            "nice_any": _rate(group, lambda r: r.get("skills_nice")),
            "words_median": median(r.get("n_words") or 0 for r in group) if full_text else None,
            "years_median": median(years) if len(years) >= MIN_GROUP else None,
            "remote": _rate(group, lambda r: r.get("remote")),
            # MyCareersFuture makes a range mandatory, so its ads would only measure the rule.
            "pay_stated": _rate([r for r in group if r.get("source") not in PAY_MANDATORY] or group,
                                lambda r: r.get("salary_min")),
            "degree_any": _rate(group, lambda r: r.get("degree")),
            "degree_phd": _rate(group, lambda r: r.get("degree") == "phd"),
            "visa_yes": _rate(group, lambda r: r.get("visa_sponsorship") is True),
            "visa_no": _rate(group, lambda r: r.get("visa_sponsorship") is False),
            "equity": _rate(group, lambda r: r.get("equity")),
        }
        for m in metrics:
            out[m].append(values[m])
    return out


def companies(rows: list[dict], today: date, days: int = 30) -> dict:
    """Who is hiring most now, who is hiring more or less than a month ago, and what each asks for."""
    now, before = _windows(rows, today, days)
    by_now: dict[str, list[dict]] = defaultdict(list)
    for r in now:
        by_now[r["company"]].append(r)
    n_before = Counter(r["company"] for r in before)

    def card(name: str) -> dict:
        group = by_now.get(name, [])
        top = sorted(shares(group).items(), key=lambda kv: (-kv[1], kv[0]))[:6] if group else []
        families = Counter(r["role_family"] for r in group).most_common(3)
        return {
            "name": name, "n": len(group), "n_before": n_before.get(name, 0), "delta": len(group) - n_before.get(name, 0),
            "sector": Counter(r["sector"] for r in group).most_common(1)[0][0] if group else None,
            "families": [{"key": k, "n": n} for k, n in families],
            "skills": [{"name": s, "share": round(v, 3)} for s, v in top],
            "remote": _rate(group, lambda r: r.get("remote")) if group else None,
        }

    largest = [name for name, _ in sorted(by_now.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:TOP_COMPANIES]]
    growing = sorted((n for n in by_now if len(by_now[n]) >= 10 and n_before.get(n, 0) >= 5),
                     key=lambda n: -(len(by_now[n]) - n_before[n]))[:15]
    shrinking = sorted((n for n in n_before if n_before[n] >= 10),
                       key=lambda n: len(by_now.get(n, [])) - n_before[n])[:15]
    return {
        "days": days,
        "n_companies": len(by_now),
        "largest": [card(n) for n in largest],
        "growing": [card(n) for n in growing if len(by_now[n]) > n_before[n]],
        "shrinking": [card(n) for n in shrinking if len(by_now.get(n, [])) < n_before[n]],
    }


def emerging_terms(rows: list[dict], today: date, days: int = MOVER_DAYS) -> dict:
    """Tech-looking terms the taxonomy doesn't know yet, most frequent now and fastest growing.

    Counted once per company per window, so one company repeating a product name in fifty ads
    doesn't look like a market trend.
    """
    now, before = _windows([r for r in rows if r.get("terms")], today, days)

    def companies_using(group: list[dict]) -> Counter:
        pairs = {(term, r["company"]) for r in group for term in r["terms"]}
        return Counter(term for term, _ in pairs)

    c_now, c_before = companies_using(now), companies_using(before)
    common = [{"term": t, "companies": n, "companies_before": c_before.get(t, 0)}
              for t, n in c_now.most_common() if n >= MIN_TERM_POSTS][:TOP_TERMS]
    # Without a comparable earlier window every term would look new, so nothing is called rising.
    rising = [] if len(before) < MIN_GROUP else sorted(
        ({"term": t, "companies": n, "companies_before": c_before.get(t, 0)} for t, n in c_now.items()
         if n >= MIN_TERM_POSTS and n >= 2 * c_before.get(t, 0)),
        key=lambda e: -(e["companies"] - e["companies_before"]),
    )[:TOP_TERMS]
    return {"days": days, "n_postings": len(now), "common": common, "rising": rising}
