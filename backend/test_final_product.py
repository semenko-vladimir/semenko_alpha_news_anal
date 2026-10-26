import unittest
import json
import time
from unittest.mock import patch
from dataclasses import replace
from pathlib import Path
from fastapi.testclient import TestClient
from backend.app import make_app
from backend.news_service import NewsRepository
from backend.sources.cbr_rss import parse
from backend import test_mvp_trace as trace_fixture


class FinalProductTests(unittest.TestCase):
    def setUp(self):
        self.fixture=trace_fixture.MVPTraceTests('test_trace_is_read_only_whitelisted_and_pinned')
        self.fixture.setUp()
        if 'public_catalog_path' not in self.fixture.app.state.mvp_engine.config.__dataclass_fields__:
            self.fixture.tearDown();self.fail('Public catalog configuration is not implemented')
        NewsRepository(self.fixture.root/'news.sqlite3').ingest(parse('<rss><channel><item><title>Условия финансирования и график поставок</title><guid>generic-business</guid><description>Условия оплаты и графики поставок требуют проверки.</description></item></channel></rss>'.encode(),synthetic=True))
        config=replace(self.fixture.app.state.mvp_engine.config, public_catalog_path=Path('data/catalog/companies.json'))
        self.app=make_app(self.fixture.root/'final.sqlite3',analysis_engine='mvp_offline',mvp_config=config)
        self.client=TestClient(self.app)

    def tearDown(self):
        if hasattr(self,'client'):
            self.client.close();self.app.state.dialogue_store.close()
        self.fixture.tearDown()

    def test_fifteen_company_catalog_profile_and_access_are_additive(self):
        response=self.client.get('/api/v1/companies');self.assertEqual(response.status_code,200)
        items=response.json()['items'];self.assertEqual(len(items),15)
        self.assertEqual(sum(c['profile_origin']=='public' for c in items),11)
        body=self.client.get('/api/v1/companies/PUB_MAGNIT/profile').json()
        self.assertEqual(body['company_id'],'PUB_MAGNIT');self.assertEqual(body['profile_origin'],'public')
        self.assertTrue(body['metrics']);self.assertTrue(body['sources']);self.assertEqual(body['as_of'],'2026-06-30')
        private=self.client.get('/api/v1/companies/U_135/profile').json()
        self.assertEqual(private['profile_origin'],'provided');self.assertIsNone(private['as_of'])
        self.assertNotIn('attributes',private);self.assertNotIn('flags',private)
        self.assertEqual(self.client.get('/api/v1/companies/PUB_OTHER/profile').status_code,403)

    def test_all_company_role_paths_keep_resolvable_sources_and_do_not_claim_exposure(self):
        for item in self.client.get('/api/v1/companies').json()['items']:
            for role in ['CFO','COO']:
                with self.subTest(company=item['company_id'],role=role):
                    run=self.client.post('/api/v1/companies/'+item['company_id']+'/analysis-runs',json={'role_id':role,'idempotency_key':'catalog-'+role})
                    self.assertEqual(run.status_code,202);body=run.json();self.assertEqual(body['status'],'succeeded')
                    self.assertTrue(body['result_ids'])
                    detail=self.client.get('/api/v1/insights/'+body['result_ids'][0])
                    self.assertEqual(detail.status_code,200,detail.json());card=detail.json()
                    ids={s['source_id'] for s in card['sources']}
                    self.assertTrue(all(set(b['evidence_refs'])<=ids for b in card['blocks']))
                    self.assertNotEqual(card['exposure_status'],'confirmed')
                    if item['profile_origin']=='public':
                        self.assertTrue(any(s.get('trust_status')=='official_publication' for s in card['sources']))

    def test_live_jobs_are_distinct_persistent_and_minimize_case_features(self):
        captured=[]
        def transport(job):
            captured.append(job);context=json.loads(job['prompt'])
            refs=[s['evidence_id'] for s in context.get('evidence',[]) if s['kind']=='news']
            candidate={'kind':'ai_inference','claim':'Может потребоваться проверка условий финансирования. Поддерживающая позиция банка не найдена.',
                'evidence_refs':refs[:1],'exposure_status':'unknown','bank_source_id':None}
            return json.dumps({'status':'ok','task_id':job['task_id'],'model':job['model'],'content':json.dumps(candidate,ensure_ascii=False)})
        config=replace(self.app.state.mvp_engine.config,allow_live=True,allow_client_data=True,task_id='FINAL-INTEGRATION',operation_jobs=True,transport=transport)
        app=make_app(self.fixture.root/'live.sqlite3',analysis_engine='broker_live',mvp_config=config)
        try:
            with TestClient(app) as client:
                for cid in ['U_135','PUB_MAGNIT']:
                    result=client.post('/api/v1/companies/'+cid+'/analysis-runs',json={'role_id':'CFO','idempotency_key':'stable'})
                    self.assertEqual(result.json()['status'],'succeeded',result.json())
                    replay=client.post('/api/v1/companies/'+cid+'/analysis-runs',json={'role_id':'CFO','idempotency_key':'stable'})
                    self.assertEqual(replay.json()['run_id'],result.json()['run_id'])
                self.assertEqual(len(captured),2);self.assertNotEqual(captured[0]['task_id'],captured[1]['task_id'])
                self.assertNotIn('NEGATIVEMARK',captured[0]['prompt'])
        finally:app.state.dialogue_store.close()

    def test_late_dialogue_completion_cannot_reopen_live_after_unknown(self):
        from backend.dialogue import DialogueService as RealDialogueService
        captured=[]
        def transport(job):
            captured.append(job);context=json.loads(job['prompt'])
            if 'sources' in context:
                time.sleep(.15)
                candidate={'blocks':[{'kind':'ai_inference','text':'Может потребоваться проверка.','evidence_refs':[context['message_source_id']]}],'unknowns':[]}
            else:
                refs=[s['evidence_id'] for s in context['evidence'] if s['kind']=='news']
                candidate={'kind':'ai_inference','claim':'Может потребоваться проверка. Позиция банка не найдена.','evidence_refs':refs[:1],'exposure_status':'unknown','bank_source_id':None}
            return json.dumps({'status':'ok','task_id':job['task_id'],'model':job['model'],'content':json.dumps(candidate,ensure_ascii=False)})
        config=replace(self.app.state.mvp_engine.config,allow_live=True,allow_client_data=True,task_id='FINAL-LATE',operation_jobs=True,transport=transport)
        def fast_dialogue(*args,**kwargs):return RealDialogueService(*args,**{**kwargs,'timeout_seconds':.02})
        with patch('backend.mvp.api.DialogueService',side_effect=fast_dialogue):
            app=make_app(self.fixture.root/'late.sqlite3',analysis_engine='broker_live',mvp_config=config)
            with TestClient(app) as client:
                run=client.post('/api/v1/companies/PUB_MAGNIT/analysis-runs',json={'role_id':'CFO','idempotency_key':'late-run'}).json()
                iid=run['result_ids'][0]
                result=client.post('/api/v1/insights/'+iid+'/messages',json={'text':'Какие ограничения?','idempotency_key':'late-message'})
                self.assertEqual(result.json()['status'],'unknown_outcome')
                time.sleep(.2)
                self.assertTrue(client.get('/api/v1/health').json()['live_operations']['blocked'])
                self.assertEqual(len(captured),2)

    def test_unrelated_source_produces_empty_feed_instead_of_invented_relevance(self):
        from unittest.mock import Mock
        database=self.fixture.root/'unrelated.sqlite3'
        NewsRepository(database).ingest(parse('<rss><channel><item><title>Поздравление с праздником</title><guid>unrelated</guid><description>Торжественное вручение наград.</description></item></channel></rss>'.encode(),synthetic=True))
        config=replace(self.app.state.mvp_engine.config,news_path=database)
        app=make_app(self.fixture.root/'unrelated-app.sqlite3',analysis_engine='mvp_offline',mvp_config=config)
        model=Mock(side_effect=AssertionError('Unrelated source must not start generation'))
        app.state.mvp_engine.model.generate=model
        with TestClient(app) as client:
            result=client.post('/api/v1/companies/PUB_POSITIVE/analysis-runs',json={'role_id':'COO','idempotency_key':'unrelated'}).json()
            self.assertEqual(result['status'],'succeeded');self.assertEqual(result['result_ids'],[]);model.assert_not_called()


if __name__=='__main__':unittest.main()
