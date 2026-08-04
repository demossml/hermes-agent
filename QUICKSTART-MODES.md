# QUICKSTART MODES

Два режима работы Hermes Agent: **dev** (разработка) и **secretary** (секретарь).

## Таблица: dev vs secretary

| | dev | secretary |
|---|---|---|
| **Назначение** | Код, проект, файлы | Чаты, архив, отчёты |
| **terminal** | да | нет |
| **file tools** | да | нет |
| **delegation (coder/tester)** | да | нет |
| **archive_query** | нет | да |
| **archive_admin** | нет | да |
| **cronjob** | да | да |
| **memory, todo** | да | да |
| **Архивация (фон)** | да, всегда | да, всегда |
| **path_guard** | project root | `~/.hermes/archive/` |

## Как переключить из Telegram

Фразы (RU):
```
режим разработки        → dev
режим секретаря         → secretary
/mode dev               → dev (slash)
/mode secretary          → secretary (slash)
какой режим             → статус
```

Фразы (EN):
```
switch to dev mode
switch to secretary mode
current mode
```

## Как настроить группы в secretary

1. Переключись: `режим секретаря`
2. Проверь статус: `какие группы в архиве`
3. Добавь чат: `добавь эту группу в архив`
4. Включи архив: `включи архив`
5. Настрой лимит: `поставь лимит вложений 50 МБ`

Или через команды:
```
/mode secretary
archive_admin list_chats
archive_admin add_chat -1001234567890
archive_admin set_enabled true
archive_admin set_max_file_mb 50
```

## Как работать с проектом в dev

1. Переключись: `режим разработки`
2. Выбери проект: `/project list`, затем `/project switch <name>`
3. Работай: код, тесты, сабагенты coder/tester
4. Коммиты: `feat:`, `fix:`, `refactor:`, `docs:`

Без активного проекта агент предложит `/project new` или `/project switch`.

## Что архивируется всегда

Архивация — **фоновый процесс**, не зависит от режима. Работает при:
- `message_archive.enabled = true` в `config.yaml`
- `chat_id` в allowlist `message_archive.chats`

Переключение в `dev` **не выключает** архивацию. Чтобы выключить:
```
archive_admin set_enabled false
```

## Troubleshooting

### Режим не переключается

1. Проверь `~/.hermes/state/modes.json` — там запись для твоего chat_id
2. Ручная установка: `/mode dev` или `/mode secretary`
3. Если файл повреждён — удали `modes.json`, он пересоздастся
4. Проверь логи: `grep "Mode set" ~/.hermes/logs/gateway.log | tail -5`

### archive_query нет в secretary

1. Проверь, что `hermes-archive-bot` plugin установлен и enabled
2. Убедись, что `archive_query` зарегистрирован в tool registry
3. Проверь `filter_individual_tools("secretary", ...)` — `archive_query` не должен быть в blocked
4. Проверь `config.yaml`: `modes.secretary.allow_terminal` не влияет на `archive_query`

### terminal всё ещё доступен в secretary

По умолчанию `terminal` заблокирован на двух уровнях:
1. Toolset: `MODE_TOOLSETS["secretary"]` не содержит `"terminal"`
2. Individual: `_BLOCKED_SECRETARY_DEFAULT = {"terminal"}`

Если terminal нужен — `config.yaml`: `modes.secretary.allow_terminal: true`

### project не виден в dev

1. `/project list` — посмотри список
2. `/project switch <name>` — выбери
3. `/project current` — проверь активный
4. Убедись, что `.hermes-project` или `.project.lock` существует в корне проекта
