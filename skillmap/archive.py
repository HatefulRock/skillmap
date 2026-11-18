"""Full posting text, kept out of the database.

The free Supabase database holds 500 MB, which ~1,000 companies' ads would fill in months; the
extracted fields alone are about 1 KB a posting and fit for years. So after each run the new
postings' text is packed into one gzip'd JSON-lines file (job ads share a lot of boilerplate, so
a batch compresses ~5×) and put in a private Supabase Storage bucket, a separate free 1 GB. The
database keeps only which file a posting's text is in, and re-extraction reads the files back
one at a time.

Without storage credentials the text simply stays in the database, as before.
"""

from __future__ import annotations

import gzip
import json
import os
import re
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import httpx

BUCKET = "skillmap-text"


class Archive(Protocol):
    def put(self, name: str, data: bytes) -> None: ...
    def get(self, name: str) -> bytes: ...


def pack(items: list[tuple[str, str]]) -> bytes:
    lines = "\n".join(json.dumps({"id": pid, "text": text}, ensure_ascii=False) for pid, text in items)
    return gzip.compress(lines.encode(), compresslevel=9)


def unpack(data: bytes) -> dict[str, str]:
    out = {}
    # Split on "\n" only: splitlines() also breaks on U+2028, U+0085 and form feeds, which
    # json.dumps(ensure_ascii=False) leaves unescaped inside posting text.
    for line in gzip.decompress(data).decode().split("\n"):
        if line:
            item = json.loads(line)
            out[item["id"]] = item["text"]
    return out


class LocalArchive:
    """A directory; for tests and local runs."""

    def __init__(self, root: Path):
        self.root = root

    def put(self, name: str, data: bytes) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as fh:  # never overwrite, like the Supabase bucket
            fh.write(data)

    def get(self, name: str) -> bytes:
        return (self.root / name).read_bytes()


class SupabaseArchive:
    """A private bucket in Supabase Storage, through its REST API with the project's secret key."""

    def __init__(self, url: str, key: str, bucket: str = BUCKET, transport: httpx.BaseTransport | None = None):
        # New-style keys (sb_secret_…) go in `apikey` only; legacy service_role JWTs also as a bearer token.
        headers = {"apikey": key} if key.startswith("sb_") else {"apikey": key, "Authorization": f"Bearer {key}"}
        self.base = f"{url.rstrip('/')}/storage/v1"
        self.bucket = bucket
        self.client = httpx.Client(headers=headers, timeout=120, transport=transport)
        self._bucket_ready = False

    def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        response = self.client.post(f"{self.base}/bucket", json={"id": self.bucket, "name": self.bucket, "public": False})
        # Already there answers 409, or 400 with "already exists" on some versions.
        if response.status_code not in (200, 201, 409) and "exist" not in response.text.lower():
            response.raise_for_status()
        self._bucket_ready = True

    def put(self, name: str, data: bytes) -> None:
        self._ensure_bucket()
        response = self.client.post(f"{self.base}/object/{self.bucket}/{name}", content=data,
                                    headers={"Content-Type": "application/gzip", "x-upsert": "false"})  # never overwrite
        response.raise_for_status()

    def get(self, name: str) -> bytes:
        response = self.client.get(f"{self.base}/object/{self.bucket}/{name}")
        response.raise_for_status()
        return response.content


def project_url(database_url: str | None) -> str | None:
    """https://<ref>.supabase.co from a Supabase connection string (pooler user is postgres.<ref>,
    direct host is db.<ref>.supabase.co), so only the secret key needs its own secret."""
    if not database_url:
        return None
    parsed = urlparse(database_url)
    match = re.fullmatch(r"postgres\.([a-z0-9]+)", parsed.username or "") or re.fullmatch(
        r"db\.([a-z0-9]+)\.supabase\.co", parsed.hostname or "")
    return f"https://{match.group(1)}.supabase.co" if match else None


def from_env() -> Archive | None:
    key = os.environ.get("SUPABASE_SECRET_KEY")
    url = os.environ.get("SUPABASE_URL") or project_url(os.environ.get("DATABASE_URL"))
    return SupabaseArchive(url, key) if key and url else None
