# Secretary Profile Isolation (S5 + S13)

Per-turn HERMES_HOME isolation via contextvars.

## Статус: PASS ✓ (S13 verified)

### S13 проверка

- 3 точки создания AIAgent — все под `_handle_message_with_agent` override
- Модульный кэш `_hermes_home` (run.py:1075) — только для .env загрузки, не для agent
- 5 integration тестов: override flow, два профиля не утекают, reset после токена

## Как работает

sec:secretary-acme → set_active(uid, "secretary-acme")
Следующий message turn:
  → get_active(uid) → "secretary-acme"
  → _handle_message_with_agent: set_hermes_home_override(profiles/secretary-acme)
  → AIAgent() использует get_hermes_home() → профиль
  → задача завершается → контекст очищается

## Telegram чеклист для человека

| # | Действие | Ожидание |
|---|---------|---------|
| 1 | `sec:secretary-acme` | ✅ переключился |
| 2 | `/who` | Профиль: secretary-acme |
| 3 | «запомни: токен-A» (любой факт) | Запомнил |
| 4 | `sec:default` → спросить «токен-A» | ❌ не знает (другой профиль) |
| 5 | `sec:secretary-acme` → спросить «токен-A» | ✅ знает (вернулся) |
| 6 | `/secretary_health` | Профиль: secretary-acme |

## Что изолировано

- Memory, sessions, skills, project state — в profiles/<name>/
- НЕ изолировано: secretary_users.db, secretary_router.json (control home), OS filesystem

## Concurrency

ContextVar per-asyncio-task. Два пользователя не мешают.

## Тесты

72/72 PASS (5 integration + 11 unit isolation + 56 secretary).
