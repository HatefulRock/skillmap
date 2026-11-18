"""The published aggregates as flat CSV tables (data/csv/), for spreadsheets and notebooks.

Long format throughout (one value per row) so every table loads the same way. They are
derived from skills-market.json in the same run and hold nothing that file doesn't.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path


def _write(path: Path, header: list[str], rows: Iterable[Iterable]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def _series(series: dict, label: str) -> Iterable[list]:
    for skill, values in series.get("shares", {}).items():
        for month, n, share in zip(series["months"], series["n"], values, strict=True):
            if share is not None:
                yield [label, month, n, skill, share]


def write_csvs(data: dict, out: Path) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    written = []

    def table(name: str, header: list[str], rows: Iterable[Iterable]) -> None:
        _write(out / name, header, rows)
        written.append(name)

    table("skills_current.csv", ["skill", "category", "share", "postings", "nice_to_have_share", "delta_vs_previous"],
          ([s["name"], s["category"], s["share"], s["count"], s["nice_share"], s["delta"]] for s in data["current"]["skills"]))
    trends = data["trends"]
    table("skill_trends_monthly.csv", ["series", "month", "postings", "skill", "share"],
          [*_series(trends["market"], "market"), *_series(trends["hn"], "hn")])
    table("skill_trends_by_group_quarterly.csv", ["group_type", "group", "series", "quarter_start", "postings", "skill", "share"],
          ([kind, group, *row] for kind in ("by_role", "by_seniority") for group, block in (trends.get(kind) or {}).items()
           for row in [*_series(block["market"], "market"), *_series(block["hn"], "hn")]))
    table("segments.csv", ["dimension", "key", "label", "postings", "skill", "share"],
          ([dim, seg["key"], seg["label"], seg["n"], skill, share]
           for dim in ("seniority", "role_family", "region", "country", "sector")
           for seg in data["segments"][dim]["segments"] if seg["shares"]
           for skill, share in seg["shares"].items()))

    pay = data.get("pay") or {}
    table("pay_by_country.csv", ["country", "ads_with_pay", "currency", "median", "p25", "p75", "median_usd", "p25_usd", "p75_usd"],
          ([c["key"], c["n"], c["currency"], c["local"]["median"], c["local"]["p25"], c["local"]["p75"],
            c["usd"]["median"], c["usd"]["p25"], c["usd"]["p75"]] for c in pay.get("countries", [])))
    table("pay_by_role_seniority_usd.csv", ["country", "role_family", "seniority", "ads", "median", "p25", "p75"],
          ([country, family, level, cell["n"], cell["median"], cell["p25"], cell["p75"]]
           for country, families in (pay.get("role_seniority_usd") or {}).items()
           for family, levels in families.items() for level, cell in levels.items()))

    insights = data.get("insights") or {}
    table("pay_premium.csv", ["skill", "premium", "matched_postings"],
          ([e["name"], e["premium"], e["n"]] for e in (insights.get("pay_premium") or {}).get("skills", [])))
    table("role_dna.csv", ["role_family", "skill", "share", "share_elsewhere", "lift"],
          ([family, s["name"], s["share"], s["elsewhere"], s["lift"]]
           for family, block in (insights.get("role_dna") or {}).items() for s in block["skills"]))
    ladder = insights.get("seniority_ladder") or {}
    table("seniority_ladder.csv", ["skill", "seniority", "share"],
          ([s["name"], level, share] for s in ladder.get("skills", []) for level, share in zip(ladder["levels"], s["shares"], strict=True)))
    table("skill_pairs.csv", ["skill_a", "skill_b", "postings_together", "lift"],
          ([e["a"], e["b"], e["n"], e["lift"]] for e in (insights.get("stacks") or {}).get("edges", [])))

    changes = data.get("changes") or {}
    movers = changes.get("movers") or {}
    table("skill_movers.csv", ["direction", "skill", "share_now", "share_before", "delta"],
          ([side, m["name"], m["now"], m["before"], m["delta"]] for side in ("risers", "fallers") for m in movers.get(side, [])))
    per_post = (changes.get("per_post") or {}).get("market") or {}
    metrics = [k for k in per_post if k not in ("months", "n")]
    table("ad_shape_monthly.csv", ["month", "postings", *metrics],
          ([m, n, *[per_post[k][i] for k in metrics]] for i, (m, n) in enumerate(zip(per_post.get("months", []), per_post.get("n", []), strict=True))))
    companies = changes.get("companies") or {}
    table("companies.csv", ["company", "live_postings", "previous_window", "delta", "sector", "top_role", "top_skills"],
          ([c["name"], c["n"], c["n_before"], c["delta"], c["sector"], c["families"][0]["key"] if c["families"] else "",
            "; ".join(s["name"] for s in c["skills"])] for c in companies.get("largest", [])))
    velocity = data.get("velocity") or {}
    weekly = velocity.get("new_per_week") or {}
    families = list(weekly.get("by_role") or {})
    table("new_postings_weekly.csv", ["week", "postings", *families],
          ([w, n, *[weekly["by_role"][f][i] for f in families]]
           for i, (w, n) in enumerate(zip(weekly.get("weeks", []), weekly.get("n", []), strict=True))))
    closed = (velocity.get("time_open") or {}).get("closed") or {}
    table("days_open.csv", ["group", "key", "closed_ads", "median_days", "p25_days", "p75_days"],
          ([group, e.get("key") or e.get("name"), e["n"], e["median"], e["p25"], e["p75"]]
           for group, entries in (("role_family", closed.get("by_role", [])), ("skill", closed.get("by_skill", [])))
           for e in entries))
    return written
