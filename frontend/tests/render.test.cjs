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
