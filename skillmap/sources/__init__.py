"""Collectors. Each module exposes `collect(...)` yielding `RawPosting`s.

Only sources that publish postings for programmatic reading are used: companies' own public
job-board APIs (Greenhouse, Lever, Ashby, Workable), the JSON that Workday career sites,
amazon.jobs and Microsoft's careers site load into their own pages, schema.org JobPosting
markup on careers sites (robots.txt honoured), the Adzuna API (free key, attribution),
Hacker News "Who is hiring" threads via the official Algolia HN API, and MyCareersFuture's
search API (read slowly, metadata only). LinkedIn, Indeed and Glassdoor forbid scraping and
are deliberately absent.
"""
