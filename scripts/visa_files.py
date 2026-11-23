"""List the LCA disclosure files the DOL page links, with the header row of the first of each year:
python scripts/visa_files.py. Stores nothing."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from skillmap import visa
from skillmap.http import PoliteClient

client = PoliteClient(min_interval=2.0, timeout=600.0)
page = client.request("GET", visa.PAGE, headers={"Accept": "text/html"}).text
links = sorted(set(re.findall(r'href="([^"]*LCA_Disc[^"]*\.xlsx)"', page, re.I)))
print(len(links), "files")
for link in links:
    print(" ", link)
seen = set()
for link in links:
    year = re.search(r"FY(\d{4})", link)
    if not year or year.group(1) in seen or int(year.group(1)) not in (2012, 2016, 2019, 2020):
        continue
    seen.add(year.group(1))
    url = link if link.startswith("http") else f"https://www.dol.gov{link}"
    response = client.request("GET", url, headers={"Accept": "*/*"})
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as tmp:
        tmp.write(response.content)
        tmp.flush()
        row = next(iter(visa.read_xlsx(Path(tmp.name))), {})
    print(year.group(1), response.status_code, len(response.content), "bytes; columns:", sorted(row)[:80])
