# Hermes Secretary — Telegram MVP

**Дата:** 2026-08-10
**Ветка:** `feat/secretary-mvp-s1-s9`
**База:** `multi-agent` (8e1cbc9)
**Статус:** 88 тестов, READY FOR MERGE

## Что это

Telegram-секретарь на базе Hermes Agent. Онбординг, меню, почта, дайджест, календарь, задачи, изоляция профилей — всё управляется кнопками из Telegram, без терминала.

## Быстрый старт

```bash
cd ~/.hermes/hermes-agent
git checkout feat/secretary-mvp-s1-s9
hermes gateway restart
```

В Telegram: `/start` → онбординг за 5 шагов → `/меню`

## Возможности

### Почта
- `/меню` → 📧 Почта → список за 12ч/48ч
- Классификация: 🔴 важное / 📰 рассылки / ⚪ прочее
- Ответ: LLM-черновик → [Отправить] [Править] [Отклонить]
- DRY_RUN=1 по умолчанию — письма не уходят наружу
- Trust policy: подтверждение для новых адресатов
- Follow-up: письма без ответа >N дней

### Дайджест
- `/digest` — утренняя сводка: почта + календарь + follow-up
- Cron-ready: `run_daily_digest(bot)` + digest_hour

### Календарь
- ICS URL — сегодня/завтра/7 дней
- 5-минутный in-memory кэш

### Задачи
- `/меню` → ✅ Задачи → добавить / отметить done

### Профили и режимы
- `sec:secretary-acme` — изоляция памяти/sessions через ContextVar
- `/mode dev|secretary` — tool filtering
- `/who` — 8 полей статуса

### Команды
- `/start` — онбординг
- `/меню` — главное меню
- `/who` — статус
- `/digest` — дайджест
- `/secretary_health` — проверка
- `/help` — подсказка

## Настройка .env

```bash
# Почта (IMAP)
SECRETARY_MAIL_BACKEND=***S...=***     # imap.gmail.com
SECRETARY_MAIL_EMAIL=***D=***   # пароль приложения

# Календарь (ICS)
SECRETARY_CAL_ICS_URL=***# LLM-draft (опционально)
SECRETARY_MAIL_DRAFT_MODE=***# template = без LLM
SECRETARY_LLM_API_KEY=***SECRETARY_MAIL_DRY_RUN=***  # 0 = реальная отправка
```

## Архитектура

```
gateway/
├── secretary_user_store.py    SQLite: users, prefs, digest state
├── secretary_router.py        profile routing (sec:*)
├── secretary_digest.py        digest builder
├── platforms/telegram.py      9224 строк — onboarding, menu, mail, calendar, tasks
├── run.py                     ContextVar HERMES_HOME override (S5)

tools/secretary/
├── mail_inbox.py              IMAP/SMTP, list_recent, classify, follow-up
├── mail_draft.py              LLM-draft + template fallback + trust policy
├── calendar.py                ICS parser + cache
└── tasks.py                   SQLite todo list

tests/secretary/               10 файлов, 88 тестов
docs/secretary-*.md            10 документов
```

## Коммиты (12)

```
25c690c7c S27: E2E v2 report
5e38061ca S22: tasks
699228c93 S25+S19: /help + tour + mail status
e70717dd8 S23: digest 2.0
1402fd012 S16: classification
fc41bfb91 S17: trust policy
1529d4a7f S15: LLM-draft + edit
3c693eb50 S14: silence regression
9fa8a9092 S13: profile isolation
40cb1bc0d S12: live hardening
31a6c765a S5+S10: isolation + calendar
36d5ed105 S1–S9: MVP core
```

## Тесты

```bash
pytest tests/secretary -q   # 88 passed
```

## Документация

| Файл | Тема |
|------|------|
| `docs/secretary-live-setup.md` | Пошаговая настройка .env |
| `docs/secretary-user-store.md` | SQLite-схема пользователя |
| `docs/secretary-onboarding.md` | Онбординг flow |
| `docs/secretary-menu.md` | Структура меню |
| `docs/secretary-mail.md` | Почта: чтение |
| `docs/secretary-mail-send.md` | Отправка + trust + follow-up |
| `docs/secretary-digest.md` | Дайджест |
| `docs/secretary-calendar.md` | Календарь |
| `docs/secretary-profile-isolation.md` | Изоляция профилей |
| `docs/secretary-silence-regression.md` | Archive silence |
| `docs/secretary-status-now.md` | S0 инвентаризация |
| `docs/SECRETARY_E2E_REPORT.md` | E2E v1 |
| `docs/SECRETARY_E2E_REPORT_v2.md` | E2E v2 финальный |
