"""Server-assigned, durable operations; does not change broker counters or retry."""
from datetime import datetime, timezone
import hashlib
import json
import threading

from backend.model_clients import (BrokerModelClient,UnknownOutcome,TaskIdConflict,
    BudgetExceeded,AuthenticationFailure,InvalidRequest)
from .engine import digest


class OperationJobs:
    def __init__(self,db,client,prefix,clock=None,*,client_factory=None,status_policy=None):
        if not isinstance(prefix,str) or not 1<=len(prefix)<=45 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-' for c in prefix):
            raise InvalidRequest('Invalid operator assignment prefix')
        self.db,self.client,self.prefix=db,client,prefix
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.lock=threading.Lock()
        self.client_factory,self.status_policy=client_factory,status_policy or {}
        with db.connection() as conn:
            conn.executescript('''CREATE TABLE IF NOT EXISTS mvp_model_jobs (
                task_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,status TEXT NOT NULL,output_json TEXT);
                CREATE TABLE IF NOT EXISTS mvp_live_guard (
                assignment TEXT PRIMARY KEY,active_job TEXT,stop_code TEXT,stop_day TEXT);''')
            conn.execute('INSERT OR IGNORE INTO mvp_live_guard VALUES(?,NULL,NULL,NULL)',(prefix,))

    def _day(self):return self.clock().astimezone(timezone.utc).date().isoformat()

    def status(self):
        with self.db.connection() as conn:
            row=conn.execute('SELECT * FROM mvp_live_guard WHERE assignment=?',(self.prefix,)).fetchone()
        code=row['stop_code']
        if code=='budget_exceeded' and row['stop_day']!=self._day():code=None
        return {'blocked':bool(code or row['active_job']),'stop_code':code or ('unresolved_operation' if row['active_job'] else None),
                'daily_attempt_limit':4,'remaining_attempts':0 if code=='budget_exceeded' else None,'reset_time_msk':'03:00',**self.status_policy}

    def stop(self,code):
        with self.db.connection() as conn:
            # A daily quota snapshot must not weaken a durable unknown/auth/validation stop.
            condition=" AND (stop_code IS NULL OR stop_code='budget_exceeded')" if code=='budget_exceeded' else ''
            conn.execute('UPDATE mvp_live_guard SET stop_code=?,stop_day=? WHERE assignment=?'+condition,(code,self._day(),self.prefix))

    def for_operation(self,operation_id):
        if not isinstance(operation_id,str) or not operation_id:raise InvalidRequest('Missing durable operation identity')
        task_id=self.prefix+':'+hashlib.sha256(operation_id.encode()).hexdigest()[:40]
        registry=self
        class BoundOperation:
            def generate(self,context,correction=None):
                if correction is not None:raise TaskIdConflict('Correction is not assigned')
                return registry._generate(task_id,context)
        return BoundOperation()

    def _generate(self,task_id,context):
        fingerprint=digest(context)
        with self.lock:
            with self.db.connection() as conn:
                conn.execute('BEGIN IMMEDIATE')
                old=conn.execute('SELECT * FROM mvp_model_jobs WHERE task_id=?',(task_id,)).fetchone()
                if old:
                    if old['fingerprint']!=fingerprint:raise TaskIdConflict('Operation payload changed')
                    if old['status']=='succeeded':return json.loads(old['output_json'])
                    raise UnknownOutcome('Prior operation is terminal or unresolved; no retry')
                guard=conn.execute('SELECT * FROM mvp_live_guard WHERE assignment=?',(self.prefix,)).fetchone()
                if guard['active_job']:raise UnknownOutcome('Previous live operation is unresolved')
                if guard['stop_code']:
                    if guard['stop_code']=='budget_exceeded' and guard['stop_day']!=self._day():
                        conn.execute('UPDATE mvp_live_guard SET stop_code=NULL,stop_day=NULL WHERE assignment=?',(self.prefix,))
                    elif guard['stop_code']=='budget_exceeded':raise BudgetExceeded('Daily broker limit reached')
                    else:raise UnknownOutcome('Live assignment stopped; operator review required')
                conn.execute('INSERT INTO mvp_model_jobs VALUES(?,?,?,NULL)',(task_id,fingerprint,'unknown_outcome'))
                conn.execute('UPDATE mvp_live_guard SET active_job=? WHERE assignment=?',(task_id,self.prefix))
                conn.commit()
            client=(self.client_factory(task_id) if self.client_factory else
                    BrokerModelClient(task_id,model=self.client.model,transport=self.client._transport))
            safe_context=dict(context)
            if isinstance(safe_context.get('sources'),list):
                safe_context['sources']=[s for s in safe_context['sources'] if not str(s.get('source_id','')).startswith(('csv:payments:','csv:links:'))]
            try:
                output=client.generate(safe_context)
            except Exception as exc:
                code='budget_exceeded' if isinstance(exc,BudgetExceeded) else 'authentication_failure' if isinstance(exc,AuthenticationFailure) else 'unknown_outcome'
                with self.db.connection() as conn:
                    conn.execute('UPDATE mvp_live_guard SET active_job=NULL,stop_code=?,stop_day=? WHERE assignment=?',(code,self._day(),self.prefix))
                raise
            with self.db.connection() as conn:
                conn.execute("UPDATE mvp_model_jobs SET status='succeeded',output_json=? WHERE task_id=?",(json.dumps(output,ensure_ascii=False),task_id))
                conn.execute('UPDATE mvp_live_guard SET active_job=NULL WHERE assignment=?',(self.prefix,))
            return output
