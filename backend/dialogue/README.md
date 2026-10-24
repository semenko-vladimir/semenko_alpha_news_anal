# DIALOG / MVP API

Независимое от HTTP ядро. Импорт не вызывает модель/инструменты/сеть. Зависимости только stdlib и исключения `analysis.pipeline`.

```python
from backend.dialogue import (
    DialogueContext, DialogueSource, DialogueService, SQLiteDialogueStore,
)
from dataclasses import asdict

store = SQLiteDialogueStore("/operator/configured/local/dialogue.sqlite")
base = store.register(DialogueContext(
    company_id="allowed-company-id", role_id="CFO", insight_id="insight-id",
    event_id="event-id", snapshot_id="analysis-snapshot-id",
    sources=(DialogueSource(
        source_id="source-id", locator="page:1", kind="news",
        text="Bounded source text", company_id="allowed-company-id",
        synthetic=False,
    ),),
    question={"question_id": "question-id", "text": "Есть ли связь?",
              "kind": "exposure_confirmation"},
))
service = DialogueService(model=model_adapter, tools=tools_adapter_or_none,
                          store=store, timeout_seconds=10)
result = service.answer_question(
    company_id=authorized_company, role_id=authorized_role, insight_id="insight-id",
    question_id="question-id", answer="Ответ пользователя", idempotency_key="key-1",
)
payload = asdict(result)  # MVP API response fields; HTTP200 for returned results
result = service.ask_message(
    company_id=authorized_company, role_id=authorized_role, insight_id="insight-id",
    text="Вопрос пользователя", idempotency_key="key-1",
)
```

APP отвечает за авторизацию, allowlist и HTTP. Передавать фактически авторизованные company/role; роль и компанию нельзя извлекать из текста. `DialogueError.code` и `.http_status` — безопасные ошибки (422 invalid input, 404 scope/question, 409 changed payload). Внутренние исключения зависимостей не попадают в результат. Конструктор store принимает явный существующий родительский каталог или `:memory:`; не угадывает пути. `close()` завершает работу store.

`store.get(company_id, role_id, insight_id, version=None)` возвращает текущую/точную историческую ревизию. `register(context)` идемпотентен для исходного контекста, повтор с иной исходной версией того же insight даёт 409: новую анализ-карточку регистрировать под новым insight_id. Контекст включает scope, event, snapshot, source texts/refs, даты, вопрос и fingerprint. Вложенные значения копируются. Источники bound к компании; общие новости/банк адаптер привязывает к запрошенной компании. URI намеренно null, locator обязателен. `kind="bank_document"` обозначает банковский документ; `document_date="YYYY-MM-DD"`, `historical=True` задаёт историческую позицию. Synthetic банк не подходит как реальное банковское доказательство.

Внедрённая модель: `generate(context: Mapping) -> {"blocks": [{"kind": "ai_inference" | "bank_position", "text": str, "evidence_refs": [source_id]}], "unknowns": [str]}`. Другие поля/виды блоков отклоняются. Успешный результат добавляет отдельный observed_fact «Пользователь сообщил (независимо не проверено)…» со ссылкой на новый user_input. Модель не может создавать observed_fact/confirmed exposure. AI маркируется как сценарий; bank_position требует реальных датированных банковских ссылок, исторический блок помечается и не становится текущим мнением. Это структурная проверка происхождения, не доказательство семантической истинности текста модели.

Внедрённые инструменты необязательны: `fetch(context, limit=3) -> tuple[DialogueSource,...]`. Их context содержит только server-established company_id/role_id/event_id/snapshot_id, без пользовательского текста и команды. Один вызов fetch, один generate, без correction/retry. Адаптер должен выполнять только ограниченные read-only операции и собственную отмену/сетевой timeout; late result после deadline ядро отбрасывает, Python thread принудительно не уничтожается. Для live нужен отдельный разрешённый адаптер; этот модуль ничего не отправляет самостоятельно.

Ограничения: 2000 символов сообщения, 24 источника × максимум3000 символов; JSON контекста максимум24000, 3 результата инструмента, 6 блоков, 12000 символов ответа модели. После исчерпания контекста нужен новый анализ/insight; скрытого сжатия/удаления старых источников нет. Budget/auth → failed, timeout/неопределённый сбой → unknown_outcome. Все outcomes сохраняются и повтор с тем же key не вызывает зависимостей. Область ключа: company/role/insight/operation/key, fingerprint включает точный текст и question_id. Разные операции могут использовать один key.

SQLite сохраняет pending unknown_outcome и ревизию **до** вызова зависимостей. Прерывание процесса не разрешает новый вызов; ключ остаётся operation_incomplete. Во время исполнения из другого процесса повтор может увидеть unknown_outcome, после завершения — окончательный сохранённый результат. Сервис сериализует операции одного store внутри процесса; SQLite защищает reservation между процессами. История не перезаписывается, новые инструменты входят в отдельную итоговую ревизию. Старые CSV/источники не изменяются. Это локальное хранилище без банковского IAM; APP обеспечивает доступ и резервное копирование.

Проверка из корня: `python -B -m unittest backend.test_dialogue -v`.
