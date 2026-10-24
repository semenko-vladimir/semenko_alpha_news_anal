import { RequestError, type DialogueResult } from './api';

export type DialogueEntry = {
  answer: string; message: string; state: 'idle' | 'pending' | 'unknown';
  notice: string; result?: DialogueResult; answered: Set<string>;
};
export function newDialogueEntry(): DialogueEntry {
  return { answer: '', message: '', state: 'idle', notice: '', answered: new Set() };
}
const listeners = new WeakMap<DialogueEntry, Set<() => void>>();
export function subscribeDialogue(entry: DialogueEntry, listener: () => void): () => void {
  let subscriptions = listeners.get(entry);
  if (!subscriptions) { subscriptions = new Set(); listeners.set(entry, subscriptions); }
  subscriptions.add(listener);
  return () => { subscriptions.delete(listener); };
}

const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(x => typeof x === 'string');
const nullableString = (value: unknown) => value === null || typeof value === 'string';
function validResult(value: unknown): value is DialogueResult {
  if (!record(value)) return false;
  return typeof value.message_id === 'string' && typeof value.insight_id === 'string'
    && typeof value.input_version === 'string' && nullableString(value.error_code)
    && ['succeeded', 'failed', 'unknown_outcome'].includes(value.status as string)
    && Array.isArray(value.blocks) && value.blocks.every(block => record(block)
      && typeof block.block_id === 'string' && typeof block.text === 'string'
      && ['observed_fact', 'bank_position', 'ai_inference'].includes(block.kind as string)
      && strings(block.evidence_refs) && strings(block.assumptions) && strings(block.unknowns))
    && Array.isArray(value.sources) && value.sources.every(source => record(source)
      && typeof source.source_id === 'string' && typeof source.locator === 'string'
      && ['kind', 'title', 'retrieved_at', 'trust_status'].every(key => source[key] === undefined || typeof source[key] === 'string')
      && ['uri', 'published_at', 'document_date'].every(key => source[key] === undefined || nullableString(source[key]))
      && (source.synthetic === undefined || typeof source.synthetic === 'boolean'))
    && strings(value.unknowns)
    && (value.question === null || (record(value.question)
      && typeof value.question.question_id === 'string' && typeof value.question.text === 'string'
      && value.question.kind === 'exposure_confirmation'
      && (value.question.options === undefined || strings(value.question.options))));
}
// The entry belongs to company/role/insight and survives component unmounts.
export async function executeDialogue(entry: DialogueEntry, operation: 'answer' | 'message', questionId: string | undefined,
  send: (text: string, key: string) => Promise<DialogueResult>, notify: () => void = () => {}): Promise<void> {
  const publish = () => { notify(); listeners.get(entry)?.forEach(listener => listener()); };
  const text = (operation === 'answer' ? entry.answer : entry.message).trim();
  if (entry.state !== 'idle' || !text || text.length > 2000 || (operation === 'answer' && (!questionId || entry.answered.has(questionId)))) return;
  entry.state = 'pending'; entry.notice = 'Отправляем…'; publish();
  try {
    const result = await send(text, crypto.randomUUID());
    if (!validResult(result)) throw new Error('Invalid dialogue response');
    entry.result = result;
    if (result.status === 'unknown_outcome') {
      entry.state = 'unknown'; entry.notice = 'Результат отправки неизвестен. Повторная отправка для этого инсайта заблокирована в текущей сессии.';
    } else {
      entry.state = 'idle';
      entry.notice = result.status === 'failed' ? 'Ответ не подготовлен. Подробности ограничений указаны ниже.' : 'Ответ получен. Уточнение пользователя сохранено отдельно от исходных данных.';
      if (result.status === 'succeeded') {
        if (operation === 'answer') { entry.answered.add(questionId!); entry.answer = ''; } else entry.message = '';
      }
    }
  } catch (error) {
    if (error instanceof RequestError && [400, 401, 403, 404, 409, 422, 429].includes(error.status)) {
      entry.state = 'idle'; entry.notice = 'Сервис отклонил запрос. Проверьте доступ, ограничения и актуальность вопроса. Автоматического повтора нет.';
    } else {
      entry.state = 'unknown'; entry.notice = 'Ответ не получен; результат отправки неизвестен. Повторная отправка заблокирована в текущей сессии.';
    }
  }
  publish();
}
