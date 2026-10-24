"""Bounded, deterministic, offline normalization of source items.

The bridge treats all source fields as untrusted data. It does not fetch, infer
an event date, assert company exposure, or infer that a source is the bank.
"""
from __future__ import annotations

from dataclasses import dataclass, replace as dataclass_replace
from hashlib import sha256
import json
from typing import Iterable

from analysis.pipeline import Evidence, MAX_SOURCE_CHARS
from sources.cbr_rss import SourceItem

MAX_ITEMS = 500
MAX_FIELD_CHARS = 16_000
MAX_TOTAL_CHARS = 48_000
MAX_FIELD_BYTES = 48_000


@dataclass(frozen=True)
class BridgeError:
    code: str
    index: int | None = None
    source_id: str | None = None
    field: str | None = None


@dataclass(frozen=True)
class EventSourceRecord:
    event_id: str
    source_id: str | None
    item_id: str | None
    title: str | None
    text: str | None
    link: str | None
    published_at_raw: str | None
    published_at_utc: str | None
    event_date: None
    text_fingerprint: str | None
    upstream_content_hash: str | None
    provenance: str | None
    synthetic: bool | None
    unknown_fields: tuple[str, ...]
    evidence: Evidence | None


@dataclass(frozen=True)
class BridgeResult:
    records: tuple[EventSourceRecord, ...]
    errors: tuple[BridgeError, ...]


def _valid_text(value: object, field: str, source_id: str | None) -> BridgeError | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return BridgeError("invalid_field_type", source_id=source_id, field=field)
    if len(value) > MAX_FIELD_CHARS:
        return BridgeError("field_char_limit", source_id=source_id, field=field)
    try:
        if len(value.encode("utf-8")) > MAX_FIELD_BYTES:
            return BridgeError("field_byte_limit", source_id=source_id, field=field)
    except UnicodeEncodeError:
        return BridgeError("invalid_unicode", source_id=source_id, field=field)
    return None


def _normalize_one(item: object, index: int) -> tuple[EventSourceRecord | None, tuple[BridgeError, ...]]:
    if not isinstance(item, SourceItem):
        return None, (BridgeError("malformed_item", index=index),)
    sid = item.source_id if isinstance(item.source_id, str) else None
    if not sid or len(sid) > 512:
        return None, (BridgeError("invalid_source_id", index=index, source_id=sid),)
    source_id_error = _valid_text(sid, "source_id", sid)
    if source_id_error:
        return None, (BridgeError(source_id_error.code, index=index, source_id=sid, field="source_id"),)
    fields = {
        "title": item.title,
        "link": item.canonical_url,
        "item_id": item.guid,
        "published_at_raw": item.published_at_raw,
        "published_at_utc": item.published_at,
        "text": item.body_text,
        "content_hash": item.content_hash,
        "provenance": item.provenance,
    }
    for name, value in fields.items():
        error = _valid_text(value, name, sid)
        if error:
            return None, (BridgeError(error.code, index=index, source_id=sid, field=name),)
    if not isinstance(item.synthetic, bool):
        return None, (BridgeError("invalid_field_type", index=index, source_id=sid, field="synthetic"),)
    if item.published_at is not None and not _valid_utc(item.published_at):
        # Preserve the raw source value, but never pass an invalid timestamp as publication.
        published = None
        date_error = BridgeError("invalid_publication_timestamp", index=index, source_id=sid,
                                 field="published_at")
    else:
        published, date_error = item.published_at, None

    text = item.body_text
    title = item.title
    payload = "\x1f".join((title or "", text or "")).encode("utf-8", errors="replace")
    fp = "sha256:" + sha256(payload).hexdigest()
    identity_payload = json.dumps(
        [sid, item.guid, title, text, item.canonical_url, item.published_at_raw,
         published, item.content_hash, item.provenance, item.synthetic],
        ensure_ascii=False, separators=(",", ":"),
    )
    event_id = "source-event:" + sha256(identity_payload.encode("utf-8")).hexdigest()
    unknown = tuple(name for name, value in (
        ("link", item.canonical_url), ("published_at", published),
        ("body_text", text), ("title", title), ("item_id", item.guid),
    ) if value is None)
    evidence = None
    # A title alone is not promoted into body evidence; empty body stays unknown.
    if text and text.strip() and len(text) <= MAX_SOURCE_CHARS:
        evidence = Evidence(
            # event_id includes the normalized metadata variant, so evidence
            # with identical text but conflicting source metadata stays unique.
            evidence_id="evidence:" + sha256(event_id.encode("utf-8")).hexdigest(),
            source_id=sid,
            kind="news",
            text=text,
            published_at=published,
            confirmed_exposure=False,
        )
    record = EventSourceRecord(
        event_id=event_id, source_id=sid, item_id=item.guid, title=title,
        text=text, link=item.canonical_url, published_at_raw=item.published_at_raw,
        published_at_utc=published, event_date=None, text_fingerprint=fp,
        upstream_content_hash=item.content_hash, provenance=item.provenance,
        synthetic=item.synthetic, unknown_fields=unknown, evidence=evidence,
    )
    errors = [error for error in (date_error,) if error is not None]
    if text and len(text) > MAX_SOURCE_CHARS:
        errors.append(BridgeError("evidence_text_limit", index=index, source_id=sid, field="text"))
    return record, tuple(errors)


def _valid_utc(value: str) -> bool:
    from datetime import datetime, timezone
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed.utcoffset() is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)
    except (TypeError, ValueError, OverflowError):
        return False


def normalize(items: Iterable[SourceItem]) -> BridgeResult:
    """Normalize a bounded batch; identical records dedupe, conflicts remain.

    Ordering is deterministic by source ID, payload fingerprint and normalized
    event ID; original position is only a final tie-breaker. Oversized input is
    rejected without consuming or retaining an unbounded iterator.
    """
    if isinstance(items, (str, bytes)):
        return BridgeResult((), (BridgeError("invalid_batch"),))
    collected: list[object] = []
    try:
        for index, item in enumerate(items):
            if index >= MAX_ITEMS:
                return BridgeResult((), (BridgeError("item_limit"),))
            collected.append(item)
    except TypeError:
        return BridgeResult((), (BridgeError("invalid_batch"),))

    records: list[tuple[int, EventSourceRecord]] = []
    errors: list[BridgeError] = []
    total = 0
    for index, item in enumerate(collected):
        record, item_errors = _normalize_one(item, index)
        errors.extend(item_errors)
        if record is None:
            continue
        total += sum(len(value) for value in (record.title, record.text, record.link,
                      record.published_at_raw, record.published_at_utc, record.item_id,
                      record.provenance) if value is not None)
        if total > MAX_TOTAL_CHARS:
            errors.append(BridgeError("batch_char_limit", index=index, source_id=record.source_id))
            return BridgeResult((), tuple(errors))
        records.append((index, record))

    # Drop byte-identical normalized payloads, while retaining any conflicting
    # metadata or text under a reused source ID as separate records.
    unique: dict[tuple[str, str], tuple[int, EventSourceRecord]] = {}
    for index, record in records:
        unique.setdefault((record.source_id or "", record.event_id), (index, record))
    # Pipeline rejects differing (kind, publication date) for one source ID.
    # Keep all raw records, but withhold incompatible evidence as a group.
    metadata_by_source: dict[str, set[tuple[str, str | None]]] = {}
    for _, record in unique.values():
        if record.evidence is not None:
            metadata_by_source.setdefault(record.evidence.source_id, set()).add(
                (record.evidence.kind, record.evidence.published_at))
    conflicts = {source_id for source_id, metadata in metadata_by_source.items() if len(metadata) > 1}
    if conflicts:
        rewritten = {}
        for key, (index, record) in unique.items():
            if record.source_id in conflicts and record.evidence is not None:
                errors.append(BridgeError("evidence_metadata_conflict", index=index,
                                          source_id=record.source_id, field="published_at"))
                record = dataclass_replace(record, evidence=None)
            rewritten[key] = (index, record)
        unique = rewritten
    ordered = sorted(unique.values(), key=lambda pair: (
        pair[1].source_id or "", pair[1].text_fingerprint or "",
        pair[1].event_id, pair[0]))
    return BridgeResult(tuple(record for _, record in ordered), tuple(errors))
