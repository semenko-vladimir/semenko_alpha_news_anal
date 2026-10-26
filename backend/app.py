"""Local API; real-data modes require explicit operator configuration."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
# Support both root `backend.app:app` and uvicorn's `--app-dir backend`.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from application.fixture_engine import EngineResult, FixtureAnalysisEngine

API = "/api/v1"
COMPANIES = {
    "demo-trade-01": {"display_name": "Демо · торговля", "category": "TRADE", "profile_version": "demo-v1"},
    "demo-transport-01": {"display_name": "Демо · транспорт", "category": "TRANSPORT", "profile_version": "demo-v1"},
}
ROLE_VERSIONS = {"CFO": "role-demo-v1", "COO": "role-demo-v1"}
PRINCIPAL = "local-demo-principal"
CORPUS_REVISION = "demo-corpus-v1"


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False):
        self.status, self.code, self.message, self.retryable = status, code, message, retryable


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role_id: str
    idempotency_key: str = Field(min_length=1, max_length=200)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connection() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS companies (
                    company_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                    category TEXT NOT NULL, profile_version TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    snapshot_id TEXT PRIMARY KEY, company_id TEXT NOT NULL,
                    role_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analysis_runs (
                    run_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL,
                    company_id TEXT NOT NULL, role_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL, input_version TEXT NOT NULL,
                    input_snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
                    status TEXT NOT NULL, stage TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    result_ids_json TEXT NOT NULL, error_code TEXT,
                    execution_mode TEXT NOT NULL,
                    trace_json TEXT NOT NULL DEFAULT '[]',
                    UNIQUE(principal_id, company_id, role_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL, locator TEXT NOT NULL,
                    synthetic INTEGER NOT NULL CHECK(synthetic = 1),
                    trust_status TEXT NOT NULL CHECK(trust_status = 'synthetic')
                );
                CREATE TABLE IF NOT EXISTS insights (
                    insight_id TEXT PRIMARY KEY, company_id TEXT NOT NULL,
                    role_id TEXT NOT NULL, event_id TEXT NOT NULL,
                    input_version TEXT NOT NULL, headline TEXT NOT NULL,
                    summary TEXT NOT NULL, created_at TEXT NOT NULL,
                    bank_position_status TEXT NOT NULL,
                    exposure_status TEXT NOT NULL,
                    blocks_json TEXT NOT NULL, source_ids_json TEXT NOT NULL,
                    unknowns_json TEXT NOT NULL,
                    UNIQUE(company_id, role_id, event_id, input_version)
                );
                CREATE TABLE IF NOT EXISTS analysis_run_insights (
                    run_id TEXT NOT NULL REFERENCES analysis_runs(run_id),
                    insight_id TEXT NOT NULL REFERENCES insights(insight_id),
                    PRIMARY KEY(run_id, insight_id)
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS source_search USING fts5(source_id, title, content);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(analysis_runs)")}
            if "execution_mode" not in columns:
                db.execute("ALTER TABLE analysis_runs ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'deterministic_demo'")
            if "trace_json" not in columns:
                db.execute("ALTER TABLE analysis_runs ADD COLUMN trace_json TEXT NOT NULL DEFAULT '[]'")
            db.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(1, ?)", (utc_now(),))
            for company_id, company in COMPANIES.items():
                db.execute("INSERT OR IGNORE INTO companies VALUES(?,?,?,?)", (company_id, company["display_name"], company["category"], company["profile_version"]))


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


_ENGINE_ERROR_CODES = {"analysis_failed", "budget_exceeded", "authentication_failure", "timeout",
                      "unknown_outcome", "tool_failure", "tool_result_invalid", "model_failure",
                      "input_invalid", "input_size_limit", "schema_version_invalid", "source_invalid",
                      "output_invalid", "provenance_unresolved", "input_limit", "engine_result_invalid"}
_TRACE_STAGES = {"plan", "tool", "model", "validate", "adapt", "corpus", "bridge", "mapping"}
_TRACE_STATUSES = {"started", "submitted", "completed", "passed", "rejected", "one_correction_available",
                   "budget_exceeded", "authentication_failure", "timeout", "unknown_outcome", "failed"}
_TRACE_REASONS = {"claim_missing", "kind_invalid", "exposure_status_invalid", "evidence_refs_missing",
                  "evidence_refs_invalid", "assumptions_invalid", "unknowns_invalid", "schema_invalid",
                  "provenance_invalid", "unresolved_evidence"}


def _validate_engine_snapshot(snapshot: Any, company_id: str, role_id: str) -> bool:
    if not isinstance(snapshot, dict) or set(snapshot) != {"engine_version", "corpus_revision", "role_id", "event_ids", "source_fingerprints"}:
        return False
    if any(not isinstance(snapshot.get(k), str) or not snapshot[k] or len(snapshot[k]) > 100 for k in ("engine_version", "corpus_revision")) or snapshot["role_id"] != role_id:
        return False
    ids = snapshot["event_ids"]
    fingerprints = snapshot["source_fingerprints"]
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 8
            or any(not isinstance(x, str) or len(x) > 120 or not x.startswith(f"fixture:event:{company_id}") for x in ids)
            or not isinstance(fingerprints, dict) or not 1 <= len(fingerprints) <= 16):
        return False
    return all(isinstance(k, str) and k.startswith("fixture:") and len(k) <= 160
               and isinstance(v, str) and len(v) <= 100 for k, v in fingerprints.items())


def _safe_trace_item(item: Any, source_ids: set[str]) -> dict[str, Any]:
    if not isinstance(item, dict) or set(item) - {"stage", "status", "attempt", "source_ids", "reasons"}:
        raise ValueError("invalid_trace")
    stage, status = item.get("stage"), item.get("status")
    if stage not in _TRACE_STAGES or (status is not None and status not in _TRACE_STATUSES):
        raise ValueError("invalid_trace")
    safe = {"stage": stage}
    if status is not None:
        safe["status"] = status
    if "attempt" in item:
        if type(item["attempt"]) is not int or not 1 <= item["attempt"] <= 3:
            raise ValueError("invalid_trace")
        safe["attempt"] = item["attempt"]
    if "source_ids" in item:
        values = item["source_ids"]
        if (not isinstance(values, (list, tuple)) or len(values) > 16
                or any(not isinstance(x, str) or x not in source_ids for x in values)):
            raise ValueError("invalid_trace")
        safe["source_ids"] = list(values)
    if "reasons" in item:
        values = item["reasons"]
        if not isinstance(values, (list, tuple)) or len(values) > 16 or any(x not in _TRACE_REASONS for x in values):
            raise ValueError("invalid_trace")
        safe["reasons"] = list(values)
    return safe


def _safe_engine_error(code: Any) -> str:
    return code if isinstance(code, str) and code in _ENGINE_ERROR_CODES else "engine_metadata_invalid"


def _validate_engine_cards(cards: Any, snapshot_source_ids: set[str]) -> None:
    if not isinstance(cards, (tuple, list)) or len(cards) > 2:
        raise ValueError("invalid_cards")
    for card in cards:
        if not isinstance(card, dict) or not all(isinstance(card.get(k), str) and card[k] for k in ("event_id", "headline", "summary")):
            raise ValueError("invalid_card")
        if card.get("bank_position_status") != "not_checked" or card.get("exposure_status") not in {"unknown", "conditional"}:
            raise ValueError("invalid_card")
        sources = card.get("sources")
        blocks = card.get("blocks")
        if not isinstance(sources, list) or not sources or not isinstance(blocks, list) or not blocks:
            raise ValueError("invalid_card")
        source_ids = set()
        for source in sources:
            if not isinstance(source, dict) or not isinstance(source.get("source_id"), str) or not source["source_id"].startswith("fixture:"):
                raise ValueError("invalid_source")
            if source["source_id"] not in snapshot_source_ids:
                raise ValueError("invalid_provenance")
            if not isinstance(source.get("title"), str) or not source["title"]:
                raise ValueError("invalid_source")
            if source.get("kind") is not None and (not isinstance(source["kind"], str) or not source["kind"]):
                raise ValueError("invalid_source")
            if source.get("locator") is not None and not isinstance(source["locator"], str):
                raise ValueError("invalid_source")
            if source.get("synthetic") is not True or source.get("trust_status") != "synthetic" or source.get("uri") is not None:
                raise ValueError("invalid_source")
            source_ids.add(source["source_id"])
        for block in blocks:
            if (not isinstance(block, dict) or block.get("kind") not in {"observed_fact", "ai_inference"}
                    or not isinstance(block.get("text"), str) or not block["text"]):
                raise ValueError("invalid_block")
            refs = block.get("evidence_refs")
            if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) for ref in refs) or not set(refs).issubset(source_ids):
                raise ValueError("invalid_provenance")
            for field in ("block_id",):
                if field in block and (not isinstance(block[field], str) or not block[field]):
                    raise ValueError("invalid_block")
            for field in ("assumptions", "unknowns"):
                if field in block and (not isinstance(block[field], list) or not all(isinstance(x, str) for x in block[field])):
                    raise ValueError("invalid_block")
        if not isinstance(card.get("unknowns"), list) or not all(isinstance(x, str) for x in card["unknowns"]):
            raise ValueError("invalid_unknowns")


def fixture(company_id: str, role_id: str) -> dict[str, Any]:
    transport = company_id == "demo-transport-01"
    company_label = "транспортная компания" if transport else "торговая компания"
    event_id = "fixture:synthetic-demand-shift"
    source_id = "fixture:demo-demand-shift"
    if role_id == "CFO":
        headline = "Сценарий денежного потока требует проверки" if not transport else "Сценарий затрат на маршруты требует проверки"
        summary = "Синтетическое изменение спроса может повлиять на денежный поток; фактические объёмы и ставки неизвестны."
        inference = "При наличии экспозиции возможна чувствительность денежного потока; масштаб эффекта не рассчитан."
        unknowns = ["Фактическая экспозиция не проверена", "Объёмы, валюта и период не заданы", "Позиция банка не проверялась"]
    else:
        headline = "Операционная устойчивость зависит от экспозиции" if not transport else "Устойчивость маршрутов требует фактических данных"
        summary = "Синтетическое событие может создать операционный сценарий; использование затронутых маршрутов неизвестно."
        inference = "Если компания зависит от соответствующих поставок или маршрутов, возможна операционная задержка."
        unknowns = ["Реальные маршруты и поставщики неизвестны", "Запасы и резервные мощности неизвестны", "Позиция банка не проверялась"]
    return {"event_id": event_id, "source_id": source_id, "source_title": "Синтетический пример изменения условий рынка", "company_label": company_label,
            "headline": headline, "summary": summary, "inference": inference, "unknowns": unknowns}


def make_app(db_path: str | Path | None = None, demo_mode: bool | None = None, corpus_revision: str = CORPUS_REVISION, *, analysis_engine: str | None = None, injected_engine: Any | None = None, mvp_config: Any | None = None) -> FastAPI:
    enabled = (os.getenv("ALPHA_DEMO_MODE") == "1") if demo_mode is None else demo_mode
    path = db_path or os.getenv("ALPHA_DB_PATH", ".tmp/demo/demo.sqlite3")
    db = Database(path)
    engine_mode = analysis_engine if analysis_engine is not None else os.getenv("ALPHA_ANALYSIS_ENGINE", "deterministic_demo")
    if engine_mode in {"mvp_offline", "broker_live", "local_ollama"}:
        if injected_engine is not None:
            raise ValueError("MVP uses server-configured repositories, not injected engines")
        from backend.mvp.api import make_mvp_app
        from backend.mvp.config import MVPConfig
        return make_mvp_app(db, engine_mode, mvp_config or MVPConfig.from_environment())
    if engine_mode not in {"deterministic_demo", "agentic_fixture"}:
        raise ValueError("invalid ALPHA_ANALYSIS_ENGINE")
    if injected_engine is not None and engine_mode != "agentic_fixture":
        raise ValueError("injected engine requires agentic_fixture mode")
    engine = injected_engine if injected_engine is not None else (FixtureAnalysisEngine() if engine_mode == "agentic_fixture" else None)
    app = FastAPI(title="Synthetic Business Insights API", version="0.1.0")
    app.state.db = db
    app.state.demo_mode = enabled

    @app.exception_handler(APIError)
    async def api_error_handler(_: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable, "run_id": None}})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": {"code": "invalid_input", "message": "Некорректный запрос", "retryable": False, "run_id": None}})

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            code, message = "not_found", "Объект не найден"
        elif exc.status_code == 405:
            code, message = "invalid_input", "Метод не поддерживается"
        else:
            code, message = "invalid_input", "Некорректный запрос"
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": code, "message": message, "retryable": False, "run_id": None}})

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_: Request, __: Exception) -> JSONResponse:
        # Do not return SQL, filesystem, or framework details to the local client.
        return JSONResponse(status_code=500, content={"error": {"code": "run_failed", "message": "Внутренняя ошибка", "retryable": False, "run_id": None}})

    def require_demo() -> None:
        if not enabled:
            raise APIError(503, "auth_unconfigured", "Демонстрационный доступ отключён")

    def require_company(company_id: str) -> dict[str, Any]:
        require_demo()
        company = COMPANIES.get(company_id)
        if company is None:
            raise APIError(403, "access_denied", "Нет доступа")
        return company

    def get_run(run_id: str) -> dict[str, Any]:
        require_demo()
        with db.connection() as conn:
            row = conn.execute("SELECT * FROM analysis_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise APIError(404, "not_found", "Объект не найден")
        if row["company_id"] not in COMPANIES:
            raise APIError(403, "access_denied", "Нет доступа")
        return run_dto(row)

    def run_dto(row: sqlite3.Row) -> dict[str, Any]:
        return {"run_id": row["run_id"], "company_id": row["company_id"], "role_id": row["role_id"],
                "input_version": row["input_version"], "input_snapshot_id": row["input_snapshot_id"],
                "status": row["status"], "stage": row["stage"], "created_at": row["created_at"],
                "updated_at": row["updated_at"], "result_ids": json.loads(row["result_ids_json"]),
                "error_code": row["error_code"], "execution_mode": row["execution_mode"]}

    def summary(row: sqlite3.Row) -> dict[str, Any]:
        return {"insight_id": row["insight_id"], "company_id": row["company_id"], "role_id": row["role_id"],
                "event_id": row["event_id"], "headline": row["headline"], "summary": row["summary"],
                "created_at": row["created_at"], "bank_position_status": row["bank_position_status"],
                "synthetic": True, "exposure_status": row["exposure_status"]}

    @app.get(API + "/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "demo" if enabled else "disabled"}

    @app.get(API + "/companies")
    def companies() -> dict[str, Any]:
        require_demo()
        with db.connection() as conn:
            rows = conn.execute("SELECT * FROM companies WHERE company_id IN (?,?) ORDER BY company_id", tuple(COMPANIES)).fetchall()
        return {"items": [{"company_id": r["company_id"], "display_name": r["display_name"], "category": r["category"], "profile_version": r["profile_version"]} for r in rows]}

    @app.post(API + "/companies/{company_id}/analysis-runs", status_code=202)
    def create_run(company_id: str, body: RunRequest) -> dict[str, Any]:
        company = require_company(company_id)
        if body.role_id not in ROLE_VERSIONS:
            raise APIError(422, "invalid_input", "Некорректный запрос")
        snapshot_valid = True
        try:
            engine_snapshot = engine.snapshot(company_id, body.role_id) if engine is not None else {"corpus_revision": corpus_revision, "event_ids": ["fixture:synthetic-demand-shift"]}
            if engine is not None:
                snapshot_valid = _validate_engine_snapshot(engine_snapshot, company_id, body.role_id)
        except Exception:
            snapshot_valid = False
            engine_snapshot = None
        if not snapshot_valid:
            # Keep only a bounded marker in the fingerprint; never persist untrusted snapshot content.
            engine_snapshot = {"metadata_error": "engine_metadata_invalid"}
        current = {"company_id": company_id, "company_version": company["profile_version"], "role_id": body.role_id,
                   "role_version": ROLE_VERSIONS[body.role_id], "corpus_revision": corpus_revision, "user_input_revision": 0,
                   "engine_mode": engine_mode, "engine_snapshot": engine_snapshot,
                   "event_ids": engine_snapshot.get("event_ids", ["fixture:synthetic-demand-shift"])}
        fingerprint = canonical_hash(current)
        now = utc_now()
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT * FROM analysis_runs WHERE principal_id=? AND company_id=? AND role_id=? AND idempotency_key=?",
                               (PRINCIPAL, company_id, body.role_id, body.idempotency_key)).fetchone()
            if old is not None:
                if old["input_version"] != fingerprint:
                    raise APIError(409, "idempotency_conflict", "Ключ уже использован с другой версией входных данных")
                return run_dto(old)
            snap_id = "snap-" + uuid.uuid4().hex
            conn.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?)", (snap_id, company_id, body.role_id, fingerprint, json.dumps(current, sort_keys=True), now))
            run_id = "run-" + uuid.uuid4().hex
            conn.execute("INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (run_id, PRINCIPAL, company_id, body.role_id, body.idempotency_key, fingerprint, snap_id,
                          "running", "reserved", now, now, "[]", None, engine_mode, "[]"))
            conn.commit()
        try:
            # Pin the allowlist before run(): an injected engine may retain and
            # mutate its snapshot object, but cannot expand the persisted input.
            snapshot_source_ids = set(engine_snapshot["source_fingerprints"]) if engine is not None and snapshot_valid else set()
            result = engine.run(company_id, body.role_id) if engine is not None and snapshot_valid else None
            if engine is not None and not snapshot_valid:
                raise ValueError("invalid_engine_snapshot")
            if engine is not None and not isinstance(result, EngineResult):
                raise ValueError("invalid_engine_result")
            if engine is not None and (result.status not in {"succeeded", "failed", "unknown_outcome"}
                    or not isinstance(result.trace, (tuple, list)) or len(result.trace) > 32
                    or (result.error_code is not None and (not isinstance(result.error_code, str) or result.error_code not in _ENGINE_ERROR_CODES))):
                raise ValueError("invalid_engine_metadata")
            safe_trace = [] if engine is None else [_safe_trace_item(item, snapshot_source_ids) for item in result.trace]
            if engine is not None and result.status == "succeeded":
                _validate_engine_cards(result.cards, snapshot_source_ids)
            elif engine is not None and (result.cards or result.status not in {"failed", "unknown_outcome"}):
                raise ValueError("invalid_engine_result")
        except Exception:
            result = EngineResult("failed", (), (), "engine_result_invalid")
            safe_trace = []
        try:
            with db.connection() as conn:
              conn.execute("BEGIN IMMEDIATE")
              run_status = "succeeded" if engine is None or result.status == "succeeded" else result.status
              error_code = None if run_status == "succeeded" else _safe_engine_error(result.error_code)
              insight_ids = []
              cards = [fixture(company_id, body.role_id)] if engine is None else result.cards if run_status == "succeeded" else ()
              trace = safe_trace
              for card_data in cards:
                  if engine is None:
                      demo = card_data
                      source_rows = [{"source_id": demo["source_id"], "kind": "synthetic_demo", "title": demo["source_title"], "locator": "synthetic event fixture"}]
                  else:
                      demo = {"event_id": card_data["event_id"], "headline": card_data["headline"], "summary": card_data["summary"],
                              "unknowns": card_data["unknowns"], "source_id": card_data["sources"][0]["source_id"]}
                      source_rows = card_data["sources"]
                  for source in source_rows:
                      src = conn.execute("SELECT source_id FROM sources WHERE source_id=?", (source["source_id"],)).fetchone()
                      if src is None:
                          conn.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?)", (source["source_id"], source.get("kind", "synthetic_fixture"), source["title"], now, source.get("locator") or "synthetic fixture", 1, "synthetic"))
                          conn.execute("INSERT INTO source_search(source_id,title,content) VALUES(?,?,?)", (source["source_id"], source["title"], demo["summary"]))
                  existing = conn.execute("SELECT * FROM insights WHERE company_id=? AND role_id=? AND event_id=? AND input_version=?", (company_id, body.role_id, demo["event_id"], fingerprint)).fetchone()
                  if existing:
                      insight_ids.append(existing["insight_id"])
                      continue
                  insight_id = "ins-" + uuid.uuid4().hex
                  if engine is None:
                      refs = [demo["source_id"]]
                      blocks = [{"block_id":"fact-1","kind":"observed_fact","text":"Демо-набор содержит синтетическое событие об изменении условий рынка.","evidence_refs":refs,"assumptions":[],"unknowns":[]}, {"block_id":"inference-1","kind":"ai_inference","text":demo["inference"],"evidence_refs":refs,"assumptions":[f"Возможная связь для {demo['company_label']} является сценарием, а не установленным фактом."],"unknowns":demo["unknowns"]}]
                  else:
                      blocks = card_data["blocks"]
                  conn.execute("INSERT INTO insights VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (insight_id, company_id, body.role_id, demo["event_id"], fingerprint, demo["headline"], demo["summary"], now, "not_checked", card_data.get("exposure_status", "unknown"), json.dumps(blocks, ensure_ascii=False), json.dumps([s["source_id"] for s in source_rows]), json.dumps(demo["unknowns"], ensure_ascii=False)))
                  insight_ids.append(insight_id)
              conn.execute("UPDATE analysis_runs SET status=?,stage=?,updated_at=?,result_ids_json=?,error_code=?,trace_json=? WHERE run_id=?", (run_status, "complete" if run_status == "succeeded" else "failed", utc_now(), json.dumps(insight_ids), error_code, json.dumps(trace, ensure_ascii=False), run_id))
              for iid in insight_ids:
                  conn.execute("INSERT OR IGNORE INTO analysis_run_insights VALUES(?,?)", (run_id, iid))
              conn.commit()
              return run_dto(conn.execute("SELECT * FROM analysis_runs WHERE run_id=?", (run_id,)).fetchone())
        except Exception:
            # Roll back partially persisted cards/sources, then close the reservation
            # as failed so an unexpected storage/schema error cannot strand it.
            with db.connection() as conn:
                if conn.in_transaction:
                    conn.rollback()
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("UPDATE analysis_runs SET status='failed',stage='failed',updated_at=?,result_ids_json='[]',error_code='result_persistence_failed',trace_json='[]' WHERE run_id=? AND status='running'", (utc_now(), run_id))
                conn.commit()
                row = conn.execute("SELECT * FROM analysis_runs WHERE run_id=?", (run_id,)).fetchone()
            return run_dto(row)

    @app.get(API + "/analysis-runs/{run_id}")
    def read_run(run_id: str) -> dict[str, Any]:
        return get_run(run_id)

    @app.get(API + "/companies/{company_id}/feed")
    def feed(company_id: str, role_id: str = Query(...), after: str | None = Query(None)) -> dict[str, Any]:
        require_company(company_id)
        if role_id not in ROLE_VERSIONS:
            raise APIError(422, "invalid_input", "Некорректный запрос")
        if after:
            raise APIError(422, "invalid_input", "Параметр after пока не поддерживается")
        with db.connection() as conn:
            rows = conn.execute("SELECT * FROM insights WHERE company_id=? AND role_id=? ORDER BY created_at, insight_id", (company_id, role_id)).fetchall()
        return {"items": [summary(row) for row in rows], "next_cursor": None}

    @app.get(API + "/insights/{insight_id}")
    def read_insight(insight_id: str) -> dict[str, Any]:
        require_demo()
        with db.connection() as conn:
            row = conn.execute("SELECT * FROM insights WHERE insight_id=?", (insight_id,)).fetchone()
            sources = conn.execute("SELECT s.* FROM sources s JOIN json_each(?) ids ON ids.value=s.source_id", (row["source_ids_json"],)).fetchall() if row else []
        if row is None:
            raise APIError(404, "not_found", "Объект не найден")
        if row["company_id"] not in COMPANIES:
            raise APIError(403, "access_denied", "Нет доступа")
        srcs = [{"source_id": s["source_id"], "kind": s["kind"], "title": s["title"], "uri": None, "published_at": None,
                 "retrieved_at": s["retrieved_at"], "document_date": None, "locator": s["locator"], "synthetic": True, "trust_status": "synthetic"} for s in sources]
        return {**summary(row), "blocks": json.loads(row["blocks_json"]), "sources": srcs,
                "unknowns": json.loads(row["unknowns_json"]), "question": None}

    return app


app = make_app()
