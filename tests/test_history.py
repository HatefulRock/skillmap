import gzip
import json
from datetime import date

import httpx

from skillmap import history
from skillmap.archive import LocalArchive
from skillmap.extract.skills import Taxonomy
from skillmap.http import PoliteClient

TAX = Taxonomy.load()
TODAY = date(2026, 11, 19)
CDX = "https://index.commoncrawl.org/CC-MAIN-2024-33-index"


def warc(html: str, status: str = "200 OK") -> bytes:
    body = html.encode()
    http = f"HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\n\r\n".encode() + body
    return gzip.compress(b"WARC/1.0\r\nWARC-Type: response\r\n\r\n" + http)


def page(title, company, body):
    return (f"<html><head><title>Job Application for {title} at {company}</title><script>var x='Kubernetes'</script>"
            f"</head><body><div id=content><p>{body}</p></div></body></html>")


JOBS = {  # (token, id) -> html
    ("acme", "1"): page("Machine Learning Engineer", "Acme", "We use Python, PyTorch and Kubernetes."),
    ("acme", "2"): page("Backend Engineer", "Acme", "Go and PostgreSQL."),
    ("acme", "3"): page("Account Executive", "Acme", "Sell Salesforce."),
    ("beta", "9"): page("Data Engineer", "Beta", "Spark, Airflow and SQL."),
}


def cdx_block() -> bytes:
    lines = []
    for (t, i), h in sorted(JOBS.items()):
        cap = {"url": f"https://boards.greenhouse.io/{t}/jobs/{i}", "mime": "text/html", "status": "200",
               "filename": f"crawl/{t}-{i}.warc.gz", "offset": "0", "length": str(len(warc(h)))}
        lines.append(f"io,greenhouse,boards)/{t}/jobs/{i} 20240810120000 {json.dumps(cap)}")
    lines.append('io,greenhouse,boards)/acme 20240810120000 {"url": "https://boards.greenhouse.io/acme", '
                 '"mime": "text/html", "status": "200"}')
    lines.append('io,greenhouse,boards)/beta/jobs/7 20240810120000 {"url": "https://boards.greenhouse.io/beta/jobs/7", '
                 '"mime": "text/html", "status": "404"}')
    return gzip.compress("\n".join(lines).encode())


BLOCK = cdx_block()
OTHER = gzip.compress(b"com,example)/ 20240810120000 {}")
CLUSTER = (f"com,example)/ 20240810120000\tcdx-00000.gz\t0\t{len(OTHER)}\t1\n"
           f"io,greenhouse,boards)/acme/jobs/1 20240810120000\tcdx-00001.gz\t0\t{len(BLOCK)}\t2\n"
           f"org,zzz)/ 20240810120000\tcdx-00002.gz\t0\t10\t3\n").encode()
INDEX = history.DATA + "cc-index/collections/CC-MAIN-2024-33/indexes/"


def ranged(request: httpx.Request, body: bytes) -> httpx.Response:
    a, b = request.headers["Range"].removeprefix("bytes=").split("-")
    return httpx.Response(206, content=body[int(a):int(b) + 1], headers={"Content-Range": f"bytes {a}-{b}/{len(body)}"})


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.startswith(history.COLLINFO):
        return httpx.Response(200, json=[
            {"id": "CC-MAIN-2026-40", "cdx-api": "https://index.commoncrawl.org/CC-MAIN-2026-40-index"},  # running half
            {"id": "CC-MAIN-2024-33", "cdx-api": CDX},
            {"id": "CC-MAIN-2024-30", "cdx-api": "https://index.commoncrawl.org/CC-MAIN-2024-30-index"},  # older, same half
            {"id": "CC-MAIN-2012-10", "cdx-api": "x"},
        ])
    if url.startswith("https://index.commoncrawl.org/"):
        return httpx.Response(503)  # the index server is never needed
    if url == INDEX + "cluster.idx":
        return ranged(request, CLUSTER)
    if url == INDEX + "cdx-00001.gz":
        return ranged(request, BLOCK)
    if url == INDEX + "cdx-00000.gz":
        return ranged(request, OTHER)
    if url.startswith(history.DATA + "crawl/"):
        name = url.removeprefix(history.DATA + "crawl/").removesuffix(".warc.gz")
        token, job = name.split("-")
        assert request.headers["Range"].startswith("bytes=0-")
        return httpx.Response(206, content=warc(JOBS[(token, job)]))
    return httpx.Response(404)


def client():
    c = PoliteClient(min_interval=0, transport=httpx.MockTransport(handler), retries=0)
    c.sleep = lambda s: None
    return c


def test_pick_crawls_last_of_each_half_year_skipping_the_running_one():
    crawls = history.pick_crawls(client().get_json(history.COLLINFO), TODAY)
    assert [(c.id, c.period) for c in crawls] == [("CC-MAIN-2024-33", "2024-07")]


def test_job_key_only_job_pages():
    assert history.job_key("https://boards.greenhouse.io/acme/jobs/123") == ("boards.greenhouse.io", "acme", "123")
    assert history.job_key("https://jobs.lever.co/acme/0f5a3b2c-1111-2222-3333-444455556666") is not None
    assert history.job_key("https://jobs.lever.co/acme/0f5a3b2c-1111-2222-3333-444455556666/apply") is None
    assert history.job_key("https://boards.greenhouse.io/acme") is None
    assert history.job_key("https://boards.greenhouse.io/embed/jobs/1") is None


def test_page_title_and_text_drop_scripts():
    html = JOBS[("acme", "1")]
    assert history.page_title(html, "boards.greenhouse.io") == "Machine Learning Engineer"
    assert "Kubernetes'" not in history.page_text(html) and "PyTorch" in history.page_text(html)
    lever = "<title>Acme - Senior Data Scientist</title>"
    assert history.page_title(lever, "jobs.lever.co") == "Senior Data Scientist"


def test_parse_warc_skips_non_200():
    assert history.parse_warc(warc("<p>x</p>", "404 Not Found")) is None


def test_run_backfills_tech_ads_archives_text_and_resumes(tmp_path):
    archive = LocalArchive(tmp_path / "archive")
    result = history.run(client(), TAX, tmp_path, archive, TODAY, budget_minutes=10)
    assert result["crawls_added"] == 1 and result["crawls_left"] == 0
    rows = history.load_rows(tmp_path / "history-ads.jsonl.gz")
    by_title = {r["title"]: r for r in rows}
    assert set(by_title) == {"Machine Learning Engineer", "Backend Engineer", "Data Engineer"}  # no sales role
    ml = by_title["Machine Learning Engineer"]
    assert "Python" in ml["skills"] and "Kubernetes" in ml["skills"] and ml["period"] == "2024-07"
    assert ml["captured"] == "2024-08-10" and ml["company"] == "acme"
    state = json.loads((tmp_path / "history-state.json").read_text())
    assert state["crawls"]["CC-MAIN-2024-33"]["ads"] == 3
    # A second run has nothing left to fetch.
    assert history.run(client(), TAX, tmp_path, archive, TODAY, budget_minutes=10)["crawls_added"] == 0
    # A taxonomy change re-extracts from the archived text without fetching.
    newer = Taxonomy.load()
    newer.version = TAX.version + 1
    offline = PoliteClient(min_interval=0, transport=httpx.MockTransport(
        lambda r: handler(r) if str(r.url).startswith(history.COLLINFO) else httpx.Response(500)), retries=0)
    assert history.run(offline, newer, tmp_path, archive, TODAY, budget_minutes=10)["reextracted"] == 3


def test_out_of_time_keeps_what_it_fetched_and_the_next_run_carries_on(tmp_path):
    archive = LocalArchive(tmp_path / "archive")
    pages: list[str] = []

    def counting(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(history.DATA + "crawl/"):
            pages.append(str(request.url))
        return handler(request)

    c = PoliteClient(min_interval=0, transport=httpx.MockTransport(counting), retries=0)
    c.sleep = lambda s: None
    # The deadline passes once two job pages have been fetched.
    result = history.run(c, TAX, tmp_path, archive, TODAY, budget_minutes=1,
                         clock=lambda: 10_000.0 if len(pages) >= 2 else 0.0)
    assert result["crawls_added"] == 0 and len(pages) == 2
    state = json.loads((tmp_path / "history-state.json").read_text())
    partial = state["partial"]["CC-MAIN-2024-33"]
    assert partial["fetched"] == 2 and len(partial["tried"]) == 2 and len(partial["archives"]) <= 1
    kept = history.load_rows(tmp_path / "history-ads.jsonl.gz")
    # The second run fetches only the pages not tried yet, and settles the crawl.
    first = list(pages)
    result = history.run(c, TAX, tmp_path, archive, TODAY, budget_minutes=10, clock=lambda: 0.0)
    assert result["crawls_added"] == 1 and len(pages) == 4 and not set(first) & set(pages[2:])
    rows = history.load_rows(tmp_path / "history-ads.jsonl.gz")
    assert {r["id"] for r in kept} <= {r["id"] for r in rows}
    assert {r["title"] for r in rows} == {"Machine Learning Engineer", "Backend Engineer", "Data Engineer"}
    state = json.loads((tmp_path / "history-state.json").read_text())
    assert not state.get("partial") and state["crawls"]["CC-MAIN-2024-33"]["ads"] == 3
    # Text from both runs is archived, so a taxonomy change re-extracts every ad.
    newer = Taxonomy.load()
    newer.version = TAX.version + 1
    offline = PoliteClient(min_interval=0, transport=httpx.MockTransport(
        lambda r: handler(r) if str(r.url).startswith(history.COLLINFO) else httpx.Response(500)), retries=0)
    assert history.run(offline, newer, tmp_path, archive, TODAY, budget_minutes=10)["reextracted"] == 3


def test_series_withholds_thin_half_years():
    rows = [{"period": "2019-01", "company": f"c{i % 30}", "skills": ["Python"] * (i % 2)} for i in range(120)]
    rows += [{"period": "2019-07", "company": "c", "skills": ["Python"]}] * 5
    s = history.series(rows, ["Python", "Go"])
    assert s["months"] == ["2019-01", "2019-07"] and s["n"] == [120, 5] and s["companies"] == [30, 1]
    assert s["shares"]["Python"] == [0.5, None] and s["shares"]["Go"] == [0.0, None]


def test_surt_prefix_and_cluster_blocks():
    assert history.surt_prefix("boards.greenhouse.io") == "io,greenhouse,boards)/"
    crawl = history.Crawl("CC-MAIN-2024-33", CDX, "2024-07")
    assert history.cluster_blocks(client(), crawl, "io,greenhouse,boards)/") == [
        ("cdx-00000.gz", 0, len(OTHER)), ("cdx-00001.gz", 0, len(BLOCK))]
    assert history.cluster_blocks(client(), crawl, "co,lever,jobs)/") == []


def test_cluster_search_over_a_large_index():
    keys = [f"com,site{i:05d})/ 20240810120000\tcdx-{i // 100:05d}.gz\t{i}\t10\t{i}" for i in range(3000)]
    keys.insert(1500, "io,greenhouse,boards)/x 20240810120000\tcdx-99999.gz\t7\t9\t0")
    big = ("\n".join(sorted(keys)) + "\n").encode()
    assert len(big) > 3 * 16384
    c = PoliteClient(min_interval=0, transport=httpx.MockTransport(lambda r: ranged(r, big)), retries=0)
    c.sleep = lambda s: None
    blocks = history.cluster_blocks(c, history.Crawl("CC-MAIN-2024-33", CDX, "2024-07"), "io,greenhouse,boards)/")
    assert blocks[-1] == ("cdx-99999.gz", 7, 9) and len(blocks) == 2


def test_an_empty_crawl_is_retried_before_it_counts_as_done(tmp_path):
    empty = PoliteClient(min_interval=0, transport=httpx.MockTransport(
        lambda r: handler(r) if str(r.url).startswith(history.COLLINFO) else httpx.Response(503)), retries=0)
    for expected_done in (False, False, True):
        history.run(empty, TAX, tmp_path, None, TODAY, budget_minutes=10)
        state = json.loads((tmp_path / "history-state.json").read_text())
        assert ("CC-MAIN-2024-33" in state["crawls"]) is expected_done
    # A crawl recorded empty by an older run (no tries) is tried again.
    state["crawls"]["CC-MAIN-2024-33"] = {"period": "2024-07", "ads": 0}
    state.pop("empty", None)
    (tmp_path / "history-state.json").write_text(json.dumps(state))
    assert history.run(client(), TAX, tmp_path, None, TODAY, budget_minutes=10)["crawls_added"] == 1
