const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
process.env.NODE_PATH = path.resolve(__dirname,'../node_modules');
require('node:module').Module._initPaths();
const React = require('react');
const {renderToStaticMarkup} = require('react-dom/server');
const compiledFeed=path.resolve(__dirname,'../../.tmp/ui-tests/render/InsightFeed.js');
const {filterInsights, InsightFeed} = require('node:fs').existsSync(compiledFeed) ? require(compiledFeed) : {};
const {Dialogue} = require('../../.tmp/ui-tests/render/Dialogue.js');
const {newDialogueEntry} = require('../../.tmp/ui-tests/render/dialogueLifecycle.js');
const render = (Component,props) => renderToStaticMarkup(React.createElement(Component,props));
const items = [
 {insight_id:'unknown',headline:'Расчёты за перевозки',summary:'Сроки и платежи',exposure_status:'unknown',created_at:'2026-10-25T10:00:00Z',synthetic:true},
 {insight_id:'confirmed',headline:'Стоимость финансирования',summary:'Расчеты по договору',exposure_status:'confirmed',created_at:'2026-10-25T10:00:00Z',synthetic:false},
 {insight_id:'conditional',headline:'Поставки',summary:'Условный сценарий',exposure_status:'conditional',created_at:'2026-10-25T10:00:00Z',synthetic:true},
 {insight_id:'contradicted',headline:'Иное событие',summary:'Связь опровергнута',exposure_status:'contradicted',created_at:'2026-10-25T10:00:00Z',synthetic:false},
];
test('search handles Russian case, ё and multiple words without changing ranked order',()=>{
 assert.equal(typeof filterInsights,'function','production feed filtering is not implemented');
 assert.deepEqual(filterInsights(items,'  РАСЧЕТЫ  ','all').map(x=>x.insight_id),['unknown','confirmed']);
 assert.deepEqual(filterInsights(items,'платежи перевозки','all').map(x=>x.insight_id),['unknown']);
 assert.deepEqual(filterInsights(items,'','all'),items);
});
test('filters use actual exposure and exclude contradicted events from unconfirmed selection',()=>{
 assert.equal(typeof filterInsights,'function','production feed filtering is not implemented');
 assert.deepEqual(filterInsights(items,'','confirmed').map(x=>x.insight_id),['confirmed']);
 assert.deepEqual(filterInsights(items,'','unconfirmed').map(x=>x.insight_id),['unknown','conditional']);
 assert.deepEqual(filterInsights(items,'финанс','unconfirmed'),[]);
 assert.deepEqual(items.map(x=>x.insight_id),['unknown','confirmed','conditional','contradicted']);
});
test('feed presents usable search, accessible filters and real provenance without invented impact levels',()=>{
 assert.equal(typeof InsightFeed,'function','production feed presentation is not implemented');
 const html=render(InsightFeed,{items,selectedId:null,loading:false,showEmpty:true,onSelect(){}});
 assert.match(html,/type="search"/);assert.match(html,/aria-pressed="true"/);
 assert.match(html,/Синтетические данные/);assert.match(html,/Материалы с указанными источниками/);
 assert.doesNotMatch(html,/Высокое влияние|Среднее влияние|Низкое влияние/);
 assert.match(html,/aria-labelledby="feed-heading"/);
});
test('assistant examples remain local drafts and respect pending or unknown lock',()=>{
 for(const state of ['pending','unknown']) {
  const entry=newDialogueEntry();entry.state=state;
  const html=render(Dialogue,{entry,insight:{insight_id:'i1',question:null,sources:[]}});
  const suggestions=html.match(/<div class="question-suggestions"[\s\S]*?<\/div>/)?.[0] ?? '';
  assert.equal((suggestions.match(/<button[^>]*disabled/g)||[]).length,3);
  assert.match(html,/AI-ассистент/);
 }
 const originalState=React.useState,originalEffect=React.useEffect,originalRef=React.useRef;
 const entry=newDialogueEntry();const initialResult=entry.result;
 try{
  React.useState=()=>[0,()=>{}];React.useEffect=()=>{};React.useRef=()=>({current:null});
  const tree=Dialogue({entry,insight:{insight_id:'i1',question:null,sources:[]}});
  const suggestions=tree.props.children.find(x=>x?.props?.className==='question-suggestions');
  suggestions.props.children[0].props.onClick();
  assert.equal(entry.message,'Как это может повлиять на наши расходы?');
  assert.equal(entry.state,'idle');assert.equal(entry.result,initialResult);
 }finally{React.useState=originalState;React.useEffect=originalEffect;React.useRef=originalRef;}
});
