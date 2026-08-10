# Secretary Calendar (S10)

Read-only calendar via ICS URL. Lights-out parser — no icalendar dependency.

## Env

```bash
SECRETARY_CAL_ICS_URL=***            # Google Calendar, iCloud, CalDAV read-only URL

## API

### `list_events(days_ahead=1) -> list[dict]`

Каждое событие: `{start: datetime, end: datetime, title, location, all_day}`

### `format_calendar_list(events) -> str`

Формат для Telegram: дата, время, название, локация.

## Telegram

- `menu:cal` → подменю: [Сегодня] [Завтра] [7 дней] [Меню]
- `cal:day:0` — сегодня, `cal:day:1` — завтра, `cal:week` — неделя
- Без URL → инструкция по настройке
- Только чтение (запись — отдельный промпт)

## Тесты

6 unit-тестов: парсер ICS, форматтер, конфиг. Все 67/67 PASS.
