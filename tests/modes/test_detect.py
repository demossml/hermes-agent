"""Tests for modes.detect — NL + slash intent detection."""

import pytest
from modes.detect import detect_mode_intent, ModeIntent


class TestSlashCommands:
    def test_slash_dev(self):
        intent = detect_mode_intent("/mode dev")
        assert intent is not None
        assert intent.mode == "dev"
        assert intent.confidence == 1.0
        assert intent.is_slash is True

    def test_slash_secretary(self):
        intent = detect_mode_intent("/mode secretary")
        assert intent.mode == "secretary"
        assert intent.confidence == 1.0

    def test_slash_status(self):
        intent = detect_mode_intent("/mode status")
        assert intent.mode == "status"
        assert intent.confidence == 1.0

    def test_slash_case_insensitive(self):
        assert detect_mode_intent("/MODE DEV").mode == "dev"
        assert detect_mode_intent("/Mode Secretary").mode == "secretary"


class TestRuPhrases:
    def test_pereydi_dev(self):
        intent = detect_mode_intent("перейди в режим разработки")
        assert intent.mode == "dev"
        assert intent.confidence >= 0.90

    def test_pereydi_secretary(self):
        intent = detect_mode_intent("перейди в режим секретаря")
        assert intent.mode == "secretary"
        assert intent.confidence >= 0.90

    def test_smeni_na_project(self):
        assert detect_mode_intent("смени режим на проект").mode == "dev"

    def test_smeni_na_archive(self):
        assert detect_mode_intent("смени режим на архив").mode == "secretary"

    def test_rezhim_dev_short(self):
        assert detect_mode_intent("режим разработка").mode == "dev"

    def test_rezhim_manager(self):
        assert detect_mode_intent("режим менеджера").mode == "secretary"

    def test_vernis_dev(self):
        assert detect_mode_intent("вернись в разработку").mode == "dev"

    def test_vernis_secretary(self):
        assert detect_mode_intent("вернись в режим секретаря").mode == "secretary"

    def test_stan_secretaryom(self):
        assert detect_mode_intent("стань секретарём").mode == "secretary"

    def test_rabotay_kak_dev(self):
        assert detect_mode_intent("работай как разработчик").mode == "dev"

    def test_otkroysya_manager(self):
        intent = detect_mode_intent("откройся как менеджер")
        assert intent.mode == "secretary"

    def test_pereklyuchis_project(self):
        intent = detect_mode_intent("переключись в режим проекта")
        assert intent.mode == "dev"


class TestEnPhrases:
    def test_switch_to_dev(self):
        assert detect_mode_intent("switch to dev mode").mode == "dev"

    def test_switch_to_secretary(self):
        assert detect_mode_intent("switch to secretary mode").mode == "secretary"

    def test_go_back_dev(self):
        assert detect_mode_intent("go back to dev mode").mode == "dev"

    def test_go_back_secretary(self):
        assert detect_mode_intent("go back to secretary mode").mode == "secretary"


class TestStatusQueries:
    def test_kakoy_rezhim(self):
        intent = detect_mode_intent("какой режим")
        assert intent.mode == "status"

    def test_tekushchiy_rezhim(self):
        assert detect_mode_intent("текущий режим").mode == "status"

    def test_current_mode(self):
        assert detect_mode_intent("current mode").mode == "status"

    def test_which_mode(self):
        assert detect_mode_intent("which mode").mode == "status"


class TestFalsePositives:
    """Must NOT trigger on descriptive/narrative text."""

    def test_narrative_ru(self):
        assert detect_mode_intent("в режиме разработки мы используем typescript") is None

    def test_narrative_en(self):
        assert detect_mode_intent("in dev mode we use pytest for testing") is None

    def test_past_tense_ru(self):
        assert detect_mode_intent("я работал в режиме секретаря вчера") is None

    def test_past_tense_ru_short(self):
        assert detect_mode_intent("был в режиме разработки") is None

    def test_past_tense_en(self):
        assert detect_mode_intent("was in secretary mode yesterday") is None

    def test_plain_greeting(self):
        assert detect_mode_intent("привет, как дела?") is None

    def test_code_request(self):
        assert detect_mode_intent("напиши код для api") is None

    def test_empty(self):
        assert detect_mode_intent("") is None

    def test_none(self):
        assert detect_mode_intent(None) is None
