"""Bounded, standard-library adapter for the Central Bank of Russia press RSS."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
import re
import urllib.error
import xml.etree.ElementTree as ET
from xml.parsers import expat

URL = "https://www.cbr.ru/rss/RssPress"
MAX_BYTES = 1024 * 1024
MAX_DEPTH = 32
MAX_ELEMENTS = 10000


@dataclass(frozen=True)
class SourceItem:
    source_id: str
    title: str | None
    canonical_url: str | None
    guid: str | None
    published_at_raw: str | None
    published_at: str | None
    body_text: str | None
    content_hash: str
    provenance: str = "live"
    synthetic: bool = False


@dataclass(frozen=True)
class SourceBatch:
    items: tuple[SourceItem, ...]
    next_cursor: str | None
    fetched_at: str
    status: str
    error: str | None = None


class SourceError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ("script", "style"):
            self.skip += 1
        elif tag.lower() in ("p", "br", "div", "li") and not self.skip:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag.lower() in ("script", "style") and self.skip:
            self.skip -= 1
        elif tag.lower() in ("p", "div", "li") and not self.skip:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _text(value: str | None) -> str | None:
    if value is None:
        return None
    parser = _Text()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        return " ".join(value.split()) or None
    result = " ".join("".join(parser.parts).split())
    return result or None


def _url(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not re.match(r"^https?://", value, re.I):
        return None
    from urllib.parse import urlsplit, urlunsplit
    p = urlsplit(value)
    host = (p.hostname or "").lower().rstrip(".")
    try:
        port = p.port
    except ValueError:
        return None
    if (p.scheme.lower() != "https" or not host or
            (host != "cbr.ru" and not host.endswith(".cbr.ru")) or
            p.username or p.password or p.query or port not in (None, 443)):
        return None
    return urlunsplit(("https", host, p.path or "/", "", ""))


def _check_xml_limits(data: bytes) -> None:
    """Reject dangerous or oversized XML structure while the parser reads it."""
    depth = 0
    count = 0
    parser = expat.ParserCreate()

    def start(_name, _attrs):
        nonlocal depth, count
        depth += 1
        count += 1
        if depth > MAX_DEPTH:
            raise SourceError("xml_too_deep")
        if count > MAX_ELEMENTS:
            raise SourceError("too_many_xml_elements")

    def end(_name):
        nonlocal depth
        depth -= 1

    def reject_declaration(*_args):
        raise SourceError("unsafe_xml_declaration")

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.StartDoctypeDeclHandler = reject_declaration
    parser.EntityDeclHandler = reject_declaration
    parser.ExternalEntityRefHandler = lambda *_args: 0
    try:
        # Feeding bounded chunks makes the structural checks effective before
        # parsing the complete document into ElementTree's retained tree.
        for offset in range(0, len(data), 4096):
            parser.Parse(data[offset:offset + 4096], False)
        parser.Parse(b"", True)
    except SourceError:
        raise
    except expat.ExpatError:
        raise SourceError("malformed_xml") from None


def _date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value.strip())
        if dt is None or dt.tzinfo is None or dt.utcoffset() is None:
            return None
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError):
        return None


def parse(data: bytes, *, synthetic: bool = False) -> tuple[SourceItem, ...]:
    if len(data) > MAX_BYTES:
        raise SourceError("response_too_large")
    _check_xml_limits(data)
    try:
        root = ET.fromstring(data)
    except (ET.ParseError, ValueError):
        raise SourceError("malformed_xml") from None
    stack = [(root, 1)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if depth > MAX_DEPTH:
            raise SourceError("xml_too_deep")
        if count > MAX_ELEMENTS:
            raise SourceError("too_many_xml_elements")
        stack.extend((child, depth + 1) for child in node)
    channel = root if root.tag.rsplit("}", 1)[-1].lower() == "channel" else next((n for n in root.iter() if n.tag.rsplit("}", 1)[-1].lower() == "channel"), None)
    if channel is None or root.tag.rsplit("}", 1)[-1].lower() not in ("rss", "channel"):
        raise SourceError("unsupported_feed_format")
    records = []
    variants: dict[str, set[str]] = {}
    for node in channel:
        if node.tag.rsplit("}", 1)[-1].lower() != "item":
            continue
        vals = {}
        for child in node:
            name = child.tag.rsplit("}", 1)[-1].lower()
            vals.setdefault(name, child.text)
        title = _text(vals.get("title"))
        link = _url(vals.get("link"))
        guid = (vals.get("guid") or "").strip() or None
        raw_date = (vals.get("pubdate") or "").strip() or None
        body = _text(vals.get("encoded") or vals.get("description"))
        payload = "\x1f".join((title or "", link or "", guid or "", raw_date or "", body or ""))
        digest = sha256(payload.encode("utf-8")).hexdigest()
        identity = "url:" + link if link else "guid:" + guid if guid else "hash:" + digest
        variants.setdefault(identity, set()).add(digest)
        records.append((identity, digest, title, link, guid, raw_date, body))
    items = []
    emitted: set[tuple[str, str]] = set()
    for identity, digest, title, link, guid, raw_date, body in records:
        key = (identity, digest)
        if key in emitted:
            continue
        emitted.add(key)
        # Every version of a conflicting identity gets a fingerprinted ID;
        # the result is independent of which version appeared first.
        id_material = identity + ":" + digest if len(variants[identity]) > 1 else identity
        stable_id = sha256(id_material.encode("utf-8")).hexdigest()
        items.append(SourceItem("cbr:" + stable_id, title, link, guid, raw_date, _date(raw_date), body, digest,
                                "synthetic_fixture" if synthetic else "live", synthetic))
    return tuple(items)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SourceAdapter:
    def __init__(self, fetcher=None, timeout: float = 12):
        if not 0 < timeout <= 12:
            raise ValueError("timeout must be within (0, 12]")
        self._fetcher = fetcher
        self.timeout = timeout

    def fetch(self, since: datetime | None = None, cursor: str | None = None) -> SourceBatch:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if cursor is not None:
            return SourceBatch((), None, now, "error", "unsupported_cursor")
        if since is not None and (since.tzinfo is None or since.utcoffset() is None):
            return SourceBatch((), None, now, "error", "invalid_since_timezone")
        try:
            if self._fetcher:
                data = self._fetcher(URL, self.timeout, MAX_BYTES + 1)
            else:
                opener = build_opener(_NoRedirect())
                req = Request(URL, headers={"User-Agent": "AlphaHackathonNewsAdapter/1.0"})
                with opener.open(req, timeout=self.timeout) as response:
                    data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise SourceError("response_too_large")
            items = parse(data)
            if since is not None:
                cutoff = since
                items = tuple(i for i in items if i.published_at is None or datetime.fromisoformat(i.published_at.replace("Z", "+00:00")) >= cutoff.astimezone(timezone.utc))
            return SourceBatch(items, None, now, "ok")
        except SourceError as exc:
            return SourceBatch((), None, now, "error", exc.code)
        except HTTPError as exc:
            return SourceBatch((), None, now, "error", "redirect_rejected" if 300 <= exc.code < 400 else "http_error")
        except (TimeoutError, urllib.error.URLError):
            return SourceBatch((), None, now, "error", "network_error")
        except Exception:
            return SourceBatch((), None, now, "error", "fetch_error")
