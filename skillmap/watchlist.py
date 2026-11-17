"""data/watchlist.json: the company job boards most worth watching for the targets in config/my_skills.yaml.

Omnideck's `career.discovery` agent reads Greenhouse, Lever and Ashby boards for the companies
on the owner's watchlist (profile `watch_greenhouse` / `watch_lever` / `watch_ashby`, rotated
across days when long). This file ranks the boards skillmap reads by how many live postings
match the targets in config/my_skills.yaml (role families, countries, regions, remote), so
that watchlist can be filled from the census instead of by hand.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import UTC, date, datetime

from .models import normalise_company

WATCHABLE = {"greenhouse", "lever", "ashby"}  # the boards career.discovery can read
LIMIT = 150
VERSION = 1
SLUG = re.compile(r"^[a-z0-9-]{1,64}$")  # what Omnideck's profile accepts as a board slug


def matches(row: dict, targets: dict) -> bool:
    if row["role_family"] not in set(targets.get("role_families") or []):
        return False
    return bool(
        row.get("country") in set(targets.get("countries") or [])
        or row.get("region") in set(targets.get("regions") or [])
        or (targets.get("remote") and row.get("remote"))
    )


def build(rows: list[dict], boards: list[dict], targets: dict, ratings: dict[str, int], today: date,
          live_days: int = 30, now: datetime | None = None) -> dict:
    by_company = {normalise_company(b["company"]): b for b in boards if b["ats"] in WATCHABLE}
    live = [r for r in rows if r["source"] in WATCHABLE and (today - r["last_seen"]).days <= live_days
            and r["role_family"] != "non_tech"]
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in live:
        key = normalise_company(r["company"])
        if key in by_company:
            groups[key].append(r)
    strong = {skill for skill, level in ratings.items() if level >= 3}
    out = []
    for key, group in groups.items():
        hits = [r for r in group if matches(r, targets)]
        if not hits:
            continue
        fit = sum(len(strong.intersection(r["skills"])) / max(1, len(r["skills"])) for r in hits) / len(hits)
        board = by_company[key]
        out.append({
            "company": board["company"], "ats": board["ats"], "token": board["token"], "sector": board.get("sector"),
            "matching": len(hits), "live_tech": len(group), "skill_fit": round(fit, 2),
            "countries": [c for c, _ in Counter(r.get("country") or "remote" for r in hits).most_common(3)],
            "titles": [t for t, _ in Counter(r["title"] for r in hits).most_common(3)],
        })
    out.sort(key=lambda b: (-b["matching"] * (0.5 + b["skill_fit"]), b["company"]))
    out = out[:LIMIT]
    now = now or datetime.now(UTC)
    return {
        "version": VERSION,
        "generated_at": now.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "generated": today.isoformat(),
        "targets": targets,
        "boards": out,
        # Ready to paste into the Omnideck career profile's watched boards.
        # Only tokens that are valid Omnideck slugs (boards whose token isn't are still listed above).
        "omnideck": {f"watch_{ats}": [b["token"] for b in out if b["ats"] == ats and SLUG.match(b["token"])]
                     for ats in sorted(WATCHABLE)},
    }
