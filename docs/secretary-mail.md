# Secretary Mail (S6)

Чтение почты через Telegram-меню. Два backend-а: imaplib (по умолчанию) и himalaya CLI.

## Файлы

| Файл | Назначение |
|------|-----------|
| `tools/secretary/mail_inbox.py` | Модуль чтения: `list_recent()`, `format_mail_list()`, `is_configured()` |
| `gateway/platforms/telegram.py` | Telegram UI: `menu:mail` → submenu, `mail:list:N` → список писем |
| `tests/secretary/test_mail_inbox.py` | 15 unit-тестов (форматтер, парсер, конфиг) |

## Настройка (.env)

```bash
# Backend: imap (default) или himalaya
SECRETARY_MAIL_BACKEND=imap

# Для IMAP:
SECRETARY_MAIL_IMAP_HOST=imap.gmail.com
SECRETARY_MAIL_IMAP_PORT=993
SECRETARY_MAIL_EMAIL=you@gmail.com
SECRETARY_MAIL_PASSWORD=your-app-password

# Для himalaya: только BACKEND, конфиг в ~/.config/himalaya/config.toml
SECRETARY_MAIL_BACKEND=himalaya
```

## API

### `list_recent(hours=12, limit=20) -> list[dict]`

Возвращает список писем за N часов. Каждое письмо:

```python
{
    "id": "12345",           # IMAP UID
    "from_addr": "alice@example.com",
    "from_name": "Alice",
    "subject": "Re: Project update",
    "date_iso": "2026-08-10T09:30:00+00:00",
    "date_display": "Сегодня 09:30",
    "snippet": "",
}
```

Бросает `RuntimeError` если не настроен.

### `format_mail_list(emails, hours) -> str`

Форматирует список для Telegram (max 15 писем, обрезание тем).

### `is_configured() -> bool`

Проверяет наличие креденшелов.

## Telegram UX

- `menu:mail` → подменю:
  ```
  [🌙 За ночь (12ч)] [📆 За 48ч]
  [🔄 Обновить]      [⬅️ Меню]
  ```
- Без `.env` → сообщение с инструкцией по настройке
- `mail:list:12` / `mail:list:48` → загрузка + список
- После списка → кнопки [Обновить] [Меню]

## Тесты

```
tests/secretary/test_mail_inbox.py  — 15 тестов
tests/secretary/test_user_store.py  — 19 тестов
Все: 34 passed
```

## Чеклист

- [x] menu:mail → кнопки (4 шт)
- [x] Без .env → понятная ошибка с инструкцией
- [x] С валидным .env → список или «пусто»
- [x] Форматтер: дата (Сегодня/Вчера/день мес), от кого, тема
- [x] Обрезание длинных тем и пагинация (>15 → «... и ещё N»)
- [x] S1–S4 тесты не сломаны (34/34)
