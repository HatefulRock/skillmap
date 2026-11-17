# skillmap open data

Every Monday at 02:00 Singapore time, the refresh workflow republishes this folder. Don't edit it by hand.

It answers questions like these. Which skills do AI/ML, forward-deployed and Python job ads ask for? How is that changing? What does the work pay? The data comes from public job boards (Greenhouse, Lever, Ashby, Workable), MyCareersFuture, Adzuna and the Hacker News "Who is hiring" threads. How the posts are found and categorised is in the [top-level README](../README.md).

## Files

| File | What it is |
|---|---|
| `skills-market.json` | All aggregates in one document, and what the website reads. Shape below. |
| `csv/*.csv` | The same aggregates as flat, long-format tables (one value per row). |
| `digest.json` | The weekly "what changed" notes, newest first, two years kept. |
| `weekly.md` | The latest note as Markdown. The same text is in the top-level README. |
| `feed.xml` | RSS 2.0 feed of the notes. |
| `postings-live.jsonl.gz` | The week's live tech postings, one JSON object per line: metadata, extracted skills and stated pay. It contains no description text. It is also attached to the `data-latest` release. |
| `watchlist.json` | The company boards with the most postings matching the author's own targets. It is personal; see below. |
| `skills-heatmap.svg` | The author's self-rated skills heatmap. |

Raw URLs follow the pattern
`https://raw.githubusercontent.com/HatefulRock/skillmap/main/data/<file>`.

## How to read the numbers

- **Share** is the fraction of postings in a group that ask for a skill. It runs from 0 to 1. A skill counts as asked for if it is required or listed as nice to have. Shares are over *tech* postings only: every role family except `non_tech`.
- **Live** means seen on a board within the current window (`meta.windows.current_days`, 90 days). A posting is counted once even if it appears on several sources. Duplicates are matched on company, title and place.
- A group with fewer than 30 postings is left out rather than shown with a noisy share.
- **Pay** is what the ad states, converted to a yearly figure. It uses the midpoint of the range and is also given in USD at fixed rates. Most ads state no pay. Where pay is stated, it skews toward places that require it (US states, Singapore's MyCareersFuture), so compare within a country.
- **Pay premium** compares ads that ask for a skill with ads that don't, only within the same country, role family and seniority. It is the difference in median pay, weighted across those groups, shown as a percentage. It is a correlation, not a price: the skill may stand in for something else about the job.
- **Lift** is how much more often something appears than chance or than elsewhere would predict. In role DNA, 2.0 means twice as common in this role as in other tech roles. In the stacks network, it means two skills appear together twice as often as their separate shares predict.
- Skill names come from a fixed taxonomy (`config/taxonomy.yaml`, version in `meta.taxonomy_version`). When the taxonomy changes, every stored posting is re-extracted, so trends stay comparable. `changes.terms` lists tech-looking words the taxonomy doesn't know yet.

## `skills-market.json`

| Key | Contents |
|---|---|
| `meta` | `as_of`, `generated_at`, `taxonomy_version`, `windows`, `min_group`, `totals`, source `attribution` and `runs` history. |
| `categories` | Skill → category. |
| `current` | Live postings: `n`, and `skills[]` with `name`, `category`, `share`, `count`, `nice_share`, and `delta` against the previous window. |
| `trends.market` / `trends.hn` | Monthly series: `months[]`, `n[]`, and `shares{skill: [share or null]}`. |
| `trends.by_role` / `trends.by_seniority` | The same series per group, by quarter. |
| `segments` | Shares by `seniority`, `role_family`, `region`, `country` and `sector`. |
| `pay` | `countries[]` (local and USD quartiles) and `role_seniority_usd[country][family][level]`. |
| `salary` | Singapore monthly pay by skill. |
| `changes` | `movers` (risers and fallers over 90 days, also `movers_by_role`, `movers_by_seniority` and `hn_movers`), `mix` (role and level mix over time), `per_post` (ad shape: skills, words, years, degree, visa, equity), `companies` (largest and growing), and `terms` (new vocabulary). |
| `insights` | `pay_premium`, `role_dna`, `seniority_ladder`, `stacks`, `requirement_creep` and `learn_next`. |
| `pairs` | The most common skill pairs. |
| `digest` | The latest weekly notes. These are the same as `digest.json`. |
| `me` | The author's self-ratings next to demand. It is personal. |

## CSV tables

| Table | Columns |
|---|---|
| `skills_current` | skill, category, share, postings, nice_to_have_share, delta_vs_previous |
| `skill_trends_monthly` | series (`market` or `hn`), month, postings, skill, share |
| `skill_trends_by_group_quarterly` | group_type, group, series, quarter_start, postings, skill, share |
| `segments` | dimension, key, label, postings, skill, share |
| `pay_by_country` | country, ads_with_pay, currency, median, p25, p75, median_usd, p25_usd, p75_usd |
| `pay_by_role_seniority_usd` | country, role_family, seniority, ads, median, p25, p75 |
| `pay_premium` | skill, premium, matched_postings |
| `role_dna` | role_family, skill, share, share_elsewhere, lift |
| `seniority_ladder` | skill, seniority, share |
| `skill_pairs` | skill_a, skill_b, postings_together, lift |
| `skill_movers` | direction, skill, share_now, share_before, delta |
| `ad_shape_monthly` | month, postings, and one column per measure |
| `companies` | company, live_postings, previous_window, delta, sector, top_role, top_skills |

## Personal files

`watchlist.json`, `skills-heatmap.svg` and the `me` and `learn_next` blocks are built from the author's own targets and self-ratings (`config/my_skills.yaml`). They are published as an example, not as market data. Fork the repo and edit that file to get your own.

`watchlist.json` (`"version": 1`) has the following fields:

- `generated_at` (UTC);
- `targets`;
- `boards[]`, ranked: company, ats, token, sector, matching, live_tech, skill_fit, countries, titles;
- `omnideck`: `watch_greenhouse`, `watch_lever` and `watch_ashby` slug lists, ready for a job-search agent.

## License

The data in this folder is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). You may use it for anything if you credit "skillmap by Pierre Blachère" and link back to this repository.

The job ads themselves belong to the companies that published them. This folder holds only facts derived from them, such as titles, places, extracted skill names and stated pay ranges, never the ad text.
