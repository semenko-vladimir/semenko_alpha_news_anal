import json
from pathlib import Path
import shutil
import unittest
import uuid
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.product_server import build_product_app
from backend.model_clients.ollama_local import DEFAULT_LOCAL_MODEL
from backend.news_service import NewsRepository
from backend.sources.cbr_rss import parse


class LocalProductTests(unittest.TestCase):
    def setUp(self):
        self.root=Path('.tmp/tests/local-product')/uuid.uuid4().hex;self.root.mkdir(parents=True)
        NewsRepository(self.root/'news.sqlite3').ingest(parse('<rss><channel><item><title>Финансирование и графики поставок</title><guid>local-synthetic-test</guid><description>Условия оплаты и графики поставок требуют проверки.</description></item></channel></rss>'.encode(),synthetic=True))
        self.calls=[];self.transport_fail=False

    def tearDown(self):shutil.rmtree(self.root)

    def transport(self,body):
        self.calls.append(body)
        if self.transport_fail:raise TimeoutError('Synthetic transport timeout')
        raw={'scenario':getattr(self,'scenario','При изменении сроков поставки может измениться график поступлений.'),
            'next_check':'Проверьте условия оплаты затронутых договоров.',
            'bank_note':'Подтверждающая позиция банка в источниках не представлена.',
            'evidence_refs':['e0'],'exposure_status':'unknown'}
        return {'model':body['model'],'done':True,'done_reason':'stop','message':{'content':json.dumps(raw,ensure_ascii=False)}}

    def app(self):
        return build_product_app(self.root,Path('data/catalog/companies.json'),local_model=DEFAULT_LOCAL_MODEL,
            local_endpoint='http://127.0.0.1:11435',local_transport=self.transport)

    def run_analysis(self,client,role='CFO',key='local-one'):
        return client.post('/api/v1/companies/PUB_FESCO/analysis-runs',json={'role_id':role,'idempotency_key':key})

    def test_local_mode_is_real_separate_public_only_and_replays_after_restart_without_broker(self):
        with patch('backend.model_clients.BrokerModelClient.live') as broker:
            for index in range(2):
                with TestClient(self.app()) as client:
                    health=client.get('/api/v1/health').json()
                    self.assertEqual(health['mode'],'local_ollama');self.assertEqual(health['model'],'ollama')
                    status=client.get('/api/v1/data-status').json()
                    self.assertFalse(status['local_model']['hackathon_allowlisted'])
                    self.assertIsNone(status['live_operations']['daily_attempt_limit'])
                    self.assertEqual(len(client.get('/api/v1/companies').json()['items']),11)
                    result=self.run_analysis(client);self.assertEqual(result.status_code,202,result.text)
                    run=result.json();self.assertEqual(run['status'],'succeeded',run)
                    self.assertEqual(run['execution_mode'],'local_ollama')
                    if index==0:identity=run['run_id']
                    else:self.assertEqual(run['run_id'],identity)
                    card=client.get('/api/v1/insights/'+run['result_ids'][0]).json()
                    self.assertNotIn('FAKE MODEL',card['summary'])
                    self.assertTrue(all(ref in {s['source_id'] for s in card['sources']} for b in card['blocks'] for ref in b['evidence_refs']))
                    self.assertEqual(client.get('/api/v1/companies/U_135/profile').status_code,403)
            broker.assert_not_called();self.assertEqual(len(self.calls),1)

    def test_local_path_rejects_broker_or_private_inputs_without_loading_them(self):
        for arguments in [{'live':True},{'case_csv':Path('must-not-open.csv')},{'bank':Path('must-not-open.sqlite3')}]:
            with self.subTest(arguments=arguments),self.assertRaises(ValueError):
                build_product_app(self.root,Path('data/catalog/companies.json'),local_model=DEFAULT_LOCAL_MODEL,**arguments)

    def test_local_unknown_outcome_stops_new_operations_and_survives_restart(self):
        self.transport_fail=True
        with TestClient(self.app()) as client:
            run=self.run_analysis(client).json();self.assertEqual(run['status'],'unknown_outcome',run)
            self.assertTrue(client.get('/api/v1/data-status').json()['live_operations']['blocked'])
            self.assertEqual(self.run_analysis(client,'COO','second').status_code,503)
            self.assertEqual(self.run_analysis(client).json()['run_id'],run['run_id'])
        with TestClient(self.app()) as client:self.assertEqual(self.run_analysis(client,'COO','third').status_code,503)
        self.assertEqual(len(self.calls),1)

    def test_semantic_invalid_analysis_preserves_stop_after_restart_without_retry_or_card(self):
        self.scenario='Компания гарантированно потеряет 900 млн рублей.'
        for restarted in (False,True):
            with self.subTest(restarted=restarted),TestClient(self.app()) as client:
                response=self.run_analysis(client)
                self.assertEqual(response.status_code,202,response.text)
                run=response.json()
                self.assertEqual(run['status'],'failed',run)
                self.assertEqual(run['error_code'],'output_invalid',run)
                self.assertEqual(run['result_ids'],[])
                if not restarted:run_id=run['run_id']
                else:self.assertEqual(run['run_id'],run_id)
                saved=client.get('/api/v1/analysis-runs/'+run_id).json()
                self.assertEqual(saved,run)
                guard=client.get('/api/v1/data-status').json()['live_operations']
                self.assertTrue(guard['blocked'])
                self.assertEqual(guard['stop_code'],'output_invalid')
                blocked=self.run_analysis(client,'COO','new-operation-'+str(restarted))
                self.assertEqual(blocked.status_code,503,blocked.text)
                self.assertEqual(blocked.json()['error']['code'],'output_invalid')
                self.assertEqual(client.get('/api/v1/companies/PUB_FESCO/feed?role_id=CFO').json()['items'],[])
                self.assertEqual(len(self.calls),1)

    def test_local_dialogue_uses_same_client_and_durable_idempotency_without_fake_fallback(self):
        with TestClient(self.app()) as client:
            run=self.run_analysis(client).json();self.assertEqual(run['status'],'succeeded',run)
            insight=run['result_ids'][0];client.get('/api/v1/insights/'+insight)
            body={'text':'Какие условия оплаты следует проверить?','idempotency_key':'local-dialogue-one'}
            reply=client.post('/api/v1/insights/'+insight+'/messages',json=body)
            self.assertEqual(reply.status_code,200,reply.text);self.assertEqual(reply.json()['status'],'succeeded',reply.json())
            again=client.post('/api/v1/insights/'+insight+'/messages',json=body)
            self.assertEqual(again.json(),reply.json());self.assertEqual(len(self.calls),2)

    def test_previously_accepted_unsafe_local_card_is_quarantined_without_rewriting_history(self):
        self.scenario='Рост перевозок может создать дефицит средств, но также повышает вероятность получения дополнительных кредитных лимитов.'
        app=self.app()
        with TestClient(app) as client:
            # Reproduce a snapshot accepted before the probability guard existed.
            with patch('backend.analysis.pipeline.unsupported_categorical_impact',return_value=False):
                run=self.run_analysis(client).json()
            self.assertEqual(run['status'],'succeeded',run);insight=run['result_ids'][0]
            with app.state.db.connection() as conn:before=conn.execute('select payload from mvp_cards where id=?',(insight,)).fetchone()[0]
            response=client.get('/api/v1/insights/'+insight)
            self.assertEqual(response.status_code,409,response.text)
            self.assertEqual(response.json()['error']['code'],'local_output_requires_review')
            self.assertEqual(client.get('/api/v1/companies/PUB_FESCO/feed?role_id=CFO').json()['items'],[])
            with app.state.db.connection() as conn:after=conn.execute('select payload from mvp_cards where id=?',(insight,)).fetchone()[0]
            self.assertEqual(before,after);self.assertEqual(len(self.calls),1)


if __name__=='__main__':unittest.main()
