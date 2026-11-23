import zipfile
from datetime import date

from skillmap import visa

HEAD = ["CASE_STATUS", "VISA_CLASS", "JOB_TITLE", "EMPLOYER_NAME", "WAGE_RATE_OF_PAY_FROM", "WAGE_UNIT_OF_PAY",
        "WORKSITE_STATE"]


def _xlsx(path, rows):
    strings = sorted({c for r in rows for c in r if isinstance(c, str)})
    index = {s: i for i, s in enumerate(strings)}
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    sst = f"<sst {ns}>" + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>"
    body = []
    for n, row in enumerate(rows, 1):
        cells = []
        for col, value in enumerate(row):
            ref = f"{chr(65 + col)}{n}"
            if isinstance(value, str):
                cells.append(f'<c r="{ref}" t="s"><v>{index[value]}</v></c>')
            else:
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
        body.append(f'<row r="{n}">{"".join(cells)}</row>')
    sheet = f"<worksheet {ns}><sheetData>{''.join(body)}</sheetData></worksheet>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/sharedStrings.xml", sst)
        z.writestr("xl/worksheets/sheet1.xml", sheet)


def test_reads_rows_and_keeps_certified_technical_filings(tmp_path):
    path = tmp_path / "lca.xlsx"
    _xlsx(path, [
        HEAD,
        ["Certified", "H-1B", "Senior Software Engineer", "GOOGLE LLC", 185000, "Year", "CA"],
        ["Certified", "H-1B", "Machine Learning Engineer", "Google LLC", 95, "Hour", "NY"],
        ["Denied", "H-1B", "Software Engineer", "Google LLC", 150000, "Year", "CA"],
        ["Certified", "E-3 Australian", "Software Engineer", "Google LLC", 150000, "Year", "CA"],
        ["Certified", "H-1B", "Accountant", "Google LLC", 90000, "Year", "CA"],
        ["Certified", "H-1B", "Software Engineer", "Tiny LLC", 900, "Year", "TX"],
    ])
    rows = list(visa.read_xlsx(path))
    assert rows[0]["EMPLOYER_NAME"] == "GOOGLE LLC" and rows[0]["WAGE_RATE_OF_PAY_FROM"] == "185000"
    kept = [r for r in map(visa.parse, rows) if r]
    assert [(r["company"], r["usd"]) for r in kept] == [("Google", 185000.0), ("Google", 95 * 2080.0)]
    assert kept[0]["size"] == "big_tech"


def test_wage_units_and_money_text():
    assert visa.annual_wage({"WAGE_RATE_OF_PAY_FROM": "$12,500.00", "WAGE_UNIT_OF_PAY": "Month"}) == 150000
    assert visa.annual_wage({"WAGE_RATE_OF_PAY_FROM": "4000", "WAGE_UNIT_OF_PAY": "Bi-Weekly"}) == 104000
    assert visa.annual_wage({"WAGE_RATE_OF_PAY_FROM": "", "WAGE_UNIT_OF_PAY": "Year"}) is None


def test_latest_files_and_summary():
    page = "".join(f'<a href="/sites/dolgov/files/ETA/oflc/pdfs/LCA_Disclosure_Data_FY{fy}_Q{q}.xlsx">x</a>'
                   for fy in (2025, 2026) for q in (1, 2, 3, 4))
    assert [label for label, _ in visa.latest_files(page)] == ["FY2026 Q4", "FY2026 Q3", "FY2026 Q2", "FY2026 Q1"]
    rows = [{"company": "Google", "size": "big_tech", "role_family": "backend", "seniority": "senior",
             "state": "CA", "usd": 150000.0 + i * 1000} for i in range(25)]
    data = visa.summarise(rows, ["FY2026 Q4"], date(2026, 11, 23))
    assert data["by_company"][0]["company"] == "Google" and data["by_company"][0]["n"] == 25
    assert data["by_role"]["backend"]["median"] == 162000
    assert "backend" in data["by_company"][0]["roles"]


def test_employer_names_merge_legal_entities():
    assert visa.employer_name("Apple Inc.") == "Apple"
    assert visa.employer_name("GOOGLE LLC") == "Google"
    assert visa.employer_name("Microsoft Corporation") == "Microsoft"
    assert visa.employer_name("WAL-MART ASSOCIATES, INC.") == "Wal-Mart Associates"
    assert visa.employer_name("Ernst & Young U.S. LLP") == "Ernst & Young"
    assert visa.employer_name("BANK OF AMERICA N.A.") == "Bank of America"


def test_history_gives_a_median_per_fiscal_year():
    rows = [{"company": "Google", "size": "big_tech", "role_family": "backend", "seniority": "senior", "state": "CA",
             "usd": 100000.0 + fy * 1000 + i, "fy": fy} for fy in (2020, 2021) for i in range(25)]
    rows += [{"company": "Tiny", "size": "unknown", "role_family": "backend", "seniority": "mid", "state": "TX",
              "usd": 90000.0, "fy": 2020}]
    out = visa.history(rows)
    assert sorted(out["years"]) == ["2020", "2021"]
    assert out["years"]["2021"]["by_company"]["Google"]["median"] > out["years"]["2020"]["by_company"]["Google"]["median"]
    assert "Tiny" not in out["years"]["2020"]["by_company"]  # too few filings
