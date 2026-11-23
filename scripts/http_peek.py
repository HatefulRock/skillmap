"""Print status, redirect target, cookies set and the start of the body for each URL, without
following redirects. For checking how a careers site's search API answers before writing a collector.

    uv run python scripts/http_peek.py URL [URL ...]          GET
    uv run python scripts/http_peek.py --post '{...}' URL     POST JSON
    uv run python scripts/http_peek.py --find REGEX URL       show text around the first matches
"""

from __future__ import annotations

import re
import sys

import httpx

AGENT = "skillmap/0.1 (+https://github.com/HatefulRock/skillmap; job-market research)"


def main(argv: list[str]) -> None:
    body = find = None
    if argv[:1] == ["--post"]:
        body, argv = argv[1], argv[2:]
    if argv[:1] == ["--find"]:
        find, argv = re.compile(argv[1]), argv[2:]
    with httpx.Client(headers={"User-Agent": AGENT, "Accept": "application/json, text/html"}, timeout=30) as client:
        for url in argv:
            r = client.post(url, content=body, headers={"Content-Type": "application/json"}) if body else client.get(url)
            print(f"=== {url}\n{r.status_code} {r.headers.get('content-type', '')}")
            for key in ("location", "set-cookie", "x-apple-csrf-token", "x-csrf-token"):
                if key in r.headers:
                    print(f"  {key}: {r.headers[key][:200]}")
            if not find:
                print(r.text[:1500])
                continue
            print(len(r.text), "bytes")
            for match in list(find.finditer(r.text))[:6]:
                print("...", r.text[max(0, match.start() - 150):match.end() + 450].replace("\n", " "))


if __name__ == "__main__":
    main(sys.argv[1:])
