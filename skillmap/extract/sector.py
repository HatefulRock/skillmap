"""Sector: from the curated company list when the company is on it, else from the posting text."""

from __future__ import annotations

import re

SECTORS = ["ai", "crypto", "fintech", "tech", "other"]
LABELS = {"ai": "AI labs & AI-first", "crypto": "Crypto & Web3", "fintech": "Fintech & finance",
          "tech": "Other tech", "other": "Everything else"}

_RULES = [
    ("crypto", re.compile(r"\b(?:crypto(?:currency)?|blockchain|web3|defi|on-?chain|stablecoins?|digital assets?|"
                          r"exchange for digital|tokeni[sz]ation)\b", re.I)),
    ("fintech", re.compile(r"\b(?:fintech|payments? (?:company|platform|provider)|bank(?:ing)?|neobank|lending|"
                           r"brokerage|insurance|wealth management|asset management|hedge fund|trading firm|"
                           r"financial services)\b", re.I)),
    ("ai", re.compile(r"\b(?:ai (?:company|lab|startup|research lab|native|first)|ai-(?:native|first)|"
                      r"frontier (?:ai|models?)|foundation models? company|we build (?:ai|llm))\b", re.I)),
]


def classify(description: str, hint: str | None = None) -> str:
    if hint in SECTORS:
        return hint
    head = (description or "")[:1500]  # the "About us" paragraph, not the whole ad
    for sector, rule in _RULES:
        if rule.search(head):
            return sector
    return "tech" if hint == "tech" else "other"
