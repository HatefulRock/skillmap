"""The weekly "what changed" note.

Each run compares the new aggregates with the previous run's skills-market.json (still in
data/ when the run starts) and writes a few plain sentences: how many postings are live, the
skills that moved most since last week, terms that appeared, companies hiring more, pay. Notes
accumulate in data/digest.json (newest first); the latest is also data/weekly.md, a section of
the README between markers, and an RSS feed (data/feed.xml).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

KEEP = 104  # two years of weekly notes
PAGE_URL = "https://www.pierreblachere.com/skills"
README_START, README_END = "<!-- weekly:start -->", "<!-- weekly:end -->"


def _pp(delta: float) -> str:
    return f"{'+' if delta >= 0 else '−'}{abs(delta) * 100:.1f} pp"


def _pct(share: float) -> str:
    return f"{share * 100:.0f}%"


def build_note(current: dict, previous: dict | None) -> dict:
    lines: list[str] = []
    now = current["current"]
    line = f"{now['n']:,} live tech postings in the last {current['meta']['windows']['current_days']} days"
    if previous and previous.get("current", {}).get("n"):
        line += f" ({now['n'] - previous['current']['n']:+,} since {previous['meta']['as_of']})"
    lines.append(line + ".")

    if previous and previous.get("current", {}).get("skills"):
        before = {s["name"]: s["share"] for s in previous["current"]["skills"]}
        moves = sorted(
            ((s["name"], s["share"], s["share"] - before[s["name"]]) for s in now["skills"] if s["name"] in before),
            key=lambda m: -abs(m[2]),
        )
        moves = [m for m in moves if abs(m[2]) >= 0.005][:3]
        if moves:
            lines.append("Biggest moves since last week: " + ", ".join(
                f"{name} {_pct(share)} ({_pp(delta)})" for name, share, delta in moves) + ".")

    movers = (current.get("changes") or {}).get("movers")
    if movers and movers["risers"]:
        lines.append(f"Rising over {movers['days']} days: " + ", ".join(
            f"{m['name']} ({_pp(m['delta'])})" for m in movers["risers"][:3]) + ".")

    terms = (current.get("changes") or {}).get("terms") or {}
    old_terms = {t["term"] for t in ((previous or {}).get("changes") or {}).get("terms", {}).get("common", [])}
    new_terms = [t for t in terms.get("common", []) if t["term"] not in old_terms][:4] if previous else []
    if new_terms:
        lines.append("New on the radar: " + ", ".join(f"{t['term']} ({t['companies']} companies)" for t in new_terms) + ".")

    growing = ((current.get("changes") or {}).get("companies") or {}).get("growing") or []
    if growing:
        lines.append("Hiring more: " + ", ".join(f"{c['name']} ({c['delta']:+})" for c in growing[:3]) + ".")

    pay = {c["key"]: c for c in (current.get("pay") or {}).get("countries", [])}
    old_pay = {c["key"]: c for c in ((previous or {}).get("pay") or {}).get("countries", [])}
    for country in ("SG", "US"):
        if country in pay:
            entry = pay[country]
            text = f"Median advertised pay in {country}: {entry['currency']} {entry['local']['median']:,.0f} a year"
            if country in old_pay and old_pay[country]["currency"] == entry["currency"]:
                change = entry["local"]["median"] / old_pay[country]["local"]["median"] - 1
                if abs(change) >= 0.01:
                    text += f" ({change * 100:+.0f}%)"
            lines.append(text + f", from {entry['n']:,} ads.")
    return {"date": current["meta"]["as_of"], "lines": lines}


def load_notes(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return []


def add_note(notes: list[dict], note: dict) -> list[dict]:
    """Newest first; a second run on the same day replaces that day's note."""
    return [note, *[n for n in notes if n["date"] != note["date"]]][:KEEP]


def markdown(note: dict) -> str:
    return f"**Week of {note['date']}**\n\n" + "\n".join(f"- {line}" for line in note["lines"]) + "\n"


def rss(notes: list[dict]) -> str:
    items = []
    for note in notes[:20]:
        when = format_datetime(datetime.fromisoformat(note["date"]).replace(tzinfo=UTC))
        body = "<ul>" + "".join(f"<li>{escape(line)}</li>" for line in note["lines"]) + "</ul>"
        items.append(
            f"<item><title>Skills market, week of {note['date']}</title><link>{PAGE_URL}</link>"
            f"<guid isPermaLink=\"false\">skillmap-{note['date']}</guid><pubDate>{when}</pubDate>"
            f"<description>{escape(body)}</description></item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
        f"<title>Skills in demand: weekly changes</title><link>{PAGE_URL}</link>"
        "<description>What changed in AI, ML, forward-deployed and Python job postings this week.</description>"
        + "".join(items) + "</channel></rss>\n"
    )


def update_readme(path: Path, note: dict) -> bool:
    """Replace the section between the weekly markers; False if the README has no markers."""
    if not path.exists():
        return False
    text = path.read_text()
    pattern = re.compile(re.escape(README_START) + r".*?" + re.escape(README_END), re.S)
    if not pattern.search(text):
        return False
    path.write_text(pattern.sub(lambda _: f"{README_START}\n{markdown(note)}{README_END}", text))
    return True
