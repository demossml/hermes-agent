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
