# Hermes Agent ☤
<p align="center">
  <a href="https://hermes-agent.nousresearch.com/">Hermes Agent</a> | <a href="https://hermes-agent.nousresearch.com/">Hermes Desktop</a>
</p>
<p align="center">
  <a href="https://hermes-agent.nousresearch.com/docs/"><img src="https://img.shields.io/badge/Docs-hermes--agent.nousresearch.com-FFD700?style=for-the-badge" alt="Documentation"></a>
  <a href="https://discord.gg/NousResearch"><img src="https://img.shields.io/badge/Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord"></a>
  <a href="https://github.com/NousResearch/hermes-agent/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="https://nousresearch.com"><img src="https://img.shields.io/badge/Built%20by-Nous%20Research-blueviolet?style=for-the-badge" alt="Built by Nous Research"></a>
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/Lang-中文-red?style=for-the-badge" alt="中文"></a>
  <a href="README.ur-pk.md"><img src="https://img.shields.io/badge/Lang-اردو-green?style=for-the-badge" alt="اردو"></a>
</p>

**The self-improving AI agent built by [Nous Research](https://nousresearch.com).** It's the only agent with a built-in learning loop — it creates skills from experience, improves them during use, nudges itself to persist knowledge, searches its own past conversations, and builds a deepening model of who you are across sessions. Run it on a $5 VPS, a GPU cluster, or serverless infrastructure that costs nearly nothing when idle. It's not tied to your laptop — talk to it from Telegram while it works on a cloud VM.

Use any model you want — [Nous Portal](https://portal.nousresearch.com), [OpenRouter](https://openrouter.ai) (200+ models), [NovitaAI](https://novita.ai) (AI-native cloud for Model API, Agent Sandbox, and GPU Cloud), [NVIDIA NIM](https://build.nvidia.com) (Nemotron), [Xiaomi MiMo](https://platform.xiaomimimo.com), [z.ai/GLM](https://z.ai), [Kimi/Moonshot](https://platform.moonshot.ai), [MiniMax](https://www.minimax.io), [Hugging Face](https://huggingface.co), OpenAI, or your own endpoint. Switch with `hermes model` — no code changes, no lock-in.

<table>
<tr><td><b>A real terminal interface</b></td><td>Full TUI with multiline editing, slash-command autocomplete, conversation history, interrupt-and-redirect, and streaming tool output.</td></tr>
<tr><td><b>Lives where you do</b></td><td>Telegram, Discord, Slack, WhatsApp, Signal, and CLI — all from a single gateway process. Voice memo transcription, cross-platform conversation continuity.</td></tr>
<tr><td><b>A closed learning loop</b></td><td>Agent-curated memory with periodic nudges. Autonomous skill creation after complex tasks. Skills self-improve during use. FTS5 session search with LLM summarization for cross-session recall. <a href="https://github.com/plastic-labs/honcho">Honcho</a> dialectic user modeling. Compatible with the <a href="https://agentskills.io">agentskills.io</a> open standard.</td></tr>
<tr><td><b>Scheduled automations</b></td><td>Built-in cron scheduler with delivery to any platform. Daily reports, nightly backups, weekly audits — all in natural language, running unattended.</td></tr>
<tr><td><b>Delegates and parallelizes</b></td><td>Spawn isolated subagents for parallel workstreams. Write Python scripts that call tools via RPC, collapsing multi-step pipelines into zero-context-cost turns.</td></tr>
<tr><td><b>Runs anywhere, not just your laptop</b></td><td>Six terminal backends — local, Docker, SSH, Singularity, Modal, and Daytona. Daytona and Modal offer serverless persistence — your agent's environment hibernates when idle and wakes on demand, costing nearly nothing between sessions. Run it on a $5 VPS or a GPU cluster.</td></tr>
<tr><td><b>Research-ready</b></td><td>Batch trajectory generation, trajectory compression for training the next generation of tool-calling models.</td></tr>
</table>

---

## Multi-Agent Edition

This branch extends Hermes Agent with **multi-agent orchestration**, DAG pipelines, isolated sub-agent memory, per-agent LLM configuration, and a **RuleEngine** for behaviour control. Built on [Hermes Agent by Nous Research](https://github.com/NousResearch/hermes-agent).

**New capabilities in this branch:**

- **DAG orchestration** — chain agents in pipelines (e.g. `coder → reviewer`) with parallel execution
- **Adaptive routing** — ultra-cheap classifier decides `SIMPLE` (1 API call) vs `COMPLEX` (full delegation)
- **Horizontal isolation** — agents cannot call siblings, only the Orchestrator and their own descendants
- **Subtree memory** — each agent branch has isolated conversation history (`subtree_session_id`)
- **Long-term vector memory** — ChromaDB backend with semantic search, auto-summarization, and subtree isolation
- **RuleEngine** — `critical_rules` with violation detection, self-correction loops, and per-agent config
- **Per-agent LLM config** — each agent on its own provider/model with inheritance, fallback, and auto-select
- **Auto-propagation** — `hermes tools enable` automatically propagates toolsets to all existing agents
- **Full Clone agents** — new sub-agents get ALL tools by default (complete Hermes capability)
- **Code Workflow** — automated coder→tester pipeline with sandboxed test execution and auto-generated pytest suites
- **Parallel workflows** — run multiple code tasks simultaneously, each with isolated coder+tester agents

---

## Quick Start

### Fresh Install (upstream)

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
```

### Windows (native, PowerShell)

> **Heads up:** Native Windows runs Hermes without WSL — CLI, gateway, TUI, and tools all work natively. If you'd rather use WSL2, the Linux/macOS one-liner above works there too. Found a bug? Please [file issues](https://github.com/NousResearch/hermes-agent/issues).

Run this in PowerShell:

```powershell
iex (irm https://hermes-agent.nousresearch.com/install.ps1)
```

The installer handles everything: uv, Python 3.11, Node.js, ripgrep, ffmpeg, **and a portable Git Bash** (MinGit, unpacked to `%LOCALAPPDATA%\hermes\git` — no admin required, completely isolated from any system Git install). Hermes uses this bundled Git Bash to run shell commands.

If you already have Git installed, the installer detects it and uses that instead. Otherwise a ~45MB MinGit download is all you need — it won't touch or interfere with any system Git.

> **Android / Termux:** The tested manual path is documented in the [Termux guide](https://hermes-agent.nousresearch.com/docs/getting-started/termux). On Termux, Hermes installs a curated `.[termux]` extra because the full `.[all]` extra currently pulls Android-incompatible voice dependencies.
>
> **Windows:** Native Windows is fully supported — the PowerShell one-liner above installs everything. If you'd rather use WSL2, the Linux command works there too. Native Windows install lives under `%LOCALAPPDATA%\hermes`; WSL2 installs under `~/.hermes` as on Linux.

### Multi-Agent Branch Setup

```bash
# 1. Pull the latest changes
cd ~/.hermes/hermes-agent
git pull origin multi-agent
pip install -e .

# 2. Enable multi-agent toolsets (auto-propagates to all sub-agents)
hermes tools enable delegation messaging

# 3. (Optional) Enable long-term vector memory
pip install chromadb

# 4. Apply migrations to existing agent configs (if any)
hermes update

# 5. Launch Hermes
hermes
```

You're ready to go:

```
/subagents tree                                    # view the agent hierarchy
/subagents create myclone "You are a Python expert"  # create a full clone
/agent myclone write a sorting function            # call a sub-agent
/orchestrate research sorting algorithms           # auto-delegate
/agents                                            # list all agents with stats
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

## Multi-Agent Features

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

### Long-Term Vector Memory
- **ChromaDB backend** — persistent vector storage that survives restarts
- **Multi-level architecture** — Short-term (conversation) → Subtree (branch) → Long-term (vectors) → Global (rules)
- **Auto-summarization** — every 10 exchanges, key insights are extracted and stored
- **Semantic retrieval** — before each agent call, relevant past memories are injected into context
- **Subtree isolation** — each branch only retrieves its own memories + global rules
- **Critical rules persistence** — `critical_rules` automatically stored with maximum importance
- **Graceful degradation** — fully functional without ChromaDB; install `pip install chromadb` to enable
- **CLI access** — `/memory search`, `/memory summarize`, `/memory insights`

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
- **Telegram API 10.1** — native tables, slideshows for long responses, LaTeX rendering
  - Config: `telegram.use_api_10_markup: true`

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
                ├── LongTermMemory (ChromaDB)
                │    ├── add_memory / search / get_insights
                │    ├── auto-summarize every 10 turns
                │    └── subtree-isolated + global rules
                │
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
                ├── CodeGenerationWorkflow
                │    ├── classify_code_task() — 55 patterns, EN+RU
                │    ├── CoderAgent → write_code / fix_code
                │    ├── TesterAgent → review_code + CodeRunner
                │    │    ├── Sandboxed execution (subprocess, 15s)
                │    │    ├── Auto-generate 5-8 pytest tests
                │    │    ├── mypy type checking
                │    │    └── Security scan (13 patterns)
                │    ├── Stop: 2 confident passes or 1 at ≥95%
                │    └── Parallel: multiple workflows simultaneously
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
hermes setup --portal
```

That logs you in via OAuth, sets Nous as your provider, and turns on the Tool Gateway. Check what's wired up any time with `hermes portal info`. Full details on the [Tool Gateway docs page](https://hermes-agent.nousresearch.com/docs/user-guide/features/tool-gateway).

You can still bring your own keys per-tool whenever you want — the gateway is per-backend, not all-or-nothing.

### Multi-Agent Branch Install

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

## CLI vs Messaging Quick Reference

Hermes has two entry points: start the terminal UI with `hermes`, or run the gateway and talk to it from Telegram, Discord, Slack, WhatsApp, Signal, or Email. Once you're in a conversation, many slash commands are shared across both interfaces.

| Action                         | CLI                                           | Messaging platforms                                                              |
| ------------------------------ | --------------------------------------------- | -------------------------------------------------------------------------------- |
| Start chatting                 | `hermes`                                      | Run `hermes gateway setup` + `hermes gateway start`, then send the bot a message |
| Start fresh conversation       | `/new` or `/reset`                            | `/new` or `/reset`                                                               |
| Change model                   | `/model [provider:model]`                     | `/model [provider:model]`                                                        |
| Set a personality              | `/personality [name]`                         | `/personality [name]`                                                            |
| Retry or undo the last turn    | `/retry`, `/undo`                             | `/retry`, `/undo`                                                                |
| Compress context / check usage | `/compress`, `/usage`, `/insights [--days N]` | `/compress`, `/usage`, `/insights [days]`                                        |
| Browse skills                  | `/skills` or `/<skill-name>`                  | `/<skill-name>`                                                                  |
| Interrupt current work         | `Ctrl+C` or send a new message                | `/stop` or send a new message                                                    |
| Platform-specific status       | `/platforms`                                  | `/status`, `/sethome`                                                            |

For the full command lists, see the [CLI guide](https://hermes-agent.nousresearch.com/docs/user-guide/cli) and the [Messaging Gateway guide](https://hermes-agent.nousresearch.com/docs/user-guide/messaging).

---

## Documentation

All documentation lives at **[hermes-agent.nousresearch.com/docs](https://hermes-agent.nousresearch.com/docs/)**:

| Section                                                                                             | What's Covered                                             |
| --------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| [Quickstart](https://hermes-agent.nousresearch.com/docs/getting-started/quickstart)                 | Install → setup → first conversation in 2 minutes          |
| [CLI Usage](https://hermes-agent.nousresearch.com/docs/user-guide/cli)                              | Commands, keybindings, personalities, sessions             |
| [Configuration](https://hermes-agent.nousresearch.com/docs/user-guide/configuration)                | Config file, providers, models, all options                |
| [Messaging Gateway](https://hermes-agent.nousresearch.com/docs/user-guide/messaging)                | Telegram, Discord, Slack, WhatsApp, Signal, Home Assistant |
| [Security](https://hermes-agent.nousresearch.com/docs/user-guide/security)                          | Command approval, DM pairing, container isolation          |
| [Tools & Toolsets](https://hermes-agent.nousresearch.com/docs/user-guide/features/tools)            | 40+ tools, toolset system, terminal backends               |
| [Skills System](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)              | Procedural memory, Skills Hub, creating skills             |
| [Memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory)                     | Persistent memory, user profiles, best practices           |
| [MCP Integration](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)               | Connect any MCP server for extended capabilities           |
| [Cron Scheduling](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron)              | Scheduled tasks with platform delivery                     |
| [Context Files](https://hermes-agent.nousresearch.com/docs/user-guide/features/context-files)       | Project context that shapes every conversation             |
| [Architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture)             | Project structure, agent loop, key classes                 |
| [Contributing](https://hermes-agent.nousresearch.com/docs/developer-guide/contributing)             | Development setup, PR process, code style                  |
| [CLI Reference](https://hermes-agent.nousresearch.com/docs/reference/cli-commands)                  | All commands and flags                                     |
| [Environment Variables](https://hermes-agent.nousresearch.com/docs/reference/environment-variables) | Complete env var reference                                 |

---

## Migrating from OpenClaw

If you're coming from OpenClaw, Hermes can automatically import your settings, memories, skills, and API keys.

**During first-time setup:** The setup wizard (`hermes setup`) automatically detects `~/.openclaw` and offers to migrate before configuration begins.

**Anytime after install:**

```bash
hermes claw migrate              # Interactive migration (full preset)
hermes claw migrate --dry-run    # Preview what would be migrated
hermes claw migrate --preset user-data   # Migrate without secrets
hermes claw migrate --overwrite  # Overwrite existing conflicts
```

What gets imported:

- **SOUL.md** — persona file
- **Memories** — MEMORY.md and USER.md entries
- **Skills** — user-created skills → `~/.hermes/skills/openclaw-imports/`
- **Command allowlist** — approval patterns
- **Messaging settings** — platform configs, allowed users, working directory
- **API keys** — allowlisted secrets (Telegram, OpenRouter, OpenAI, Anthropic, ElevenLabs)
- **TTS assets** — workspace audio files
- **Workspace instructions** — AGENTS.md (with `--workspace-target`)

See `hermes claw migrate --help` for all options, or use the `openclaw-migration` skill for an interactive agent-guided migration with dry-run previews.

---

## Contributing

We welcome contributions! See the [Contributing Guide](https://hermes-agent.nousresearch.com/docs/developer-guide/contributing) for development setup, code style, and PR process.

Quick start for contributors — clone and go with `setup-hermes.sh`:

```bash
git clone https://github.com/NousResearch/hermes-agent.git
cd hermes-agent
```

---

## CLI Commands (Multi-Agent)

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

### Long-Term Memory

```bash
# Semantic search across past sessions
/memory search auth bug

# Force summarise current session to long-term memory
/memory summarize

# View important insights for current agent
/memory insights

# View insights for a specific agent
/memory insights coder
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

# Auto-detected code tasks launch the coder→tester pipeline
/orchestrate write a prime number checker
```

### Code Workflow

The Orchestrator can automatically detect code-related requests and launch a **coder→tester→fix** pipeline with real sandboxed execution.

```bash
/orchestrate write a function that checks if a number is prime
```

**How it works:**

1. **Classify** — `classify_code_task()` detects code requests (55 EN/RU patterns)
2. **Coder** writes production-quality code (docstrings, type hints, edge cases)
3. **Tester** runs code in a sandbox + auto-generates 5-8 pytest tests
4. **Review** — if code fails real tests, Coder fixes and resubmits
5. **Stop criteria** — 2 consecutive confident passes, or max 5 iterations

```
🚀 Code workflow starting…
✍️  Coder writing code…
🔍 Tester reviewing (real execution + analysis)…
   → pytest: 5 passed, 0 failed
   → mypy: PASS
   → security: 0 issues
✅ Code Workflow PASSED after 1 iteration
```

**Tester agent capabilities:**
- Real sandboxed execution (subprocess, 15s timeout)
- Auto-generates quality pytest tests (5-8 cases: normal, boundary, error)
- Type checking with mypy
- Security scan (13 patterns: eval, exec, secrets, injection)
- Structured output: Syntax, Tests, Types, Security, Performance, Style

### Parallel Workflows

Run multiple code tasks simultaneously — each with its own isolated coder+tester pair.

```bash
/orchestrate write a sorting function
/orchestrate create a User class with JSON serialization
/orchestrate write unit tests for the auth module
```

```bash
/workflows
  ✍️  WRITING • 1/5 • coder-a1b2 → tester-a1b2  ◀ active
  🔍 REVIEWING • 2/5 • coder-e5f6 → tester-e5f6

/workflow switch e5f6          # focus another workflow
/workflow show a1b2c3d4         # view final code + review
/workflow stop                  # stop the active workflow
/workflow delete a1b2c3d4       # delete a completed workflow
/workflow list                  # show all stored workflow history
```

### Workflow Commands

```bash
/workflows                       # list all active workflows
/workflow status                 # show current workflow progress
/workflow switch <id>            # focus a different workflow
/workflow show <id>              # view code + review + iteration history
/workflow stop                   # stop the active workflow
/workflow delete <id>            # delete workflow and all its iterations
/workflow list                   # show all stored workflow history
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

    # ── Long-Term Memory ───────────────────────
    r.summarize_to_longterm("coder")              # save session to LTM
    results = r.search_longterm_memory("auth")    # semantic search
    insights = r.get_insights("coder")            # important insights

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
├── memory/
│   └── __init__.py                    ← LongTermMemory (ChromaDB)
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
