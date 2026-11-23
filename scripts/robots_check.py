"""Check whether robots.txt lets a generic crawler fetch each URL, and print the rules that decide it.

    uv run python scripts/robots_check.py URL [URL ...]
"""

from __future__ import annotations

import sys
import urllib.request
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

AGENT = "skillmap/0.1 (+https://github.com/HatefulRock/skillmap; job-market research)"


def main(urls: list[str]) -> None:
    seen: dict[str, RobotFileParser | None] = {}
    for url in urls:
        parts = urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}"
        if root not in seen:
            try:
                req = urllib.request.Request(root + "/robots.txt", headers={"User-Agent": AGENT})
                text = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")  # noqa: S310
            except Exception as exc:  # a missing robots.txt allows everything
                print(f"{root}/robots.txt: {exc}")
                text = ""
            parser = RobotFileParser()
            parser.parse(text.splitlines())
            seen[root] = parser
            path = parts.path.rstrip("/").rsplit("/", 1)[0] or "/"
            star = False
            for line in text.splitlines():
                low = line.strip().lower()
                if low.startswith("user-agent:"):
                    star = low.split(":", 1)[1].strip() == "*"
                elif star and (low.startswith("disallow") or low.startswith("allow")) and \
                        any(seg and seg in low for seg in path.lower().split("/")[1:3]):
                    print(f"  {root} [*] {line.strip()}")
        parser = seen[root]
        print(f"{'ALLOWED' if parser.can_fetch(AGENT, url) else 'BLOCKED'}  {url}")


if __name__ == "__main__":
    main(sys.argv[1:])
