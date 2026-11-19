"""Precision check for every tech skill: read live ads from a sample of Greenhouse boards and
MyCareersFuture and print each skill's hit rate with two random matched sentences (short), so a
person can spot patterns that match the wrong thing. argv[1]: boards to read (default 40);
argv[2:]: only skills whose group or category contains this text."""

from __future__ import annotations

import random
import sys

from skillmap.extract import roles
from skillmap.extract.skills import _SENTENCE, Taxonomy
from skillmap.extract.text import split_sections, to_plain
from skillmap.http import PoliteClient
from skillmap.runner import load_boards
from skillmap.sources import greenhouse, mcf

random.seed(11)
tax = Taxonomy.load()
needle = " ".join(sys.argv[2:]).lower()
skills = [s for s in tax.skills if s.category != "Soft skills" and (not needle or needle in f"{s.group} {s.category}".lower())]
boards = [b for b in load_boards() if b["ats"] == "greenhouse"]
random.shuffle(boards)
client = PoliteClient()
ads: list[tuple[str, str]] = []
for board in boards[: int(sys.argv[1]) if len(sys.argv) > 1 else 40]:
    try:
        for raw in greenhouse.parse(client.get_json(greenhouse.board_url(board["token"])), board["company"]):
            if roles.classify(raw.title):
                ads.append((raw.title, raw.description))
    except Exception as error:
        print("skip", board["token"], error)
try:
    for page in range(3):
        for job in mcf.category_page(client, "Information Technology", page).get("results") or []:
            raw = mcf.parse_job(job)
            if roles.classify(raw.title):
                ads.append((raw.title, raw.description))
except Exception as error:
    print("mcf failed", error)

print(f"{len(ads)} tech ads, {len(skills)} skills")
hits: dict[str, list[str]] = {s.name: [] for s in skills}
for title, text in ads:
    core, nice = split_sections(to_plain(text))
    sentences = [x for line in [title, *core, *nice] for x in _SENTENCE.split(line) if x]
    for skill in skills:
        found = [x for x in sentences if skill.matches(x)]
        if found:
            hits[skill.name].append(f"{title[:30]} :: {random.choice(found)[:150]}")
for name, lines in sorted(hits.items(), key=lambda kv: -len(kv[1])):
    if not lines:
        continue
    print(f"\n=== {name}: {len(lines)} ({len(lines) / max(1, len(ads)):.1%})")
    for line in random.sample(lines, min(2, len(lines))):
        print("   ", line)
print("\nno hits:", ", ".join(name for name, lines in hits.items() if not lines))
