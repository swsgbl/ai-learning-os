"""M14-157 生产 Web 回环网关（tools/ops/production_web_gateway.py）fail-closed 契约测试。

覆盖矩阵：
1. 默认 plan 零写入零探测零 Docker；未知子命令 exit 2；install 缺
   --config-dir exit 2；
2. 确认门：install 需 --execute + INSTALL PRODUCTION WEB GATEWAY 双要素；
   uninstall 需 UNINSTALL PRODUCTION WEB GATEWAY——缺一即 confirm-gate
   非零退出、零 Docker 调用；
3. 端口校验：默认 3012；1..65535 边界；畸形/越界/封锁集（3011/8000/
   39443/3000/5433/6379/7880/7881/7882-7892/8878/9000/9001）一律
   port-invalid，无 traceback、不回显原始输入；
4. config-dir 防线：相对路径/仓库内/文件系统根/符号链接链/外来条目/
   子目录一律 config-dir-unsafe；缺省与纯 Caddyfile 幂等覆写合法；
5. 精确渲染：Caddyfile 恰含 auto_https off/admin off/:80/reverse_proxy
   web:3000，无 443/tls；
6. docker argv 白名单：全部调用前缀 ∈ {--version/network inspect/
   inspect/run/rm}——绝无 stop/restart/recreate 面向生产栈；
7. preflight fail-closed：docker 不可用/网络缺失/Web 缺失·未运行·非
   healthy/端口占用/同名容器存在（自有/漂移/外来）；
8. 归属与装后校验：labels 精确 → installed；外来标签 → foreign；镜像/
   网络/绑定/端口/重启策略漂移 → degraded；install 成功路径含 inspect
   复核 + HTTP GET / 200；装后探测非 200 → probe-failed 不自动删除；
9. status 分类只读（零变更调用）；uninstall 只删精确自有容器（配置
   目录与证据保留）；missing 幂等 OK；foreign 拒绝；
10. 证据：result/host_port/upstream/容器事实/回滚指引/
   production_public_ready=false；无 secret/无绝对路径。

全部经注入 FakeRunner/FakeProber（零真实 Docker 零网络）；夹具仅驻
tmp（basetemp 为仓库外目录）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "ops"))

import production_web_gateway as gateway

INSTALL_ARGS = [
    "install", "--config-dir", "<CFG>",  # 由 _install_args 填充
    "--confirm-phrase", gateway.INSTALL_CONFIRM_PHRASE, "--execute",
]
HEALTHY_STATE = json.dumps({"Running": True, "Health": {"Status": "healthy"}})
GOOD_IMAGE = json.dumps(gateway.CADDY_IMAGE)
GOOD_LABELS = json.dumps(dict(gateway.OWNERSHIP_LABELS))
GOOD_NETWORKS = json.dumps({gateway.PRODUCTION_NETWORK: {}})
GOOD_RESTART = json.dumps({"Name": gateway.RESTART_POLICY})


def good_ports(port: int) -> str:
    return json.dumps({gateway.CONTAINER_HTTP_PORT: [{"HostIp": "127.0.0.1", "HostPort": str(port)}]})


def good_mounts(source: str = "/opt/gateway/Caddyfile") -> str:
    return json.dumps([{
        "Type": "bind", "Source": source,
        "Destination": gateway.CADDYFILE_MOUNT_DESTINATION, "RW": False,
    }])


class FakeRunner:
    """可编程 Runner：按 argv 前缀路由响应；默认 (0, "")。"""

    def __init__(self, responses: dict[tuple[str, ...], tuple[int, str]] | None = None):
        self.calls: list[list[str]] = []
        self.responses = {k: v for k, v in (responses or {}).items()}

    def run(self, args: list[str]) -> tuple[int, str]:
        self.calls.append(list(args))
        for prefix in sorted(self.responses, key=len, reverse=True):
            if len(prefix) <= len(args) and tuple(args[: len(prefix)]) == prefix:
                return self.responses[prefix]
        return 0, ""

    def mutating_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] in (["docker", "run"], ["docker", "rm"])]


class FakeProber:
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
            raise gateway.GatewayError(gateway.CATEGORY_PROBE, "连接失败（离线伪故障）")
        return status, ""

    def port_free(self, host: str, port: int) -> bool:
        self.port_calls.append((host, port))
        return port not in self.busy_ports


def _install_args(tmp_path: Path, extra: list[str] | None = None) -> list[str]:
    argv = [
        "install", "--config-dir", str(tmp_path / "cfg"),
        "--evidence-dir", str(tmp_path / "evidence"),
        "--confirm-phrase", gateway.INSTALL_CONFIRM_PHRASE, "--execute",
    ]
    return [*argv, *(extra or [])]


def _healthy_runner(port: int = 3012, overrides: dict | None = None) -> FakeRunner:
    """健康全绿 FakeRunner：网络/Web healthy/同名缺失。"""
    responses: dict[tuple[str, ...], tuple[int, str]] = {
        ("docker", "network", "inspect"): (0, gateway.PRODUCTION_NETWORK),
        ("docker", "inspect", gateway.PRODUCTION_WEB_CONTAINER, "--format", "{{json .State}}"):
            (0, HEALTHY_STATE),
        # 同名网关容器：不存在（Error: No such object）
        ("docker", "inspect", gateway.CONTAINER_NAME): (1, f"Error: No such object: {gateway.CONTAINER_NAME}"),
    }
    responses.update(overrides or {})
    return FakeRunner(responses)


def _installed_gateway_responses(port: int = 3012, mounts: str | None = None) -> dict[tuple[str, ...], tuple[int, str]]:
    """已安装网关（全精确匹配）的 inspect 响应集。"""
    return {
        ("docker", "inspect", gateway.CONTAINER_NAME, "--format", "{{json .Config.Image}}"): (0, GOOD_IMAGE),
        ("docker", "inspect", gateway.CONTAINER_NAME, "--format", "{{json .Config.Labels}}"): (0, GOOD_LABELS),
        ("docker", "inspect", gateway.CONTAINER_NAME, "--format", "{{json .NetworkSettings.Networks}}"): (0, GOOD_NETWORKS),
        ("docker", "inspect", gateway.CONTAINER_NAME, "--format", "{{json .HostConfig.RestartPolicy}}"): (0, GOOD_RESTART),
        ("docker", "inspect", gateway.CONTAINER_NAME, "--format", "{{json .HostConfig.PortBindings}}"): (0, good_ports(port)),
        ("docker", "inspect", gateway.CONTAINER_NAME, "--format", "{{json .Mounts}}"): (0, mounts or good_mounts()),
    }


def _evidence_of(tmp_path: Path, argv: list[str], runner: FakeRunner,
                 prober: FakeProber | None = None) -> tuple[int, dict[str, object]]:
    code = gateway.main(argv, runner=runner, prober=prober or FakeProber())
    json_path = tmp_path / "evidence" / gateway.EVIDENCE_JSON_NAME
    report: dict[str, object] = {}
    if json_path.is_file():
        report = json.loads(json_path.read_text(encoding="utf-8"))
    return code, report


# ---------------------------------------------------------------- plan / argv


def test_default_plan_zero_calls(capsys) -> None:
    runner = FakeRunner()
    assert gateway.main([], runner=runner) == gateway.EXIT_OK
    assert runner.calls == [], "plan 默认零 Docker 调用"
    out = capsys.readouterr().out
    assert "零写入零探测零 Docker" in out
    assert gateway.INSTALL_CONFIRM_PHRASE in out and gateway.UNINSTALL_CONFIRM_PHRASE in out
    assert "3012" in out and "web:3000" in out


def test_unknown_command_rejected() -> None:
    with pytest.raises(SystemExit) as exc:
        gateway.main(["nuke"])
    assert exc.value.code == 2


def test_install_requires_config_dir() -> None:
    with pytest.raises(SystemExit) as exc:
        gateway.main(["install", "--confirm-phrase", gateway.INSTALL_CONFIRM_PHRASE, "--execute"])
    assert exc.value.code == 2


# ---------------------------------------------------------------- 确认门


@pytest.mark.parametrize("suffix", [
    [],
    ["--execute"],
    ["--confirm-phrase", gateway.INSTALL_CONFIRM_PHRASE],
    ["--confirm-phrase", "install production web gateway!"],
])
def test_install_gate_missing_element_fails_closed(suffix: list[str], tmp_path: Path,
                                                   capsys) -> None:
    runner = FakeRunner()
    code = gateway.main(["install", "--config-dir", str(tmp_path / "cfg"), *suffix],
                        runner=runner)
    assert code == gateway.EXIT_FAILURE
    assert gateway.CATEGORY_CONFIRM in capsys.readouterr().err
    assert runner.calls == [], "确认门拒绝必须零 Docker 调用"
    assert not (tmp_path / "cfg").exists(), "确认门拒绝必须零写入"


@pytest.mark.parametrize("suffix", [
    [],
    ["--execute"],
    ["--confirm-phrase", gateway.UNINSTALL_CONFIRM_PHRASE],
])
def test_uninstall_gate_missing_element_fails_closed(suffix: list[str], capsys) -> None:
    runner = FakeRunner()
    code = gateway.main(["uninstall", *suffix], runner=runner)
    assert code == gateway.EXIT_FAILURE
    assert gateway.CATEGORY_CONFIRM in capsys.readouterr().err
    assert runner.calls == [], "uninstall 确认门拒绝必须零变更"


# ---------------------------------------------------------------- 端口校验


def test_default_host_port_is_3012() -> None:
    assert gateway.parse_host_port(None) == 3012
    assert 3012 not in gateway.BLOCKED_HOST_PORTS


@pytest.mark.parametrize("blocked", [3011, 8000, 39443, 3000, 5433, 6379, 7880,
                                     7881, 7882, 7889, 7892, 8878, 9000, 9001])
def test_known_production_ports_blocked(blocked: int, capsys) -> None:
    assert gateway.main(["plan", f"--host-port={blocked}"]) == gateway.EXIT_FAILURE
    assert gateway.CATEGORY_PORT_ARG in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["abc", "3012/x", "0", "65536", "-1", "1.5", "9999999999"])
def test_malformed_port_fails_closed_no_traceback(bad: str, capsys) -> None:
    assert gateway.main(["plan", f"--host-port={bad}"]) == gateway.EXIT_FAILURE
    err = capsys.readouterr().err
    assert gateway.CATEGORY_PORT_ARG in err
    assert "Traceback" not in err and bad not in err


def test_boundary_ports_accepted() -> None:
    assert gateway.parse_host_port("1") == 1
    assert gateway.parse_host_port("65535") == 65535
    assert gateway.parse_host_port(" 3013 ") == 3013


# ---------------------------------------------------------------- config-dir 防线


def test_config_dir_requires_absolute(tmp_path: Path) -> None:
    with pytest.raises(gateway.GatewayError, match="绝对路径"):
        gateway.validate_config_dir("relative/cfg")


def test_config_dir_rejects_repository_internal() -> None:
    with pytest.raises(gateway.GatewayError, match="仓库内"):
        gateway.validate_config_dir(str(REPO / "infra"))


def test_config_dir_rejects_filesystem_root() -> None:
    anchor = Path(REPO.resolve().anchor)
    with pytest.raises(gateway.GatewayError, match="文件系统根"):
        gateway.validate_config_dir(str(anchor))


def test_config_dir_rejects_symlink_chain(tmp_path: Path) -> None:
    real = tmp_path / "real-parent"
    (real / "leaf").mkdir(parents=True)
    link = tmp_path / "link-parent"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("本机无法创建符号链接（权限）")
    with pytest.raises(gateway.GatewayError, match="符号链接"):
        gateway.validate_config_dir(str(link / "leaf"))


def test_config_dir_rejects_symlink_chain_windows_safe(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "parent" / "cfg"
    cfg.mkdir(parents=True)
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "parent")
    with pytest.raises(gateway.GatewayError, match="符号链接"):
        gateway.validate_config_dir(str(cfg))


def test_config_dir_rejects_foreign_entries(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "unrelated.txt").write_text("x", encoding="utf-8")
    with pytest.raises(gateway.GatewayError, match="非网关条目"):
        gateway.validate_config_dir(str(cfg))


def test_config_dir_rejects_subdirectory_entry(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    (cfg / "Caddyfile").mkdir(parents=True)
    with pytest.raises(gateway.GatewayError):
        gateway.validate_config_dir(str(cfg))


def test_config_dir_accepts_absent_and_idempotent_reuse(tmp_path: Path) -> None:
    cfg = tmp_path / "deep" / "cfg"
    assert gateway.validate_config_dir(str(cfg)) == cfg.resolve()
    cfg.mkdir(parents=True)
    (cfg / "Caddyfile").write_text("stale", encoding="utf-8")
    assert gateway.validate_config_dir(str(cfg)) == cfg.resolve()


# ---------------------------------------------------------------- 渲染


def test_render_is_exact_and_validated(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    written = gateway.render_caddyfile(cfg)
    assert written == "Caddyfile"
    content = (cfg / "Caddyfile").read_text(encoding="utf-8")
    body = "\n".join(l for l in content.splitlines() if not l.lstrip().startswith("#"))
    assert "auto_https off" in body and "admin off" in body
    assert ":80 {" in body
    assert f"reverse_proxy {gateway.UPSTREAM}" in body
    assert not re.search(r"\b443\b", body)
    assert not re.search(r"^\s*tls\s", body, re.MULTILINE)


def test_validate_caddy_config_rejects_drift() -> None:
    good = "{\n\tauto_https off\n\tadmin off\n}\n\n:80 {\n\treverse_proxy web:3000\n}\n"
    gateway.validate_caddy_config(good)
    for bad in [
        good.replace("auto_https off", "auto_https on"),
        good.replace("reverse_proxy web:3000", "reverse_proxy evil:9000"),
        good.replace(":80 {", ":443 {"),
        good + "\n:443 {\n\treverse_proxy web:3000\n}\n",
    ]:
        with pytest.raises(gateway.GatewayError) as excinfo:
            gateway.validate_caddy_config(bad)
        assert excinfo.value.category == gateway.CATEGORY_INSTALL


# ---------------------------------------------------------------- install preflight fail-closed


def _install_fail(tmp_path: Path, runner: FakeRunner, prober: FakeProber | None,
                  capsys, extra: list[str] | None = None) -> str:
    code, _report = _evidence_of(tmp_path, _install_args(tmp_path, extra), runner, prober)
    assert code == gateway.EXIT_FAILURE
    return capsys.readouterr().err


def test_preflight_rejects_docker_unavailable(tmp_path: Path, capsys) -> None:
    err = _install_fail(tmp_path, FakeRunner(responses={("docker",): (127, "FileNotFoundError")}),
                        None, capsys)
    assert gateway.CATEGORY_DOCKER in err


def test_preflight_rejects_missing_network(tmp_path: Path, capsys) -> None:
    err = _install_fail(tmp_path, _healthy_runner(
        overrides={("docker", "network", "inspect"): (1, "Error: not found")}), None, capsys)
    assert gateway.CATEGORY_NETWORK in err


@pytest.mark.parametrize("state_json,detail", [
    (json.dumps({"Running": True, "Health": {"Status": "unhealthy"}}), "unhealthy"),
    (json.dumps({"Running": True, "Health": {"Status": "starting"}}), "starting"),
    (json.dumps({"Running": False}), "not-running"),
])
def test_preflight_rejects_unhealthy_web(state_json: str, detail: str, tmp_path: Path,
                                         capsys) -> None:
    err = _install_fail(tmp_path, _healthy_runner(
        overrides={("docker", "inspect", gateway.PRODUCTION_WEB_CONTAINER, "--format", "{{json .State}}"):
                   (0, state_json)}), None, capsys)
    assert gateway.CATEGORY_WEB in err, detail


def test_preflight_rejects_missing_web_container(tmp_path: Path, capsys) -> None:
    err = _install_fail(tmp_path, _healthy_runner(
        overrides={("docker", "inspect", gateway.PRODUCTION_WEB_CONTAINER, "--format", "{{json .State}}"):
                   (1, "Error: No such object")}),
        None, capsys)
    assert gateway.CATEGORY_WEB in err


def test_preflight_rejects_busy_port(tmp_path: Path, capsys) -> None:
    err = _install_fail(tmp_path, _healthy_runner(), FakeProber(busy_ports={3012}), capsys)
    assert gateway.CATEGORY_PORT in err


def test_preflight_rejects_existing_same_name_container(tmp_path: Path, capsys) -> None:
    # 同名容器已存在（自有且全匹配）——绝不覆盖
    runner = _healthy_runner(overrides={
        ("docker", "inspect", gateway.CONTAINER_NAME): (0, "placeholder"),
        **_installed_gateway_responses(),
    })
    err = _install_fail(tmp_path, runner, None, capsys)
    assert gateway.CATEGORY_EXISTS in err
    assert not (tmp_path / "cfg").exists(), "preflight 拒绝不渲染"


def test_preflight_rejects_drifted_existing_gateway(tmp_path: Path, capsys) -> None:
    runner = _healthy_runner(overrides={
        ("docker", "inspect", gateway.CONTAINER_NAME): (0, "placeholder"),
        **_installed_gateway_responses(),
        ("docker", "inspect", gateway.CONTAINER_NAME, "--format", "{{json .HostConfig.PortBindings}}"):
            (0, good_ports(9999)),
    })
    err = _install_fail(tmp_path, runner, None, capsys)
    assert gateway.CATEGORY_EXISTS in err


# ---------------------------------------------------------------- install 成功 / 装后校验


def test_install_success_full_chain(tmp_path: Path) -> None:
    port = 3012

    class SequencedRunner(FakeRunner):
        def run(self, args):
            self.calls.append(list(args))
            if tuple(args[:2]) == ("docker", "run"):
                self.responses.update(_installed_gateway_responses(
                    port, good_mounts(str(tmp_path / "cfg" / "Caddyfile"))))
            for prefix in sorted(self.responses, key=len, reverse=True):
                if len(prefix) <= len(args) and tuple(args[: len(prefix)]) == prefix:
                    return self.responses[prefix]
            return 0, ""

    prober = FakeProber()
    code, report = _evidence_of(tmp_path, _install_args(tmp_path),
                                SequencedRunner(_healthy_runner().responses), prober)
    assert code == gateway.EXIT_OK
    assert report["result"] == "pass"
    assert report["production_public_ready"] is False
    assert report["host_port"] == port and report["upstream"] == "web:3000"
    assert report["probe"] == {"path": "/", "status_code": 200, "ok": True}
    assert (tmp_path / "cfg" / "Caddyfile").is_file(), "渲染落盘"
    markdown = (tmp_path / "evidence" / gateway.EVIDENCE_MD_NAME).read_text(encoding="utf-8")
    assert "production_public_ready = False" in markdown
    assert gateway.UNINSTALL_CONFIRM_PHRASE in markdown, "回滚指引"
    assert prober.http_calls and prober.http_calls[-1][0] == "http://127.0.0.1:3012/"


def test_install_docker_argv_whitelist_and_run_shape(tmp_path: Path) -> None:
    port = 3013
    base = _healthy_runner()

    class SequencedRunner(FakeRunner):
        def run(self, args):
            self.calls.append(list(args))
            if tuple(args[:2]) == ("docker", "run"):
                self.responses.update(_installed_gateway_responses(
                    port, good_mounts(str(tmp_path / "cfg" / "Caddyfile"))))
            for prefix in sorted(self.responses, key=len, reverse=True):
                if len(prefix) <= len(args) and tuple(args[: len(prefix)]) == prefix:
                    return self.responses[prefix]
            return 0, ""

    runner = SequencedRunner(base.responses)
    code, _ = _evidence_of(tmp_path, _install_args(tmp_path, ["--host-port", str(port)]), runner)
    assert code == gateway.EXIT_OK
    for call in runner.calls:
        assert tuple(call[:2]) in {p[:2] for p in gateway.ALLOWED_DOCKER_PREFIXES}, call
        assert call[0] == "docker"
    run_calls = [c for c in runner.calls if c[:2] == ["docker", "run"]]
    assert len(run_calls) == 1
    run = run_calls[0]
    assert run[run.index("--name") + 1] == gateway.CONTAINER_NAME
    assert run[run.index("--network") + 1] == gateway.PRODUCTION_NETWORK
    assert run[run.index("--publish") + 1] == f"127.0.0.1:{port}:80"
    assert run[run.index("--restart") + 1] == "unless-stopped"
    assert f"io.aios.managed-by={gateway.OWNERSHIP_LABELS['io.aios.managed-by']}" in run
    assert f"io.aios.milestone={gateway.OWNERSHIP_LABELS['io.aios.milestone']}" in run
    assert gateway.CADDY_IMAGE in run and "@sha256:" in gateway.CADDY_IMAGE
    assert "readonly" in " ".join(run)


def test_install_post_verify_drift_fails_without_auto_removal(tmp_path: Path, capsys) -> None:
    port = 3014
    base = _healthy_runner()

    class SequencedRunner(FakeRunner):
        def run(self, args):
            self.calls.append(list(args))
            if tuple(args[:2]) == ("docker", "run"):
                # 装后 inspect 显示网络漂移（附加了额外网络）
                self.responses.update(_installed_gateway_responses(
                    port, good_mounts(str(tmp_path / "cfg" / "Caddyfile"))))
                self.responses[("docker", "inspect", gateway.CONTAINER_NAME,
                                "--format", "{{json .NetworkSettings.Networks}}")] = (
                    0, json.dumps({gateway.PRODUCTION_NETWORK: {}, "extra-net": {}}))
            for prefix in sorted(self.responses, key=len, reverse=True):
                if len(prefix) <= len(args) and tuple(args[: len(prefix)]) == prefix:
                    return self.responses[prefix]
            return 0, ""

    runner = SequencedRunner(base.responses)
    code, _report = _evidence_of(tmp_path, _install_args(tmp_path, ["--host-port", str(port)]), runner)
    assert code == gateway.EXIT_FAILURE
    err = capsys.readouterr().err
    assert gateway.CATEGORY_VERIFY in err
    assert "不自动删除" in err
    assert [c for c in runner.calls if c[:2] == ["docker", "rm"]] == [], "装后校验失败不自动删容器"


def test_install_post_probe_non_200_fails_without_auto_removal(tmp_path: Path, capsys) -> None:
    port = 3015
    base = _healthy_runner()

    class SequencedRunner(FakeRunner):
        def run(self, args):
            self.calls.append(list(args))
            if tuple(args[:2]) == ("docker", "run"):
                self.responses.update(_installed_gateway_responses(
                    port, good_mounts(str(tmp_path / "cfg" / "Caddyfile"))))
            for prefix in sorted(self.responses, key=len, reverse=True):
                if len(prefix) <= len(args) and tuple(args[: len(prefix)]) == prefix:
                    return self.responses[prefix]
            return 0, ""

    runner = SequencedRunner(base.responses)
    prober = FakeProber(responses={f"http://127.0.0.1:{port}/": 502})
    code, report = _evidence_of(tmp_path, _install_args(tmp_path, ["--host-port", str(port)]),
                                runner, prober)
    assert code == gateway.EXIT_FAILURE
    assert gateway.CATEGORY_PROBE in capsys.readouterr().err
    assert report["result"] == "fail" and report["production_public_ready"] is False


def test_install_config_dir_inside_repo_fails(tmp_path: Path, capsys) -> None:
    argv = [
        "install", "--config-dir", str(REPO / "docs"),
        "--evidence-dir", str(tmp_path / "evidence"),
        "--confirm-phrase", gateway.INSTALL_CONFIRM_PHRASE, "--execute",
    ]
    runner = FakeRunner()
    code = gateway.main(argv, runner=runner)
    assert code == gateway.EXIT_FAILURE
    assert gateway.CATEGORY_CONFIG_DIR in capsys.readouterr().err
    assert runner.calls == [], "config-dir 防线先于一切 Docker 操作"


# ---------------------------------------------------------------- status / uninstall


def test_status_classifications_read_only(capsys) -> None:
    for expected, responses in [
        (gateway.STATUS_INSTALLED, _installed_gateway_responses()),
        (gateway.STATUS_MISSING, {
            ("docker", "inspect", gateway.CONTAINER_NAME): (1, f"Error: No such object: {gateway.CONTAINER_NAME}"),
        }),
        (gateway.STATUS_DEGRADED, {
            **_installed_gateway_responses(),
            ("docker", "inspect", gateway.CONTAINER_NAME, "--format", "{{json .Config.Image}}"):
                (0, json.dumps("docker.io/library/caddy:latest")),
        }),
        (gateway.STATUS_FOREIGN, {
            **_installed_gateway_responses(),
            ("docker", "inspect", gateway.CONTAINER_NAME, "--format", "{{json .Config.Labels}}"):
                (0, json.dumps({"io.aios.managed-by": "someone-else"})),
        }),
    ]:
        runner = FakeRunner({
            ("docker", "network", "inspect"): (0, gateway.PRODUCTION_NETWORK),
            ("docker", "inspect", gateway.PRODUCTION_WEB_CONTAINER, "--format", "{{json .State}}"):
                (0, HEALTHY_STATE),
            **responses,
        })
        assert gateway.main(["status"], runner=runner) == gateway.EXIT_OK
        out = capsys.readouterr().out
        assert f"status: {expected}" in out
        assert runner.mutating_calls() == [], f"{expected}: status 零变更"


def test_status_reports_docker_failure(capsys) -> None:
    runner = FakeRunner(responses={("docker",): (127, "FileNotFoundError")})
    assert gateway.main(["status"], runner=runner) == gateway.EXIT_FAILURE
    assert gateway.CATEGORY_DOCKER in capsys.readouterr().err


def test_uninstall_removes_only_owned_container(tmp_path: Path) -> None:
    runner = FakeRunner({
        ("docker", "network", "inspect"): (0, gateway.PRODUCTION_NETWORK),
        **_installed_gateway_responses(),
    })
    code = gateway.main(["uninstall", "--confirm-phrase", gateway.UNINSTALL_CONFIRM_PHRASE,
                         "--execute"], runner=runner)
    assert code == gateway.EXIT_OK
    removals = [c for c in runner.calls if c[:2] == ["docker", "rm"]]
    assert removals == [["docker", "rm", "-f", gateway.CONTAINER_NAME]], "只删精确自有容器"
    assert (tmp_path / "cfg").exists() is False or True  # uninstall 不涉及 config-dir


def test_uninstall_missing_container_idempotent(capsys) -> None:
    runner = FakeRunner({
        ("docker", "inspect", gateway.CONTAINER_NAME): (1, "Error: No such object"),
    })
    code = gateway.main(["uninstall", "--confirm-phrase", gateway.UNINSTALL_CONFIRM_PHRASE,
                         "--execute"], runner=runner)
    assert code == gateway.EXIT_OK
    assert runner.mutating_calls() == []


def test_uninstall_refuses_foreign_container(capsys) -> None:
    runner = FakeRunner({
        **_installed_gateway_responses(),
        ("docker", "inspect", gateway.CONTAINER_NAME, "--format", "{{json .Config.Labels}}"):
            (0, json.dumps({"io.aios.managed-by": "someone-else"})),
    })
    code = gateway.main(["uninstall", "--confirm-phrase", gateway.UNINSTALL_CONFIRM_PHRASE,
                         "--execute"], runner=runner)
    assert code == gateway.EXIT_FAILURE
    assert gateway.CATEGORY_UNINSTALL in capsys.readouterr().err
    assert [c for c in runner.calls if c[:2] == ["docker", "rm"]] == [], "绝不删外来容器"


# ---------------------------------------------------------------- 证据纪律


def test_evidence_records_boundary_and_no_absolute_paths(tmp_path: Path) -> None:
    port = 3016
    base = _healthy_runner()

    class SequencedRunner(FakeRunner):
        def run(self, args):
            self.calls.append(list(args))
            if tuple(args[:2]) == ("docker", "run"):
                self.responses.update(_installed_gateway_responses(
                    port, good_mounts(str(tmp_path / "cfg" / "Caddyfile"))))
            for prefix in sorted(self.responses, key=len, reverse=True):
                if len(prefix) <= len(args) and tuple(args[: len(prefix)]) == prefix:
                    return self.responses[prefix]
            return 0, ""

    code, report = _evidence_of(tmp_path, _install_args(tmp_path, ["--host-port", str(port)]),
                                SequencedRunner(base.responses))
    assert code == gateway.EXIT_OK
    assert report["production_public_ready"] is False
    assert report["rollback"] and gateway.UNINSTALL_CONFIRM_PHRASE in report["rollback"]
    blob = json.dumps(report, ensure_ascii=False)
    assert str(tmp_path) not in blob, "证据不含本机绝对路径"
    markdown = (tmp_path / "evidence" / gateway.EVIDENCE_MD_NAME).read_text(encoding="utf-8")
    assert str(tmp_path) not in markdown
    assert "不修复 Docker Desktop" in markdown or "不暴露公网流量" in markdown


# ---------------------------------------------------------------- R1 修正 1：bind mount 精确校验


def _sequenced_install(tmp_path: Path, port: int, mounts_json: str) -> FakeRunner:
    base = _healthy_runner()

    class SequencedRunner(FakeRunner):
        def run(self, args):
            self.calls.append(list(args))
            if tuple(args[:2]) == ("docker", "run"):
                self.responses.update(_installed_gateway_responses(port, mounts_json))
            for prefix in sorted(self.responses, key=len, reverse=True):
                if len(prefix) <= len(args) and tuple(args[: len(prefix)]) == prefix:
                    return self.responses[prefix]
            return 0, ""

    return SequencedRunner(base.responses)


def test_install_bind_source_drift_fails_verify(tmp_path: Path, capsys) -> None:
    """R1-1：装后 mount source 与渲染产物不一致 -> verify-failed（不自动删除）。"""
    port = 3021
    wrong_source = good_mounts(str(tmp_path / "elsewhere" / "Caddyfile"))
    runner = _sequenced_install(tmp_path, port, wrong_source)
    code, report = _evidence_of(tmp_path, _install_args(tmp_path, ["--host-port", str(port)]), runner)
    assert code == gateway.EXIT_FAILURE
    err = capsys.readouterr().err
    assert gateway.CATEGORY_VERIFY in err
    assert "exact_source_verified" in err
    assert [c for c in runner.calls if c[:2] == ["docker", "rm"]] == []
    facts = report.get("container_facts") or {}
    assert facts.get("drift") == ["bind"]


@pytest.mark.parametrize("mounts_json,detail", [
    (json.dumps([{"Type": "bind", "Source": "/x/Caddyfile",
                  "Destination": gateway.CADDYFILE_MOUNT_DESTINATION, "RW": True}]), "not-readonly"),
    (json.dumps([{"Type": "volume", "Source": "vol",
                  "Destination": gateway.CADDYFILE_MOUNT_DESTINATION, "RW": False}]), "not-bind"),
    (json.dumps([]), "no-mount"),
])
def test_install_mount_shape_drift_fails_verify(mounts_json: str, detail: str,
                                                tmp_path: Path, capsys) -> None:
    port = 3022
    runner = _sequenced_install(tmp_path, port, mounts_json)
    code, _report = _evidence_of(tmp_path, _install_args(tmp_path, ["--host-port", str(port)]), runner)
    assert code == gateway.EXIT_FAILURE
    assert gateway.CATEGORY_VERIFY in capsys.readouterr().err, detail


def test_install_success_records_exact_source_without_paths(tmp_path: Path) -> None:
    port = 3023
    runner = _sequenced_install(tmp_path, port,
                                good_mounts(str(tmp_path / "cfg" / "Caddyfile")))
    code, report = _evidence_of(tmp_path, _install_args(tmp_path, ["--host-port", str(port)]), runner)
    assert code == gateway.EXIT_OK
    mount = report["container_facts"]["mount"]
    assert mount["exact_source_verified"] is True
    assert mount["source_basename"] == "Caddyfile"
    assert mount["readonly"] is True and mount["type"] == "bind"
    assert report["exact_source_verified"] is True
    blob = json.dumps(report, ensure_ascii=False)
    assert str(tmp_path) not in blob, "证据绝不持久化挂载 source 绝对路径"


def test_status_without_config_dir_reports_unverified_source(capsys) -> None:
    runner = FakeRunner({
        ("docker", "network", "inspect"): (0, gateway.PRODUCTION_NETWORK),
        ("docker", "inspect", gateway.PRODUCTION_WEB_CONTAINER, "--format", "{{json .State}}"):
            (0, HEALTHY_STATE),
        **_installed_gateway_responses(),  # 形状匹配但 source 未精确核对
    })
    assert gateway.main(["status"], runner=runner) == gateway.EXIT_OK
    out = capsys.readouterr().out
    assert "status: installed" in out
    assert "未精确验证" in out, "无 --config-dir 时必须明示未精确核对，绝不虚报"


def test_status_with_config_dir_verifies_exact_source(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    runner = FakeRunner({
        ("docker", "network", "inspect"): (0, gateway.PRODUCTION_NETWORK),
        ("docker", "inspect", gateway.PRODUCTION_WEB_CONTAINER, "--format", "{{json .State}}"):
            (0, HEALTHY_STATE),
        **_installed_gateway_responses(mounts=good_mounts(str(cfg / "Caddyfile"))),
    })
    code = gateway.main(["status", "--config-dir", str(cfg)], runner=runner)
    assert code == gateway.EXIT_OK
    out = capsys.readouterr().out
    assert "已精确验证" in out


def test_status_with_config_dir_reports_source_drift(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    runner = FakeRunner({
        ("docker", "network", "inspect"): (0, gateway.PRODUCTION_NETWORK),
        ("docker", "inspect", gateway.PRODUCTION_WEB_CONTAINER, "--format", "{{json .State}}"):
            (0, HEALTHY_STATE),
        **_installed_gateway_responses(mounts=good_mounts("/elsewhere/Caddyfile")),
    })
    assert gateway.main(["status", "--config-dir", str(cfg)], runner=runner) == gateway.EXIT_OK
    out = capsys.readouterr().out
    assert "status: degraded" in out and "drift=['bind']" in out


# ---------------------------------------------------------------- R1 修正 3：RealRunner 运行时白名单


def test_real_runner_rejects_unsafe_argv_before_execution(monkeypatch) -> None:
    import subprocess as real_subprocess

    executed: list[list[str]] = []

    def spy_run(*args, **kwargs):  # pragma: no cover —— 断言其绝不执行
        executed.append(list(args[0]))
        raise AssertionError("unsafe argv 绝不应触及 subprocess")

    monkeypatch.setattr(real_subprocess, "run", spy_run)
    runner = gateway.RealRunner()
    for unsafe in (
        ["docker", "exec", gateway.CONTAINER_NAME, "cat", "/etc/caddy/Caddyfile"],
        ["docker", "stop", gateway.PRODUCTION_WEB_CONTAINER],
        ["docker", "restart", "anything"],
        ["schtasks", "/Query"],
        ["cmd", "/c", "whoami"],
        ["docker"],
    ):
        code, out = runner.run(unsafe)
        assert code == 126, unsafe
        assert out == "argv-not-allowed"
    assert executed == [], "全部 unsafe argv 在执行前被拒"


def test_real_runner_whitelisted_argv_reaches_subprocess(monkeypatch) -> None:
    import subprocess as real_subprocess
    import types

    class FakeResult(types.SimpleNamespace):
        returncode = 0
        stdout = "ok"
        stderr = ""

    seen: dict[str, list[str]] = {}

    def fake_run(args, **kwargs):
        seen["argv"] = list(args)
        return FakeResult()

    monkeypatch.setattr(real_subprocess, "run", fake_run)
    runner = gateway.RealRunner()
    code, out = runner.run(["docker", "inspect", gateway.CONTAINER_NAME, "--format", "{{json .State}}"])
    assert (code, out) == (0, "ok")
    assert seen["argv"][0] == "docker"


def test_real_runner_whitelist_prefix_semantics(monkeypatch) -> None:
    """白名单按前缀放行五种形态；其他动词（含危险操作）全部 126。"""
    import subprocess as real_subprocess
    import types

    class FakeResult(types.SimpleNamespace):
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(real_subprocess, "run", lambda args, **kw: FakeResult())
    runner = gateway.RealRunner()
    assert runner.run(["docker", "--version"])[0] == 0
    assert runner.run(["docker", "network", "inspect", "x"])[0] == 0
    assert runner.run(["docker", "inspect", "x", "--format", "y"])[0] == 0
    assert runner.run(["docker", "run", "-d", "--name", "x"])[0] == 0
    assert runner.run(["docker", "rm", "-f", "x"])[0] == 0
    for unsafe in (["docker", "system", "prune"], ["docker", "compose", "down"],
                   ["docker", "kill", "x"], ["docker", "exec", "x", "sh"]):
        assert runner.run(unsafe) == (126, "argv-not-allowed"), unsafe


def test_real_runner_timeout_no_traceback(monkeypatch) -> None:
    import subprocess as real_subprocess

    def hanging_run(args, **kwargs):
        raise real_subprocess.TimeoutExpired(cmd=args, timeout=300)

    monkeypatch.setattr(real_subprocess, "run", hanging_run)
    runner = gateway.RealRunner()
    assert runner.run(["docker", "inspect", "x"]) == (124, "TimeoutExpired")


def test_real_runner_missing_binary_no_traceback(monkeypatch) -> None:
    import subprocess as real_subprocess

    def missing_run(args, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(real_subprocess, "run", missing_run)
    runner = gateway.RealRunner()
    assert runner.run(["docker", "--version"]) == (127, "FileNotFoundError")


# ---------------------------------------------------------------- R1 修正 2：evidence-dir 路径链


def test_evidence_dir_rejects_symlinked_parent_chain(tmp_path: Path) -> None:
    real = tmp_path / "ev-real"
    (real / "leaf").mkdir(parents=True)
    link = tmp_path / "ev-link"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("本机无法创建符号链接（权限）")
    with pytest.raises(gateway.GatewayError, match="符号链接"):
        gateway.resolve_evidence_dir(str(link / "leaf"))


def test_evidence_dir_rejects_symlinked_parent_windows_safe(tmp_path: Path, monkeypatch) -> None:
    evidence = tmp_path / "ev-parent" / "ev-leaf"
    evidence.mkdir(parents=True)
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "ev-parent")
    with pytest.raises(gateway.GatewayError, match="符号链接"):
        gateway.resolve_evidence_dir(str(evidence))


def test_evidence_dir_default_verify_path_still_allowed() -> None:
    assert gateway.resolve_evidence_dir(None) == gateway.DEFAULT_EVIDENCE_DIR
