# Локальные профили MVP API

Импорт `profiles` не открывает файлы. Только оператор задаёт пути; JSON job и пользовательский текст их не выбирают. Пример подключения из backend:

```python
from profiles import ProfileRepository

repository = ProfileRepository(
    companies_path=operator_companies_path,
    payments_path=operator_payments_path,  # optional, None means not loaded
    links_path=operator_links_path,        # optional, None means not loaded
)
profiles = repository.load(server_allowed_ids)
profile = repository.get(authorized_company_id)
```

`load(allowed_ids)` возвращает отсортированный по company_id список dataclass `CompanyProfile`: company_id, category (`TRADE`/`TRANSPORT`), обезличенный display_name, version, features:dict, unknowns:list[str], source_refs:list[dict]. Отбор категории по двум первым цифрам основного ОКВЭД: 45–47 / 49–53. Роль отсутствует: CFO/COO выбирается явно в приложении. Сам repository не является IAM: APP обязан формировать server allowlist и проверять доступ текущего пользователя перед get/ответом.

`get` доступен только для последнего успешного load; неизвестный ID даёт KeyError. Повторный load заменяет scope, любой неуспешный load очищает его. Возвращаемые объекты — независимые копии. Репозиторий предназначен для конфигурационной загрузки при запуске, не для конкурентного изменения allowlist из запросов; thread safety при reload не обещается.

Файлы UTF-8-sig, delimiter `;`, читаются потоково, без pandas/сети. Проверяются точный набор уникальных заголовков, ширина записей, синтаксис U/F ключей. Типы и дубли/конфликты проверяются для выбранных компаний и затрагивающих их платежей/связей. Несвязанные конфликтующие профили не объединяются и не мешают загрузке разрешённого scope. Любая повторная выбранная компания, платёжная пара или F/U связь отклоняется, даже идентичная: нет данных для выбора версии/безопасной агрегации. Ключи внешних контрагентов допустимы, их профиль не выдумывается. Отсутствующий выбранный профиль — ошибка, а не synthetic fallback. Ошибки содержимого — ProfileDataError, ошибки файла/кодировки — штатные OSError/UnicodeError.

`features` содержит main_okved_code/name/division, eligibility=`unknown`, revenue с value_raw в исходной числовой шкале и units/currency/year=null, flags с исходными именами, attributes с исходными именами прочих полей. NEGATIVEMARK по словарю означает признак ключевого собственника; это не негативный риск и не связь физлица с владением. Категориальные градации и GROUP_ID остаются исходными строками, не точной численностью/возрастом или юридическим контролем. Пропущенные значения attributes дают null и missing в unknowns.

При наличии payments появляются payment_observations: число различных внешних входящих/исходящих ID, self_rows и observed_rows. Это топология наблюдаемой выборки, без сумм, долей закупок и выводов о поставках. При links появляется только observed_linked_person_count; F ID/география физлиц не выдаются. Неизвестный источник отличается от источника с нулём подходящих записей. Неизвестные единицы/валюта/период, eligibility и ограничения интерпретации перечислены явно. Регистрация не устанавливает географию операций, кредитный флаг не задаёт ставку.

`version` — sha256 канонического snapshot: версия схемы, хеши всех явно переданных файлов, company_id, признаки, unknowns и refs. `source_refs` содержит source_id=`csv:<kind>:<file sha256>`, locator=`<basename>#record=<N>`, fingerprint=`sha256:<file sha256>`, synthetic=false. N — порядковый CSV record с заголовком как record 1, а не физическая строка: quoted newline поддерживается. Нет публичного URI/даты, так как они не установлены. Хеши до/после чтения проверяют неизменность исходников. Обезличенный реальный CSV не помечается synthetic.

Проверки: `python -X utf8 -m unittest backend.test_profiles -v` из корня. Только synthetic fixtures создаются в ignored `.tmp/tests/profiles`. Реальные derived snapshots допускаются исключительно в ignored `data/processed/profiles/`; module сам их не пишет. Этот README не содержит клиентских строк.
