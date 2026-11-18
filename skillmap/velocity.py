"""How fast the market moves: new postings per week, and how long ads stay up.

- New per week counts postings by the week their ad says it was posted, so the first run's
  backlog of older ads lands in the right weeks instead of all on the first day. Postings
  without a posted date are left out of this series.
- Time open: an ad that stops appearing has (very likely) been filled or pulled. Days from
  posting to the last run that saw it, for ads that closed in the last 90 days, is a demand
  signal: roles and skills that close fast are hard to staff or quick to fill. Ads still up
  are reported separately by how old they are. A closed ad needs two weekly runs to be seen
  closing, so these figures fill in over the first weeks of data.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta

# A weekly run that misses an ad marks it closed; a week and a day leaves room for a late run.
CLOSED_AFTER = 8
CLOSED_WINDOW = 90
WEEKS = 26
MIN_GROUP = 30


def _week(day: date) -> date:
    return day - timedelta(days=day.weekday())


def weekly_new(rows: list[dict], today: date, families: list[str], weeks: int = WEEKS) -> dict:
    start = _week(today) - timedelta(weeks=weeks - 1)
    labels = [start + timedelta(weeks=i) for i in range(weeks)]
    total: Counter[date] = Counter()
    by_family: dict[str, Counter[date]] = defaultdict(Counter)
    for r in rows:
        posted = r.get("posted_on")
        if not posted or posted < start or posted > today:
            continue
        week = _week(posted)
        total[week] += 1
        by_family[r["role_family"]][week] += 1
    return {
        "weeks": [w.isoformat() for w in labels],
        "n": [total[w] for w in labels],
        "by_role": {f: [by_family[f][w] for w in labels] for f in families if by_family.get(f)},
        "note": "Postings by the week their ad was posted; ads without a posted date are left out.",
    }


def _days_open(r: dict) -> int | None:
    posted = r.get("posted_on") or r["first_seen"]
    days = (r["last_seen"] - posted).days
    return days if days >= 0 else None


def _summary(values: list[int]) -> dict:
    if not values:
        return {"n": 0, "median": None}
    q = statistics.quantiles(values, n=4) if len(values) >= 4 else [min(values), statistics.median(values), max(values)]
    return {"n": len(values), "median": statistics.median(values), "p25": q[0], "p75": q[2]}


def time_open(rows: list[dict], today: date, families: list[str], skills: list[str]) -> dict:
    cutoff = today - timedelta(days=CLOSED_AFTER)
    since = today - timedelta(days=CLOSED_WINDOW)
    closed = [r for r in rows if since <= r["last_seen"] <= cutoff]
    live = [r for r in rows if r["last_seen"] > cutoff]

    def age(r: dict) -> int | None:
        days = (today - (r.get("posted_on") or r["first_seen"])).days
        return days if days >= 0 else None

    def grouped(group_rows: list[dict], days, keys: list[str]) -> list[dict]:
        out = []
        for k in keys:
            values = [d for r in group_rows if r["role_family"] == k and (d := days(r)) is not None]
            if len(values) >= MIN_GROUP:
                out.append({"key": k, **_summary(values)})
        return out

    by_skill = []
    for name in skills:
        values = [d for r in closed if name in (r.get("skills") or []) and (d := _days_open(r)) is not None]
        if len(values) >= MIN_GROUP:
            by_skill.append({"name": name, **_summary(values)})
    by_skill.sort(key=lambda s: s["median"])

    return {
        "closed_window_days": CLOSED_WINDOW,
        "closed": {"all": _summary([d for r in closed if (d := _days_open(r)) is not None]),
                   "by_role": grouped(closed, _days_open, families),
                   "by_skill": by_skill},
        "open_age": {"all": _summary([a for r in live if (a := age(r)) is not None]),
                     "by_role": grouped(live, age, families)},
    }
