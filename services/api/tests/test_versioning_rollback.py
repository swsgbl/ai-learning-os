"""M7-06 Versioning and rollback: version single-source, executable rollback plan.

Acceptance (backlog M7-06): 版本、changelog、数据库回滚和应用回滚方案可执行.

Five facets:
1. version single source - VERSION file is the only truth; web
   package.json version and CHANGELOG top entry are guarded to stay in
   sync (three sources, one number);
2. runtime endpoint - GET /api/v1/version exposes version/git/alembic
   with honest not_available degradation;
3. db-rollback safety - dry-run by default prints the plan without
   touching the DB; --yes is required to execute; steps<1 and unknown
   current refuse to run (fail-closed);
4. db-rollback execution - monkeypatched subprocess asserts the exact
   alembic downgrade argv and state re-query after rollback;
5. app rollback anchor - compose renders AIOS_IMAGE_TAG into api/web
   image refs (rollback = old tag + up -d --no-build).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.ops.version import REPO_ROOT, build_version_info, read_version

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
COMPOSE_FILE = Path(__file__).resolve().parents[3] / "infra" / "docker-compose.yml"


def test_version_sources_in_sync() -> None:
    """三个版本真相源一致：VERSION == web package.json == CHANGELOG 顶部。"""
    version = read_version()
    web_pkg = json.loads(
        (REPO_ROOT / "apps" / "web" / "package.json").read_text(encoding="utf-8")
    )
    assert web_pkg["version"] == version
    changelog = (REPO_ROOT / "docs" / "CHANGELOG.md").read_text(encoding="utf-8")
    m = [ln for ln in changelog.splitlines() if ln.startswith("## [")]
    assert m, "CHANGELOG 无版本条目"
    top = m[0]
    assert top.startswith(f"## [{version}]"), f"CHANGELOG 顶部 {top!r} != VERSION {version!r}"


def test_read_version_rejects_bad_format(tmp_path: Path) -> None:
    """VERSION 格式非法即抛错（不虚报版本）。"""
    import app.ops.version as vmod

    bad = tmp_path / "VERSION"
    bad.write_text("v1.x", encoding="utf-8")
    orig = vmod.VERSION_FILE
    try:
        vmod.VERSION_FILE = bad
        with pytest.raises(ValueError):
            read_version.__wrapped__()
    finally:
        vmod.VERSION_FILE = orig


def test_version_endpoint_fields() -> None:
    """/version 端点 200 + 四字段（git/alembic 如实降级）。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        r = client.get("/api/v1/version")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["version"] == read_version()
        assert set(body) >= {"version", "git_commit", "alembic_current", "alembic_head"}


def test_build_version_info_honest_degradation() -> None:
    """git/alembic 缺席如实 not_available（不虚报状态）。"""
    info = build_version_info(git=None, alembic={})
    assert info["git_commit"] == "not_available"
    assert info["alembic_current"] == "not_available"


class _Args:
    def __init__(self, steps: int = 1, yes: bool = False) -> None:
        self.steps = steps
        self.yes = yes


def test_db_rollback_dry_run_no_execution(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """dry-run 打印计划且绝不调用 downgrade 子进程。"""
    from app.ops import cli as cli_mod

    called = []

    def _no_exec(argv, **kw):
        called.append(argv)
        raise AssertionError(f"dry-run 不得执行子进程: {argv}")

    monkeypatch.setattr(cli_mod.subprocess, "run", _no_exec)
    monkeypatch.setattr(cli_mod, "alembic_state", lambda _d=None: {
        "current": "0021_eval_runs", "head": "0021_eval_runs"
    })
    rc = cli_mod._run_db_rollback(_Args(steps=1, yes=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "[dry-run]" in out and "downgrade -1" in out
    assert called == []


def test_db_rollback_yes_executes(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """--yes 执行精确 argv 的 downgrade 并回读状态。"""
    from app.ops import cli as cli_mod

    captured = []

    def _fake_run(argv, **kw):
        captured.append(argv)
        if "downgrade" in argv:
            class P:
                returncode = 0
                stdout = ""
                stderr = ""
            return P()
        raise AssertionError(f"unexpected subprocess call: {argv}")

    monkeypatch.setattr(cli_mod.subprocess, "run", _fake_run)

    states = iter([
        {"current": "0021_eval_runs", "head": "0021_eval_runs"},
        {"current": "0020_prev", "head": "0021_eval_runs"},
    ])
    monkeypatch.setattr(cli_mod, "alembic_state", lambda _d=None: next(states))
    rc = cli_mod._run_db_rollback(_Args(steps=1, yes=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "0021_eval_runs -> 0020_prev" in out
    down = [a for a in captured if "downgrade" in a]
    assert len(down) == 1 and down[0][-1] == "-1"


def test_db_rollback_refuses_invalid_input(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """steps<1 或 current 未知一律拒绝（fail-closed，退出码 2）。"""
    from app.ops import cli as cli_mod

    monkeypatch.setattr(cli_mod.subprocess, "run", lambda a, **k: (_ for _ in ()).throw(
        AssertionError("refused input must not execute")))
    monkeypatch.setattr(cli_mod, "alembic_state", lambda _d=None: {
        "current": "0021_eval_runs", "head": "0021_eval_runs"
    })
    assert cli_mod._run_db_rollback(_Args(steps=0, yes=True)) == 2

    monkeypatch.setattr(cli_mod, "alembic_state", lambda _d=None: {
        "current": None, "head": "0021_eval_runs"
    })
    assert cli_mod._run_db_rollback(_Args(steps=1, yes=True)) == 2


def _compose_available() -> bool:
    import shutil

    return shutil.which("docker") is not None


@pytest.mark.skipif(not _compose_available(), reason="需要 docker compose CLI")
def test_compose_renders_image_tag_anchor() -> None:
    """AIOS_IMAGE_TAG 渲染进 api/web image（应用回滚锚点可插值）。"""
    import subprocess

    def render(env_tag: str | None) -> dict:
        cmd = ["docker", "compose", "-f", str(COMPOSE_FILE), "config", "--format", "json"]
        import os

        env = {**os.environ}
        if env_tag:
            env["AIOS_IMAGE_TAG"] = env_tag
        else:
            env.pop("AIOS_IMAGE_TAG", None)
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True, env=env)
        return json.loads(proc.stdout)

    model = render(None)
    assert model["services"]["api"]["image"] == "aios/api:local"
    assert model["services"]["web"]["image"] == "aios/web:local"

    model_v = render("v0.1.0")
    assert model_v["services"]["api"]["image"] == "aios/api:v0.1.0"
    assert model_v["services"]["web"]["image"] == "aios/web:v0.1.0"


def test_readme_documents_rollback_runbook() -> None:
    """README 回滚 runbook 与实现同源：三个可执行命令与 tag 锚点必须在文档中。"""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "db-rollback --steps 1 --yes" in readme
    assert "AIOS_IMAGE_TAG" in readme
    assert "up -d --no-build" in readme
    assert "cli backup" in readme or "cli backup --out" in readme
