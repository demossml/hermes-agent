# Silence + Archive Bridge

Набор изменений для режима «читать и молчать»: архив сообщений группы без вызова LLM, когда нет @mention.

## Что делает

1. **Расширенные маркеры тишины** (`gateway/response_filters.py`) — `(SILENCE)`, `(SILENT)`, `(NO REPLY)`, `(NO MESSAGE)`, `NO_MESSAGE`, `[NO_REPLY]` добавлены в `LIVE_GATEWAY_SILENT_MARKERS`. Если агент всё же был вызван и вернул такой маркер — доставка подавляется.

2. **Мост archive_bridge** (`gateway/archive_bridge.py`) — новый модуль. Позволяет архивировать входящие сообщения через `message_archive` plugin без запуска агента. Работает через `build_hook_context()` + `archive_message_context()` — формат совместим с `hooks/message_archiver`.

3. **Archive-only в группах** (`gateway/platforms/telegram.py`) — в трёх обработчиках (TEXT, LOCATION, MEDIA) после `_observe_unmentioned_group_message()` добавлен fire-and-forget вызов `archive_message_context()`. Если `require_mention: true` и сообщение без @mention — оно архивируется, агент не запускается.

## Конфиг (минимум)

```yaml
telegram:
  require_mention: true
  observe_unmentioned_group_messages: true

message_archive:
  enabled: true
  silence_without_reply: true
  db_path: ~/.hermes/archive/messages.db
  files_dir: ~/.hermes/archive/files
  max_file_mb: 40
  chats:
    - "-100YOUR_GROUP_ID"
```

## Поведение

| Ситуация | Результат |
|----------|-----------|
| Группа, нет @mention | Сообщение → архив, без ответа |
| Группа, есть @mention | Обычный agent |
| Личка | Обычный agent |
| Агент вернул `NO_REPLY` | Доставка подавлена (upstream) |

## Зависимости

- `message_archive` plugin (из `hermes-archive-bot`) — должен быть установлен отдельно
- `require_mention: true` в секции `telegram`
- `observe_unmentioned_group_messages: true` + allowlist чатов

## Файлы

- `gateway/archive_bridge.py` — новый
- `gateway/response_filters.py` — расширен `LIVE_GATEWAY_SILENT_MARKERS`
- `gateway/platforms/telegram.py` — archive-вызовы в `_handle_text_message`, `_handle_location_message`, `_handle_media_message`
- `gateway/run.py` — уже содержал `full_message`/`media_urls`/`media_types`/`message_id` в `agent:start` hook
