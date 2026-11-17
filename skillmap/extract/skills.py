"""Skill matching against the taxonomy in config/taxonomy.yaml."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import yaml

from .text import split_sections, to_plain

DEFAULT_TAXONOMY = Path(__file__).resolve().parents[2] / "config" / "taxonomy.yaml"
_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n")


@dataclass
class Skill:
    name: str
    category: str
    patterns: list[re.Pattern[str]]
    negatives: list[re.Pattern[str]]

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
            self.skills.append(Skill(name, spec["category"], patterns, negatives))

    @classmethod
    def load(cls, path: Path = DEFAULT_TAXONOMY) -> Taxonomy:
        return cls(yaml.safe_load(path.read_text()))

    @cached_property
    def categories(self) -> dict[str, str]:
        return {skill.name: skill.category for skill in self.skills}

    def known(self, term: str) -> bool:
        """Whether a bare term (from extract.terms) is already one of the skills."""
        cache = self.__dict__.setdefault("_known", {})
        if term not in cache:
            cache[term] = any(skill.matches(term) for skill in self.skills)
        return cache[term]

    def _find(self, lines: list[str]) -> set[str]:
        found: set[str] = set()
        sentences = [s for line in lines for s in _SENTENCE.split(line) if s]
        for skill in self.skills:
            if any(skill.matches(sentence) for sentence in sentences):
                found.add(skill.name)
        return found

    def extract(self, title: str, description: str) -> tuple[list[str], list[str]]:
        """(skills asked for, skills only mentioned as nice-to-have), both sorted.

        The title counts as core text: "Senior Python Engineer" asks for Python even when the
        body never repeats it.
        """
        core_lines, nice_lines = split_sections(to_plain(description))
        core = self._find([title, *core_lines])
        nice = self._find(nice_lines) - core
        return sorted(core | nice), sorted(nice)
