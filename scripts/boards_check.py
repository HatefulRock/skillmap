"""Check boards before adding them to config/companies.yaml:
python scripts/boards_check.py ATS=TOKEN=COMPANY [...]
Runs each board's collector (texts for at most two new ads) and prints how many postings it lists
and the first few titles. Stores nothing."""

from __future__ import annotations

import inspect
import itertools
import sys

from skillmap.http import PoliteClient
from skillmap.runner import BOARDS, NEEDS_KNOWN

client = PoliteClient(min_interval=1.0)
for arg in sys.argv[1:]:
    ats, token, company = arg.split("=", 2)
    module = BOARDS[ats]
    extra = {"known": set()} if ats in NEEDS_KNOWN else {}
    if "max_new_texts" in inspect.signature(module.collect).parameters:
        extra["max_new_texts"] = 2
    try:
        postings = list(itertools.islice(module.collect(client, token, company.replace("_", " "), **extra), 3000))
    except Exception as exc:  # noqa: BLE001 - report and go on to the next board
        print(f"{ats}:{token} FAILED {type(exc).__name__}: {str(exc)[:120]}", flush=True)
        continue
    print(f"{ats}:{token} {len(postings)} postings; e.g. {[p.title for p in postings[:4]]}", flush=True)
