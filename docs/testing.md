# Проверки

Установите зависимости по [инструкции](setup.md). Из корня в PowerShell:

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s backend -q
.venv/Scripts/python.exe -B scripts/verify-agent-chain.py
.venv/Scripts/python.exe -B scripts/verify-local-demo.py
.venv/Scripts/python.exe -B -m unittest discover -s tests/evaluation -q
.venv/Scripts/python.exe -B -m unittest discover -s tests/stabilization -q
npm --prefix frontend run typecheck
npm --prefix frontend run test:lifecycle
npm --prefix frontend run test:render
node --test frontend/tests/e2e-posts.test.cjs
npm --prefix frontend run build
```

Backend-тесты используют synthetic fixtures и fake transport. HTTP smoke запускает собственный loopback-сервер с отдельной SQLite и проверяет доступ, четыре company/role контекста, источники и идемпотентность. Успех этих проверок не доказывает качество LLM или наличие банковского подключения. Артефакты тестов сохраняются в `.tmp/`.

## Интеграция с локальными материалами и браузер

`scripts/verify-mvp.py --offline` запускает расширенный набор, включая банковское покрытие и браузер. Он требует три CSV в `data/raw/clients/` с именами `df_Uid_data_hack.csv`, `df_Uid_payment_hack.csv`, `df_Fid_Uid_link_hack.csv`, новостную БД `data/processed/news/news.sqlite3` и банковский индекс `data/processed/bank/index.sqlite3`. Assertions этого набора ожидают реальные локальные материалы. Синтетический quickstart не создаёт такой индекс. Проверка банковского покрытия требует соответствующего локального корпуса; из публичного клона она недоступна без этих материалов. Переносимые backend-тесты и HTTP smoke выше проверяют схемы и ошибки без него.

`npm --prefix frontend run test:e2e` также требует свежую сборку, входные файлы и банковский индекс. Установите Playwright-core отдельно в своё QA-окружение; задайте `ALPHA_PLAYWRIGHT_MODULE` абсолютным путём к модулю, `ALPHA_CHROME_PATH` путём к Chrome и `ALPHA_TEST_PYTHON` путём к Python с backend-зависимостями. Тест запускает собственные серверы на свободных портах, сравнивает хеши входов и закрывает свои процессы. Chrome работает с sandbox. Внешние запросы блокируются. Положительные сценарии используют API без mocks; ошибки интерфейса проверяются отдельными mocks.

## Метрики и оценка

[Синтетические наборы](../examples/README.md) содержат cases и labels для инженерной проверки. Проверяйте поддержку выводов источниками, атрибуцию банка, различие темы и экспозиции, полезность для роли, задержку, usage и долю остановок. Авторские labels и структурная валидация не заменяют человеческую оценку полезности.

Качество реальных LLM на новых событиях и стоимость gateway требуют отдельного исследования. Для Docker smoke нужен работающий daemon; `compose config` подтверждает только конфигурацию.
