"""Precision check for a category's patterns (soft skills unless argv[2] names others, comma-separated): read live ads from a sample of Greenhouse boards
and MyCareersFuture, and print each soft skill's hit rate with random matched sentences, so a
person can judge whether the matches are real asks or boilerplate. Prints only short sentences."""

from __future__ import annotations

import random
import sys

from skillmap.extract import roles
from skillmap.extract.skills import _SENTENCE, Taxonomy
from skillmap.extract.text import split_sections, to_plain
from skillmap.http import PoliteClient
from skillmap.runner import load_boards
from skillmap.sources import greenhouse, mcf

random.seed(7)
tax = Taxonomy.load()
# The probe workflow splits arguments on spaces, so names are rejoined.
CATEGORIES = {c.strip() for c in (" ".join(sys.argv[2:]) or "Soft skills").split(",")}
soft = [s for s in tax.skills if s.category in CATEGORIES or s.name in CATEGORIES]
boards = [b for b in load_boards() if b["ats"] == "greenhouse"]
random.shuffle(boards)
client = PoliteClient()
ads: list[tuple[str, str, str]] = []  # (source, title, text)
for board in boards[: int(sys.argv[1]) if len(sys.argv) > 1 else 30]:
    try:
        for raw in greenhouse.parse(client.get_json(greenhouse.board_url(board["token"])), board["company"]):
            if roles.classify(raw.title):
                ads.append(("gh", raw.title, raw.description))
    except Exception as error:
        print("skip", board["token"], error)
try:
    for page in range(3):
        for job in mcf.category_page(client, "Information Technology", page).get("results") or []:
            raw = mcf.parse_job(job)
            if roles.classify(raw.title):
                ads.append(("mcf", raw.title, raw.description))
except Exception as error:
    print("mcf failed", error)

print(f"{len(ads)} tech ads")
hits: dict[str, list[str]] = {s.name: [] for s in soft}
for source, title, text in ads:
    core, nice = split_sections(to_plain(text))
    sentences = [x for line in core + nice for x in _SENTENCE.split(line) if x]
    for skill in soft:
        found = [x for x in sentences if skill.matches(x)]
        if found:
            hits[skill.name].append(f"[{source}] {title[:40]} :: {random.choice(found)[:220]}")
for name, lines in hits.items():
    print(f"\n=== {name}: {len(lines)}/{len(ads)} = {len(lines) / max(1, len(ads)):.0%}")
    for line in random.sample(lines, min(12, len(lines))):
        print("  ", line)
