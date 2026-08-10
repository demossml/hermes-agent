# Secretary Mail — Reply + Send (S8)

Черновик ответа + кнопки Отправить/Отклонить. DRY_RUN=1 по умолчанию.

## Файлы

| Файл | Назначение |
|------|-----------|
| `tools/secretary/mail_inbox.py` | +230 строк: `fetch_body()`, `send_mail()`, `_extract_body()`, SMTP/himalaya send |
| `tools/secretary/mail_draft.py` | `generate_draft()` — шаблонный черновик (без LLM) |
| `gateway/platforms/telegram.py` | 3 новых метода: `_handle_mail_reply/send/reject` |
| `tests/secretary/test_mail_send.py` | 8 unit-тестов |

## API

### `fetch_body(mail_id) -> dict`

```python
{
    "subject": str,
    "from_addr": str,
    "from_name": str,
    "date": str,
    "body_text": str,   # plain text, max 3000 chars
}
```

### `send_mail(to_addr, subject, body) -> dict`

```python
{"sent": bool, "dry_run": bool, "details": str}
```

- `SECRETARY_MAIL_DRY_RUN=*** (default): writes `[DRY_YUN] would send to ...` to log
- `SECRETARY_MAIL_DRY_RUN=*** реальная отправка через SMTP или himalaya

### `generate_draft(mail_dict, sender_name="") -> str`

Шаблонный генератор (без LLM):

- Определяет тему: `Re: оригинальная_тема`
- Приветствие: `Здравствуйте, {имя}!`
- Тело: детектит вопрос/встречу/документы/спасибо → подходящий ответ
- Подпись: `С уважением, {sender_name}`

## Telegram UX

1. Список писем → кнопки [✉️ Alice], [✉️ Bob] для первых 5
2. `mail:reply:<id>` → черновик + [✅ Отправить] [❌ Отклонить]
3. `mail:send:<id>` → DRY_RUN: «[DRY_RUN] would send to alice@...: «Re: ...»»
4. `mail:reject:<id>` → «Отправка отменена» + [Почта] [Меню]

## Безопасность

- Отправка только после callback (кнопка «Отправить»)
- DRY_RUN=1 блокирует реальную SMTP-отправку
- mail_id проверяется через IMAP fetch (не path traversal)
- ACL как у всех callback-ов

## Чеклист

- [x] reply → draft в чате
- [x] reject → без отправки
- [x] send в dry-run → «[DRY_RUN] would send to …»
- [x] generate_draft: 5 шаблонов (вопрос/встреча/документы/спасибо/общий)
- [x] Все тесты проходят (50/50)
- [x] list/digest/menu не сломаны

## Follow-up (S9)

Кнопка [📬 Без ответа] в подменю почты.

### `list_awaiting_reply(days=3, limit=15) -> list[dict]`

MVP-эвристика:
- IMAP: `BEFORE N days` поиск в INBOX
- Исключает отправителей, которым мы уже ответили (проверка Sent folder)
- himalaya: фильтр по дате из envelope list
- `days` из `prefs.followup_days` (default 3)

### Telegram: `mail:followup`

- Список писем + кнопки [✉️] для reply (reuse S8 draft flow)
- «Нет писем без ответа» если пусто
