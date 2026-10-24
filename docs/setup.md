# Настройка

## Синтетический пример

Следуйте [первому запуску](../README.md). Python 3.14.3 соответствует контейнерному runtime. Генератор использует стандартную библиотеку и локальный модуль новостей. SQLite должен поддерживать FTS5. Docker собирает API, React и Nginx; API получает входы через read-only mounts и хранит изменяемое состояние в именованных volumes. LIVE выключен в Compose.

`compose.env` содержит пути и настройки доступа к локальному примеру, без ключей. Генератор создаёт четыре вымышленных профиля, новости и пустые таблицы платежей и связей. Если папка существует, выберите новый `--output` и используйте её `compose.env` в последующих командах. Выберите свободный `--port`.

## Запуск без Docker

Нужны Python и Node.js 24 с npm. После создания `.venv` из README:

```powershell
.venv/Scripts/python.exe -m pip install -r deploy/runtime-requirements.lock
.venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt
npm --prefix frontend ci
npm --prefix frontend run build
.venv/Scripts/python.exe -B scripts/run_mvp.py
```

Откройте http://127.0.0.1:8010/. Команда читает `.tmp/mvp-synthetic/`, копирует новостную БД и пишет состояние в `.tmp/mvp-local/`. Остановите сервер через Ctrl+C. Генерацию синтетики выполните до запуска. Отсутствие обязательного файла завершит запуск с ошибкой.

Для API без интерфейса задайте операторские параметры из следующего раздела и выполните `.venv/Scripts/python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000` в PowerShell. В Unix используйте `.venv/bin/python`. Frontend dev server (`npm --prefix frontend run dev`) проксирует `/api` на этот порт.

## Собственные материалы

Перед использованием проверьте права на материалы. Храните CSV и SQLite вне публичного состава; не помещайте их в образ. Приложение не загружает банковский контекст из внешних систем.

`run_mvp.py` принимает явные `--companies`, `--payments`, `--links`, `--news`, `--bank`, `--runtime` и `--port`. Платежи, связи и банковский индекс в API необязательны; локальный launcher по умолчанию ожидает синтетические файлы платежей и связей. Для него передайте существующие файлы. Runtime должен находиться в `.tmp/` и отличаться от входных файлов.

CSV использует UTF-8 с допустимым BOM и разделитель `;`. Точные поля и правила в [профилях](../backend/profiles/README.md). Allowlist API: `U_32424`, `U_135`, `U_42266`, `U_754`; другие ID этот прототип не обслуживает. Новостная БД должна иметь схему `NewsRepository`, банковская БД схему `BankRepository`. [Новости](../backend/news_service/README.md) и [банковский индекс](../backend/bank_corpus/README.md) описывают подготовку. Отсутствующий индекс даёт `not_checked`; пустой поиск даёт `not_found`.

В Compose замените значения `MVP_COMPANIES_FILE`, `MVP_PAYMENTS_FILE`, `MVP_LINKS_FILE`, `MVP_NEWS_DB_FILE` в отдельном локальном env-файле абсолютными путями к своим материалам. Для банковского индекса добавьте `MVP_BANK_FILE` и передайте `-f compose.yaml -f compose.bank.yaml` перед `--env-file`. Укажите `ALPHA_MVP_LOCAL_ACCESS=1` только для локального примера. Порт привязан к loopback. Новый входной набор запускайте с отдельным Compose project name (`-p`) для изоляции volumes; существующие volumes хранят состояние прежнего запуска.

Для прямого API используйте `ALPHA_ANALYSIS_ENGINE=mvp_offline`, `ALPHA_MVP_LOCAL_ACCESS=1`, `ALPHA_DB_PATH` и `ALPHA_MVP_{COMPANIES,PAYMENTS,LINKS,NEWS,BANK}_PATH`. HTTP-клиент не выбирает пути и транспорт. Без настройки локального доступа сервер закрывает бизнес-маршруты.

## Транспорт модели

Обычный запуск использует fake и не требует SSH. Оператор отдельно развёртывает [broker](../scripts/hackathon_broker/README.md). Python transport читает только `ALPHA_BROKER_HOST` и `ALPHA_BROKER_SCRIPT`: нейтральные defaults `model-broker` и `/opt/alpha-broker/broker.py`. Задайте SSH host из собственной конфигурации и абсолютный путь установки. Host допускает буквы, цифры, точку, дефис и подчёркивание; путь допускает эти символы и `/`, без пробелов и `..`. Клиент запрещает shell-метасимволы; prompt передаёт через stdin при `shell=False`.

В PowerShell wrapper передайте те же значения параметрами `-BrokerHost` и `-BrokerScript`. Эти параметры не включают LIVE. Ключ пользователь вводит скрытым интерактивным configure на broker host; ключ не должен попадать в env приложения, команды или логи. Broker сохраняет фиксированный список моделей, квоту, endpoint и запрет retry. Оператор отдельно разрешает live и передачу клиентского контекста и назначает устойчивый task ID. На auth/budget/unknown outcome остановите зависимую операцию. Повтор с новым ID не решает неопределённый исход.
