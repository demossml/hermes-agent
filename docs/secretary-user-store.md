# Secretary User Store

Хранилище prefs Telegram-пользователя секретаря. SQLite, одна таблица.

## Расположение

- **Модуль:** `gateway/secretary_user_store.py`
- **БД:** `{HERMES_HOME}/secretary_users.db`
- **Тесты:** `tests/secretary/test_user_store.py` (19 тестов)

## Схема

```sql
CREATE TABLE secretary_users (
    telegram_id    TEXT PRIMARY KEY,   -- Telegram user ID
    display_name   TEXT,               -- имя пользователя
    timezone       TEXT DEFAULT 'Europe/Moscow',
    digest_enabled INTEGER DEFAULT 1,  -- 0/1
    digest_hour    INTEGER DEFAULT 9,  -- 0–23
    onboarding_step TEXT,              -- NULL | welcome | name | tz | email_skip | digest | done
    prefs_json     TEXT DEFAULT '{}',  -- произвольный JSON
    updated_at     TEXT                -- ISO-8601
);
```

## API

### `get_user(telegram_id: str) -> dict | None`

Возвращает словарь пользователя или None. Ключи: `telegram_id`, `display_name`, `timezone`, `digest_enabled` (int 0/1), `digest_hour` (int), `onboarding_step`, `prefs` (dict — распаршенный `prefs_json`), `prefs_json` (строка), `updated_at`.

```python
from gateway.secretary_user_store import get_user

user = get_user("123456789")
if user:
    print(user["display_name"], user["timezone"])
```

### `upsert_user(telegram_id: str, **fields) -> dict`

Создаёт или обновляет запись. Возвращает полный словарь пользователя.

Допустимые поля:
- `display_name` (str)
- `timezone` (str, IANA)
- `digest_enabled` (bool/int → 0/1)
- `digest_hour` (int, 0–23, clamped)
- `onboarding_step` (str, одно из: welcome/name/tz/email_skip/digest/done/None)
- `prefs_json` (dict или JSON-строка)

```python
from gateway.secretary_user_store import upsert_user

# Создать
upsert_user("123", display_name="Alice", timezone="Asia/Tokyo")

# Обновить одно поле — остальные не трогает
upsert_user("123", digest_hour=8)
```

### `is_onboarded(telegram_id: str) -> bool`

True если `onboarding_step == 'done'`.

```python
from gateway.secretary_user_store import is_onboarded

if is_onboarded("123"):
    show_main_menu()
else:
    start_onboarding()
```

### `set_onboarding_step(telegram_id: str, step: str | None)`

Установить шаг онбординга. Создаёт запись, если пользователя ещё нет.

## Thread safety

Используется `threading.Lock()` на запись и thread-local connections. Несколько потоков могут читать одновременно.

## Отличие от Hermes profiles

- `secretary_users` — это prefs пользователя Telegram (имя, TZ, настройки дайджеста). Одна БД на control HERMES_HOME.
- `secretary_router` — routing pointer Telegram user → secretary-* profile.
- Hermes `profiles/` — изолированные окружения с отдельными memory/skills/cron.

Эти три слоя не пересекаются.
