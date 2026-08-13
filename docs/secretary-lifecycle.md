# Secretary profile lifecycle (Telegram)

Полный lifecycle secretary-профилей прямо из Telegram-пикера — без терминала.

`create → switch → work → delete`, плюс защита от удаления активного/`default`.

## Callback-схема (`sec:*`)

| Callback | Действие |
|----------|----------|
| `sec:<profile>` | switch активного секретаря (существующее) |
| `sec:create` | старт создания — запрос slug |
| `sec:delete` | пикер удаления |
| `sec:del:<profile>` | запрос confirm удаления |
| `sec:confirm_del:<profile>` | выполнить delete |
| `sec:cancel` | сброс transient state → назад в пикер |

`callback_data` ≤ 64 байт — profile id короткий (`secretary-<slug>`, slug ≤ 64).

## Пикер (секция B)

Если есть `secretary-*` профили:
- ряд кнопок профилей (✓ на active)
- нижний ряд: `[➕ Создать]` `sec:create` | `[🗑 Удалить]` `sec:delete`

Если нет ни одного `secretary-*`:
- одна кнопка `[➕ Создать первого секретаря]` `sec:create`
- текст «Нет профилей secretary-*. Создайте первого.»

Реализация: `_secretary_picker_view(active)` / `_secretary_delete_view(active)`
в `gateway/platforms/telegram.py`.

## Создание (L1)

1. `sec:create` → только allowlisted (ACL), только private chat
   (в группе — alert «создавайте в ЛС с ботом»).
2. В `prefs_json` ставится `awaiting_secretary_name = true`
   (`gateway/secretary_user_store.py`, `set_pref`).
3. Сообщение: «Введите slug латиницей…» + `[↩ Отмена]` `sec:cancel`.
4. Текстовый перехват ДО LLM — `_handle_secretary_name_text` (рядом с
   onboarding text intercept):
   - не команда `/`;
   - slug = `text.strip().lower()`, снять префикс `secretary-` если ввели;
   - валидация `[a-z0-9][a-z0-9_-]{0,63}` (`_normalize_secretary_slug`);
   - `full = secretary-<slug>`;
   - `validate_profile(full)` уже есть → ошибка, state НЕ сбрасывается;
   - иначе `create_profile(full, no_skills=True)` + `set_active(caller, full)`;
   - сброс `awaiting_secretary_name`;
   - `✅ Создан full\nАктивен: full` + обновлённый пикер.

## Удаление (L2–L3)

1. `sec:delete` → список `secretary-*` **без** `default`; для active — пометка
   `(активен)`; `[↩ Отмена]`.
2. `sec:del:<name>`:
   - `name == active` → alert «нельзя удалить активного, сначала переключитесь»;
   - `name == default` или `not validate_profile` → отказ;
   - иначе confirm «Memory, sessions, skills, config будут удалены. Необратимо.»
     + `[✅ Да, удалить]` `sec:confirm_del:<name>` / `[❌ Отмена]`.
3. `sec:confirm_del:<name>`: повторные проверки, `delete_profile(name, yes=True)`,
   очистка stale active-указателей (`unset_active_for_profile`), `🗑 name удалён`,
   обновлённый пикер.

`FileNotFoundError` → «уже удалён», пикер.

## Безопасность

- ACL на все `sec:*` create/delete/confirm.
- path traversal только через `create_profile`/`delete_profile`
  (пути вручную не собираются, произвольный path из callback не принимается).
- не удалять active, не удалять `default`.
- audit: `logger.info` с `user_id`, `action`, `profile`.

## Что в profile vs control home

| Данные | Где |
|--------|-----|
| `config.yaml`, `.env`, `SOUL.md`, skills, memories, sessions, cron | `~/.hermes/profiles/<name>/` (сам profile) |
| `secretary_router.json` (active-указатель на Telegram-юзера) | control home (`~/.hermes/`) |
| `secretary_users.db` (`prefs_json`, onboarding, digest) | control home (`~/.hermes/`) |

То есть: удаление profile (`delete_profile`) стирает только каталог профиля.
Роутер-указатель и `prefs_json` живут в control home и чистятся отдельно
(`unset_active_for_profile`, `set_pref(..., None)`).

## Тесты

`tests/test_secretary_lifecycle.py` — slug-валидация, prefs-хелперы,
`unset_active_for_profile`, построение пикера, routing create/delete/del/
confirm_del/cancel, блок удаления active, перехват slug (success/duplicate/
bad slug), cancel сбрасывает флаг, group-guard для create.

## Дерево умений (L2)

Экран «Что умеет» для активного секретаря: `/меню → [📋 Что умеет] menu:skills`.

### Реестр

`gateway/secretary_skills_registry.py` — фиксированный каталог:

| id | title | emoji | default_enabled | requires_setup |
|----|-------|-------|-----------------|----------------|
| mail | Почта | 📧 | ✅ | ✅ |
| calendar | Календарь | 📅 | — | ✅ |
| groups | Группы | 📁 | ✅ | — |
| tasks | Задачи | 📝 | ✅ | — |
| vision | Зрение | 👁 | — | ✅ |
| tgcli | Telegram CLI | ✈️ | — | ✅ |

Поля `SecretarySkill`: `id`, `title`, `emoji`, `default_enabled`, `requires_setup`.

### Статусы

`off | needs_setup | ready | error` (константы в registry).

`apply_toggle(id, on)`:
- `on` + `requires_setup` → `needs_setup`
- `on` без `requires_setup` → `ready`
- `off` → `off`

### Storage

`gateway/secretary_skills_store.py` — JSON `secretary_skills.json` **в активном
profile HERMES_HOME** (не в control db):

- `load_state(profile_home)` — слияние с defaults (новые умения появляются,
  неизвестные id отбрасываются, битый файл → defaults).
- `set_skill(profile_home, id, enabled, status)` — атомарная запись.
- Путь резолвится через `secretary_router.get_active_profile_path(uid)`.

### UI / callbacks

- `menu:skills` → `_render_skills` (отправляет экран).
- `skill:on:<id>` / `skill:off:<id>` — toggle + `query.answer` + перерисовка.
- `skill:setup:<id>` — заглушка «мастер появится в L3» (не меняет state).
- Строка умения: `[<глиф> <emoji> <title> (<status>)] [Вкл/Выкл/Настроить]`.
- Глифы: ⬜ выкл · ✅ готов · 🟡 настроить · ⚠️ ошибка.

### Persistence / изоляция

Стейт лежит в каталоге профиля — переключение секретаря (`sec:<profile>`)
даёт другой набор флагов; переживает рестарт гейтвея.

Тесты: `tests/test_secretary_skills.py` (registry, store, экран, callbacks).

## Мастер настройки (L3)

При `skill:on:<id>` (или `skill:setup:<id>`) для `requires_setup`-умения открывается
пошаговый мастер, пишущий значения в `.env` активного профиля.

### Манифесты

`gateway/secretary_skills_registry.py` — `SecretaryField` + `MANIFESTS`:

| skill | поля |
|-------|------|
| mail | `SECRETARY_MAIL_EMAIL` (text), `SECRETARY_MAIL_PASSWORD` (password/secret), `SECRETARY_MAIL_IMAP_HOST` (choice gmail/mailru/yandex/other → hostname) |
| calendar | `SECRETARY_CAL_ICS_URL` (url) |
| tgcli | `TG_API_ID` (text), `TG_API_HASH` (password/secret) |
| vision / groups / tasks | нет полей (валидация в L4) |

`SecretaryField`: `key`, `label`, `type` (text/password/choice/url), `secret`,
`help`, `optional`, `choices`, `choice_map` (label → env value; «other» → свободный ввод).

### Поток

- `skill:on` + `requires_setup` → `needs_setup` + старт мастера.
- prefs `awaiting_skill_setup = {skill_id, field_index, draft, await_text}`.
- Шаг выбора (`choice`) → inline-кнопки; «other» → свободный ввод хоста.
- Секрет уже в `.env` → «уже задано» [Оставить] / [Заменить].
- Текстовый перехват `_handle_skill_setup_text` (до LLM): значение → draft,
  пароль в лог не пишется (`value hidden`).
- Финал → `write_env_value` в `~/.hermes/profiles/<name>/.env` (0600),
  clear awaiting, статус остаётся `needs_setup` до L4, кнопка [🔍 Проверить].
- `skill:cancel` → `enabled=false, status=off`, clear awaiting.

### Callbacks

`skill:on/off/setup/validate/cancel`, `skill:choice:<id>:<choice>`,
`skill:keep:<id>`, `skill:replace:<id>`.

### Storage env

`gateway/secretary_skills_store.py`: `write_env_value(profile_home, key, value)`
(атомарно, 0600, с квотингом спецсимволов), `read_env_value(profile_home, key)`.

Тесты: `tests/test_secretary_skills_setup.py` (манифесты, env write/read, шаги,
choice/keep/replace, cancel, text intercept, routing).

## Проверка умений (L4)

`gateway/secretary_skill_validate.py` — `validate_skill(skill_id, profile_home)`
→ `(status, message)`, статусы `ready | error | needs_setup`:

| skill | проверка |
|-------|----------|
| groups / tasks | всегда `ready` |
| mail | IMAP-логин (host/email/пароль из profile `.env`) |
| calendar | fetch ICS URL (urllib, 10с) |
| vision | `shutil.which("vision-cli")` |
| tgcli | `which tg`; без сессии → `needs_setup` «выполните tg auth» |

UI:
- `skill:validate:<id>` — из дерева умений ([Проверить]) и из мастера
  ([🔍 Проверить]).
- `_validate_skill_callback` гоняет проверку через `asyncio.to_thread`
  (не блокирует event loop) и пишет статус `set_skill(...)`.
- Результат: ✅ готово / 🟡 нужна настройка / ⚠️ ошибка (+ текст);
  после fail — [🔧 Повторить настройку] `skill:setup:<id>`.
- Дерево умений: у включённых умений добавлена кнопка [Проверить].
- `/secretary_health` — блок «Умения» с ready/needs_setup/off/error для
  активного профиля.
- `menu:mail` — список писем только при `mail == ready`, иначе [⚙️ Настроить].

Тесты: `tests/test_secretary_skill_validate.py` (dispatch, binary checks, IMAP
mock, result view, callback, кнопка [Проверить]).

## Группы (L5)

Экран `/меню → [👥 Группы] menu:groups`.

- `_groups_view` — если умение `groups` не `ready`, предлагает [⚙️ Включить].
- Список чатов = известные боту группы (`channel_directory`) ∪ архивный
  allowlist. Статус «следит / не следит» по `archive_admin list_chats`.
- `groups:watch:<chat_id>` → `archive_admin add_chat` (при all→allowlist —
  confirm `groups:watch_yes`). `groups:unwatch:<chat_id>` → `remove_chat`.
- `groups:how` — инструкция «добавь бота в группу → нажми Следить».
- `groups:report` → подменю [Сутки / 3 дня / Неделя] → `groups:report:<hours>`.
- `_archive_summary(hours)` — сводка из `message_archive` DB: всего сообщений,
  по типу, по чатам. Отправляется **в ЛС** (`int(caller_id)`), не в группу.

Silence не ломается: отчёт — только по явной кнопке и в личку.

Тесты: `tests/test_secretary_groups.py` (view, watch/unwatch, report,
resolve name, callback routing).

