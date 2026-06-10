# Hermes Agent — Multi-Agent Edition

An extended version of [Hermes Agent](https://github.com/NousResearch/hermes-agent) with multi-agent orchestration, DAG pipelines, isolated sub-agent memory, per-agent LLM configuration, and a **RuleEngine** for behaviour control.

> **Built on Hermes Agent by Nous Research.** Works with any LLM provider. Runs on Linux, macOS, and WSL.

---

## Quick Start

```bash
# 1. Pull the latest changes
cd ~/.hermes/hermes-agent
git pull origin multi-agent
pip install -e .

# 2. Enable multi-agent toolsets (auto-propagates to all sub-agents)
hermes tools enable delegation messaging

# 3. Apply migrations to existing agent configs (if any)
hermes update

# 4. Launch Hermes
hermes
```

You're ready to go:

```
/subagents tree                                   # view the agent hierarchy
/subagents create myclone "You are a Python expert"  # create a full clone
/agent myclone write a sorting function            # call a sub-agent
/orchestrate research sorting algorithms            # auto-delegate
/agents                                           # list all agents with stats
```

**What `hermes tools enable delegation messaging` does:**
- Enables `delegation` and `messaging` globally for Hermes
- Automatically propagates these toolsets to **all existing** L1+ agents
- New agents are created with full access by default (`enabled_toolsets: null`)
- No need to manually run `/subagents tools` for each agent

---

## Creating a Full Clone Agent

By default, every new sub-agent is a **full Hermes clone** — it gets access to all tools, including the ability to create its own sub-agents, work with Telegram/Discord, access the file system, and drive a browser.

```bash
# Create a full clone (default — ALL tools)
/subagents create assistant "You are an autonomous Hermes clone. You can create sub-agents and work across platforms."

# Explicit full access
/subagents create myclone "..." --full
/subagents create myclone "..." --tools all

# Restricted toolset
/subagents create helper "..." --tools terminal,file

# Create under a different parent
/subagents create child "..." --parent coder
```

**Success output:**

```
✅ Sub-agent 'assistant' successfully created as a full clone

Level:    1 | Parent: orchestrator
Provider: deepseek (deepseek-v4-pro) — inherited
Tools:    ALL (full access)
Memory:   isolated (subtree-assistant-a1b2c3d4)

Capabilities:
  ✅ Delegation: Can create sub-agents and delegate tasks
  ✅ Messaging:  Can send messages to Telegram/Discord/Slack
  ✅ Terminal:   Shell command access
  ✅ File:       Read/write/search files
  ✅ Web Search: Internet search
  ✅ Skills:     Skill management (persistent experience)
  ✅ Browser:    Browser automation

Usage:
  /agent assistant <task>     — send a task to this sub-agent
```

**Auto-propagation on `hermes tools enable`:**

```bash
$ hermes tools enable delegation
Enabled: delegation
Multi-agent: propagated ['delegation'] to 3 agent(s) (1 skipped)
# → coder, researcher, helper received delegation
# → orchestrator skipped (already full clone)
```

---

## Features

### Orchestration & Hierarchy
- **5+ built-in sub-agents** — `coder`, `researcher`, `reviewer`, `summarizer`, `orchestrator` plus dynamic creation
- **Level-based hierarchy** — `level: 0` (Orchestrator) → `level: 1` (sub-agents) → `level: 2+` (grandchildren)
- **DAG pipelines** — `coder → reviewer` chains, parallel execution
- **Adaptive orchestration** — ultra-cheap classifier: `SIMPLE` → 1 API call, `COMPLEX` → full delegation
- **Dynamic creation** — `/subagents create` or `/agents-create` on the fly

### Isolation & Security
- **Horizontal isolation** — a sub-agent cannot call a sibling agent
- **Memory isolation** — each agent sees only its own branch (`subtree_session_id`)
- **Full Clone agents** — new agents get **all** tools by default (`enabled_toolsets: None`)
- **Tool control** — only the Orchestrator can change `enabled_toolsets` via `/subagents tools <id> set|add`
- **Sandbox mode** — `enabled_toolsets: []` = agent with zero tools
- **Creation permissions** — `level: 1` agents can only create `level: 2` children

### Subtree Memory Architecture
- **Memory branches** — `coder` and its children share one `subtree_session_id`
- **Main agent** — `main-session`; each branch has its own isolated `subtree`
- **Inheritance** — grandchildren inherit `subtree_session_id` from their parent
- **Memory inspection** — `/subagents memory <id>` shows the entire branch history

### RuleEngine
- **`critical_rules`** — rules in the YAML config that persist across sessions
- **`_build_system_prompt`** — automatically injects `[CRITICAL RULES]` into the system prompt
- **`rule_reminder_every`** — reminder every N user messages
- **RuleChecker** — violation detector (keyword matching)
- **Self-correction loop** — up to 2 correction attempts
- **Compression-safe** — `[RULES STILL APPLY]` preserved in history summaries
- **Violation stats** — `violations` and `last_violation` visible in `/agents`

### Interface
- **Agent indicator** — status bar shows `[coder]` when a sub-agent is active
- **Colored hierarchy** — `level: 0` (blue), `level: 1` (green), `level: 2+` (yellow)
- **@mention routing** — `@coder write a sorting function` in Telegram / Discord
- **Slash commands** — `/agent`, `/orchestrate`, `/subagents`, `/agent-off`

### Per-Agent LLM Configuration
- **Individual provider** — each agent on its own provider (`anthropic` / `deepseek` / `openai`)
- **Inheritance** — children inherit `provider`, `model`, `temperature` from their parent
- **Smart fallback** — automatic switch to the next model in `fallback_models` on failure
- **Auto-select** — `cheapest` / `fastest` / `balanced` — pick the best model automatically
- **Propagation** — parent config changes apply to the entire branch
- **CLI management** — `/subagents provider` to view and configure

### Safe Updater
- **Versioned migrations** — `MIGRATIONS` with unique IDs; idempotent; never overwrite user settings
- **Auto-migration on reload** — `apply_all_migrations()` runs on `/agents-reload` and startup
- **Tracking** — `applied_migrations` + `migration_version` in each agent's YAML
- **`multiagent_updater`** — delegates to the versioned migration system
- **Backup** — automatic backup to `backups/` before any changes
- **Dry-run** — `/hermes-update --dry-run` shows what will change without applying
- **LLM reset** — `/hermes-update --reset-llm` for forced reset (optional)

### Auto-Propagation
- **`propagate_toolset_to_agents()`** — when you `hermes tools enable`, the new toolset is automatically added to every existing L1+ agent
- After `git pull`, just run `hermes tools enable delegation messaging` — all agents are ready
- Full Clone agents (None) are skipped — they already have everything

---

## Architecture

```
User → Main Hermes Agent [level: 0, main-session]
           │
           ├── CLI:   /subagents tree           — view hierarchy tree
           │          /subagents create <id>    — create a sub-agent
           │          /subagents tools <id> set — manage tools
           │          /subagents memory <id>    — read branch memory
           │          /agent <id> <msg>         — call + activate
           │          /agent-off                — return to main agent
           │          /orchestrate <msg>        — auto-delegate
           │          /agents                   — list with Violations
           │
           ├── Gateway: @coder <msg>
           │            @orchestrate <msg>
           │
           └── AgentRegistry
                ├── MIGRATIONS [versioned, idempotent]
                │    ├── 20260609: subtree_session_id
                │    ├── 20260610: enabled_toolsets=None
                │    ├── 20260611: critical_rules
                │    ├── 20260612: fallback_models, LLM params
                │    └── 20260613: upgrade agent toolsets
                │
                ├── propagate_toolset_to_agents()
                │    └── hermes tools enable → auto-propagation
                │
                ├── orchestrator [L0, main-session]
                │    ├── critical_rules: routing, DELEGATE
                │    ├── rule_reminder_every: 3
                │    └── RuleChecker: self-correction
                │
                ├── coder [L1, subtree-coder]
                │    ├── enabled_toolsets: [terminal, file, search, skills]
                │    └── children: [L2] code-checker ← shared memory
                │
                ├── researcher [L1, subtree-researcher]
                │    ├── enabled_toolsets: [browser, search, web]
                │    └── dynamic clone → ALL tools (None)
                │
                ├── reviewer [L1, subtree-reviewer]
                │    └── enabled_toolsets: [file, search]
                │
                └── summarizer [L1, subtree-summarizer]

Isolation:
  coder ✗→ researcher      (horizontal block)
  coder ✓→ code-checker    (own descendant)
  orchestrator ✓→ any      (level 0)

Toolsets:
  enabled_toolsets: null    → ALL (full Hermes clone)
  enabled_toolsets: [...]   → restricted to listed toolsets
  enabled_toolsets: []      → sandbox (no tools)
```

---

## Installation

### Fresh Install

```bash
git clone https://github.com/demossml/hermes-agent.git
cd hermes-agent
git checkout multi-agent
pip install -e .
```

### Update Existing

```bash
cd ~/.hermes/hermes-agent
git pull origin multi-agent
pip install -e .

# Enable multi-agent toolsets (auto-propagates to all agents)
hermes tools enable delegation messaging

# Apply migrations to existing configs
hermes update
```

After the update, all existing agents automatically receive the new toolsets. New agents are created as full clones by default.

### Install @mention Hook (optional)

```bash
python install_hooks.py
```

---

## CLI Commands

### Agent Management

```bash
# Hierarchy tree — levels, calls, violations, provider
/subagents tree

# Create a sub-agent
/subagents create translator "Translate to English"
/subagents create expert "You are a Python expert" --full      # full clone
/subagents create helper "..." --tools terminal,file             # restricted
/subagents create code-checker "Review code" --parent coder

# Tools
/subagents tools coder                     # show current (ALL / list / none)
/subagents tools coder set file,search     # replace all tools
/subagents tools coder add web,browser     # add to existing

# Branch memory
/subagents memory coder                   # last 20 messages
/subagents memory coder --limit 50        # last 50
/subagents memory coder --full            # entire history

# Delete
/subagents delete translator

# List — ID, Lvl, Calls, Violations, Avg ms
/agents
```

### Working Modes

```bash
# Call + activate a sub-agent
/agent coder write a sorting function

# Switch without sending a message
/agent coder

# Return to the main agent
/agent-off

# Auto-delegate through the Orchestrator
/orchestrate research algorithms and write a benchmark
```

### Per-Agent LLM Config

```bash
/subagents provider coder                     # show LLM settings
/subagents provider coder set anthropic claude-3-opus
/subagents provider coder set-param temperature 0.3
/subagents provider coder fallback             # view fallback models
/subagents provider coder reset                # reset to defaults
/subagents provider list                       # list available providers

# Safe update
/hermes-update                                  # migrate configs
/hermes-update --dry-run                        # preview changes
/hermes-update --reset-llm                      # full LLM reset
```

---

## Gateway (Telegram / Discord)

```
@coder write a JSON parser
@researcher what is RAG
@orchestrate complex multi-step task
@agents
@agents-reload
```

---

## Agent Configuration

Agents live in `agent_configs/*.yaml`. Full example:

```yaml
# ── Identity ────────
agent_id: coder
description: "Writes clean, documented code"
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
  You are a code-writing agent. Write clean, documented Python code.
  Follow best practices and add type hints.

# ── RuleEngine ──────
critical_rules:
  - "Do NOT write code unless explicitly asked"
  - "Always add docstrings to functions"
rule_reminder_every: 3

# ── Tools ───────────
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

    # ── Calls ──────────────────────────────────
    reply = await r.call("coder", "session-1", "write a sort function",
                         caller_id="orchestrator")

    reply = await r.orchestrate("session-1", "research sorting algorithms")

    # ── Creation ───────────────────────────────
    created = r.create("helper", {
        "system_prompt": "Help with code",
        "parent_id": "coder",
    }, caller_id="orchestrator")         # level auto-computed

    # ── Isolation ──────────────────────────────
    r._check_isolation("coder", "child1")       # ✅ descendant
    r._check_isolation("coder", "researcher")    # ❌ sibling — blocked

    # ── Memory ─────────────────────────────────
    msgs = r.get_subtree_memory("coder", limit=50)

    # ── Tools ──────────────────────────────────
    r._check_tool_permission("coder", "child1")  # ✅ own descendant
    r.update_tools("child1", ["file"], caller_id="coder")

    # ── Stats ──────────────────────────────────
    for a in r.list():
        print(f"{a['agent_id']}: {a['calls']} calls, "
              f"{a['violations']} violations")

    # ── Tree ───────────────────────────────────
    print(r.get_tree())

    # ── LLM Config ─────────────────────────────
    r.update_provider("coder", "anthropic", "claude-opus",
                      caller_id="orchestrator")
    r.update_config_param("coder", "temperature", 0.3,
                          caller_id="orchestrator")
    r.propagate_to_subtree("coder", {"temperature": 0.5},
                           caller_id="orchestrator")

    cfg = r.get_effective_config("coder")
    print(f"Effective: {cfg['provider']}/{cfg['model']} "
          f"t={cfg['temperature']}")

    # ── Propagation ────────────────────────────
    report = r.propagate_toolset_to_agents(["delegation"])
    print(f"Updated {report['agents_updated']} agents")

asyncio.run(main())
```

---

## Permission Matrix

| Action                 | Orchestrator (`L0`)     | Sub-agent (`L1+`)          |
|------------------------|-------------------------|----------------------------|
| Create agent           | under any `parent`      | only `parent=self`         |
| Call agent             | any                     | only descendants            |
| Change `enabled_toolsets` | any                  | self and descendants        |
| Change LLM config      | any                     | self and descendants        |
| Read memory            | any branch              | own branch only             |
| Delete agent           | any                     | own children only           |

### Full Clone vs Sandbox

| `enabled_toolsets`   | Meaning                                  |
|----------------------|------------------------------------------|
| `None` (default)     | **Full Clone** — all tools available     |
| `["terminal","web"]` | Restricted to listed toolsets            |
| `[]`                 | **Sandbox** — agent with zero tools      |

### Versioned Migrations

```bash
# Apply all pending migrations to all agents
/agents-reload          # calls apply_all_migrations()

# Or via the updater (with backup and dry-run)
/hermes-update          # full update + migrations
/hermes-update --dry-run  # preview what will change
```

Each migration:
- Has a unique ID (e.g. `20260610_add_enabled_toolsets`)
- Is idempotent — safe to run repeatedly
- Never overwrites user-set values
- Is tracked in `applied_migrations` inside each agent's YAML

---

## Project Structure

```
multi-agent/                           ← branch
├── agent_registry.py                  ← registry + orchestrator + RuleEngine
├── agent_configs/
│   ├── orchestrator.yaml              ← L0, main-session, critical_rules
│   ├── coder.yaml                     ← L1, subtree-coder
│   ├── researcher.yaml                ← L1, subtree-researcher
│   ├── reviewer.yaml                  ← L1, subtree-reviewer
│   └── summarizer.yaml                ← L1, subtree-summarizer
├── cli.py                             ← /subagents, /agent-off, status indicator
├── multiagent_updater.py              ← migrations (versioned system)
├── hermes_cli/
│   └── commands.py                    ← CommandDef for new commands
├── tests/
│   ├── test_multiagent_updater.py     ← migration tests
│   └── test_agent_registry.py         ← registry smoke tests
├── gateway/
│   ├── agent_mention.py               ← @mention routing
│   └── run.py                         ← dispatch
├── hooks/agent-mention/               ← hook integration
└── install_hooks.py
```

---

## License

Based on [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent). MIT License.
