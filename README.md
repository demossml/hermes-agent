# Hermes Agent — Multi-Agent Edition

Расширенная версия [Hermes Agent](https://github.com/NousResearch/hermes-agent) от Nous Research с мульти-агентной оркестрацией.

## Что добавлено

### Multi-Agent Orchestration

Главный агент управляет подагентами, каждый с изолированной памятью и контекстным окном.

```
Пользователь → Главный агент (Orchestrator)
                 ├── delegate_to_agent(coder, "напиши код")
                 ├── delegate_to_agent(researcher, "найди информацию")
                 └── create_subagent(translator, "claude-haiku", "переводчик")
```

### Инструменты главного агента

| Инструмент | Описание |
|-----------|----------|
| `create_subagent` | Создать подагента в диалоге: модель, токены, роль |
| `delegate_to_agent` | Отправить задачу подагенту (изолированная память) |
| `remove_subagent` | Удалить созданного подагента |

### Изоляция памяти

Каждый подагент имеет свою сессию в SessionDB. История сохраняется между вызовами. Контекстное окно настраивается индивидуально (`max_context_tokens`).

### Готовые агенты

- **coder** (claude-sonnet-4) — пишет и рефакторит код
- **researcher** (claude-sonnet-4) — ищет и анализирует информацию
- **reviewer** (claude-opus-4) — код-ревью и безопасность

### CLI

```bash
hermes agents setup             # установка зависимостей и миграция БД
hermes agents list              # список агентов
hermes agents spawn <id>        # запустить подагента
hermes agents call <id> "..."   # прямой вызов подагента
hermes agents stop <id>         # остановить
hermes agents remove <id>       # удалить
```

## Установка

```bash
# Клонировать
git clone git@github.com:demossml/hermes-agent.git
cd hermes-agent
git checkout multi-agent

# Установить
hermes agents setup

# Перезапустить Hermes
```

## Файлы

```
agent_configs/         # YAML конфиги подагентов
  coder.yaml
  researcher.yaml
  reviewer.yaml
agent_registry.py      # реестр агентов
subagent_manager.py    # менеджер сессий и памяти
providers/
  anthropic_provider.py  # нативный Anthropic SDK
tools/
  create_subagent.py
  delegate_to_agent.py
  remove_subagent.py
patches/               # диффы для оригинального Hermes
```

## Лицензия

Основано на [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent). MIT License.
