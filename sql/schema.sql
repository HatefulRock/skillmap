-- Applied on every run (idempotent). Lives in its own schema, so it never collides with
-- anything else in the Supabase project, and isn't exposed through Supabase's REST API.
create schema if not exists skillmap;

create table if not exists skillmap.postings (
  posting_id       text primary key,          -- "<source>:<native id>"
  source           text not null,
  native_id        text not null,
  company          text not null default '',
  title            text not null default '',
  description      text,                      -- full text, kept only for re-extraction; never published
  url              text,
  location         text,
  country          text,
  city             text,
  region           text,
  remote           boolean not null default false,
  seniority        text not null,
  role_family      text not null,
  sector           text not null,
  skills           text[] not null default '{}',
  skills_nice      text[] not null default '{}',
  posted_on        date,
  first_seen       date not null,
  last_seen        date not null,
  dedup_key        text not null,
  salary_min       numeric,
  salary_max       numeric,
  salary_currency  text,
  salary_period    text,
  seniority_hint   text,
  min_years_hint   int,
  sector_hint      text,
  country_hint     text,
  remote_hint      boolean,
  taxonomy_version int,
  inserted_at      timestamptz not null default now()
);
alter table skillmap.postings add column if not exists n_words int not null default 0;
alter table skillmap.postings add column if not exists min_years int;
alter table skillmap.postings add column if not exists terms text[] not null default '{}';
alter table skillmap.postings add column if not exists degree text;
alter table skillmap.postings add column if not exists visa_sponsorship boolean;
alter table skillmap.postings add column if not exists equity boolean not null default false;
-- Where the full text lives once it has moved out of the database (an object in file storage).
alter table skillmap.postings add column if not exists text_archive text;
create index if not exists postings_last_seen_idx on skillmap.postings (last_seen);
create index if not exists postings_source_idx on skillmap.postings (source, native_id);
create index if not exists postings_dedup_idx on skillmap.postings (dedup_key);
alter table skillmap.postings enable row level security;
-- lz4 compresses posting text better and faster than the default; skipped where unsupported.
do $$ begin
  alter table skillmap.postings alter column description set compression lz4;
exception when others then null;
end $$;

create table if not exists skillmap.runs (
  id          bigserial primary key,
  ran_at      timestamptz not null default now(),
  source      text not null,
  fetched     int not null,
  inserted    int not null,
  error       text
);
alter table skillmap.runs enable row level security;

-- Each taxonomy version's per-skill fingerprints, so a re-extraction after a taxonomy change only
-- matches the skills whose patterns changed (see Taxonomy.changed_since).
create table if not exists skillmap.taxonomy_versions (
  version      int primary key,
  fingerprints jsonb not null,
  saved_at     timestamptz not null default now()
);
alter table skillmap.taxonomy_versions enable row level security;
