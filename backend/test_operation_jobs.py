import json
import unittest
from datetime import datetime,timezone,timedelta
from backend import test_mvp_trace as trace_fixture
from backend.model_clients import BrokerModelClient,UnknownOutcome,TaskIdConflict,BudgetExceeded
try:
    from backend.mvp.operation_jobs import OperationJobs
except ImportError:
    OperationJobs=None


class OperationJobTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(OperationJobs),'Durable operation-level broker jobs are not implemented')
        self.fixture=trace_fixture.MVPTraceTests('test_trace_is_read_only_whitelisted_and_pinned');self.fixture.setUp()
        self.calls=[];self.fail=None
        def transport(job):
            self.calls.append(dict(job))
            if self.fail=='unknown':raise UnknownOutcome('test unknown')
            if self.fail=='budget':return json.dumps({**{k:job[k] for k in ['task_id','model']},'status':'error','error':'daily_attempt_limit'})
            return json.dumps({**{k:job[k] for k in ['task_id','model']},'status':'ok','content':json.dumps({'kind':'ai_inference','claim':'Может потребоваться проверка','evidence_refs':['e1'],'exposure_status':'unknown','bank_source_id':None})})
        self.base=BrokerModelClient('FINAL-TEST',transport=transport)
        self.jobs=OperationJobs(self.fixture.app.state.db,self.base,'FINAL-TEST')

    def tearDown(self):
        if hasattr(self,'fixture'):self.fixture.tearDown()

    def test_distinct_operations_and_restart_replay_have_stable_ids_without_duplicate_transport(self):
        first=self.jobs.for_operation('run-1').generate({'test':'one'})
        other=OperationJobs(self.fixture.app.state.db,self.base,'FINAL-TEST')
        self.assertEqual(other.for_operation('run-1').generate({'test':'one'}),first)
        other.for_operation('run-2').generate({'test':'two'})
        self.assertEqual(len(self.calls),2);self.assertNotEqual(self.calls[0]['task_id'],self.calls[1]['task_id'])
        with self.assertRaises(TaskIdConflict):other.for_operation('run-1').generate({'test':'changed'})
        self.assertEqual(len(self.calls),2)

    def test_unknown_outcome_stops_other_operations_and_survives_restart(self):
        self.fail='unknown'
        with self.assertRaises(UnknownOutcome):self.jobs.for_operation('run-1').generate({'test':'one'})
        other=OperationJobs(self.fixture.app.state.db,self.base,'FINAL-TEST')
        with self.assertRaises(UnknownOutcome):other.for_operation('run-2').generate({'test':'two'})
        self.assertEqual(len(self.calls),1)

    def test_budget_stop_releases_only_at_natural_utc_day_change(self):
        day=datetime(2026,10,25,23,0,tzinfo=timezone.utc)
        self.jobs.clock=lambda:day;self.fail='budget'
        with self.assertRaises(BudgetExceeded):self.jobs.for_operation('run-1').generate({'test':'one'})
        with self.assertRaises(BudgetExceeded):self.jobs.for_operation('run-2').generate({'test':'two'})
        self.assertEqual(len(self.calls),1)
        day+=timedelta(days=1);self.fail=None
        self.jobs.for_operation('run-2').generate({'test':'two'});self.assertEqual(len(self.calls),2)
        with self.assertRaises(UnknownOutcome):self.jobs.for_operation('run-1').generate({'test':'one'})

    def test_quota_snapshot_cannot_replace_unresolved_stop_on_restart(self):
        day=datetime(2026,10,25,23,0,tzinfo=timezone.utc)
        self.jobs.clock=lambda:day;self.fail='unknown'
        with self.assertRaises(UnknownOutcome):self.jobs.for_operation('run-1').generate({'test':'one'})
        restarted=OperationJobs(self.fixture.app.state.db,self.base,'FINAL-TEST',clock=lambda:day)
        restarted.stop('budget_exceeded') # A safe startup status reports today's quota at zero.
        day+=timedelta(days=1)
        self.assertEqual(restarted.status()['stop_code'],'unknown_outcome')
        with self.assertRaises(UnknownOutcome):restarted.for_operation('run-2').generate({'test':'two'})
        self.assertEqual(len(self.calls),1)


if __name__=='__main__':unittest.main()
