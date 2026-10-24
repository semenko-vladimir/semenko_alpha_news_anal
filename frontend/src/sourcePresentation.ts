import type { SourceRef } from './api';

export function formatSourceDate(value: string | null | undefined): string {
  if (!value) return 'неизвестна';
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) return 'неизвестна';
  return new Intl.DateTimeFormat('ru-RU', {
    dateStyle: 'long', timeStyle: 'short', timeZone: 'UTC',
  }).format(parsed) + ' UTC';
}

export function safeSourceHref(uri: string | null | undefined): string | null {
  if (!uri) return null;
  try {
    const parsed = new URL(uri);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
    return parsed.href;
  } catch {
    return null;
  }
}

export function sourceDateLabels(source: SourceRef) {
  return [
    ['Опубликован', formatSourceDate(source.published_at)],
    ['Дата документа', formatSourceDate(source.document_date)],
    ['Получен', formatSourceDate(source.retrieved_at)],
  ] as const;
}
