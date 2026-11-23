"""Try the H-1B filings summary without committing it: python scripts/visa_try.py [QUARTERS]
Prints the headline numbers and the top companies. Writes only to a temporary file."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from skillmap import visa
from skillmap.http import PoliteClient
from skillmap.store import today_utc

quarters = int(sys.argv[1]) if len(sys.argv) > 1 else 1
client = PoliteClient(min_interval=2.0, timeout=600.0)
with tempfile.TemporaryDirectory() as tmp:
    data = visa.run(client, Path(tmp) / "visa-pay.json", today_utc(), quarters)
print(data["files"], data["n"], "filings")
for key in ("by_role", "by_size", "by_level"):
    print(key, {k: (v["n"], v["median"]) for k, v in data[key].items()})
for c in data["by_company"][:40]:
    print(f"  {c['company']:<30} {c['size']:<10} n={c['n']:<6} median={c['median']:>9,.0f}",
          {k: v["median"] for k, v in list(c["roles"].items())[:3]})
