# QUICKSTART-MODES

Mode Router для Hermes Agent — два режима работы: `dev` и `secretary`.

## Установка

Mode Router уже встроен в `hermes-agent-multi-agent`. Ничего устанавливать не нужно.
Пакет `modes/` находится в корне проекта, интеграция в `gateway/run.py` применена.

Проверка:
```
python3 -c "from modes import get_default_mode; print(get_default_mode())"
# → dev
```

## Быстрый старт

Из любого канала (Telegram, CLI, Discord):

```
режим секретаря          → переключение в secretary
режим разработки         → переключение в dev
/mode dev                → slash-команда
/mode secretary          → slash-команда
какой режим              → статус
```

## Режимы

### dev — разработка
- Инструменты: terminal, file, delegation, coder/tester
- Фокус: текущий проект, код, тесты
- archive_query недоступен (по умолчанию)
- Если проект не выбран — предложит /project new

### secretary — секретарь
- Инструменты: archive_query, archive_admin, cronjob
- Фокус: архив сообщений, отчёты, группы
- terminal недоступен (по умолчанию)
- Видит все чаты глобально (без фильтра по проекту)
- archive_admin для настройки архива

## Конфигурация

`~/.hermes/config.yaml`:
```yaml
modes:
  default: dev                # режим по умолчанию
  dev:
    allow_archive_query: false  # variant A (строже)
  secretary:
    allow_terminal: false       # по умолчанию terminal заблокирован
```

## Идемпотентность

- `modes.json` в `~/.hermes/state/` — персистентное хранилище per-chat
- Повторная установка не ломает существующие записи
- `default: dev` — существующие пользователи не замечают изменений
- Неизвестный chat → `dev` mode

## Архивация vs Mode

Архивация (`message_archive.enabled`) работает ВСЕГДА фоном, независимо от режима.
Mode влияет только на ответы агента и доступные инструменты.
Подробнее: `docs/modes-vs-archiving.md`

## Сценарии для secretary

Загрузи skill: `/skill secretary-reports`

| Фраза | Действие |
|---|---|
| «отчёт за вчера» | archive_query с since/until UTC |
| «найди всё про X в группе Y» | keyword FTS + chat_id |
| «сводка по документам за месяц» | msg_type document_* |
| «добавь эту группу в архив» | archive_admin add_chat |
| «какие группы в архиве» | archive_admin list_chats |
| «включи архив» | archive_admin set_enabled |
| «настрой ежедневный отчёт» | hermes cron create (выводит команду) |

## Тесты

```
python3 -c "
from modes.detect import detect_mode_intent
assert detect_mode_intent('режим секретаря').mode == 'secretary'
assert detect_mode_intent('/mode dev').mode == 'dev'
assert detect_mode_intent('какой режим').mode == 'status'
assert detect_mode_intent('привет') is None
print('detect: OK')
"
```
