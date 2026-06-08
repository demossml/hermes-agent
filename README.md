# Hermes Agent — Multi-Agent Edition

Расширенная версия [Hermes Agent](https://github.com/NousResearch/hermes-agent) с мульти-агентной оркестрацией, DAG-пайплайнами и изолированной памятью подагентов.

## Возможности

- **5 подагентов** — coder, researcher, reviewer, summarizer, orchestrator
- **DAG-оркестрация** — цепочки `coder → reviewer`, параллельное выполнение
- **@mention роутинг** — `@coder напиши сортировку` в Telegram/Discord
- **Slash-команды** — `/agent`, `/orchestrate`, `/agents` в CLI и gateway
- **provider: current** — подагенты используют тот же провайдер что и главный Hermes (без отдельных API-ключей)
- **Per-agent toolsets** — coder с terminal/file, researcher с browser/search
- **История между вызовами** — скользящее окно токенов + авто-суммаризация
- **Shared scratchpad** — канал для общения агентов
- **Статистика** — calls, tokens, avg latency на каждого агента
- **Retry с fallback** — автоматический ретрай при ошибках

## Архитектура

```
Пользователь → Главный агент Hermes
                 │
                 ├── CLI:   /agent coder <msg>
                 │          /orchestrate <msg>
                 │          /agents
                 │
                 ├── Gateway: @coder <msg>
                 │            @orchestrate <msg>
                 │            @agents
                 │
                 └── AgentRegistry
                      ├── orchestrator (маршрутизация)
                      │    ├── DELEGATE: coder | задача | -> reviewer
                      │    ├── DELEGATE: researcher | задача
                      │    └── Параллельное выполнение через asyncio.gather
                      │
                      ├── coder (терминал, файлы, код)
                      ├── researcher (браузер, поиск)
                      ├── reviewer (проверка кода)
                      └── summarizer (резюме)
```

## Установка

```bash
# Клонировать ветку multi-agent
git clone https://github.com/demossml/hermes-agent.git
cd hermes-agent
git checkout multi-agent

# Установить зависимости (если нужно)
pip install -e .

# Скопировать конфиги агентов
cp agent_configs/*.yaml ~/.hermes/hermes-agent/agent_configs/

# Установить @mention hook для gateway (опционально)
python install_hooks.py
hermes hooks doctor
```

## CLI команды

```bash
# Список всех подагентов со статистикой
/agents

# Прямой вызов подагента
/agent coder напиши функцию сортировки на Python
/agent researcher объясни архитектуру transformer
/agent reviewer проверь этот код на безопасность

# Оркестратор — сам решит кому делегировать
/orchestrate исследуй алгоритмы сортировки и напиши бенчмарк

# Создать нового агента на лету
/agents-create translator Переводи всё на английский

# Hot-reload конфигов
/agents-reload
```

## Gateway (Telegram / Discord)

```
@agents                              # список агентов
@coder напиши парсер JSON            # прямой вызов coder
@researcher что такое RAG            # прямой вызов researcher
@orchestrate сложная задача          # через оркестратор
@agents-reload                       # перезагрузка конфигов
```

## Конфигурация агентов

Агенты живут в `agent_configs/*.yaml`. Пример:

```yaml
agent_id: coder
provider: current          # current = использовать провайдер Hermes
description: "Пишет код"
system_prompt: |
  Ты — агент-программист. Пишешь чистый код.
max_context_tokens: 8000
max_iterations: 5          # макс. итераций tool loop
enabled_toolsets: [terminal, file, search, skills]
```

`provider: current` — подагент использует тот же API-ключ что и главный Hermes. Для отдельных провайдеров: `provider: anthropic` или `provider: deepseek`.

## DAG пайплайны

Оркестратор поддерживает цепочки через `| ->`:

```
DELEGATE: coder | напиши парсер | -> reviewer
DELEGATE: researcher | найди best practices
```

- coder получает задачу, reviewer получает **вывод coder** как контекст
- Если coder упал — reviewer пропускается
- Результаты сохраняются как `coder→reviewer` в статистике

## Python API

```python
import asyncio
from agent_registry import get_registry

async def main():
    r = get_registry()

    # Прямой вызов
    reply = await r.call("coder", "session-1", "напиши hello world")
    print(reply)

    # Оркестратор
    reply = await r.orchestrate("session-1", "исследуй и напиши сортировку")
    print(reply)

    # Стриминг
    async for chunk in r.stream("researcher", "session-1", "что такое RAG"):
        print(chunk, end="")

    # Диалог между агентами
    transcript = await r.dialogue("coder", "researcher", "session-1",
        "Discuss: is Python good for ML?", turns=3)

    # Статистика
    for a in r.list():
        print(f"{a['agent_id']}: {a['calls']} calls, {a['avg_latency_ms']}ms")

    # Создать агента на лету
    r.create("translator", {"system_prompt": "Translate to English"})

    # Shared scratchpad
    r.scratchpad_publish("general", "coder", "I wrote the parser")
    msgs = r.scratchpad_read("general")

asyncio.run(main())
```

## Структура файлов

```
agent_registry.py              # реестр + оркестратор + DAG
agent_configs/
├── coder.yaml                 # пишет код (terminal, file, search)
├── researcher.yaml            # исследует (browser, search, web)
├── reviewer.yaml              # проверяет код
├── orchestrator.yaml          # маршрутизация (без инструментов)
└── summarizer.yaml            # резюмирует текст

gateway/
├── agent_mention.py           # @mention роутинг для gateway
└── run.py                     # патч: перехват @ перед dispatch

hooks/
└── agent-mention/             # Hook-альтернатива для @mention
    ├── HOOK.yaml
    └── handler.py

install_hooks.py               # установщик hook
```

## Зависимости

- Hermes Agent (основной репо)
- `pyyaml` — парсинг конфигов
- `anthropic` — опционально, если `provider: anthropic`
- SessionDB из `hermes_state.py` — для истории и scratchpad

## Лицензия

Основано на [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent). MIT License.
