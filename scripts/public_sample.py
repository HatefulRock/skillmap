"""Dry run of the public job boards: how many tech ads each would add for the last N days, and
a few of them, without storing anything."""

from __future__ import annotations

import sys
from collections import Counter
from datetime import date, timedelta

from skillmap.extract import roles
from skillmap.extract.skills import Taxonomy
from skillmap.http import PoliteClient
from skillmap.normalise import normalise
from skillmap.runner import load_yaml
from skillmap.sources import boards_public

days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
cfg = load_yaml("sources.yaml")["public"]
cfg["himalayas"]["max_pages"] = int(sys.argv[2]) if len(sys.argv) > 2 else 20
taxonomy, today = Taxonomy.load(), date.today()
client = PoliteClient()
for name in cfg["boards"]:
    try:
        rows = [normalise(raw, taxonomy, today) for raw in boards_public.collect(client, cfg, name, today - timedelta(days=days))]
    except Exception as error:
        print(f"=== {name}: FAILED {type(error).__name__}: {error}")
        continue
    families = Counter(r.role_family for r in rows)
    countries = Counter(r.country or ("remote" if r.remote else "?") for r in rows)
    paid = sum(1 for r in rows if r.salary_min)
    skills = sum(len(r.skills) for r in rows) / max(1, len(rows))
    print(f"=== {name}: {len(rows)} tech ads in {days} days, {paid} with pay, {skills:.1f} skills each")
    print("   families:", dict(families.most_common(9)))
    print("   countries:", dict(countries.most_common(8)))
    for r in rows[:4]:
        print("   -", r.title[:60], "|", r.company[:20], "|", r.country, "|", r.skills[:5])
    assert all(roles.classify(r.title) for r in rows)
