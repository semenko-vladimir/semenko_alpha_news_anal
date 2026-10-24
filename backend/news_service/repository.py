"""SQLite persistence for normalized CBR RSS events.

RSS values are untrusted data. Real body text is reduced to a short snippet for
local UI use; publication time is never treated as event date.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Iterable

# Keep one source-model identity regardless of discovery/import order. Both
# backend.news_service and the root-launched --app-dir backend entrypoint use
# this namespace; availability of a top-level `sources` package is irrelevant.
from backend.sources.cbr_rss import SourceAdapter, SourceItem

MAX_ITEMS = 500
MAX_TITLE_CHARS = 1000
MAX_TEXT_CHARS = 16_000
MAX_TOTAL_CHARS = 48_000
MAX_SNIPPET_CHARS = 500
MAX_LIMIT = 100


class NewsServiceError(ValueError):
    """Invalid or unsafe news input / repository configuration."""


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    title: str | None
    text: str | None
    source_refs: list[dict]
    published_at: str | None
    event_date: str | None
    synthetic: bool


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _snippet(text: str | None) -> str | None:
    if text is None:
        return None
    compact = " ".join(text.split())
    if not compact:
        return None
    if len(compact) > MAX_SNIPPET_CHARS:
        compact = compact[:MAX_SNIPPET_CHARS - 1].rstrip() + "…"
    return compact


class NewsRepository:
    """Persistent local repository; no network or filesystem work at import."""

    def __init__(self, database_path: str | Path, *, adapter: SourceAdapter | None = None):
        self.database_path = Path(database_path)
        self.adapter = adapter or SourceAdapter()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS news_events (
                event_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                title TEXT,
                text TEXT,
                canonical_url TEXT,
                published_at TEXT,
                published_at_raw TEXT,
                retrieved_at TEXT NOT NULL,
                guid TEXT,
                content_hash TEXT NOT NULL,
                provenance TEXT NOT NULL,
                synthetic INTEGER NOT NULL,
                inserted_at TEXT NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS news_events_published ON news_events(published_at DESC, event_id)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(str(self.database_path), timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def ingest(self, items: Iterable[SourceItem], *, retrieved_at: str | None = None) -> dict:
        """Validate and store normalized RSS items, retaining distinct versions.

        Exact duplicates are idempotent. Conflicting source versions remain
        separate because upstream source IDs are version fingerprinted.
        """
        if isinstance(items, (str, bytes)):
            raise NewsServiceError("invalid_batch")
        batch: list[SourceItem] = []
        total = 0
        try:
            for item in items:
                if len(batch) >= MAX_ITEMS:
                    raise NewsServiceError("item_limit")
                if not isinstance(item, SourceItem):
                    raise NewsServiceError("malformed_item")
                for value, maximum in ((item.source_id, 512), (item.title, MAX_TITLE_CHARS),
                                       (item.body_text, MAX_TEXT_CHARS), (item.canonical_url, 2048),
                                       (item.guid, 2048), (item.published_at_raw, 256),
                                       (item.published_at, 64), (item.content_hash, 128),
                                       (item.provenance, 64)):
                    if value is not None and (not isinstance(value, str) or len(value) > maximum):
                        raise NewsServiceError("field_limit_or_type")
                if not item.source_id or not item.content_hash or not isinstance(item.synthetic, bool):
                    raise NewsServiceError("invalid_item")
                if not re.fullmatch(r"cbr:[0-9a-f]{64}", item.source_id):
                    raise NewsServiceError("invalid_source_id")
                expected_provenance = "synthetic_fixture" if item.synthetic else "live"
                if item.provenance != expected_provenance:
                    raise NewsServiceError("invalid_provenance")
                if item.canonical_url is not None and not item.canonical_url.startswith("https://www.cbr.ru/") and not item.canonical_url.startswith("https://cbr.ru/"):
                    raise NewsServiceError("invalid_source_url")
                if item.published_at is not None:
                    try:
                        publication = datetime.fromisoformat(item.published_at.replace("Z", "+00:00"))
                        if publication.tzinfo is None or publication.utcoffset() != timezone.utc.utcoffset(publication):
                            raise ValueError()
                    except (ValueError, TypeError, OverflowError):
                        raise NewsServiceError("invalid_published_at") from None
                total += sum(len(v) for v in (item.source_id, item.title, item.body_text,
                    item.canonical_url, item.guid, item.published_at_raw, item.published_at) if v)
                if total > MAX_TOTAL_CHARS:
                    raise NewsServiceError("batch_char_limit")
                batch.append(item)
        except TypeError:
            raise NewsServiceError("invalid_batch") from None

        fetched = retrieved_at or _utc_now()
        if not isinstance(fetched, str) or len(fetched) > 64:
            raise NewsServiceError("invalid_retrieved_at")
        try:
            dt = datetime.fromisoformat(fetched.replace("Z", "+00:00"))
            if dt.tzinfo is None or dt.utcoffset() is None:
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            raise NewsServiceError("invalid_retrieved_at") from None
        fetched = dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

        inserted = 0
        with self._connect() as db:
            for item in batch:
                identity = json.dumps([item.source_id, item.content_hash, item.title,
                    item.body_text, item.canonical_url, item.published_at_raw], ensure_ascii=False, separators=(",", ":"))
                event_id = "news:" + sha256(identity.encode("utf-8")).hexdigest()
                result = db.execute("""INSERT OR IGNORE INTO news_events
                    (event_id, source_id, title, text, canonical_url, published_at,
                     published_at_raw, retrieved_at, guid, content_hash, provenance,
                     synthetic, inserted_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (event_id, item.source_id, item.title, _snippet(item.body_text),
                     item.canonical_url, item.published_at, item.published_at_raw,
                     fetched, item.guid, item.content_hash, item.provenance,
                     int(item.synthetic), _utc_now()))
                inserted += result.rowcount
        return {"inserted": inserted, "duplicates": len(batch) - inserted,
                "received": len(batch), "retrieved_at": fetched}

    def latest(self, limit: int = 10) -> list[EventRecord]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
            raise NewsServiceError("invalid_limit")
        with self._connect() as db:
            rows = db.execute("""SELECT * FROM news_events
                ORDER BY CASE WHEN published_at IS NULL THEN 1 ELSE 0 END,
                         published_at DESC, event_id LIMIT ?""", (limit,)).fetchall()
        return [EventRecord(
            event_id=row["event_id"], title=row["title"], text=row["text"],
            source_refs=[{
                "source_id": row["source_id"], "uri": row["canonical_url"],
                "published_at_raw": row["published_at_raw"],
                "retrieved_at": row["retrieved_at"], "guid": row["guid"],
                "content_hash": row["content_hash"], "provenance": row["provenance"],
                "synthetic": bool(row["synthetic"]),
            }],
            published_at=row["published_at"], event_date=None,
            synthetic=bool(row["synthetic"]),
        ) for row in rows]


class NewsService:
    """One explicit source fetch per call; callers control whether to fetch."""

    def __init__(self, repository: NewsRepository):
        self.repository = repository

    def fetch_and_ingest(self) -> dict:
        batch = self.repository.adapter.fetch()
        if batch.status != "ok":
            return {"status": "blocked", "error": batch.error, "retrieved_at": batch.fetched_at,
                    "received": 0, "inserted": 0}
        result = self.repository.ingest(batch.items, retrieved_at=batch.fetched_at)
        return {"status": "ok", "error": None, **result}
