"""
Multi-Agent Updater — безопасное обновление Hermes Multi-Agent.

Главное правило: НИКОГДА не перезаписывать существующий provider и model.

Использование:
    hermes update              # полное обновление
    hermes update --dry-run    # показать что будет изменено, без реальных правок
    hermes update --reset-llm  # принудительно сбросить LLM-настройки (ОПАСНО)
"""

import shutil
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────

DEFAULT_PROVIDER = "deepseek"

DEFAULT_MODELS: dict[str, str] = {
    "deepseek": "deepseek-v4-pro",
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
    "grok": "grok-2",
    "openrouter": "anthropic/claude-sonnet-4",
    "current": "",
}

DEFAULT_FALLBACKS: dict[str, list[str]] = {
    "deepseek": ["deepseek-chat"],
    "anthropic": ["claude-3-5-sonnet-20240620", "claude-3-opus-20240229"],
    "openai": ["gpt-4o-mini"],
    "grok": ["grok-2-mini"],
    "openrouter": [],
    "current": [],
}

DEFAULT_LLM_PARAMS = {
    "temperature": 0.7,
    "max_tokens": 8192,
    "top_p": 0.95,
    "priority": 2,
    "auto_select": "none",
    "reasoning_effort": "medium",
    "inherit_from_parent": True,
}

NEW_FIELDS = [
    "subtree_session_id",
    "level",
    "parent_id",
    "fallback_models",
    "temperature",
    "max_tokens",
    "top_p",
    "priority",
    "auto_select",
    "reasoning_effort",
    "inherit_from_parent",
    "critical_rules",
    "rule_reminder_every",
]

CORE_FILES = [
    "agent_registry.py",
    "cli.py",
]

# ── Helpers ───────────────────────────────────────────────────


def _find_hermes_root() -> Path | None:
    """Find the Hermes agent installation directory."""
    candidates = [
        Path.home() / ".hermes" / "hermes-agent",
        Path.home() / "hermes-agent",
    ]
    for c in candidates:
        if (c / "agent_registry.py").exists():
            return c
    return None


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _get_default_model(provider: str) -> str:
    return DEFAULT_MODELS.get(provider, "")


def _get_fallbacks(provider: str) -> list[str]:
    return DEFAULT_FALLBACKS.get(provider, [])


# ── Backup ────────────────────────────────────────────────────


def backup_current_installation(root: Path | None = None) -> Path:
    """Create a timestamped backup of the current installation."""
    root = root or _find_hermes_root()
    if not root:
        raise FileNotFoundError("Hermes agent installation not found")

    backup_dir = root.parent / "backups" / f"hermes-multiagent-{_timestamp()}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Back up agent_registry.py
    src = root / "agent_registry.py"
    if src.exists():
        shutil.copy2(src, backup_dir / "agent_registry.py")

    # Back up agent configs
    configs_src = root / "agent_configs"
    if configs_src.exists():
        configs_dst = backup_dir / "agent_configs"
        shutil.copytree(configs_src, configs_dst)

    logger.info(f"Backup created: {backup_dir}")
    return backup_dir


# ── Core files ────────────────────────────────────────────────


def update_core_files(root: Path | None = None, dry_run: bool = False) -> list[str]:
    """Copy updated core files from the current hermes-agent directory."""
    root = root or _find_hermes_root()
    if not root:
        raise FileNotFoundError("Hermes agent installation not found")

    updated = []
    source_dir = Path(__file__).resolve().parent  # where this script lives

    for filename in CORE_FILES:
        src = source_dir / filename
        dst = root / filename
        if src.exists() and src != dst:
            if not dry_run:
                shutil.copy2(src, dst)
            updated.append(filename)
            logger.info(f"{'[DRY-RUN] ' if dry_run else ''}Updated: {filename}")

    return updated


# ── Migration ─────────────────────────────────────────────────


def migrate_agent_config(
    file_path: Path,
    dry_run: bool = False,
    reset_llm: bool = False,
) -> dict[str, Any]:
    """Migrate a single agent YAML config using the versioned migration system.

    Delegates to ``AgentRegistry.migrate_agent_config()`` which applies
    only pending migrations (idempotent, safe to run repeatedly).

    Returns a report dict with changes made.
    """
    from agent_registry import AgentRegistry, MIGRATIONS

    agent_id = file_path.stem
    report: dict[str, Any] = {
        "file": str(file_path),
        "agent_id": agent_id,
        "added": [],
        "preserved": [],
        "error": None,
    }

    try:
        # Load the config so we can report what was preserved
        with open(file_path) as f:
            config = yaml.safe_load(f) or {}

        if "provider" in config:
            report["preserved"].append(f"provider={config['provider']}")
        if "model" in config and config["model"]:
            report["preserved"].append(f"model={config['model']}")

        if reset_llm:
            config["provider"] = DEFAULT_PROVIDER
            config["model"] = _get_default_model(DEFAULT_PROVIDER)
            config.update(DEFAULT_LLM_PARAMS)
            config["fallback_models"] = _get_fallbacks(DEFAULT_PROVIDER)
            report["added"].append("LLM settings RESET to defaults")

        # ── Delegate to the versioned migration system ──────────
        registry = AgentRegistry(config_dir=file_path.parent)
        registry.register(agent_id, config)
        mig_report = registry.migrate_agent_config(agent_id, dry_run=dry_run)

        if mig_report.get("error"):
            report["error"] = mig_report["error"]
        else:
            for mig_id in mig_report.get("migrations_applied", []):
                # Find the human-readable description
                desc = mig_id
                for m in MIGRATIONS:
                    if m["id"] == mig_id:
                        desc = f"{mig_id}: {m['description']}"
                        break
                report["added"].append(desc)

            for mig_id in mig_report.get("already_applied", []):
                report["preserved"].append(f"migration: {mig_id}")

    except Exception as e:
        report["error"] = str(e)
        logger.error(f"Migration failed for {file_path}: {e}")

    return report


def migrate_all_agent_configs(
    root: Path | None = None,
    dry_run: bool = False,
    reset_llm: bool = False,
) -> list[dict[str, Any]]:
    """Migrate all YAML configs in agent_configs/ directory."""
    root = root or _find_hermes_root()
    if not root:
        raise FileNotFoundError("Hermes agent installation not found")

    configs_dir = root / "agent_configs"
    if not configs_dir.exists():
        logger.warning(f"No agent_configs directory at {configs_dir}")
        return []

    reports = []
    for yaml_file in sorted(configs_dir.glob("*.yaml")):
        logger.info(f"Migrating: {yaml_file.name}")
        report = migrate_agent_config(yaml_file, dry_run=dry_run, reset_llm=reset_llm)
        reports.append(report)

    return reports


def migrate_subtree_sessions(root: Path | None = None, dry_run: bool = False) -> int:
    """Ensure all agents have valid subtree_session_id.

    Returns count of agents updated.
    """
    root = root or _find_hermes_root()
    if not root:
        return 0

    configs_dir = root / "agent_configs"
    if not configs_dir.exists():
        return 0

    count = 0
    for yaml_file in sorted(configs_dir.glob("*.yaml")):
        try:
            with open(yaml_file) as f:
                config = yaml.safe_load(f) or {}

            if "subtree_session_id" not in config:
                import uuid
                agent_id = config.get("agent_id", yaml_file.stem)
                config["subtree_session_id"] = f"subtree-{agent_id}-{uuid.uuid4().hex[:8]}"
                if not dry_run:
                    with open(yaml_file, "w") as f:
                        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
                count += 1
        except Exception as e:
            logger.warning(f"subtree migration failed for {yaml_file}: {e}")

    return count


# ── Report ────────────────────────────────────────────────────


def show_migration_report(reports: list[dict[str, Any]], dry_run: bool = False) -> str:
    """Format a human-readable migration report."""
    lines = []
    mode = "[DRY-RUN] " if dry_run else ""

    total_added = 0
    preserved_providers = 0
    errors = 0

    for r in reports:
        if r.get("error"):
            lines.append(f"  ❌ {Path(r['file']).name}: {r['error']}")
            errors += 1
        else:
            agent_id = r.get("agent_id", "?")
            added = r.get("added", [])
            preserved = r.get("preserved", [])
            total_added += len(added)

            if any("provider=" in p for p in preserved):
                preserved_providers += 1

            if added:
                lines.append(f"  ✅ {agent_id}: +{len(added)} fields")
                for a in added:
                    lines.append(f"      + {a}")
            else:
                lines.append(f"  ✓  {agent_id}: already up-to-date")

    summary = [
        f"\n{mode}Migration report:",
        f"  Files processed: {len(reports)}",
        f"  Fields added:    {total_added}",
        f"  Providers preserved: {preserved_providers}",
        f"  Errors:          {errors}",
    ]

    return "\n".join(lines + [""] + summary)


# ── Entry Point ───────────────────────────────────────────────


def run_update(
    dry_run: bool = False,
    reset_llm: bool = False,
    root: Path | None = None,
) -> str:
    """Run the full update process. Returns a report string."""
    root = root or _find_hermes_root()
    if not root:
        return "❌ Hermes agent installation not found."

    lines = ["\n🔄 Hermes Multi-Agent Updater", f"   Root: {root}"]

    if dry_run:
        lines.append("   Mode: DRY-RUN (no changes will be made)")
    if reset_llm:
        lines.append("   ⚠️  LLM settings will be RESET to defaults")

    lines.append("")

    # 1. Backup
    try:
        backup_path = backup_current_installation(root)
        lines.append(f"📦 Backup: {backup_path}")
    except Exception as e:
        lines.append(f"⚠️  Backup failed: {e}")

    # 2. Update core files
    updated = update_core_files(root, dry_run=dry_run)
    if updated:
        lines.append(f"📄 Core files: {', '.join(updated)}")

    # 3. Migrate agent configs
    reports = migrate_all_agent_configs(root, dry_run=dry_run, reset_llm=reset_llm)
    lines.append(show_migration_report(reports, dry_run=dry_run))

    # 4. Subtree sessions
    subtree_count = migrate_subtree_sessions(root, dry_run=dry_run)
    if subtree_count:
        lines.append(f"🌳 Subtree sessions: {subtree_count} agents updated")

    # 5. Success message
    if dry_run:
        lines.append("\n🔍 Dry-run complete. Run without --dry-run to apply changes.")
    else:
        lines.append(f"\n✅ Hermes Multi-Agent обновлён!")
        lines.append(f"   Выполните /agents-reload для применения изменений.")

    return "\n".join(lines)


# ── CLI Integration ───────────────────────────────────────────


def register_update_command(cli_instance) -> None:
    """Register /hermes-update command in the CLI."""
    # The command is registered in commands.py, this is a no-op
    # provided for explicit integration if needed.
    pass
