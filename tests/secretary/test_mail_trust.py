"""Tests for trust policy."""
from __future__ import annotations


class TestIsTrusted:
    def test_no_prefs_not_trusted(self):
        from tools.secretary.mail_draft import is_trusted
        assert not is_trusted("alice@example.com", {})

    def test_trusted_domain(self):
        from tools.secretary.mail_draft import is_trusted
        prefs = {"trusted_domains": ["example.com"]}
        assert is_trusted("alice@example.com", prefs)
        assert not is_trusted("bob@other.com", prefs)

    def test_trusted_email_exact(self):
        from tools.secretary.mail_draft import is_trusted
        prefs = {"trusted_emails": ["alice@example.com"]}
        assert is_trusted("alice@example.com", prefs)
        assert not is_trusted("bob@example.com", prefs)

    def test_empty_addr_allows(self):
        from tools.secretary.mail_draft import is_trusted
        assert is_trusted("", {"trusted_domains": []})


class TestTrustDomain:
    def test_add_domain(self):
        from tools.secretary.mail_draft import trust_domain
        prefs = trust_domain("alice@example.com", {})
        assert "example.com" in prefs.get("trusted_domains", [])

    def test_no_duplicate(self):
        from tools.secretary.mail_draft import trust_domain
        prefs = {"trusted_domains": ["example.com"]}
        prefs = trust_domain("bob@example.com", prefs)
        assert prefs["trusted_domains"] == ["example.com"]
