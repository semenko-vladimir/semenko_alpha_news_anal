import unittest
import uuid
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.product_server import build_product_app
from backend.news_service import NewsRepository
from backend.sources.cbr_rss import parse


class ProductRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.root=Path('.tmp/tests/product-runtime')/uuid.uuid4().hex;self.root.mkdir(parents=True)
        self.catalog=Path('data/catalog/companies.json')
        NewsRepository(self.root/'news.sqlite3').ingest(parse(b'<rss><channel><item><title>Interest rate</title><guid>runtime-test</guid></item></channel></rss>',synthetic=True))
    def tearDown(self):
        import shutil
        shutil.rmtree(self.root)
    def test_public_only_restart_keeps_news_and_does_not_require_original_csv(self):
        for _ in range(2):
            app=build_product_app(self.root,self.catalog,auto_refresh=False)
            with TestClient(app) as client:
                self.assertEqual(len(client.get('/api/v1/companies').json()['items']),11)
                self.assertEqual(client.get('/api/v1/data-status').json()['news_count'],1)
    def test_live_zero_quota_never_invokes_transport_and_is_visible(self):
        status={'daily_attempt_limit':4,'max_output_tokens':512,'budget_day':datetime.now(timezone.utc).date().isoformat(),'remaining_attempts':0}
        with patch('backend.model_clients._subprocess_transport') as transport:
            app=build_product_app(self.root,self.catalog,live=True,broker_status=status)
            with TestClient(app) as client:
                self.assertTrue(client.get('/api/v1/data-status').json()['live_operations']['blocked'])
                self.assertEqual(client.get('/api/v1/data-status').json()['live_operations']['remaining_attempts'],0)
                run=client.post('/api/v1/companies/PUB_MAGNIT/analysis-runs',json={'role_id':'CFO','idempotency_key':'zero-quota'})
                self.assertEqual(run.status_code,429);self.assertEqual(run.json()['error']['code'],'budget_exceeded');transport.assert_not_called()
                with app.state.db.connection() as conn:self.assertEqual(conn.execute('SELECT count(*) FROM mvp_runs').fetchone()[0],0)


if __name__=='__main__':unittest.main()
