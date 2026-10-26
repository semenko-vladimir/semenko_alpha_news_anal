import csv
import uuid
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import make_app
from backend.profiles.repository import COMPANY_FIELDS, FLAGS
from backend.mvp.config import ALLOWED_COMPANIES, MVPConfig
from backend.news_service import NewsRepository
from backend.sources.cbr_rss import parse


class MVPTraceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path('.tmp/tests/trace') / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        companies = self.root / 'companies.csv'
        with companies.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=COMPANY_FIELDS, delimiter=';')
            writer.writeheader()
            for cid in ALLOWED_COMPANIES:
                row = dict.fromkeys(COMPANY_FIELDS, 'synthetic')
                row.update(dict.fromkeys(FLAGS, '0.0'))
                row.update(SYNTHETIC_U=cid, MAINOKVED_CODE='46.20', MAINOKVED_NAME='Synthetic fixture', REVENUE_COMPANY='1000.0', FTS_COUNTRY_NAME='')
                writer.writerow(row)
        news = NewsRepository(self.root / 'news.sqlite3')
        news.ingest(parse(b'<rss><channel><item><title>Interest rate event</title><guid>fixture-guid</guid><description>Fixture only</description></item></channel></rss>', synthetic=True))
        config = MVPConfig(companies_path=companies, news_path=self.root / 'news.sqlite3', local_access=True)
        self.app = make_app(self.root / 'app.sqlite3', analysis_engine='mvp_offline', mvp_config=config)
        self.client = TestClient(self.app)

    def tearDown(self):
        import shutil
        self.client.close()
        self.client.app.state.dialogue_store.close()
        shutil.rmtree(self.root)

    def run_once(self):
        return self.client.post('/api/v1/companies/U_135/analysis-runs', json={'role_id':'CFO','idempotency_key':'trace-test'}).json()

    def test_trace_is_read_only_whitelisted_and_pinned(self):
        run = self.run_once()
        before = self.client.app.state.db.connection()
        with before as conn:
            original = conn.execute('SELECT trace_json FROM mvp_runs WHERE id=?', (run['run_id'],)).fetchone()[0]
        with self.client.app.state.dialogue_store.lock:
            dialogue_before = self.client.app.state.dialogue_store.connection.execute('SELECT count(*) FROM dialogue_contexts').fetchone()[0]
        first = self.client.get(f"/api/v1/analysis-runs/{run['run_id']}/trace")
        second = self.client.get(f"/api/v1/analysis-runs/{run['run_id']}/trace")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(first.json()['execution_mode'], 'mvp_offline')
        self.assertTrue(first.json()['trace'])
        self.assertTrue(first.json()['features'])
        self.assertIn('model', [item['stage'] for item in first.json()['trace']])
        self.assertIn('context', [item['stage'] for item in first.json()['trace']])
        self.assertTrue(all(set(item) <= {'stage', 'status', 'features', 'attempt', 'reason', 'reasons', 'sources'}
                            for item in first.json()['trace']))
        self.assertNotIn('Fixture only', first.text)
        with self.client.app.state.db.connection() as conn:
            self.assertEqual(conn.execute('SELECT trace_json FROM mvp_runs WHERE id=?', (run['run_id'],)).fetchone()[0], original)
        with self.client.app.state.dialogue_store.lock:
            self.assertEqual(self.client.app.state.dialogue_store.connection.execute('SELECT count(*) FROM dialogue_contexts').fetchone()[0], dialogue_before)
        self.assertEqual(self.client.get('/api/v1/analysis-runs/missing/trace').status_code, 404)

    def test_disabled_access_and_foreign_run_scope_are_denied(self):
        run = self.run_once()
        disabled = TestClient(make_app(self.root / 'disabled.sqlite3', analysis_engine='mvp_offline', mvp_config=MVPConfig(local_access=False)))
        self.assertEqual(disabled.get(f"/api/v1/analysis-runs/{run['run_id']}/trace").status_code, 503)
        disabled.close()
        disabled.app.state.dialogue_store.close()
        with self.client.app.state.db.connection() as conn:
            conn.execute('UPDATE mvp_runs SET company=? WHERE id=?', ('U_42266', run['run_id']))
        response = self.client.get(f"/api/v1/analysis-runs/{run['run_id']}/trace")
        self.assertEqual(response.status_code, 403)

    def test_snapshot_tampering_rejected_and_untrusted_trace_fields_dropped(self):
        run = self.run_once()
        with self.client.app.state.db.connection() as conn:
            conn.execute('UPDATE mvp_runs SET trace_json=? WHERE id=?', ('[{"stage":"plan","status":"completed","prompt":"PRIVATE_SENTINEL","reason":"raw private text"},{"stage":[],"status":{}}]', run['run_id']))
        response = self.client.get(f"/api/v1/analysis-runs/{run['run_id']}/trace")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('PRIVATE_SENTINEL', response.text)
        self.assertNotIn('raw private text', response.text)
        with self.client.app.state.db.connection() as conn:
            conn.execute('UPDATE mvp_snapshots SET payload=? WHERE id=?', ('{}', run['input_snapshot_id']))
        self.assertEqual(self.client.get(f"/api/v1/analysis-runs/{run['run_id']}/trace").json()['error']['code'], 'source_scope_invalid')

    def test_oversized_or_nested_trace_metadata_is_bounded_and_safe(self):
        run = self.run_once()
        with self.app.state.db.connection() as conn:
            conn.execute('UPDATE mvp_runs SET trace_json=? WHERE id=?', (' ' * 65537, run['run_id']))
        self.assertEqual(self.client.get(f"/api/v1/analysis-runs/{run['run_id']}/trace").status_code, 403)
        with self.app.state.db.connection() as conn:
            conn.execute('UPDATE mvp_runs SET trace_json=? WHERE id=?', ('[{"stage":"plan","status":"completed","reasons":{"raw":"value"},"source_ids":[{"x":1}],"feature_keys":[[]]}]', run['run_id']))
        response = self.client.get(f"/api/v1/analysis-runs/{run['run_id']}/trace")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['trace'], [{'stage': 'plan', 'status': 'completed'}])

    def test_malformed_trace_json_returns_safe_error(self):
        run = self.run_once()
        with self.app.state.db.connection() as conn:
            conn.execute('UPDATE mvp_runs SET trace_json=? WHERE id=?', ('{malformed', run['run_id']))
        self.assertEqual(self.client.get(f"/api/v1/analysis-runs/{run['run_id']}/trace").status_code, 403)


if __name__ == '__main__':
    unittest.main()
