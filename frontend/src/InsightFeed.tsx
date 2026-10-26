import { useState } from 'react';
import type { InsightSummary } from './api';
import { formatSourceDate } from './sourcePresentation';
import { UiIcon } from './UiIcon';

type ExposureFilter = 'all' | 'confirmed' | 'unconfirmed';
const filters: { id: ExposureFilter; label: string }[] = [
  { id: 'all', label: 'Все' },
  { id: 'unconfirmed', label: 'Требует проверки' },
  { id: 'confirmed', label: 'Связь подтверждена' },
];
const exposure: Record<InsightSummary['exposure_status'], string> = {
  unknown: 'Связь не подтверждена', conditional: 'Возможная связь', confirmed: 'Связь подтверждена', contradicted: 'Связь опровергнута',
};
const normalize = (value: string) => value.toLocaleLowerCase('ru-RU').replace(/ё/g, 'е');

export function filterInsights(items: readonly InsightSummary[], query: string, filter: ExposureFilter): InsightSummary[] {
  const words = normalize(query).trim().split(/\s+/).filter(Boolean);
  return items.filter(item => {
    const matchesExposure = filter === 'all' || (filter === 'confirmed' ? item.exposure_status === 'confirmed' : item.exposure_status === 'unknown' || item.exposure_status === 'conditional');
    const text = normalize(`${item.headline} ${item.summary}`);
    return matchesExposure && words.every(word => text.includes(word));
  });
}

export function InsightFeed({ items, selectedId, loading, showEmpty, analyzed = false, onSelect }: {
  items: readonly InsightSummary[]; selectedId: string | null; loading: boolean; showEmpty: boolean; analyzed?: boolean; onSelect: (item: InsightSummary) => void;
}) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<ExposureFilter>('all');
  const visible = filterInsights(items, query, filter);
  const narrowed = query.trim().length > 0 || filter !== 'all';
  return <section className="insight-feed" aria-labelledby="feed-heading">
    <div className="section-heading"><div><p className="eyebrow">ВАЖНОЕ ДЛЯ ВАС</p><h2 id="feed-heading" tabIndex={-1}>Лента инсайтов</h2></div><span className="count" aria-label={`Показано ${visible.length} из ${items.length}`}>{visible.length}</span></div>
    <div className="feed-filters" role="group" aria-label="Фильтр по связи с компанией">{filters.map(option => <button key={option.id} type="button" className={`filter-chip ${filter === option.id ? 'active' : ''}`} aria-pressed={filter === option.id} onClick={() => setFilter(option.id)}>{option.label}</button>)}</div>
    <label className="feed-search"><span className="sr-only">Поиск по заголовку и описанию инсайтов</span><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Поиск по инсайтам" /><UiIcon name="search" /></label>
    <p className="filter-caption" role="status">{narrowed ? `Найдено ${visible.length} из ${items.length}` : 'События по результатам анализа'}</p>
    {loading && <p role="status" className="notice">Загружаем ленту…</p>}
    {!loading && !visible.length && (narrowed || showEmpty) && <div className="empty"><div className="empty-icon"><UiIcon name="news" /></div><h3>{narrowed ? 'Ничего не найдено' : analyzed ? 'Подходящих событий не найдено' : 'Пока нет инсайтов'}</h3><p>{narrowed ? 'Измените запрос или сбросьте фильтры. Поиск работает по загруженной ленте.' : analyzed ? 'В загруженных источниках нет подходящих событий для выбранной компании и роли. Даты загрузки указаны выше.' : 'Запустите анализ, чтобы увидеть события и их возможное значение для компании.'}</p>{narrowed && <button type="button" className="secondary" onClick={() => { setQuery(''); setFilter('all'); }}>Сбросить фильтры</button>}</div>}
    <div className="feed-list">{visible.map(item => <button className={`feed-card ${selectedId === item.insight_id ? 'chosen' : ''}`} key={item.insight_id} onClick={() => onSelect(item)} aria-pressed={selectedId === item.insight_id}>
      <span className="event-icon"><UiIcon name="news" /></span><div className="feed-card-content"><div className="card-meta"><span className={`tag exposure-${item.exposure_status}`}>{exposure[item.exposure_status] ?? 'Связь неизвестна'}</span><time dateTime={item.event_published_at ?? item.created_at}>{item.event_published_at ? 'Публикация · ' : 'Анализ · '}{formatSourceDate(item.event_published_at ?? item.created_at)}</time></div><span className="provenance-label">{item.synthetic ? 'Синтетические данные' : 'Материалы с указанными источниками'}</span><h3>{item.headline}</h3><p>{item.summary}</p><span className="card-link">Открыть инсайт <UiIcon name="arrow" /></span></div>
    </button>)}</div>
  </section>;
}
