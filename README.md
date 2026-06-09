# Hermes Agent — Multi-Agent Edition

Расширенная версия [Hermes Agent](https://github.com/NousResearch/hermes-agent) с мульти-агентной оркестрацией, DAG-пайплайнами, изолированной памятью подагентов и **RuleEngine** для контроля поведения.

## Возможности

### Оркестрация и иерархия
- **5+ подагентов** — `coder`, `researcher`, `reviewer`, `summarizer`, `orchestrator` и динамическое создание
- **Иерархия уровней** — `level: 0` (orchestrator) → `level: 1` (субагенты) → `level: 2+` (подагенты)
- **DAG-оркестрация** — цепочки `coder → reviewer`, параллельное выполнение
- **Адаптивный оркестратор** — ultra-cheap классификатор: `SIMPLE` → 1 вызов, `COMPLEX` → full delegation
- **Динамическое создание** — `/subagents create` или `/agents-create` на лету

### Изоляция и безопасность
- **Горизонтальная изоляция** — субагент не может вызвать соседнего агента
- **Изоляция памяти** — каждый агент видит только свою ветку (`subtree_session_id`)
- **Full Clone агенты** — по умолчанию новый агент получает **все** инструменты (`enabled_toolsets: None`)
- **Контроль инструментов** — только оркестратор меняет `enabled_toolsets` через `/subagents tools <id> set|add`
- **Sandbox-режим** — `enabled_toolsets: []` = агент без инструментов
- **Права создания** — `level: 1` может создать только своих `level: 2` детей

### Память (Subtree Architecture)
- **Ветки памяти** — `coder` и его дети делят один `subtree_session_id`
- **Главный агент** — `main-session`; каждая ветка — свой изолированный `subtree`
- **Наследование** — подагенты наследуют `subtree_session_id` от родителя
- **Чтение памяти** — `/subagents memory <id>` показывает историю всей ветки

### RuleEngine
- **`critical_rules`** — правила в YAML-конфиге, переживают сессии
- **`_build_system_prompt`** — автоматически вставляет `[CRITICAL RULES]` в system_prompt
- **`rule_reminder_every`** — напоминание каждые N сообщений
- **RuleChecker** — детектор нарушений (keyword matching)
- **Self-correction loop** — до 2 попыток исправления
- **Сохранение при сжатии** — `[RULES STILL APPLY]` в history summary
- **Статистика нарушений** — `violations` и `last_violation` в `/agents`

### Интерфейс
- **Индикатор агента** — статус-бар показывает `[coder]`, когда активен субагент
- **Цветная иерархия** — `level: 0` (синий), `level: 1` (зелёный), `level: 2+` (жёлтый)
- **@mention-роутинг** — `@coder напиши сортировку` в Telegram / Discord
- **Слеш-команды** — `/agent`, `/orchestrate`, `/subagents`, `/agent-off`

### Per-Agent LLM Config
- **Индивидуальный провайдер** — каждый агент на своём провайдере (`anthropic` / `deepseek` / `openai`)
- **Наследование** — подагенты наследуют `provider`, `model`, `temperature` от родителя
- **Smart fallback** — автоматическое переключение на следующую модель из `fallback_models`
- **Auto-select** — `cheapest` / `fastest` / `balanced` — выбор модели из доступных
- **Propagation** — изменение настроек родителя применяется ко всей ветке
- **CLI-управление** — `/subagents provider` для просмотра и настройки

### Безопасное обновление
- **Версионированные миграции** — `MIGRATIONS` с уникальными ID, идемпотентные, не затирают пользовательские настройки
- **Автомиграция при `reload()`** — `apply_all_migrations()` вызывается при `/agents-reload` и старте
- **Трекинг** — `applied_migrations` + `migration_version` в YAML каждого агента
- **`multiagent_updater`** — делегирует версионированной системе миграций
- **Бэкап** — автоматический бэкап в `backups/` перед изменениями
- **Dry-run** — `/hermes-update --dry-run` показывает что изменится без правок
- **Сброс LLM** — `/hermes-update --reset-llm` для принудительного сброса (опционально)

---

## Архитектура

```
Пользователь → Главный агент Hermes [level: 0, main-session]
                 │
                 ├── CLI:   /subagents tree           — дерево иерархии
                 │          /subagents create <id>    — создать субагента
                 │          /subagents tools <id> set — управление инструментами
                 │          /subagents memory <id>    — чтение памяти ветки
                 │          /agent <id> <msg>         — вызов + активация
                 │          /agent-off                — возврат к главному
                 │          /orchestrate <msg>        — авто-делегирование
                 │          /agents                   — список с violations
                 │
                 ├── Gateway: @coder <msg>
                 │            @orchestrate <msg>
                 │
                 └── AgentRegistry
                      ├── MIGRATIONS [versioned, idempotent]
                      │    ├── 20260609: subtree_session_id
                      │    ├── 20260610: enabled_toolsets=None
                      │    ├── 20260611: critical_rules
                      │    └── 20260612: fallback_models, LLM params
                      │
                      ├── orchestrator [L0, main-session]
                      │    ├── critical_rules: маршрутизация, DELEGATE
                      │    ├── rule_reminder_every: 3
                      │    └── RuleChecker: self-correction
                      │
                      ├── coder [L1, subtree-coder]
                      │    ├── enabled_toolsets: [terminal, file, search, skills]
                      │    └── children: [L2] code-checker ← общая память
                      │
                      ├── researcher [L1, subtree-researcher]
                      │    ├── enabled_toolsets: [browser, search, web]
                      │    └── динамический клон → ALL tools (None)
                      │
                      ├── reviewer [L1, subtree-reviewer]
                      │    └── enabled_toolsets: [file, search]
                      │
                      └── summarizer [L1, subtree-summarizer]

Изоляция:
  coder ✗→ researcher      (горизонтальная блокировка)
  coder ✓→ code-checker    (свой потомок)
  orchestrator ✓→ любой    (level 0)

Инструменты:
  enabled_toolsets: null    → ALL (полный клон Гермеса)
  enabled_toolsets: [...]   → только указанные наборы
  enabled_toolsets: []      → sandbox (без инструментов)
```

---

## Установка

```bash
# Клонировать ветку multi-agent
git clone https://github.com/demossml/hermes-agent.git
cd hermes-agent
git checkout multi-agent

# Установить зависимости
pip install -e .

# Конфиги агентов уже лежат в agent_configs/
# Установить @mention-hook для gateway (опционально)
python install_hooks.py
```

---

## CLI-команды

### Управление агентами

```bash
# Дерево иерархии — уровни, вызовы, нарушения, провайдер
/subagents tree

# Создать субагента
/subagents create translator "Переводи на английский"
/subagents create code-checker "Проверяй код" --parent coder

# Инструменты
/subagents tools coder                     # показать текущие (ALL/список/none)
/subagents tools coder set file,search     # установить новые (замена)
/subagents tools coder add web,browser     # добавить к существующим

# Память ветки
/subagents memory coder                   # последние 20 сообщений
/subagents memory coder --limit 50        # последние 50
/subagents memory coder --full            # вся история

# Удалить
/subagents delete translator

# Список — ID, Lvl, Calls, Violations, Avg ms
/agents
```

### Режимы работы

```bash
# Вызов + активация субагента
/agent coder напиши функцию сортировки

# Только переключиться (без сообщения)
/agent coder

# Вернуться к главному агенту
/agent-off

# Авто-делегирование через оркестратор
/orchestrate исследуй алгоритмы и напиши бенчмарк
```

### Per-Agent LLM Config

```bash
/subagents provider coder                     # показать настройки LLM
/subagents provider coder set anthropic claude-3-opus
/subagents provider coder set-param temperature 0.3
/subagents provider coder fallback             # fallback-модели
/subagents provider coder reset                # сброс к дефолтам
/subagents provider list                       # список провайдеров

# Безопасное обновление
/hermes-update                                  # миграция конфигов
/hermes-update --dry-run                        # показать что изменится
/hermes-update --reset-llm                      # полный сброс LLM-настроек
```

---

## Gateway (Telegram / Discord)

```
@coder напиши парсер JSON
@researcher что такое RAG
@orchestrate сложная задача
@agents
@agents-reload
```

---

## Конфигурация агентов

Агенты живут в `agent_configs/*.yaml`. Полный пример:

```yaml
# ── Идентификация ───
agent_id: coder
description: "Пишет код"
level: 1
parent_id: orchestrator
subtree_session_id: subtree-coder

# ── LLM Config ──────
provider: anthropic
model: claude-3-5-sonnet-20240620
temperature: 0.7
max_tokens: 8192
top_p: 0.95
fallback_models:
  - claude-3-opus-20240229
  - claude-3-haiku-20240307
priority: 2                  # 1 = critical, 2 = normal, 3 = low
auto_select: balanced        # cheapest | fastest | balanced | none
reasoning_effort: medium
inherit_from_parent: true

# ── System Prompt ───
system_prompt: |
  Ты — агент-программист. Пишешь чистый, документированный код.

# ── RuleEngine ──────
critical_rules:
  - "НЕ пиши код пока не попросят явно"
  - "Всегда добавляй docstring к функциям"
rule_reminder_every: 3

# ── Инструменты ─────
max_context_tokens: 8000
max_iterations: 5
enabled_toolsets: [terminal, file, search]
```

---

## Python API

```python
import asyncio
from agent_registry import get_registry

async def main():
    r = get_registry()

    # ── Вызовы ───────────────────────────────
    reply = await r.call("coder", "session-1", "напиши sort",
                         caller_id="orchestrator")

    reply = await r.orchestrate("session-1", "исследуй алгоритмы")

    # ── Создание ─────────────────────────────
    created = r.create("helper", {
        "system_prompt": "Помогай с кодом",
        "parent_id": "coder",
    }, caller_id="orchestrator")         # level вычисляется автоматически

    # ── Изоляция ─────────────────────────────
    r._check_isolation("coder", "child1")       # ✅ потомок
    r._check_isolation("coder", "researcher")    # ❌ сосед — заблокирован

    # ── Память ───────────────────────────────
    msgs = r.get_subtree_memory("coder", limit=50)

    # ── Инструменты ──────────────────────────
    r._check_tool_permission("coder", "child1")  # ✅ свой потомок
    r.update_tools("child1", ["file"], caller_id="coder")

    # ── Статистика ───────────────────────────
    for a in r.list():
        print(f"{a['agent_id']}: {a['calls']} calls, "
              f"{a['violations']} violations")

    # ── Дерево ───────────────────────────────
    print(r.get_tree())

    # ── LLM Config ───────────────────────────
    r.update_provider("coder", "anthropic", "claude-opus",
                      caller_id="orchestrator")
    r.update_config_param("coder", "temperature", 0.3,
                          caller_id="orchestrator")
    r.propagate_to_subtree("coder", {"temperature": 0.5},
                           caller_id="orchestrator")

    cfg = r.get_effective_config("coder")
    print(f"Effective: {cfg['provider']}/{cfg['model']} "
          f"t={cfg['temperature']}")

asyncio.run(main())
```

---

## Permission Matrix

| Действие              | orchestrator (`L0`)    | sub-agent (`L1+`)        |
|-----------------------|------------------------|--------------------------|
| Создать агента        | под любым `parent`     | только `parent=self`     |
| Вызвать агента        | любого                 | только потомков          |
| Менять `enabled_toolsets` | любому             | себе и потомкам          |
| Менять LLM config     | любому                 | себе и потомкам          |
| Читать память         | любой ветки            | только своей             |
| Удалить агента        | любого                 | только своих детей       |

### Full Clone vs Sandbox

| `enabled_toolsets`  | Смысл                                   |
|----------------------|-----------------------------------------|
| `None` (default)     | **Full Clone** — все инструменты        |
| `["terminal","web"]` | Только указанные наборы                 |
| `[]`                 | **Sandbox** — агент без инструментов    |

### Версионированные миграции

```bash
# Применить все ожидающие миграции ко всем агентам
/agents-reload          # вызывает apply_all_migrations()

# Или через updater (с бэкапом и dry-run)
/hermes-update          # полное обновление + миграции
/hermes-update --dry-run  # показать что будет изменено
```

Каждая миграция:
- Имеет уникальный ID (например, `20260610_add_enabled_toolsets`)
- Идемпотентна — можно запускать多次 без вреда
- Не затирает пользовательские значения
- Трекается в `applied_migrations` внутри YAML агента

---

## Структура проекта

```
multi-agent/                           ← ветка
├── agent_registry.py                  ← реестр + оркестратор + RuleEngine
├── agent_configs/
│   ├── orchestrator.yaml              ← L0, main-session, critical_rules
│   ├── coder.yaml                     ← L1, subtree-coder
│   ├── researcher.yaml                ← L1, subtree-researcher
│   ├── reviewer.yaml                  ← L1, subtree-reviewer
│   └── summarizer.yaml                ← L1, subtree-summarizer
├── cli.py                             ← /subagents, /agent-off, индикатор
├── multiagent_updater.py              ← миграции (версионированная система)
├── hermes_cli/
│   └── commands.py                    ← CommandDef для новых команд
├── tests/
│   ├── test_multiagent_updater.py     ← тесты миграции
│   └── test_agent_registry.py         ← тесты реестра (smoke)
├── gateway/
│   ├── agent_mention.py               ← @mention-роутинг
│   └── run.py                         ← диспетчеризация
├── hooks/agent-mention/               ← hook-интеграция
└── install_hooks.py
```

---

## Лицензия

Основано на [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent). MIT License.
