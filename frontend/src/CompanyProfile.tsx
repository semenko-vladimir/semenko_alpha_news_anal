import { useEffect, useState } from 'react';
import { api, type Company, type CompanyProfile, type DataStatus } from './api';
import { Sources } from './Sources';
import { formatSourceDate } from './sourcePresentation';

export function sectorLabel(company: Company) {
  return company.sector_label ?? ({ TRADE: 'Торговля', TRANSPORT: 'Транспорт и логистика' }[company.category] ?? 'Отрасль требует уточнения');
}

export function eligibilityCopy(company?: Pick<Company,'eligibility_status' | 'profile_origin'>) {
  return company?.eligibility_status === 'confirmed'
    ? 'Годовая выручка свыше 15 млрд ₽ подтверждена по указанной отчётности. Период и периметр — в профиле.'
    : company?.eligibility_status === 'below_threshold'
      ? 'По подтверждённому годовому показателю компания не превышает порог 15 млрд ₽.'
      : company?.profile_origin === 'public'
        ? 'Принадлежность к сегменту с выручкой от 15 млрд ₽ не подтверждена сопоставимым годовым показателем. Другие виды показателей не заменяют выручку.'
        : 'Принадлежность к сегменту с выручкой от 15 млрд ₽ не подтверждена: единицы выручки требуют проверки.';
}

export function CompanyFacts({ profile }: { profile: CompanyProfile }) {
  const publicProfile = profile.profile_origin === 'public';
  return <div className="company-facts">
    <div className="profile-description"><span className="tag">{publicProfile ? 'Открытый профиль' : 'Данные кейса'}</span><h3>{profile.legal_name ?? profile.display_name}</h3><p>{profile.activity ?? 'Вид деятельности требует уточнения'}</p>
      <p className="profile-date">{profile.as_of ? `Отчётный срез: ${profile.as_of}` : 'Дата среза не установлена'}{profile.checked_at ? ` · Проверка источников: ${formatSourceDate(profile.checked_at)}` : ''}</p>
      {profile.entity_scope === 'group' && <p className="profile-date">Консолидированные сведения группы компаний; они не описывают каждое юридическое лицо отдельно.</p>}
    </div>
    {profile.metrics.length > 0 && <dl className="profile-metrics">{profile.metrics.map((metric, index) => <div key={`${metric.source_id}-${index}`}><dt>{metric.name}</dt><dd>{new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 4 }).format(metric.value)} <span>{metric.unit}</span></dd><small>Период: {metric.period} · {metric.scope === 'group' ? 'группа компаний' : 'организация'}</small></div>)}</dl>}
    {publicProfile ? profile.unknowns.length > 0 && <ul className="profile-caveats">{profile.unknowns.map(text => <li key={text}>{text}</li>)}</ul> : <>
      <ul className="profile-caveats"><li>Дата среза и единицы финансовых показателей в выгрузке не подтверждены.</li><li>Основной вид деятельности и продуктовые признаки не устанавливают фактическое влияние события, ставки или остатки.</li><li>Контекст используется в обезличенном виде; имя организации по ID не восстанавливается.</li></ul>
      <details className="profile-raw-unknowns"><summary>Исходные ограничения профиля ({profile.unknowns.length})</summary><ul>{profile.unknowns.map(text => <li key={text}>{text}</li>)}</ul></details>
    </>}
    <Sources sources={profile.sources} />
  </div>;
}

export function CompanyProfilePanel({ company }: { company: Company }) {
  const [state, setState] = useState<{ companyId: string; value: CompanyProfile | null; failed: boolean } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    api.profile(company.company_id, controller.signal).then(value => {
      if (!controller.signal.aborted && value.company_id === company.company_id) setState({companyId: company.company_id, value, failed: false});
    }).catch(() => { if (!controller.signal.aborted) setState({ companyId: company.company_id, value: null, failed: true }); });
    return () => controller.abort();
  }, [company.company_id]);
  const current = state?.companyId === company.company_id ? state : null;
  return <details className="company-profile" key={company.company_id}><summary><span><strong>Контекст компании</strong><small>{sectorLabel(company)} · {company.profile_origin === 'public' ? 'официальные открытые материалы' : 'обезличенные данные кейса'}</small></span><span className="profile-summary-action">Источники и показатели</span></summary>
    {current?.value ? <CompanyFacts profile={current.value} /> : <p role="status">{current?.failed ? 'Профиль не удалось загрузить. Сведения не подменяются другими данными.' : 'Загружаем профиль…'}</p>}
  </details>;
}

export function RuntimeNotice({ status }: { status: DataStatus }) {
  const live = status.execution_mode === 'broker_live';
  const local = status.execution_mode === 'local_ollama';
  const stop = status.live_operations;
  const publishers: Record<string, string> = {mintrans:'Минтранс',lukoil:'ЛУКОЙЛ',nornickel:'Норникель',fesco:'FESCO',mvideo:'М.Видео',magnit:'Магнит',mdmedical:'Мать и дитя',positive:'Группа Позитив',mts:'МТС',interrao:'Интер РАО',pik:'ПИК'};
  return <section className="runtime-notice" aria-label="Актуальность данных и режим анализа">
    <div><strong>{live ? 'Режим реальной модели' : local ? 'Локальная LLM сервера · эксперимент' : 'Локальный анализ · тестовая модель'}</strong><p>{status.latest_successful_fetch ? `Последняя успешная загрузка новостей: ${formatSourceDate(status.latest_successful_fetch)}` : 'Успешная загрузка новостей пока не подтверждена.'} · Материалов: {status.news_count}</p>
      {status.source_errors > 0 && <p>Источников с ошибками: {status.source_errors}. Последняя успешная загрузка, если была, сохраняется.</p>}
      {status.official_sources && <details className="publisher-status"><summary>Проверить источники новостей</summary><ul><li>Банк России · {status.cbr?.last_attempt?.status === 'ok' ? 'данные получены' : 'статус требует проверки'}</li>{status.official_sources.map(source => <li key={source.source_id}>{publishers[source.source_id] ?? 'Дополнительный источник'} · {source.status === 'ok' ? 'данные получены' : source.status === 'error' ? 'ошибка обновления' : 'статус требует проверки'}{source.last_success_at ? ` · последняя загрузка ${formatSourceDate(source.last_success_at)}` : ''}</li>)}</ul></details>}
    </div>
    {live && stop && <div className={`quota-status ${stop.blocked ? 'quota-blocked' : ''}`}><strong>{stop.blocked ? stop.stop_code === 'budget_exceeded' ? 'Дневная квота исчерпана' : 'Новые запросы приостановлены' : 'Общая квота — 4 запроса в сутки'}</strong><p>{stop.blocked && stop.stop_code !== 'budget_exceeded' ? 'Сохранённые результаты доступны. Для новых запросов нужна проверка оператора.' : `Сброс в ${stop.reset_time_msk} МСК. Каждый новый анализ или ответ может расходовать запрос; повторное открытие использует сохранённый результат.`}</p></div>}
    {local && <div className={`quota-status ${stop?.blocked ? 'quota-blocked' : ''}`}><strong>{status.local_model?.display_name ?? 'Модель сервера'}</strong><p>Расчёт выполняется на вашем сервере и может занять несколько минут. Повторное открытие использует сохранённый результат.</p>{status.local_model?.hackathon_allowlisted === false && <p>Модель отсутствует в разрешённом списке кейса. Этот маршрут используется как отдельный эксперимент.</p>}{stop?.blocked && <p>{['output_invalid','operation_failed'].includes(stop.stop_code ?? '') ? 'Модель выдала ответ, не прошедший проверку. Новые генерации остановлены до разбора результата.' : stop.stop_code === 'unknown_outcome' ? 'Исход запроса не подтверждён. Автоматический повтор запрещён; нужна проверка оператора.' : 'Новый локальный запрос временно недоступен: дождитесь результата текущей операции или проверьте её статус.'}</p>}</div>}
  </section>;
}
