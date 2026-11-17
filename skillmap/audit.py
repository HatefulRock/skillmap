"""Monthly accuracy check: how well the regex skill extraction agrees with Claude.

A sample of recent tech postings is read by Claude, which picks from the same taxonomy every
skill the posting asks for (required or nice to have). The regex result on the same text is
then scored against Claude's answer: precision (what the regex finds that Claude agrees with),
recall (what Claude finds that the regex also finds), per skill and overall. The skills with
the most disagreements are where config/taxonomy.yaml needs work.

Only skill names, titles and companies are written to data/audit.json, never posting text.
"""

from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

from .archive import Archive, unpack
from .extract.skills import Taxonomy
from .extract.text import to_plain
from .store import Store

MODEL = "claude-opus-5-5"
SAMPLE = 100
LIVE_DAYS = 30
KEEP = 24  # two years of monthly checks
EXAMPLES = 3

SCHEMA = {
    "type": "object",
    "properties": {"skills": {"type": "array", "items": {"type": "string"}}},
    "required": ["skills"],
    "additionalProperties": False,
}


def system_prompt(taxonomy: Taxonomy) -> str:
    by_category: dict[str, list[str]] = defaultdict(list)
    for skill in taxonomy.skills:
        by_category[skill.category].append(skill.name)
    listing = "\n".join(f"{cat}: " + "; ".join(names) for cat, names in by_category.items())
    return (
        "You label job postings for a study of which skills employers ask for. You get one posting. "
        "Return every skill from the list below that the posting asks the candidate to have or to "
        "work with, whether it is required or only nice to have. A name like \"Rust / Scala\" or "
        "\"Java / Kotlin (Spring)\" is one entry: return it if the posting asks for any of the things "
        "it names. Leave out skills that appear only in the company description, in a list of "
        "products the company sells, or in a sentence saying the skill is not needed. Return names "
        "exactly as written in the list, and nothing that is not in it. An empty list is a valid answer.\n\n"
        f"Skills, by category:\n{listing}"
    )


def label(client: Any, system: str, title: str, text: str) -> list[str] | None:
    """Claude's skills for one posting, or None when it declined to answer."""
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # on a policy decline, retry on the model the API picks
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": f"<posting>\nTitle: {title}\n\n{text}\n</posting>"}],
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        return None
    body = next((block.text for block in response.content if block.type == "text"), None)
    return json.loads(body)["skills"] if body else None


def sample(store: Store, rows: list[dict], today: date, n: int, seed: str) -> list[dict]:
    """Up to n live tech postings that still have text, in a fixed order for the month."""
    live = [r for r in rows if r["role_family"] != "non_tech" and r["source"] != "hn"
            and (today - r["last_seen"]).days <= LIVE_DAYS]
    live.sort(key=lambda r: r["posting_id"])
    random.Random(seed).shuffle(live)
    picked: list[dict] = []
    for start in range(0, len(live), n * 2):
        chunk = live[start:start + n * 2]
        where = store.text_locations([r["posting_id"] for r in chunk])
        picked += [{**r, "_text": where[r["posting_id"]]} for r in chunk if r["posting_id"] in where]
        if len(picked) >= n:
            break
    return picked[:n]


def load_texts(picked: list[dict], archive: Archive | None) -> dict[str, str]:
    """Each picked posting's text, opening every archive file it needs once."""
    texts: dict[str, str] = {}
    files: dict[str, list[str]] = defaultdict(list)
    for r in picked:
        stored, archived = r["_text"]
        if stored:
            texts[r["posting_id"]] = stored
        elif archived:
            files[archived].append(r["posting_id"])
    if archive is not None:
        for name, ids in files.items():
            batch = unpack(archive.get(name))
            texts.update({pid: batch[pid] for pid in ids if pid in batch})
    return texts


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 3) if b else None


def score(results: Iterable[tuple[dict, set[str], set[str]]]) -> dict:
    """results: (row, regex skills, Claude's skills). Micro-averaged overall, plus per skill."""
    counts: dict[str, Counter] = defaultdict(Counter)
    examples: dict[str, dict[str, list[dict]]] = defaultdict(lambda: {"missed": [], "extra": []})
    n = 0
    for r, regex, truth in results:
        n += 1
        for skill in regex | truth:
            kind = "tp" if skill in regex and skill in truth else "fp" if skill in regex else "fn"
            counts[skill][kind] += 1
            bucket = {"fp": "extra", "fn": "missed"}.get(kind)
            if bucket and len(examples[skill][bucket]) < EXAMPLES:
                examples[skill][bucket].append({"posting_id": r["posting_id"], "company": r["company"], "title": r["title"]})
    total = sum((c for c in counts.values()), Counter())
    precision, recall = _ratio(total["tp"], total["tp"] + total["fp"]), _ratio(total["tp"], total["tp"] + total["fn"])
    skills = [
        {"name": name, "tp": c["tp"], "fp": c["fp"], "fn": c["fn"],
         "precision": _ratio(c["tp"], c["tp"] + c["fp"]), "recall": _ratio(c["tp"], c["tp"] + c["fn"]),
         **examples[name]}
        for name, c in counts.items()
    ]
    skills.sort(key=lambda s: (-(s["fp"] + s["fn"]), s["name"]))
    f1 = round(2 * precision * recall / (precision + recall), 3) if precision and recall else None
    return {"n": n, "precision": precision, "recall": recall, "f1": f1,
            "tp": total["tp"], "fp": total["fp"], "fn": total["fn"], "skills": skills}


def run(store: Store, archive: Archive | None, taxonomy: Taxonomy, client: Any, today: date, n: int = SAMPLE) -> dict:
    picked = sample(store, store.rows(), today, n, seed=today.isoformat()[:7])
    texts = load_texts(picked, archive)
    system = system_prompt(taxonomy)
    names = {skill.name for skill in taxonomy.skills}
    results, declined = [], 0
    for r in picked:
        text = texts.get(r["posting_id"])
        if not text:
            continue
        answer = label(client, system, r["title"], to_plain(text))
        if answer is None:
            declined += 1
            continue
        required, nice = taxonomy.extract(r["title"], text)
        results.append((r, set(required) | set(nice), {s for s in answer if s in names}))
    report = score(results)
    report.update(date=today.isoformat(), model=MODEL, taxonomy_version=taxonomy.version, declined=declined,
                  window_days=LIVE_DAYS)
    return report


def write(report: dict, path: Path) -> dict:
    """data/audit.json: the latest full report plus a short history of the headline numbers."""
    try:
        history = json.loads(path.read_text()).get("history", [])
    except (FileNotFoundError, ValueError):
        history = []
    keys = ("date", "n", "precision", "recall", "f1", "model", "taxonomy_version")
    history = [{k: report[k] for k in keys}, *[h for h in history if h["date"] != report["date"]]][:KEEP]
    data = {"latest": report, "history": history}
    path.write_text(json.dumps(data, indent=1) + "\n")
    return data


def headline(path: Path) -> dict | None:
    """The part of data/audit.json the website shows: history and the latest worst skills."""
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None
    latest = data["latest"]
    worst = [{k: s[k] for k in ("name", "tp", "fp", "fn", "precision", "recall")}
             for s in latest["skills"] if s["fp"] + s["fn"]][:8]
    return {"history": data["history"], "worst": worst}


def summary(report: dict) -> str:
    def pct(v: float | None) -> str:
        return "n/a" if v is None else f"{v * 100:.0f}%"

    lines = [f"Checked {report['n']} postings with {report['model']} (taxonomy v{report['taxonomy_version']}): "
             f"precision {pct(report['precision'])}, recall {pct(report['recall'])}, F1 {pct(report['f1'])}."]
    if report["declined"]:
        lines.append(f"{report['declined']} postings were declined and skipped.")
    worst = [s for s in report["skills"] if s["fp"] + s["fn"]][:10]
    if worst:
        lines.append("Most disagreements (missed by regex / extra from regex):")
        lines += [f"  {s['name']}: {s['fn']} / {s['fp']}" for s in worst]
    return "\n".join(lines)
