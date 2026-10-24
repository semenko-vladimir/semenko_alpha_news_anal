# Локальные новости

`NewsRepository(database_path)` хранит нормализованные RSS-записи ЦБ в SQLite. `latest(limit=10)` возвращает `EventRecord` и source_refs с source ID, URI, исходной/UTC датой публикации, временем получения, GUID, content hash, provenance и synthetic flag. Дата события остаётся неизвестной. `ingest(items)` принимает `SourceItem`; `NewsService(repository).fetch_and_ingest()` отдельно вызывает фиксированный RSS adapter ЦБ без retry; ошибка fetch возвращает `status=blocked`. API читает локальную БД без fetch.

```python
from backend.news_service import NewsRepository
repo = NewsRepository("data/processed/news/news.sqlite3")
events = repo.latest(limit=10)
```

Оператор хранит БД вне публичного состава. Новости и инструкции внутри них остаются недоверенными данными. Повторный ingest не должен дублировать тот же payload; конфликтующее содержимое сохраняет собственную версию. Детальные сценарии, границы записей, даты и ошибки проверяет `backend/test_news_service.py`.

Проверка из корня: `python -B -m unittest backend.test_news_service -v`. Тесты используют synthetic fixtures; они не подтверждают доступность live-источника, права на контент или аналитическое качество.

Конструктор и импорт не вызывают fetch. Текст для показа ограничен 500 символами; полный feed требует проверки прав. Используйте канонические `backend.sources.cbr_rss.SourceItem`, `SourceAdapter` и `parse`: старое пространство `sources.cbr_rss` содержит другие классы. Strict validation отклоняет их.
