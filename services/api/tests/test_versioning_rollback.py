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
5. app rollback anchor - compose renders AIOS_IMAGE_TAG into the api
   image ref and AIOS_WEB_IMAGE_TAG into the web image ref (M14-09:
   independent anchors; rollback = old tags + up -d --no-build).
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
    heads = [ln for ln in changelog.splitlines() if ln.startswith("## [")]
    assert heads, "CHANGELOG 无版本条目"
    # Keep a Changelog：[Unreleased] 是未发布变更节而非版本条目，守卫跳过它
    released = [ln for ln in heads if not ln.startswith("## [Unreleased]")]
    assert released, "CHANGELOG 无已发布版本条目"
    assert released[0].startswith(f"## [{version}]"), (
        f"CHANGELOG 已发布顶部 {released[0]!r} != VERSION {version!r}"
    )


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
    """api/web 镜像 tag 独立锚点渲染（M14-09）：api 只随 AIOS_IMAGE_TAG、
    web 只随 AIOS_WEB_IMAGE_TAG——单设其一不连坐另一服务。"""
    import subprocess

    def render(env_updates: dict[str, str] | None) -> dict:
        cmd = ["docker", "compose", "-f", str(COMPOSE_FILE), "config", "--format", "json"]
        import os

        env = {**os.environ}
        for key in ("AIOS_IMAGE_TAG", "AIOS_WEB_IMAGE_TAG"):
            env.pop(key, None)
        if env_updates:
            env.update(env_updates)
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True, env=env)
        return json.loads(proc.stdout)

    model = render(None)
    assert model["services"]["api"]["image"] == "aios/api:local"
    assert model["services"]["web"]["image"] == "aios/web:local"

    # M14-09：只设 AIOS_IMAGE_TAG —— api 换 tag、web 保持独立默认（不跟随）
    model_api_only = render({"AIOS_IMAGE_TAG": "v0.1.0"})
    assert model_api_only["services"]["api"]["image"] == "aios/api:v0.1.0"
    assert model_api_only["services"]["web"]["image"] == "aios/web:local"

    # 同 tag 发布/回滚 = 显式同时设置两个变量
    model_both = render({"AIOS_IMAGE_TAG": "v0.1.0", "AIOS_WEB_IMAGE_TAG": "v0.1.0"})
    assert model_both["services"]["api"]["image"] == "aios/api:v0.1.0"
    assert model_both["services"]["web"]["image"] == "aios/web:v0.1.0"

    # Web-only 升级 = 只设 AIOS_WEB_IMAGE_TAG（api 不动）
    model_web_only = render({"AIOS_WEB_IMAGE_TAG": "m14-09-web"})
    assert model_web_only["services"]["api"]["image"] == "aios/api:local"
    assert model_web_only["services"]["web"]["image"] == "aios/web:m14-09-web"


def test_readme_documents_rollback_runbook() -> None:
    """README 回滚 runbook 与实现同源：三个可执行命令与 tag 锚点必须在文档中。"""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "db-rollback --steps 1 --yes" in readme
    assert "AIOS_IMAGE_TAG" in readme
    assert "up -d --no-build" in readme
    assert "cli backup" in readme or "cli backup --out" in readme


# --- M14-211: 共享仓库根发现（locate_repository_root） ---------------------------


def test_locate_repository_root_resolves_source_checkout() -> None:
    """源码 checkout 布局：从 version.py 真实位置出发解析到真仓库根（VERSION
    标记齐备），与 ``REPO_ROOT`` 常量一致；弱契约（只找 VERSION）与强契约
    （额外要求 infra）都成立——源码仓库根两者齐备。"""
    from app.ops.version import locate_repository_root

    root = locate_repository_root()
    assert root == REPO_ROOT
    assert (root / "VERSION").is_file()
    # 强契约：源码仓库根也含 infra（provider-smoke 编排冒烟脚本所需）。
    root_strong = locate_repository_root(require_infra=True)
    assert root_strong == REPO_ROOT
    assert (root_strong / "infra").is_dir()


def test_locate_repository_root_resolves_container_app_layout(tmp_path: Path) -> None:
    """容器 /app 布局（Dockerfile COPY services/api/app ./app + VERSION +
    infra/smoke_*.sh → /app/app/ops/version.py）：从 /app/app/ops 出发解析到
    /app——旧 parents[N] 固定偏移在此布局下越界 IndexError；弱契约只找
    VERSION 即可解析，强契约额外要求 infra 目录（容器内冒烟脚本可见）。"""
    from app.ops.version import locate_repository_root

    app_root = tmp_path / "app"
    module_dir = app_root / "app" / "ops"
    module_dir.mkdir(parents=True)
    (app_root / "VERSION").write_text("0.0.0-container\n", encoding="utf-8")
    # 弱契约：只找 VERSION，无需 infra——容器内 version 端点等不需要 infra。
    resolved_weak = locate_repository_root(
        module_dir / "version.py", require_infra=False
    )
    assert resolved_weak == app_root.resolve()
    # 强契约：缺 infra 时 fail-closed（明确 RuntimeError，不含混失败）。
    with pytest.raises(RuntimeError, match="仓库根"):
        locate_repository_root(module_dir / "version.py", require_infra=True)
    # 补上 infra 后强契约成立——容器内 provider-smoke 编排冒烟脚本所需。
    (app_root / "infra").mkdir()
    (app_root / "infra" / "smoke_search.sh").write_text("#!/usr/bin/env bash\n")
    resolved_strong = locate_repository_root(
        module_dir / "version.py", require_infra=True
    )
    assert resolved_strong == app_root.resolve()
    assert (resolved_strong / "infra").is_dir()


def test_locate_repository_root_missing_markers_raises_runtime_error(
    tmp_path: Path,
) -> None:
    """标记缺失（无 VERSION 的目录树）=> 明确 RuntimeError——不允许含混失败
    （如固定偏移越界的 IndexError）。弱契约与强契约都 fail-closed。"""
    from app.ops.version import locate_repository_root

    orphan = tmp_path / "orphan" / "app" / "ops"
    orphan.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="仓库根"):
        locate_repository_root(orphan / "version.py")
    with pytest.raises(RuntimeError, match="仓库根"):
        locate_repository_root(orphan / "version.py", require_infra=True)


def test_locate_repository_root_strong_contract_rejects_version_without_infra(
    tmp_path: Path,
) -> None:
    """强契约边界：有 VERSION 但无 infra 的目录不是 provider-smoke 的合法
    仓库根——require_infra=True 必须拒绝（避免把缺冒烟脚本的目录误判为根，
    导致容器内编排失败时含混）。弱契约仍放行（version 自身等不需要 infra）。"""
    from app.ops.version import locate_repository_root

    partial = tmp_path / "partial"
    (partial / "app" / "ops").mkdir(parents=True)
    (partial / "VERSION").write_text("0.0.0\n", encoding="utf-8")
    # 弱契约放行
    assert locate_repository_root(partial / "app" / "ops" / "v.py") == partial.resolve()
    # 强契约拒绝
    with pytest.raises(RuntimeError, match="仓库根"):
        locate_repository_root(
            partial / "app" / "ops" / "v.py", require_infra=True
        )
