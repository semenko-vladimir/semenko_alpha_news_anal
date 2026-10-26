import { useCallback, useEffect, useRef, useState } from 'react';
import { api, type Company, type Insight, type InsightSummary, type RoleId, type RunTrace, type DataStatus } from './api';
import { executeRun, isRunDisabled, runSuccessMessage, waitForPoll, type Attempt } from './runLifecycle';
import { formatSourceDate } from './sourcePresentation';
import { Dialogue } from './Dialogue';
import { Sources } from './Sources';
import { newDialogueEntry, type DialogueEntry } from './dialogueLifecycle';
import { InsightFeed } from './InsightFeed';
import { UiIcon } from './UiIcon';
import { CompanyProfilePanel, RuntimeNotice, eligibilityCopy, sectorLabel } from './CompanyProfile';

const roles: { id: RoleId; label: string; hint: string }[] = [
  { id: 'CFO', label: 'Финансовому директору', hint: 'Финансы и расчёты' },
  { id: 'COO', label: 'Операционному директору', hint: 'Процессы и мощности' },
];
const date = formatSourceDate;
const bankStatus: Record<string, string> = { found: 'Найдена в датированном документе', historical: 'Историческая позиция · не текущее мнение', not_found: 'Подходящий документ не найден', not_checked: 'Не проверялась' };
const stageLabel: Record<string, string> = { plan: 'Планирование', tool: 'Сбор источников', relevance: 'Оценка тематической связи', model: 'Модель', validate: 'Проверка результата', adapt: 'Корректировка' };
const traceSourceKind: Record<string, string> = { news: 'Новости', bank_document: 'Документ банка', company_profile: 'Профиль компании', user_input: 'Сообщение пользователя', company: 'Записи о компании', payment: 'Платёжные записи', link: 'Связи' };
const reasonText: Record<string, string> = { topic_only_exposure_unknown: 'Тема связана с профилем, фактическая связь не подтверждена', no_retry: 'Повторный запрос не выполнялся', no_correction_assignment: 'Корректировка не назначалась', output_invalid: 'Ответ не прошёл проверку', timeout: 'Истекло время ожидания', unknown_outcome: 'Результат запроса неизвестен', budget_exceeded: 'Дневной лимит исчерпан', authentication_failure: 'Не удалось подтвердить доступ', model_failure: 'Модель не вернула результат', output_not_object: 'Ответ имеет неверный формат', schema_fields_invalid: 'Поля ответа не прошли проверку', kind_invalid: 'Тип вывода не поддерживается', claim_invalid: 'Формулировка вывода не прошла проверку', claim_size_limit: 'Вывод превышает допустимый размер', evidence_refs_invalid: 'Ссылки на подтверждения некорректны', evidence_refs_limit: 'Слишком много ссылок на подтверждения', fact_evidence_required: 'Для факта требуется подтверждение', evidence_reference_unresolved: 'Подтверждение не найдено', exposure_status_invalid: 'Статус связи некорректен', exposure_not_supported: 'Связь не подтверждена источниками', bank_attribution_unverified: 'Позиция банка не подтверждена', unexpected_bank_attribution: 'Неподтверждённая позиция банка исключена', unsupported_categorical_impact: 'Влияние не подтверждено', output_size_limit: 'Ответ превышает допустимый размер' };

export default function App() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [runtime, setRuntime] = useState<DataStatus | null>(null);
  const [companyId, setCompanyId] = useState('');
  const [role, setRole] = useState<RoleId>('CFO');
  const [feed, setFeed] = useState<InsightSummary[]>([]);
  const [selected, setSelected] = useState<Insight | null>(null);
  const detailPanel = useRef<HTMLElement>(null);
  const dialogues = useRef(new Map<string, DialogueEntry>());
  const detailGeneration = useRef(0);
  const [detailEpoch, setDetailEpoch] = useState(0);
  const [detailState, setDetailState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [modes, setModes] = useState(new Map<string, string>());
  const [trace, setTrace] = useState<{ context: string; value: RunTrace | null; error: boolean } | null>(null);
  const [feedLoading, setFeedLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const attempts = useRef(new Map<string, Attempt>());
  const [attemptContexts, setAttemptContexts] = useState<Map<string, Attempt>>(() => new Map());
  const generation = useRef(0);
  const active = useRef<AbortController | null>(null);
  const traceController = useRef<AbortController | null>(null);
  const traceGeneration = useRef(0);
  const traceTimeout = useRef<number | null>(null);
  const saveAttempt = (key: string, attempt: Attempt | null) => {
    if (attempt) attempts.current.set(key, attempt); else attempts.current.delete(key);
    setAttemptContexts(new Map(attempts.current));
  };
  const refresh = useCallback(async (id: string, selectedRole: RoleId, token: number) => {
    const controller = new AbortController(); active.current = controller;
    setFeedLoading(true);
    try {
      const data = await api.feed(id, selectedRole, controller.signal);
      if (generation.current === token) { setFeed(data.items); }
    } catch (e) { if (!controller.signal.aborted && generation.current === token) setNotice(errorText(e)); }
    finally { if (generation.current === token) setFeedLoading(false); }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    api.companies(controller.signal).then(({ items }) => {
      if (controller.signal.aborted) return;
      setCompanies(items); setCompanyId(items.find(item => item.company_id === 'PUB_MAGNIT')?.company_id ?? items[0]?.company_id ?? '');
      if (!items.length) setNotice('Нет доступных демонстрационных компаний.');
    }).catch(e => { if (!controller.signal.aborted) setNotice(errorText(e)); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController(); let timer: number | undefined;
    const read = async () => {
      try { const value = await api.dataStatus(controller.signal); if (!controller.signal.aborted) setRuntime(value); }
      catch { /* Leave the last known status visible; this request never starts analysis. */ }
      finally { if (!controller.signal.aborted) timer = window.setTimeout(read, 30_000); }
    };
    void read();
    return () => { controller.abort(); if (timer !== undefined) window.clearTimeout(timer); };
  }, []);

  useEffect(() => {
    if (!companyId) return;
    const token = ++generation.current; active.current?.abort();
    detailGeneration.current++; traceGeneration.current++; traceController.current?.abort(); setSelected(null); setDetailState('idle'); setFeed([]); setBusy(false); setNotice(''); setTrace(null);
    void refresh(companyId, role, token);
    return () => { traceGeneration.current++; active.current?.abort(); traceController.current?.abort(); if (traceTimeout.current !== null) window.clearTimeout(traceTimeout.current); traceTimeout.current = null; };
  }, [companyId, role, refresh]);

  useEffect(() => {
    if (!selected || selected.company_id !== companyId || selected.role_id !== role) return;
    const controller = new AbortController(); const token = generation.current; const detailToken = detailGeneration.current;
    setDetailState('loading');
    api.insight(selected.insight_id, controller.signal).then(data => {
      if (!controller.signal.aborted && generation.current === token && detailGeneration.current === detailToken) { setSelected(data); setDetailState('ready'); }
    }).catch(e => { if (!controller.signal.aborted && generation.current === token && detailGeneration.current === detailToken) { setNotice(errorText(e)); setDetailState('error'); } });
    return () => controller.abort();
  }, [selected?.insight_id, companyId, role, detailEpoch]);

  const choose = (id: string) => { generation.current++; traceGeneration.current++; traceController.current?.abort(); active.current?.abort(); detailGeneration.current++; setSelected(null); setDetailState('idle'); setFeed([]); setBusy(false); setNotice(''); setTrace(null); setCompanyId(id); };
  useEffect(() => {
    if (selected && window.matchMedia('(max-width: 900px)').matches) {
      detailPanel.current?.focus({ preventScroll: true });
      detailPanel.current?.scrollIntoView({ block: 'start' });
    }
  }, [selected?.insight_id]);
  const closeInsight = () => {
    detailGeneration.current++; setSelected(null); setDetailState('idle');
    window.requestAnimationFrame(() => document.getElementById('feed-heading')?.focus());
  };
  const runAnalysis = async () => {
    const pair = contextKey(companyId, role);
    if (!companyId || busy || attempts.current.has(pair)) return;
    const token = generation.current; active.current?.abort(); const controller = new AbortController(); active.current = controller;
    const idempotencyKey = crypto.randomUUID();
    traceController.current?.abort(); if (traceTimeout.current !== null) window.clearTimeout(traceTimeout.current); traceTimeout.current = null; const traceToken = ++traceGeneration.current;
    let createdRunId = '';
    setTrace(null);
    setBusy(true); setNotice('Запускаем анализ…');
    try {
      const outcome = await executeRun({ ...api, run: async (...args) => { const run = await api.run(...args); createdRunId = run.run_id; return run; } }, companyId, role, idempotencyKey, controller.signal,
        attempt => saveAttempt(pair, attempt), waitForPoll);
      if (outcome.status === 'unknown') {
        if (generation.current === token) setNotice('Результат анализа неизвестен. Повторный запуск может создать дублирующую операцию. Для этого контекста новый запуск недоступен в текущей сессии.');
      }
      else {
        if (generation.current === token) {
          if (outcome.status === 'failed') setNotice('Анализ не завершён.');
          else { setModes(previous => new Map(previous).set(pair, outcome.execution_mode ? runSuccessMessage(outcome.execution_mode) : 'Режим анализа не указан сервисом.')); setNotice('Анализ готов.');
            if (createdRunId) { const traceAbort = new AbortController(); traceController.current = traceAbort; let timedOut = false; const timeout = window.setTimeout(() => { timedOut = true; traceAbort.abort(); }, 8000); traceTimeout.current = timeout; setTrace({ context: pair, value: null, error: false }); void api.trace(createdRunId, traceAbort.signal).then(data => { if (!traceAbort.signal.aborted && traceGeneration.current === traceToken && generation.current === token && data.company_id === companyId && data.role_id === role) setTrace({ context: pair, value: data, error: false }); }).catch(() => { if ((!traceAbort.signal.aborted || timedOut) && traceGeneration.current === traceToken && generation.current === token) setTrace({ context: pair, value: null, error: true }); }).finally(() => { if (traceTimeout.current === timeout) { window.clearTimeout(timeout); traceTimeout.current = null; } }); }
            await refresh(companyId, role, token); }
        }
      }
    } catch { /* executeRun contains API and lifecycle failures. */ }
    finally { if (generation.current === token) setBusy(false); }
  };
  const selectInsight = (item: InsightSummary) => { detailGeneration.current++; setDetailEpoch(n => n + 1); setDetailState('loading'); setSelected({ ...item, blocks: [], sources: [], unknowns: [], question: null }); };
  const dialogueKey = selected ? JSON.stringify([companyId, role, selected.insight_id]) : '';
  if (selected && !dialogues.current.has(dialogueKey)) dialogues.current.set(dialogueKey, newDialogueEntry());
  const activeCompany = companies.find(c => c.company_id === companyId);
  const currentAttempt = companyId ? attemptContexts.get(contextKey(companyId, role)) : undefined;
  const currentContextUnknown = currentAttempt?.status === 'unknown';
  const currentContextPending = currentAttempt?.status === 'pending';
  const liveBlocked = Boolean(runtime?.live_operations?.blocked);
  const dataPending = runtime?.news_count === 0;

  return <div className="app-shell">
    <a className="skip-link" href="#home">К содержимому</a><header className="topbar"><a className="wordmark" href="#home" aria-label="Бизнес-инсайты, главная"><span className="mark" aria-hidden="true">А</span><span>Бизнес-инсайты<small>Новости и аналитика</small></span></a><span className="demo-badge"><i />{['broker_live','local_ollama'].includes(runtime?.execution_mode ?? '') ? 'Бизнес-аналитика' : 'Локальный демонстрационный доступ'}</span><div className="user-dot" aria-label="Демо-профиль">Д</div></header>
    <main id="home" tabIndex={-1}>
      <section className="welcome"><div><p className="eyebrow"><UiIcon name="spark" /> НОВОСТИ С УЧЁТОМ ВАШЕЙ КОМПАНИИ</p><h1>Инсайты для вашего бизнеса</h1><p className="lede">События, возможное влияние и источники.</p></div><div className="today"><span>Режим анализа</span><strong>{modes.get(contextKey(companyId, role)) ?? (runtime?.execution_mode === 'local_ollama' ? 'Локальная LLM · сервер' : runtime?.execution_mode === 'broker_live' ? 'Реальная LLM · запросы в пределах квоты' : 'Будет указан после анализа')}</strong></div></section>
      <section className="context-panel" aria-label="Настройки анализа">
        <div className="company-field"><label htmlFor="company">Компания</label><select id="company" value={companyId} onChange={e => choose(e.target.value)} disabled={!companies.length}><option value="" disabled>Загрузка компаний…</option>{(['public','provided'] as const).map(origin => { const group = companies.filter(c => (c.profile_origin ?? 'provided') === origin); return group.length > 0 ? <optgroup key={origin} label={origin === 'public' ? 'Публичные компании' : 'Обезличенные компании кейса'}>{group.map(c => <option key={c.company_id} value={c.company_id}>{c.display_name} · {sectorLabel(c)}</option>)}</optgroup> : null; })}</select>{activeCompany && <span className="field-caption">{sectorLabel(activeCompany)} · {activeCompany.profile_origin === 'public' ? 'открытый профиль' : 'обезличенный профиль'}</span>}</div>
        <fieldset className="role-field"><legend>Для кого подготовить</legend><div className="role-options">{roles.map(r => <label key={r.id} className={`role-option ${role === r.id ? 'active' : ''}`}><input type="radio" name="role" value={r.id} checked={role === r.id} onChange={() => { setNotice(''); setRole(r.id); }} /><span className="radio-dot"/><span className="role-copy"><strong>{r.label}</strong><small>{r.hint}</small></span></label>)}</div></fieldset>
        <button className="primary" onClick={runAnalysis} disabled={liveBlocked || dataPending || isRunDisabled(Boolean(companyId), busy, currentAttempt)}>{busy ? <><span className="spinner"/>Анализируем</> : liveBlocked ? <>Новые запросы приостановлены</> : dataPending ? <>Загружаем источники</> : currentContextUnknown ? <>Запуск недоступен</> : currentContextPending ? <>Анализ уже выполняется</> : <>Запустить анализ <span aria-hidden="true">→</span></>}</button>
      </section>
      {runtime && <RuntimeNotice status={runtime} />}
      {activeCompany && <CompanyProfilePanel company={activeCompany} />}
      <TraceDetails trace={trace} companyId={companyId} role={role} />
      <p className="eligibility-note">{eligibilityCopy(activeCompany)}</p><div className="content-grid"><section className="feed-section" aria-label="События компании">
        {currentContextPending && <div className="notice" role="status">Анализ для этой компании и роли выполняется. Повторный запуск временно недоступен.</div>}
        {currentContextUnknown && <div className="notice" role="status">Для этой компании и роли результат остаётся неизвестным. Повторный запуск может создать дублирующую операцию; новый запуск в этой сессии заблокирован.</div>}
        {notice && !currentContextUnknown && <div className={`notice ${notice.startsWith('Не удалось связаться') ? 'error' : ''}`} role="status">{notice}</div>}
        <InsightFeed key={contextKey(companyId, role)} items={feed} selectedId={selected?.insight_id ?? null} loading={feedLoading} showEmpty={!notice || notice === 'Анализ готов.'} analyzed={Boolean(modes.get(contextKey(companyId, role)))} onSelect={selectInsight} />
      </section>
      <aside ref={detailPanel} className="detail-panel" aria-label="Детали инсайта" tabIndex={-1}>{selected ? <><div className="detail-top"><span className="tag">{selected.synthetic ? 'Синтетический инсайт' : 'Инсайт по локальным материалам'}</span><button className="close-detail" onClick={closeInsight} aria-label="Закрыть карточку"><span aria-hidden="true">←</span> К ленте</button></div><h2>{selected.headline}</h2><p className="detail-summary">{selected.summary}</p>{selected.event_published_at ? <p className="detail-date">Публикация · {date(selected.event_published_at)}</p> : <p className="detail-date">Дата публикации не установлена</p>}<p className="detail-date">Анализ подготовлен · {date(selected.created_at)}</p><div className="source-status"><span>Позиция Альфа-Банка</span><strong>{bankStatus[selected.bank_position_status] ?? 'Статус неизвестен'}</strong><small>{selected.bank_position_status === 'historical' ? 'Сверьте дату документа. Исторический материал не определяет текущую позицию банка.' : selected.bank_position_status === 'found' ? 'Позиция относится к указанному документу и дате.' : 'Доступной подтверждённой позиции банка нет.'}</small></div><div className="detail-blocks">{detailState === 'ready' ? selected.blocks.map(block => <article className={`info-block block-${block.kind}`} key={block.block_id}><span className="block-label">{block.kind === 'observed_fact' ? selected.synthetic ? 'Факт в синтетических данных' : 'Факт из источника' : block.kind === 'bank_position' ? 'Позиция банка' : 'Вывод / сценарий AI'}</span><p>{block.text}</p>{block.evidence_refs.map(ref => { const source = selected.sources.find(s => s.source_id === ref); return <p className="source-ref" key={ref}>Источник: {source?.title ?? 'Ссылка на источник не найдена'}</p>; })}{block.assumptions.map(a => <p className="unknown" key={a}>Предположение: {a}</p>)}</article>) : <p className="detail-summary">{detailState === 'error' ? 'Детали не удалось загрузить. Откройте карточку повторно.' : 'Загружаем детали…'}</p>}</div><Sources sources={selected.sources} /><BusinessUnknowns unknowns={selected.unknowns} blocks={detailState === 'ready' ? selected.blocks : []} />{detailState === 'ready' && <Dialogue key={dialogueKey} insight={selected} entry={dialogues.current.get(dialogueKey)!} blocked={liveBlocked} />}</> : <div className="detail-placeholder"><div className="detail-symbol">↖</div><h3>Выберите инсайт</h3><p>В карточке будут показаны факты, выводы и доступные основания.</p><div className="detail-legend"><span><i className="legend-fact"/>Факт из источника</span><span><i className="legend-ai"/>Вывод / сценарий AI</span></div><small>Источники и неизвестные указываются для каждого инсайта</small></div>}</aside></div>
      <footer><span>Локальный доступ · происхождение указано у материалов</span><span>Решения принимает пользователь</span></footer>
    </main>
  </div>;
}

function errorText(error: unknown) { return error instanceof Error ? error.message : 'Не удалось загрузить данные. Попробуйте ещё раз.'; }
function contextKey(companyId: string, role: RoleId) { return JSON.stringify([companyId, role]); }

export function TraceDetails({ trace, companyId, role }: { trace: { context: string; value: RunTrace | null; error: boolean } | null; companyId: string; role: RoleId }) {
  if (!trace || trace.context !== contextKey(companyId, role)) return null;
  return <details className="trace-panel"><summary>Как подготовлен анализ</summary>{trace.error ? <p>Этапы анализа пока недоступны.</p> : !trace.value ? <p>Загружаем этапы…</p> : <><p>{trace.value.execution_mode === 'mvp_offline' ? 'Использована тестовая модель (FAKE); качество реальной генерации этим не подтверждается.' : trace.value.execution_mode === 'broker_live' ? 'Использована разрешённая модель.' : trace.value.execution_mode === 'local_ollama' ? 'Использована локальная LLM сервера; экспериментальный маршрут.' : 'Использован демонстрационный режим.'}</p><ol>{trace.value.trace.map((item, index) => <li key={`${item.stage}-${index}`}>{stageLabel[item.stage] ?? 'Этап анализа'} · {item.status === 'completed' || item.status === 'passed' ? 'завершён' : item.status === 'stopped' ? 'остановлен' : item.status === 'one_correction_available' ? 'доступна одна корректировка' : item.status === 'submitted' ? 'запущена' : item.status === 'started' ? 'начат' : 'отклонён'}{item.sources?.length ? <ul>{item.sources.map((source, sourceIndex) => <li key={`${source.source_id}-${sourceIndex}`}>{traceSourceKind[source.kind] ?? 'Источник'}{source.date ? ` · ${date(source.date)}` : ''}</li>)}</ul> : item.stage === 'tool' ? ' · дополнительный поиск не дал новых источников' : ''}{item.reasons?.length ? ` · причины: ${item.reasons.map(reason => reasonText[reason] ?? 'Причина недоступна').join('; ')}` : ''}{item.reason ? ` · ${reasonText[item.reason] ?? 'Причина недоступна'}` : ''}</li>)}</ol><p>Использованы признаки: {trace.value.features.join(', ')}.</p></>}</details>;
}

const caveatCategories: [RegExp, string][] = [
  [/(?:exposure|impact|influence|экспозици|влияни|масштаб|контрол|владени|роль контрагент)/i, 'Масштаб и связь влияния с компанией не установлены'],
  [/(?:register_location_is_not_operating_geography|geograph|географ|регистрац|местонахожд)/i, 'География регистрации не определяет географию бизнеса'],
  [/(?:unit|currency|amount|единиц|валют|сумм|выручк)/i, 'Единицы и значения показателей требуют проверки'],
  [/(?:trade\/payments|payment|плат[её]ж|закупк|поставк.{0,30}(?:доказ|подтверж|объ[её]м|оплат))/i, 'Платежи сами по себе не подтверждают поставку или объём закупок'],
  [/(?:credit|loan|rate|(?<!по)ставк|кредит|услови[яй].{0,20}финансирован)/i, 'Условия финансирования требуют отдельной проверки'],
  [/(?:bank|позици[яию].{0,20}банк|банковск|bank_position)/i, 'Позиция банка не найдена или требует проверки'],
  [/(?:period|date|период|срок|дата)/i, 'Период и сроки требуют уточнения'],
  [/missing:[A-Z0-9_]+/i, 'В профиле отсутствуют отдельные сведения'],
];
const knownServiceCodes = /^(?:mvp_offline|broker_live|missing:[A-Z0-9_]+|[a-z][a-z0-9_]*(?:\/[a-z][a-z0-9_]*)+|[a-z][a-z0-9_]*_[a-z0-9_]+)$/i;
function caveatLabel(raw: string) {
  const category = caveatCategories.find(([pattern]) => pattern.test(raw));
  if (category) return category[1];
  return knownServiceCodes.test(raw.trim()) ? 'Дополнительные ограничения данных требуют проверки' : 'Дополнительные ограничения требуют проверки';
}
export function BusinessUnknowns({ unknowns, blocks }: { unknowns: string[]; blocks: Insight['blocks'] }) {
  const entries = new Map<string, { raw: string; locations: Set<string> }>();
  unknowns.forEach(raw => { if (raw) entries.set(raw, { raw, locations: new Set(['Общая оговорка']) }); });
  blocks.forEach(block => [...block.unknowns, ...block.assumptions].forEach(raw => { if (!raw) return; const entry = entries.get(raw) ?? { raw, locations: new Set<string>() }; const kind = block.kind === 'ai_inference' ? 'Сценарий AI' : block.kind === 'bank_position' ? 'Позиция банка' : 'Факт'; entry.locations.add(`${kind} (${block.block_id})`); entries.set(raw, entry); }));
  const rows = [...entries.values()];
  const priority = (label: string) => label.startsWith('Масштаб') || label.startsWith('География') ? 0 : label.startsWith('Единицы') ? 1 : label.startsWith('Позиция банка') ? 2 : 3;
  const labels = [...new Set(rows.map(({ raw }) => caveatLabel(raw)))].sort((a, b) => priority(a) - priority(b)).slice(0, 6);
  return <section className="unknown-box" aria-labelledby="unknown-heading"><h3 id="unknown-heading">Что важно проверить</h3>{labels.length ? <ul className="caveat-summary">{labels.map(label => <li key={label}>{label}</li>)}</ul> : <p>Пока нет детализации ограничений; влияние события на компанию требует проверки.</p>}<details className="unknown-disclosure"><summary>Показать исходные оговорки и ограничения ({rows.length})</summary>{rows.length ? <ul>{rows.map(({ raw, locations }) => <li key={raw}><span>{raw}</span><small>Относится к: {[...locations].join(', ')}</small></li>)}</ul> : <p>Исходных оговорок нет.</p>}</details></section>;
}
