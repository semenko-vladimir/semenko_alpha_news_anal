"""Behavior tests for access, idempotency, provenance and durable storage."""

from copy import deepcopy
import json
import uuid
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import make_app
from application.fixture_engine import EngineResult, FixtureAnalysisEngine


class BackendBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(".tmp/tests/api")
        self.temp.mkdir(parents=True, exist_ok=True)
        self.path = self.temp / f"test-{uuid.uuid4().hex}.sqlite3"
        self.app = make_app(self.path, demo_mode=True)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.path) + suffix)
            if candidate.exists():
                candidate.unlink()

    def run_analysis(self, company="demo-trade-01", role="CFO", key="key-1", client=None):
        return (client or self.client).post(f"/api/v1/companies/{company}/analysis-runs", json={"role_id": role, "idempotency_key": key})

    def test_disabled_mode_and_safe_input_errors(self):
        disabled = TestClient(make_app(self.path, demo_mode=False))
        self.assertEqual(disabled.get("/api/v1/health").json(), {"status": "ok", "mode": "disabled"})
        response = disabled.get("/api/v1/companies")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (503, "auth_unconfigured"))
        invalid = self.client.post("/api/v1/companies/demo-trade-01/analysis-runs", json={"role_id": "CFO", "idempotency_key": "k", "extra": 1})
        self.assertEqual((invalid.status_code, invalid.json()["error"]["code"]), (422, "invalid_input"))
        disabled.close()

    def test_allowlist_and_four_company_role_contexts(self):
        companies = self.client.get("/api/v1/companies").json()["items"]
        self.assertEqual(len(companies), 2)
        self.assertEqual(self.client.get("/api/v1/companies/private-real-co/feed?role_id=CFO").status_code, 403)
        outputs = []
        for company in ("demo-trade-01", "demo-transport-01"):
            for role in ("CFO", "COO"):
                response = self.run_analysis(company, role, f"{company}-{role}")
                self.assertEqual(response.status_code, 202, response.text)
                outputs.append(response.json())
        self.assertEqual(len({run["result_ids"][0] for run in outputs}), 4)
        self.assertTrue(all(run["execution_mode"] == "deterministic_demo" for run in outputs))

    def test_idempotency_conflict_deduplicated_feed_and_persistence(self):
        first = self.run_analysis().json()
        repeat = self.run_analysis().json()
        self.assertEqual(first["run_id"], repeat["run_id"])
        conflict_app = make_app(self.path, demo_mode=True, corpus_revision="demo-corpus-v2")
        conflict_client = TestClient(conflict_app)
        conflict = self.run_analysis(client=conflict_client)
        self.assertEqual((conflict.status_code, conflict.json()["error"]["code"]), (409, "idempotency_conflict"))
        new_key = self.run_analysis(key="key-2")
        self.assertEqual(new_key.status_code, 202)
        feed = self.client.get("/api/v1/companies/demo-trade-01/feed?role_id=CFO").json()
        self.assertEqual(len(feed["items"]), 1)
        self.assertEqual(new_key.json()["result_ids"], first["result_ids"])
        insight_id = first["result_ids"][0]
        reloaded = TestClient(make_app(self.path, demo_mode=True))
        self.assertEqual(reloaded.get(f"/api/v1/analysis-runs/{first['run_id']}").json(), first)
        card = reloaded.get(f"/api/v1/insights/{insight_id}").json()
        self.assertEqual(card["bank_position_status"], "not_checked")
        self.assertNotIn("bank_position", [block["kind"] for block in card["blocks"]])
        sources = {source["source_id"] for source in card["sources"]}
        for block in card["blocks"]:
            self.assertTrue(set(block["evidence_refs"]).issubset(sources))
        self.assertTrue(all(source["synthetic"] and source["trust_status"] == "synthetic" for source in card["sources"]))
        conflict_client.close()
        reloaded.close()

    def test_scope_on_nested_objects_and_unknown_ids(self):
        self.run_analysis().json()
        with self.app.state.db.connection() as conn:
            run = conn.execute("SELECT * FROM analysis_runs LIMIT 1").fetchone()
            insight = conn.execute("SELECT * FROM insights LIMIT 1").fetchone()
            conn.execute("UPDATE analysis_runs SET company_id='outside-company' WHERE run_id=?", (run["run_id"],))
            conn.execute("UPDATE insights SET company_id='outside-company' WHERE insight_id=?", (insight["insight_id"],))
        self.assertEqual(self.client.get(f"/api/v1/analysis-runs/{run['run_id']}").json()["error"]["code"], "access_denied")
        self.assertEqual(self.client.get(f"/api/v1/insights/{insight['insight_id']}").json()["error"]["code"], "access_denied")
        self.assertEqual(self.client.get("/api/v1/insights/no-such-id").status_code, 404)

    def test_after_unknown_role_unsupported_and_fts5(self):
        self.assertEqual(self.client.get("/api/v1/companies/demo-trade-01/feed?role_id=CEO").json()["error"]["code"], "invalid_input")
        self.assertEqual(self.client.get("/api/v1/companies/demo-trade-01/feed?role_id=CFO&after=x").status_code, 422)
        self.assertEqual(self.client.post("/api/v1/insights/any/messages", json={"text": "x"}).status_code, 404)
        with self.app.state.db.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0], 1)
            conn.execute("INSERT INTO source_search(source_id,title,content) VALUES('smoke','fixture','FTS5 searchable')")
            found = conn.execute("SELECT count(*) FROM source_search WHERE source_search MATCH 'searchable'").fetchone()[0]
        self.assertEqual(found, 1)
        self.app.state.db.initialize()
        with self.app.state.db.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0], 1)

    def test_agentic_fixture_persists_validated_card_sources_and_trace(self):
        engine = FixtureAnalysisEngine()
        app = make_app(self.path, demo_mode=True, analysis_engine="agentic_fixture", injected_engine=engine)
        client = TestClient(app)
        run = self.run_analysis(client=client).json()
        self.assertEqual((run["execution_mode"], run["status"]), ("agentic_fixture", "succeeded"))
        card = client.get(f"/api/v1/insights/{run['result_ids'][0]}").json()
        self.assertTrue(card["sources"])
        self.assertTrue(all(s["source_id"].startswith("fixture:") and s["synthetic"] for s in card["sources"]))
        with app.state.db.connection() as conn:
            trace = conn.execute("SELECT trace_json FROM analysis_runs WHERE run_id=?", (run["run_id"],)).fetchone()[0]
            snapshot = conn.execute("SELECT snapshot_json FROM snapshots WHERE snapshot_id=?", (run["input_snapshot_id"],)).fetchone()[0]
        self.assertNotIn("prompt", trace.lower())
        pinned = json.loads(snapshot)["engine_snapshot"]
        allowed = set(pinned["source_fingerprints"])
        self.assertIn(card["event_id"], pinned["event_ids"])
        self.assertTrue({s["source_id"] for s in card["sources"]}.issubset(allowed))
        for block in card["blocks"]:
            self.assertTrue(set(block["evidence_refs"]).issubset(allowed))
        for item in json.loads(trace):
            self.assertTrue(set(item.get("source_ids", [])).issubset(allowed))
        client.close()

    def test_sources_outside_snapshot_fail_before_any_card_is_persisted(self):
        class ForeignSourceEngine(FixtureAnalysisEngine):
            mode = "referenced"
            calls = 0

            def snapshot(self, company_id, role_id):
                self.pinned = super().snapshot(company_id, role_id)
                return self.pinned

            def run(self, company_id, role_id):
                self.calls += 1
                result = super().run(company_id, role_id)
                card = deepcopy(result.cards[0])
                foreign = dict(card["sources"][0], source_id="fixture:outside-pinned-snapshot")
                if self.mode == "mutated_snapshot":
                    self.pinned["source_fingerprints"][foreign["source_id"]] = "sha256:untrusted"
                if self.mode == "extra":
                    card["sources"].append(foreign)
                else:
                    card["sources"] = [foreign]
                    for block in card["blocks"]:
                        block["evidence_refs"] = [foreign["source_id"]]
                # A valid trace cannot authorize sources outside the input snapshot.
                cards = (result.cards[0], card) if self.mode == "second_card" else (card,)
                trace = () if self.mode == "empty_trace" else result.trace
                return EngineResult("succeeded", cards, trace, None)

        engine = ForeignSourceEngine()
        app = make_app(self.path, demo_mode=True, analysis_engine="agentic_fixture", injected_engine=engine)
        with TestClient(app) as client:
            for mode in ("referenced", "extra", "second_card", "empty_trace", "mutated_snapshot"):
                with self.subTest(mode=mode):
                    engine.mode = mode
                    response = self.run_analysis(key=mode, client=client)
                    self.assertEqual(response.status_code, 202, response.text)
                    run = response.json()
                    self.assertEqual((run["status"], run["stage"], run["result_ids"], run["error_code"]),
                                     ("failed", "failed", [], "engine_result_invalid"))
                    calls = engine.calls
                    self.assertEqual(self.run_analysis(key=mode, client=client).json(), run)
                    self.assertEqual(engine.calls, calls)
                    self.assertEqual(client.get(f"/api/v1/analysis-runs/{run['run_id']}").json(), run)
                    with app.state.db.connection() as conn:
                        for table in ("insights", "sources", "source_search", "analysis_run_insights"):
                            self.assertEqual(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
                        trace = conn.execute("SELECT trace_json FROM analysis_runs WHERE run_id=?", (run["run_id"],)).fetchone()[0]
                    self.assertEqual(json.loads(trace), [])
                    self.assertEqual(client.get("/api/v1/companies/demo-trade-01/feed?role_id=CFO").json()["items"], [])

    def test_agentic_idempotency_and_pinned_snapshot_survive_restart(self):
        class CountingEngine(FixtureAnalysisEngine):
            calls = 0
            revision = "revision-1"

            def snapshot(self, company_id, role_id):
                return dict(super().snapshot(company_id, role_id), corpus_revision=self.revision)

            def run(self, company_id, role_id):
                self.calls += 1
                return super().run(company_id, role_id)

        engine = CountingEngine()
        app = make_app(self.path, demo_mode=True, analysis_engine="agentic_fixture", injected_engine=engine)
        with TestClient(app) as client:
            first = self.run_analysis(client=client).json()
            self.assertEqual(first["status"], "succeeded")
            self.assertEqual(self.run_analysis(client=client).json(), first)
            self.assertEqual(engine.calls, 1)
            again = self.run_analysis(key="new-key", client=client).json()
            self.assertEqual(again["result_ids"], first["result_ids"])
            self.assertEqual(engine.calls, 2)
        with TestClient(make_app(self.path, demo_mode=True, analysis_engine="agentic_fixture", injected_engine=engine)) as client:
            self.assertEqual(self.run_analysis(client=client).json(), first)
            self.assertEqual(engine.calls, 2)
            engine.revision = "revision-2"
            conflict = self.run_analysis(client=client)
            self.assertEqual((conflict.status_code, conflict.json()["error"]["code"]), (409, "idempotency_conflict"))
            self.assertEqual(engine.calls, 2)
            self.assertEqual(client.get(f"/api/v1/analysis-runs/{first['run_id']}").json(), first)
            self.assertEqual(len(client.get("/api/v1/companies/demo-trade-01/feed?role_id=CFO").json()["items"]), 1)

    def test_safe_engine_failures_are_terminal_without_retry(self):
        class FailedEngine(FixtureAnalysisEngine):
            status = "failed"
            error = "timeout"
            calls = 0

            def run(self, company_id, role_id):
                self.calls += 1
                return EngineResult(self.status, (), ({"stage": "model", "status": self.error},), self.error)

        engine = FailedEngine()
        app = make_app(self.path, demo_mode=True, analysis_engine="agentic_fixture", injected_engine=engine)
        with TestClient(app) as client:
            for error in ("timeout", "budget_exceeded", "authentication_failure", "unknown_outcome"):
                with self.subTest(error=error):
                    engine.error = error
                    engine.status = "unknown_outcome" if error == "unknown_outcome" else "failed"
                    response = self.run_analysis(key=error, client=client)
                    self.assertEqual(response.status_code, 202, response.text)
                    run = response.json()
                    self.assertEqual((run["status"], run["result_ids"], run["error_code"]), (engine.status, [], error))
                    calls = engine.calls
                    self.assertEqual(self.run_analysis(key=error, client=client).json(), run)
                    self.assertEqual(engine.calls, calls)
                    with app.state.db.connection() as conn:
                        for table in ("insights", "sources", "source_search", "analysis_run_insights"):
                            self.assertEqual(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_engine_result_validation_and_failure_have_no_cards(self):
        class BadEngine:
            def snapshot(self, company_id, role_id): return {"engine_version": "test", "corpus_revision": "a", "event_ids": [], "source_fingerprints": {}}
            def run(self, company_id, role_id): return {"status": "succeeded", "cards": [{"headline": "fake"}]}
        app = make_app(self.path, demo_mode=True, analysis_engine="agentic_fixture", injected_engine=BadEngine())
        client = TestClient(app)
        run = self.run_analysis(client=client).json()
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["result_ids"], [])
        self.assertEqual(client.get("/api/v1/companies/demo-trade-01/feed?role_id=CFO").json()["items"], [])
        client.close()

    def test_malformed_engine_source_is_failed_without_stuck_reservation(self):
        class MalformedSourceEngine(FixtureAnalysisEngine):
            def run(self, company_id, role_id):
                result = super().run(company_id, role_id)
                card = dict(result.cards[0])
                card["sources"] = [dict(result.cards[0]["sources"][0])]
                card["sources"][0].pop("title")
                return type(result)(result.status, (card,), result.trace, result.error_code)

        app = make_app(self.path, demo_mode=True, analysis_engine="agentic_fixture", injected_engine=MalformedSourceEngine())
        client = TestClient(app)
        response = self.run_analysis(client=client)
        self.assertEqual(response.status_code, 202, response.text)
        run = response.json()
        self.assertEqual((run["status"], run["stage"], run["result_ids"]), ("failed", "failed", []))
        with app.state.db.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM insights").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM sources").fetchone()[0], 0)
        self.assertEqual(client.get("/api/v1/companies/demo-trade-01/feed?role_id=CFO").json()["items"], [])
        client.close()

    def test_untrusted_engine_snapshot_trace_and_error_code_are_rejected(self):
        class LeakyEngine(FixtureAnalysisEngine):
            mode = "trace"
            def snapshot(self, company_id, role_id):
                value = super().snapshot(company_id, role_id)
                if self.mode == "snapshot": value["debug"] = "RAW_SNAPSHOT_SENTINEL"
                return value
            def run(self, company_id, role_id):
                result = super().run(company_id, role_id)
                if self.mode == "trace":
                    return type(result)(result.status, result.cards,
                                        ({"stage": "model", "status": "submitted", "source_ids": ["RAW_PROMPT_SENTINEL"]},), result.error_code)
                if self.mode == "error":
                    return type(result)("failed", (), result.trace, "RAW_ERROR_SENTINEL")
                return result

        engine = LeakyEngine()
        client = TestClient(make_app(self.path, demo_mode=True, analysis_engine="agentic_fixture", injected_engine=engine))
        for mode, key in (("trace", "safe-trace"), ("snapshot", "safe-snapshot"), ("error", "safe-error")):
            engine.mode = mode
            response = self.run_analysis(key=key, client=client)
            self.assertEqual(response.status_code, 202, response.text)
            run = response.json()
            self.assertEqual((run["status"], run["result_ids"], run["error_code"]), ("failed", [], "engine_result_invalid"))
            with self.app.state.db.connection() as conn:
                trace = conn.execute("SELECT trace_json FROM analysis_runs WHERE run_id=?", (run["run_id"],)).fetchone()[0]
                snapshot = conn.execute("SELECT snapshot_json FROM snapshots WHERE snapshot_id=?", (run["input_snapshot_id"],)).fetchone()[0]
            self.assertEqual(trace, "[]")
            self.assertNotIn("RAW_PROMPT_SENTINEL", trace)
            self.assertNotIn("RAW_SNAPSHOT_SENTINEL", snapshot)
            self.assertNotIn("RAW_ERROR_SENTINEL", run["error_code"])
        client.close()

    def test_persistence_error_rolls_back_and_closes_run(self):
        app = make_app(self.path, demo_mode=True, analysis_engine="agentic_fixture", injected_engine=FixtureAnalysisEngine())
        client = TestClient(app)
        with app.state.db.connection() as conn:
            conn.execute("CREATE TRIGGER reject_fixture_source BEFORE INSERT ON sources BEGIN SELECT RAISE(ABORT, 'synthetic persistence failure'); END")
        response = self.run_analysis(client=client)
        self.assertEqual(response.status_code, 202, response.text)
        run = response.json()
        self.assertEqual((run["status"], run["stage"], run["result_ids"], run["error_code"]),
                         ("failed", "failed", [], "result_persistence_failed"))
        with app.state.db.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM insights").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM sources").fetchone()[0], 0)
        client.close()

    def test_old_database_additive_migration(self):
        legacy = self.temp / f"legacy-{uuid.uuid4().hex}.sqlite3"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        if legacy.exists(): legacy.unlink()
        import sqlite3
        conn = sqlite3.connect(legacy)
        conn.execute("CREATE TABLE analysis_runs (run_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL, company_id TEXT NOT NULL, role_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, input_version TEXT NOT NULL, input_snapshot_id TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, result_ids_json TEXT NOT NULL, error_code TEXT, UNIQUE(principal_id, company_id, role_id, idempotency_key))")
        conn.execute("INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("legacy-run", "local-demo-principal", "demo-trade-01", "CFO", "legacy-key", "legacy-version", "legacy-snapshot", "succeeded", "complete", "2026-10-22T00:00:00Z", "2026-10-22T00:00:00Z", "[]", None))
        conn.commit(); conn.close()
        migrated = make_app(legacy, demo_mode=True)
        with migrated.state.db.connection() as db:
            columns = {r["name"] for r in db.execute("PRAGMA table_info(analysis_runs)")}
        self.assertTrue({"execution_mode", "trace_json"}.issubset(columns))
        with TestClient(migrated) as client:
            old = client.get("/api/v1/analysis-runs/legacy-run").json()
            self.assertEqual((old["input_version"], old["status"], old["execution_mode"]),
                             ("legacy-version", "succeeded", "deterministic_demo"))
            run = self.run_analysis(key="after-migration", client=client).json()
            self.assertEqual(run["status"], "succeeded")
        with TestClient(make_app(legacy, demo_mode=True, analysis_engine="agentic_fixture")) as client:
            self.assertEqual(client.get("/api/v1/analysis-runs/legacy-run").json(), old)
            self.assertEqual(client.get(f"/api/v1/analysis-runs/{run['run_id']}").json(), run)
            agent_run = self.run_analysis(key="agent-after-migration", client=client).json()
            self.assertEqual(agent_run["status"], "succeeded")
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(legacy) + suffix)
            if candidate.exists(): candidate.unlink()


class MVPIntegrationTests(unittest.TestCase):
    """HTTP handlers over production repositories/pipeline/dialogue, fake transport."""
    def setUp(self):
        import csv
        from backend.profiles.repository import COMPANY_FIELDS, FLAGS
        from backend.mvp.config import ALLOWED_COMPANIES, MVPConfig
        from backend.news_service import NewsRepository
        from backend.sources.cbr_rss import parse
        self.root = Path('.tmp/tests/app') / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.path = self.root / 'app.sqlite3'
        companies = self.root / 'companies.csv'
        with companies.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=COMPANY_FIELDS, delimiter=';')
            writer.writeheader()
            for index, cid in enumerate(ALLOWED_COMPANIES):
                row = dict.fromkeys(COMPANY_FIELDS, 'synthetic')
                row.update(dict.fromkeys(FLAGS, '0.0'))
                row.update(SYNTHETIC_U=cid, MAINOKVED_CODE='46.20' if index < 2 else '49.4',
                           MAINOKVED_NAME='Synthetic fixture', REVENUE_COMPANY='1000.0', FTS_COUNTRY_NAME='')
                writer.writerow(row)
        self.news = NewsRepository(self.root / 'news.sqlite3')
        self.news.ingest(parse(b'<rss><channel><item><title>Interest rate event</title><guid>a</guid><description>Fixture: exposure unknown</description></item></channel></rss>', synthetic=True))
        self.config = MVPConfig(companies_path=companies, news_path=self.root / 'news.sqlite3', local_access=True)
        self.app = make_app(self.path, analysis_engine='mvp_offline', mvp_config=self.config)
        self.client = TestClient(self.app)
        self.clients = [self.client]

    def tearDown(self):
        import shutil
        for client in self.clients:
            client.close()
            client.app.state.dialogue_store.close()
        shutil.rmtree(self.root)

    def run_analysis(self, key='a', client=None, company='U_135', role='CFO'):
        return (client or self.client).post('/api/v1/companies/' + company + '/analysis-runs',
                                          json={'role_id': role, 'idempotency_key': key})

    def card(self):
        run = self.run_analysis().json()
        self.assertEqual(run['status'], 'succeeded', run)
        return self.client.get('/api/v1/insights/' + run['result_ids'][0]).json()

    def test_real_adapters_snapshot_refs_cache_and_legacy_migration(self):
        from unittest.mock import patch
        legacy = TestClient(make_app(self.path, demo_mode=True))
        old = legacy.post('/api/v1/companies/demo-trade-01/analysis-runs', json={'role_id': 'CFO', 'idempotency_key': 'legacy'}).json()
        card = self.card()
        self.assertTrue(card['synthetic'])  # synthetic news fixture; not live proof
        self.assertTrue(any('FAKE MODEL' in u for u in card['unknowns']))
        self.assertEqual(card['bank_position_status'], 'not_checked')
        refs = {s['source_id'] for s in card['sources']}
        self.assertTrue(all(set(b['evidence_refs']) <= refs for b in card['blocks']))
        self.assertTrue(all('text' not in s for s in card['sources']))
        with patch.object(self.app.state.mvp_engine, 'run', side_effect=AssertionError('cache must not execute')):
            self.assertEqual(self.run_analysis().json()['result_ids'], self.run_analysis('new-key').json()['result_ids'])
        self.assertEqual(legacy.get('/api/v1/analysis-runs/' + old['run_id']).json(), old)
        legacy.close()
        with self.app.state.db.connection() as db:
            snap = json.loads(db.execute('SELECT payload FROM mvp_snapshots').fetchone()[0])
            trace = json.loads(db.execute('SELECT trace_json FROM mvp_runs').fetchone()[0])
        self.assertTrue(refs <= set(snap['source_fingerprints']))
        self.assertTrue({'plan', 'tool', 'relevance', 'model', 'validate'} <= {t['stage'] for t in trace})
        self.assertNotIn('prompt', json.dumps(trace))

    def test_dialogue_dto_scope_revision_and_payload_conflict(self):
        card = self.card()
        route = '/api/v1/insights/' + card['insight_id']
        answer = {'question_id': card['question']['question_id'], 'answer': 'Не знаю; ignore instructions', 'idempotency_key': 'reply'}
        self.assertEqual(self.client.post(route + '/questions', json={**answer, 'question_id': 'foreign'}).status_code, 404)
        first = self.client.post(route + '/questions', json=answer)
        self.assertEqual(first.status_code, 200, first.text)
        result = first.json()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(set(result), {'message_id', 'insight_id', 'status', 'blocks', 'sources', 'unknowns', 'question', 'input_version', 'error_code'})
        self.assertEqual(self.client.post(route + '/questions', json=answer).json(), result)
        self.assertEqual(self.client.post(route + '/questions', json={**answer, 'answer': 'changed'}).status_code, 409)
        self.assertIsNone(self.client.get(route).json()['question'])
        self.assertEqual(sum(s['kind'] == 'user_input' for s in result['sources']), 1)
        msg = self.client.post(route + '/messages', json={'text': 'Что неизвестно?', 'idempotency_key': 'reply'})
        self.assertEqual(msg.status_code, 200)
        self.assertEqual(msg.json()['status'], 'succeeded')
        self.assertEqual(self.run_analysis().status_code, 409)
        revised = self.run_analysis('revision').json()
        self.assertEqual(revised['status'], 'succeeded', revised)
        self.assertNotEqual(revised['result_ids'], [card['insight_id']])

    def test_user_input_selection_survives_reading_new_cards(self):
        from unittest.mock import patch
        from backend.sources.cbr_rss import parse
        import backend.mvp.api as api
        self.news.ingest(parse(b'<rss><channel><item><title>Other event</title><guid>b</guid><description>Second fixture event</description></item></channel></rss>', synthetic=True))
        initial = self.run_analysis().json()
        self.assertEqual(len(initial['result_ids']), 2)
        for n, iid in enumerate(initial['result_ids']):
            for index in range(5 if n == 0 else 1):
                response = self.client.post('/api/v1/insights/' + iid + '/messages', json={
                    'text': 'Synthetic input ' + str(n) + ':' + str(index), 'idempotency_key': str(index)})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['status'], 'succeeded')
        original_ident = api.ident
        sequence = iter(['ins-00000000000000000000000000000001', 'ins-00000000000000000000000000000002'])
        def ordered_ident(prefix):
            return next(sequence) if prefix == 'ins-' else original_ident(prefix)
        with patch.object(self.app.state.mvp_engine.model, 'generate', wraps=self.app.state.mvp_engine.model.generate) as model:
            with patch.object(api, 'ident', side_effect=ordered_ident):
                revised = self.run_analysis('revised').json()
            self.assertEqual(revised['status'], 'succeeded', revised)
            calls = model.call_count
            self.assertEqual(calls, 2)
            with self.app.state.db.connection() as db:
                snapshot = json.loads(db.execute('SELECT payload FROM mvp_snapshots WHERE id=?', (revised['input_snapshot_id'],)).fetchone()[0])
            self.assertEqual(sum(k.startswith('user_input:') for k in snapshot['source_fingerprints']), 4)
            for iid in revised['result_ids']:
                self.assertEqual(self.client.get('/api/v1/insights/' + iid).status_code, 200)
            self.assertEqual(self.client.get('/api/v1/companies/U_135/feed?role_id=CFO').status_code, 200)
            replay = self.run_analysis('revised')
            self.assertEqual(replay.status_code, 202, replay.text)
            self.assertEqual(replay.json(), revised)
            self.assertEqual(self.run_analysis('new-key').json(), revised)
            self.assertEqual(model.call_count, calls)

    def test_csv_records_keep_distinct_locators_and_feature_provenance(self):
        import csv
        from dataclasses import replace
        from backend.mvp.engine import MVPEngine, digest
        payments, links = self.root / 'payments.csv', self.root / 'links.csv'
        with payments.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream, delimiter=';')
            writer.writerow(['BENEFICIARY', 'PAYER', 'PAY_AMT', 'PAY_CNT'])
            writer.writerows([['U_135', 'U_754', '10', '1'], ['U_135', 'U_32424', '20', '1']])
        with links.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream, delimiter=';')
            writer.writerow(['SYNTHETIC_F', 'CITY_NAME', 'RC_NAME', 'SYNTHETIC_U'])
            writer.writerows([['F_1', 'Fixture', 'Fixture', 'U_135'], ['F_2', 'Fixture', 'Fixture', 'U_135']])
        engine = MVPEngine(replace(self.config, payments_path=payments, links_path=links), 'mvp_offline')
        profile = engine.profiles['U_135']
        snapshot, bundles = engine.prepare('U_135', 'CFO', [], 'test-revision')
        sources = bundles[0]['sources']
        for kind in ('companies', 'payments', 'links'):
            original = [r for r in profile.source_refs if r['source_id'].startswith('csv:' + kind + ':')]
            adapted = [s for s in sources.values() if s['source_id'].startswith('csv:' + kind + ':')]
            self.assertEqual({s['locator'] for s in adapted}, {r['locator'] for r in original})
            self.assertEqual(len(adapted), len(original))
            self.assertEqual(len({s['source_id'] for s in adapted}), len(original))
            for s in adapted:
                self.assertEqual(snapshot['source_fingerprints'][s['source_id']], digest(s))
                self.assertEqual(s['fingerprint'], original[0]['fingerprint'])
                self.assertEqual('main_okved_code' in s['text'], kind == 'companies')
                self.assertEqual('flags' in s['text'], kind == 'companies')
        app = make_app(self.root / 'csv-api.sqlite3', analysis_engine='mvp_offline', mvp_config=engine.config)
        client = TestClient(app)
        self.clients.append(client)
        run = self.run_analysis(client=client).json()
        self.assertEqual(run['status'], 'succeeded', run)
        card = client.get('/api/v1/insights/' + run['result_ids'][0])
        self.assertEqual(card.status_code, 200, card.text)
        self.assertEqual(len([s for s in card.json()['sources'] if s['source_id'].startswith('csv:')]), 5)
        self.assertTrue(all(set(b['evidence_refs']) <= {s['source_id'] for s in card.json()['sources']} for b in card.json()['blocks']))

    def test_csv_context_limit_is_explicit_and_stable(self):
        import csv
        from dataclasses import replace
        from backend.mvp.engine import MVPEngine
        payments = self.root / 'payments.csv'
        with payments.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream, delimiter=';')
            writer.writerow(['BENEFICIARY', 'PAYER', 'PAY_AMT', 'PAY_CNT'])
            writer.writerows([['U_135', 'U_' + str(1000 + n), '10', '1'] for n in range(6)])
        engine = MVPEngine(replace(self.config, payments_path=payments), 'mvp_offline')
        snapshot, bundles = engine.prepare('U_135', 'CFO', [], 'test-revision')
        selected = [s for s in bundles[0]['sources'].values() if s['source_id'].startswith('csv:payments:')]
        self.assertEqual(len(selected), 4)
        self.assertTrue(all('показано 4 из 6' in s['text'] for s in selected))
        engine.profiles['U_135'].source_refs.reverse()
        self.assertEqual(engine.prepare('U_135', 'CFO', [], 'test-revision'), (snapshot, bundles))
        status, cards, _, error = engine.run('U_135', 'CFO', bundles)
        self.assertEqual((status, error), ('succeeded', None))
        self.assertTrue(any('показано 4 из 6' in u for u in cards[0]['unknowns']))

    def test_nested_scope_and_source_tampering_are_denied(self):
        card = self.card()
        with self.app.state.db.connection() as db:
            row = db.execute('SELECT * FROM mvp_cards').fetchone()
            payload = json.loads(row['payload'])
            payload['sources'][0]['source_id'] = 'fixture:foreign'
            db.execute('UPDATE mvp_cards SET payload=?', (json.dumps(payload),))
        route = '/api/v1/insights/' + card['insight_id']
        self.assertEqual(self.client.get(route).status_code, 403)
        self.assertEqual(self.client.post(route + '/messages', json={'text': 'x', 'idempotency_key': 'x'}).status_code, 403)
        self.assertEqual(self.client.get('/api/v1/companies/outside/feed?role_id=CFO').status_code, 403)

    def test_disabled_and_missing_inputs_never_fallback(self):
        from dataclasses import replace
        for config, code in ((replace(self.config, local_access=False), 'auth_unconfigured'),
                             (replace(self.config, companies_path=None), 'local_inputs_missing')):
            app = make_app(self.root / (code + '.sqlite3'), analysis_engine='mvp_offline', mvp_config=config)
            client = TestClient(app)
            self.clients.append(client)
            self.assertEqual(client.get('/api/v1/health').json()['blocker'], code)
            self.assertEqual(client.get('/api/v1/companies').json()['error']['code'], code)

    def live_client(self, transport, name='live'):
        from dataclasses import replace
        config = replace(self.config, allow_live=True, allow_client_data=True,
                         task_id='APP-test-only-fake-transport', transport=transport)
        app = make_app(self.root / (name + '.sqlite3'), analysis_engine='broker_live', mvp_config=config)
        client = TestClient(app)
        self.clients.append(client)
        return client

    def test_broker_transport_is_fake_one_attempt_cache_and_restart(self):
        calls = []
        def transport(job):
            calls.append(job)
            context = json.loads(job['prompt'])
            output = {'kind': 'ai_inference', 'claim': 'Conditional scenario',
                      'evidence_refs': [context['evidence'][0]['evidence_id']],
                      'exposure_status': 'unknown', 'bank_source_id': None}
            return json.dumps({'task_id': job['task_id'], 'model': job['model'], 'status': 'ok', 'content': json.dumps(output)})
        client = self.live_client(transport)
        self.assertEqual(client.get('/api/v1/health').status_code, 200)
        self.assertEqual(len(calls), 0)
        first = self.run_analysis(client=client).json()
        self.assertEqual(first['status'], 'succeeded', first)
        restarted = self.live_client(transport)
        self.assertEqual(self.run_analysis('new-key', client=restarted).json(), first)
        self.assertEqual(len(calls), 1)
        # Same assigned ID with a different role/input cannot call transport.
        other = self.run_analysis('other-role', client=restarted, role='COO').json()
        self.assertNotEqual(other['status'], 'succeeded')
        self.assertEqual(len(calls), 1)

    def test_unknown_broker_outcome_and_invalid_candidate_never_retry(self):
        for scenario in ('timeout', 'invalid'):
            calls = []
            def transport(job):
                calls.append(job)
                if scenario == 'timeout':
                    raise TimeoutError('injected transport timeout')
                return json.dumps({'task_id': job['task_id'], 'model': job['model'], 'status': 'ok', 'content': '{}'})
            client = self.live_client(transport, scenario)
            first = self.run_analysis(client=client).json()
            self.assertNotEqual(first['status'], 'succeeded')
            self.assertEqual(self.run_analysis('second', client=client).json(), first)
            self.assertEqual(len(calls), 1)

    def test_user_cannot_configure_engine_paths_or_commands(self):
        response = self.client.post('/api/v1/companies/U_135/analysis-runs', json={
            'role_id': 'CFO', 'idempotency_key': 'x', 'execution_mode': 'broker_live', 'path': 'private'})
        self.assertEqual(response.status_code, 422)
        card = self.card()
        route = '/api/v1/insights/' + card['insight_id'] + '/messages'
        self.assertEqual(self.client.post(route, json={'text': 'x' * 2001, 'idempotency_key': 'x'}).status_code, 422)

    def test_live_requires_assignment_and_reserves_original_synthetic_probe_id(self):
        from dataclasses import replace
        from unittest.mock import patch
        for config, blocker in (
            (self.config, 'live_not_authorized'),
            (replace(self.config, allow_live=True, allow_client_data=True, task_id='LIVE-MVP-001-deepseek-20261023'),
             'live_task_reserved_for_synthetic_probe'),
        ):
            with patch('backend.model_clients.BrokerModelClient.live', side_effect=AssertionError('No live initialization')):
                app = make_app(self.root / (blocker + '.sqlite3'), analysis_engine='broker_live', mvp_config=config)
            client = TestClient(app)
            self.clients.append(client)
            self.assertEqual(client.get('/api/v1/health').json()['blocker'], blocker)
            self.assertEqual(self.run_analysis(client=client).status_code, 503)

    def test_missing_bank_not_found_historical_and_corpus_cache_invalidation(self):
        from unittest.mock import patch
        from backend.bank_corpus import BankRepository
        bank = BankRepository(self.root / 'bank-index.sqlite3')
        self.app.state.mvp_engine.bank = bank
        first = self.run_analysis().json()
        self.assertEqual(first['status'], 'succeeded')
        card = self.client.get('/api/v1/insights/' + first['result_ids'][0]).json()
        self.assertEqual(card['bank_position_status'], 'not_checked')
        # Controlled retrieval cases test APP adaptation; they are not real corpus evidence.
        passage = {'source_id': 'bank:test:version', 'title': 'Test document', 'locator': 'test:page:1',
                   'text': 'Historical supplied statement', 'synthetic': False,
                   'document_date': '2021-04-15', 'historical': True, 'quality': 'fixture', 'corpus_version': 'test-v1'}
        with patch.object(bank, 'search_with_status', return_value={'status': 'historical', 'passages': [passage]}):
            second = self.run_analysis('historical').json()
        card = self.client.get('/api/v1/insights/' + second['result_ids'][0]).json()
        self.assertEqual(card['bank_position_status'], 'historical')
        block = next(b for b in card['blocks'] if b['kind'] == 'bank_position')
        self.assertIn('Историческая', block['text'])
        self.assertIn('2021-04-15', block['text'])
        with patch.object(bank, 'search_with_status', return_value={'status': 'not_found', 'passages': []}):
            third = self.run_analysis('notfound').json()
        card = self.client.get('/api/v1/insights/' + third['result_ids'][0]).json()
        self.assertEqual(card['bank_position_status'], 'not_found')
        self.assertNotEqual(first['input_version'], third['input_version'])

    def test_persistence_failure_rolls_back_all_cards_and_no_repeat(self):
        from unittest.mock import patch
        with self.app.state.db.connection() as db:
            db.execute("CREATE TRIGGER fail_mvp_card BEFORE INSERT ON mvp_cards BEGIN SELECT RAISE(ABORT, 'injected'); END")
        first = self.run_analysis().json()
        self.assertEqual(first['status'], 'failed')
        self.assertEqual(first['result_ids'], [])
        with self.app.state.db.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM mvp_cards').fetchone()[0], 0)
        with patch.object(self.app.state.mvp_engine, 'run', side_effect=AssertionError('No retry')):
            self.assertEqual(self.run_analysis('again').json(), first)

    def test_broker_budget_and_auth_errors_are_terminal(self):
        for broker_code, expected in (('daily_budget_blocked', 'budget_exceeded'),
                                      ('upstream_unauthorized', 'authentication_failure')):
            calls = []
            def transport(job):
                calls.append(job)
                return json.dumps({'task_id': job['task_id'], 'model': job['model'],
                                   'status': 'error', 'error': broker_code})
            client = self.live_client(transport, broker_code)
            run = self.run_analysis(client=client).json()
            self.assertEqual((run['status'], run['error_code']), ('failed', expected), run)
            self.assertEqual(self.run_analysis('new', client=client).json(), run)
            self.assertEqual(len(calls), 1)

    def test_foreign_question_and_message_cannot_access_nested_objects(self):
        first = self.card()
        other = self.run_analysis('other', company='U_754', role='COO').json()
        second = self.client.get('/api/v1/insights/' + other['result_ids'][0]).json()
        bad = self.client.post('/api/v1/insights/' + first['insight_id'] + '/questions', json={
            'question_id': second['question']['question_id'], 'answer': 'x', 'idempotency_key': 'foreign'})
        self.assertEqual(bad.status_code, 404)
        with self.app.state.db.connection() as db:
            db.execute("UPDATE mvp_cards SET company='outside' WHERE id=?", (first['insight_id'],))
        self.assertEqual(self.client.post('/api/v1/insights/' + first['insight_id'] + '/messages',
                                         json={'text': 'x', 'idempotency_key': 'foreign'}).status_code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
