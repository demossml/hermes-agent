# Hermes Agent — Multi-Agent Edition

Расширенная версия [Hermes Agent](https://github.com/NousResearch/hermes-agent) с мульти-агентной оркестрацией, DAG-пайплайнами, изолированной памятью подагентов и RuleEngine для контроля поведения.

## Возможности

### Оркестрация и иерархия
- **5+ подагентов** — coder, researcher, reviewer, summarizer, orchestrator + динамическое создание
- **Иерархия уровней** — level 0 (orchestrator) → level 1 (субагенты) → level 2+ (подагенты)
- **DAG-оркестрация** — цепочки `coder → reviewer`, параллельное выполнение
- **Адаптивный оркестратор** — ultra-cheap классификатор: SIMPLE → 1 вызов, COMPLEX → full delegation
- **Динамическое создание** — `/subagents create` или `/agents-create` на лету

### Изоляция и безопасность
- **Горизонтальная изоляция** — субагент не может вызвать соседнего агента
- **Изоляция памяти** — каждый агент видит только свою ветку (subtree_session_id)
- **Контроль инструментов** — только оркестратор меняет tools; субагент — только себе и потомкам
- **Права создания** — level 1 может создать только своих level 2 детей

### Память (Subtree Architecture)
- **Ветки памяти** — coder + его дети делят один `subtree_session_id`
- **Главный агент** — `main-session`; каждая ветка — свой изолированный subtree
- **Наследование** — подагенты наследуют `subtree_session_id` от родителя
- **Чтение памяти** — `/subagents memory <id>` показывает историю всей ветки

### RuleEngine
- **critical_rules** — правила в YAML конфиге, переживают сессии
- **_build_system_prompt** — автоматически вставляет [CRITICAL RULES] в system_prompt
- **rule_reminder_every** — напоминание каждые N сообщений
- **RuleChecker** — детектор нарушений (keyword matching)
- **Self-correction loop** — до 2 попыток исправления
- **Сохранение при сжатии** — [RULES STILL APPLY] в history summary
- **Статистика нарушений** — violations + last_violation в /agents

### Интерфейс
- **Индикатор агента** — статус-бар показывает `[coder]` когда активен субагент
- **Цветная иерархия** — level 0 (синий), level 1 (зелёный), level 2+ (жёлтый)
- **@mention роутинг** — `@coder напиши сортировку` в Telegram/Discord
- **Slash-команды** — `/agent`, `/orchestrate`, `/subagents`, `/agent-off`

### Per-Agent LLM Config
- **Индивидуальный провайдер** — каждый агент на своём провайдере (anthropic/deepseek/openai)
- **Наследование** — подагенты наследуют provider, model, temperature от родителя
- **Smart fallback** — автоматическое переключение на следующую модель из `fallback_models`
- **Auto-select** — cheapest/fastest/balanced выбор модели из доступных
- **Propagation** — изменение настроек родителя → вся ветка
- **CLI управление** — `/subagents provider` для просмотра и настройки

## Архитектура

```
Пользователь → Главный агент Hermes [level 0, main-session]
                 │
                 ├── CLI:   /subagents tree           — дерево иерархии
                 │          /subagents create <id>    — создать субагента
                 │          /subagents tools <id> set — управление инструментами
                 │          /subagents memory <id>    — чтение памяти ветки
                 │          /agent <id> <msg>         — вызов + активация
                 │          /agent-off                — возврат к главному
                 │          /orchestrate <msg>        — авто-делегирование
                 │          /agents                   — список + violations
                 │
                 ├── Gateway: @coder <msg>
                 │            @orchestrate <msg>
                 │
                 └── AgentRegistry
                      ├── orchestrator [L0, main-session]
                      │    ├── critical_rules: маршрутизация, DELEGATE
                      │    ├── rule_reminder_every: 3
                      │    └── RuleChecker: self-correction
                      │
                      ├── coder [L1, subtree-coder]
                      │    ├── tools: terminal, file
                      │    └── children: [L2] code-checker ← общая память
                      │
                      ├── researcher [L1, subtree-researcher]
                      │    └── tools: browser, search
                      │
                      ├── reviewer [L1, subtree-reviewer]
                      │    └── tools: file, search
                      │
                      └── summarizer [L1, subtree-summarizer]

Изоляция:
  coder ✗→ researcher    (горизонтальная блокировка)
  coder ✓→ code-checker   (свой потомок)
  orchestrator ✓→ любой   (level 0)
```

## Установка

```bash
# Клонировать ветку multi-agent
git clone https://github.com/demossml/hermes-agent.git
cd hermes-agent
git checkout multi-agent

# Установить зависимости
pip install -e .

# Конфиги агентов уже в agent_configs/
# Установить @mention hook для gateway (опционально)
python install_hooks.py
```

## CLI команды

```bash
# Дерево иерархии с уровнями и violations
/subagents tree

# Создать субагента (по умолчанию под текущим активным)
/subagents create translator "Переводи на английский"
/subagents create code-checker "Проверяй код" --parent coder

# Управление инструментами
/subagents tools coder                    # показать текущие
/subagents tools coder set file,search    # установить новые

# Память ветки
/subagents memory coder                   # последние 20 сообщений
/subagents memory coder --limit 50        # последние 50
/subagents memory coder --full            # вся история

# Удалить субагента
/subagents delete translator

# Список с колонкой Violations
/agents

# Прямой вызов + активация
/agent coder напиши функцию сортировки
/agent coder                               # только переключиться

# Вернуться к главному
/agent-off

# Оркестратор
/orchestrate исследуй и напиши бенчмарк

# ── Per-agent LLM config ─────────────────────
/subagents provider coder                     # показать настройки LLM
/subagents provider coder set anthropic claude-3-opus
/subagents provider coder set-param temperature 0.3
/subagents provider coder fallback             # fallback-модели
/subagents provider coder reset                # сброс к дефолтам
/subagents provider list                       # список провайдеров
```

## Gateway (Telegram / Discord)

```
@coder напиши парсер JSON
@researcher что такое RAG
@orchestrate сложная задача
@agents
@agents-reload
```

## Конфигурация агентов

```yaml
agent_id: coder
provider: current
level: 1
parent_id: orchestrator
subtree_session_id: subtree-coder
description: "Пишет код"
system_prompt: |
  Ты — агент-программист.

# ── LLM Config ──────
provider: anthropic
model: claude-3-5-sonnet-20240620
temperature: 0.7
max_tokens: 8192
top_p: 0.95
fallback_models:
  - claude-3-opus-20240229
  - claude-3-haiku-20240307
priority: 2
auto_select: balanced
reasoning_effort: medium
inherit_from_parent: true

# ── RuleEngine ──────
critical_rules:
  - "НЕ пиши код пока не попросят явно"
  - "Всегда добавляй docstring"
rule_reminder_every: 3

# ── Настройки ───────
max_context_tokens: 8000
max_iterations: 5
enabled_toolsets: [terminal, file, search]
```

## Python API

```python
import asyncio
from agent_registry import get_registry

async def main():
    r = get_registry()

    # Прямой вызов с проверкой изоляции
    reply = await r.call("coder", "session-1", "напиши sort",
                         caller_id="orchestrator")

    # Оркестратор (адаптивный: SIMPLE/COMPLEX)
    reply = await r.orchestrate("session-1", "исследуй алгоритмы")

    # Создать агента с проверкой прав
    created = r.create("helper", {
        "system_prompt": "Помогай с кодом",
        "parent_id": "coder",
    }, caller_id="orchestrator")  # level авто = 2

    # Разрешённые вызовы
    r._check_isolation("coder", "child1")       # ✅ потомок
    r._check_isolation("coder", "researcher")    # ❌ сосед

    # Memory
    msgs = r.get_subtree_memory("coder", limit=50)

    # Tools
    r._check_tool_permission("coder", "child1")  # ✅ свой потомок
    r.update_tools("child1", ["file"], caller_id="coder")

    # Статистика с violations
    for a in r.list():
        print(f"{a['agent_id']}: {a['calls']} calls, "
              f"{a['violations']} violations")

    # Дерево иерархии
    print(r.get_tree())

    # ── LLM Config ──────────────────────────
    r.update_provider("coder", "anthropic", "claude-opus", caller_id="orchestrator")
    r.update_config_param("coder", "temperature", 0.3, caller_id="orchestrator")
    r.propagate_to_subtree("coder", {"temperature": 0.5}, caller_id="orchestrator")
    effective = r.get_effective_config("coder")
    print(f"Effective: {effective['provider']}/{effective['model']} t={effective['temperature']}")

asyncio.run(main())
```

## Permission Matrix

| Действие | orchestrator (L0) | sub-agent (L1+) |
|----------|------------------|-----------------|
| Создать агента | под любым parent | только parent=self |
| Вызвать агента | любого | только потомков |
| Менять tools | любому | себе и потомкам |
| Менять LLM config | любому | себе и потомкам |
| Читать память | любой ветки | только своей |
| Удалить агента | любого | только своих детей |

## Структура

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
├── hermes_cli/
│   └── commands.py                    ← CommandDef для новых команд
├── gateway/
│   ├── agent_mention.py               ← @mention роутинг
│   └── run.py                         ← диспетчеризация
├── hooks/agent-mention/               ← hook-интеграция
└── install_hooks.py
```

## Лицензия

Основано на [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent). MIT License.
