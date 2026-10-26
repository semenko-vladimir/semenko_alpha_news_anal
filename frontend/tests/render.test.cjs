const test = require('node:test');
const assert = require('node:assert/strict');
process.env.NODE_PATH = require('node:path').resolve(__dirname, '../node_modules');
require('node:module').Module._initPaths();
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { Sources } = require('../../.tmp/ui-tests/render/Sources.js');
const { Dialogue } = require('../../.tmp/ui-tests/render/Dialogue.js');
const { newDialogueEntry } = require('../../.tmp/ui-tests/render/dialogueLifecycle.js');
const App = require('../../.tmp/ui-tests/render/App.js').default;
const { TraceDetails } = require('../../.tmp/ui-tests/render/App.js');
const { BusinessUnknowns } = require('../../.tmp/ui-tests/render/App.js');
const render=(component,props)=>renderToStaticMarkup(React.createElement(component,props));
test('production source rendering preserves real/synthetic/unknown distinction, dates, locator and escapes source instructions',()=>{
 const html=render(Sources,{sources:[{source_id:'real',locator:'стр. 3',synthetic:false,title:'<script>execute()</script>',uri:'javascript:alert(1)',document_date:'2020-01-01T00:00:00Z'},{source_id:'fake',locator:'fixture',synthetic:true,title:'Fixture'},{source_id:'missing',locator:'п. 2'}]});
 assert.match(html,/Реальный локальный материал/);assert.match(html,/Синтетический источник/);assert.match(html,/Происхождение не указано/);assert.match(html,/2020/);assert.match(html,/стр. 3/);assert.match(html,/Опубликован: неизвестна/);assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script|href="javascript/);
});
test('production dialogue renders question, options, scoped drafts and unknown state without reporting a failure',()=>{
 const entry=newDialogueEntry();entry.answer='Да';entry.message='Контекст CFO';entry.state='unknown';entry.notice='Ответ не получен; результат отправки неизвестен.';
 const html=render(Dialogue,{entry,insight:{insight_id:'i1',question:{question_id:'q1',kind:'exposure_confirmation',text:'Есть связь?',options:['Да','Нет']},sources:[]}});
 assert.match(html,/Есть связь\?/);assert.match(html,/Контекст CFO/);assert.match(html,/disabled/);assert.match(html,/результат отправки неизвестен/);assert.doesNotMatch(html,/failed|Уточнения и вопросы появятся/);
});
test('production shell shows local authorization and eligibility uncertainty without falsely labelling all data synthetic',()=>{
 const html=render(App,{});assert.match(html,/Локальный демонстрационный доступ/);assert.match(html,/единицы выручки требуют проверки/);assert.doesNotMatch(html,/данные синтетические|22 октября 2026/);
});
test('trace is scoped to current company and role and loading failure leaves a safe explanation',()=>{
 const stale=render(TraceDetails,{trace:{context:'["U_135","CFO"]',value:null,error:false},companyId:'U_42266',role:'CFO'});
 assert.equal(stale,'');
 const failed=render(TraceDetails,{trace:{context:'["U_42266","COO"]',value:null,error:true},companyId:'U_42266',role:'COO'});
 assert.match(failed,/Как подготовлен анализ/);assert.match(failed,/Этапы анализа пока недоступны/);assert.doesNotMatch(failed,/FAKE|mvp_offline|backend|sqlite/);
});

test('trace implementation reason codes render only as concise Russian explanations',()=>{
 const html=render(TraceDetails,{trace:{context:'["U_42266","COO"]',error:false,value:{run_id:'r',company_id:'U_42266',role_id:'COO',status:'succeeded',execution_mode:'mvp_offline',trace:[{stage:'validate',status:'rejected',reason:'evidence_reference_unresolved'}],features:[]}},companyId:'U_42266',role:'COO'});
 assert.match(html,/Подтверждение не найдено/);assert.doesNotMatch(html,/evidence_reference_unresolved/);
});

test('business caveats summarize plainly and disclosure preserves deduplicated raw and block-only unknowns',()=>{
 const repeated='impact_unknown: экспозиция не подтверждена';
 const rawCode='new_limit_code: source mapping absent';
 const html=render(BusinessUnknowns,{unknowns:[repeated,repeated,'Единицы выручки не указаны',rawCode],blocks:[{kind:'ai_inference',unknowns:[repeated,'Сроки поставки неизвестны'],assumptions:['Условия аванса могут измениться']},{kind:'bank_position',unknowns:['Позиция банка не найдена'],assumptions:[]} ]});
 assert.match(html,/Масштаб и связь влияния с компанией не установлены/);assert.match(html,/Единицы и значения показателей требуют проверки/);assert.match(html,/Позиция банка не найдена или требует проверки/);
 assert.match(html,/new_limit_code: source mapping absent/);assert.match(html,/Дополнительные ограничения требуют проверки/);assert.match(html,/Относится к: Общая оговорка, Сценарий AI/);assert.match(html,/Сроки поставки неизвестны/);assert.match(html,/Позиция банка не найдена/);assert.match(html,/Условия аванса могут измениться/);
 assert.equal((html.match(/impact_unknown: экспозиция не подтверждена/g)||[]).length,1);
});
test('many mixed caveats stay within six business summaries while raw codes and every origin remain disclosed',()=>{
 const raws=['register_location_is_not_operating_geography','missing:OPFS_NAME','mvp_offline','trade/payments/volume_is_not_market_share','payment_does_not_prove_delivery','currency_unknown','credit_rate_unknown','bank_search_not_found','period_unavailable','exposure_unknown','raw_unknown_1','raw_unknown_2','raw_unknown_3','Единицы выручки не указаны','Город регистрации не подтверждает географию деятельности','Платёж не доказывает поставку','Кредит не определяет ставку','Позиция банка не найдена','Период наблюдения неизвестен','Контрагент не подтверждает контроль','Документ имеет синтетический статус','Дополнительное условие требует проверки'];
 const blocks=raws.map((raw,index)=>({kind:index%3===0?'observed_fact':index%3===1?'bank_position':'ai_inference',block_id:`b${index}`,unknowns:[raw],assumptions:[]}));
 const html=render(BusinessUnknowns,{unknowns:raws,blocks});
 const summary=html.split('<details')[0];
 assert.equal((summary.match(/<li>/g)||[]).length,6);
 for(const raw of raws)assert.ok(html.includes(raw),`missing disclosed raw caveat: ${raw}`);
 for(let i=0;i<raws.length;i++)assert.ok(html.includes(`(${`b${i}`})`),`missing provenance b${i}`);
 for(const code of raws.slice(0,13))assert.ok(!summary.includes(code),`raw service code leaked into summary: ${code}`);
 assert.match(summary,/География регистрации не определяет географию бизнеса/);
 assert.match(summary,/Позиция банка не найдена или требует проверки/);
 assert.match(html,/Документ имеет синтетический статус/);
});
test('critical caveats take priority, missing profile fields stay neutral, and delivery timing is not a payment claim',()=>{
 const raws=['missing:REG_DATE','missing:OPFS_NAME','registration_code_unknown','payment_code_unknown','contractor_code_unknown','other_code_unknown','another_unknown','yet_another_unknown','Экспозиция по компании неизвестна','Единицы выручки неизвестны','Банковские ограничения требуют проверки'];
 const blocks=raws.map((raw,index)=>({kind:index%2?'ai_inference':'observed_fact',block_id:`p${index}`,unknowns:[raw],assumptions:[]}));
 const html=render(BusinessUnknowns,{unknowns:raws,blocks});const summary=html.split('<details')[0];
 assert.equal((summary.match(/<li>/g)||[]).length,6);
 assert.ok(summary.indexOf('Экспозиция') < summary.indexOf('Единицы'));
 assert.ok(summary.indexOf('Единицы') < summary.indexOf('Позиция банка'));
 assert.match(summary,/В профиле отсутствуют отдельные сведения/);
 assert.doesNotMatch(summary,/Единицы и значения.*missing/);
 for(const raw of raws)assert.ok(html.includes(raw),`missing raw: ${raw}`);
 for(let i=0;i<raws.length;i++)assert.ok(html.includes(`(p${i})`),`missing provenance p${i}`);
 const delivery=render(BusinessUnknowns,{unknowns:['Сроки поставки неизвестны'],blocks:[]});
 assert.match(delivery,/Период и сроки требуют уточнения/);assert.doesNotMatch(delivery,/Платежи.*подтверждают поставку/);
});
test('empty caveats remain explicit without inventing raw unknowns',()=>{
 const html=render(BusinessUnknowns,{unknowns:[],blocks:[]});assert.match(html,/влияние события на компанию требует проверки/);assert.match(html,/Показать исходные оговорки и ограничения \(0\)/);assert.match(html,/Исходных оговорок нет/);
});

test('close and reopen pending dialogue receives completion and renders success or unknown without duplicate POST',async()=>{
 // Hook harness verifies production subscription/callback wiring; not browser E2E.
 const {api}=require('../../.tmp/ui-tests/render/api.js');
 for(const status of ['succeeded','unknown_outcome']){
  const original={useState:React.useState,useEffect:React.useEffect,useRef:React.useRef};
  const oldMessage=api.message,cleanups=[],redraws={old:0,reopened:0};let instance='old',resolve,posts=0;
  const entry=newDialogueEntry();entry.message='Question';const insight={insight_id:'i1',question:null,sources:[]};
  try{
   React.useState=()=>{const owner=instance;return [0,()=>redraws[owner]++]};
   React.useRef=value=>({current:value});React.useEffect=f=>cleanups.push(f());
   api.message=()=>{posts++;return new Promise(r=>resolve=r)};
   const tree=Dialogue({insight,entry});tree.props.children.find(c=>c&&c.type==='form').props.onSubmit({preventDefault(){}});
   assert.equal(entry.state,'pending');cleanups.splice(0).forEach(f=>f?.());
   instance='reopened';Dialogue({insight,entry});const before=redraws.reopened;
   resolve({message_id:'m1',insight_id:'i1',status,blocks:[],sources:[],unknowns:[],question:null,input_version:'v2',error_code:null});
   await new Promise(r=>setImmediate(r));assert.ok(redraws.reopened>before);assert.equal(posts,1);
  }finally{cleanups.forEach(f=>f?.());Object.assign(React,original);api.message=oldMessage}
  const html=render(Dialogue,{insight,entry});
  if(status==='succeeded'){assert.equal(entry.state,'idle');assert.match(html,/Ответ получен/);assert.doesNotMatch(html,/<textarea[^>]*disabled/)}
  else{assert.equal(entry.state,'unknown');assert.match(html,/Повторная отправка/);assert.match(html,/<textarea[^>]*disabled/)}
 }
});

test('incomplete API block renders safe unknown notice rather than crashing production dialogue',async()=>{
 const {executeDialogue}=require('../../.tmp/ui-tests/render/dialogueLifecycle.js');
 const entry=newDialogueEntry();entry.message='Question';
 await executeDialogue(entry,'message',undefined,async()=>({message_id:'m1',insight_id:'i1',status:'succeeded',blocks:[{block_id:'b1',kind:'ai_inference',text:'text'}],sources:[],unknowns:[],question:null,input_version:'v2',error_code:null}));
 const html=render(Dialogue,{entry,insight:{insight_id:'i1',question:null,sources:[]}});
 assert.equal(entry.state,'unknown');assert.match(html,/результат отправки неизвестен/);assert.match(html,/<textarea[^>]*disabled/);assert.doesNotMatch(html,/Ответ получен/);
});
