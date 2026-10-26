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
from backend.profiles.catalog import profile_metadata
from backend.analysis.claim_policy import unsupported_categorical_impact
from .operation_jobs import OperationJobs

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
    operations = None
    if mode in {"broker_live","local_ollama"} and not engine.blocker:
        if mode=='local_ollama':
            operations=OperationJobs(db,engine.model,config.task_id,client_factory=lambda task_id:engine.model,
                status_policy={'daily_attempt_limit':None,'remaining_attempts':None,'reset_time_msk':None,'provider':'ollama_local'})
        elif config.operation_jobs:
            operations = OperationJobs(db, engine.model, config.task_id)
        else:
            engine.model = DurableModel(db, engine.model, config.task_id)
    store = SQLiteDialogueStore(db.path)
    dialogue_timeout=90 if mode in {'local_ollama','broker_live'} else 10
    dialogue = DialogueService(engine.model, None, store, timeout_seconds=dialogue_timeout)

    @asynccontextmanager
    async def lifespan(app):
        yield
        store.close()

    app = FastAPI(title="Local business insights MVP", lifespan=lifespan)
    app.state.db, app.state.mvp_engine, app.state.dialogue_store = db, engine, store
    app.state.operation_jobs = operations

    def dialogue_for(row, operation, key):
        if not operations:return dialogue
        stable=digest([row['company'],row['role'],row['id'],operation,key])
        return DialogueService(operations.for_operation('dialogue:'+stable),None,store,timeout_seconds=dialogue_timeout)

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

    def get_card(insight_id, register_context=True):
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
        if mode=='local_ollama' and any(b.get('kind')=='ai_inference' and unsupported_categorical_impact(b.get('text','')) for b in card['blocks']):
            raise SafeError(409,'local_output_requires_review')
        if register_context:
            register(row, card)
        return row, card

    def summary(card):
        return {**{k: card[k] for k in ("insight_id", "company_id", "role_id", "event_id", "headline", "summary", "created_at", "bank_position_status", "synthetic", "exposure_status")},
                'event_published_at':card.get('event_published_at')}

    @app.get(API + "/health")
    def health():
        return {"status": "ok", "mode": mode, "access_mode": "configured_local_demo" if config.local_access else "disabled",
                "model": "fake" if mode == "mvp_offline" else "ollama" if mode=='local_ollama' else "broker", "blocker": engine.blocker,
                "live_operations":operations.status() if operations else None}

    @app.get(API + "/companies")
    def companies():
        require_ready()
        return {"items": [profile_metadata(p) for p in sorted(engine.profiles.values(), key=lambda x: (x.features.get('profile_origin')!='public',x.display_name))]}

    @app.get(API + "/companies/{company}/profile")
    def company_profile(company: str):
        require_ready();access(company)
        profile=engine.profiles[company]
        sources=[{'kind':'company_profile','title':'Предоставленный профиль компании','uri':None,'trust_status':'provided',
                  **{k:v for k,v in source.items() if k not in {'text','fingerprint'}}} for source in profile.source_refs]
        return {**profile_metadata(profile), 'legal_name':profile.features.get('legal_name'),
            'entity_scope':profile.features.get('entity_scope'),
            'activity':profile.features.get('activity_description') or profile.features.get('main_okved_name'),
            'metrics':profile.features.get('metrics',[]),'unknowns':profile.unknowns,'sources':sources}

    @app.get(API + '/data-status')
    def data_status():
        require_ready()
        from backend.news_service.product_refresh import product_data_status
        local_model=({'provider':'ollama','name':engine.model.model,'display_name':'Qwen 3.5 4B' if '4B-' in engine.model.model else 'Qwen 3.5 9B',
            'prompt_version':engine.model.prompt_version,'hackathon_allowlisted':False,'last_usage':engine.model.last_usage} if mode=='local_ollama' else None)
        return {**product_data_status(engine.news.database_path),'public_profile_count':sum(p.features.get('profile_origin')=='public' for p in engine.profiles.values()),
                'provided_profile_count':sum(p.features.get('profile_origin')!='public' for p in engine.profiles.values()),
                'execution_mode':mode,'live_operations':operations.status() if operations else None,'local_model':local_model}

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
            if operations and operations.status()['blocked']:
                reason=operations.status()['stop_code']
                raise SafeError(429 if reason=='budget_exceeded' else 503,reason or 'live_unavailable')
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
            status, cards, trace, code = (engine.run(company, body.role_id, bundles,model=operations.for_operation(run_id))
                                         if operations else engine.run(company, body.role_id, bundles))
            if operations and status != 'succeeded' and operations.status()['stop_code'] in {None,'unresolved_operation'}:
                operations.stop('budget_exceeded' if code=='budget_exceeded' else 'unknown_outcome' if status=='unknown_outcome' else 'output_invalid' if code=='output_invalid' else 'operation_failed')
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
        return validate_read_run(run_id, register_context=True)

    def validate_read_run(run_id: str, register_context=True):
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
            nested, card = get_card(iid, register_context=register_context)
            if nested["company"] != row["company"] or nested["role"] != row["role"]:
                raise SafeError(403, "access_denied")
            if nested["snapshot_id"] != dto["input_snapshot_id"]:
                raise SafeError(403, "access_denied")
        return dto

    @app.get(API + "/analysis-runs/{run_id}/trace")
    def read_run_trace(run_id: str):
        # Reuse the run's complete access and nested card/snapshot checks.
        dto = validate_read_run(run_id, register_context=False)
        with db.connection() as conn:
            row = conn.execute("SELECT * FROM mvp_runs WHERE id=?", (run_id,)).fetchone()
            snap = conn.execute("SELECT * FROM mvp_snapshots WHERE id=?", (dto["input_snapshot_id"],)).fetchone()
        if not row or not snap or snap["company"] != row["company"] or snap["role"] != row["role"]:
            raise SafeError(403, "access_denied")
        snapshot = json.loads(snap["payload"])
        if digest(snapshot) != snap["fingerprint"] or snap["fingerprint"] != row["fingerprint"]:
            raise SafeError(403, "source_scope_invalid")
        trace_json = row["trace_json"]
        if not isinstance(trace_json, str) or len(trace_json) > 65536 or len(trace_json.encode("utf-8", errors="replace")) > 65536:
            raise SafeError(403, "source_scope_invalid")
        try:
            raw_trace = json.loads(trace_json)
        except (TypeError, json.JSONDecodeError):
            raise SafeError(403, "source_scope_invalid") from None
        if not isinstance(raw_trace, list):
            raise SafeError(403, "source_scope_invalid")
        raw_trace = raw_trace[:64]
        stage_names = {"context", "plan", "tool", "relevance", "model", "validate", "adapt"}
        feature_keys = {"company_category", "role", "eligibility_status", "main_okved_name"}
        source_kinds = {"news", "bank_document", "company_profile", "user_input", "company", "payment", "link"}
        statuses = {"completed", "submitted", "started", "passed", "rejected", "stopped",
                    "one_correction_available", "unknown_outcome", "timeout", "budget_exceeded",
                    "authentication_failure", "model_failure"}
        reasons = {"topic_only_exposure_unknown", "no_retry", "no_correction_assignment", "output_invalid",
                   "timeout", "unknown_outcome", "budget_exceeded", "authentication_failure", "model_failure",
                   "output_not_object", "schema_fields_invalid", "kind_invalid", "claim_invalid",
                   "claim_size_limit", "evidence_refs_invalid", "evidence_refs_limit", "fact_evidence_required",
                   "evidence_reference_unresolved", "exposure_status_invalid", "exposure_not_supported",
                   "bank_attribution_unverified", "unexpected_bank_attribution", "unsupported_categorical_impact",
                   "output_size_limit"}
        safe_trace = []
        for item in raw_trace:
            if not isinstance(item, dict):
                continue
            stage, status = item.get("stage"), item.get("status")
            if not isinstance(stage, str) or stage not in stage_names or not isinstance(status, str) or status not in statuses:
                continue
            entry = {"stage": stage, "status": status}
            if stage == "context" and isinstance(item.get("feature_keys"), list):
                entry["features"] = [key for key in item["feature_keys"][:16] if isinstance(key, str) and key in feature_keys]
            if stage == "model" and isinstance(item.get("attempt"), int) and item["attempt"] in {1, 2}:
                entry["attempt"] = item["attempt"]
            reason = item.get("reason")
            if isinstance(reason, str) and reason in reasons:
                entry["reason"] = reason
            reason_list = item.get("reasons")
            if isinstance(reason_list, list):
                entry["reasons"] = [value for value in reason_list[:8] if isinstance(value, str) and value in reasons][:4]
            refs = item.get("source_ids")
            if stage == "tool" and isinstance(refs, list):
                refs = [value for value in refs[:32] if isinstance(value, str) and len(value) <= 256]
                pinned = snapshot.get("source_fingerprints", {})
                # Source metadata comes from the pinned snapshot payload, never from trace text.
                source_rows = {}
                # Older snapshots store fingerprints only; resolve safe refs from the pinned cards.
                for iid in dto["result_ids"]:
                    with db.connection() as card_conn:
                        card_row = card_conn.execute("SELECT payload FROM mvp_cards WHERE id=?", (iid,)).fetchone()
                    if card_row:
                        for source in json.loads(card_row["payload"]).get("sources", []):
                            if isinstance(source, dict) and source.get("source_id") in refs:
                                source_rows[source["source_id"]] = source
                entry["sources"] = [{"source_id": sid, "kind": source_rows[sid].get("kind") if source_rows[sid].get("kind") in source_kinds else "unknown",
                                      "date": (source_rows[sid].get("document_date") or source_rows[sid].get("published_at"))[:32]
                                      if isinstance(source_rows[sid].get("document_date") or source_rows[sid].get("published_at"), str) else None}
                    for sid in refs if sid in source_rows and sid in pinned]
            safe_trace.append(entry)
        feature_labels = {"company_category": "категория компании", "role": "роль руководителя",
                          "eligibility_status": "статус допуска", "main_okved_name": "основной вид деятельности"}
        features = list(dict.fromkeys(feature_labels[key] for item in safe_trace
                                      for key in item.get("features", []) if key in feature_labels))
        return {"run_id": run_id, "company_id": row["company"], "role_id": row["role"],
                "status": dto["status"], "execution_mode": dto["execution_mode"],
                "trace": safe_trace, "features": features}

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
        items=[]
        for iid in ids:
            try:items.append(summary(get_card(iid)[1]))
            except SafeError as exc:
                if mode!='local_ollama' or exc.code!='local_output_requires_review':raise
        return {"items":items,"next_cursor":None}

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
        result = dialogue_for(row,'answer',body.idempotency_key).answer_question(company_id=row["company"], role_id=row["role"], insight_id=insight_id,
                    question_id=body.question_id, answer=body.answer, idempotency_key=body.idempotency_key)
        if operations and result.status!='succeeded' and operations.status()['stop_code'] in {None,'unresolved_operation'}:operations.stop('unknown_outcome' if result.status=='unknown_outcome' else 'output_invalid')
        return asdict(result)

    @app.post(API + "/insights/{insight_id}/messages")
    def messages(insight_id: str, body: MessageBody):
        require_ready()
        row, card = get_card(insight_id)
        result = dialogue_for(row,'message',body.idempotency_key).ask_message(company_id=row["company"], role_id=row["role"], insight_id=insight_id,
                                     text=body.text, idempotency_key=body.idempotency_key)
        if operations and result.status!='succeeded' and operations.status()['stop_code'] in {None,'unresolved_operation'}:operations.stop('unknown_outcome' if result.status=='unknown_outcome' else 'output_invalid')
        return asdict(result)

    return app
