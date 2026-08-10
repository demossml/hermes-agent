# Secretary Profile Isolation (S5)

Per-turn HERMES_HOME isolation via contextvars.

## Что изменилось

**Было (S0):** run.py:7418 выставлял os.environ но НЕ переключал get_hermes_home(). Memory/sessions профилей смешивались.

**Стало (S5):** set_hermes_home_override() в _handle_message_with_agent перед agent turn. ContextVar — видна только в текущей asyncio-задаче, авто-очистка.

## Как работает

sec:secretary-acme → set_active(uid, "secretary-acme")
Следующий message turn:
  → get_active(uid) → "secretary-acme"
  → _handle_message_with_agent: set_hermes_home_override(profiles/secretary-acme)
  → AIAgent() использует get_hermes_home() → профиль
  → задача завершается → контекст очищается

## Что изолировано

- Memory, sessions, skills, project state — в profiles/<name>/
- Что НЕ изолировано: secretary_users.db, secretary_router.json (control home намеренно), OS filesystem

## Concurrency

Разные asyncio-задачи = разные значения ContextVar. Без глобального side effect.

## Тесты

61/61 PASS (11 isolation + 50 secretary).
