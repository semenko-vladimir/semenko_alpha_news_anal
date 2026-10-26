# SSH broker модели

Broker на стандартной библиотеке Python принимает JSON `task_id`, `model`, `prompt` через stdin и возвращает безопасный JSON-результат. Код фиксирует шесть разрешённых моделей и URL `https://api.alfahack-gateway26.ru/v1`. Исторический primary `https://changellenge.up.railway.app/v1` не служит автоматическим fallback. Вход не меняет headers, URL, tools, пути или команды. Prompt ограничен 12000 символами, output 512 токенами. Configure и health не вызывают модель.

## Настройка оператора

Оператор развёртывает broker отдельно и задаёт SSH host и абсолютный путь установки. Нейтральные defaults клиента: `model-broker`, `/opt/alpha-broker/broker.py`. Python-клиент читает `ALPHA_BROKER_HOST` и `ALPHA_BROKER_SCRIPT`; PowerShell wrapper принимает `-BrokerHost` и `-BrokerScript`. [Настройка приложения](../../docs/setup.md) описывает синтаксис. Эти значения не меняют модели, endpoint или квоту.

После установки пользователь вводит ключ скрытым интерактивным вводом. Ключ не передают через argv, env приложения или логи. Пример с нейтральными значениями:

```powershell
powershell -NoProfile -File scripts/model-broker.ps1 -Action configure
powershell -NoProfile -File scripts/model-broker.ps1 -Action health
powershell -NoProfile -File scripts/model-broker.ps1 -Action status
```

`configure -Replace` явно заменяет ключ. При `GetPassWarning` broker завершает настройку без ввода с отображением. Broker хранит credential в `~/.config/alpha-hack-broker/upstream.key` с правами 0600, каталог 0700, состояние в `~/.local/state/alpha-hack-broker`. Администратор учётной записи технически может прочитать эти файлы; схема не изолирует credential от него.

`health` сообщает наличие credential без значения. `status` читает агрегированные дневные счётчики через read-only SQLite, без credential и содержимого jobs; он не сбрасывает квоту и не показывает фактический рублёвый баланс команды. Пока ключ не настроен, `health` возвращает `credential_configured=false`, run завершается локально с `credential_missing`.

## Выполнение

Broker читает ключ только для HTTPS Authorization. Redirect и retry запрещены; upstream timeout 90 секунд. Он ограничивает response двумя миллионами байт и резервирует до четырёх уникальных task IDs за московский бюджетный день со сбросом в 03:00. Task ID привязан к SHA-256 модели и prompt; конфликт отклоняется до сети. Новый timeout сохраняет `upstream_timeout`, прежняя незавершённая reservation возвращает `previous_request_unresolved` без повтора. Миграция SQLite сохраняет jobs и квоту. Клиент передаёт job через stdin, модельный текст остаётся данными.

Общий бюджет организаторов 500 рублей в сутки на команду отличается от локального лимита попыток. Usage включает только известные неотрицательные integer counters. Broker не возвращает upstream error body или traceback. На budget/auth/unknown outcome остановите операцию. Новая задача не служит обходом неопределённого исхода или лимита.

Для DeepSeek код передаёт `reasoning_effort=none` с пределом 512 output токенов; остальные модели сохраняют свои параметры. [Документация провайдера](https://api-docs.deepseek.com/api/create-chat-completion/) описывает параметр, поддержку gateway проверяют отдельно. Пустой content завершает запрос ошибкой с сохранённым безопасным usage; он не разрешает retry. Изменение политики не повторяет исторические cached jobs.

## Offline-проверки

Из корня выполните `python -B -m unittest discover -s scripts/hackathon_broker -v`. Тесты используют fake upstream и synthetic canary. Они не подтверждают доступность реальной модели, качество, тарификацию или изоляцию от администратора. Обычный synthetic quickstart приложения не требует broker или SSH.
