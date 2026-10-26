import { useEffect, useRef, useState } from 'react';
import { api, type Insight } from './api';
import { Sources } from './Sources';
import { executeDialogue, subscribeDialogue, type DialogueEntry } from './dialogueLifecycle';
import { UiIcon } from './UiIcon';

const suggestions = ['Как это может повлиять на наши расходы?', 'Какие риски стоит проверить в первую очередь?', 'На каких данных основан этот вывод?'];

export function Dialogue({ insight, entry, blocked = false }: { insight: Insight; entry: DialogueEntry; blocked?: boolean }) {
  const [, redraw] = useState(0);
  const messageInput = useRef<HTMLTextAreaElement>(null);
  const update = () => redraw(n => n + 1);
  useEffect(() => {
    const unsubscribe = subscribeDialogue(entry, () => redraw(n => n + 1));
    redraw(n => n + 1); // Include a completion between render and effect subscription.
    return unsubscribe;
  }, [entry]);
  const question = entry.result?.status === 'succeeded' ? entry.result.question : insight.question;
  const send = (operation: 'answer' | 'message') => void executeDialogue(entry, operation, question?.question_id,
    (text, key) => operation === 'answer' ? api.answer(insight.insight_id, question!.question_id, text, key) : api.message(insight.insight_id, text, key));
  const locked = entry.state !== 'idle' || blocked;
  return <section className="dialogue" aria-label="Обсуждение инсайта">
    <div className="assistant-heading"><span className="assistant-icon"><UiIcon name="chat" /></span><div><p className="eyebrow">УТОЧНИТЬ И ОБСУДИТЬ</p><h3>AI-ассистент</h3></div></div><p>Сообщения относятся к выбранной компании, роли и этому инсайту. Ответ пользователя не заменяет исходные данные.</p>
    <p className="suggestions-label">Примеры вопросов</p><div className="question-suggestions" role="group" aria-label="Примеры вопросов ассистенту">{suggestions.map(text => <button type="button" key={text} disabled={locked} onClick={() => { entry.message = text; update(); messageInput.current?.focus(); }}>{text}</button>)}</div>
    {question && !entry.answered.has(question.question_id) && <form onSubmit={e => { e.preventDefault(); send('answer'); }}>
      <label htmlFor="exposure-answer">{question.text}</label>
      {question.options && <div className="answer-options">{question.options.map(option => <button type="button" key={option} disabled={locked} onClick={() => { entry.answer = option; update(); }}>{option}</button>)}</div>}
      <textarea id="exposure-answer" maxLength={2000} value={entry.answer} disabled={locked} onChange={e => { entry.answer = e.target.value; update(); }} />
      <button className="primary" disabled={locked || !entry.answer.trim()}>Сохранить уточнение</button>
    </form>}
    <form onSubmit={e => { e.preventDefault(); send('message'); }}>
      <label htmlFor="insight-message">Вопрос по инсайту</label><textarea ref={messageInput} id="insight-message" placeholder="Введите вопрос по этому инсайту" maxLength={2000} value={entry.message} disabled={locked} onChange={e => { entry.message = e.target.value; update(); }} />
      <button className="primary" disabled={locked || !entry.message.trim()}>Отправить вопрос</button>
    </form>
    {entry.notice && <p className="notice" role="status">{entry.notice}</p>}
    {entry.result && <section aria-label="Ответ по инсайту">{entry.result.blocks.map(block => <article className="info-block" key={block.block_id}><strong>{block.kind === 'observed_fact' ? 'Факт' : block.kind === 'bank_position' ? 'Позиция банка' : 'Вывод AI'}</strong><p>{block.text}</p>{block.evidence_refs.map(ref => <small key={ref}>Основание: {entry.result!.sources.find(s => s.source_id === ref)?.locator ?? 'источник не разрешён'}</small>)}{block.assumptions.map((x, i) => <p key={i}>Предположение: {x}</p>)}{block.unknowns.map((x, i) => <p key={i}>Неизвестно: {x}</p>)}</article>)}<Sources sources={entry.result.sources.map(ref => ({ ...insight.sources.find(s => s.source_id === ref.source_id), ...ref }))} />{entry.result.unknowns.map((x, i) => <p className="unknown" key={i}>{x}</p>)}</section>}
  </section>;
}
