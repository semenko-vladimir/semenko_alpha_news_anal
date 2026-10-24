const test = require('node:test');
const assert = require('node:assert/strict');
const { executeRun, isRunDisabled, runSuccessMessage } = require('../../.tmp/ui-tests/out/runLifecycle.js');
const { formatSourceDate, safeSourceHref, sourceDateLabels } = require('../../.tmp/ui-tests/out/sourcePresentation.js');
const run = (status, id = 'r1') => ({ run_id: id, status });
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const immediate = async () => {};
test('pending context stays unavailable after switch and return; other pair works', async () => {
 const attempts = new Map(), gate = deferred(); let posts = 0;
 const api = { run: async company => { posts++; return company === 'A' ? gate.promise : run('succeeded'); } };
 const save = (pair, a) => a ? attempts.set(pair, a) : attempts.delete(pair);
 const first = executeRun(api,'A','CFO','k1',new AbortController().signal,a=>save('A:CFO',a),immediate);
 assert.equal(isRunDisabled(true,false,attempts.get('A:CFO')),true); assert.equal(isRunDisabled(true,false,attempts.get('B:CFO')),false);
 await executeRun({run:async()=>({...run('succeeded'),execution_mode:'deterministic_demo'})},'B','CFO','k2',new AbortController().signal,()=>{},immediate);
 assert.equal(isRunDisabled(true,false,attempts.get('A:CFO')),true); assert.equal(posts,1);
 gate.resolve({...run('failed'),execution_mode:'deterministic_demo'}); assert.deepEqual(await first,{status:'failed',execution_mode:'deterministic_demo'}); assert.equal(isRunDisabled(true,false,attempts.get('A:CFO')),false);
});
test('agentic fixture is disclosed as test-model analysis and deterministic demo is explicit',()=>{
 assert.equal(runSuccessMessage('agentic_fixture'),'Анализ на тестовой модели.');
 assert.equal(runSuccessMessage('deterministic_demo'),'Демонстрационный анализ · синтетические данные.');
});
test('unknown, missing company and busy disable launch',()=>{ assert.equal(isRunDisabled(true,false,{key:'k',status:'unknown'}),true); assert.equal(isRunDisabled(false,false),true); assert.equal(isRunDisabled(true,true),true); });
test('network rejection and abort retain originating pair as unknown',async t=>{ for(const mode of ['network','abort']) await t.test(mode,async()=>{
 const c=new AbortController(), states=[]; const api={run:()=>new Promise((resolve,reject)=>{if(mode==='network')reject(Error('offline'));else{c.abort();reject(Error('aborted'));}})};
 assert.deepEqual(await executeRun(api,'A','COO','k',c.signal,a=>states.push(a),immediate),{status:'unknown'}); assert.deepEqual(states.at(-1),{key:'k',status:'unknown'}); assert.equal(isRunDisabled(true,false,states.at(-1)),true);
}); });
test('terminal releases lock; unknown outcome and polling error retain it',async()=>{
 for(const terminal of ['succeeded','failed']){let gets=0,saved={key:'k',status:'pending'};const api={run:async()=>run('queued'),getRun:async()=>{gets++;return run(terminal)}};
 assert.deepEqual(await executeRun(api,'A','CFO','k',new AbortController().signal,a=>saved=a,immediate),{status:terminal,execution_mode:undefined});assert.equal(gets,1);assert.equal(saved,null);assert.equal(isRunDisabled(true,false,saved),false);}
 for(const mode of ['unknown','get-error']){const states=[],api={run:async()=>run(mode==='unknown'?'unknown_outcome':'running'),getRun:async()=>{throw Error('poll failed')}};
 assert.deepEqual(await executeRun(api,'A','CFO','key-A',new AbortController().signal,a=>states.push(a),immediate),{status:'unknown'});assert.deepEqual(states.at(-1),{key:'key-A',status:'unknown'});assert.equal(isRunDisabled(true,false,states.at(-1)),true);}
});
test('late result updates originating pair only and leaves other pair intact',async()=>{
 const gate=deferred(), attempts=new Map();const first=executeRun({run:()=>gate.promise},'A','COO','kA',new AbortController().signal,a=>a?attempts.set('A:COO',a):attempts.delete('A:COO'),immediate);
 attempts.set('B:COO',{key:'kB',status:'unknown'});gate.resolve(run('unknown_outcome'));assert.deepEqual(await first,{status:'unknown'});assert.equal(attempts.get('A:COO').key,'kA');assert.deepEqual(attempts.get('B:COO'),{key:'kB',status:'unknown'});
});
test('source links allow only valid HTTP(S) URLs; fixture, malformed and script URLs stay plain text',()=>{
 assert.equal(safeSourceHref('https://example.test/source?id=1'),'https://example.test/source?id=1');
 assert.equal(safeSourceHref('http://example.test'),'http://example.test/');
 for(const uri of [null,'fixture:event-1','javascript:alert(1)','data:text/html,hi','not a url']) assert.equal(safeSourceHref(uri),null);
});
test('source dates stay distinct and missing or invalid dates are explicitly unknown',()=>{
 assert.equal(formatSourceDate(null),'неизвестна');
 assert.equal(formatSourceDate('invalid-date'),'неизвестна');
 const labels=sourceDateLabels({published_at:null,document_date:'bad',retrieved_at:'2026-10-22T12:00:00Z'});
 assert.deepEqual(labels.map(([label])=>label),['Опубликован','Дата документа','Получен']);
 assert.equal(labels[0][1],'неизвестна');assert.equal(labels[1][1],'неизвестна');assert.match(labels[2][1],/22 октября 2026/);
});

test('offline and live labels distinguish test model from live generation',()=>{
 assert.match(runSuccessMessage('mvp_offline'),/тестовая модель, без живой LLM/);
 assert.match(runSuccessMessage('broker_live'),/живой моделью/);
});
test('malformed run response never becomes a successful analysis',async()=>{
 const states=[];
 assert.deepEqual(await executeRun({run:async()=>({})},'A','CFO','k',new AbortController().signal,a=>states.push(a),immediate),{status:'unknown'});
 assert.equal(states.at(-1).status,'unknown');
});
