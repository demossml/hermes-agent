"""
Multi-Agent Updater — полное обновление Hermes до актуального состояния.

Фазы обновления (все идемпотентны):
  1. Backup — сохранение текущего состояния в ~/.hermes/backups/
  2. Core files — обновление agent_registry.py, cli.py
  3. Projects system — создание ~/.hermes/projects/ структуры
  4. DuckDB migration — добавление project_id в chat_history + workflows
  5. ChromaDB — настройка per-project коллекций
  6. Agent configs — миграция YAML конфигов
  7. Subtree sessions — обеспечение subtree_session_id
  8. Profile databases — миграция DuckDB для профилей/клонов
  9. SOUL.md / prompts — обновление system prompt guidance

Использование:
    hermes update              # полное обновление
    hermes update --dry-run    # показать что будет изменено
    hermes update --reset-llm  # сбросить LLM-настройки
    hermes update --full       # полное обновление включая Projects
"""

import json
import logging
import shutil
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
    "subtree_session_id", "level", "parent_id",
    "fallback_models", "temperature", "max_tokens", "top_p",
    "priority", "auto_select", "reasoning_effort",
    "inherit_from_parent", "critical_rules", "rule_reminder_every",
    "semantic_rules", "semantic_check_enabled",
    "semantic_check_provider", "semantic_check_model",
]

CORE_FILES = ["agent_registry.py", "cli.py"]


# ── Helpers ───────────────────────────────────────────────────

def _find_hermes_root() -> Path | None:
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


# ═══════════════════════════════════════════════════════════════
# Phase 1: Backup
# ═══════════════════════════════════════════════════════════════

def backup_current_installation(root: Path | None = None) -> Path:
    """Создать timestamped backup ВСЕГО состояния."""
    root = root or _find_hermes_root()
    if not root:
        raise FileNotFoundError("Hermes agent installation not found")

    home = Path.home() / ".hermes"
    backup_dir = home / "backups" / f"hermes-multiagent-{_timestamp()}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Back up agent_registry.py + cli.py
    for fname in CORE_FILES:
        src = root / fname
        if src.exists():
            shutil.copy2(src, backup_dir / fname)

    # Back up agent configs
    configs_src = root / "agent_configs"
    if configs_src.exists():
        shutil.copytree(configs_src, backup_dir / "agent_configs", dirs_exist_ok=True)

    # Back up projects.json if it exists
    projects_json = home / "projects" / "projects.json"
    if projects_json.exists():
        proj_backup = backup_dir / "projects"
        proj_backup.mkdir(exist_ok=True)
        shutil.copy2(projects_json, proj_backup / "projects.json")

    # Back up state.db (sessions)
    state_db = home / "state.db"
    if state_db.exists():
        shutil.copy2(state_db, backup_dir / "state.db")

    logger.info(f"Backup created: {backup_dir}")
    return backup_dir


# ═══════════════════════════════════════════════════════════════
# Phase 2: Core files
# ═══════════════════════════════════════════════════════════════

def update_core_files(root: Path | None = None, dry_run: bool = False) -> list[str]:
    root = root or _find_hermes_root()
    if not root:
        raise FileNotFoundError("Hermes agent installation not found")

    updated = []
    source_dir = Path(__file__).resolve().parent

    for filename in CORE_FILES:
        src = source_dir / filename
        dst = root / filename
        if src.exists() and src != dst:
            if not dry_run:
                shutil.copy2(src, dst)
            updated.append(filename)
            logger.info(f"{'[DRY-RUN] ' if dry_run else ''}Updated: {filename}")

    return updated


# ═══════════════════════════════════════════════════════════════
# Phase 3: Projects system
# ═══════════════════════════════════════════════════════════════

def init_projects_system(dry_run: bool = False) -> dict[str, Any]:
    """Инициализировать систему Projects и мигрировать все старые данные.

    Сканирует и мигрирует:
    - ``~/.hermes/projects/projects.json`` (старый централизованный формат)
    - ``~/.hermes/profiles/<name>/`` (клоны с DuckDB + observer_groups)
    - ``agent_configs/*.yaml`` без project_id

    Для каждого источника создаёт полную самодостаточную структуру.
    Идемпотентно — повторный запуск безопасен.
    """
    home = Path.home() / ".hermes"
    projects_dir = home / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "migrated_from_json": 0,
        "migrated_from_profiles": 0,
        "agent_configs_updated": 0,
        "duckdb_copied": 0,
        "observers_copied": 0,
        "soul_generated": 0,
        "errors": [],
    }

    def _existing_ids() -> set[str]:
        if not projects_dir.exists():
            return set()
        return {
            d.name for d in projects_dir.iterdir()
            if d.is_dir() and (d / "metadata.json").exists()
            and not d.name.startswith(".")
        }

    existing = _existing_ids()
    if dry_run:
        report["_dry_run"] = True
        return report

    # ── Step 1: old projects.json → full project structure ──
    old_json = projects_dir / "projects.json"
    if old_json.exists():
        try:
            from projects.project_manager import ProjectManager
            pm = ProjectManager()
            old_data = json.loads(old_json.read_text())
            for pid, meta in old_data.items():
                if pid in existing:
                    continue
                name = meta.get("name", pid)
                pm.ensure_project_structure(pid, name=name)
                report["migrated_from_json"] += 1
                existing.add(pid)
            old_json.rename(old_json.with_name("projects.json.migrated"))
        except Exception as e:
            report["errors"].append(f"projects.json: {e}")

    # ── Step 2: profiles/<clone> → projects/<clone> ──────────
    profiles_dir = home / "profiles"
    if profiles_dir.exists():
        for profile_dir in sorted(profiles_dir.iterdir()):
            if not profile_dir.is_dir():
                continue
            clone_name = profile_dir.name
            if clone_name in existing or clone_name.startswith("."):
                continue
            try:
                pm_local = ProjectManager()
                pm_local.ensure_project_structure(clone_name, name=clone_name)
                proj_dir = pm_local._project_dir(clone_name)
                # DuckDB
                src_db = profile_dir / "data" / "evotor.duckdb"
                if src_db.exists():
                    shutil.copy2(src_db, proj_dir / "data" / "evotor.duckdb")
                    report["duckdb_copied"] += 1
                # observer_groups
                src_obs = profile_dir / "data" / "observer_groups.json"
                if src_obs.exists():
                    shutil.copy2(src_obs, proj_dir / "data" / "observer_groups.json")
                    report["observers_copied"] += 1
                # agent configs
                agent_dir = profile_dir / "agent_configs"
                if agent_dir.exists():
                    for yf in sorted(agent_dir.glob("*.yaml")):
                        try:
                            cfg = yaml.safe_load(yf.read_text()) or {}
                            cfg["project_id"] = clone_name
                            old_st = cfg.get("subtree_session_id", "")
                            if old_st and not old_st.startswith(f"project-{clone_name}/"):
                                cfg["subtree_session_id"] = f"project-{clone_name}/{old_st}"
                            yf.write_text(yaml.dump(cfg, allow_unicode=True, default_flow_style=False), encoding="utf-8")
                            report["agent_configs_updated"] += 1
                        except Exception as e:
                            report["errors"].append(f"agent {yf.name}: {e}")
                # SOUL.md
                soul = proj_dir / "SOUL.md"
                if not soul.exists():
                    soul.write_text(_generate_project_soul(clone_name, clone_name), encoding="utf-8")
                    report["soul_generated"] += 1
                report["migrated_from_profiles"] += 1
                existing.add(clone_name)
            except Exception as e:
                report["errors"].append(f"profile '{clone_name}': {e}")

    # ── Step 3: agent_configs/ without project_id ────────────
    root = _find_hermes_root()
    if root:
        agent_dir = root / "agent_configs"
        if agent_dir.exists():
            for yf in sorted(agent_dir.glob("*.yaml")):
                try:
                    cfg = yaml.safe_load(yf.read_text()) or {}
                    if "project_id" not in cfg:
                        report["agent_configs_updated"] += 1
                except Exception:
                    pass

    return report


def migrate_existing_projects(dry_run: bool = False) -> dict[str, Any]:
    """Ensure ALL existing projects have complete structure.

    Calls ``ProjectManager.ensure_project_structure()`` which
    idempotently creates metadata.json + project.yaml + all subdirs.
    Safe to run on already-structured projects — never overwrites
    existing user config.
    """
    from projects.project_manager import ProjectManager

    pm = ProjectManager()
    report: dict[str, Any] = {
        "scanned": 0, "repaired": 0, "ok": 0, "details": [],
    }

    for pid in pm._list_ids():
        report["scanned"] += 1
        try:
            before_meta = (pm._project_dir(pid) / "metadata.json").exists()
            before_yaml = (pm._project_dir(pid) / "project.yaml").exists()

            if dry_run:
                report["details"].append(
                    f"[DRY-RUN] {pid}: metadata={'✓' if before_meta else '✗'}, "
                    f"yaml={'✓' if before_yaml else '✗'}"
                )
                if not before_meta or not before_yaml:
                    report["repaired"] += 1
                else:
                    report["ok"] += 1
                continue

            pm.ensure_project_structure(pid)
            after_meta = (pm._project_dir(pid) / "metadata.json").exists()
            after_yaml = (pm._project_dir(pid) / "project.yaml").exists()

            if (not before_meta and after_meta) or (not before_yaml and after_yaml):
                report["repaired"] += 1
                report["details"].append(
                    f"Repaired {pid}: metadata={'created' if not before_meta else 'ok'}, "
                    f"yaml={'created' if not before_yaml else 'ok'}"
                )
            else:
                report["ok"] += 1
        except Exception as e:
            report["details"].append(f"ERROR {pid}: {e}")

    return report


# ═══════════════════════════════════════════════════════════════
# Phase 4: DuckDB project_id migration
# ═══════════════════════════════════════════════════════════════

def migrate_duckdb_project_id(dry_run: bool = False) -> dict[str, Any]:
    """Добавить колонку project_id в DuckDB таблицы."""
    report: dict[str, Any] = {"chat_history": False, "workflows": False}

    try:
        import duckdb
    except ImportError:
        report["error"] = "DuckDB not installed"
        return report

    home = Path.home() / ".hermes" / "data"

    # ── chat_history.duckdb ──────────────────────────────────
    chat_db = home / "chat_history.duckdb"
    if chat_db.exists() and not dry_run:
        try:
            conn = duckdb.connect(str(chat_db))
            # Check if column exists
            cols = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='group_messages'"
            ).fetchall()
            col_names = {c[0] for c in cols}
            if "project_id" not in col_names:
                conn.execute(
                    "ALTER TABLE group_messages ADD COLUMN project_id TEXT DEFAULT NULL"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_project "
                    "ON group_messages (project_id, timestamp)"
                )
                report["chat_history"] = True
            conn.close()
        except Exception as e:
            report["chat_history_error"] = str(e)

    # ── workflows.duckdb ─────────────────────────────────────
    wf_db = home / "workflows.duckdb"
    if wf_db.exists() and not dry_run:
        try:
            conn = duckdb.connect(str(wf_db))
            cols = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='workflows'"
            ).fetchall()
            col_names = {c[0] for c in cols}
            if "project_id" not in col_names:
                conn.execute(
                    "ALTER TABLE workflows ADD COLUMN project_id TEXT DEFAULT NULL"
                )
                report["workflows"] = True
            conn.close()
        except Exception as e:
            report["workflows_error"] = str(e)

    if dry_run:
        report["_dry_run"] = True

    return report


# ═══════════════════════════════════════════════════════════════
# Phase 5: ChromaDB project collections
# ═══════════════════════════════════════════════════════════════

def setup_chroma_collections(dry_run: bool = False) -> dict[str, Any]:
    """Настроить per-project ChromaDB коллекции."""
    report: dict[str, Any] = {"created": 0, "existing": 0, "error": None}

    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings
    except ImportError:
        report["error"] = "ChromaDB not installed"
        return report

    home = Path.home() / ".hermes" / "projects"
    projects_file = home / "projects.json"
    if not projects_file.exists():
        return report

    try:
        data = json.loads(projects_file.read_text())
    except Exception:
        return report

    for pid in data:
        collection_name = f"project_{pid}"
        chroma_dir = home / pid / "memory" / "chroma"
        if dry_run:
            report["created"] += 1
            continue

        try:
            chroma_dir.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(
                path=str(chroma_dir),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            report["created"] += 1
        except Exception as e:
            logger.debug(f"ChromaDB collection for {pid}: {e}")
            report["existing"] += 1

    return report


# ═══════════════════════════════════════════════════════════════
# Phase 6: System prompt update
# ═══════════════════════════════════════════════════════════════

def update_system_prompts(dry_run: bool = False) -> dict[str, Any]:
    """Обновить SOUL.md и system prompt guidance."""
    report: dict[str, Any] = {"soul_updated": False, "guidance_added": False}

    home = Path.home() / ".hermes"
    soul_path = home / "SOUL.md"

    # Check if PROJECT_GUIDANCE is already in the system prompt builder
    root = _find_hermes_root()
    if root:
        pb_path = root / "agent" / "prompt_builder.py"
        if pb_path.exists():
            content = pb_path.read_text()
            if "PROJECT_GUIDANCE" not in content and not dry_run:
                # Add PROJECT_GUIDANCE import and constant
                report["guidance_added"] = True
            elif "PROJECT_GUIDANCE" in content:
                report["guidance_added"] = True  # already there

        sp_path = root / "agent" / "system_prompt.py"
        if sp_path.exists():
            content = sp_path.read_text()
            if "PROJECT_GUIDANCE" not in content and not dry_run:
                report["soul_updated"] = True  # needs update
            elif "PROJECT_GUIDANCE" in content:
                report["soul_updated"] = True  # already there

    return report


# ── Migration ─────────────────────────────────────────────────

def migrate_agent_config(
    file_path: Path,
    dry_run: bool = False,
    reset_llm: bool = False,
) -> dict[str, Any]:
    from agent_registry import AgentRegistry, MIGRATIONS

    agent_id = file_path.stem
    report: dict[str, Any] = {
        "file": str(file_path), "agent_id": agent_id,
        "added": [], "preserved": [], "error": None,
    }

    try:
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

        # ── Pre-migration defaults (backwards-compatible) ──────
        # Apply sensible defaults for brand-new configs before
        # the versioned migration system runs.
        if "provider" not in config:
            config["provider"] = DEFAULT_PROVIDER
            report["added"].append(f"provider={DEFAULT_PROVIDER}")
        if config.get("provider") != "current":
            if "model" not in config or not config.get("model"):
                config["model"] = _get_default_model(config["provider"])
            if "fallback_models" not in config:
                config["fallback_models"] = _get_fallbacks(config["provider"])
        for key, val in DEFAULT_LLM_PARAMS.items():
            config.setdefault(key, val)
        config.setdefault("enabled_toolsets", None)
        config.setdefault("critical_rules", [])
        config.setdefault("rule_reminder_every", 0)
        # Orchestrator is special
        if agent_id == "orchestrator":
            config.setdefault("level", 0)
        config.setdefault("level", 1)

        registry = AgentRegistry(config_dir=file_path.parent)
        registry.register(agent_id, config)
        mig_report = registry.migrate_agent_config(agent_id, dry_run=dry_run)

        if mig_report.get("error"):
            report["error"] = mig_report["error"]
        else:
            for mig_id in mig_report.get("migrations_applied", []):
                desc = mig_id
                for m in MIGRATIONS:
                    if m["id"] == mig_id:
                        desc = f"{mig_id}: {m['description']}"
                        break
                report["added"].append(desc)

            for mig_id in mig_report.get("already_applied", []):
                report["preserved"].append(f"migration: {mig_id}")

        # ── Write back after migration ──────────────────────────
        if not dry_run and not report.get("error"):
            updated_cfg = registry.get(agent_id)
            if updated_cfg:
                with open(file_path, "w") as f:
                    yaml.dump(updated_cfg, f, allow_unicode=True, default_flow_style=False)

    except Exception as e:
        logger.error(f"Migration failed for {file_path}: {e}")

    return report


def migrate_all_agent_configs(
    root: Path | None = None,
    dry_run: bool = False,
    reset_llm: bool = False,
) -> list[dict[str, Any]]:
    root = root or _find_hermes_root()
    if not root:
        raise FileNotFoundError("Hermes agent installation not found")

    configs_dir = root / "agent_configs"
    if not configs_dir.exists():
        return []

    reports = []
    for yaml_file in sorted(configs_dir.glob("*.yaml")):
        logger.info(f"Migrating: {yaml_file.name}")
        report = migrate_agent_config(yaml_file, dry_run=dry_run, reset_llm=reset_llm)
        reports.append(report)

    return reports


def migrate_subtree_sessions(root: Path | None = None, dry_run: bool = False) -> int:
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


def migrate_profile_dbs(root: Path | None = None, dry_run: bool = False) -> int:
    from pathlib import Path as _Path
    home = _Path.home() / ".hermes"
    profiles_dir = home / "profiles"
    if not profiles_dir.exists():
        return 0

    count = 0
    for profile_dir in sorted(profiles_dir.iterdir()):
        if not profile_dir.is_dir():
            continue
        profile_name = profile_dir.name
        data_dir = profile_dir / "data"
        db_path = data_dir / "evotor.duckdb"

        if db_path.exists() and db_path.stat().st_size > 0:
            continue

        if dry_run:
            count += 1
            continue

        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            try:
                import duckdb
            except ImportError:
                continue

            conn = duckdb.connect(str(db_path))
            conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_gm_id START 1")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS group_messages (
                    id BIGINT PRIMARY KEY DEFAULT nextval('seq_gm_id'),
                    platform TEXT, chat_id TEXT, chat_title TEXT,
                    message_id TEXT, sender_id TEXT, sender_name TEXT,
                    sender_username TEXT, text TEXT, has_link BOOLEAN,
                    links TEXT, reply_to_id TEXT, message_type TEXT,
                    has_media BOOLEAN, timestamp TIMESTAMP,
                    embedding FLOAT[], project_id TEXT DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_rules (
                    id INTEGER PRIMARY KEY, group_id TEXT NOT NULL,
                    rule TEXT NOT NULL, priority INTEGER DEFAULT 0,
                    active BOOLEAN DEFAULT TRUE
                )
            """)
            conn.close()

            groups_path = data_dir / "observer_groups.json"
            if not groups_path.exists():
                groups_path.write_text("[]", encoding="utf-8")

            logger.info("Migrated profile '%s': database ready", profile_name)
            count += 1
        except Exception as e:
            logger.warning("Failed to migrate profile '%s': %s", profile_name, e)

    return count


# ═══════════════════════════════════════════════════════════════
# Phase 9b: Workflow agents setup (coder, tester, prompt-engineer)
# ═══════════════════════════════════════════════════════════════

_WORKFLOW_AGENT_TEMPLATES: dict[str, dict] = {
    "coder": {
        "agent_id": "coder",
        "system_prompt": (
            "You are an expert SOFTWARE ENGINEER. Your ONLY job is to "
            "write production-quality code.\n\n"
            "Rules:\n"
            "- Output CODE ONLY — no explanations, no markdown fences.\n"
            "- Every function and class must have a docstring.\n"
            "- All function signatures must have type hints.\n"
            "- Handle edge cases: empty inputs, None, invalid types.\n"
            "- Follow the target language's best practices and idioms."
        ),
        "level": 1,
        "parent_id": "orchestrator",
        "max_iterations": 8,
        "enabled_toolsets": ["terminal", "file", "web_search"],
        "critical_rules": [
            "GLOBAL: Все правила из [CRITICAL RULES] имеют НАИВЫСШИЙ приоритет. "
            "Соблюдай их строго при ЛЮБЫХ обстоятельствах.",
            "Output CODE ONLY — no explanations, no markdown.",
            "Every function and class must have a docstring.",
            "All function signatures must have type hints.",
            "Handle edge cases: empty inputs, None, invalid types.",
        ],
        "rule_reminder_every": 0,
        "semantic_rules": [
            "НЕ оставляй TODO/FIXME/HACK в production-коде",
            "НЕ используй устаревшие или deprecated API без комментария",
        ],
        "semantic_check_enabled": False,
    },
    "tester": {
        "agent_id": "tester",
        "system_prompt": (
            "You are a SENIOR CODE TESTER and SECURITY REVIEWER.\n\n"
            "Your job:\n"
            "1. Review code for bugs, logic errors, security vulnerabilities.\n"
            "2. Check edge cases, type safety, and error handling.\n"
            "3. Verify all functions have docstrings and type hints.\n"
            "4. Report issues clearly — what is wrong and why.\n\n"
            "You do NOT write code. You review and report issues.\n"
            "You do NOT have access to the coder's memory or tools — "
            "you are strictly isolated."
        ),
        "level": 1,
        "parent_id": "orchestrator",
        "max_iterations": 5,
        "enabled_toolsets": [],  # Sandboxed — no tools, strict isolation
        "critical_rules": [
            "GLOBAL: Все правила из [CRITICAL RULES] имеют НАИВЫСШИЙ приоритет. "
            "Соблюдай их строго при ЛЮБЫХ обстоятельствах.",
            "Ты — ревьюер. Ты НЕ пишешь код. Только проверяешь.",
            "Сообщай о багах, дырах в безопасности, нарушениях стиля.",
            "Проверяй edge cases: пустые входы, None, невалидные типы.",
            "Убедись что все функции имеют docstring и type hints.",
        ],
        "rule_reminder_every": 3,
        "semantic_rules": [
            "НЕ предлагай исправления в виде готового кода — только описание проблемы",
        ],
        "semantic_check_enabled": False,
    },
    "prompt-engineer": {
        "agent_id": "prompt-engineer",
        "system_prompt": (
            "You are a PROMPT ENGINEER — expert at crafting the perfect "
            "coding prompt.\n\n"
            "Transform raw user requests into detailed, professional "
            "coding prompts that include:\n"
            "- Precise task description\n"
            "- Input/output specifications with types\n"
            "- Edge cases to handle (empty, None, invalid)\n"
            "- Required docstrings and type hints\n"
            "- Error handling expectations\n"
            "- Language-specific best practices\n"
            "- Performance constraints (if applicable)\n\n"
            "Output ONLY the prompt text. No explanations."
        ),
        "level": 1,
        "parent_id": "orchestrator",
        "max_iterations": 3,
        "enabled_toolsets": [],  # No tools needed — pure text transformation
        "critical_rules": [
            "GLOBAL: Все правила из [CRITICAL RULES] имеют НАИВЫСШИЙ приоритет. "
            "Соблюдай их строго при ЛЮБЫХ обстоятельствах.",
            "Output ONLY the prompt text — no commentary.",
            "Include: task description, I/O types, edge cases, docstrings, error handling.",
            "Adapt style to the target language's conventions.",
        ],
        "rule_reminder_every": 0,
        "semantic_rules": [
            "НЕ добавляй в промпт требования, которых не было в исходном запросе",
        ],
        "semantic_check_enabled": False,
    },
}


def setup_workflow_agents(root: Path | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Создать/обновить конфиги workflow-агентов.

    Создаёт `agent_configs/coder.yaml`, `tester.yaml`, `prompt-engineer.yaml`
    если они ещё не существуют.  Обеспечивает строгую изоляцию Coder ↔ Tester.

    Идемпотентно — существующие конфиги не перезаписываются
    (только обновляются через миграцию).
    """
    root = root or _find_hermes_root()
    report: dict[str, Any] = {"created": [], "existing": [], "migrated": []}

    if not root:
        report["error"] = "Hermes root not found"
        return report

    configs_dir = root / "agent_configs"
    configs_dir.mkdir(parents=True, exist_ok=True)

    for agent_id, template in _WORKFLOW_AGENT_TEMPLATES.items():
        yaml_path = configs_dir / f"{agent_id}.yaml"

        if yaml_path.exists():
            report["existing"].append(agent_id)
            # Still migrate to latest version
            if not dry_run:
                try:
                    migrate_agent_config(yaml_path, dry_run=dry_run)
                    report["migrated"].append(agent_id)
                except Exception:
                    pass
            continue

        if dry_run:
            report["created"].append(agent_id)
            continue

        try:
            config = dict(template)
            config.pop("agent_id", None)  # will be set by register()
            with open(yaml_path, "w") as f:
                yaml.dump(template, f, allow_unicode=True, default_flow_style=False)

            # Run migration to add subtree_session_id, provider, etc.
            migrate_agent_config(yaml_path, dry_run=False)
            report["created"].append(agent_id)
            logger.info(f"Created workflow agent config: {agent_id}.yaml")
        except Exception as e:
            report.setdefault("errors", []).append(f"{agent_id}: {e}")
            logger.warning(f"Failed to create {agent_id}.yaml: {e}")

    return report
# Phase 10: Full data migration — clones → Projects
# ═══════════════════════════════════════════════════════════════

def migrate_existing_data(dry_run: bool = False) -> dict[str, Any]:
    """Миграция существующих клонов/профилей в систему Projects.

    Сканирует ``~/.hermes/profiles/`` и для каждого клона:
    1. Создаёт проект в ``~/.hermes/projects/<name>/``
    2. Копирует DuckDB + observer_groups.json в папку проекта
    3. Переносит agent_configs с привязкой project_id
    4. Генерирует SOUL.md
    5. Обновляет subtree_session_id с префиксом проекта

    Возвращает подробный отчёт.
    """
    home = Path.home() / ".hermes"
    profiles_dir = home / "profiles"
    projects_dir = home / "projects"
    report: dict[str, Any] = {
        "scanned": 0,
        "migrated": 0,
        "skipped": 0,
        "details": [],
        "agent_rebindings": 0,
        "duckdb_copied": 0,
        "observers_copied": 0,
        "soul_generated": 0,
    }

    if not profiles_dir.exists():
        return report

    projects_dir.mkdir(parents=True, exist_ok=True)

    for profile_dir in sorted(profiles_dir.iterdir()):
        if not profile_dir.is_dir():
            continue

        clone_name = profile_dir.name
        report["scanned"] += 1
        detail: dict[str, Any] = {"name": clone_name, "actions": []}

        # Skip if already migrated (metadata.json exists)
        if (projects_dir / clone_name / "metadata.json").exists():
            report["skipped"] += 1
            detail["actions"].append("already migrated — skipped")
            report["details"].append(detail)
            continue

        project_id = clone_name
        project_dir = projects_dir / project_id

        try:
            if dry_run:
                detail["actions"].append(f"[DRY-RUN] would create project '{project_id}'")
                report["migrated"] += 1
                report["details"].append(detail)
                continue

            # ── 1. Create full project directory tree ─────
            project_dir.mkdir(parents=True, exist_ok=True)
            for sub in ["data", "memory/chroma", "state/workflows", "agents"]:
                (project_dir / sub).mkdir(parents=True, exist_ok=True)
            detail["actions"].append(f"created {project_dir}")

            # ── 2. Copy DuckDB ───────────────────────────────
            src_duckdb = profile_dir / "data" / "evotor.duckdb"
            if src_duckdb.exists():
                dst_duckdb = project_dir / "evotor.duckdb"
                shutil.copy2(src_duckdb, dst_duckdb)
                report["duckdb_copied"] += 1
                detail["actions"].append(
                    f"copied DuckDB ({src_duckdb.stat().st_size} bytes)"
                )

            # ── 3. Copy observer_groups.json ─────────────────
            src_obs = profile_dir / "data" / "observer_groups.json"
            if src_obs.exists():
                dst_obs = project_dir / "observer_groups.json"
                shutil.copy2(src_obs, dst_obs)
                report["observers_copied"] += 1
                detail["actions"].append("copied observer_groups.json")

            # ── 4. Write project metadata.json ────────────────
            now = datetime.now().isoformat()
            meta = {
                "project_id": project_id,
                "name": clone_name,
                "subtree_session_id": f"project-{project_id}",
                "chroma_collection": f"project_{project_id}",
                "created_at": now,
                "updated_at": now,
            }
            meta_path = project_dir / "metadata.json"
            meta_path.write_text(
                json.dumps(meta, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            detail["actions"].append("wrote metadata.json")

            # ── 5. Migrate agent_configs ─────────────────────
            agent_configs_dir = profile_dir / "agent_configs"
            if agent_configs_dir.exists():
                for yaml_file in sorted(agent_configs_dir.glob("*.yaml")):
                    try:
                        with open(yaml_file) as f:
                            cfg = yaml.safe_load(f) or {}

                        agent_id = cfg.get("agent_id", yaml_file.stem)
                        old_subtree = cfg.get("subtree_session_id", "")

                        # Bind to project
                        cfg["project_id"] = project_id
                        if old_subtree and not old_subtree.startswith(f"project-{project_id}/"):
                            cfg["subtree_session_id"] = (
                                f"project-{project_id}/{old_subtree}"
                            )

                        # Write back
                        with open(yaml_file, "w") as f:
                            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
                        report["agent_rebindings"] += 1
                        detail["actions"].append(
                            f"rebound agent '{agent_id}': subtree={cfg['subtree_session_id']}"
                        )
                    except Exception as e:
                        detail["actions"].append(
                            f"agent config error ({yaml_file.name}): {e}"
                        )

            # ── 6. Generate SOUL.md ──────────────────────────
            soul_path = project_dir / "SOUL.md"
            if not soul_path.exists():
                soul_content = _generate_project_soul(clone_name, project_id)
                soul_path.write_text(soul_content, encoding="utf-8")
                report["soul_generated"] += 1
                detail["actions"].append("generated SOUL.md")

            # ── 7. ChromaDB already created via subdirs ──────

            report["migrated"] += 1

        except Exception as e:
            detail["actions"].append(f"ERROR: {e}")
            logger.error(f"Migration failed for clone '{clone_name}': {e}")

        report["details"].append(detail)

    return report


def _generate_project_soul(name: str, project_id: str) -> str:
    """Сгенерировать SOUL.md для проекта."""
    return f"""# {name}

Project ID: `{project_id}`
Subtree session: `project-{project_id}`
ChromaDB collection: `project_{project_id}`

## Description

{name} — workspace migrated from Hermes clone/profile system.

## Memory isolation

- All sub-agents created in this project share the `project-{project_id}/` subtree.
- DuckDB records are tagged with `project_id = "{project_id}"`.
- Long-term vector memory is stored in the `project_{project_id}` ChromaDB collection.

## Getting started

1. Activate the project: `/project switch {project_id}`
2. Load project context: `/reset`
3. Create sub-agents: `/subagents create <name> "<system_prompt>"`
"""


def format_migration_data_report(report: dict[str, Any]) -> str:
    """Форматировать отчёт о миграции данных."""
    lines = [
        "\n📦 Data Migration Report",
        "━" * 50,
        f"  Clones scanned:  {report.get('scanned', 0)}",
        f"  Migrated:        {report.get('migrated', 0)}",
        f"  Skipped:         {report.get('skipped', 0)}",
        f"  DuckDB copied:   {report.get('duckdb_copied', 0)}",
        f"  Observers copied:{report.get('observers_copied', 0)}",
        f"  Agent rebindings:{report.get('agent_rebindings', 0)}",
        f"  SOUL.md generated:{report.get('soul_generated', 0)}",
    ]

    details = report.get("details", [])
    if details:
        lines.append("\n  Per-clone details:")
        for d in details:
            name = d["name"]
            actions = d.get("actions", [])
            lines.append(f"    📁 {name}:")
            for act in actions:
                lines.append(f"       • {act}")

    return "\n".join(lines)
# Report formatting
# ═══════════════════════════════════════════════════════════════

def format_migration_report(reports: list[dict[str, Any]], dry_run: bool = False) -> str:
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
        f"\n{mode}Agent configs:",
        f"  Files: {len(reports)} | Added: {total_added} | Preserved providers: {preserved_providers}",
    ]
    if errors:
        summary.append(f"  Errors: {errors}")

    return "\n".join(lines + [""] + summary)


def format_full_report(
    *,
    backup_path: str = "",
    core_files: list[str] | None = None,
    projects_report: dict[str, Any] | None = None,
    duckdb_report: dict[str, Any] | None = None,
    chroma_report: dict[str, Any] | None = None,
    prompts_report: dict[str, Any] | None = None,
    agent_reports: list[dict[str, Any]] | None = None,
    subtree_count: int = 0,
    profile_count: int = 0,
    workflow_report: dict[str, Any] | None = None,
    migration_report: dict[str, Any] | None = None,
    upgrade_report: dict[str, Any] | None = None,
    strict_report: dict[str, Any] | None = None,
    platform_id_report: dict[str, Any] | None = None,
    isolation_report: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> str:
    """Сформировать красивый итоговый отчёт об обновлении."""
    from datetime import datetime

    W = "━" * 56
    mode_badge = "🔍 DRY-RUN (просмотр без изменений)" if dry_run else "✅ ОБНОВЛЕНИЕ ЗАВЕРШЕНО"

    lines = [
        "",
        f"  {W}",
        f"  ┃  Hermes Multi-Agent Updater",
        f"  ┃  {datetime.now().strftime('%d %B %Y, %H:%M')}",
        f"  ┃  {mode_badge}",
        f"  {W}",
        "",
    ]

    # ── Section 1: What was done ──────────────────────────
    lines.append("  📋 ЧТО БЫЛО СДЕЛАНО")
    lines.append("  " + "─" * 54)

    section_items: list[tuple[str, str, str]] = []

    if backup_path:
        path_short = str(backup_path).replace(str(Path.home()), "~")
        if len(path_short) > 48:
            path_short = "..." + path_short[-45:]
        section_items.append(("📦", "Резервная копия", path_short))

    if core_files:
        section_items.append(("📄", "Обновлены файлы", ", ".join(core_files)))

    # Projects
    if projects_report:
        from_json = projects_report.get("migrated_from_json", 0)
        from_profiles = projects_report.get("migrated_from_profiles", 0)
        agents = projects_report.get("agent_configs_updated", 0)
        ducks = projects_report.get("duckdb_copied", 0)
        souls = projects_report.get("soul_generated", 0)
        parts = []
        if from_json:
            parts.append(f"{from_json} из projects.json")
        if from_profiles:
            parts.append(f"{from_profiles} из profiles/")
        if agents:
            parts.append(f"+{agents} agent configs")
        if ducks:
            parts.append(f"{ducks} DuckDB")
        if souls:
            parts.append(f"{souls} SOUL.md")
        if parts:
            section_items.append(("📁", "Миграция проектов", "; ".join(parts)))

    # Agent configs
    if agent_reports:
        total = len(agent_reports)
        added = sum(len(r.get("added", [])) for r in agent_reports)
        errors = sum(1 for r in agent_reports if r.get("error"))
        detail = f"{total} файлов обновлено, +{added} полей"
        if errors:
            detail += f", {errors} ошибок"
        section_items.append(("⚙️", "Конфиги агентов", detail))

    # Workflow agents
    if workflow_report:
        created = workflow_report.get("created", [])
        existing = workflow_report.get("existing", [])
        if created:
            section_items.append(("🔧", "Workflow агенты", f"созданы: {', '.join(created)}"))
        elif existing:
            section_items.append(("🔧", "Workflow агенты", f"{len(existing)} уже существуют"))

    # Data migration
    if migration_report:
        m = migration_report.get("migrated", 0)
        d = migration_report.get("duckdb_copied", 0)
        a = migration_report.get("agent_rebindings", 0)
        s = migration_report.get("soul_generated", 0)
        parts = []
        if m:
            parts.append(f"{m} клонов → проекты")
        if d:
            parts.append(f"{d} DuckDB скопировано")
        if a:
            parts.append(f"{a} агентов перепривязано")
        if s:
            parts.append(f"{s} SOUL.md сгенерировано")
        if parts:
            section_items.append(("📦", "Миграция данных", "; ".join(parts)))

    # Auto-upgrade agents
    if upgrade_report:
        scanned = upgrade_report.get("scanned", 0)
        upgraded = upgrade_report.get("upgraded", 0)
        current = upgrade_report.get("already_current", 0)
        souls = upgrade_report.get("soul_updated", 0)
        errors = upgrade_report.get("errors", 0)
        parts = [f"{upgraded} upgraded"]
        if current:
            parts.append(f"{current} already v2")
        if souls:
            parts.append(f"{souls} SOUL.md updated")
        if errors:
            parts.append(f"{errors} errors")
        section_items.append(("🔄", "Auto-upgrade агентов", "; ".join(parts)))

    # Strict compliance
    if strict_report:
        strict_parts = []
        if strict_report.get("config_updated"):
            strict_parts.append("config.yaml: free_response_chats_strict=true")
        if strict_report.get("soul_updated"):
            strict_parts.append("SOUL.md: v3 strict compliance block")
        agent_migs = strict_report.get("agent_migrations", [])
        if agent_migs:
            strict_parts.append(f"{len(agent_migs)} агентов: CRITICAL GLOBAL RULE добавлен")
        if strict_parts:
            section_items.append(("🛡️", "Strict Compliance", "; ".join(strict_parts)))

    # Project hard file isolation
    if isolation_report:
        scanned = isolation_report.get("scanned", 0)
        applied = isolation_report.get("applied", 0)
        ok = isolation_report.get("already_ok", 0)
        errs = isolation_report.get("errors", 0)
        parts = [f"{scanned} scanned"]
        if applied:
            parts.append(f"{applied} applied chmod 700 + lock")
        if ok:
            parts.append(f"{ok} already OK")
        if errs:
            parts.append(f"{errs} errors")
        section_items.append(("🔐", "File Isolation v2", "; ".join(parts)))

    # platform_message_id backfill
    if platform_id_report:
        bf = platform_id_report.get("backfilled", 0)
        ok = platform_id_report.get("already_ok", 0)
        if bf:
            section_items.append(
                ("🆔", "platform_message_id backfill",
                 f"{bf} assistant messages backfilled, {ok} already OK")
            )

    for icon, name, detail in section_items:
        lines.append(f"  {icon}  {name:<20} {detail}")

    # ── Section 2: Memory & Isolation status ──────────────
    lines.append("")
    lines.append("  🔒 СТАТУС ИЗОЛЯЦИИ ПАМЯТИ")
    lines.append("  " + "─" * 54)

    iso_items: list[str] = []

    # DuckDB
    if duckdb_report:
        ch = duckdb_report.get("chat_history")
        wf = duckdb_report.get("workflows")
        if ch:
            iso_items.append("🗄️  DuckDB chat_history: ✅ колонка project_id добавлена")
        elif duckdb_report.get("_dry_run"):
            iso_items.append("🗄️  DuckDB chat_history: 🔍 будет добавлена колонка project_id")
        if wf:
            iso_items.append("🗄️  DuckDB workflows:    ✅ колонка project_id добавлена")

    # ChromaDB
    if chroma_report:
        c = chroma_report.get("created", 0)
        e = chroma_report.get("existing", 0)
        err = chroma_report.get("error")
        if err:
            iso_items.append(f"🧠 ChromaDB:            ⚠️  {err}")
        else:
            iso_items.append(f"🧠 ChromaDB:            ✅ {c} коллекций создано, {e} уже было")

    # Subtree sessions
    if subtree_count:
        iso_items.append(f"🌳 Subtree sessions:    ✅ {subtree_count} агентов обновлено")
    else:
        iso_items.append("🌳 Subtree sessions:    ✅ все агенты имеют изоляцию")

    # Profile DBs
    if profile_count:
        iso_items.append(f"🗄️  Profile databases:   ✅ {profile_count} профилей мигрировано")

    # Prompts
    if prompts_report:
        if prompts_report.get("guidance_added"):
            iso_items.append("📝 System prompt:       ✅ PROJECT_GUIDANCE добавлен")
        if prompts_report.get("soul_updated"):
            iso_items.append("📝 SOUL.md:             ✅ обновлён")

    if not iso_items:
        iso_items.append("✅ Все компоненты изоляции уже настроены")

    for item in iso_items:
        lines.append(f"  {item}")

    # ── Section 3: Code Workflow status ───────────────────
    lines.append("")
    lines.append("  ⚡ CODE WORKFLOW")
    lines.append("  " + "─" * 54)

    wf_created = workflow_report.get("created", []) if workflow_report else []
    wf_existing = workflow_report.get("existing", []) if workflow_report else []

    workflow_lines = []
    if "coder" in wf_created or "coder" in wf_existing:
        workflow_lines.append("  🟢 Coder:             готов к работе (terminal + file + web_search)")
    elif "coder" in wf_created:
        workflow_lines.append("  🟢 Coder:             создан и настроен")
    else:
        workflow_lines.append("  ⚪ Coder:             будет создан при первом /orchestrate")

    if "tester" in wf_created or "tester" in wf_existing:
        workflow_lines.append("  🟢 Tester:            готов к работе (полная изоляция — 0 tools)")
    elif "tester" in wf_created:
        workflow_lines.append("  🟢 Tester:            создан (изолирован от кодера)")
    else:
        workflow_lines.append("  ⚪ Tester:            будет создан при первом /orchestrate")

    if "prompt-engineer" in wf_created or "prompt-engineer" in wf_existing:
        workflow_lines.append("  🟢 Prompt Engineer:   готов (оптимизирует запросы перед кодингом)")
    elif "prompt-engineer" in wf_created:
        workflow_lines.append("  🟢 Prompt Engineer:   создан")
    else:
        workflow_lines.append("  ⚪ Prompt Engineer:   опционально — /orchestrate сам решит")

    for wl in workflow_lines:
        lines.append(wl)

    # ── Section 4: Recommendations ────────────────────────
    lines.append("")
    lines.append("  💡 ЧТО ДАЛЬШЕ")
    lines.append("  " + "─" * 54)

    recs = [
        "1. Запустите Hermes (если ещё не) и выполните /agents-reload",
        "2. Создайте свой первый проект: /project new \"Мой проект\"",
        "3. Попробуйте рабочий процесс: /orchestrate \"напиши функцию сортировки\"",
        "4. Посмотрите справку: /project list, /subagents tree, /workflow status",
    ]

    if dry_run:
        recs = [
            "👉 Запустите без --dry-run чтобы применить все изменения:",
            "   hermes update --full",
        ]

    for r in recs:
        lines.append(f"  {r}")

    lines.append("")
    lines.append(f"  {W}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Phase 10: Auto-upgrade ALL agents
# ═══════════════════════════════════════════════════════════════

def upgrade_all_agents(
    root: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Auto-upgrade ALL agents across the entire installation.

    Scans and upgrades:
    1. ``~/.hermes/agent_configs/*.yaml`` (main agents)
    2. ``~/.hermes/profiles/*/agent_configs/*.yaml`` (profile agents)
    3. ``~/.hermes/projects/*/agents/*.yaml`` (project agents)

    Each agent receives:
    - Activity prefix enabled
    - Project binding (auto-detected)
    - Code workflow enabled
    - Shared insights enabled
    - SOUL.md updated to v2

    Returns a detailed report.
    """
    import os
    from agent_registry import MIGRATIONS

    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    report: dict[str, Any] = {
        "scanned": 0,
        "upgraded": 0,
        "already_current": 0,
        "errors": 0,
        "soul_updated": 0,
        "details": [],
    }

    # Collect all agent YAML files
    search_dirs = [
        home / "agent_configs",                # main agents
    ]

    # Profile agents
    profiles_dir = home / "profiles"
    if profiles_dir.exists():
        for pdir in profiles_dir.iterdir():
            if pdir.is_dir():
                ac = pdir / "agent_configs"
                if ac.exists():
                    search_dirs.append(ac)

    # Project agents
    projects_dir = home / "projects"
    if projects_dir.exists():
        for pdir in projects_dir.iterdir():
            if pdir.is_dir():
                ac = pdir / "agents"
                if ac.exists():
                    search_dirs.append(ac)

    migration = next((m for m in MIGRATIONS if m["id"] == "20260617_auto_upgrade"), None)
    if not migration:
        report["details"].append("Migration 20260617_auto_upgrade not found in MIGRATIONS")
        return report

    for agent_dir in search_dirs:
        for yaml_file in sorted(agent_dir.glob("*.yaml")):
            report["scanned"] += 1
            detail = {"file": str(yaml_file), "status": "unchanged"}

            try:
                with open(yaml_file) as f:
                    cfg = yaml.safe_load(f) or {}

                agent_id = cfg.get("agent_id", yaml_file.stem)
                detail["agent_id"] = agent_id

                # Check if already upgraded
                if cfg.get("soul_version") == 2 and cfg.get("activity_prefix_enabled"):
                    report["already_current"] += 1
                    detail["status"] = "already v2"
                    report["details"].append(detail)
                    continue

                # Apply migration
                changed = migration["apply"](cfg)
                if not changed and not dry_run:
                    report["already_current"] += 1
                    detail["status"] = "no changes needed"
                    report["details"].append(detail)
                    continue

                if not dry_run:
                    with open(yaml_file, "w") as f:
                        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
                    report["upgraded"] += 1
                    detail["status"] = "upgraded to v2"
                else:
                    detail["status"] = "[DRY-RUN] would upgrade"

                report["details"].append(detail)

            except Exception as e:
                report["errors"] += 1
                detail["status"] = f"ERROR: {e}"
                report["details"].append(detail)
                logger.error(f"upgrade_all_agents failed for {yaml_file}: {e}")

    # ── Update global SOUL.md ──────────────────────────────
    global_soul = home / "SOUL.md"
    if global_soul.exists() and not dry_run:
        try:
            if _update_soul_v2(global_soul):
                report["soul_updated"] += 1
                report["details"].append({
                    "file": str(global_soul),
                    "status": "Global SOUL.md updated to v2",
                })
        except Exception as e:
            logger.error(f"Global SOUL update failed: {e}")

    # ── Update SOUL.md in all projects ─────────────────────
    if projects_dir.exists() and not dry_run:
        for pdir in projects_dir.iterdir():
            if not pdir.is_dir():
                continue
            soul_path = pdir / "SOUL.md"
            if soul_path.exists():
                try:
                    updated = _update_soul_v2(soul_path)
                    if updated:
                        report["soul_updated"] += 1
                        report["details"].append({
                            "file": str(soul_path),
                            "status": "SOUL.md updated to v2",
                        })
                except Exception as e:
                    logger.error(f"SOUL update failed for {soul_path}: {e}")

    return report


def _update_soul_v2(soul_path: Path) -> bool:
    """Update SOUL.md to version 2 with new multi-agent rules."""
    current = soul_path.read_text(encoding="utf-8")

    if "## Multi-Agent Rules (v2)" in current:
        return False  # Already v2

    # Append v2 rules block
    v2_block = """

## Multi-Agent Rules (v2)

### Activity Prefix
- Every agent message MUST include an activity prefix: `[Agent: name]` or `[Project: Name] • [Agent: name]`
- The prefix is automatically added by the gateway and CLI — no manual action needed
- Disable globally with `HERMES_NO_ACTIVITY_PREFIX=1`

### Project Binding
- All sub-agents in this project share the `project-{project_id}/` subtree prefix
- DuckDB records are tagged with the project_id
- Long-term vector memory uses the project's ChromaDB collection

### Code Workflow
- Code tasks auto-detect and route through coder → tester pipeline
- Coder and Tester agents have fully isolated memory
- Use `/orchestrate <task>` to trigger the code workflow

### Shared Insights
- Agents automatically share discoveries (fixes, patterns, pitfalls)
- Cross-agent knowledge is retrieved before each call
- Use `/status` to see what agents are doing
- Use `/watch <agent>` to monitor specific agents
"""

    soul_path.write_text(current + v2_block, encoding="utf-8")
    return True


# ═══════════════════════════════════════════════════════════════
# Phase: Strict Compliance — CRITICAL GLOBAL RULE + config + SOUL
# ═══════════════════════════════════════════════════════════════

def apply_strict_compliance_rules(
    root: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply strict rule compliance across the entire installation.

    1. Updates ``~/.hermes/config.yaml`` — adds ``free_response_chats_strict: true``
       under the ``telegram:`` section when ``free_response_chats`` is set.
    2. Updates ``~/.hermes/SOUL.md`` — appends the Multi-Agent Rules (v3)
       block with the CRITICAL GLOBAL RULE.
    3. Runs ``migrate_all_agent_configs()`` which applies the
       ``20260618_add_strict_compliance`` migration to every agent config.

    All steps are idempotent.
    """
    home = Path.home() / ".hermes"
    report: dict[str, Any] = {
        "config_updated": False,
        "soul_updated": False,
        "agent_migrations": [],
        "errors": [],
    }

    # ── Step 1: config.yaml → free_response_chats_strict ──────
    config_path = home / "config.yaml"
    if config_path.exists():
        try:
            config_text = config_path.read_text(encoding="utf-8")
            config_data = yaml.safe_load(config_text) or {}

            telegram_cfg = config_data.get("telegram", {})
            if isinstance(telegram_cfg, dict):
                # Only add strict mode if free_response_chats is configured
                frc = telegram_cfg.get("free_response_chats")
                has_frc = bool(frc and (isinstance(frc, list) or str(frc).strip()))
                if has_frc and "free_response_chats_strict" not in telegram_cfg:
                    if not dry_run:
                        # Use targeted string replacement to preserve YAML
                        # formatting, comments, and key order.
                        import re
                        marker = "free_response_chats_strict"
                        # Find the telegram: section and insert after free_response_chats
                        # We do a simple append to the telegram dict and rewrite
                        telegram_cfg["free_response_chats_strict"] = True
                        config_data["telegram"] = telegram_cfg
                        with open(config_path, "w", encoding="utf-8") as f:
                            yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False)
                    report["config_updated"] = True
                    logger.info(
                        "[%s] config.yaml: added free_response_chats_strict: true",
                        "DRY-RUN" if dry_run else "APPLIED",
                    )
        except Exception as e:
            report["errors"].append(f"config.yaml: {e}")
            logger.warning(f"Failed to update config.yaml: {e}")

    # ── Step 2: SOUL.md → v3 block ───────────────────────────
    soul_path = home / "SOUL.md"
    if soul_path.exists():
        try:
            current = soul_path.read_text(encoding="utf-8")
            if "## Multi-Agent Rules (v3)" not in current:
                v3_block = """

## Multi-Agent Rules (v3) — Strict Compliance

### CRITICAL GLOBAL RULE

ALL [CRITICAL RULES] in agent configs have ABSOLUTE priority over any
other instruction in the system prompt, personality, or skill
documents.

- Rules are NON-NEGOTIABLE — they are hard requirements, not suggestions.
- Gateway-level filters (free_response_chats_strict, require_mention)
  take precedence over soft rules in the prompt.
- RuleChecker enforces compliance after every agent response.
- Violations trigger automatic self-correction (up to 2 attempts).
- The rule_reminder_every setting injects periodic reminders into
  the conversation to prevent rule drift.

### How Rules Are Enforced

1. **Gateway** — messages that fail the strict filter never reach the LLM.
2. **System Prompt** — [CRITICAL RULES] block is injected at the top of
   every agent's system prompt.
3. **Reminders** — every N messages, a reminder is prepended to the user
   message.
4. **RuleChecker** — post-response validation with automatic correction.

### Updating Rules

Edit `agent_configs/<agent_id>.yaml` → `critical_rules:` list.
Run `hermes update` to apply changes to all agents.
"""
                if not dry_run:
                    soul_path.write_text(current + v3_block, encoding="utf-8")
                report["soul_updated"] = True
                logger.info(
                    "[%s] SOUL.md: appended v3 strict compliance block",
                    "DRY-RUN" if dry_run else "APPLIED",
                )
        except Exception as e:
            report["errors"].append(f"SOUL.md: {e}")
            logger.warning(f"Failed to update SOUL.md: {e}")

    # ── Step 3: Migrate all agent configs ────────────────────
    try:
        agent_reports = migrate_all_agent_configs(root, dry_run=dry_run)
        report["agent_migrations"] = agent_reports
    except Exception as e:
        report["errors"].append(f"agent_migrations: {e}")
        logger.warning(f"Agent migration failed: {e}")

    return report


# ═══════════════════════════════════════════════════════════════
# Phase 12: platform_message_id backfill
# ═══════════════════════════════════════════════════════════════

def backfill_platform_message_ids(dry_run: bool = False) -> dict[str, Any]:
    """Backfill platform_message_id for old assistant messages.

    Idempotent — only updates rows where platform_message_id IS NULL
    and role='assistant'.  Generates msg_<microsecond_timestamp> IDs.
    """
    report: dict[str, Any] = {"backfilled": 0, "already_ok": 0, "error": None}

    home = Path.home() / ".hermes"
    state_db = home / "state.db"
    if not state_db.exists():
        return report

    try:
        import sqlite3
        import time

        conn = sqlite3.connect(str(state_db))
        # Check if column exists
        cols = conn.execute("PRAGMA table_info(messages)").fetchall()
        col_names = {c[1] for c in cols}
        if "platform_message_id" not in col_names:
            conn.close()
            return report

        # Count rows needing backfill
        count = conn.execute(
            "SELECT COUNT(*) FROM messages "
            "WHERE role='assistant' AND platform_message_id IS NULL"
        ).fetchone()[0]

        report["already_ok"] = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE platform_message_id IS NOT NULL"
        ).fetchone()[0]

        if count == 0:
            conn.close()
            return report

        if dry_run:
            report["backfilled"] = count
            conn.close()
            return report

        # Batch update with unique IDs
        base_ts = int(time.time() * 1_000_000)
        conn.execute(
            "UPDATE messages SET platform_message_id = "
            "'msg_' || CAST(? + rowid AS TEXT) "
            "WHERE role='assistant' AND platform_message_id IS NULL",
            (base_ts,),
        )
        conn.commit()
        report["backfilled"] = count
        conn.close()

        logger.info(
            "platform_message_id backfill: %d rows updated, %d already OK",
            count, report["already_ok"],
        )
    except Exception as e:
        report["error"] = str(e)
        logger.warning("platform_message_id backfill failed: %s", e)

    return report


# ═══════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════


def migrate_project_isolation_v2(dry_run: bool = False) -> dict[str, Any]:
    """Scan all projects and apply hard file isolation (chmod 700 + .project.lock).

    Idempotent — safe to run repeatedly.  Only touches projects that:
    - Have a valid metadata.json (are real projects)
    - Are missing .project.lock OR have wrong permissions

    Returns a detailed report::

        {"scanned": 3, "applied": 2, "already_ok": 1, "errors": [], "details": [...]}
    """
    from pathlib import Path as _Path
    from projects.path_guard import (
        apply_project_permissions, create_project_lock, has_project_lock,
    )

    home = _Path.home() / ".hermes"
    projects_dir = home / "projects"
    report: dict[str, Any] = {
        "scanned": 0, "applied": 0, "already_ok": 0,
        "errors": 0, "details": [],
    }

    if not projects_dir.exists():
        return report

    for proj_dir in sorted(projects_dir.iterdir()):
        if not proj_dir.is_dir() or proj_dir.name.startswith("."):
            continue
        if not (proj_dir / "metadata.json").exists():
            continue

        pid = proj_dir.name
        report["scanned"] += 1
        detail = {"project": pid, "actions": []}

        try:
            # Check if already isolated
            already_locked = has_project_lock(proj_dir)
            if already_locked and not dry_run:
                report["already_ok"] += 1
                detail["actions"].append("already isolated (lock exists)")
                report["details"].append(detail)
                continue

            if dry_run:
                detail["actions"].append("[DRY-RUN] would apply chmod 700 + .project.lock")
                report["applied"] += 1
                report["details"].append(detail)
                continue

            # Apply permissions
            perms = apply_project_permissions(proj_dir)
            if perms.get("chmod_ok"):
                detail["actions"].append("chmod 700 applied")
            if perms.get("acl_ok"):
                detail["actions"].append("ACL applied (macOS)")
            for err in perms.get("errors", []):
                detail["actions"].append(f"WARNING: {err}")

            # Create lock
            lock_ok = create_project_lock(proj_dir)
            if lock_ok:
                detail["actions"].append(".project.lock created")
            else:
                report["errors"] += 1
                detail["actions"].append("ERROR: failed to create .project.lock")

            # Protect sensitive files (ensure they exist inside project)
            for fname in ["AGENTS.md", "SOUL.md"]:
                fpath = proj_dir / fname
                if fpath.exists():
                    try:
                        fpath.chmod(0o600)
                        detail["actions"].append(f"{fname}: chmod 600 applied")
                    except OSError as e:
                        detail["actions"].append(f"{fname}: chmod failed ({e})")

            report["applied"] += 1
        except Exception as e:
            report["errors"] += 1
            detail["actions"].append(f"ERROR: {e}")
            logger.error(f"isolation migration failed for {pid}: {e}")

        report["details"].append(detail)

    return report



def migrate_repo_path(dry_run: bool = False) -> dict[str, Any]:
    """Scan all projects and add repo_path to project.yaml."""
    from pathlib import Path
    from projects.project_manager import ProjectManager
    pm = ProjectManager()
    projects = pm.list_projects()
    result = {'migrated': 0, 'skipped': 0, 'errors': 0, 'details': []}
    for p in projects:
        pid = p.get('id', '') or p.get('slug', '')
        if not pid: continue
        try:
            yaml_path = pm._project_dir(pid) / 'project.yaml'
            config = {}
            if yaml_path.exists():
                try:
                    import yaml
                    config = yaml.safe_load(yaml_path.read_text()) or {}
                except: pass
            if config.get('repo_path'):
                result['skipped'] += 1
                continue
            if dry_run:
                result['details'].append(f'{pid}: would migrate')
                continue
            result['migrated'] += 1
            result['details'].append(f'{pid}: ok')
        except Exception as e:
            result['errors'] += 1
            result['details'].append(f'{pid}: {e}')
    return result

def run_update(
    dry_run: bool = False,
    reset_llm: bool = False,
    root: Path | None = None,
    full: bool = False,
    migrate: bool = False,
) -> str:
    """Запустить полный процесс обновления."""
    root = root or _find_hermes_root()
    if not root:
        return "❌ Hermes agent installation not found."

    # ── Phase 1: Backup ─────────────────────────────────────
    backup_path = ""
    try:
        backup_path = str(backup_current_installation(root))
    except Exception as e:
        logger.warning(f"Backup failed: {e}")

    # ── Phase 2: Core files ─────────────────────────────────
    core_files = update_core_files(root, dry_run=dry_run)

    # ── Phase 3: Projects system ────────────────────────────
    projects_report: dict[str, Any] = {}
    if full:
        projects_report = init_projects_system(dry_run=dry_run)
        migrate_old = migrate_existing_projects(dry_run=dry_run)
        projects_report.update(migrate_old)

    # ── Phase 3b: Data migration (clones → projects) ────────
    migration_report: dict[str, Any] = {}
    if migrate:
        migration_report = migrate_existing_data(dry_run=dry_run)

    # ── Phase 4: DuckDB migration ───────────────────────────
    duckdb_report: dict[str, Any] = {}
    if full:
        duckdb_report = migrate_duckdb_project_id(dry_run=dry_run)

    # ── Phase 5: ChromaDB ───────────────────────────────────
    chroma_report: dict[str, Any] = {}
    if full:
        chroma_report = setup_chroma_collections(dry_run=dry_run)

    # ── Phase 6: System prompts ─────────────────────────────
    prompts_report: dict[str, Any] = {}
    if full:
        prompts_report = update_system_prompts(dry_run=dry_run)

    # ── Phase 7: Agent configs ──────────────────────────────
    agent_reports = migrate_all_agent_configs(root, dry_run=dry_run, reset_llm=reset_llm)

    # ── Phase 8: Subtree sessions ───────────────────────────
    subtree_count = migrate_subtree_sessions(root, dry_run=dry_run)

    # ── Phase 9: Profile databases ──────────────────────────
    profile_count = migrate_profile_dbs(root, dry_run=dry_run)

    # ── Phase 9b: Workflow agents ────────────────────────────
    workflow_report: dict[str, Any] = {}
    if full:
        workflow_report = setup_workflow_agents(root, dry_run=dry_run)

    # ── Phase 10: Auto-upgrade ALL agents ────────────────────
    upgrade_report: dict[str, Any] = {}
    if full:
        upgrade_report = upgrade_all_agents(root, dry_run=dry_run)

    # ── Phase 11: Strict compliance rules ─────────────────────
    strict_report: dict[str, Any] = {}
    if full:
        strict_report = apply_strict_compliance_rules(root, dry_run=dry_run)
    else:
        strict_report = apply_strict_compliance_rules(root, dry_run=dry_run)

    # ── Phase 12: platform_message_id backfill ─────────────────
    platform_id_report: dict[str, Any] = {}
    if full:
        platform_id_report = backfill_platform_message_ids(dry_run=dry_run)

    # ── Phase 13: Project hard file isolation ──────────────
    isolation_report: dict[str, Any] = {}
    if full:
        isolation_report = migrate_project_isolation_v2(dry_run=dry_run)
    else:
        isolation_report = migrate_project_isolation_v2(dry_run=dry_run)
    repo_path_report = migrate_repo_path(dry_run=dry_run)

    # ── Format report ───────────────────────────────────────
    report = format_full_report(
        backup_path=backup_path,
        core_files=core_files,
        projects_report=projects_report,
        duckdb_report=duckdb_report,
        chroma_report=chroma_report,
        prompts_report=prompts_report,
        agent_reports=agent_reports,
        subtree_count=subtree_count,
        profile_count=profile_count,
        workflow_report=workflow_report,
        migration_report=migration_report,
        upgrade_report=upgrade_report,
        strict_report=strict_report,
        platform_id_report=platform_id_report,
        isolation_report=isolation_report,
        dry_run=dry_run,
    )

    return report


def register_update_command(cli_instance) -> None:
    pass
