---
name: secretary-reports
description: "Secretary-mode report scenarios: daily/weekly summaries, keyword search, document reports, scheduled cron jobs. Triggered by: отчёт, сводка, архив, что было в группе, секретарь."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Secretary, Reports, Archive, Cron, Search]
    triggers:
      - "отчёт"
      - "сводка"
      - "архив"
      - "что было в группе"
      - "секретарь"
      - "report"
      - "summary"
      - "digest"
---

# Secretary Reports

Готовые сценарии для режима secretary. Каждый сценарий — точный вызов
`archive_query` с правильными параметрами.

## Сценарий 1: отчёт за период

Фразы: «отчёт за вчера», «отчёт за сегодня», «отчёт за неделю»,
«сводка за выходные», «daily summary», «weekly digest».

### Шаг 1: вычислить since/until в UTC

- вчера: `since = (today - 1 day)T00:00:00Z`, `until = todayT00:00:00Z`
- сегодня: `since = todayT00:00:00Z`, `until = now`
- неделя: `since = (today - 7 days)T00:00:00Z`, `until = now`
- всегда UTC, формат ISO-8601: `2026-08-03T00:00:00Z`

### Шаг 2: archive_query

```
archive_query(
  since="2026-08-03T00:00:00Z",
  until="2026-08-04T00:00:00Z",
  limit=500
)
```

### Шаг 3: постобработка результата

- Сгруппировать сообщения по `msg_type` (text, photo, voice, document_*).
- Топ-10 ключевых слов из поля `extracted_text` (частотный анализ).
- Список документов: `original_name`, `msg_type`, `sender_id`.
- Если `count = 0` — честно: «найдено 0 сообщений за этот период».

### Шаг 4: ответ пользователю

```
Сводка за 3 августа 2026 (UTC):
• Всего сообщений: 142
• Текстовых: 98, фото: 23, голос: 5, документов: 16
• Ключевые темы: деплой, багфикс, API, dokumenty, staging
• Документы: spec_v2.pdf, otchot_август.xlsx, photo_2026-08-03.jpg
```

## Сценарий 2: поиск по теме в группе

Фразы: «найди всё про {тема} в группе {id}», «что писали про X в чате -100…»,
«search for {topic} in {chat_id}».

### Шаг 1: определить chat_id

- Если пользователь назвал группу по имени — найти `chat_id` через
  `archive_admin("list_chats")` или спросить.
- Telegram ID: обычно `-1001234567890`.
- Если chat_id не указан — искать по всем monitored чатам.

### Шаг 2: archive_query с keyword FTS

```
archive_query(
  keyword="{тема}",
  chat_id="-1001234567890",
  since="...",       # опционально, по умолчанию 30 дней
  until="...",
  limit=200
)
```

### Шаг 3: ответ

- Список релевантных сообщений: `timestamp`, `sender_id`, `msg_type`,
  фрагмент `extracted_text` (до 200 символов).
- Группировка по дате.
- Если результатов > limit — предложить сузить период или keyword.

## Сценарий 3: сводка по документам

Фразы: «сводка по документам за месяц», «какие документы присылали»,
«все pdf за неделю», «document report for {period}».

### Шаг 1: archive_query с msg_type фильтром

```
archive_query(
  msg_type=["document_pdf", "document_docx", "document_xlsx"],
  since="2026-07-01T00:00:00Z",
  until="2026-08-01T00:00:00Z",
  limit=1000
)
```

### Шаг 2: ответ

- Таблица: `original_name`, `msg_type`, `sender_id`, `timestamp`.
- Если есть `extracted_text` — первые 100 символов.
- Отсортировать по дате (сначала новые).
- Итог: общее количество документов, уникальных отправителей.

## Сценарий 4: плановый отчёт (cron)

Фразы: «настрой ежедневный отчёт», «присылай сводку каждое утро»,
«weekly digest every Monday», «еженедельный отчёт по группе X».

### Шаг 1: сформировать команду cron

НЕ выполняй shell. Выведи пользователю готовую команду:

```
hermes cron create "0 9 * * *"
  --prompt "Сделай отчёт за вчера по всем monitored чатам.
  archive_query с since=(today-1day)T00:00:00Z, until=todayT00:00:00Z, limit=500.
  Сгруппируй по msg_type, топ ключевых слов, список документов."
  --deliver origin
  --skills secretary-reports
```

### Шаг 2: пояснить

- `0 9 * * *` — каждый день в 9:00 UTC.
- `0 9 * * 1` — каждый понедельник (weekly).
- `--deliver origin` — результат придёт в тот же чат.
- `--skills secretary-reports` — этот skill будет загружен при выполнении.

## Сценарий 5: статус архива

Фразы: «что с архивом», «статус архива», «archive status», «какие чаты».

```
archive_admin("status")
```

Ответ: `enabled`, `chat_mode` (all/allowlist), `monitored_chats`,
количество сообщений в БД, `unique_chats`, `max_file_mb`.

## Важно

- Всегда UTC, ISO-8601 с секундами: `2026-08-03T00:00:00Z`.
- `count = 0` — не выдумывай, честно сообщи.
- Крупные ответы → сводка сначала, детали по запросу.
- Не храни extracted_text в ответе если он > 500 символов — обрежь.
- Если archive_query недоступен → скажи «archive_query не настроен».
