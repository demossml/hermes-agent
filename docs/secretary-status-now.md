# Secretary Status Report — S0 Inventory

**Дата:** 2026-08-10
**Ветка:** multi-agent (8e1cbc9, HEAD)
**Репозиторий:** demossml/hermes-agent (~/.hermes/hermes-agent)

---

## 1. Git Log (последние 20)

```
8e1cbc983 feat: Telegram inline keyboards for mode, secretary, project switching
d7d76d0d7 feat(gateway): silence markers, archive_bridge, archive-only without reply in groups
660a39049 feat: Mode Router (dev/secretary) + Message Archive stack
8915a5cce docs: repo_path documentation in README
8d6013d63 feat: repo_path full support
fd798e3d7 fix: support external project paths
71a36ef55 fix: File Isolation v2 — terminal path scanning
7e697b774 fix: syntax error in path_guard.py
0752c71f2 fix: allow .env writes inside project root
5254a3227 feat: VS Code Settings UI
6f996f569 docs: final README update — Context Manager v4
26cc54a32 fix: Context Manager v4 — flock, session fallback, cache invalidation
362c43827 docs: update README — Context Manager + /project exit
36527f90f fix: Context Manager v3 — access control, insights isolation
16515d825 fix: Context Manager security hardening
3bac868c7 fix: /neutral and /project exit work instantly without restart
a6e84d40a feat: Context Manager — save/restore context across project switches
91a65a2b1 feat: /project exit — alias for leaving project mode
65427e082 docs: document /neutral and /mode commands in README
73ae82d61 feat: /neutral and /mode commands — exit project mode
```

---

## 2. Component Inventory

### 2.1 modes/ — Mode Router

| File | Назначение | Статус |
|------|-----------|--------|
| `modes/__init__.py` | Public API re-export | ✅ OK |
| `modes/state.py` | JSON-хранилище (modes.json), thread-local turn mode | ✅ OK |
| `modes/detect.py` | NL-триггеры (RU/EN), slash-команды, анти-паттерны | ✅ OK |
| `modes/router.py` | apply_mode_change(), format_status_reply(), format_already_reply() | ✅ OK |
| `modes/policy.py` | MODE_TOOLSETS, filter_tools(), filter_individual_tools(), блокировка terminal в secretary | ✅ OK |
| `modes/prompts.py` | DEV_GUIDANCE, SECRETARY_GUIDANCE, REPLY_* шаблоны | ✅ OK |

**Работает из Telegram:** Да — `/mode`, `/modes`, NL-фразы, inline keyboard `mode:dev`/`mode:secretary`.

### 2.2 gateway/secretary_router.py — Routing профилей

| API | Описание | Статус |
|-----|---------|--------|
| `list_secretaries()` | Сканирует `~/.hermes/profiles/secretary-*` или SECRETARY_PROFILES allowlist | ✅ OK |
| `get_active(user_id)` | Возвращает активный профиль для Telegram user | ✅ OK |
| `set_active(user_id, name)` | Записывает routing pointer в `secretary_router.json` | ✅ OK |
| `get_profile_path(name)` | Возвращает Path к HERMES_HOME профиля | ✅ OK |
| `validate_profile(name)` | Проверяет существование профиля на диске | ✅ OK |

**Работает из Telegram:** Да — `/secretaries`, inline keyboard `sec:<name>`.

**Критическая дыра:** Секретарь меняет JSON-указатель, но **не переключает HERMES_HOME** на turn агента.
См. `gateway/run.py:7418`: "Model: lightweight — sets context, does NOT switch HERMES_HOME."
Только выставляет `os.environ["HERMES_ACTIVE_SECRETARY"]` и `event._active_secretary`.
Агент продолжает работать с default HERMES_HOME. Memory/sessions/cron разных secretary-* профилей НЕ изолированы.

### 2.3 Inline Keyboards в telegram.py

| Callback | Handler | Статус |
|----------|---------|--------|
| `mode:dev` / `mode:secretary` | `_handle_mode_callback()` | ✅ Работает |
| `sec:<name>` | `_handle_sec_callback()` | ✅ Работает |
| `proj:<id>` / `proj:__neutral__` | `_handle_proj_callback()` | ✅ Работает |

**Picker senders:**
- `_handle_mode_picker_locally()` — `/mode`, `/modes`, NL триггеры
- `_handle_secretary_picker_locally()` — `/secretaries`
- `_handle_project_picker_locally()` — `/projects`, `/project list`, NL триггеры

### 2.4 /who — Текущий статус

**Файл:** `gateway/platforms/telegram.py:6437-6477`

Выводит ТРИ строки:
```
Секретарь: <profile_name>
Проект: <project_name>
Режим: 🛠 разработка | 📋 секретарь
```

**Чего НЕТ:**
- Имя пользователя
- Timezone
- Статус дайджеста (on/off, время)
- Шаг онбординга
- Кнопки для действий

### 2.5 Project Manager

**Файл:** `projects/project_context.py`

| API | Статус |
|-----|--------|
| `list_projects()` → list[dict] | ✅ |
| `switch_project(id)` → dict | ✅ |
| `get_current_project_name()` → str | ✅ |
| `get_current_project_id()` → str | ✅ |
| `create_project()` | ✅ |

### 2.6 Message Archive + Silence

| Файл | Статус |
|------|--------|
| `gateway/archive_bridge.py` | ✅ silence markers, archive-only без ответа |
| `plugins/message_archive/` | ✅ DB, extractors |
| `hooks/message-archiver/` | ✅ hook handler |

**Работает из Telegram:** Да — `should_skip_agent_for_archive_only()` проверяет is_group и silence.

### 2.7 Himalaya Skill (Email)

**Файл:** `~/.hermes/skills/email/himalaya/SKILL.md` (304 строки)

Содержит: IMAP/SMTP настройка, конфигурация, message composition (MML).
**НЕТ интеграции с Telegram UI** — это только CLI skill, не подключена к кнопкам меню.

### 2.8 Secretary Reports Skill

**Файл:** `optional-skills/productivity/secretary-reports/SKILL.md`

Сценарии: отчёты за день/неделю, поиск по ключевым словам, cron jobs.
Использует `archive_query`, не почту.

---

## 3. Что УЖЕ закрывает Фазу 0 плана

| Пункт плана | Готовность |
|-------------|-----------|
| `/mode` + NL-триггеры | ✅ Полностью |
| `/projects` + `proj:` переключение | ✅ Полностью |
| Секретари `sec:` + `/secretaries` | ✅ Routing pointer работает, изоляция HERMES_HOME — НЕТ |
| `/who` | ⚠️ Минимальный (3 строки) |
| QUICKSTART-MODES.md | ✅ Есть, 104 строки |
| Archive silence в группах | ✅ Работает |
| dev/secretary tool policy | ✅ terminal заблокирован в secretary |

**Вердикт по Фазе 0:** Костяк навигации готов. Можно переходить к Фазе 1.

---

## 4. Блокеры и дыры

### 4.1 Блокеры S1–S11

| Компонент | Файл | Работает из Telegram | Дыра |
|-----------|------|---------------------|------|
| `secretary_users` | **НЕТ** | — | Нет хранилища prefs пользователя (S1) |
| `/start` onboarding | **НЕТ** | — | Нет туннеля онбординга (S2) |
| `/меню` | **НЕТ** | — | Нет главного меню (S3) |
| `/who` расширенный | `telegram.py:6437` | ⚠️ Минимальный | Нет имени/TZ/дайджеста/onboarding (S4) |
| Profile routing HERMES_HOME | `run.py:7418` | ❌ Не переключает | Только env vars, не изоляция (S5) |
| Mail read | **НЕТ** | — | Himalaya skill есть, но не в TG (S6) |
| Digest cron | **НЕТ** | — | Cron инфраструктура есть, сценария нет (S7) |
| Draft + confirm | **НЕТ** | — | Нет (S8) |
| Follow-up | **НЕТ** | — | Нет (S9) |
| Calendar read | **НЕТ** | — | Нет (S10) |

### 4.2 path_guard

**Файл:** `projects/path_guard.py`
**Режим:** fail-closed — все записи вне project root блокируются.
**Влияние на S1–S11:** Если `write_file` в `~/.hermes/` файлы (не под проектом) блокируется guard-ом, код нужно будет копировать вручную. Согласно плану: "Если write блокируется — выдать полный код/diff в чат."

---

## 5. Общий вердикт

**Готовность к Фазе 1 (Telegram UX):** 30% — каркас есть, продукта нет.
- Режимы переключаются, проекты переключаются, секретари листаются.
- Но нет пользователя как сущности: ни имени, ни TZ, ни настроек.
- Нет главного меню, нет онбординга.
- `/who` — три строки без кнопок.

**Следующий шаг:** S1 — secretary_user_store (модель пользователя).
