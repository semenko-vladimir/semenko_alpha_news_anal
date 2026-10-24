# RSS adapter ЦБ

`cbr_rss.SourceAdapter.fetch(since, cursor)` читает фиксированный RSS URL ЦБ и возвращает `SourceBatch.status/error`. Неподдерживаемый cursor даёт ошибку. `parse(bytes)` работает без сети; тесты передают fixtures. Adapter использует стандартную библиотеку Python. API приложения читает подготовленную БД и не вызывает fetch.

`SourceItem` сохраняет title, body_text, canonical_url, guid, source_id, content_hash и исходную/UTC дату публикации. Дата публикации не устанавливает дату события. Неизвестные значения остаются null; дата без timezone остаётся неизвестной. `since` требует timezone. Одинаковые заголовки не объединяют разные события. Одинаковые URL/GUID и content deduplicate; конфликтующие payload сохраняются с устойчивыми ID. Fixtures имеют `synthetic=true` и `provenance=synthetic_fixture`.

Fetch ограничивает timeout 12 секундами и чтение 1 MiB плюс один байт для обнаружения превышения. Redirect запрещён. Parser ограничивает глубину и число XML-элементов и отклоняет DTD/entities. Canonical URL допускает только HTTPS cbr.ru, без query, userinfo и fragment. HTML script/style не попадает в текст. Оператор проверяет права перед загрузкой или показом полного текста новости. Тесты fixtures не подтверждают доступность live-источника или права на него.

## Нормализация

`ingestion.source_bridge.normalize()` преобразует `SourceItem` в `EventSourceRecord` и optional `Evidence` без сети и изменения публичного DTO. Он сохраняет происхождение, даты, upstream hash и synthetic flag. SHA-256 текста охватывает title/body; `event_date=None`. Missing-поля перечисляются в `unknown_fields`. Adapter не устанавливает связь компании с событием, подтверждённую экспозицию или позицию банка.

Точные дубли исчезают, конфликтующие payload под одним source ID получают устойчивые event IDs. Неверные значения, даты и превышение лимитов дают структурную ошибку; текст не обрезается. Инструкции внутри источника остаются данными. `validated` означает проверку структуры и происхождения, а не истинность вывода.
