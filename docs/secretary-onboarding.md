# Secretary Onboarding (/start)

Онбординг-туннель для Telegram-пользователя. Реализован в `gateway/platforms/telegram.py`.

## Точки входа

| Что | Где | Когда |
|-----|-----|-------|
| `/start` | `_handle_command()` | До LLM, после `/rule`, перед `/mode` |
| `onb:*` callback | `_handle_callback_query()` | После `proj:`, перед `cl:` |
| Текст (name, tz_custom) | `_handle_message()` | После `/who` триггера, перед agent |

## Шаги онбординга

```
welcome → name → tz → email_skip → digest → done
                  ↘ tz_custom (text) ↗
```

### welcome
- Текст: приветствие AI-секретаря
- Кнопки: [Давай] `onb:go` | [Позже] `onb:later`
- `later` → "Ок, напишите /start когда будете готовы", step не меняется

### name
- Предлагает Telegram `first_name` кнопкой [Оставить {name}] `onb:name_ok`
- Либо пользователь пишет имя текстом → сохраняется, step=tz

### tz
- Кнопки: [Москва] `onb:tz:Europe/Moscow` | [Киев] `onb:tz:Europe/Kyiv` | [Другой] `onb:tz:custom`
- `custom` → step=tz_custom, ждёт текст IANA

### tz_custom (текстовый ввод)
- Проверяет формат: содержит `/`, нет пробелов
- При ошибке — просит повторить (потребляет сообщение, не уходит в LLM)
- При успехе → step=email_skip

### email_skip
- "Почту подключим позже"
- Кнопка: [Продолжить] `onb:email_skip`

### digest
- [Да, в 09:00] `onb:digest:9` → digest_enabled=1, digest_hour=9
- [Выключить] `onb:digest:off` → digest_enabled=0
- Оба → step=done, финальное сообщение

### done
- `is_onboarded()` возвращает True
- Повторный `/start` показывает "Вы уже настроены"

## Callbacks

| Callback | Действие |
|----------|---------|
| `onb:go` | step → name |
| `onb:later` | Выход без смены step |
| `onb:name_ok` | step → tz |
| `onb:tz:Europe/Moscow` | tz=Europe/Moscow, step → email_skip |
| `onb:tz:Europe/Kyiv` | tz=Europe/Kyiv, step → email_skip |
| `onb:tz:custom` | step → tz_custom |
| `onb:email_skip` | step → digest |
| `onb:digest:9` | digest on, step → done |
| `onb:digest:off` | digest off, step → done |
| `onb:who` | Вызов `_handle_who_locally` |

## ACL

Callback `onb:*` проверяет авторизацию через `_is_callback_user_authorized` — тот же механизм, что у mode/sec/proj.

## Данные

Все prefs пользователя хранятся в `secretary_users.db` через `secretary_user_store` из S1.

## Файлы

- `gateway/platforms/telegram.py` — строки 6093–6095 (диспетчеризация /start), 4098–4102 (callback onb:*), 6078–6081 (текстовый перехват), 7493–7863 (методы онбординга)
- `gateway/secretary_user_store.py` — хранилище (S1)

## Чеклист

- [x] Новый uid: `/start` → все шаги → `is_onboarded` True
- [x] Повторный `/start` → не гоняет туннель заново
- [x] `onb:later` работает
- [x] Данные в `~/.hermes/secretary_users.db`
- [x] ACL как у mode
- [x] Синтаксис AST valid
- [x] S1 тесты не сломаны (19/19)
