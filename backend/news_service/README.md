# Обновление новостей оператором

Финальный runtime дополнительно использует `backend.news_service.product_refresh` и реестр `data/catalog/news_sources.json`: фиксированный CBR RSS и разрешённые официальные HTML-источники, ограниченный объём/время/число статей, безопасные редиректы и last-good. `scripts/update_product_data.py` запускает один цикл без LLM. `backend/product_server.py` выполняет такой цикл при старте и раз в час, сохраняет обновления после рестарта. Состояние доступно через `/api/v1/data-status`; сетевые ошибки и неизвестные даты не скрываются. Старая CLI/seed-схема ниже относится к прежнему runtime.

Команды запускаются из корня проекта. CLI принимает только явный путь SQLite и поддерживает две формы:

```powershell
python -m backend.news_service refresh --database data/processed/news/news.sqlite3
python -m backend.news_service status --database data/processed/news/news.sqlite3
python -m backend.news_service status --database data/processed/news/news.sqlite3 --stale-after-seconds 43200
```

`refresh` делает ровно один запрос к фиксированному RSS ЦБ через существующий `SourceAdapter` с ограничениями объёма и времени, без повтора. Успешная загрузка добавляет новости идемпотентно и сохраняет конфликтующие версии. Ошибка/blocked записывает диагностику попытки и сохраняет последние полученные новости и время последнего успеха. Exit code: 0 при успехе, 2 при blocked/error. JSON содержит только статус, коды ошибки, времена, freshness и counts; тексты новостей и пути БД не печатаются.

`status` не обращается к сети и открывает существующую БД read-only; отсутствующую БД не создаёт. `fetch_freshness` описывает время последнего успешного получения: `fresh`, `stale`, `future` или `unknown`. Отдельный объект `publication_age` группирует число публикаций с неизвестной/будущей датой, старше порога и в пределах порога. Это разные часы: дата публикации не подменяет возраст fetch. Порог задаётся `--stale-after-seconds` (по умолчанию 86400). Тесты используют передаваемые часы в сервисе и repository API.

Из корня после подготовки локальной конфигурации Compose:

```powershell
docker compose exec api python -m backend.news_service status --database /var/lib/alpha-news/news.sqlite3
docker compose exec api python -m backend.news_service refresh --database /var/lib/alpha-news/news.sqlite3
docker compose exec api python -m backend.news_service status --database /var/lib/alpha-news/news.sqlite3
```

Движок API читает тот же рабочий файл `/var/lib/alpha-news/news.sqlite3`, поэтому добавленные записи доступны следующему анализу без рестарта контейнера. `deploy/entrypoint.sh` при каждом старте копирует исходную базу только для чтения `MVP_NEWS_DB_FILE` в runtime volume; следовательно, после рестарта ручные обновления рабочей базы заменятся исходной версией. Чтобы закрепить обновлённый набор при пересоздании/рестарте, оператор должен обновить исходный файл `MVP_NEWS_DB_FILE` контролируемым процессом до старта. CLI не меняет seed и compose/entrypoint.

Источник и тексты считаются недоверенными данными. CLI не классифицирует содержание и не доказывает полезность модели, полноту RSS, права на полный feed или актуальность самих событий. Импорт модуля и `status` не выполняют fetch. Адресные локальные проверки:

```powershell
python -B -m unittest backend.test_news_service backend.test_news_cli backend.test_cbr_rss -v
```
