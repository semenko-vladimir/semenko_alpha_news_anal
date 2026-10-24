import type { SourceRef } from './api';
import { safeSourceHref, sourceDateLabels } from './sourcePresentation';

export function Sources({ sources }: { sources: SourceRef[] }) {
  return <section className="source-list" aria-label="Источники"><h3>Источники</h3>{sources.length ? sources.map(source => {
    const href = safeSourceHref(source.uri);
    return <article className="info-block" key={source.source_id}>
      <strong>{source.title || 'Источник без названия'}</strong>
      <small>{source.synthetic === true ? 'Синтетический источник' : source.synthetic === false ? 'Реальный локальный материал' : 'Происхождение не указано'}</small>
      {sourceDateLabels(source).map(([label, value]) => <small key={label}>{label}: {value}</small>)}
      <small>Место в источнике: {source.locator || 'не указано'}</small>
      {href ? <a href={href} target="_blank" rel="noopener noreferrer">Открыть источник</a> : <small>Внешняя ссылка недоступна</small>}
    </article>;
  }) : <p>Источники не указаны.</p>}</section>;
}
