"""Tests for projects/project_auto.py"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from projects.project_auto import detect_project_creation_intent


class TestDetectIntent:
    """Test keyword-based project creation intent detection."""

    # ── Russian ─────────────────────────────────────────────

    def test_ru_novyi_proekt(self):
        assert detect_project_creation_intent("Давай начнём новый проект") == ""

    def test_ru_sozdai_proekt_s_name(self):
        name = detect_project_creation_intent("Создай проект для Телеграм Бота")
        assert name is not None

    def test_ru_sozdai_proekt_dlia(self):
        name = detect_project_creation_intent("создай проект для интернет-магазина")
        assert name == "интернет-магазина"

    def test_ru_rabotaem_nad_proektom(self):
        name = detect_project_creation_intent("Работаем над новым проектом Telegram Trading Bot")
        assert name == "telegram trading bot"

    def test_ru_nachinaem_proekt(self):
        name = detect_project_creation_intent("Начинаем проект аналитики")
        assert name == "аналитики"

    def test_ru_sdelai_proekt(self):
        name = detect_project_creation_intent("Сделай проект MyWebApp")
        assert name is not None

    # ── English ─────────────────────────────────────────────

    def test_en_new_project_called(self):
        name = detect_project_creation_intent("let's start a new project called Analytics Dashboard")
        assert name == "analytics dashboard"

    def test_en_create_project_for(self):
        name = detect_project_creation_intent("create a project for customer onboarding")
        assert name == "customer onboarding"

    def test_en_work_on_project(self):
        name = detect_project_creation_intent("I want to work on a project for deployment automation")
        assert name == "deployment automation"

    def test_en_new_project_colon(self):
        name = detect_project_creation_intent("new project: API Gateway")
        assert name == "api gateway"

    # ── No intent ───────────────────────────────────────────

    def test_no_intent_general_chat(self):
        assert detect_project_creation_intent("What is the weather today?") is None

    def test_no_intent_code_request(self):
        assert detect_project_creation_intent("Напиши функцию сортировки") is None

    def test_no_intent_question(self):
        assert detect_project_creation_intent("Как работает DuckDB?") is None

    # ── Edge cases ──────────────────────────────────────────

    def test_empty_string(self):
        assert detect_project_creation_intent("") is None

    def test_just_project_word(self):
        """'project' alone without 'new' or 'create' shouldn't trigger."""
        # Actually "project" is in both RU and EN detection but not alone
        result = detect_project_creation_intent("project")
        # No new/create/start/begin prefix → no match
        assert result is None
