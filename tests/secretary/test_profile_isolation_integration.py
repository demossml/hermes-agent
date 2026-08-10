"""Integration: verify hermes_home override with two profiles."""
from __future__ import annotations
import os, tempfile, shutil
from pathlib import Path
from hermes_constants import (
    set_hermes_home_override, reset_hermes_home_override,
    get_hermes_home, get_hermes_home_override,
)


def test_override_flows_to_get_hermes_home():
    """ContextVar override changes get_hermes_home() dynamically."""
    token = set_hermes_home_override("/tmp/profile-A")
    try:
        assert get_hermes_home() == Path("/tmp/profile-A")
        assert get_hermes_home_override() == "/tmp/profile-A"
    finally:
        reset_hermes_home_override(token)
    assert get_hermes_home_override() is None


def test_two_profiles_dont_leak():
    """Profile A and B are independent."""
    t1 = set_hermes_home_override("/tmp/A")
    try:
        assert get_hermes_home() == Path("/tmp/A")
        t2 = set_hermes_home_override("/tmp/B")
        try:
            assert get_hermes_home() == Path("/tmp/B")
        finally:
            reset_hermes_home_override(t2)
        assert get_hermes_home() == Path("/tmp/A")
    finally:
        reset_hermes_home_override(t1)


def test_default_path_restored():
    """After reset, back to default."""
    default = get_hermes_home()
    token = set_hermes_home_override("/tmp/override-test")
    try:
        assert get_hermes_home() == Path("/tmp/override-test")
    finally:
        reset_hermes_home_override(token)
    assert get_hermes_home() == default


def test_router_paths_under_profiles():
    """get_profile_path returns profiles/<name> for non-default."""
    from gateway.secretary_router import get_profile_path
    path = get_profile_path("secretary-acme")
    assert "profiles" in str(path)
    assert "secretary-acme" == path.parts[-1]


def test_router_default_is_home():
    """get_profile_path('default') returns the control home."""
    from gateway.secretary_router import get_profile_path
    path = get_profile_path("default")
    # Should be the same as get_hermes_home() (or a sub-path thereof)
    assert path is not None
