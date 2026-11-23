"""US H-1B filings (Department of Labor LCA disclosure data) as a separate pay view.

Every H-1B petition starts with a Labor Condition Application that states the employer, job title,
worksite and the wage offered. The Office of Foreign Labor Certification publishes them quarterly as
Excel files. These are filings, not ads: they name no skills and cover only roles filled with visa
workers, so they are summarised on their own (data/visa-pay.json) and never mixed into ad statistics.

The files hold no personal data (no worker names); employer names are companies.
"""

from __future__ import annotations

import json
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from statistics import median, quantiles

from . import company_size
from .extract import roles, seniority
from .http import PoliteClient

PAGE = "https://www.dol.gov/agencies/eta/foreign-labor/performance"
FILE = re.compile(r'href="([^"]*LCA_Disclosure_Data_FY(\d{4})_Q(\d)\.xlsx)"', re.I)
QUARTERS = 4  # the latest year of filings
PER_YEAR = {"year": 1, "month": 12, "bi-weekly": 26, "week": 52, "hour": 2080}
MIN_USD, MAX_USD = 30_000, 1_500_000
MIN_N = 20  # fewest filings behind a published median
TOP_COMPANIES = 60
MIN_ROWS = 5_000  # a year of filings has far more; fewer means the files were misread
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def latest_files(page: str, quarters: int = QUARTERS) -> list[tuple[str, str]]:
    """(label, url) of the newest quarterly files listed on the OFLC page, newest first."""
    found = {(int(fy), int(q)): url for url, fy, q in FILE.findall(page)}
    newest = sorted(found, reverse=True)[:quarters]
    return [(f"FY{fy} Q{q}", found[(fy, q)]) for fy, q in newest]


def _column(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + ord(ch.upper()) - 64
    return n - 1


def read_xlsx(path: Path) -> Iterator[dict[str, str]]:
    """Rows of the first sheet as {header: text}, streamed so a 100 MB file never sits in memory."""
    with zipfile.ZipFile(path) as book:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in book.namelist():
            with book.open("xl/sharedStrings.xml") as fh:
                for _, el in ET.iterparse(fh):
                    if el.tag == f"{NS}si":
                        shared.append("".join(t.text or "" for t in el.iter(f"{NS}t")))
                        el.clear()
        sheet = next(n for n in sorted(book.namelist()) if n.startswith("xl/worksheets/sheet"))
        header: list[str] | None = None
        with book.open(sheet) as fh:
            for _, el in ET.iterparse(fh):
                if el.tag != f"{NS}row":
                    continue
                cells: dict[int, str] = {}
                for c in el.iter(f"{NS}c"):
                    kind, v = c.get("t"), c.find(f"{NS}v")
                    if kind == "s" and v is not None:
                        text = shared[int(v.text or 0)]
                    elif kind == "inlineStr":
                        text = "".join(t.text or "" for t in c.iter(f"{NS}t"))
                    else:
                        text = v.text if v is not None and v.text else ""
                    cells[_column(c.get("r", ""))] = text
                el.clear()
                if header is None:
                    header = [cells.get(i, "").strip().upper() for i in range(max(cells, default=-1) + 1)]
                    continue
                yield {name: cells.get(i, "") for i, name in enumerate(header) if name}


def _number(text: str) -> float | None:
    text = re.sub(r"[$,\s]", "", text or "")
    try:
        return float(text)
    except ValueError:
        return None


def annual_wage(row: dict[str, str]) -> float | None:
    """The offered wage per year in USD (the low end of a range), or None when it is not plausible."""
    wage = _number(row.get("WAGE_RATE_OF_PAY_FROM", ""))
    unit = (row.get("WAGE_UNIT_OF_PAY") or "").strip().lower()
    if wage is None or unit not in PER_YEAR:
        return None
    wage *= PER_YEAR[unit]
    return wage if MIN_USD <= wage <= MAX_USD else None


def parse(row: dict[str, str]) -> dict | None:
    """A certified H-1B filing for a technical role, reduced to what the pay view needs."""
    if (row.get("CASE_STATUS") or "").strip().lower() != "certified":
        return None
    if (row.get("VISA_CLASS") or "H-1B").strip().upper() != "H-1B":
        return None
    title = (row.get("JOB_TITLE") or "").strip()
    family = roles.classify(title)
    if family is None:
        return None
    wage = annual_wage(row)
    if wage is None:
        return None
    employer = (row.get("EMPLOYER_NAME") or "").strip()
    return {
        "company": company_size.display_name(employer),
        "size": company_size.classify(employer),
        "role_family": family,
        "seniority": seniority.classify(title),
        "state": (row.get("WORKSITE_STATE") or "").strip().upper()[:2] or None,
        "usd": wage,
    }


def _spread(values: list[float]) -> dict:
    p25, p50, p75 = quantiles(values, n=4) if len(values) >= 4 else (min(values), median(values), max(values))
    return {"n": len(values), "median": round(p50, -2), "p25": round(p25, -2), "p75": round(p75, -2)}


def summarise(rows: list[dict], files: list[str], today: date) -> dict:
    by_role: dict[str, list[float]] = defaultdict(list)
    by_size: dict[str, list[float]] = defaultdict(list)
    by_state: dict[str, list[float]] = defaultdict(list)
    by_level: dict[str, list[float]] = defaultdict(list)
    by_company: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_role[r["role_family"]].append(r["usd"])
        by_size[r["size"]].append(r["usd"])
        by_level[r["seniority"]].append(r["usd"])
        if r["state"]:
            by_state[r["state"]].append(r["usd"])
        by_company[r["company"]].append(r)
    companies = []
    for name, group in sorted(by_company.items(), key=lambda kv: -len(kv[1]))[:TOP_COMPANIES]:
        if len(group) < MIN_N:
            break
        per_role: dict[str, list[float]] = defaultdict(list)
        for r in group:
            per_role[r["role_family"]].append(r["usd"])
        companies.append({
            "company": name, "size": group[0]["size"], **_spread([r["usd"] for r in group]),
            "roles": {f: _spread(v) for f, v in sorted(per_role.items(), key=lambda kv: -len(kv[1])) if len(v) >= MIN_N},
        })
    keep = lambda groups: {k: _spread(v) for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))  # noqa: E731
                           if len(v) >= MIN_N}
    return {
        "source": "US Department of Labor, H-1B Labor Condition Application disclosure data",
        "source_url": PAGE,
        "note": ("Certified H-1B filings for technical roles: the wage offered (low end of the range), per year in "
                 "USD. Filings, not ads: no skills, and only roles filled with visa workers."),
        "files": files,
        "generated": today.isoformat(),
        "n": len(rows),
        "role_labels": {f: roles.LABELS[f] for f in by_role},
        "size_labels": company_size.LABELS,
        "by_role": keep(by_role),
        "by_size": keep(by_size),
        "by_level": keep(by_level),
        "by_state": keep(by_state),
        "by_company": companies,
    }


def run(client: PoliteClient, out: Path, today: date, quarters: int = QUARTERS) -> dict:
    page = client.request("GET", PAGE, headers={"Accept": "text/html"})
    page.raise_for_status()
    files = latest_files(page.text, quarters)
    if not files:
        raise RuntimeError(f"no LCA disclosure files listed on {PAGE}")
    rows: list[dict] = []
    for label, url in files:
        url = url if url.startswith("http") else f"https://www.dol.gov{url}"
        response = client.request("GET", url, headers={"Accept": "*/*"})
        response.raise_for_status()
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as tmp:
            tmp.write(response.content)
            tmp.flush()
            del response
            before = len(rows)
            rows.extend(r for r in map(parse, read_xlsx(Path(tmp.name))) if r)
            print(f"{label}: {len(rows) - before} technical filings")
    if len(rows) < MIN_ROWS:
        # A changed file layout reads as almost nothing: keep the published summary instead.
        raise RuntimeError(f"only {len(rows)} technical filings read; the file layout may have changed")
    data = summarise(rows, [label for label, _ in files], today)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    return data


def load(path: Path) -> dict | None:
    """The published summary, for skills-market.json; None when it was never built."""
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None
