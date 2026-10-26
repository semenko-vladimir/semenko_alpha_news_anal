const test=require('node:test');const assert=require('node:assert/strict');const path=require('node:path');const fs=require('node:fs');
process.env.NODE_PATH=path.resolve(__dirname,'../node_modules');require('node:module').Module._initPaths();
const React=require('react');const {renderToStaticMarkup}=require('react-dom/server');
const file=path.resolve(__dirname,'../../.tmp/ui-tests/render/CompanyProfile.js');
const {CompanyFacts,eligibilityCopy,RuntimeNotice}=fs.existsSync(file)?require(file):{};
const render=(Component,props)=>renderToStaticMarkup(React.createElement(Component,props));
test('public profile keeps group scope, financial period, provenance and escaped content',()=>{
 assert.equal(typeof CompanyFacts,'function','Company profile presentation is missing');
 const html=render(CompanyFacts,{profile:{company_id:'PUB_TEST',display_name:'Test',profile_origin:'public',category:'ENERGY',sector_label:'Нефть и газ',legal_name:'<script>name</script>',entity_scope:'group',activity:'Oil',as_of:'2025-12-31',checked_at:'2026-10-25T10:00:00Z',eligibility_status:'confirmed',metrics:[{name:'Выручка',value:20,unit:'млрд ₽',currency:'RUB',period:'2025',scope:'group',source_id:'p1'}],unknowns:['Условия кредитов неизвестны'],sources:[{source_id:'p1',title:'Отчёт',uri:'https://www.lukoil.com/Company',locator:'Показатели',synthetic:false,trust_status:'official_publication'}]}});
 assert.match(html,/2025/);assert.match(html,/млрд ₽/);assert.match(html,/групп/);assert.match(html,/Условия кредитов неизвестны/);assert.doesNotMatch(html,/<script>/);assert.match(html,/https:\/\/www.lukoil.com/);
});
test('case profile does not invent a snapshot date or revenue eligibility',()=>{
 assert.equal(typeof CompanyFacts,'function','Company profile presentation is missing');
 const html=render(CompanyFacts,{profile:{company_id:'U_135',display_name:'Обезличенная компания U_135',profile_origin:'provided',category:'TRADE',activity:'Торговля',as_of:null,checked_at:null,metrics:[],sources:[],unknowns:['profile_snapshot_date_unknown'],eligibility_status:'unknown'}});
 assert.match(html,/Дата среза не установлена/);assert.doesNotMatch(html,/ЛУКОЙЛ|Сбер|Выручка подтверждена/);
 assert.match(html,/Исходные ограничения профиля/);assert.doesNotMatch(html.split('<details class="profile-raw-unknowns"')[0],/profile_snapshot_date_unknown/);
});
test('eligibility wording distinguishes sourced annual revenue from unknown case units',()=>{
 assert.equal(typeof eligibilityCopy,'function','Eligibility wording is missing');
 assert.match(eligibilityCopy({eligibility_status:'confirmed'}),/подтвержд/);
 assert.match(eligibilityCopy({eligibility_status:'unknown'}),/не подтверждена/);
});
test('runtime makes offline mode, source failures and exhausted quota visible without a fake fallback',()=>{
 assert.equal(typeof RuntimeNotice,'function','Runtime status presentation is missing');
 const html=render(RuntimeNotice,{status:{news_count:22,source_errors:3,execution_mode:'broker_live',latest_successful_fetch:'2026-10-25T10:00:00Z',live_operations:{blocked:true,stop_code:'budget_exceeded',daily_attempt_limit:4,remaining_attempts:0,reset_time_msk:'03:00'}}});
 assert.match(html,/исчерпана/);assert.match(html,/03:00/);assert.match(html,/3/);assert.doesNotMatch(html,/переключены на.*fake/i);
 const offline=render(RuntimeNotice,{status:{news_count:0,source_errors:0,execution_mode:'mvp_offline',latest_successful_fetch:null,live_operations:null}});
 assert.match(offline,/тестовая модель/);
});

test('source errors report an update failure without claiming the publisher is unavailable',()=>{
 const failed=render(RuntimeNotice,{status:{news_count:22,source_errors:1,execution_mode:'mvp_offline',latest_successful_fetch:'2026-10-25T10:00:00Z',live_operations:null,
  official_sources:[{source_id:'lukoil',status:'error',last_success_at:'2026-10-24T10:00:00Z'}]}});
 assert.match(failed,/Источников с ошибками: 1/);
 assert.match(failed,/ЛУКОЙЛ · ошибка обновления/);
 assert.match(failed,/последняя загрузка/);
 assert.doesNotMatch(failed,/Недоступных источников|ЛУКОЙЛ · недоступен/);
 const healthy=render(RuntimeNotice,{status:{news_count:22,source_errors:0,execution_mode:'mvp_offline',latest_successful_fetch:null,live_operations:null,
  official_sources:[{source_id:'lukoil',status:'ok',last_success_at:null}]}});
 assert.match(healthy,/ЛУКОЙЛ · данные получены/);
 assert.doesNotMatch(healthy,/Источников с ошибками:/);
});
test('local server mode names the real model and separates experiment eligibility from broker quota',()=>{
 const html=render(RuntimeNotice,{status:{news_count:26,source_errors:2,execution_mode:'local_ollama',latest_successful_fetch:null,
  local_model:{name:'hf.co/unsloth/Qwen3.5-4B-GGUF:Q6_K',display_name:'Qwen 3.5 4B',hackathon_allowlisted:false},
  live_operations:{blocked:false,stop_code:null,daily_attempt_limit:null,remaining_attempts:null,reset_time_msk:null}}});
 assert.match(html,/Qwen 3.5 4B/);assert.match(html,/Локальная LLM/);assert.match(html,/разрешённом списке/);
 assert.doesNotMatch(html,/тестовая модель|4 запроса|03:00|FAKE MODEL/);
});
test('a rejected local output explains the persistent stop instead of reporting a daily quota',()=>{
 const html=render(RuntimeNotice,{status:{news_count:26,source_errors:0,execution_mode:'local_ollama',latest_successful_fetch:null,
  local_model:{name:'local-model',display_name:'Qwen 3.5 4B',hackathon_allowlisted:false},
  live_operations:{blocked:true,stop_code:'output_invalid',daily_attempt_limit:null,remaining_attempts:null,reset_time_msk:null}}});
 assert.match(html,/не прошедший проверку/);assert.match(html,/остановлены/);assert.doesNotMatch(html,/квота исчерпана|03:00/);
});
