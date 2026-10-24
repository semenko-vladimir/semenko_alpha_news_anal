import { useCallback, useEffect, useRef, useState } from 'react';
import { api, type Company, type Insight, type InsightSummary, type RoleId } from './api';
import { executeRun, isRunDisabled, runSuccessMessage, waitForPoll, type Attempt } from './runLifecycle';
import { formatSourceDate } from './sourcePresentation';
import { Dialogue } from './Dialogue';
import { Sources } from './Sources';
import { newDialogueEntry, type DialogueEntry } from './dialogueLifecycle';

const roles: { id: RoleId; label: string; hint: string }[] = [
  { id: 'CFO', label: 'Финансовому директору', hint: 'Деньги и стоимость' },
  { id: 'COO', label: 'Операционному директору', hint: 'Работа и устойчивость' },
];
const date = formatSourceDate;
const bankStatus: Record<string, string> = { found: 'Найдена в датированном документе', historical: 'Историческая позиция · не текущее мнение', not_found: 'Покрытие не найдено', not_checked: 'Не проверялась' };
const exposure: Record<string, string> = { unknown: 'Связь не подтверждена', conditional: 'Возможная связь', confirmed: 'Связь подтверждена', contradicted: 'Связь опровергнута' };

export default function App() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [companyId, setCompanyId] = useState('');
  const [role, setRole] = useState<RoleId>('CFO');
  const [feed, setFeed] = useState<InsightSummary[]>([]);
  const [selected, setSelected] = useState<Insight | null>(null);
  const dialogues = useRef(new Map<string, DialogueEntry>());
  const detailGeneration = useRef(0);
  const [detailEpoch, setDetailEpoch] = useState(0);
  const [detailState, setDetailState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [modes, setModes] = useState(new Map<string, string>());
  const [feedLoading, setFeedLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const attempts = useRef(new Map<string, Attempt>());
  const [attemptContexts, setAttemptContexts] = useState<Map<string, Attempt>>(() => new Map());
  const generation = useRef(0);
  const active = useRef<AbortController | null>(null);
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
      setCompanies(items); setCompanyId(items[0]?.company_id ?? '');
      if (!items.length) setNotice('Нет доступных демонстрационных компаний.');
    }).catch(e => { if (!controller.signal.aborted) setNotice(errorText(e)); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!companyId) return;
    const token = ++generation.current; active.current?.abort();
    detailGeneration.current++; setSelected(null); setDetailState('idle'); setFeed([]); setBusy(false); setNotice('');
    void refresh(companyId, role, token);
    return () => { active.current?.abort(); };
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

  const choose = (id: string) => { generation.current++; active.current?.abort(); detailGeneration.current++; setSelected(null); setDetailState('idle'); setFeed([]); setBusy(false); setNotice(''); setCompanyId(id); };
  const runAnalysis = async () => {
    const pair = contextKey(companyId, role);
    if (!companyId || busy || attempts.current.has(pair)) return;
    const token = generation.current; active.current?.abort(); const controller = new AbortController(); active.current = controller;
    const idempotencyKey = crypto.randomUUID();
    setBusy(true); setNotice('Запускаем анализ…');
    try {
      const outcome = await executeRun(api, companyId, role, idempotencyKey, controller.signal,
        attempt => saveAttempt(pair, attempt), waitForPoll);
      if (outcome.status === 'unknown') {
        if (generation.current === token) setNotice('Результат анализа неизвестен. Повторный запуск может создать дублирующую операцию. Для этого контекста новый запуск недоступен в текущей сессии.');
      }
      else {
        if (generation.current === token) {
          if (outcome.status === 'failed') setNotice('Анализ не завершён.');
          else { setModes(previous => new Map(previous).set(pair, outcome.execution_mode ? runSuccessMessage(outcome.execution_mode) : 'Режим анализа не указан сервисом.')); setNotice('Анализ готов.'); await refresh(companyId, role, token); }
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

  return <div className="app-shell">
    <header className="topbar"><a className="wordmark" href="#home" aria-label="Бизнес-инсайты, главная"><span className="mark">а</span><span>Бизнес-инсайты</span></a><span className="demo-badge"><i />Локальный демонстрационный доступ</span><div className="user-dot" aria-label="Демо-профиль">Д</div></header>
    <main id="home">
      <section className="welcome"><div><p className="eyebrow">РАБОЧЕЕ ПРОСТРАНСТВО</p><h1>Что происходит<br className="mobile-break"/> в бизнесе</h1><p className="lede">События и контекст компании — в одном месте.</p></div><div className="today"><span>Режим анализа</span><strong>{modes.get(contextKey(companyId, role)) ?? 'Будет указан после анализа'}</strong></div></section>
      <section className="context-panel" aria-label="Настройки анализа">
        <div className="company-field"><label htmlFor="company">Компания</label><select id="company" value={companyId} onChange={e => choose(e.target.value)} disabled={!companies.length}><option value="" disabled>Загрузка компаний…</option>{companies.map(c => <option key={c.company_id} value={c.company_id}>{c.display_name}</option>)}</select>{activeCompany && <span className="field-caption">{activeCompany.category === 'TRADE' ? 'Торговля' : 'Транспорт'} · обезличенный профиль</span>}</div>
        <fieldset className="role-field"><legend>Для кого подготовить</legend><div className="role-options">{roles.map(r => <label key={r.id} className={`role-option ${role === r.id ? 'active' : ''}`}><input type="radio" name="role" value={r.id} checked={role === r.id} onChange={() => { setNotice(''); setRole(r.id); }} /><span className="radio-dot"/><span className="role-copy"><strong>{r.label}</strong><small>{r.hint}</small></span></label>)}</div></fieldset>
        <button className="primary" onClick={runAnalysis} disabled={isRunDisabled(Boolean(companyId), busy, currentAttempt)}>{busy ? <><span className="spinner"/>Анализируем</> : currentContextUnknown ? <>Запуск недоступен</> : currentContextPending ? <>Анализ уже выполняется</> : <>Запустить анализ <span aria-hidden="true">→</span></>}</button>
      </section>
      <p className="eligibility-note">Принадлежность к сегменту с выручкой от 15 млрд ₽ не подтверждена: единицы выручки требуют проверки.</p><div className="content-grid"><section className="feed-section" aria-labelledby="feed-heading"><div className="section-heading"><div><p className="eyebrow">ВАЖНОЕ ДЛЯ ВАС</p><h2 id="feed-heading">Лента инсайтов</h2></div><span className="count">{feed.length}</span></div>
        {currentContextPending && <div className="notice" role="status">Анализ для этой компании и роли выполняется. Повторный запуск временно недоступен.</div>}
        {currentContextUnknown && <div className="notice" role="status">Для этой компании и роли результат остаётся неизвестным. Повторный запуск может создать дублирующую операцию; новый запуск в этой сессии заблокирован.</div>}
        {notice && !currentContextUnknown && <div className={`notice ${notice.startsWith('Не удалось связаться') ? 'error' : ''}`} role="status">{notice}</div>}
        {feedLoading && <p role="status">Загружаем ленту…</p>}
        {!feedLoading && !notice && !feed.length && <div className="empty"><div className="empty-icon">✳</div><h3>Пока нет инсайтов</h3><p>Запустите анализ, чтобы увидеть события и их возможное значение для компании.</p></div>}
        <div className="feed-list">{feed.map(item => <button className={`feed-card ${selected?.insight_id === item.insight_id ? 'chosen' : ''}`} key={item.insight_id} onClick={() => selectInsight(item)}><div className="card-meta"><span className="tag">{exposure[item.exposure_status] ?? 'Связь неизвестна'} · {item.synthetic ? 'синтетика' : 'локальные материалы'}</span><time>{date(item.created_at)}</time></div><h3>{item.headline}</h3><p>{item.summary}</p><span className="card-link">Открыть инсайт <b>↗</b></span></button>)}</div>
      </section>
      <aside className="detail-panel" aria-label="Детали инсайта">{selected ? <><div className="detail-top"><span className="tag">{selected.synthetic ? 'Синтетический инсайт' : 'Инсайт по локальным материалам'}</span><button className="close-detail" onClick={() => { detailGeneration.current++; setSelected(null); setDetailState('idle'); }} aria-label="Закрыть карточку">×</button></div><h2>{selected.headline}</h2><p className="detail-summary">{selected.summary}</p><p className="detail-date">Дата наблюдения · {date(selected.created_at)}</p><div className="source-status"><span>Позиция Альфа-Банка</span><strong>{bankStatus[selected.bank_position_status] ?? 'Статус неизвестен'}</strong><small>{selected.bank_position_status === 'historical' ? 'Сверьте дату документа. Исторический материал не определяет текущую позицию банка.' : selected.bank_position_status === 'found' ? 'Позиция относится к указанному документу и дате.' : 'Доступной подтверждённой позиции банка нет.'}</small></div><div className="detail-blocks">{detailState === 'ready' ? selected.blocks.map(block => <article className="info-block" key={block.block_id}><span className="block-label">{block.kind === 'observed_fact' ? selected.synthetic ? 'Факт в синтетических данных' : 'Факт из источника' : block.kind === 'bank_position' ? 'Позиция банка' : 'Вывод / сценарий AI'}</span><p>{block.text}</p>{block.evidence_refs.map(ref => { const source = selected.sources.find(s => s.source_id === ref); return <p className="source-ref" key={ref}>Источник: {source?.title ?? 'Ссылка на источник не найдена'}</p>; })}{block.assumptions.map(a => <p className="unknown" key={a}>Предположение: {a}</p>)}{block.unknowns.length > 0 && <div className="unknown"><strong>Неизвестно по этому блоку</strong><ul>{block.unknowns.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></div>}</article>) : <p className="detail-summary">{detailState === 'error' ? 'Детали не удалось загрузить. Откройте карточку повторно.' : 'Загружаем детали…'}</p>}</div><Sources sources={selected.sources} /><div className="unknown-box"><strong>Что пока неизвестно</strong>{selected.unknowns.length ? <ul>{selected.unknowns.map((x, i) => <li key={`${i}-${x}`}>{x}</li>)}</ul> : <p>Данные о влиянии события на компанию требуют проверки.</p>}</div>{detailState === 'ready' && <Dialogue key={dialogueKey} insight={selected} entry={dialogues.current.get(dialogueKey)!} />}</> : <div className="detail-placeholder"><div className="detail-symbol">↖</div><h3>Выберите инсайт</h3><p>В карточке будут показаны факты, выводы и доступные основания.</p><div className="detail-legend"><span><i className="legend-fact"/>Факт из источника</span><span><i className="legend-ai"/>Вывод / сценарий AI</span></div><small>Источники и неизвестные указываются для каждого инсайта</small></div>}</aside></div>
      <footer><span>Локальный доступ · происхождение указано у материалов</span><span>Решения принимает пользователь</span></footer>
    </main>
  </div>;
}

function errorText(error: unknown) { return error instanceof Error ? error.message : 'Не удалось загрузить данные. Попробуйте ещё раз.'; }
function contextKey(companyId: string, role: RoleId) { return JSON.stringify([companyId, role]); }
