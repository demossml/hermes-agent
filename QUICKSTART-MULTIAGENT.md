# Hermes Multi-Agent — Quick Start & Upgrade Guide

## How to upgrade to the new multi-project version

### Option 1: Fresh clone (recommended)

If you're new to Hermes Multi-Agent or want a clean start:

```bash
cd ~/.hermes/hermes-agent
git remote add demoss git@github.com:demossml/hermes-agent.git 2>/dev/null || true
git fetch demoss multi-agent
git checkout multi-agent
git pull demoss multi-agent
pip install -e .
hermes tools enable delegation messaging
```

### Option 2: Upgrade existing installation

If you already have Hermes Multi-Agent installed:

```bash
cd ~/.hermes/hermes-agent

# 1. Pull latest changes
git pull origin multi-agent

# 2. The updater auto-detects the multi-agent branch and runs:
#    - Agent config migration (all YAML files)
#    - Projects system initialisation
#    - DuckDB project_id column migration
#    - ChromaDB per-project collections
#    - Subtree session migration
#    - Profile database migration

# 3. Or run it manually:
hermes update --multiagent

# 4. Migrate existing clones to Projects (optional):
hermes update --migrate --dry-run   # preview what will be migrated
hermes update --migrate              # run the migration
```

### Option 3: In-CLI update (runtime)

From inside an active Hermes session:

```
/hermes-update           # basic multi-agent update (configs + subtree)
/hermes-update --full    # full update including Projects system
/hermes-update --dry-run # preview without changes
/hermes-update --migrate # migrate existing clones → Projects
```

### What the updater does (all phases are idempotent)

| Phase | What |
|-------|------|
| 📦 Backup | `~/.hermes/backups/hermes-multiagent-{timestamp}/` |
| 📄 Core files | Updates `agent_registry.py`, `cli.py` |
| 📁 Projects | Creates `~/.hermes/projects/` structure |
| 🗄️ DuckDB | Adds `project_id` column to `chat_history.duckdb` + `workflows.duckdb` |
| 🧠 ChromaDB | Creates `project_{id}` collections for each project |
| 📝 Prompts | Updates system prompt with `PROJECT_GUIDANCE` |
| ⚙️ Agent configs | Migrates all `agent_configs/*.yaml` to latest version |
| 🌳 Subtree sessions | Ensures `subtree_session_id` on all agents |
| 📦 Migration | Converts `~/.hermes/profiles/<clone>/` → Projects |

### After upgrade: first steps

```
/project new "My Project"          # create a project
/project list                       # list all projects
/project switch my-project          # switch active project
/reset                              # load project context

/subagents create coder "You write code."   # create sub-agent in project
```

### Quick Start: 5 Minutes to Your First Agent

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

Long-term vector memory allows agents to remember important information
across sessions. Each project gets its own isolated ChromaDB collection.

## Step 3: Create and Use Agents

### In CLI:
```
/orchestrate build a REST API for user management
/agent coder write a function to sort arrays
/subagents create reviewer "You review code for bugs and style"
/subagents tree
/agents
```

### Direct delegation:
The orchestrator can spawn sub-agents automatically. Sub-agents have
their own isolated memory, tools, and LLM configs.

## Project System

```
/project new "My App"              # create isolated workspace
/project switch my-app             # switch to project
/project list                      # list all projects with status
/project rename old new            # rename project
/project delete my-app             # delete with confirmation

/search auth bug                   # search current project
/search refactor --all             # search all projects
/global search deployment          # force global search
```

Projects provide:
- Isolated memory (subtree_session_id, ChromaDB, DuckDB)
- Per-project sub-agents with automatic project binding
- Workflow pause/resume on project switch
- Cross-project search
- Status bar integration: `[Project: My App]`
