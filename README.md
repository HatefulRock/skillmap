# skillmap

Which skills do employers actually ask for, how is that changing, and how does it differ by
seniority, location, role and sector, and what does it pay? `skillmap` answers that from
job postings at about 1,000 companies plus Singapore's national job portal, collected every
week, and publishes the result to
[pierreblachere.com/skills](https://www.pierreblachere.com/skills).

![Skills demand vs my level](data/skills-heatmap.svg)

The heatmap above regenerates every week: each cell is the share of current tech postings
in that role family that ask for the skill, and the last column is my own rating.

## This week

<!-- weekly:start -->
_The first weekly note appears after the first full run._
<!-- weekly:end -->

[RSS](data/feed.xml) · [all notes](data/digest.json) · [CSV tables](data/csv/) · [JSON](data/skills-market.json)

## How it works

```
 discover (monthly)
 YC companies +   ──▶ config/boards.yaml
 candidates.yaml        (~1,000 boards)
                              │
 company boards ◀─────────────┘
 (Greenhouse, Lever, ─┐
  Ashby, Workable,    │    normalise                 Supabase Postgres           aggregate
  SmartRecruiters)    ├──▶ skills · seniority ──▶   postings (fields;     ──▶  data/skills-market.json ──▶ website
 Adzuna API           │    role · location          text → Storage)            data/skills-heatmap.svg ──▶ this README
 MyCareersFuture      │    sector · pay                                       data/postings-live.jsonl.gz
 HN "Who is hiring?" ─┘
                         GitHub Actions, weekly, Mondays 02:00 Singapore time
```

- **Discover** (`skillmap/discover.py`). Greenhouse, Lever, Ashby and Workable publish a company's open jobs at a fixed address once you know its board name. Once a month,
  discovery takes every active Y Combinator company that is hiring (from the public
  [yc-oss](https://github.com/yc-oss/api) list) plus the AI, crypto, fintech, quant and
  Singapore/Asia employers in `config/candidates.yaml`, tries the likely board names on each
  platform, checks that a board naming its company names the right one, and writes every board
  with open jobs to `config/boards.yaml`. `config/companies.yaml` holds hand-checked boards and
  wins over a discovered one. To widen the census, add names to `candidates.yaml`.
- **Collect** (`skillmap/sources/`). One module per source, each turning that source's JSON
  into a common `RawPosting`. A shared HTTP client identifies itself, rate-limits per host and
  retries transient errors. A source that fails is recorded in the run summary and the rest of
  the run carries on.
- **Extract** (`skillmap/extract/`). A taxonomy of 90 skills (`config/taxonomy.yaml`), each a
  set of regexes written for full posting text: word boundaries, case-sensitive patterns for
  ambiguous names like *Go* and *MCP*, and context rules so "go beyond" is not Go. Lines under
  a "Nice to have" heading (or saying "is a plus") are tracked separately from requirements.
  Seniority comes from the title, then from stated years of experience, then from the
  source's own level. Role family comes from the title; location is normalised to country,
  city and region. Pay comes from the source's salary field when it has one (MyCareersFuture,
  Ashby, Lever, Adzuna) and otherwise from a stated range in the text, which US and EU
  pay-transparency laws now put in most ads; it is converted to a yearly figure, and to USD at
  fixed rates for comparisons.
- **Store** (`skillmap/store.py`). Every posting is stored once, keyed by its id on its source,
  with the day it was first and last seen: a new id is a new posting, a known one only moves
  its last-seen date, and one that stops appearing keeps the day it was last live. The
  database holds the extracted fields (about 1 KB a posting). The full text of tech postings
  goes, after each run, into gzip'd batch files in a private Supabase Storage bucket
  (`skillmap/archive.py`), so extraction can be re-run: bumping the taxonomy `version`
  re-extracts every stored posting on the next run, one file at a time. Non-tech postings keep
  metadata only. The text itself is never published.
- **Per-posting shape.** Besides skills, each posting records its length, the years of
  experience it asks for, the degree it firmly requires, whether it says it sponsors visas,
  whether it offers equity, and the tech-looking terms in it that the taxonomy doesn't know yet
  (`extract/terms.py`, `extract/perks.py`). None of these can be recovered once an ad is gone,
  so they are recorded from the first run.
- **Aggregate** (`skillmap/aggregate.py`). Pure functions from stored rows to one JSON file:
  skill shares over the last 30 days with change against the 30 days before, monthly trends,
  quarterly trends inside each role family and seniority level, what is changing
  (`skillmap/trends.py`: skill risers and fallers overall and per role and level, the monthly
  mix of roles, levels, sectors and regions, the average ad's skill count, length, years asked,
  degree, visa, equity and pay-stated rates, companies hiring more or less and what each asks
  for, and emerging terms counted by how many companies use them), breakdowns by seniority, role
  family, region, country and sector over 90 days, which skills appear together, and advertised
  pay by country and by role × seniority. The same ad found on several
  sources is counted once. A group with fewer than 30 postings is reported with its size but no
  percentages.

## Sources

Only sources that publish postings for programmatic reading are used:

| Source | What it adds | Access |
|---|---|---|
| Greenhouse, Lever, Ashby, Workable (SmartRecruiters opt-in: its API host's robots.txt disallows crawlers) | Full postings from ~1,000 companies (`config/boards.yaml`, `config/companies.yaml`) | Public job-board APIs that the companies publish themselves |
| [Adzuna](https://developer.adzuna.com/) | Broad market in Singapore, the UK and the US | Free API key; "Job data: The Adzuna API". Descriptions are snippets, so its skill counts are a lower bound |
| MyCareersFuture | Singapore's national job portal, including advertised salary ranges | The search API its own site uses, read slowly (one request every 3 s), newest first, stopping at postings already collected |
| Hacker News "Who is hiring?" | Monthly threads back to 2011: the long trend lines | Official [HN Algolia API](https://hn.algolia.com/api) |

LinkedIn, Indeed and Glassdoor forbid scraping in their terms and are not used.

## Running it

```sh
uv sync
uv run pytest                                  # unit + end-to-end tests (no network)
uv run skillmap run --sources hn --hn-since 2026-10-01 --out /tmp/out   # in-memory store
DATABASE_URL=postgresql://… uv run skillmap run                         # the real thing
```

Repository secrets used by `.github/workflows/refresh.yml`:

| Secret | Required | Where it comes from |
|---|---|---|
| `DATABASE_URL` | yes | Supabase → Connect → Session pooler URI |
| `SUPABASE_SECRET_KEY` | recommended (without it, posting text stays in the database and fills it within months) | Supabase → Project Settings → API Keys → secret key |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | no (Adzuna is skipped without them) | developer.adzuna.com |

The first run backfills every HN thread since 2011 and the last 60 days of MyCareersFuture;
later runs only fetch what is new. Both workflows can also be started by hand: `refresh` with
a subset of sources, a board limit or a dry run; `discover` with a candidate limit.

```sh
uv run skillmap discover --no-yc --limit 50     # probe the first 50 curated names
```

Tests that need Postgres run when `SKILLMAP_TEST_DATABASE_URL` is set; CI provides one.

## Known limits

- Company boards cover companies whose careers pages run on those platforms: mostly
  startups and scale-ups, US-heavy through Y Combinator. Big employers on Workday or in-house
  systems are missing; MyCareersFuture (and Adzuna, with a key) cover them for Singapore.
- Pay is only what ads state. US ads state it far more often (by law) than Asian ones, so
  per-country figures rest on very different sample sizes; each figure carries its count.
- HN posters are mostly startups and skew remote and US. That series is shown separately and
  never mixed with the live market numbers.
- Regex extraction misses phrasing it has no pattern for. The tests pin both what it must find
  and the false positives it must avoid; new cases go in `tests/test_extract.py`. Once a month
  (`audit` workflow) Claude labels about 100 recent postings and the regex is scored against it;
  the precision and recall land in `data/audit.json` and on the website, and the skills with the
  most disagreements show where the taxonomy needs work. Set the `ANTHROPIC_API_KEY` secret to
  turn it on (a few dollars a month).
