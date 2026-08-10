# Secretary Digest (S7)

Утренний дайджест — сводка почты с доставкой в Telegram.

## Файлы

| Файл | Назначение |
|------|-----------|
| `gateway/secretary_digest.py` | `build_digest_text()`, `send_digest_to_user()`, `run_daily_digest()` |
| `gateway/secretary_user_store.py` | `get_digest_users()`, `mark_digest_sent()`, `should_send_digest()`, `last_digest_date` |
| `gateway/platforms/telegram.py` | `/digest`, `mail:digest_now`, `_handle_digest_command` |
| `tests/secretary/test_digest.py` | 6 unit-тестов |

## Как работает

1. **Сборка** (`build_digest_text`):
   - Проверяет `should_send_digest` (onboarded, digest_enabled, не слали сегодня)
   - Почта: `list_recent(hours=12, limit=10)` если настроена, иначе «не настроена»
   - Режим (dev/secretary) — опционально
   - Итог: текст с заголовком «Доброе утро, Имя!»

2. **Доставка** (`send_digest_to_user`):
   - Отправляет в Telegram DM (chat_id == telegram_id)
   - Кнопки: [Почта] [Меню]
   - `mark_digest_sent` — записывает сегодняшнюю дату

3. **Cron** (`run_daily_digest`):
   - Итерирует `get_digest_users()` (onboarded + digest_enabled)
   - Проверяет локальный час == digest_hour в timezone пользователя
   - Пропускает если уже слали сегодня (`last_digest_date`)

4. **Ручной запуск:**
   - `/digest` — force-send дайджест себе (игнорирует hour и last_digest_date)
   - `mail:digest_now` — callback из меню почты

## Cron job

Добавить в cron (раз в час или каждые 15 мин):

```python
# В gateway/run.py или отдельном скрипте
async def digest_cron():
    bot = ...  # Telegram Bot instance
    from gateway.secretary_digest import run_daily_digest
    result = await run_daily_digest(bot)
    logger.info("Digest cron: %s", result)
```

Или через Hermes cron:
```
hermes cron create --name secretary-digest --schedule "0 * * * *" \
  --prompt "Вызови run_daily_digest из gateway.secretary_digest"
```

## БД

`last_digest_date TEXT` — дата последней отправки (ISO format: `2026-08-10`).
Миграция автоматическая: при открытии БД добавляет колонку если её нет.

## Чеклист

- [x] `/digest` → сообщение с почтой + кнопки
- [x] `mail:digest_now` → то же
- [x] `digest_enabled=0` → skip (should_send_digest)
- [x] `last_digest_date` не даёт дубль в тот же день
- [x] `get_digest_users` фильтрует: только onboarded + digest_enabled
- [x] Все тесты проходят (40/40)
