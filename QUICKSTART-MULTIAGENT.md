# Quick Start: 5 Minutes to Your First Agent

Hermes Multi-Agent lets you create autonomous AI sub-agents that work
in parallel — each with its own tools, memory, and LLM config.

## Prerequisites

You already have Hermes Agent installed. You need the `multi-agent`
branch from the fork:

```bash
cd ~/.hermes/hermes-agent
git remote add demoss git@github.com:demossml/hermes-agent.git 2>/dev/null
git fetch demoss multi-agent
git checkout multi-agent
pip install -e .
```

## Step 1: Enable Multi-Agent Tools (10 seconds)

```bash
hermes tools enable delegation messaging
```

This enables delegation and messaging globally AND auto-propagates them
to all existing sub-agents. You only need to do this once.

## Step 2: (Optional) Enable Long-Term Memory (30 seconds)

```bash
pip install chromadb
```

Without ChromaDB everything still works — agents just won't remember
across sessions. With it, they get persistent vector memory.

## Step 3: Create Your First Agent (30 seconds)

Inside a Hermes session:

```
/subagents create bot-1 "You are a helpful coding assistant. Write clean Python code with type hints and docstrings."

/subagents create researcher "You are a research assistant. Search the web and summarise findings concisely."
```

## Step 4: Use Your Agents (10 seconds)

```
/agent bot-1 write a function to sort a list of dictionaries by a key

/agent researcher what's new in Python 3.13

/orchestrate research sorting algorithms and write a benchmark
```

## Step 5: Check Everything Works (10 seconds)

```
/multiagent-doctor
/agents
/subagents tree
/memory insights
```

## That's It

You now have a team of AI agents working for you. Create as many as you
need — each one is a full Hermes clone with access to all tools.

## Key Commands

| Command | What it does |
|---------|-------------|
| `/subagents create <id> "<prompt>"` | Create a new sub-agent (full clone by default) |
| `/subagents create <id> "..." --tools terminal,file` | Create with restricted tools |
| `/agent <id> <task>` | Send a task to a sub-agent |
| `/orchestrate <task>` | Auto-delegate to the best agent |
| `/subagents tree` | View the agent hierarchy |
| `/agents` | List all agents with stats |
| `/memory search <query>` | Search long-term memory |
| `/memory summarize` | Save current session to long-term memory |
| `/multiagent-doctor` | Health check |
| `/agent-off` | Return to main agent |

## Agent Config Files

Agents are stored as YAML in `agent_configs/`. Edit them directly to
change LLM provider, model, temperature, tools, and rules:

```yaml
# agent_configs/bot-1.yaml
agent_id: bot-1

# ── Identity ────────────────────────────────────────────────
description: "Coding assistant bot"
level: 1                         # 0=orchestrator, 1=sub-agent, 2+=grandchild
parent_id: orchestrator          # Who created this agent
subtree_session_id: subtree-bot-1-a1b2c3d4  # Memory isolation key

# ── System Prompt ───────────────────────────────────────────
system_prompt: |
  You are a helpful coding assistant.
  Write clean Python code with type hints and docstrings.

# ── LLM Configuration ───────────────────────────────────────
provider: current                # "current" = use Hermes' active provider
model: claude-sonnet-4-20250514  # Model name (ignored when provider="current")
temperature: 0.7                 # Creativity level (0.0-2.0)
max_tokens: 8192                 # Maximum response length
top_p: 0.95                      # Nucleus sampling
fallback_models: []              # Models to try on failure
priority: 2                      # 1=critical, 2=normal, 3=low
auto_select: none                # cheapest|fastest|balanced|none
reasoning_effort: medium         # low|medium|high
inherit_from_parent: true        # Inherit missing fields from parent

# ── Tools ───────────────────────────────────────────────────
enabled_toolsets:                # null = ALL tools (full clone)
  - delegation                   # Can create sub-agents
  - messaging                    # Can send messages across platforms
  - terminal                     # Shell access
  - file                         # Read/write files
  - web_search                   # Internet search
  - skills                       # Skill management
  - browser                      # Browser automation

# ── Behaviour ───────────────────────────────────────────────
max_context_tokens: 8000         # Max tokens for conversation history
max_iterations: 5                # Max tool-calling iterations per turn

# ── RuleEngine ───────────────────────────────────────────────
critical_rules:                  # Rules the agent MUST follow (enforced)
  - "Never write code without explicit request"
  - "Always add docstrings to functions"
rule_reminder_every: 3           # Remind agent of rules every N messages (0=off)

# ── Migration tracking (auto-managed) ───────────────────────
applied_migrations:              # Versioned migrations already applied
  - 20260609_add_subtree_session
  - 20260610_add_enabled_toolsets
  - 20260611_add_critical_rules
  - 20260612_add_provider_fallback
  - 20260613_upgrade_agent_toolsets
migration_version: "20260613"    # Current migration version
```
