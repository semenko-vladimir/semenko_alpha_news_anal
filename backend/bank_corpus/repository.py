"""SQLite FTS5 retrieval. Document metadata is operator supplied, never inferred."""

import hashlib
import json
import re
import sqlite3
import tempfile
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import TypedDict

MAX_QUERY = 256
MAX_SNIPPET = 600
SCHEMA = "bank-corpus-v1"


class CorpusError(ValueError):
    pass


class BankPassage(TypedDict):
    source_id: str
    document_id: str
    title: str
    document_date: str
    page: int
    locator: str
    text: str
    historical: bool
    quality: str
    source_kind: str
    synthetic: bool
    rank: float
    corpus_version: str


def _exact_date(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise CorpusError("document_date must be an exact ISO date or null")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise CorpusError("invalid document_date") from exc


def _local_file(root, name, suffix):
    if not isinstance(name, str) or Path(name).name != name or not name.endswith(suffix):
        raise CorpusError("manifest filenames must be local basenames")
    path = (root / name).resolve()
    if path.parent != root.resolve() or not path.is_file():
        raise CorpusError("required local input is unavailable")
    return path


def build_index(index_path, *, manifest_path, ocr_dir, pdf_dir):
    """Build atomically from an explicit JSON manifest and operator-selected dirs.

    Real entries require a PDF and verified date metadata (or explicit null).
    No date extraction, number correction, network access or model call occurs.
    """
    target = Path(index_path).resolve()
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("documents"), list):
        raise CorpusError("manifest requires documents list")
    rows, fingerprints, ids = [], [], set()
    for doc in manifest["documents"]:
        doc_id = doc.get("document_id")
        if not isinstance(doc_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", doc_id) or doc_id in ids:
            raise CorpusError("invalid or duplicate document_id")
        ids.add(doc_id)
        doc_date = _exact_date(doc.get("document_date"))
        for field in ("title", "quality"):
            if not isinstance(doc.get(field), str) or not doc[field].strip() or len(doc[field]) > 500:
                raise CorpusError("title and quality must be bounded nonempty strings")
        if type(doc.get("historical")) is not bool or type(doc.get("synthetic")) is not bool:
            raise CorpusError("historical and synthetic must be explicit booleans")
        # A known old document cannot be promoted to a present position.
        historical = doc["historical"] or (doc_date is not None and doc_date < "2026-01-01")
        if not doc["synthetic"] and doc_date and doc.get("date_basis") != "verified_pdf":
            raise CorpusError("real document date requires verified_pdf basis")
        ocr_path = _local_file(Path(ocr_dir), doc.get("ocr_file"), ".md")
        raw = ocr_path.read_bytes()
        fingerprints.append(hashlib.sha256(raw).hexdigest())
        if not doc["synthetic"]:
            pdf_path = _local_file(Path(pdf_dir), doc.get("pdf_file"), ".pdf")
            fingerprints.append(hashlib.sha256(pdf_path.read_bytes()).hexdigest())
        text = raw.decode("utf-8-sig")
        markers = list(re.finditer(r"^## Страница ([1-9]\d*)\s*$", text, re.MULTILINE))
        page_count = doc.get("pages")
        if type(page_count) is not int or page_count < 1 or [int(m[1]) for m in markers] != list(range(1, page_count + 1)):
            raise CorpusError("OCR page markers do not match manifest")
        for i, marker in enumerate(markers):
            page = i + 1
            body = text[marker.end():markers[i + 1].start() if i + 1 < len(markers) else len(text)].strip()
            if not body:
                raise CorpusError("empty OCR page")
            rows.append((doc_id, doc["title"], doc_date, page, f"{doc_id}:pdf-page:{page}",
                         int(historical), doc["quality"], int(doc["synthetic"]), body))
    if not rows:
        raise CorpusError("empty corpus")
    version = hashlib.sha256(json.dumps([SCHEMA, manifest, fingerprints], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    # Never overwrite an input, even if an operator accidentally supplies its path.
    protected = [Path(manifest_path).resolve()]
    protected.extend(Path(ocr_dir).resolve().glob("*.md"))
    protected.extend(Path(pdf_dir).resolve().glob("*.pdf"))
    if target in protected:
        raise CorpusError("index cannot overwrite an input")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix="bank-", suffix=".sqlite3", delete=False) as temp:
        temporary = Path(temp.name)
    try:
        with closing(sqlite3.connect(temporary)) as db, db:
            db.execute("CREATE TABLE metadata (schema TEXT, corpus_version TEXT)")
            db.execute("INSERT INTO metadata VALUES (?, ?)", (SCHEMA, version))
            db.execute("CREATE TABLE passages (id INTEGER PRIMARY KEY, document_id TEXT, title TEXT, document_date TEXT, page INTEGER, locator TEXT UNIQUE, historical INTEGER, quality TEXT, synthetic INTEGER, text TEXT)")
            db.executemany("INSERT INTO passages(document_id,title,document_date,page,locator,historical,quality,synthetic,text) VALUES (?,?,?,?,?,?,?,?,?)", rows)
            db.execute("CREATE VIRTUAL TABLE passage_fts USING fts5(title, text, content='passages', content_rowid='id', tokenize='unicode61')")
            db.execute("INSERT INTO passage_fts(passage_fts) VALUES ('rebuild')")
        temporary.replace(target)
    except sqlite3.Error as exc:
        raise CorpusError("SQLite FTS5 index creation failed") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return {"documents": len(ids), "pages": len(rows), "undated_pages": sum(r[2] is None for r in rows), "corpus_version": version}


class BankRepository:
    """Read-only repository. Missing index stays not_checked; never created lazily."""

    def __init__(self, index_path):
        self.index_path = Path(index_path).resolve()

    def search(self, query: str, limit: int = 3) -> list[BankPassage]:
        return self.search_with_status(query, limit)["passages"]

    def search_with_status(self, query: str, limit: int = 3):
        if not isinstance(query, str) or len(query) > MAX_QUERY:
            raise CorpusError("query must be a string of at most 256 characters")
        if type(limit) is not int or not 1 <= limit <= 10:
            raise CorpusError("limit must be an integer from 1 to 10")
        tokens = re.findall(r"[^\W_]+", query, re.UNICODE)
        if len(tokens) > 12:
            raise CorpusError("query has more than 12 terms")
        if not self.index_path.is_file():
            return {"status": "not_checked", "passages": []}
        # All input is literal tokens, not executable FTS syntax. Prefix matching
        # helps Russian inflections when the caller supplies a word stem.
        expression = " OR ".join('"' + token + '"*' for token in tokens)
        try:
            with closing(sqlite3.connect(self.index_path.as_uri() + "?mode=ro", uri=True)) as db:
                db.row_factory = sqlite3.Row
                meta = db.execute("SELECT schema,corpus_version FROM metadata").fetchone()
                if meta is None or meta["schema"] != SCHEMA:
                    raise CorpusError("unsupported bank corpus schema")
                if not expression:
                    return {"status": "not_found", "passages": []}
                hits = db.execute("""SELECT p.*, bm25(passage_fts,2.0,1.0) AS rank,
                    snippet(passage_fts,1,'','',' … ',48) AS excerpt
                    FROM passage_fts JOIN passages p ON p.id=passage_fts.rowid
                    WHERE passage_fts MATCH ? AND p.document_date IS NOT NULL
                    ORDER BY rank, p.document_date DESC, p.document_id, p.page LIMIT ?""", (expression, limit)).fetchall()
                passages = [BankPassage(
                    source_id=f"{'synthetic' if row['synthetic'] else 'bank'}:{row['document_id']}:{meta['corpus_version'][:16]}",
                    document_id=row["document_id"], title=row["title"],
                    document_date=row["document_date"], page=row["page"], locator=row["locator"],
                    text=row["excerpt"][:MAX_SNIPPET], historical=bool(row["historical"]),
                    quality=row["quality"], synthetic=bool(row["synthetic"]),
                    source_kind="synthetic" if row["synthetic"] else "bank",
                    rank=row["rank"], corpus_version=meta["corpus_version"],
                ) for row in hits]
                status = "not_found" if not passages else ("historical" if all(p["historical"] for p in passages) else "found")
                return {"status": status, "passages": passages}
        except sqlite3.Error as exc:
            raise CorpusError("bank corpus is invalid or unreadable") from exc
