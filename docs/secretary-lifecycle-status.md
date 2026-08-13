# Secretary Lifecycle — L0 инвентаризация

Дата: 2026-08-13 · Репозиторий: demossml/hermes-agent, ветка multi-agent
Статус: разведка, без изменений кода. Все пути/строки — на момент HEAD + L1 (уже смержен в `e60ee17ca`).

## Сводная таблица

| # | Компонент | Есть? | Файл / строка | Что нужно для L1–L6 |
|---|-----------|-------|---------------|----------------------|
| 1 | sec: picker + `_handle_sec_callback` | ✅ switch **+ create/delete** (L1 уже сделан) | `gateway/platforms/telegram.py` `_handle_secretary_picker_locally` (~6436), `_handle_sec_callback` (~6472) | L2–L6 строятся поверх |
| 2 | `create_profile` / `delete_profile` | ✅ | `hermes_cli/profiles.py`: `create_profile` (856), `delete_profile` (1152), `list_profiles` (788) | — |
| 3 | secretary_router (list/get/set/validate) | ✅ | `gateway/secretary_router.py`: `list_secretaries` (153), `get_active` (210), `set_active` (233), `validate_profile` (263), `unset_active_for_profile` (263), `get_profile_path` (306) | — |
| 4 | skills / tool policy для mode secretary | ⚠️ только prompt | `modes/prompts.py` `SECRETARY_GUIDANCE` (45); `tools/archive_admin.py` `check_archive_admin_requirements` (230) | **L2: нет per-skill registry** |
| 5 | profile config.yaml + .env | ✅ | `~/.hermes/profiles/<name>/{config.yaml,.env,SOUL.md,skills/,memories/,sessions/,cron/,home/}`; default = `~/.hermes` | L3 пишет в profile `.env` |
| 6 | menu:mail / settings «настроить почту» | ⚠️ только инструкция | `_render_mail_menu` (8665) показывает .env-подсказку, wizard нет | **L3: setup-мастер** |
| 7 | archive allowlist | ✅ | `tools/archive_admin.py` `archive_admin_tool` (85): `list_chats/add_chat/remove_chat/set_enabled/status`; `plugins/message_archive/__init__.py` `allowed_chats` (54) | L5 watch/unwatch |
| 8 | awaiting_* patterns в prefs | ✅ | `gateway/secretary_user_store.py` `get_pref/set_pref/clear_pref`; ключи: `awaiting_secretary_name` (L1), `pending_edit_mail_id`, `pending_task_add`; onboarding отдельно через `onboarding_step` (колонка, не prefs) | L3 `awaiting_skill_setup` по аналогии |

## 1. sec: picker + `_handle_sec_callback`

- `_handle_secretary_picker_locally(msg)` — отправляет пикер inline-клавиатурой (zero-LLM).
- `_handle_sec_callback(query, data, chat_id, thread_id, user_name)` — диспетчер `sec:*`:
  - `sec:<profile>` — switch (существующее)
  - `sec:create` / `sec:delete` / `sec:del:<name>` / `sec:confirm_del:<name>` / `sec:cancel` — **L1 уже реализован** (коммит `e60ee17ca`).
- ACL через `_is_callback_user_authorized` (669).
- Пикер: `_secretary_picker_view` (6437), delete-вид `_secretary_delete_view` (6467).

## 2. create_profile / delete_profile API

```
create_profile(name, clone_from=None, clone_all=False, clone_config=False,
               no_alias=False, no_skills=False, description=None) -> Path   # 856
delete_profile(name, yes=False) -> Path                                     # 1152
list_profiles() -> List[ProfileInfo]                                        # 788
seed_profile_skills(profile_dir, quiet=False)                               # 1055
```
- Автоконфиг secretary: `_seed_secretary_profile_config` (506) — SOUL.md + config.yaml для `secretary-*`.
- `NO_BUNDLED_SKILLS_MARKER = ".no-bundled-skills"` (133) — маркер для `--no-skills`.

## 3. secretary_router

- State file: `~/.hermes/secretary_router.json` (ключ — telegram_user_id, value `{active_profile, updated_at}`).
- `list_secretaries(include_tests=False)` — default + `secretary-*` с диска (или allowlist `SECRETARY_PROFILES`).
- `get_active(uid)` / `set_active(uid, name)` / `validate_profile(name)` / `unset_active_for_profile(name)`.
- `get_profile_path(name)` — `default` → `~/.hermes`, иначе `~/.hermes/profiles/<name>/`.

## 4. Skills / tool policy для mode secretary

- **Mode = prompt-инъекция**: `modes/prompts.py SECRETARY_GUIDANCE` (строки 45–109) вставляется в stable tier системного промпта.
- **Tool-gating**: только `archive_admin` обёрнут в `check_archive_admin_requirements()` (secretary mode only). Остальные инструменты не gating'уются по mode.
- **Skills** = `SKILL.md`-файлы (`skills/`, `optional-skills/`). `SECRETARY_GUIDANCE` ссылается на `/skill secretary-reports`.
- **Пробел для L2**: нет реестра умений с on/off + status на profile. Нет `gateway/secretary_skills_registry.py`.

## 5. Profile config.yaml + .env

- Named profile: `~/.hermes/profiles/<name>/` (полный HERMES_HOME).
- `config.yaml`, `.env` (0600), `SOUL.md`, `skills/`, `memories/`, `sessions/`, `cron/`, `home/`.
- default profile = `~/.hermes` (контрольный home).
- S5: per-turn `HERMES_HOME` override по active profile (`hermes_constants.set_hermes_home_override`).
- **Пробел для L2/L3**: нет per-profile storage для `skills.<id>.enabled/status` (нужен yaml/json в profile home, не в control db).

## 6. menu:mail / settings

- `menu:mail` → `_render_mail_menu` (8665): если `is_configured()` False — текст-инструкция «добавьте в .env SECRETARY_MAIL_*» + [Меню]. Wizard нет.
- `menu:settings` → `_render_settings` (8534) / `_handle_settings_callback` (8600): timezone, digest on/off, digest hour (`set:*`). Настройки почты нет.
- **Пробел L3**: wizard по манифесту, запись в profile `.env`.

Env vars (почта): `SECRETARY_MAIL_BACKEND`, `_IMAP_HOST`, `_IMAP_PORT`, `_EMAIL`, `_PASSWORD`, `_SMTP_HOST`, `_SMTP_PORT`, `_SMTP_PASSWORD`, `_DRY_RUN`.
Календарь: `SECRETARY_CAL_ICS_URL`.

## 7. Archive allowlist

- `tools/archive_admin.py::archive_admin_tool(action, chat_id, enabled, confirmed)`:
  - `list_chats`, `add_chat` (с confirm при переходе all→allowlist), `remove_chat`, `set_enabled`, `set_max_file_mb`, `status`.
- Конфиг: `message_archive.chats` (список) или `None` = архивировать все.
- `plugins/message_archive/__init__.py::allowed_chats()` (54), `chat_is_archived(chat_id)` (66).
- **L5**: watch/unwatch → `archive_admin_tool add_chat/remove_chat`.

## 8. awaiting_* patterns в prefs

- `gateway/secretary_user_store.py`: `prefs_json` (TEXT '{}') + хелперы `get_pref`/`set_pref`/`clear_pref`.
- Существующие transient-ключи:
  - `awaiting_secretary_name` (L1) — перехват slug создания.
  - `pending_edit_mail_id` — перехват правки письма (`_handle_pending_mail_edit`, 9327).
  - `pending_task_add` — перехват добавления задачи (`_handle_pending_task_add`, 9555).
- Onboarding — отдельный столбец `onboarding_step` (не prefs): `_handle_onboarding_text` (7959...).
- Текстовые перехваты в `_handle_message`: onboarding → secretary_name → pending_mail_edit → pending_task_add.
- **L3** `awaiting_skill_setup = {skill_id, field_index, draft:{}}` — по той же схеме.

## Выводы для следующих промптов

- **L1** — уже сделан (create/delete/switch из пикера). Дублировать не нужно.
- **L2** — нужен новый `gateway/secretary_skills_registry.py` (id/title/emoji/default_enabled/requires_setup) + storage в profile home (не control db).
- **L3** — манифесты setup + wizard; переиспользовать `set_pref/get_pref` + `awaiting_skill_setup`; писать в profile `.env` через S5 override.
- **L4** — валидация: mail `is_configured`/IMAP login, calendar fetch ICS, vision `shutil.which`, tgcli `which tg`.
- **L5** — watch/unwatch → `archive_admin_tool`; report → `archive_query` (в секретаре) / LLM-сводка в ЛС.
- **L6** — задания ведут в существующие handlers (`mail:list:*`, `_handle_digest_command`, calendar, tasks), без дублирования.
- **L7** — tgcli/vision в registry + validate (binary/session), tool policy по status=ready.
- **L8** — `/help` компактный + post-create hint.
