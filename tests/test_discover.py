from skillmap import discover


def test_crawl_tokens_take_the_first_path_segment_of_the_right_host():
    lines = [
        '{"url": "https://boards.greenhouse.io/Stripe/jobs/123"}',
        '{"url": "https://boards.greenhouse.io/embed/job_app?token=1"}',
        '{"url": "https://job-boards.greenhouse.io/anthropic"}',
        '{"url": "https://boards.greenhouse.io/robots.txt"}',
        'not json',
    ]
    assert discover.crawl_tokens(lines, "boards.greenhouse.io") == {"stripe"}
    assert discover.crawl_tokens(lines, "job-boards.greenhouse.io") == {"anthropic"}


def test_crawl_candidates_skip_known_boards_and_survive_a_failing_host():
    import httpx

    from skillmap.http import PoliteClient

    def handler(request):
        url = str(request.url)
        if "collinfo" in url:
            return httpx.Response(200, json=[{"cdx-api": "https://index.commoncrawl.org/CC-TEST-index"}])
        if "job-boards.greenhouse.io" in url:
            return httpx.Response(503)
        if "showNumPages" in url:
            return httpx.Response(200, json={"pages": 1})
        return httpx.Response(200, text='{"url": "https://boards.greenhouse.io/acme/jobs/1"}\n'
                                         '{"url": "https://boards.greenhouse.io/newco"}\n')

    client = PoliteClient(min_interval=0, transport=httpx.MockTransport(handler), retries=0)
    client.sleep = lambda s: None
    out = discover.crawl_candidates(client, ["greenhouse"], known={("greenhouse", "acme")})
    assert [(c.name, c.platform, c.tokens, c.origin) for c in out] == [("newco", "greenhouse", ["newco"], "commoncrawl")]


def test_scan_probes_crawl_candidates_only_on_their_platform_and_keeps_busy_boards():
    calls = []

    def probe(client, token):
        calls.append(token)
        return {"bigco": (12, "Big Co"), "tiny": (2, "Tiny")}.get(token)

    discover.PROBES["fake"] = probe
    try:
        cands = [discover.Candidate("bigco", "other", "commoncrawl", None, ["bigco"], "fake"),
                 discover.Candidate("tiny", "other", "commoncrawl", None, ["tiny"], "fake"),
                 discover.Candidate("elsewhere", "other", "commoncrawl", None, ["elsewhere"], "lever")]
        found, complete = discover.scan("fake", cands, client=None)
    finally:
        del discover.PROBES["fake"]
    assert complete and calls == ["bigco", "tiny"]
    assert [(b.company, b.token, b.jobs) for b in found] == [("Big Co", "bigco", 12)]


def test_employers_seen_only_on_job_boards_become_candidates(tmp_path):
    import gzip
    import json

    from skillmap.discover import seen_candidates

    rows = ([{"company": "Speechify", "source": "himalayas", "role_family": "backend"}] * 4
            + [{"company": "Stripe", "source": "himalayas", "role_family": "backend"}] * 4
            + [{"company": "Stripe", "source": "greenhouse", "role_family": "backend"}]
            + [{"company": "RANDSTAD PTE. LIMITED", "source": "mcf", "role_family": "backend"}] * 5
            + [{"company": "Tiny Co", "source": "hn", "role_family": "backend"}] * 2)
    path = tmp_path / "live.jsonl.gz"
    with gzip.open(path, "wt") as fh:
        fh.write("\n".join(json.dumps(r) for r in rows))
    assert [c.name for c in seen_candidates(path)] == ["Speechify"]
