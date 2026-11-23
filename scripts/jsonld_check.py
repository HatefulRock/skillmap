"""Check a careers site for the JSON-LD reader before adding it to config/companies.yaml:
python scripts/jsonld_check.py LISTING_OR_SITEMAP_URL [LINKS_REGEX] [N]
Prints whether robots.txt allows us, how many job links the page or sitemap lists (and a few of
them), and the title, place and pay read from the first N job pages. Stores nothing."""

from __future__ import annotations

import sys

from skillmap.http import USER_AGENT, PoliteClient
from skillmap.sources import jsonld

url = sys.argv[1]
pattern = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "-" else jsonld.DEFAULT_LINKS
n = int(sys.argv[3]) if len(sys.argv) > 3 else 3
client = PoliteClient(min_interval=1.0)
robots = jsonld._robots(client, url)
print("robots allows listing:", robots.can_fetch(USER_AGENT, url))
page = client.request("GET", url, headers={"Accept": "text/html,application/xml"})
print("listing:", page.status_code, page.headers.get("content-type"), len(page.text), "bytes")
links = jsonld.job_links(page.text, url, pattern)
print("job links:", len(links))
for link in links[:5]:
    print("  ", link, "allowed" if robots.can_fetch(USER_AGENT, link) else "DISALLOWED")
if not links:
    print("first <loc>s / hrefs:", (jsonld.LOC.findall(page.text) or jsonld.HREF.findall(page.text))[:15])
for posting in jsonld.collect(client, url, "check", links=pattern, max_new_texts=n):
    print("-", posting.title, "|", posting.location, "|", posting.salary_min, posting.salary_currency,
          "|", len(posting.description), "chars")
