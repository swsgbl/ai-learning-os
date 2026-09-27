"""M14-156 公网边缘本地彩排 supervisor（tools/ops/public_edge_rehearsal.py）fail-closed 契约测试。

覆盖矩阵：
1. argv 白名单/fail-closed：未知子命令 exit 2；execute 缺 --work-dir exit 2；
   默认（无子命令）= plan，零 Docker/零网络/零写入；
2. 确认门：execute 需 --execute 与精确短语 EXECUTE PUBLIC EDGE REHEARSAL
   同时满足（缺一/错词均 exit 1 + 类别 confirm-gate + 零 Docker 调用零写入）；
   cleanup 需 CLEANUP PUBLIC EDGE REHEARSAL 同款双门；
3. work-dir 安全：仓库内/仓库根/文件系统根/符号链接/空值/非目录/
   外来条目/子目录一律 work-dir-unsafe；缺省（不存在）与纯产物集幂等复用通过；
4. 项目不变量：compose 文件 name 与全部 docker compose 调用恒为
   aios-m14-156-edge-rehearsal；清理只按精确 project 标签过滤——
   绝不出现 aios-m14-03-production-rehearsal 或任何其他项目名；
5. 回环/高位端口绑定：host 面唯一发布 127.0.0.1:39443:80；Caddyfile
   HTTP-only（:80、无 443/tls）；frps/frpc TLS+token 文件不变量；
6. 清理只碰自己：label 过滤值精确等于本项目；rm 只作用于查询返回的
   本项目容器/网络 ID；cleanup 子命令独立重放同款边界；
7. 无 secret 日志：一次性 token 绝不出现在 stdout/stderr/证据 JSON/MD/
   任何 docker 命令参数；证据只记长度事实；
8. 证据/报告原子性：_atomic_write 失败保真（原文件不变、无 .tmp 残留）；
   失败路径也落盘 result=fail + public_ready=false；证据目录拒绝仓库
   tracked 区；
9. 命令边界注入：main(runner=FakeRunner, prober=FakeProber)——测试
   全程零真实 Docker/零网络（RealRunner/RealProber 仅存在，不被调用）；
10. preflight fail-closed：docker CLI/compose 不可用、家机 API/Web 不可达、
    发布端口被占 → 稳定类别 docker-unavailable/target-unreachable/port-busy。

全部夹具仅驻 tmp（basetemp 为仓库外目录）/内存；零第三方依赖。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "ops"))

import public_edge_rehearsal as rehearsal

EXEC = ["execute"]
PHRASE = ["--confirm-phrase", rehearsal.EXECUTE_CONFIRM_PHRASE]
CLEAN_PHRASE = ["--confirm-phrase", rehearsal.CLEANUP_CONFIRM_PHRASE]
EXECUTE_FLAGS = ["--execute"]
SENTINEL_TOKEN = "e" * 64  # 离线伪 token（固定哨兵，用于泄漏断言）


class FakeRunner:
    """可编程 Runner：按 argv 前缀路由响应；默认 (0, "")。"""

    def __init__(self, responses: dict[tuple[str, ...], tuple[int, str]] | None = None):
        self.calls: list[list[str]] = []
        self.responses = {k: v for k, v in (responses or {}).items()}

    def run(self, args: list[str]) -> tuple[int, str]:
        self.calls.append(list(args))
        for prefix in (p for p in self.responses if len(p) <= len(args)):
            if tuple(args[: len(prefix)]) == prefix:
                return self.responses[prefix]
        return 0, ""

    def mutating_calls(self) -> list[list[str]]:
        """真实改变 Docker 状态的调用（ps/ls/version 等只读命令排除）。"""
        mutating_verbs = {"rm", "rmi", "stop", "kill", "create", "start", "restart", "build"}
        compose_mutations = ("up", "create", "start", "restart", "kill", "stop", "build")
        out: list[list[str]] = []
        for call in self.calls:
            is_mutating = (
                (len(call) > 1 and call[1] in mutating_verbs)
                or call[:3] == ["docker", "network", "rm"]
                or (call[:2] == ["docker", "compose"] and any(v in call for v in compose_mutations))
            )
            if is_mutating:
                out.append(call)
        return out


class FakeProber:
    """可编程 Prober：按 URL 精确路由状态码；None = 连接失败。"""

    def __init__(self, responses: dict[str, int | None] | None = None,
                 busy_ports: set[int] | None = None):
        self.responses = dict(responses or {})
        self.busy_ports = set(busy_ports or ())
        self.http_calls: list[tuple[str, dict[str, str]]] = []
        self.port_calls: list[tuple[str, int]] = []

    def http_get(self, url: str, headers: dict[str, str], timeout: float) -> tuple[int, str]:
        self.http_calls.append((url, dict(headers)))
        status = self.responses.get(url, 200)
        if status is None:
            raise rehearsal.RehearsalError(rehearsal.CATEGORY_TARGET, "连接失败（离线伪故障）")
        return status, ""

    def port_free(self, host: str, port: int) -> bool:
        self.port_calls.append((host, port))
        return port not in self.busy_ports


def _evidence_of(args: list[str], tmp_path: Path, runner: FakeRunner,
                 prober: FakeProber | None = None) -> tuple[int, dict[str, object], FakeRunner]:
    evidence = tmp_path / "evidence"
    argv = [*args, "--evidence-dir", str(evidence)]
    code = rehearsal.main(argv, runner=runner, prober=prober or FakeProber())
    report: dict[str, object] = {}
    json_path = evidence / "rehearsal-evidence.json"
    if json_path.is_file():
        report = json.loads(json_path.read_text(encoding="utf-8"))
    return code, report, runner


def _happy_execute_args(tmp_path: Path) -> list[str]:
    return [*EXEC, "--work-dir", str(tmp_path / "work"), *PHRASE, *EXECUTE_FLAGS]


def _all_output(runner: FakeRunner) -> str:
    return "\n".join(" ".join(call) for call in runner.calls)


# ---------------------------------------------------------------- argv 白名单 / 默认 plan


def test_default_command_is_plan_zero_calls(capsys, tmp_path: Path) -> None:
    runner = FakeRunner()
    assert rehearsal.main([], runner=runner, prober=FakeProber()) == rehearsal.EXIT_OK
    assert runner.calls == [], "plan 默认零 Docker 调用"
    out = capsys.readouterr().out
    assert "零变更零探测零 Docker" in out
    assert rehearsal.EXECUTE_CONFIRM_PHRASE in out and rehearsal.CLEANUP_CONFIRM_PHRASE in out


def test_unknown_command_rejected() -> None:
    with pytest.raises(SystemExit) as exc:
        rehearsal.main(["destroy-the-world"])
    assert exc.value.code == 2


def test_execute_requires_work_dir() -> None:
    with pytest.raises(SystemExit) as exc:
        rehearsal.main([*EXEC, *PHRASE, *EXECUTE_FLAGS])
    assert exc.value.code == 2


def test_plan_with_work_dir_validates_read_only(tmp_path: Path) -> None:
    runner = FakeRunner()
    code = rehearsal.main(["plan", "--work-dir", str(tmp_path / "work")], runner=runner)
    assert code == rehearsal.EXIT_OK
    assert not (tmp_path / "work").exists(), "plan 零写入"


def test_plan_with_repo_internal_work_dir_fails(tmp_path: Path) -> None:
    runner = FakeRunner()
    code = rehearsal.main(["plan", "--work-dir", str(REPO / "infra")], runner=runner)
    assert code == rehearsal.EXIT_FAILURE
    assert runner.calls == []


# ---------------------------------------------------------------- 确认门（fail-closed 双要素）


@pytest.mark.parametrize(
    "argv_suffix",
    [
        [],                                   # 无短语无 --execute
        EXECUTE_FLAGS,                        # 只 --execute
        PHRASE,                               # 只短语
        ["--confirm-phrase", "EXECUTE PUBLIC EDGE REHEARSAL!"],  # 错词
        ["--confirm-phrase", "execute public edge rehearsal"],   # 大小写漂移
    ],
)
def test_execute_gate_missing_element_fails_closed(argv_suffix: list[str], capsys,
                                                   tmp_path: Path) -> None:
    runner = FakeRunner()
    code, report, runner = _evidence_of(
        [*EXEC, "--work-dir", str(tmp_path / "work"), *argv_suffix], tmp_path, runner
    )
    assert code == rehearsal.EXIT_FAILURE
    err = capsys.readouterr().err
    assert rehearsal.CATEGORY_CONFIRM in err
    assert runner.calls == [], "确认门拒绝必须零 Docker 调用"
    assert not (tmp_path / "work").exists(), "确认门拒绝必须零写入"
    assert report == {}, "确认门拒绝不产生证据（纯拒绝，无副作用）"


@pytest.mark.parametrize(
    "argv_suffix",
    [
        [],
        EXECUTE_FLAGS,
        CLEAN_PHRASE,
        ["--confirm-phrase", "CLEANUP PUBLIC EDGE REHEARSA L"],
    ],
)
def test_cleanup_gate_missing_element_fails_closed(argv_suffix: list[str], capsys) -> None:
    runner = FakeRunner()
    code = rehearsal.main(["cleanup", *argv_suffix], runner=runner)
    assert code == rehearsal.EXIT_FAILURE
    assert rehearsal.CATEGORY_CONFIRM in capsys.readouterr().err
    assert runner.calls == [], "cleanup 确认门拒绝必须零清理"


# ---------------------------------------------------------------- work-dir 安全


def test_work_dir_rejects_repository_internal() -> None:
    with pytest.raises(rehearsal.RehearsalError, match="仓库内"):
        rehearsal.validate_work_dir(str(REPO / "infra" / "edge"))


def test_work_dir_rejects_repository_root() -> None:
    with pytest.raises(rehearsal.RehearsalError):
        rehearsal.validate_work_dir(str(REPO))


def test_work_dir_rejects_filesystem_root() -> None:
    anchor = Path(REPO.resolve().anchor)
    with pytest.raises(rehearsal.RehearsalError, match="文件系统根"):
        rehearsal.validate_work_dir(str(anchor))


def test_work_dir_rejects_empty() -> None:
    with pytest.raises(rehearsal.RehearsalError):
        rehearsal.validate_work_dir("  ")


def test_work_dir_rejects_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("本机无法创建符号链接（权限）")
    with pytest.raises(rehearsal.RehearsalError, match="符号链接"):
        rehearsal.validate_work_dir(str(link))


def test_work_dir_rejects_symlinked_intermediate_component(tmp_path: Path) -> None:
    """R1：链中**中间**组件是符号链接也拒绝（末组件本身是真目录）。"""
    real_parent = tmp_path / "real-parent"
    (real_parent / "leaf").mkdir(parents=True)
    link_parent = tmp_path / "link-parent"
    try:
        link_parent.symlink_to(real_parent)
    except (OSError, NotImplementedError):
        pytest.skip("本机无法创建符号链接（权限）")
    with pytest.raises(rehearsal.RehearsalError, match="符号链接"):
        rehearsal.validate_work_dir(str(link_parent / "leaf"))


def test_work_dir_rejects_symlink_in_chain_windows_safe(tmp_path: Path, monkeypatch) -> None:
    """R1（Windows 安全）：monkeypatch is_symlink 模拟中间组件为符号链接。"""
    work = tmp_path / "parent" / "leaf"
    work.mkdir(parents=True)
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "parent")
    with pytest.raises(rehearsal.RehearsalError, match="符号链接"):
        rehearsal.validate_work_dir(str(work))


def test_evidence_dir_rejects_symlink_in_chain_windows_safe(tmp_path: Path, monkeypatch) -> None:
    """R1（Windows 安全）：证据目录同款路径链防线。"""
    evidence = tmp_path / "ev-parent" / "ev-leaf"
    evidence.mkdir(parents=True)
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "ev-parent")
    with pytest.raises(rehearsal.RehearsalError, match="符号链接"):
        rehearsal.resolve_evidence_dir(str(evidence))


def test_deeply_absent_work_dir_chain_remains_valid(tmp_path: Path) -> None:
    """R1：缺省叶/缺省父目录仍合法（链检查在不存在的组件处提前放行）。"""
    deep = tmp_path / "a" / "b" / "c"
    assert rehearsal.validate_work_dir(str(deep)) == deep.resolve()
    assert not deep.exists()


def test_work_dir_rejects_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "not-a-dir"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(rehearsal.RehearsalError, match="不是目录"):
        rehearsal.validate_work_dir(str(target))


def test_work_dir_rejects_foreign_entries(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "family-photos.txt").write_text("不是彩排产物", encoding="utf-8")
    with pytest.raises(rehearsal.RehearsalError, match="非彩排条目"):
        rehearsal.validate_work_dir(str(work))


def test_work_dir_rejects_subdirectory(tmp_path: Path) -> None:
    work = tmp_path / "work"
    (work / "docker-compose.yml").mkdir(parents=True)
    with pytest.raises(rehearsal.RehearsalError):
        rehearsal.validate_work_dir(str(work))


def test_work_dir_accepts_absent_and_idempotent_reuse(tmp_path: Path) -> None:
    work = tmp_path / "work"
    assert rehearsal.validate_work_dir(str(work)) == work.resolve()
    work.mkdir()
    for name in rehearsal.REHEARSAL_ARTIFACTS:
        (work / name).write_text("stale", encoding="utf-8")
    assert rehearsal.validate_work_dir(str(work)) == work.resolve(), "纯产物集幂等复用"


def test_execute_rejects_repo_internal_work_dir(tmp_path: Path, capsys) -> None:
    runner = FakeRunner()
    code, report, runner = _evidence_of(
        [*EXEC, "--work-dir", str(REPO / "infra"), *PHRASE, *EXECUTE_FLAGS], tmp_path, runner
    )
    assert code == rehearsal.EXIT_FAILURE
    assert rehearsal.CATEGORY_WORK_DIR in capsys.readouterr().err
    assert report.get("result") == "fail"
    assert report["cleanup"] == {"attempted": False, "containers_removed": 0,
                                 "networks_removed": 0, "work_dir_removed": False,
                                 "problems": []}, "未产生变更不做 Docker 清理"


# ---------------------------------------------------------------- 项目不变量 / 清理只碰自己


def test_rendered_configs_project_invariant(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    rehearsal.render_rehearsal(work, "a" * 64)
    compose = (work / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"name: {rehearsal.COMPOSE_PROJECT}" in compose
    for forbidden in rehearsal.FORBIDDEN_PROJECTS:
        assert forbidden not in compose
        assert forbidden not in (work / "frps.toml").read_text(encoding="utf-8")
        assert forbidden not in (work / "frpc.toml").read_text(encoding="utf-8")
        assert forbidden not in (work / "Caddyfile").read_text(encoding="utf-8")


def test_loopback_high_port_bindings(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    rehearsal.render_rehearsal(work, "b" * 64)
    compose = (work / "docker-compose.yml").read_text(encoding="utf-8")
    mappings = re.findall(r"-\s*\"?127\.0.0\.1:(\d+):(\d+)\"?", compose)
    assert mappings == [(str(rehearsal.INGRESS_PORT), "80")], "唯一 host 发布 = 回环高位 → 容器 80"
    assert rehearsal.INGRESS_PORT >= rehearsal.HIGH_PORT_MIN
    assert compose.count("ports:") == 1
    assert not re.search(r"-\s*\"?(0\.0\.0\.0|\[::\]):", compose), "绝不绑非回环地址"


def test_image_pins_official_ghcr_frpc_and_digest_invariant(tmp_path: Path) -> None:
    """R3：frpc 用官方 GHCR 源 + 同 digest；三镜像 digest-pin 不变量保持。

    supervisor 实跑证实：本地 daemon 镜像加速器拒绝 docker.io/fatedier/frpc
    （DaoCloud allowlist），直连 Docker Hub 超时；ghcr.io/fatedier/frpc 为
    frp 官方镜像源，OCI index digest 与 Docker Hub 逐字节一致
    （sha256:99ece6a2…，同一构建产物——非第三方镜像）。
    """
    work = tmp_path / "work"
    work.mkdir()
    rehearsal.render_rehearsal(work, "9" * 64)
    compose = (work / "docker-compose.yml").read_text(encoding="utf-8")
    images = re.findall(r"image:\s+(\S+)", compose)
    assert len(images) == 3, "恰好三个镜像引用"
    assert (
        "ghcr.io/fatedier/frpc@sha256:"
        "99ece6a2b62cfc68731e0df289af804ff1c699911cfc47871856434f1d6d53ee"
    ) in images, "frpc 必须精确使用官方 GHCR 引用 + 已核实 digest"
    assert (
        "docker.io/library/caddy@sha256:"
        "6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b"
    ) in images, "caddy 保持生产同源同 digest（R3 不改）"
    assert (
        "docker.io/fatedier/frps@sha256:"
        "cd8b947ba61678b200baa4f71ccc33f3c52e4e2cc0059700ba3b8354e36af7c3"
    ) in images, "frps 保持生产同源同 digest（R3 不改）"
    assert all("@sha256:" in reference for reference in images), "全部镜像 digest-pin"


def test_caddyfile_http_only_and_hosts(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    rehearsal.render_rehearsal(work, "c" * 64)
    body = "\n".join(
        line for line in (work / "Caddyfile").read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    assert ":80 {" in body
    assert "auto_https off" in body
    assert not re.search(r"\b443\b", body)
    assert not re.search(r"^\s*tls\s", body, re.MULTILINE)
    assert rehearsal.APP_HOST in body and rehearsal.API_HOST in body
    assert f"reverse_proxy frps:{rehearsal.FRPS_VHOST_PORT}" in body


def test_frp_invariants_match_production_discipline(tmp_path: Path) -> None:
    import tomllib

    work = tmp_path / "work"
    work.mkdir()
    rehearsal.render_rehearsal(work, "d" * 64)
    frps = tomllib.loads((work / "frps.toml").read_text(encoding="utf-8"))
    frpc = tomllib.loads((work / "frpc.toml").read_text(encoding="utf-8"))
    assert frps["transport"]["tls"]["force"] is True
    assert frps["auth"]["tokenSource"]["type"] == "file"
    assert "token" not in frps["auth"]
    assert frpc["transport"]["tls"]["enable"] is True
    assert frpc["auth"]["tokenSource"]["type"] == "file"
    assert "token" not in frpc["auth"]
    domains = {d for p in frpc["proxies"] for d in p["customDomains"]}
    targets = {(p["localIP"], p["localPort"]) for p in frpc["proxies"]}
    assert domains == {rehearsal.APP_HOST, rehearsal.API_HOST}
    assert targets == {("host.docker.internal", 3012), ("host.docker.internal", 8000)}


def test_validate_rendered_configs_rejects_tampering(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    rehearsal.render_rehearsal(work, "f" * 64)
    tamperings = [
        ("frps.toml", "transport.tls.force = true", "transport.tls.force = false"),
        ("frpc.toml", "serverAddr = \"frps\"", "serverAddr = \"evil\""),
        ("docker-compose.yml", '"127.0.0.1:39443:80"', '"0.0.0.0:39443:80"'),
        ("docker-compose.yml", f"name: {rehearsal.COMPOSE_PROJECT}", "name: other-project"),
        ("Caddyfile", "auto_https off", "auto_https on"),
    ]
    for name, old, new in tamperings:
        original = (work / name).read_text(encoding="utf-8")
        (work / name).write_text(original.replace(old, new), encoding="utf-8")
        with pytest.raises(rehearsal.RehearsalError) as excinfo:
            rehearsal.validate_rendered_configs(work)
        assert excinfo.value.category == rehearsal.CATEGORY_RENDER
        (work / name).write_text(original, encoding="utf-8")
    rehearsal.validate_rendered_configs(work)


def test_happy_path_only_touches_own_project(tmp_path: Path) -> None:
    runner = FakeRunner(responses={
        ("docker", "ps"): (0, "abc123\ndef456\n"),
        ("docker", "network", "ls"): (0, "net789\n"),
    })
    code, report, runner = _evidence_of(_happy_execute_args(tmp_path), tmp_path, runner)
    assert code == rehearsal.EXIT_OK, _all_output(runner)
    assert report["result"] == "pass"
    assert report["public_ready"] is False
    compose_calls = [c for c in runner.calls if c[:2] == ["docker", "compose"] and "up" in c]
    assert compose_calls, "必须真实发起 compose up"
    for call in compose_calls:
        assert "-p" in call and call[call.index("-p") + 1] == rehearsal.COMPOSE_PROJECT
    label_calls = [c for c in runner.calls if "--filter" in c]
    assert label_calls
    for call in label_calls:
        assert call[call.index("--filter") + 1] == rehearsal.PROJECT_LABEL_FILTER
    removed = [c for c in runner.calls if c[:3] == ["docker", "rm", "-f"]]
    assert {c[3] for c in removed} == {"abc123", "def456"}
    network_rm = next(c for c in runner.calls if c[:3] == ["docker", "network", "rm"])
    assert network_rm[3] == "net789"
    for forbidden in rehearsal.FORBIDDEN_PROJECTS:
        assert forbidden not in _all_output(runner)
    assert not (tmp_path / "work").exists(), "一次性 work-dir 收尾删除"


def test_cleanup_subcommand_scoped_and_idempotent(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "docker-compose.yml").write_text("stale", encoding="utf-8")
    runner = FakeRunner(responses={
        ("docker", "ps"): (0, "cid1\n"),
        ("docker", "network", "ls"): (0, ""),
    })
    code = rehearsal.main(
        ["cleanup", "--work-dir", str(work), *CLEAN_PHRASE, *EXECUTE_FLAGS], runner=runner
    )
    assert code == rehearsal.EXIT_OK
    filters = [c[c.index("--filter") + 1] for c in runner.calls if "--filter" in c]
    assert filters and set(filters) == {rehearsal.PROJECT_LABEL_FILTER}
    assert [c for c in runner.calls if c[:3] == ["docker", "rm", "-f"] and len(c) == 4] == [
        ["docker", "rm", "-f", "cid1"]
    ]
    assert not work.exists(), "契约内 work-dir 删除"
    for forbidden in rehearsal.FORBIDDEN_PROJECTS:
        assert forbidden not in _all_output(runner)


def test_cleanup_reports_query_failure(tmp_path: Path, capsys) -> None:
    runner = FakeRunner(responses={("docker", "ps"): (1, "docker daemon down")})
    code = rehearsal.main(["cleanup", *CLEAN_PHRASE, *EXECUTE_FLAGS], runner=runner)
    assert code == rehearsal.EXIT_FAILURE
    assert rehearsal.CATEGORY_CLEANUP in capsys.readouterr().err


def test_status_is_read_only(tmp_path: Path) -> None:
    runner = FakeRunner(responses={
        ("docker", "ps"): (0, "aios-m14-156-edge-rehearsal-caddy-1\n"),
        ("docker", "network", "ls"): (0, ""),
    })
    code = rehearsal.main(["status", "--work-dir", str(tmp_path / "work")], runner=runner)
    assert code == rehearsal.EXIT_OK
    assert runner.mutating_calls() == [], "status 零变更"
    assert all("--filter" in c for c in runner.calls), "只做标签过滤查询"


def test_status_reports_docker_failure(tmp_path: Path, capsys) -> None:
    runner = FakeRunner(responses={("docker", "ps"): (1, "boom")})
    code = rehearsal.main(["status"], runner=runner)
    assert code == rehearsal.EXIT_FAILURE
    assert rehearsal.CATEGORY_DOCKER in capsys.readouterr().err


def test_status_fails_closed_when_network_ls_fails(tmp_path: Path, capsys) -> None:
    """R1：network ls 返回码同样 fail-closed（只读查询不猜测状态）。"""
    runner = FakeRunner(responses={
        ("docker", "ps"): (0, ""),
        ("docker", "network", "ls"): (1, "daemon degraded"),
    })
    code = rehearsal.main(["status"], runner=runner)
    assert code == rehearsal.EXIT_FAILURE
    assert rehearsal.CATEGORY_DOCKER in capsys.readouterr().err


# ---------------------------------------------------------------- preflight fail-closed


def _prefail_execute(tmp_path: Path, runner: FakeRunner, prober: FakeProber, capsys,
                     extra_args: list[str] | None = None) -> str:
    code, _, _ = _evidence_of([*_happy_execute_args(tmp_path), *(extra_args or [])],
                              tmp_path, runner, prober)
    assert code == rehearsal.EXIT_FAILURE
    return capsys.readouterr().err


def test_preflight_rejects_missing_docker_cli(tmp_path: Path, capsys) -> None:
    err = _prefail_execute(
        tmp_path, FakeRunner(responses={("docker",): (127, "FileNotFoundError")}),
        FakeProber(), capsys,
    )
    assert rehearsal.CATEGORY_DOCKER in err


def test_preflight_rejects_missing_compose_plugin(tmp_path: Path, capsys) -> None:
    err = _prefail_execute(
        tmp_path, FakeRunner(responses={("docker", "compose"): (1, "unknown command")}),
        FakeProber(), capsys,
    )
    assert rehearsal.CATEGORY_DOCKER in err


def test_preflight_rejects_api_target_unreachable(tmp_path: Path, capsys) -> None:
    err = _prefail_execute(
        tmp_path, FakeRunner(),
        FakeProber(responses={"http://127.0.0.1:8000/health": None}), capsys,
    )
    assert rehearsal.CATEGORY_TARGET in err


def test_preflight_rejects_web_target_unhealthy(tmp_path: Path, capsys) -> None:
    err = _prefail_execute(
        tmp_path, FakeRunner(),
        FakeProber(responses={"http://127.0.0.1:3012/": 502}), capsys,
    )
    assert rehearsal.CATEGORY_TARGET in err


def test_preflight_rejects_busy_ingress_port(tmp_path: Path, capsys) -> None:
    err = _prefail_execute(
        tmp_path, FakeRunner(),
        FakeProber(busy_ports={rehearsal.INGRESS_PORT}), capsys,
    )
    assert rehearsal.CATEGORY_PORT in err


def test_preflight_failure_leaves_no_work_dir_and_no_cleanup(tmp_path: Path) -> None:
    runner = FakeRunner()
    code, report, runner = _evidence_of(
        _happy_execute_args(tmp_path), tmp_path, runner,
        FakeProber(responses={"http://127.0.0.1:8000/health": None}),
    )
    assert code == rehearsal.EXIT_FAILURE
    assert not (tmp_path / "work").exists()
    # preflight 只有两条 docker --version / compose version 只读探测
    assert all(c[:2] == ["docker", "compose"] or c == ["docker", "--version"]
               for c in runner.calls)
    assert report["cleanup"]["attempted"] is False
    assert report["result"] == "fail" and report["public_ready"] is False


def test_compose_up_failure_fails_closed_with_cleanup(tmp_path: Path, capsys) -> None:
    runner = FakeRunner(responses={
        ("docker", "compose", "-f"): (1, "up failed"),
        ("docker", "ps"): (0, "leaked1\n"),
        ("docker", "network", "ls"): (0, ""),
    })
    code, report, runner = _evidence_of(_happy_execute_args(tmp_path), tmp_path, runner)
    assert code == rehearsal.EXIT_FAILURE
    assert rehearsal.CATEGORY_COMPOSE in capsys.readouterr().err
    assert report["cleanup"]["containers_removed"] == 1, "up 失败也要 scoped 清理"
    assert not (tmp_path / "work").exists()


def test_render_failure_removes_work_dir_via_scoped_cleanup(tmp_path: Path, capsys,
                                                             monkeypatch) -> None:
    """R1：mkdir 成功后任何 render 失败都必须进入 scoped 清理（不留半成品 work-dir）。"""

    def failing_render(work_dir, token, api_port, web_port):
        raise rehearsal.RehearsalError(rehearsal.CATEGORY_RENDER, "模板不可读（离线伪故障）")

    monkeypatch.setattr(rehearsal, "render_rehearsal", failing_render)
    runner = FakeRunner(responses={("docker", "ps"): (0, ""), ("docker", "network", "ls"): (0, "")})
    code, report, runner = _evidence_of(_happy_execute_args(tmp_path), tmp_path, runner)
    assert code == rehearsal.EXIT_FAILURE
    assert rehearsal.CATEGORY_RENDER in capsys.readouterr().err
    assert not (tmp_path / "work").exists(), "render 失败不得留下半成品 work-dir"
    assert report["cleanup"]["attempted"] is True
    assert report["cleanup"]["work_dir_removed"] is True
    assert report["result"] == "fail" and report["public_ready"] is False


def test_probe_failure_records_evidence_fail(tmp_path: Path) -> None:
    runner = FakeRunner(responses={
        ("docker", "ps"): (0, ""),
        ("docker", "network", "ls"): (0, ""),
    })
    ingress = f"http://127.0.0.1:{rehearsal.INGRESS_PORT}"
    prober = FakeProber(responses={f"{ingress}/health": 502})
    code, report, _ = _evidence_of(
        [*_happy_execute_args(tmp_path), "--health-timeout", "0"], tmp_path, runner, prober
    )
    assert code == rehearsal.EXIT_FAILURE
    assert report["result"] == "fail"
    assert report["public_ready"] is False
    api_probe = [p for p in report["probes"] if p["route"] == "api"]
    assert api_probe and api_probe[0]["status_code"] == 502 and api_probe[0]["ok"] is False


# ---------------------------------------------------------------- secret 纪律


def test_token_never_leaks_into_output_or_evidence(tmp_path: Path, capsys,
                                                    monkeypatch) -> None:
    monkeypatch.setattr(rehearsal.secrets, "token_hex", lambda _: SENTINEL_TOKEN)
    runner = FakeRunner(responses={("docker", "ps"): (0, ""), ("docker", "network", "ls"): (0, "")})
    code, report, runner = _evidence_of(_happy_execute_args(tmp_path), tmp_path, runner)
    assert code == rehearsal.EXIT_OK
    captured = capsys.readouterr()
    haystacks = [
        captured.out, captured.err, _all_output(runner),
        json.dumps(report, ensure_ascii=False),
    ]
    evidence_dir = tmp_path / "evidence"
    for name in ("rehearsal-evidence.json", "rehearsal-report.md"):
        haystacks.append((evidence_dir / name).read_text(encoding="utf-8"))
    for haystack in haystacks:
        assert SENTINEL_TOKEN not in haystack
        assert SENTINEL_TOKEN[:16] not in haystack, "token 前缀也不得泄漏"
    facts = report["token_facts"]
    assert facts == {"bytes": rehearsal.TOKEN_BYTES, "hex_length": 64,
                     "file_name": rehearsal.TOKEN_FILE_NAME, "value_printed": False}


def test_render_writes_token_file_only_as_secret_material(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    written = rehearsal.render_rehearsal(work, SENTINEL_TOKEN)
    assert written == ["docker-compose.yml", "frps.toml", "frpc.toml",
                       "Caddyfile", rehearsal.TOKEN_FILE_NAME]
    token_file = work / rehearsal.TOKEN_FILE_NAME
    assert token_file.read_text(encoding="utf-8").strip() == SENTINEL_TOKEN
    if os.name == "posix":
        assert token_file.stat().st_mode & 0o777 == 0o600
    for name in ("docker-compose.yml", "frps.toml", "frpc.toml", "Caddyfile"):
        assert SENTINEL_TOKEN not in (work / name).read_text(encoding="utf-8"), (
            f"{name} 不得内嵌 token"
        )


def test_render_rejects_malformed_token(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    for bad in ("", "short", "z" * 64, "a" * 63):
        with pytest.raises(rehearsal.RehearsalError) as excinfo:
            rehearsal.render_rehearsal(work, bad)
        assert excinfo.value.category == rehearsal.CATEGORY_RENDER


def test_tokens_are_fresh_per_render(tmp_path: Path) -> None:
    import secrets as real_secrets

    work = tmp_path / "work"
    work.mkdir()
    rehearsal.render_rehearsal(work, real_secrets.token_hex(32))
    first = (work / rehearsal.TOKEN_FILE_NAME).read_text(encoding="utf-8").strip()
    rehearsal.render_rehearsal(work, real_secrets.token_hex(32))
    second = (work / rehearsal.TOKEN_FILE_NAME).read_text(encoding="utf-8").strip()
    assert first != second, "一次性 token 每次渲染必须重新随机"
    assert re.fullmatch(r"[0-9a-f]{64}", second)


# ---------------------------------------------------------------- 证据原子性 / 目录防线


def test_atomic_write_failure_preserves_original_and_leaves_no_tmp(
        tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "rehearsal-evidence.json"
    target.write_text('{"original": true}', encoding="utf-8")
    real_replace = os.replace

    def failing_replace(src: str, dst: str) -> None:
        raise OSError("disk full（离线伪故障）")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError):
        rehearsal._atomic_write(target, '{"original": false}', 0o644)
    monkeypatch.setattr(os, "replace", real_replace)
    assert json.loads(target.read_text(encoding="utf-8")) == {"original": True}
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], "失败不留 .tmp 半成品"


def test_evidence_dir_rejects_tracked_repo_area(tmp_path: Path, capsys) -> None:
    runner = FakeRunner()
    code = rehearsal.main(
        [*_happy_execute_args(tmp_path), "--evidence-dir", str(REPO / "docs")],
        runner=runner,
    )
    assert code == rehearsal.EXIT_FAILURE
    assert rehearsal.CATEGORY_EVIDENCE_DIR in capsys.readouterr().err
    assert not (tmp_path / "work").exists(), "证据目录防线先于任何写入"


def test_evidence_dir_accepts_verify_default(tmp_path: Path) -> None:
    resolved = rehearsal.resolve_evidence_dir(None)
    assert resolved == rehearsal.DEFAULT_EVIDENCE_DIR
    inside = rehearsal.REPO_ROOT / ".verify" / "artifacts" / "x"
    assert rehearsal.resolve_evidence_dir(str(inside)) == inside.resolve()


def test_evidence_written_atomically_on_both_paths(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    ok_code, ok_report, _ = _evidence_of(_happy_execute_args(tmp_path), tmp_path, FakeRunner())
    assert ok_code == rehearsal.EXIT_OK
    for name in ("rehearsal-evidence.json", "rehearsal-report.md"):
        assert (evidence / name).is_file()
    assert "rehearsal-evidence.json" in ok_report["stages"][-1]["detail"]
    markdown = (evidence / "rehearsal-report.md").read_text(encoding="utf-8")
    assert "public_ready = False" in markdown
    assert rehearsal.COMPOSE_PROJECT in markdown and rehearsal.APP_HOST in markdown


# ---------------------------------------------------------------- R2 目标端口覆写


def test_default_target_ports_used_without_flags(tmp_path: Path) -> None:
    code, report, _ = _evidence_of(_happy_execute_args(tmp_path), tmp_path, FakeRunner())
    assert code == rehearsal.EXIT_OK
    targets = report["targets"]
    assert targets["api_port"] == 8000 and targets["web_port"] == 3012
    assert targets["defaults_used"] is True
    assert targets["api"] == "host.docker.internal:8000"
    assert targets["web"] == "host.docker.internal:3012"


def test_valid_port_overrides_thread_through_everything(tmp_path: Path, capsys,
                                                         monkeypatch) -> None:
    work = tmp_path / "work"
    argv = [
        *EXEC, "--work-dir", str(work), *PHRASE, *EXECUTE_FLAGS,
        "--host-api-port", "18000", "--host-web-port", "13011",
        "--evidence-dir", str(tmp_path / "evidence"),
    ]
    captured: dict[str, str] = {}
    real_cleanup = rehearsal.scoped_cleanup

    def capturing_cleanup(runner, work_dir, log):
        if work_dir is not None and (work_dir / "frpc.toml").is_file():
            captured["frpc"] = (work_dir / "frpc.toml").read_text(encoding="utf-8")
        return real_cleanup(runner, work_dir, log)

    monkeypatch.setattr(rehearsal, "scoped_cleanup", capturing_cleanup)
    prober = FakeProber()
    code = rehearsal.main(argv, runner=FakeRunner(), prober=prober)
    assert code == rehearsal.EXIT_OK
    # preflight URL 构造使用覆写端口（仍为回环 + 路径固定）
    urls = {url for url, _headers in prober.http_calls}
    assert "http://127.0.0.1:18000/health" in urls
    assert "http://127.0.0.1:13011/" in urls
    assert "http://127.0.0.1:8000/health" not in urls
    # frpc 渲染按覆写端口注入 localPort（收尾清理删除 work-dir 前已快照）
    import tomllib

    frpc = tomllib.loads(captured["frpc"])
    targets = {(p["localIP"], p["localPort"]) for p in frpc["proxies"]}
    assert targets == {("host.docker.internal", 13011), ("host.docker.internal", 18000)}
    # 证据显式记录覆写端口 + defaults_used=False
    report = json.loads((tmp_path / "evidence" / "rehearsal-evidence.json")
                        .read_text(encoding="utf-8"))
    assert report["targets"]["api_port"] == 18000
    assert report["targets"]["web_port"] == 13011
    assert report["targets"]["defaults_used"] is False
    markdown = (tmp_path / "evidence" / "rehearsal-report.md").read_text(encoding="utf-8")
    assert "18000" in markdown and "13011" in markdown
    assert "覆写端口" in markdown, "MD 必须明示非默认，防止伪装成默认映射验证"


@pytest.mark.parametrize("bad", ["abc", "8000/x", "0", "65536", "-1", "1.5", "9999999999"])
def test_invalid_api_port_fails_closed_no_traceback(bad: str, tmp_path: Path, capsys) -> None:
    runner = FakeRunner()
    code = rehearsal.main(
        ["plan", f"--host-api-port={bad}"], runner=runner
    )
    assert code == rehearsal.EXIT_FAILURE
    err = capsys.readouterr().err
    assert rehearsal.CATEGORY_PORT_ARG in err
    assert "Traceback" not in err
    assert bad not in err, "错误不回显原始输入（防注入面）"
    assert runner.calls == [], "端口解析失败零 Docker 调用"


def test_blank_api_port_fails_closed(capsys) -> None:
    code = rehearsal.main(["plan", "--host-api-port="])
    assert code == rehearsal.EXIT_FAILURE
    assert rehearsal.CATEGORY_PORT_ARG in capsys.readouterr().err


def test_equal_target_ports_rejected(capsys) -> None:
    code = rehearsal.main(["plan", "--host-api-port", "9000", "--host-web-port", "9000"])
    assert code == rehearsal.EXIT_FAILURE
    assert rehearsal.CATEGORY_PORT_ARG in capsys.readouterr().err


def test_boundary_ports_accepted(tmp_path: Path) -> None:
    assert rehearsal.parse_host_port("1", 8000, "--host-api-port") == 1
    assert rehearsal.parse_host_port("65535", 8000, "--host-api-port") == 65535
    assert rehearsal.parse_host_port(None, 3012, "--host-web-port") == 3012
    assert rehearsal.parse_host_port(" 8080 ", 8000, "--host-api-port") == 8080


def test_override_preflight_failure_uses_overridden_url(tmp_path: Path, capsys) -> None:
    """preflight URL 构造：覆写端口不可达 → target-unreachable（URL 即覆写值）。"""
    err = _prefail_execute(
        tmp_path, FakeRunner(),
        FakeProber(responses={"http://127.0.0.1:18000/health": None}), capsys,
        extra_args=["--host-api-port", "18000", "--host-web-port", "13011"],
    )
    assert rehearsal.CATEGORY_TARGET in err


def test_frpc_validation_rejects_wrong_port_under_override(tmp_path: Path) -> None:
    """frpc 目标校验：渲染端口与配置不符（篡改）→ render-failed。"""
    work = tmp_path / "work"
    work.mkdir()
    rehearsal.render_rehearsal(work, "a" * 64, api_port=18000, web_port=13011)
    rehearsal.validate_rendered_configs(work, api_port=18000, web_port=13011)
    tampered = (work / "frpc.toml").read_text(encoding="utf-8").replace(
        "localPort = 13011", "localPort = 3011")
    (work / "frpc.toml").write_text(tampered, encoding="utf-8")
    with pytest.raises(rehearsal.RehearsalError) as excinfo:
        rehearsal.validate_rendered_configs(work, api_port=18000, web_port=13011)
    assert excinfo.value.category == rehearsal.CATEGORY_RENDER
    # 默认口径下同一文件同样拒绝（web 槽位回到 3011 而 api 槽位仍是 18000 —— 端口错位）
    with pytest.raises(rehearsal.RehearsalError):
        rehearsal.validate_rendered_configs(work)


def test_plan_and_status_surface_effective_ports(capsys, tmp_path: Path) -> None:
    assert rehearsal.main(["plan"]) == rehearsal.EXIT_OK
    out = capsys.readouterr().out
    assert "8000(API)/3012(Web)（默认端口）" in out
    assert rehearsal.main(
        ["plan", "--host-api-port", "18000", "--host-web-port", "13011"]
    ) == rehearsal.EXIT_OK
    out = capsys.readouterr().out
    assert "18000(API)/13011(Web)" in out and "覆写端口" in out
    runner = FakeRunner()
    assert rehearsal.main(
        ["status", "--host-api-port", "18000", "--host-web-port", "13011"], runner=runner
    ) == rehearsal.EXIT_OK
    out = capsys.readouterr().out
    assert "API host.docker.internal:18000 / Web host.docker.internal:13011" in out
    assert "覆写端口" in out


# ---------------------------------------------------------------- 命令边界注入（收尾契约）


def test_real_implementations_exist_but_tests_never_call_them() -> None:
    assert callable(rehearsal.RealRunner().run)
    assert callable(rehearsal.RealProber().http_get)
    assert callable(rehearsal.RealProber().port_free)


def test_probes_use_rehearsal_host_headers(tmp_path: Path) -> None:
    runner = FakeRunner(responses={("docker", "ps"): (0, ""), ("docker", "network", "ls"): (0, "")})
    prober = FakeProber()
    code, _report, _ = _evidence_of(_happy_execute_args(tmp_path), tmp_path, runner, prober)
    assert code == rehearsal.EXIT_OK
    headers_by_url = {url: headers for url, headers in prober.http_calls}
    assert headers_by_url.get(f"http://127.0.0.1:{rehearsal.INGRESS_PORT}/") == {
        "Host": rehearsal.APP_HOST}
    assert headers_by_url.get(f"http://127.0.0.1:{rehearsal.INGRESS_PORT}/health") == {
        "Host": rehearsal.API_HOST}
    # preflight 的家机目标探测（无 Host 覆写）
    assert ("http://127.0.0.1:8000/health" in headers_by_url)
    assert ("http://127.0.0.1:3012/" in headers_by_url)
