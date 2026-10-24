from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import json
import sqlite3
import uuid

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException

from backend.dialogue import DialogueContext, DialogueError, DialogueService, DialogueSource, SQLiteDialogueStore
from backend.model_clients import UnknownOutcome, TaskIdConflict, InvalidRequest
from .engine import MVPEngine, digest

API = "/api/v1"


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ident(prefix):
    return prefix + uuid.uuid4().hex


class SafeError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=1, max_length=200)


class RunBody(Body):
    role_id: str


class MessageBody(Body):
    text: str = Field(min_length=1, max_length=2000)


class AnswerBody(Body):
    question_id: str = Field(min_length=1, max_length=200)
    answer: str = Field(min_length=1, max_length=2000)


class DurableModel:
    """One operator-assigned broker task, durably bound before any transport."""
    def __init__(self, db, model, task_id):
        self.db, self.model, self.task_id = db, model, task_id

    def generate(self, context, correction=None):
        if correction is not None:
            raise TaskIdConflict("Corrections require a separate assignment")
        if len(json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))) > 12000:
            raise InvalidRequest("Prompt exceeds fixed broker limit")
        fingerprint = digest(context)
        with self.db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM mvp_model_jobs WHERE task_id=?", (self.task_id,)).fetchone()
            if row:
                if row["fingerprint"] != fingerprint:
                    raise TaskIdConflict("Assigned task is bound to another payload")
                if row["status"] == "succeeded":
                    return json.loads(row["output_json"])
                raise UnknownOutcome("Prior request is terminal or unresolved; no retry")
            conn.execute("INSERT INTO mvp_model_jobs VALUES(?,?,?,NULL)", (self.task_id, fingerprint, "unknown_outcome"))
            conn.commit()
        output = self.model.generate(context)
        with self.db.connection() as conn:
            conn.execute("UPDATE mvp_model_jobs SET status='succeeded', output_json=? WHERE task_id=?",
                         (json.dumps(output, ensure_ascii=False), self.task_id))
        return output


def make_mvp_app(db, mode, config):
    # Additive tables leave legacy sources CHECK(synthetic=1) and all old rows intact.
    with db.connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS mvp_snapshots (
                id TEXT PRIMARY KEY, company TEXT NOT NULL, role TEXT NOT NULL,
                fingerprint TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS mvp_runs (
                id TEXT PRIMARY KEY, company TEXT NOT NULL, role TEXT NOT NULL,
                fingerprint TEXT NOT NULL UNIQUE, dto TEXT NOT NULL, trace_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS mvp_keys (
                company TEXT, role TEXT, key TEXT, fingerprint TEXT NOT NULL, run_id TEXT NOT NULL,
                PRIMARY KEY(company,role,key));
            CREATE TABLE IF NOT EXISTS mvp_cards (
                id TEXT PRIMARY KEY, company TEXT NOT NULL, role TEXT NOT NULL,
                snapshot_id TEXT NOT NULL REFERENCES mvp_snapshots(id), mode TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS mvp_model_jobs (
                task_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, status TEXT NOT NULL, output_json TEXT);
        """)
    engine = MVPEngine(config, mode)
    if mode == "broker_live" and not engine.blocker:
        engine.model = DurableModel(db, engine.model, config.task_id)
    store = SQLiteDialogueStore(db.path)
    dialogue = DialogueService(engine.model, None, store, timeout_seconds=90 if mode == "broker_live" else 10)

    @asynccontextmanager
    async def lifespan(app):
        yield
        store.close()

    app = FastAPI(title="Local business insights MVP", lifespan=lifespan)
    app.state.db, app.state.mvp_engine, app.state.dialogue_store = db, engine, store

    def error(status, code):
        return JSONResponse(status_code=status, content={"error": {"code": code,
            "message": "Запрос не выполнен", "retryable": False, "run_id": None}})

    @app.exception_handler(SafeError)
    async def safe_error(request, exc):
        return error(exc.status, exc.code)

    @app.exception_handler(DialogueError)
    async def dialogue_error(request, exc):
        return error(exc.http_status, exc.code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error(422, "invalid_input")

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error(exc.status_code, "not_found" if exc.status_code == 404 else "invalid_input")

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        return error(500, "internal_error")

    def access(company=None, role=None):
        if not config.local_access:
            raise SafeError(503, "auth_unconfigured")
        if company is not None and company not in engine.profiles:
            raise SafeError(403, "access_denied")
        if role is not None and role not in {"CFO", "COO"}:
            raise SafeError(422, "invalid_input")

    def require_ready():
        access()
        if engine.blocker:
            raise SafeError(503, engine.blocker)

    def user_state(company, role):
        with store.lock:
            heads = store.connection.execute("SELECT insight,version FROM dialogue_heads WHERE company=? AND role=? ORDER BY insight", (company, role)).fetchall()
            sources = {}
            for insight, version in heads:
                context = store.get(company, role, insight, version)
                for s in context.sources:
                    if s.kind == "user_input":
                        sources[s.source_id] = {**s.ref(), "text": s.text, "retrieved_at": None}
        # Initial contexts are not user revisions; analysis cannot invalidate itself.
        # Copied sources can be registered by GET in any card/UUID order.
        # Canonical ID order defines a bounded sample, not message chronology.
        ordered = sorted(sources)
        return [sources[k] for k in ordered[-4:]], digest([(k, digest(sources[k])) for k in ordered])

    def register(row, card):
        question = card["question"]
        context = DialogueContext(row["company"], row["role"], row["id"], card["event_id"], row["snapshot_id"],
            tuple(DialogueSource(s["source_id"], s["locator"], s["kind"], s["text"], row["company"],
                                s["title"], s.get("document_date"), s.get("historical", False), s["synthetic"])
                  for s in card["sources"]), question)
        store.register(context)
        current = store.get(row["company"], row["role"], row["id"])
        if (current.company_id != row["company"] or current.role_id != row["role"]
                or current.insight_id != row["id"] or current.event_id != card["event_id"]
                or any(s.company_id != row["company"] for s in current.sources)):
            raise SafeError(403, "access_denied")

    def get_card(insight_id):
        access()
        with db.connection() as conn:
            row = conn.execute("SELECT * FROM mvp_cards WHERE id=?", (insight_id,)).fetchone()
            if row is None:
                raise SafeError(404, "not_found")
            access(row["company"], row["role"])
            snap = conn.execute("SELECT * FROM mvp_snapshots WHERE id=?", (row["snapshot_id"],)).fetchone()
        card = json.loads(row["payload"])
        if (not snap or snap["company"] != row["company"] or snap["role"] != row["role"]
                or card["company_id"] != row["company"] or card["role_id"] != row["role"]):
            raise SafeError(403, "access_denied")
        snapshot = json.loads(snap["payload"])
        if digest(snapshot) != snap["fingerprint"] or card["event_id"] not in snapshot["event_ids"]:
            raise SafeError(403, "source_scope_invalid")
        pinned = snapshot["source_fingerprints"]
        if any(pinned.get(s["source_id"]) != digest(s) for s in card["sources"]):
            raise SafeError(403, "source_scope_invalid")
        source_ids = {s["source_id"] for s in card["sources"]}
        if any(not set(b.get("evidence_refs", [])) <= source_ids for b in card["blocks"]):
            raise SafeError(403, "source_scope_invalid")
        register(row, card)
        return row, card

    def summary(card):
        return {k: card[k] for k in ("insight_id", "company_id", "role_id", "event_id", "headline", "summary", "created_at", "bank_position_status", "synthetic", "exposure_status")}

    @app.get(API + "/health")
    def health():
        return {"status": "ok", "mode": mode, "access_mode": "configured_local_demo" if config.local_access else "disabled",
                "model": "fake" if mode == "mvp_offline" else "broker", "blocker": engine.blocker}

    @app.get(API + "/companies")
    def companies():
        require_ready()
        return {"items": [{"company_id": p.company_id, "display_name": p.display_name, "category": p.category,
                           "profile_version": p.version} for p in sorted(engine.profiles.values(), key=lambda x: x.company_id)]}

    @app.post(API + "/companies/{company}/analysis-runs", status_code=202)
    def create_run(company: str, body: RunBody):
        require_ready()
        access(company, body.role_id)
        users, revision = user_state(company, body.role_id)
        try:
            snapshot, bundles = engine.prepare(company, body.role_id, users, revision)
        except Exception:
            raise SafeError(503, "local_retrieval_blocked") from None
        fingerprint = digest(snapshot)
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            key = conn.execute("SELECT * FROM mvp_keys WHERE company=? AND role=? AND key=?", (company, body.role_id, body.idempotency_key)).fetchone()
            if key and key["fingerprint"] != fingerprint:
                raise SafeError(409, "idempotency_conflict")
            old = conn.execute("SELECT * FROM mvp_runs WHERE fingerprint=?", (fingerprint,)).fetchone()
            if old:
                conn.execute("INSERT OR IGNORE INTO mvp_keys VALUES(?,?,?,?,?)", (company, body.role_id, body.idempotency_key, fingerprint, old["id"]))
                conn.commit()
                return json.loads(old["dto"])
            run_id, snap_id, timestamp = ident("run-"), ident("snap-"), now()
            dto = {"run_id": run_id, "company_id": company, "role_id": body.role_id,
                   "input_version": fingerprint, "input_snapshot_id": snap_id, "status": "unknown_outcome",
                   "stage": "reserved", "created_at": timestamp, "updated_at": timestamp,
                   "result_ids": [], "error_code": "operation_incomplete", "execution_mode": mode}
            conn.execute("INSERT INTO mvp_snapshots VALUES(?,?,?,?,?)", (snap_id, company, body.role_id, fingerprint, json.dumps(snapshot)))
            conn.execute("INSERT INTO mvp_runs VALUES(?,?,?,?,?,?)", (run_id, company, body.role_id, fingerprint, json.dumps(dto), "[]"))
            conn.execute("INSERT INTO mvp_keys VALUES(?,?,?,?,?)", (company, body.role_id, body.idempotency_key, fingerprint, run_id))
            conn.commit()
        try:
            status, cards, trace, code = engine.run(company, body.role_id, bundles)
            with db.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                for card in cards:
                    iid = ident("ins-")
                    # Reject any source added/mutated after the pinned snapshot.
                    if any(snapshot["source_fingerprints"].get(s["source_id"]) != digest(s) for s in card["sources"]):
                        raise ValueError("unresolved_source")
                    card.update(insight_id=iid, company_id=company, role_id=body.role_id, created_at=timestamp,
                        question={"question_id": "q-" + digest([company, body.role_id, card["event_id"], snap_id, iid])[7:39],
                                  "text": "Есть ли у компании связь с этим событием?", "kind": "exposure_confirmation",
                                  "options": ["Есть связь", "Связи нет", "Неизвестно"]})
                    conn.execute("INSERT INTO mvp_cards VALUES(?,?,?,?,?,?)", (iid, company, body.role_id, snap_id, mode, json.dumps(card, ensure_ascii=False)))
                    dto["result_ids"].append(iid)
                dto.update(status=status, stage="complete" if status == "succeeded" else "failed", error_code=code, updated_at=now())
                conn.execute("UPDATE mvp_runs SET dto=?,trace_json=? WHERE id=?", (json.dumps(dto), json.dumps(trace), run_id))
                conn.commit()
            return dto
        except Exception:
            dto.update(status="failed", stage="failed", result_ids=[], error_code="analysis_failed", updated_at=now())
            with db.connection() as conn:
                conn.execute("UPDATE mvp_runs SET dto=? WHERE id=?", (json.dumps(dto), run_id))
            return dto

    @app.get(API + "/analysis-runs/{run_id}")
    def read_run(run_id: str):
        access()
        with db.connection() as conn:
            row = conn.execute("SELECT * FROM mvp_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise SafeError(404, "not_found")
        access(row["company"], row["role"])
        dto = json.loads(row["dto"])
        if dto["company_id"] != row["company"] or dto["role_id"] != row["role"]:
            raise SafeError(403, "access_denied")
        for iid in dto["result_ids"]:
            nested, card = get_card(iid)
            if nested["company"] != row["company"] or nested["role"] != row["role"]:
                raise SafeError(403, "access_denied")
            if nested["snapshot_id"] != dto["input_snapshot_id"]:
                raise SafeError(403, "access_denied")
        return dto

    @app.get(API + "/companies/{company}/feed")
    def feed(company: str, role_id: str = Query(...), after: str | None = None):
        require_ready()
        access(company, role_id)
        if after:
            raise SafeError(422, "invalid_input")
        with db.connection() as conn:
            latest = conn.execute("""SELECT dto FROM mvp_runs WHERE company=? AND role=?
                AND json_extract(dto,'$.status')='succeeded'
                AND json_extract(dto,'$.execution_mode')=? ORDER BY rowid DESC LIMIT 1""",
                (company, role_id, mode)).fetchone()
        ids = json.loads(latest['dto'])['result_ids'] if latest else []
        return {"items": [summary(get_card(iid)[1]) for iid in ids], "next_cursor": None}

    @app.get(API + "/insights/{insight_id}")
    def read_insight(insight_id: str):
        row, card = get_card(insight_id)
        context = store.get(row["company"], row["role"], insight_id)
        return {**card, "sources": [{k: v for k, v in s.items() if k not in {"text", "fingerprint", "content_hash", "guid", "published_at_raw", "provenance", "corpus_version", "quality", "historical"}} for s in card["sources"]],
                "question": context.question}

    @app.post(API + "/insights/{insight_id}/questions")
    def questions(insight_id: str, body: AnswerBody):
        require_ready()
        row, card = get_card(insight_id)
        result = dialogue.answer_question(company_id=row["company"], role_id=row["role"], insight_id=insight_id,
                    question_id=body.question_id, answer=body.answer, idempotency_key=body.idempotency_key)
        return asdict(result)

    @app.post(API + "/insights/{insight_id}/messages")
    def messages(insight_id: str, body: MessageBody):
        require_ready()
        row, card = get_card(insight_id)
        result = dialogue.ask_message(company_id=row["company"], role_id=row["role"], insight_id=insight_id,
                                     text=body.text, idempotency_key=body.idempotency_key)
        return asdict(result)

    return app
