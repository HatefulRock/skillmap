"""Show what the Apple reader sees on a search page: python scripts/apple_debug.py"""

from __future__ import annotations

from skillmap.http import PoliteClient
from skillmap.sources import apple

client = PoliteClient(min_interval=1.5)
page = client.request("GET", f"{apple.BASE}/en-us/search", params={"sort": "newest", "page": 1},
                      headers={"Accept": "text/html"})
print("status", page.status_code, len(page.text), "bytes; hydration marker:", "__staticRouterHydrationData" in page.text)
match = apple.HYDRATION.search(page.text)
print("regex match:", bool(match), "length", len(match.group(1)) if match else None)
data = apple.hydration(page.text)
print("parsed:", type(data).__name__, list(data)[:5] if isinstance(data, dict) else None)
found = apple.entries(data)
print("entries:", len(found))
for entry in found[:3]:
    print("  ", sorted(entry)[:40])
    print("  ", entry.get("id"), entry.get("postingTitle"), entry.get("transformedPostingTitle"))
if data and not found:
    def walk(node, path="", depth=0):
        if depth > 5:
            return
        if isinstance(node, dict):
            for k, v in list(node.items())[:12]:
                print("  " * depth + f"{k}: {type(v).__name__}")
                walk(v, path + "/" + k, depth + 1)
        elif isinstance(node, list) and node:
            print("  " * depth + f"[{len(node)}] first:", type(node[0]).__name__)
            walk(node[0], path, depth + 1)
    walk(data)
