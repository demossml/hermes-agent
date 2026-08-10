# Secretary Main Menu (/menu)

Главное меню секретаря — InlineKeyboard навигация. Реализовано в `gateway/platforms/telegram.py`.

## Триггеры

| Триггер | Тип | Где |
|---------|------|-----|
| `/menu`, `/меню` | Slash-команда | `_handle_command` (после `/start`, перед `/mode`) |
| `меню`, `главное меню`, `menu`, `main menu`, `в меню` | NL | `_handle_message` (после onboarding text, перед agent) |
| `/start` (уже onboarded) | Команда | `_handle_start_command` → `send_main_menu` |
| Кнопка `[Меню]` после онбординга | Callback | `menu:home` в `_render_onboarding_done` |

## Структура меню

```
📋 Меню секретаря — Имя

[📧 Почта] [✅ Задачи]
[📅 Календарь] [🔀 Режим]
[📁 Проект] [👤 Секретарь]
[⚙️ Настройки] [ℹ️ Кто я]
```

## Callbacks

| Callback | Действие |
|----------|---------|
| `menu:home` | Вернуться в меню |
| `menu:mail` | Заглушка «Скоро (фаза 2)» + [⬅️ Меню] |
| `menu:tasks` | Заглушка «Скоро (фаза 2–3)» + [⬅️ Меню] |
| `menu:cal` | Заглушка «Скоро (фаза 2–3)» + [⬅️ Меню] |
| `menu:mode` | Делегирует в `_handle_mode_picker_locally` |
| `menu:project` | Делегирует в `_handle_project_picker_locally` |
| `menu:secretary` | Делегирует в `_handle_secretary_picker_locally` |
| `menu:who` | Делегирует в `_handle_who_locally` |
| `menu:settings` | Открывает подменю настроек |

## Подменю настроек (set:*)

| Callback | Действие |
|----------|---------|
| `set:tz:Europe/Moscow` | tz = Europe/Moscow |
| `set:tz:Europe/Kyiv` | tz = Europe/Kyiv |
| `set:digest:1` | digest_enabled = True |
| `set:digest:0` | digest_enabled = False |
| `set:hour:8` | digest_hour = 8 |
| `set:hour:9` | digest_hour = 9 |
| `set:hour:10` | digest_hour = 10 |

Все `set:*` пишут через `upsert_user` в `secretary_users.db` и перерисовывают настройки.

## Методы

| Метод | Строки | Назначение |
|-------|--------|-----------|
| `_handle_menu_command` | ~7902 | Точка входа для `/menu` и NL |
| `send_main_menu` | ~7912 | Отправляет клавиатуру меню (public API) |
| `_handle_menu_callback` | ~7977 | Диспетчер menu:* и set:* |
| `_render_settings` | ~8061 | Подменю настроек |
| `_handle_settings_callback` | ~8118 | Обработчик set:* |
| `_is_menu_trigger` | ~437 | NL-детектор (модульная функция) |

## Связь с S2

- `_handle_start_command` для onboarded → `send_main_menu` (не заглушка)
- `_render_onboarding_done` → кнопки [Меню] + [Кто я]
- `onb:who` сохранён как fallback

## Чеклист

- [x] `/меню` и `/menu` → клавиатура
- [x] NL «меню», «главное меню» → клавиатура
- [x] Режим / Проект / Секретарь → существующие picker'ы
- [x] Настройки меняют secretary_users.db
- [x] Назад в меню (menu:home) работает
- [x] `/start` для onboarded → меню
- [x] S1-тесты не сломаны (19/19 PASS)
- [x] AST syntax valid
