"""The weekly email: what changed, new edge skills for my roles, and new openings that fit my stack.

Built after each refresh from the files the run publishes (data/skills-market.json and
data/postings-live.jsonl.gz) and the previous run's copies of them, then sent through Resend's
HTTP API. The recipient and the key come from the environment (repository secrets), never from
the repository. Without a key the email is only written to a file, so a run never fails for it.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import time
from datetime import date, timedelta
from html import escape
from pathlib import Path

import httpx

from . import digest, watchlist
from .extract import roles

log = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
DEFAULT_FROM = "Skillmap <onboarding@resend.dev>"  # Resend's shared sender: delivers to the account's own address
EDGE_ROLES = ("ai_ml", "fde")
EDGE_TOP = 15  # an edge skill is "new" when it enters this role's top 15
ROLE_RISE_MIN = 0.01  # a role-share rise below one percentage point is noise
MIN_STRONG = 3  # an opening fits when it names at least this many skills I rate 3+
OPENINGS = 10  # per group
RETRIES = 4


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None


def load_postings(path: Path) -> list[dict]:
    try:
        with gzip.open(path, "rt") as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except (FileNotFoundError, OSError, ValueError):
        return []


def _top_edge(data: dict | None, role: str) -> list[dict]:
    skills = (((data or {}).get("insights") or {}).get("edge") or {}).get("families", {}).get(role, {}).get("skills") or []
    return [s for s in skills if (s.get("mine") or 0) < 3][:EDGE_TOP]


def new_edge(current: dict, previous: dict | None) -> dict[str, list[str]]:
    """Per role, edge skills (ones I don't rate 3+) that entered the top list since last week.
    The first week there's nothing to compare with, so it lists the current top five."""
    out = {}
    for role in EDGE_ROLES:
        now = [s["name"] for s in _top_edge(current, role)]
        if previous is None or not _top_edge(previous, role):
            out[role] = now[:5]
        else:
            before = {s["name"] for s in _top_edge(previous, role)}
            out[role] = [n for n in now if n not in before]
    return out


def role_risers(current: dict, previous: dict | None, role: str = "ai_ml", top: int = 5) -> list[tuple[str, float, float]]:
    """Skills whose share of this role's ads rose most since last week: (name, share, change)."""
    if not previous:
        return []
    before = {s["name"]: (s.get("roles") or {}).get(role) for s in previous.get("current", {}).get("skills", [])}
    moves = []
    for s in current.get("current", {}).get("skills", []):
        now, then = (s.get("roles") or {}).get(role), before.get(s["name"])
        if now is not None and then is not None and now - then >= ROLE_RISE_MIN:
            moves.append((s["name"], now, now - then))
    return sorted(moves, key=lambda m: -m[2])[:top]


def _where(row: dict, targets: dict) -> str:
    """'near' for a target country or region, else 'remote'."""
    if row.get("country") in set(targets.get("countries") or []) or row.get("region") in set(targets.get("regions") or []):
        return "near"
    return "remote"


def openings(live: list[dict], seen_before: set[str], targets: dict, ratings: dict[str, int],
             since: str | None) -> dict[str, list[dict]]:
    """Postings new since last week that match my targets and name MIN_STRONG+ skills I rate 3+,
    split into ones in my target places and remote ones, best fit first.

    Fit weighs each matched skill by how well I know it (a 5 counts more than a 3) and divides by
    the root of the ad's skill count, so a long ad doesn't win on length alone. My target roles
    come first in their own order (AI/ML before backend)."""
    order = {f: i for i, f in enumerate(targets.get("role_families") or [])}
    picked: dict[str, list[dict]] = {"near": [], "remote": []}
    seen: set[tuple[str, str]] = set()
    for row in live:
        # New = not in last week's snapshot; without one, first seen in the last week.
        if row["posting_id"] in seen_before or (not seen_before and since and row.get("first_seen", "") <= since):
            continue
        if not watchlist.matches(row, targets):
            continue
        skills = row.get("skills") or []
        mine = sorted((s for s in skills if ratings.get(s, 0) >= 3), key=lambda s: -ratings[s])
        if len(mine) < MIN_STRONG:
            continue
        key = (row["company"].lower(), row["title"].lower())
        if key in seen:  # the same ad posted for several countries
            continue
        seen.add(key)
        fit = sum(ratings[s] - 2 for s in mine) / max(1, len(skills)) ** 0.5
        picked[_where(row, targets)].append({**row, "mine": mine, "missing": [s for s in skills if s not in mine],
                                             "fit": round(fit, 2)})
    for rows in picked.values():
        rows.sort(key=lambda r: (order.get(r["role_family"], len(order)), -r["fit"], r["company"]))
        del rows[OPENINGS:]
    return picked


def build(current: dict, previous: dict | None, live: list[dict], live_before: list[dict], profile: dict) -> dict:
    since = (previous or {}).get("meta", {}).get("as_of")
    if not since:
        since = (date.fromisoformat(current["meta"]["as_of"]) - timedelta(days=7)).isoformat()
    jobs = openings(live, {r["posting_id"] for r in live_before}, profile.get("targets") or {},
                    profile.get("ratings") or {}, since)
    return {
        "date": current["meta"]["as_of"],
        "since": since,
        "note": digest.build_note(current, previous)["lines"],
        "risers": role_risers(current, previous),
        "edge": new_edge(current, previous),
        "first_week": previous is None,
        "openings": jobs,
    }


def _place(row: dict) -> str:
    if row.get("remote"):
        return "Remote" + (f" ({row['country']})" if row.get("country") else "")
    return row.get("city") or row.get("location") or row.get("country") or ""


def _pay(row: dict) -> str:
    if not row.get("salary_min") or not row.get("salary_currency"):
        return ""
    lo, hi = row["salary_min"], row.get("salary_max") or row["salary_min"]
    per = {"year": "/yr", "month": "/mo", "hour": "/h"}.get(row.get("salary_period") or "year", "")
    return f"{row['salary_currency']} {lo:,.0f}–{hi:,.0f}{per}" if hi != lo else f"{row['salary_currency']} {lo:,.0f}{per}"


def subject(d: dict) -> str:
    n = sum(len(v) for v in d["openings"].values())
    return f"Skills market, week of {d['date']}: {n} new opening{'s' if n != 1 else ''} that fit"


def html(d: dict) -> str:
    h = escape
    parts = [
        '<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:640px;margin:auto;color:#1a1a1a;line-height:1.5">',
        f'<h2 style="margin:0 0 4px">Skills market, week of {h(d["date"])}</h2>',
        f'<p style="margin:0 0 16px;color:#666">Since {h(d["since"])}.</p>' if d["since"] else "",
        "<h3>What changed</h3><ul>" + "".join(f"<li>{h(line)}</li>" for line in d["note"]) + "</ul>",
    ]
    if d["risers"]:
        parts.append("<h3>Rising in AI/ML ads</h3><ul>" + "".join(
            f"<li>{h(name)}: {share * 100:.1f}% of ads (+{delta * 100:.1f} pp)</li>" for name, share, delta in d["risers"])
            + "</ul>")
    edge = {r: names for r, names in d["edge"].items() if names}
    if edge:
        title = "Top edge skills I don't have yet" if d["first_week"] else "New edge skills for my roles"
        parts.append(f"<h3>{title}</h3><ul>" + "".join(
            f"<li><b>{h(roles.LABELS.get(r, r))}</b>: {h(', '.join(names))}</li>" for r, names in edge.items()) + "</ul>")
    titles = {"near": "New openings in Singapore and Asia", "remote": "New remote openings"}
    for where, jobs in d["openings"].items():
        parts.append(f"<h3>{titles[where]} ({len(jobs)})</h3>")
        if not jobs:
            parts.append("<p>None this week.</p>")
            continue
        rows = []
        for r in jobs:
            meta = " · ".join(x for x in (_place(r), _pay(r)) if x)
            gap = f'<br><span style="color:#888">Also asks: {h(", ".join(r["missing"][:5]))}</span>' if r["missing"] else ""
            rows.append(
                f'<li style="margin-bottom:10px"><a href="{h(r["url"] or "")}">{h(r["title"])}</a> at <b>{h(r["company"])}</b>'
                f'<br><span style="color:#666">{h(meta)}</span>'
                f'<br>Matches: {h(", ".join(r["mine"][:6]))}{gap}</li>')
        parts.append("<ul>" + "".join(rows) + "</ul>")
    parts.append(f'<p style="color:#888;font-size:13px">Full dashboard: <a href="{digest.PAGE_URL}">{digest.PAGE_URL}</a>. '
                 f"Openings are new since last week, in my target roles and places, and name {MIN_STRONG}+ skills I rate 3+.</p></div>")
    return "".join(parts)


def text(d: dict) -> str:
    lines = [f"Skills market, week of {d['date']}", "", "What changed:", *[f"- {x}" for x in d["note"]]]
    if d["risers"]:
        lines += ["", "Rising in AI/ML ads:", *[f"- {n}: {s * 100:.1f}% (+{dl * 100:.1f} pp)" for n, s, dl in d["risers"]]]
    for r, names in d["edge"].items():
        if names:
            lines += ["", f"Edge skills, {roles.LABELS.get(r, r)}: {', '.join(names)}"]
    for where, jobs in d["openings"].items():
        lines += ["", f"New {'openings in Singapore and Asia' if where == 'near' else 'remote openings'} ({len(jobs)}):"]
        lines += [f"- {r['title']} at {r['company']} ({_place(r)}) {r['url'] or ''}" for r in jobs]
    lines += ["", digest.PAGE_URL]
    return "\n".join(lines)


def send(d: dict, api_key: str, to: str, sender: str = DEFAULT_FROM, client: httpx.Client | None = None,
         sleep=time.sleep) -> str:
    """Send through Resend; retries rate limits and server errors with backoff. Returns the email id."""
    payload = {"from": sender, "to": [a.strip() for a in to.split(",") if a.strip()], "subject": subject(d),
               "html": html(d), "text": text(d)}
    # One key per week: a retried run the same day doesn't send twice.
    headers = {"Authorization": f"Bearer {api_key}", "Idempotency-Key": f"skillmap-digest-{d['date']}"}
    own = client is None
    client = client or httpx.Client(timeout=30)
    try:
        for attempt in range(RETRIES + 1):
            try:
                resp = client.post(RESEND_URL, json=payload, headers=headers)
            except httpx.TransportError as exc:
                if attempt == RETRIES:
                    raise
                log.warning("email: %s, retrying", exc)
            else:
                if resp.status_code < 300:
                    return resp.json().get("id", "")
                if resp.status_code != 429 and resp.status_code < 500 or attempt == RETRIES:
                    raise RuntimeError(f"Resend answered {resp.status_code}: {resp.text[:300]}")
                wait = float(resp.headers.get("retry-after") or 0)
                log.warning("email: Resend answered %s, retrying", resp.status_code)
                sleep(max(wait, 2 ** attempt * 5))
                continue
            sleep(2 ** attempt * 5)
    finally:
        if own:
            client.close()
    raise RuntimeError("unreachable")


def run(data_dir: Path, previous_dir: Path, profile: dict, out: Path | None = None) -> dict:
    current = load_json(data_dir / "skills-market.json")
    if current is None:
        raise SystemExit(f"no {data_dir / 'skills-market.json'}")
    previous = load_json(previous_dir / "skills-market.json")
    d = build(current, previous, load_postings(data_dir / "postings-live.jsonl.gz"),
              load_postings(previous_dir / "postings-live.jsonl.gz"), profile)
    if out:
        out.write_text(html(d))
    key, to = os.environ.get("RESEND_API_KEY"), os.environ.get("DIGEST_TO")
    if not key or not to:
        log.warning("email: RESEND_API_KEY or DIGEST_TO not set, not sent")
        d["sent"] = None
        return d
    d["sent"] = send(d, key, to, os.environ.get("DIGEST_FROM") or DEFAULT_FROM)
    return d
