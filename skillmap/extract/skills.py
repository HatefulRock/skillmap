"""Skill matching against the taxonomy in config/taxonomy.yaml."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import yaml

from .text import split_sections, to_plain

DEFAULT_TAXONOMY = Path(__file__).resolve().parents[2] / "config" / "taxonomy.yaml"
# The matching code itself: if it changes, every skill's result may change.
CODE_KEY = "__code__"
_CODE_FILES = [Path(__file__), Path(__file__).with_name("text.py")]
_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n")


@dataclass
class Skill:
    name: str
    category: str
    patterns: list[re.Pattern[str]]
    negatives: list[re.Pattern[str]]
    fingerprint: str = ""
    group: str = ""

    def matches(self, sentence: str) -> bool:
        if not any(pattern.search(sentence) for pattern in self.patterns):
            return False
        return not any(negative.search(sentence) for negative in self.negatives)


class Taxonomy:
    def __init__(self, raw: dict):
        self.version: int = raw.get("version", 1)
        self.skills: list[Skill] = []
        for name, spec in raw["skills"].items():
            patterns = [re.compile(p, re.I) for p in spec.get("patterns", [])]
            patterns += [re.compile(p) for p in spec.get("cs", [])]
            negatives = [re.compile(p, re.I) for p in spec.get("not", [])]
            key = json.dumps([spec["category"], spec.get("patterns", []), spec.get("cs", []), spec.get("not", [])])
            fingerprint = hashlib.sha1(key.encode()).hexdigest()[:16]
            self.skills.append(Skill(name, spec["category"], patterns, negatives, fingerprint,
                                     spec.get("group") or spec["category"]))

    @classmethod
    def load(cls, path: Path = DEFAULT_TAXONOMY) -> Taxonomy:
        return cls(yaml.safe_load(path.read_text()))

    @cached_property
    def categories(self) -> dict[str, str]:
        return {skill.name: skill.category for skill in self.skills}

    @cached_property
    def groups(self) -> dict[str, str]:
        """The broader family each skill rolls up into (React and Vue: "Web frameworks")."""
        return {skill.name: skill.group for skill in self.skills}

    @cached_property
    def fingerprints(self) -> dict[str, str]:
        """One hash per skill (its category and patterns), plus one for the matching code."""
        code = hashlib.sha1(b"".join(path.read_bytes() for path in _CODE_FILES)).hexdigest()[:16]
        return {CODE_KEY: code, **{skill.name: skill.fingerprint for skill in self.skills}}

    def changed_since(self, old: dict[str, str] | None) -> set[str] | None:
        """Skills whose matches may differ from an extraction made with `old` fingerprints, or
        None when every skill must be matched again (unknown version or changed matching code).
        A removed skill needs no matching: it simply drops out of the merged result."""
        if not old or old.get(CODE_KEY) != self.fingerprints[CODE_KEY]:
            return None
        return {name for name, fp in self.fingerprints.items() if name != CODE_KEY and old.get(name) != fp}

    def extract_since(self, title: str, description: str, prior: tuple[list[str], list[str]],
                      changed: set[str]) -> tuple[list[str], list[str]]:
        """Same result as extract(), matching only the `changed` skills and keeping the prior
        result for the rest. Skills match independently, so this is exact."""
        current = {skill.name for skill in self.skills}
        keep = current - changed
        prior_all, prior_nice = set(prior[0]) & keep, set(prior[1]) & keep
        prior_core = prior_all - prior_nice
        if changed:
            new_all, new_nice = self.extract(title, description, only=changed)
        else:
            new_all, new_nice = [], []
        core = prior_core | (set(new_all) - set(new_nice))
        nice = (prior_nice | set(new_nice)) - core
        return sorted(core | nice), sorted(nice)

    def known(self, term: str) -> bool:
        """Whether a bare term (from extract.terms) is already one of the skills."""
        cache = self.__dict__.setdefault("_known", {})
        if term not in cache:
            cache[term] = any(skill.matches(term) for skill in self.skills)
        return cache[term]

    def _find(self, lines: list[str], only: set[str] | None = None) -> set[str]:
        found: set[str] = set()
        sentences = [s for line in lines for s in _SENTENCE.split(line) if s]
        # One search per pattern over the whole text first: an ad names ~10 of the ~110 skills,
        # and only those need the per-sentence check (which the `not` rules are scoped to).
        # Joined by newlines, a sentence's match is also a match here, so nothing is missed.
        blob = "\n".join(sentences)
        for skill in self.skills:
            if only is not None and skill.name not in only:
                continue
            if not any(pattern.search(blob) for pattern in skill.patterns):
                continue
            if any(skill.matches(sentence) for sentence in sentences):
                found.add(skill.name)
        return found

    def extract(self, title: str, description: str, only: set[str] | None = None) -> tuple[list[str], list[str]]:
        """(skills asked for, skills only mentioned as nice-to-have), both sorted.

        The title counts as core text: "Senior Python Engineer" asks for Python even when the
        body never repeats it.
        """
        core_lines, nice_lines = split_sections(to_plain(description))
        core = self._find([title, *core_lines], only)
        nice = self._find(nice_lines, only) - core
        return sorted(core | nice), sorted(nice)
