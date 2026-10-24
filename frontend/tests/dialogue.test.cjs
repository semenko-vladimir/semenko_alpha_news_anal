const test = require('node:test');
const assert = require('node:assert/strict');
const { executeDialogue, newDialogueEntry } = require('../../.tmp/ui-tests/out/dialogueLifecycle.js');
const { RequestError, api } = require('../../.tmp/ui-tests/out/api.js');
const result = (status='succeeded') => ({message_id:'m1',insight_id:'i1',status,blocks:[],sources:[],unknowns:[],question:null,input_version:'v2',error_code:null});
const send = (e,op,f,q='q1') => executeDialogue(e,op,q,f,()=>{});
test('double click and context return do not duplicate pending answer; success records answered question',async()=>{
 const e=newDialogueEntry();e.answer='Да';let resolve,posts=0;const f=()=>{posts++;return new Promise(r=>resolve=r)};
 const first=send(e,'answer',f);await send(e,'answer',f);assert.equal(posts,1);assert.equal(e.state,'pending');resolve(result());await first;
 e.answer='Ещё';await send(e,'answer',f);assert.equal(posts,1);assert.equal(e.answered.has('q1'),true);
});
test('network, abort, malformed response and unknown outcome preserve unknown lock with no retry',async()=>{
 for(const f of [async()=>{throw Error('network')},async()=>{throw new DOMException('abort','AbortError')},async()=>({}),async()=>result('unknown_outcome')]){
 const e=newDialogueEntry();e.message='Что изменилось?';let posts=0;const wrapped=()=>{posts++;return f()};await send(e,'message',wrapped);await send(e,'message',wrapped);
 assert.equal(e.state,'unknown');assert.equal(posts,1);assert.doesNotMatch(e.notice,/не завершён|failed/);
 }
});
test('explicit failure and rejected request differ from unknown; empty/overlong/missing question send nothing',async()=>{
 const e=newDialogueEntry();let posts=0;const f=async()=>{posts++;return result()};await send(e,'message',f);e.message='x'.repeat(2001);await send(e,'message',f);e.answer='Да';await send(e,'answer',f,null);assert.equal(posts,0);
 e.message='Вопрос';await send(e,'message',async()=>result('failed'));assert.equal(e.state,'idle');assert.match(e.notice,/не подготовлен/);
 await send(e,'message',async()=>{throw new RequestError('denied',403)});assert.equal(e.state,'idle');assert.match(e.notice,/отклонил/);
});
test('contexts keep independent drafts and late replies do not alter another context',async()=>{
 const a=newDialogueEntry(),b=newDialogueEntry();a.message='A';b.message='B';await send(a,'message',async()=>result());assert.equal(a.message,'');assert.equal(b.message,'B');assert.equal(b.result,undefined);
});
test('production API sends exact MVP003 endpoints/payload and never retries',async()=>{
 const old=global.fetch,calls=[];global.fetch=async(path,init)=>{calls.push([path,JSON.parse(init.body)]);return {ok:true,json:async()=>result()}};
 try{await api.answer('a/b','q1','Да','key1');await api.message('a/b','Вопрос','key2');assert.deepEqual(calls,[['/api/v1/insights/a%2Fb/questions',{question_id:'q1',answer:'Да',idempotency_key:'key1'}],['/api/v1/insights/a%2Fb/messages',{text:'Вопрос',idempotency_key:'key2'}]]);}finally{global.fetch=old}
});

test('malformed nested dialogue data locks as unknown without accepting result or repeating POST',async()=>{
 const invalid = [
  {blocks:[{block_id:'b1',kind:'ai_inference',text:'text'}]},
  {blocks:[{block_id:'b1',kind:'ai_inference',text:{},evidence_refs:[],assumptions:[],unknowns:[]}]},
  {sources:[{source_id:'s1',locator:{}}]},
  {sources:[{source_id:'s1',locator:'p1',title:{}}]},
  {question:{question_id:'q2',kind:'exposure_confirmation',text:'?',options:[{}]}},
  {question:undefined}, {unknowns:[{}]}, {input_version:null},
 ];
 for(const fields of invalid){
  const e=newDialogueEntry();e.message='Question';let posts=0;
  const f=async()=>{posts++;return {...result(),...fields}};
  await send(e,'message',f);await send(e,'message',f);
  assert.equal(e.state,'unknown');assert.equal(e.result,undefined);assert.equal(posts,1);assert.equal(e.message,'Question');
 }
});

test('complete nested response is accepted with question, source refs and block provenance intact',async()=>{
 const e=newDialogueEntry();e.message='Question';
 const complete={...result(),blocks:[{block_id:'b1',kind:'ai_inference',text:'Inference',evidence_refs:['s1'],assumptions:['Conditional'],unknowns:['Exposure']}],sources:[{source_id:'s1',locator:'p1',title:'Source',synthetic:false,uri:null,document_date:null}],question:{question_id:'q2',kind:'exposure_confirmation',text:'Exposure?',options:['Yes','No']}};
 await send(e,'message',async()=>complete);assert.equal(e.state,'idle');assert.deepEqual(e.result,complete);
});
