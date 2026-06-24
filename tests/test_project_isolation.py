"""
Tests for project hard file isolation.
"""
import json, os, tempfile
from pathlib import Path
import pytest


@pytest.fixture
def temp_hermes_home():
    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / ".hermes"
        (home / "projects").mkdir(parents=True, exist_ok=True)
        old = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = str(home)
        yield home
        if old: os.environ["HERMES_HOME"] = old
        else: os.environ.pop("HERMES_HOME", None)


@pytest.fixture
def two_projects(temp_hermes_home):
    from projects.project_manager import ProjectManager
    from projects.path_guard import create_project_lock
    pm = ProjectManager(hermes_home=temp_hermes_home)
    alpha = pm._projects_dir / "alpha"
    alpha.mkdir(parents=True, exist_ok=True)
    (alpha / "metadata.json").write_text(
        json.dumps({"project_id":"alpha","name":"Alpha"}), encoding="utf-8")
    for d in ["code","data"]: (alpha/d).mkdir(exist_ok=True)
    (alpha/"code/main.py").write_text("print('alpha')")
    (alpha/"AGENTS.md").write_text("# Alpha")
    (alpha/"SOUL.md").write_text("# Alpha")
    create_project_lock(alpha)
    beta = pm._projects_dir / "beta"
    beta.mkdir(parents=True, exist_ok=True)
    (beta/"metadata.json").write_text(
        json.dumps({"project_id":"beta","name":"Beta"}), encoding="utf-8")
    (beta/"code").mkdir(exist_ok=True)
    (beta/"code/utils.py").write_text("def util(): pass")
    (beta/"AGENTS.md").write_text("# Beta")
    create_project_lock(beta)
    return pm, alpha, beta


class TestRootDetection:
    def test_no_project(self):
        os.environ.pop("HERMES_PROJECT_ROOT",None)
        from projects.path_guard import get_current_project_root
        assert get_current_project_root() is None
    def test_env_var(self,two_projects):
        _,alpha,_=two_projects
        os.environ["HERMES_PROJECT_ROOT"]=str(alpha)
        from projects.path_guard import get_current_project_root
        assert get_current_project_root() is not None
    def test_no_lock(self,two_projects):
        _,alpha,_=two_projects
        (alpha/".project.lock").unlink()
        os.environ["HERMES_PROJECT_ROOT"]=str(alpha)
        from projects.path_guard import get_current_project_root
        assert get_current_project_root() is None


class TestEnforce:
    def test_no_project_ok(self):
        os.environ.pop("HERMES_PROJECT_ROOT",None)
        from projects.path_guard import enforce
        assert enforce("/etc/passwd",operation="read") is None
    def test_inside_ok(self,two_projects):
        _,alpha,_=two_projects
        from projects.path_guard import enforce
        assert enforce(str(alpha/"code/main.py"),operation="read",project_root=alpha) is None
    def test_outside_denied(self,two_projects):
        _,alpha,_=two_projects
        from projects.path_guard import enforce
        e=enforce("/etc/passwd",operation="read",project_root=alpha)
        assert e and "DENIED" in e
    def test_cross_read_denied(self,two_projects):
        _,alpha,beta=two_projects
        from projects.path_guard import enforce
        e=enforce(str(beta/"code/utils.py"),operation="read",project_root=alpha)
        assert e and "DENIED" in e
    def test_cross_write_denied(self,two_projects):
        _,alpha,beta=two_projects
        from projects.path_guard import enforce
        assert enforce(str(beta/"code/new.py"),operation="write",project_root=alpha) is not None
    def test_cross_search_denied(self,two_projects):
        _,alpha,beta=two_projects
        from projects.path_guard import enforce
        assert enforce(str(beta),operation="search",project_root=alpha) is not None
    def test_relative_escape(self,two_projects):
        _,alpha,beta=two_projects
        from projects.path_guard import enforce
        assert enforce("../beta/code/utils.py",operation="read",project_root=str(alpha/"code")) is not None
    def test_hermes_home_ok(self,two_projects):
        pm,alpha,_=two_projects
        from projects.path_guard import enforce
        assert enforce(str(pm._home/"skills"),operation="read",project_root=alpha) is None


class TestSensitiveFiles:
    def test_cross_denied(self,two_projects):
        _,alpha,beta=two_projects
        from projects.path_guard import enforce
        assert enforce(str(beta/"AGENTS.md"),operation="read",project_root=alpha) is not None
    def test_own_allowed(self,two_projects):
        _,alpha,_=two_projects
        from projects.path_guard import enforce
        assert enforce(str(alpha/"AGENTS.md"),operation="read",project_root=alpha) is None


class TestPermissions:
    def test_chmod(self,temp_hermes_home):
        from projects.path_guard import apply_project_permissions
        d=temp_hermes_home/"projects"/"tp"
        d.mkdir(parents=True,exist_ok=True)
        (d/"metadata.json").write_text("{}")
        r=apply_project_permissions(d)
        assert r["chmod_ok"],f"chmod failed: {r.get('errors')}"
    def test_lock(self,temp_hermes_home):
        from projects.path_guard import create_project_lock, has_project_lock
        d=temp_hermes_home/"projects"/"tl"
        d.mkdir(parents=True,exist_ok=True)
        assert not has_project_lock(d)
        assert create_project_lock(d)
        assert has_project_lock(d)


class TestManagerIntegration:
    def test_create_isolation(self,temp_hermes_home):
        from projects.project_manager import ProjectManager
        from projects.path_guard import has_project_lock
        pm=ProjectManager(hermes_home=temp_hermes_home)
        meta=pm.create_project("Iso",link_cwd=False)
        assert meta.get("isolation")=="hard"
        assert has_project_lock(Path(meta["lock_file"]).parent)


class TestMigration:
    def test_dry_run(self,temp_hermes_home):
        from multiagent_updater import migrate_project_isolation_v2
        d=temp_hermes_home/"projects"/"dr"
        d.mkdir(parents=True,exist_ok=True)
        (d/"metadata.json").write_text(json.dumps({"project_id":"dr","name":"DR"}))
        (d/"code").mkdir(exist_ok=True)
        old=d.stat().st_mode
        migrate_project_isolation_v2(dry_run=True)
        assert d.stat().st_mode==old
    def test_apply(self,temp_hermes_home):
        from multiagent_updater import migrate_project_isolation_v2
        from projects.path_guard import has_project_lock
        d=temp_hermes_home/"projects"/"ap"
        d.mkdir(parents=True,exist_ok=True)
        (d/"metadata.json").write_text(json.dumps({"project_id":"ap","name":"AP"}))
        (d/"code").mkdir(exist_ok=True)
        r=migrate_project_isolation_v2(dry_run=False)
        assert r["errors"]==0,f"Errors: {r['details']}"
        assert has_project_lock(d)
    def test_idempotent(self,temp_hermes_home):
        from multiagent_updater import migrate_project_isolation_v2
        d=temp_hermes_home/"projects"/"id"
        d.mkdir(parents=True,exist_ok=True)
        (d/"metadata.json").write_text(json.dumps({"project_id":"id","name":"ID"}))
        r1=migrate_project_isolation_v2(dry_run=False)
        r2=migrate_project_isolation_v2(dry_run=False)
        assert r1["errors"]==0 and r2["errors"]==0
        assert r2["already_ok"]>=1


class TestFullFlow:
    def test_full_cycle(self,temp_hermes_home):
        from projects.project_manager import ProjectManager
        from projects.path_guard import enforce, get_current_project_root
        pm=ProjectManager(hermes_home=temp_hermes_home)
        meta=pm.create_project("Full",link_cwd=False)
        d=pm._project_dir(meta["project_id"])
        os.environ["HERMES_PROJECT_ROOT"]=str(d)
        assert get_current_project_root() is not None
        (d/"src").mkdir(exist_ok=True)
        (d/"src/app.py").write_text("print(42)")
        assert enforce(str(d/"src/app.py"),operation="read") is None
        e=enforce("/etc/hosts",operation="read")
        assert e and "DENIED" in e
        os.environ.pop("HERMES_PROJECT_ROOT",None)


if __name__=="__main__":
    pytest.main([__file__,"-v","--tb=short"])
