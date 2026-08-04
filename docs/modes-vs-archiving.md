# Modes vs Archiving

## Инвариант: архивация НЕ зависит от mode

Message Archiver — это **фоновый сборщик данных**, подписанный на хук
`agent:start`. Он работает при двух условиях:

1. `message_archive.enabled = true` в `config.yaml`
2. `chat_id` входит в allowlist `message_archive.chats` (или allowlist не задан = все чаты)

**Архивация не проверяет текущий режим** (`dev` / `secretary`).
Переключение в `dev` **не выключает** архивацию.

## Что контролирует mode

Mode влияет **только** на поведение агента при ответе пользователю:

| Аспект | dev | secretary |
|---|---|---|
| System guidance | `DEV_GUIDANCE` — код, проект, изоляция | `SECRETARY_GUIDANCE` — архив, отчёты, поиск |
| Tool schemas | `terminal`, `file`, `delegation`, … | `todo`, `memory`, `archive_query`, `archive_admin` |
| `archive_admin` tool | ❌ недоступен | ✅ доступен |
| `archive_query` tool | ❌ заблокирован (config gate) | ✅ доступен |
| Ответ по умолчанию | Предлагает `/project new` | Предлагает `archive_query` / `archive_admin` |
| Архивация сообщений | ✅ работает (фон) | ✅ работает (фон) |

## Как выключить архивацию

Только одним из способов:

- `archive_admin("set_enabled", enabled=False)` (в режиме secretary)
- `hermes config set message_archive.enabled false`
- Убрать `chat_id` из `message_archive.chats` в `config.yaml`

## Как включить архивацию

- `archive_admin("set_enabled", enabled=True)`
- «включи архив» → агент в secretary вызовет `archive_admin`
- `hermes config set message_archive.enabled true`

## Проверка статуса

- «что с архивом» / «какие группы в архиве» → `archive_admin("status")`
- «какие чаты архивируются» → `archive_admin("list_chats")`
