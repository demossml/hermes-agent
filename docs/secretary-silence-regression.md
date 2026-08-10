# Secretary Silence Regression Report (S14)

**Status: PASS** ✓

## Code Path Audit

### TEXT messages (`_handle_text_message`, L6048)

```
msg arrives
  → _should_process_message(msg) [L6060]
    → NO: _should_observe_unmentioned_group_message [L6061]
      → YES: archive_bridge [L6066-6081] → return (no agent)
      → NO: return (no agent)
    → YES: continue
      → NL mode/secretary/project/who triggers [L6085-6120]
      → onboarding text intercept [L6122]
      → menu trigger [L6126]
      → agent event [L6120-6124]
```

### COMMAND messages (`_handle_command`, L6126)

```
cmd arrives
  → /rule [L6135] → local handler
  → /start [L6140] → onboarding handler
  → /menu [L6145] → menu handler
  → /mode /who /digest /secretary_health [L6150-6180]
  → _should_process_message(msg, is_command=True) [L6179]
    → NO: return (no agent, blocked)
  → agent event
```

### LOCATION / MEDIA handlers

Аналогично TEXT: `_should_process_message` → observe → archive bridge.

## Что проверено

| Компонент | Статус |
|-----------|--------|
| `_should_process_message` на месте (TEXT/CMD/LOCATION/MEDIA) | ✓ |
| `_should_observe_unmentioned_group_message` | ✓ |
| `archive_bridge.archive_enabled()` вызов | ✓ |
| `archive_message_context()` — 3 точки (TEXT/LOCATION/MEDIA) | ✓ |
| `response_filters` импорт не задет | ✓ |
| Новые interceptы (menu/onb/who/digest/health) — ПОСЛЕ фильтра | ✓ |
| Команды в группах: `is_command=True` фильтр | ✓ |

## Ручной сценарий

1. Добавить группу в archive allowlist
2. Написать сообщение без @bot → нет ответа агента, запись в archive
3. @bot → ответ есть
4. `/start` в группе → если user не в allowlist → нет ответа

## Что grep'ать в gateway.log

```
grep "archive_enabled\|archive_message_context\|should_skip_agent" gateway.log
grep "require_mention\|should_process_message" gateway.log
```

## Тесты

72/72 PASS. Все secretary-тесты целы.
