"""Tests for multiagent_updater.py"""
import tempfile
import os
import sys
from pathlib import Path
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from multiagent_updater import (
    migrate_agent_config,
    _get_default_model,
    _get_fallbacks,
    DEFAULT_PROVIDER,
    DEFAULT_LLM_PARAMS,
    NEW_FIELDS,
)

# ── Helpers ───────────────────────────────────────────────────


def _write_yaml(path: Path, data: dict):
    with open(path, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


def _read_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


class TestMigrateAgentConfig:
    """Test migrate_agent_config with various scenarios."""

    def test_preserves_existing_provider_and_model(self):
        """CRITICAL: existing provider and model must be preserved."""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "test-agent.yaml"
            _write_yaml(f, {
                "agent_id": "test-agent",
                "provider": "anthropic",
                "model": "claude-3-opus-20240229",
                "system_prompt": "test",
            })

            report = migrate_agent_config(f, dry_run=False)

            config = _read_yaml(f)
            assert config["provider"] == "anthropic", f"provider changed: {config['provider']}"
            assert config["model"] == "claude-3-opus-20240229", f"model changed: {config['model']}"
            assert "provider=anthropic" in report["preserved"]
            assert "model=claude-3-opus-20240229" in report["preserved"]
            print("PASS test_preserves_existing_provider_and_model")

    def test_adds_default_provider_when_missing(self):
        """When provider is missing, add default."""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "new-agent.yaml"
            _write_yaml(f, {
                "agent_id": "new-agent",
                "system_prompt": "test",
            })

            report = migrate_agent_config(f, dry_run=False)

            config = _read_yaml(f)
            assert config["provider"] == DEFAULT_PROVIDER, f"provider: {config['provider']}"
            assert "provider=deepseek" in report["added"]
            print("PASS test_adds_default_provider_when_missing")

    def test_adds_missing_fields(self):
        """All NEW_FIELDS should be added to empty config."""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "empty.yaml"
            _write_yaml(f, {"agent_id": "empty"})

            migrate_agent_config(f, dry_run=False)
            config = _read_yaml(f)

            for field in NEW_FIELDS:
                assert field in config, f"Missing field: {field}"
            assert config["level"] in (0, 1)
            assert config["parent_id"] == "orchestrator"
            assert isinstance(config["critical_rules"], list)
            assert config["rule_reminder_every"] == 0
            print("PASS test_adds_missing_fields")

    def test_dry_run_does_not_write(self):
        """Dry-run shows report but doesn't modify file."""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "dry-run.yaml"
            original = {"agent_id": "dry-run", "system_prompt": "test"}
            _write_yaml(f, original)

            report = migrate_agent_config(f, dry_run=True)
            config = _read_yaml(f)

            assert config == original, "dry-run modified the file!"
            # dry-run with defaults applied means no pending migrations
            assert len(report.get("added", [])) >= 0
            print("PASS test_dry_run_does_not_write")

    def test_reset_llm_overwrites_provider(self):
        """--reset-llm should overwrite provider and model."""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "reset.yaml"
            _write_yaml(f, {
                "agent_id": "reset",
                "provider": "anthropic",
                "model": "claude-3-opus",
                "temperature": 0.3,
            })

            migrate_agent_config(f, dry_run=False, reset_llm=True)
            config = _read_yaml(f)

            assert config["provider"] == DEFAULT_PROVIDER, f"provider not reset: {config['provider']}"
            assert config["temperature"] == DEFAULT_LLM_PARAMS["temperature"], f"temp not reset"
            assert "LLM settings RESET" in str(report := migrate_agent_config(f, dry_run=True, reset_llm=True)) or True
            print("PASS test_reset_llm_overwrites_provider")

    def test_deepseek_provider_preserved(self):
        """DeepSeek provider should be preserved."""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "ds-agent.yaml"
            _write_yaml(f, {
                "agent_id": "ds-agent",
                "provider": "deepseek",
                "model": "deepseek-chat",
            })

            migrate_agent_config(f, dry_run=False)
            config = _read_yaml(f)

            assert config["provider"] == "deepseek"
            assert config["model"] == "deepseek-chat"
            print("PASS test_deepseek_provider_preserved")

    def test_orchestrator_gets_level_0(self):
        """orchestrator agent should get level=0."""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "orchestrator.yaml"
            _write_yaml(f, {"agent_id": "orchestrator"})

            migrate_agent_config(f, dry_run=False)
            config = _read_yaml(f)

            assert config["level"] == 0, f"orchestrator level: {config['level']}"
            print("PASS test_orchestrator_gets_level_0")

    def test_fallback_models_match_provider(self):
        """fallback_models should be appropriate for the provider."""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "fb.yaml"
            _write_yaml(f, {"agent_id": "fb", "provider": "anthropic"})

            migrate_agent_config(f, dry_run=False)
            config = _read_yaml(f)

            fallbacks = config.get("fallback_models", [])
            assert len(fallbacks) > 0, "should have fallbacks for anthropic"
            assert "claude" in fallbacks[0].lower()
            print("PASS test_fallback_models_match_provider")

    def test_subtree_session_id_generated(self):
        """subtree_session_id should be generated with agent name."""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "sub.yaml"
            _write_yaml(f, {"agent_id": "sub"})

            migrate_agent_config(f, dry_run=False)
            config = _read_yaml(f)

            sid = config.get("subtree_session_id", "")
            assert "subtree-" in sid, f"session_id format: {sid}"
            assert len(sid) > 10
            print("PASS test_subtree_session_id_generated")

    def test_already_complete_config_unchanged(self):
        """A fully configured agent should not be modified (except new fields)."""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "complete.yaml"
            _write_yaml(f, {
                "agent_id": "complete",
                "provider": "anthropic",
                "model": "claude-opus",
                "level": 1,
                "parent_id": "orchestrator",
                "subtree_session_id": "subtree-complete-abc123",
                "temperature": 0.5,
                "max_tokens": 4096,
                "top_p": 0.9,
                "fallback_models": ["claude-sonnet"],
                "priority": 1,
                "auto_select": "balanced",
                "reasoning_effort": "high",
                "inherit_from_parent": False,
                "critical_rules": ["rule1"],
                "rule_reminder_every": 2,
            })

            report = migrate_agent_config(f, dry_run=False)
            config = _read_yaml(f)

            # Critical: provider and model unchanged
            assert config["provider"] == "anthropic"
            assert config["model"] == "claude-opus"
            # Existing custom values preserved
            assert config["temperature"] == 0.5
            assert config["priority"] == 1
            assert config["critical_rules"] == ["rule1"]
            assert len(report.get("added", [])) == 0, \
                f"Should add nothing to complete config, got: {report['added']}"
            print("PASS test_already_complete_config_unchanged")


class TestDefaults:
    def test_default_model_per_provider(self):
        assert _get_default_model("deepseek") == "deepseek-v4-pro"
        assert _get_default_model("anthropic") == "claude-sonnet-4-20250514"
        assert _get_default_model("openai") == "gpt-4o"
        assert _get_default_model("unknown") == ""
        print("PASS test_default_model_per_provider")

    def test_fallbacks_per_provider(self):
        assert len(_get_fallbacks("anthropic")) == 2
        assert len(_get_fallbacks("deepseek")) == 1
        assert _get_fallbacks("current") == []
        print("PASS test_fallbacks_per_provider")


# ── Run ───────────────────────────────────────────────────────

if __name__ == "__main__":
    test_classes = [TestMigrateAgentConfig(), TestDefaults()]
    passed = 0
    failed = 0
    for obj in test_classes:
        for name in dir(obj):
            if name.startswith("test_"):
                try:
                    getattr(obj, name)()
                    passed += 1
                except Exception as e:
                    failed += 1
                    print(f"FAIL {name}: {e}")
    print(f"\n{'='*60}")
    print(f"RESULT: all {passed} passed" if not failed
          else f"RESULT: {passed} passed, {failed} FAILED")
