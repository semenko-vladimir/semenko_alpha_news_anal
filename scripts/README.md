# Скрипты

- `prepare_mvp_demo.py` создаёт новый synthetic-набор в `.tmp/` и Compose env без ключей.
- `run_mvp.py` обслуживает собранный UI и offline API на одном loopback-порту.
- `verify-agent-chain.py` проверяет цепочку с fake transport.
- `verify-local-demo.py` проверяет прежний детерминированный деморежим через HTTP.
- `verify-mvp.py --offline` запускает расширенные проверки с отдельно подготовленными входами и браузерным окружением.
- `run-local-demo.ps1` и `stop-local-demo.ps1` управляют собственными dev-процессами прежнего деморежима. Для launcher создайте `.venv` в Windows и установите frontend-зависимости.
- `model-broker.ps1` предоставляет операторские health/status/configure; обычный запуск приложения не требует broker.

[Установка](../docs/setup.md) · [Проверки](../docs/testing.md)
