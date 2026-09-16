r"""M14-40 契约：MinIO 自建镜像 build/smoke/preflight 工具（零真实 Docker）。

被测对象 ``tools/ops/minio_image_adoption.py``（单文件纯标准库；Runner/
Transport/Sleeper 注入）。本套件全部用 FakeRunner/FakeTransport 锁行为，
零真实 Docker、零网络、零生产读取：

- 模式门：无 --execute / 确认短语不匹配 → 拒绝且零 subprocess；plan 零采集；
- argv 结构门：白名单形态逐 token 匹配；pull/stop/restart/kill/exec/compose/
  生产容器 rm/生产卷 rm/错误镜像/非 :ro 生产卷挂载/无 --network none 的
  helper 一律在任何执行之前拒绝；
- build：只构建 compose 锚定镜像；失败保留诊断尾行；
- smoke：一次性名严格 aios-m14-40- 前缀；端口占用/镜像缺失即拒绝（杜绝
  隐式 pull）；cluster 健康 + uid 1000 数据探针 + 版本核对；finally 恒清理
  且清理只认本轮两个生成名；
- preflight：镜像元数据/运行时 uid/版本/compose+Dockerfile 锚点/六容器健康/
  卷属主（root → 采纳 blocked，全部 uid 1000 → pass）；镜像缺失时不发起任何
  docker run（隐式 pull 结构性杜绝）；
- pin 交叉锁定：工具常量 == compose image 锚点 == production_recovery.
  LOCAL_BUILD_IMAGE_REFS == Dockerfile ARG；
- 冒烟端口 19000/19001 与 compose 全部宿主发布端口恒不冲突。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "minio_image_adoption.py"
RECOVERY_SCRIPT = REPO_ROOT / "tools" / "ops" / "production_recovery.py"
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.yml"
DOCKERFILE = REPO_ROOT / "infra" / "minio" / "Dockerfile"
PROJECT = "aios-m14-03-production-rehearsal"
PROD_VOLUME = f"{PROJECT}_minio-data"
PROD_MINIO = f"{PROJECT}-minio-1"
STACK_SERVICES = ("postgres", "redis", "minio", "api", "web", "livekit")
SECRET_PATTERNS = ("sk-", "AKIA", "ghp_", "xoxb_", "-----BEGIN")


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


mia = _load_module(SCRIPT, "minio_image_adoption_under_test")
pr = _load_module(RECOVERY_SCRIPT, "production_recovery_for_m14_40_test")


# ---------------------------------------------------------------- fakes


class FakeRunner:
    """按 argv 回放预制结果；记录全部调用（含超时/异常注入）。"""

    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, *, timeout: float = 60.0, encoding: str | None = None):
        argv = tuple(str(item) for item in argv)
        self.calls.append(argv)
        rc, stdout, stderr = self.handler(argv)
        return mia.CommandResult(argv, rc, stdout, stderr)


class FakeTransport:
    """端口探测恒 refused；cluster 端点第 N 次起返回给定状态。"""

    def __init__(self, *, port_probe: str = "connection-refused",
                 cluster_status: int | None = 200, ready_after: int = 1) -> None:
        self.port_probe = port_probe
        self.cluster_status = cluster_status
        self.ready_after = ready_after
        self.cluster_hits = 0

    def get(self, host: str, port: int, path: str, *, timeout: float):
        if path == "/":
            if self.port_probe == "ok":
                return mia.HttpResult(200, None, None)
            return mia.HttpResult(None, self.port_probe, "FakeError")
        self.cluster_hits += 1
        if self.cluster_hits >= self.ready_after:
            return mia.HttpResult(self.cluster_status, None, None)
        return mia.HttpResult(None, "connection-refused", "FakeError")


class FakeSleeper:
    def __init__(self) -> None:
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


class SafeLog:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def say(self, message: str) -> None:
        self.lines.append(message)


def version_output() -> str:
    return (f"minio version {mia.MINIO_RELEASE} (commit-id={mia.MINIO_COMMIT})\n"
            "Runtime: go1.24.8 linux/amd64\nCopyright\n")


def default_handler(argv: tuple[str, ...]) -> tuple[int, str, str]:
    """全绿生产画像：镜像在场 + 六容器 healthy + 生产卷 root 属主。"""
    joined = " ".join(argv)
    if argv[:2] == ("docker", "version"):
        return 0, "29.7.2\n", ""
    if argv[:3] == ("docker", "image", "inspect"):
        if "{{.Id}}" in argv:
            return 0, "sha256:deadbeef\n", ""
        return 0, json.dumps({"User": "minio:minio",
                              "Entrypoint": ["/usr/bin/minio"]}), ""
    if argv[:2] == ("docker", "run"):
        if "--version" in argv:
            return 0, version_output(), ""
        if "id -u" in argv:
            return 0, "1000\n", ""
        if "/bin/ls" in argv:
            if "-lnAR" in argv:  # 递归 UID 普查：顶层 + 嵌套（默认画像 = root 属主）
                return 0, ("total 8\n"
                           "drwxr-xr-x 3 root root 96 Sep 14 17:02 .minio.sys\n"
                           "drwxr-xr-x 2 root root 40 Sep 14 17:02 buckets\n"
                           "\n"
                           "/probe/.minio.sys:\n"
                           "total 4\n"
                           "-rw-r--r-- 1 root root 1 Sep 14 17:02 format.json\n"), ""
            return 0, ("total 8\n"
                       "drwxr-xr-x 3 root root 96 Sep 14 17:02 .minio.sys\n"
                       "drwxr-xr-x 2 root root 40 Sep 14 17:02 buckets\n"), ""
        if "/bin/du" in argv:
            return 0, "12M\t/probe\n", ""
        if DATA_PROBE in argv:
            return 0, "m14-40-probe\n", ""
        return 0, "container-started\n", ""
    if argv[:2] == ("docker", "inspect"):
        if "{{json .Config}}" in argv:
            return 0, json.dumps({"Image": "minio/minio:latest", "User": ""}), ""
        return 0, json.dumps({"Status": "running",
                              "Health": {"Status": "healthy"}}), ""
    if argv[:2] == ("docker", "volume"):
        if "rm" in argv:
            return 0, argv[-1] + "\n", ""
        return 0, "local\n", ""
    if argv[:2] == ("docker", "rm"):
        return 0, argv[-1] + "\n", ""
    if argv[:2] == ("docker", "build"):
        return 0, f"Successfully tagged {mia.SELF_IMAGE_REF}\n", ""
    raise AssertionError(f"意外 argv: {joined}")


DATA_PROBE = mia.DATA_PROBE_SCRIPT
STAMP = "20991231-235959"
SMOKE_C, SMOKE_V = mia.disposable_names(STAMP)


def make_surface() -> mia.Surface:
    return mia.Surface(project=PROJECT, disposable=frozenset({SMOKE_C, SMOKE_V}))


# ---------------------------------------------------------------- 模式门（main，零副作用路径）


def test_plan_mode_zero_subprocess_and_exit_ok(tmp_path: Path, capsys) -> None:
    rc = mia.main(["--artifact-dir", str(tmp_path)])
    assert rc == mia.EXIT_OK
    out = capsys.readouterr().out
    assert "PLAN" in out and "零 subprocess" in out
    assert any(p.name.startswith("plan-") for p in tmp_path.iterdir())


@pytest.mark.parametrize("mode_args", [
    ["build", "--execute"],
    ["build", "--execute", "--confirm", "WRONG"],
    ["build"],
    ["smoke", "--execute"],
    ["smoke", "--execute", "--confirm", "EXECUTE MINIO IMAGE BUILD"],
    ["preflight"],
    ["preflight", "--execute", "--confirm", ""],
])
def test_mode_gates_reject_without_exact_phrase(tmp_path: Path, mode_args) -> None:
    rc = mia.main(["--artifact-dir", str(tmp_path)] + mode_args)
    assert rc == mia.EXIT_REJECT


def test_invalid_project_name_rejected(tmp_path: Path) -> None:
    rc = mia.main(["--artifact-dir", str(tmp_path), "preflight", "--execute",
                   "--confirm", mia.CONFIRM_PREFLIGHT,
                   "--project", "../evil;rm"])
    assert rc == mia.EXIT_REJECT


# ---------------------------------------------------------------- argv 结构门


@pytest.mark.parametrize("evil", [
    ("docker", "pull", "aios/minio:RELEASE.2025-10-15T17-29-55Z"),
    ("docker", "pull", "alpine"),
    ("docker", "stop", PROD_MINIO),
    ("docker", "restart", PROD_MINIO),
    ("docker", "kill", PROD_MINIO),
    ("docker", "rm", "--force", PROD_MINIO),
    ("docker", "rm", "-f", PROD_MINIO),
    ("docker", "exec", PROD_MINIO, "sh"),
    ("docker", "volume", "rm", PROD_VOLUME),
    ("docker", "volume", "rm", SMOKE_V[:-1] + "X"),  # 非本轮生成名
    ("docker", "compose", "-f", "infra/docker-compose.yml", "up", "-d"),
    ("docker", "compose", "ps"),
    ("docker", "build", "-t", "aios/minio:other", "infra/minio"),
    ("docker", "build", "-t", mia.SELF_IMAGE_REF, "."),
    ("docker", "run", "--rm", "--entrypoint", "/usr/bin/minio", "evil/img:1", "--version"),
    ("docker", "run", "--rm", "--entrypoint", "/usr/bin/minio", mia.SELF_IMAGE_REF, "server", "/x"),
    # 生产卷挂载缺 :ro
    ("docker", "run", "--rm", "--network", "none", "-v", f"{PROD_VOLUME}:/probe",
     "--entrypoint", "/bin/ls", mia.SELF_IMAGE_REF, "-lan", "/probe"),
    # helper 缺 --network none
    ("docker", "run", "--rm", "-v", f"{PROD_VOLUME}:/probe:ro",
     "--entrypoint", "/bin/ls", mia.SELF_IMAGE_REF, "-lan", "/probe"),
    # 生产卷 rw 数据探针（数据探针只允许一次性卷）
    ("docker", "run", "--rm", "--network", "none", "-v", f"{PROD_VOLUME}:/data",
     "--entrypoint", "/bin/sh", mia.SELF_IMAGE_REF, "-c", DATA_PROBE),
    # 冒烟 server 名不在本轮生成集
    ("docker", "run", "-d", "--name", "evil", "-p", "127.0.0.1:19000:9000",
     "-p", "127.0.0.1:19001:9001", "-e", "MINIO_ROOT_USER=aios",
     "-e", "MINIO_ROOT_PASSWORD=aios12345", "-v", f"{SMOKE_V}:/data",
     mia.SELF_IMAGE_REF, "server", "/data", "--console-address", ":9001"),
    # 冒烟端口漂移
    ("docker", "run", "-d", "--name", SMOKE_C, "-p", "0.0.0.0:9000:9000",
     "-p", "127.0.0.1:19001:9001", "-e", "MINIO_ROOT_USER=aios",
     "-e", "MINIO_ROOT_PASSWORD=aios12345", "-v", f"{SMOKE_V}:/data",
     mia.SELF_IMAGE_REF, "server", "/data", "--console-address", ":9001"),
    # R1-1：挂载与 entrypoint 之间插入 docker 选项（头尾匹配本可通过——必须拒绝）
    ("docker", "run", "--rm", "--network", "none", "-v", f"{PROD_VOLUME}:/probe:ro",
     "--privileged",
     "--entrypoint", "/bin/ls", mia.SELF_IMAGE_REF, "-lan", "/probe"),
    ("docker", "run", "--rm", "--network", "none", "-v", f"{PROD_VOLUME}:/probe:ro",
     "--pid=host", "--entrypoint", "/bin/du", mia.SELF_IMAGE_REF, "-sh", "/probe"),
    ("docker", "run", "--rm", "--network", "none", "-v", f"{PROD_VOLUME}:/probe:ro",
     "--user", "0", "--entrypoint", "/bin/ls", mia.SELF_IMAGE_REF, "-lnAR", "/probe"),
    ("docker", "run", "--rm", "--network", "none", "-v", f"{SMOKE_V}:/data",
     "--privileged", "--entrypoint", "/bin/sh", mia.SELF_IMAGE_REF, "-c", DATA_PROBE),
    # R1-1：尾部追加 token（长度漂移）
    ("docker", "run", "--rm", "--network", "none", "-v", f"{PROD_VOLUME}:/probe:ro",
     "--entrypoint", "/bin/ls", mia.SELF_IMAGE_REF, "-lan", "/probe", "-laR"),
    ("docker", "run", "--rm", "--network", "none", "-v", f"{PROD_VOLUME}:/probe:ro",
     "--entrypoint", "/bin/du", mia.SELF_IMAGE_REF, "-sh", "/probe", "/other"),
    # R1-6：build 代理形态——surface 未带代理值 / 值不一致 / 值形态非法
    ("docker", "build", "-t", mia.SELF_IMAGE_REF,
     "--build-arg", "HTTPS_PROXY=socks5h://host.docker.internal:10808",
     str(mia.BUILD_CONTEXT)),
    ("docker", "build", "-t", mia.SELF_IMAGE_REF,
     "--build-arg", "HTTPS_PROXY=evil.example:1080", str(mia.BUILD_CONTEXT)),
    ("docker", "build", "-t", mia.SELF_IMAGE_REF,
     "--build-arg", "GOPROXY=direct", str(mia.BUILD_CONTEXT)),
])
def test_gate_rejects_every_mutation_or_drift_shape(evil: tuple[str, ...]) -> None:
    assert not mia.is_allowed_docker_argv(evil, make_surface())


@pytest.mark.parametrize("legit", [
    ("docker", "version", "--format", "{{.Server.Version}}"),
    ("docker", "build", "-t", mia.SELF_IMAGE_REF, str(mia.BUILD_CONTEXT)),
    ("docker", "image", "inspect", mia.SELF_IMAGE_REF, "--format", "{{.Id}}"),
    ("docker", "image", "inspect", mia.SELF_IMAGE_REF, "--format", "{{json .Config}}"),
    ("docker", "run", "--rm", "--network", "none", "--entrypoint", "/usr/bin/minio",
     mia.SELF_IMAGE_REF, "--version"),
    ("docker", "run", "--rm", "--network", "none", "--entrypoint", "/bin/sh",
     mia.SELF_IMAGE_REF, "-c", "id -u"),
    ("docker", "inspect", PROD_MINIO, "--format", "{{json .Config}}"),
    ("docker", "inspect", f"{PROJECT}-api-1", "--format", "{{json .State}}"),
    ("docker", "volume", "inspect", PROD_VOLUME, "--format", "{{.Driver}}"),
    ("docker", "run", "--rm", "--network", "none", "-v", f"{PROD_VOLUME}:/probe:ro",
     "--entrypoint", "/bin/ls", mia.SELF_IMAGE_REF, "-lan", "/probe"),
    ("docker", "run", "--rm", "--network", "none", "-v", f"{PROD_VOLUME}:/probe:ro",
     "--entrypoint", "/bin/du", mia.SELF_IMAGE_REF, "-sh", "/probe"),
    ("docker", "run", "--rm", "--network", "none", "-v", f"{SMOKE_V}:/probe:ro",
     "--entrypoint", "/bin/ls", mia.SELF_IMAGE_REF, "-lan", "/probe"),
    ("docker", "run", "--rm", "--network", "none", "-v", f"{SMOKE_V}:/data",
     "--entrypoint", "/bin/sh", mia.SELF_IMAGE_REF, "-c", DATA_PROBE),
    ("docker", "run", "-d", "--name", SMOKE_C, "-p", "127.0.0.1:19000:9000",
     "-p", "127.0.0.1:19001:9001", "-e", "MINIO_ROOT_USER=aios",
     "-e", "MINIO_ROOT_PASSWORD=aios12345", "-v", f"{SMOKE_V}:/data",
     mia.SELF_IMAGE_REF, "server", "/data", "--console-address", ":9001"),
    ("docker", "rm", "--force", SMOKE_C),
    ("docker", "volume", "rm", SMOKE_V),
])
def test_gate_accepts_exactly_the_legitimate_shapes(legit: tuple[str, ...]) -> None:
    assert mia.is_allowed_docker_argv(legit, make_surface())


def test_gate_accepts_recursive_census_helper_shape() -> None:
    # R1-5：递归 UID 普查 helper（:ro + --network none + 精确全长）
    census = ("docker", "run", "--rm", "--network", "none", "-v",
              f"{PROD_VOLUME}:/probe:ro",
              "--entrypoint", "/bin/ls", mia.SELF_IMAGE_REF, "-lnAR", "/probe")
    assert mia.is_allowed_docker_argv(census, make_surface())
    # 一次性卷同样允许（形态一致，仅名字来源不同）
    disposable = ("docker", "run", "--rm", "--network", "none", "-v",
                  f"{SMOKE_V}:/probe:ro",
                  "--entrypoint", "/bin/ls", mia.SELF_IMAGE_REF, "-lnAR", "/probe")
    assert mia.is_allowed_docker_argv(disposable, make_surface())


def test_gate_build_proxy_shape_only_with_matching_surface_value() -> None:
    proxy = "socks5h://host.docker.internal:10808"
    argv = ("docker", "build", "-t", mia.SELF_IMAGE_REF,
            "--build-arg", f"HTTPS_PROXY={proxy}", str(mia.BUILD_CONTEXT))
    # surface 携带同一值 → 唯一合法代理形态
    assert mia.is_allowed_docker_argv(argv, mia.Surface(project=PROJECT,
                                                        build_https_proxy=proxy))
    # surface 无代理值 / 值不同 → 拒绝
    assert not mia.is_allowed_docker_argv(argv, make_surface())
    assert not mia.is_allowed_docker_argv(argv, mia.Surface(project=PROJECT,
                                                            build_https_proxy="http://x:1"))


def test_gate_runner_raises_before_execution() -> None:
    def fail(argv):  # pragma: no cover - 不应被执行
        raise AssertionError("gate 未拦截")

    gate = mia.GateRunner(FakeRunner(fail), make_surface())
    with pytest.raises(mia.CommandNotAllowedError):
        gate.run(("docker", "stop", PROD_MINIO))


def test_disposable_names_carry_strict_prefix() -> None:
    container, volume = mia.disposable_names("20260916-000000")
    assert container.startswith("aios-m14-40-smoke-c-")
    assert volume.startswith("aios-m14-40-smoke-v-")
    assert container.startswith(mia.DISPOSABLE_PREFIX)
    assert volume.startswith(mia.DISPOSABLE_PREFIX)


# ---------------------------------------------------------------- build


def test_build_success_records_image_id_and_single_argv() -> None:
    runner = FakeRunner(default_handler)
    facts = mia.run_build(runner)
    assert facts["ok"] is True
    assert facts["image_id"] == "sha256:deadbeef"
    assert runner.calls[0][:2] == ("docker", "build")
    assert runner.calls[0][2:4] == ("-t", mia.SELF_IMAGE_REF)
    assert runner.calls[0][4].endswith("infra") or runner.calls[0][4].endswith("minio")
    assert len([c for c in runner.calls if c[:2] == ("docker", "build")]) == 1


def test_build_failure_preserves_output_tail() -> None:
    def handler(argv):
        if argv[:2] == ("docker", "build"):
            return 1, "", "go: cannot find module\nnetwork unreachable\n"
        return default_handler(argv)

    facts = mia.run_build(FakeRunner(handler))
    assert facts["ok"] is False
    assert facts["returncode"] == 1
    assert any("network unreachable" in line for line in facts["output_tail"])


@pytest.mark.parametrize("inspect_result", [
    (1, "", "No such image\n"),   # R1-3：构建 0 但事后 inspect 失败
    (0, "\n", ""),                # R1-3：构建 0 但无镜像 Id 回显
])
def test_build_fails_closed_when_post_build_inspect_unusable(inspect_result) -> None:
    def handler(argv):
        if argv[:3] == ("docker", "image", "inspect"):
            return inspect_result
        return default_handler(argv)

    facts = mia.run_build(FakeRunner(handler))
    assert facts["ok"] is False
    assert facts["problem"] == "post-build-inspect-failed"
    assert facts["image_id"] is None


def test_build_passes_https_proxy_only_as_predefined_build_arg() -> None:
    proxy = "socks5h://host.docker.internal:10808"
    runner = FakeRunner(default_handler)
    facts = mia.run_build(runner, https_proxy=proxy)
    assert facts["ok"] is True
    build_argv = runner.calls[0]
    # 唯一代理形态：-t 锚点 + 恰一个 --build-arg HTTPS_PROXY=<值> + context
    assert build_argv == ("docker", "build", "-t", mia.SELF_IMAGE_REF,
                          "--build-arg", f"HTTPS_PROXY={proxy}",
                          str(mia.BUILD_CONTEXT))
    # GOPROXY 不受影响；代理值不入 facts
    assert "GOPROXY" not in " ".join(build_argv)
    assert proxy not in json.dumps(facts)


def test_build_without_proxy_keeps_default_shape() -> None:
    runner = FakeRunner(default_handler)
    facts = mia.run_build(runner, https_proxy=None)
    assert facts["ok"] is True
    assert runner.calls[0] == ("docker", "build", "-t", mia.SELF_IMAGE_REF,
                               str(mia.BUILD_CONTEXT))


@pytest.mark.parametrize("value", [
    "socks5h://host.docker.internal:10808",
    "socks5://127.0.0.1:1080",
    "http://proxy.corp:8080",
    "https://proxy.corp:8080/",
])
def test_build_proxy_validator_accepts_exact_url_shapes(value: str) -> None:
    assert mia.validate_build_https_proxy(value) is None


@pytest.mark.parametrize("value", [
    "", "socks5h://", "ftp://x:1", "socks5h://host:notaport",
    "socks5h://host:10808/../../etc", "not a url", "host:10808",
    # 端口语义边界：65535 之上（纯 5 位数字但非合法 TCP 端口）拒绝
    "socks5h://host:65536", "socks5h://host:99999", "http://proxy.corp:65536",
])
def test_build_proxy_validator_rejects_other_shapes(value: str) -> None:
    assert mia.validate_build_https_proxy(value) is not None


def test_build_proxy_validator_port_boundary_semantics() -> None:
    # 无端口 / 合法上界 65535 接受；0 拒绝（端口 0 非语义合法）
    assert mia.validate_build_https_proxy("socks5h://host.docker.internal") is None
    assert mia.validate_build_https_proxy("socks5h://host:65535") is None
    assert mia.validate_build_https_proxy("socks5h://host:0") is not None


def test_main_rejects_invalid_build_proxy_env_before_any_subprocess(
        tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv(mia.BUILD_PROXY_ENV, "garbage value")
    rc = mia.main(["--artifact-dir", str(tmp_path), "build", "--execute",
                   "--confirm", mia.CONFIRM_BUILD])
    assert rc == mia.EXIT_REJECT
    out = capsys.readouterr().out
    assert "形态非法" in out and "garbage" not in out  # 被拒值不回显


# ---------------------------------------------------------------- smoke


def test_smoke_full_pass_with_precise_cleanup() -> None:
    runner = FakeRunner(default_handler)
    facts = mia.run_smoke(runner, FakeTransport(), FakeSleeper(), SafeLog(), STAMP)
    assert facts["ok"] is True and facts["problems"] == []
    assert facts["cleanup_ok"] is True
    assert facts["checks"] == {"container_start": True, "cluster_health_200": True,
                               "uid1000_data_probe": True, "version_reports_pin": True}
    assert ("docker", "rm", "--force", SMOKE_C) in runner.calls
    assert ("docker", "volume", "rm", SMOKE_V) in runner.calls
    # helper 恒 --network none（唯一例外是冒烟 server -d 形态）
    run_helpers = [c for c in runner.calls if c[:2] == ("docker", "run") and "--rm" in c]
    assert run_helpers and all("none" in c[:5] for c in run_helpers)


def test_smoke_refused_when_ports_busy() -> None:
    runner = FakeRunner(default_handler)
    facts = mia.run_smoke(runner, FakeTransport(port_probe="ok"), FakeSleeper(),
                          SafeLog(), STAMP)
    assert facts["status"] == "refused" and facts["reason"] == "smoke-ports-busy"
    # 零 docker run（连一次性容器都不起，也绝无清理面副作用）
    assert not [c for c in runner.calls if c[:2] == ("docker", "run")]
    assert not [c for c in runner.calls if c[:2] == ("docker", "rm")]


def test_smoke_refused_when_image_missing_no_implicit_pull() -> None:
    def handler(argv):
        if argv[:3] == ("docker", "image", "inspect"):
            return 1, "", "No such image\n"
        return default_handler(argv)

    runner = FakeRunner(handler)
    facts = mia.run_smoke(runner, FakeTransport(), FakeSleeper(), SafeLog(), STAMP)
    assert facts["status"] == "refused" and facts["reason"] == mia.LOCAL_IMAGE_MISSING
    # 绝不发起 docker run（杜绝 docker 对缺失镜像的隐式 pull）
    assert not [c for c in runner.calls if c[:2] == ("docker", "run")]


def test_smoke_health_failure_still_cleans_up() -> None:
    runner = FakeRunner(default_handler)
    facts = mia.run_smoke(runner, FakeTransport(cluster_status=None),
                          FakeSleeper(), SafeLog(), STAMP)
    assert facts["ok"] is False
    assert "cluster-health-timeout" in facts["problems"]
    assert ("docker", "rm", "--force", SMOKE_C) in runner.calls
    assert ("docker", "volume", "rm", SMOKE_V) in runner.calls


def test_smoke_cleanup_failure_fails_the_whole_operation() -> None:
    def handler(argv):
        if argv[:2] == ("docker", "rm"):
            return 1, "", "cannot remove\n"
        return default_handler(argv)

    facts = mia.run_smoke(FakeRunner(handler), FakeTransport(), FakeSleeper(),
                          SafeLog(), STAMP)
    # 清理失败必须使冒烟整体失败（运行时检查事实保留，但 ok=False → CLI 拒绝）
    assert facts["ok"] is False
    assert facts["cleanup_ok"] is False
    assert "cleanup-container-failed" in facts["problems"]
    assert facts["checks"]["cluster_health_200"] is True  # 运行时事实仍保留


def test_smoke_data_probe_failure_fails_closed() -> None:
    def handler(argv):
        if DATA_PROBE in argv:
            return 1, "", "Permission denied\n"
        return default_handler(argv)

    facts = mia.run_smoke(FakeRunner(handler), FakeTransport(), FakeSleeper(),
                          SafeLog(), STAMP)
    assert facts["ok"] is False
    assert "uid1000-data-probe-failed" in facts["problems"]


def test_smoke_version_mismatch_fails_closed() -> None:
    def handler(argv):
        if "--version" in argv:
            return 0, "minio version RELEASE.1999-01-01 (commit-id=deadbeef)\n", ""
        return default_handler(argv)

    facts = mia.run_smoke(FakeRunner(handler), FakeTransport(), FakeSleeper(),
                          SafeLog(), STAMP)
    assert facts["ok"] is False
    assert "version-not-pinned" in facts["problems"]


# ---------------------------------------------------------------- preflight


def test_preflight_root_ownership_blocks_adoption() -> None:
    runner = FakeRunner(default_handler)
    collectors, results = mia.run_preflight(runner, PROJECT)
    assert results["adoption"] == "blocked"
    assert results["counts"]["critical"] >= 1
    ownership_check = [c for c in results["checks"]
                       if c["check_id"] == "prod-volume-ownership"]
    assert ownership_check and ownership_check[0]["severity"] == "critical"
    assert "迁移" in ownership_check[0]["detail"]
    assert collectors["prod_volume"]["ownership_suitable"] is False
    assert collectors["prod_volume"]["data_size_hint"] == "12M"
    # 只读纪律：生产卷挂载恒 :ro + --network none（只看 run 类探针）
    vol_calls = [c for c in runner.calls
                 if c[:2] == ("docker", "run") and any(PROD_VOLUME in tok for tok in c)]
    assert vol_calls and all("none" in c[:5] and c[6].endswith(":/probe:ro") for c in vol_calls)


def test_preflight_all_uid1000_passes() -> None:
    def handler(argv):
        if "/bin/ls" in argv:
            return 0, ("total 4\n"
                       "drwxr-xr-x 3 1000 1000 96 Sep 14 17:02 .minio.sys\n"
                       "\n"
                       "/probe/.minio.sys:\n"
                       "total 4\n"
                       "-rw-r--r-- 1 1000 1000 1 Sep 14 17:02 format.json\n"), ""
        return default_handler(argv)

    _collectors, results = mia.run_preflight(FakeRunner(handler), PROJECT)
    assert results["adoption"] == "pass"
    assert results["counts"]["critical"] == 0
    assert results["counts"]["ok"] >= 6


def test_preflight_nested_root_ownership_blocks_adoption() -> None:
    # R1-5：顶层全 uid 1000 但嵌套路径仍 root 属主 → 采纳 blocked（仅顶层不足）
    def handler(argv):
        if "/bin/ls" in argv:
            if "-lnAR" in argv:
                return 0, ("total 4\n"
                           "drwxr-xr-x 3 1000 1000 96 Sep 14 17:02 .minio.sys\n"
                           "\n"
                           "/probe/.minio.sys:\n"
                           "-rw-r--r-- 1 root root 1 Sep 14 17:02 format.json\n"), ""
            return 0, ("total 4\n"
                       "drwxr-xr-x 3 1000 1000 96 Sep 14 17:02 .minio.sys\n"), ""
        return default_handler(argv)

    collectors, results = mia.run_preflight(FakeRunner(handler), PROJECT)
    assert results["adoption"] == "blocked"
    failed = [c for c in results["checks"] if c["check_id"] == "prod-volume-ownership"]
    assert failed and failed[0]["severity"] == "critical"
    assert collectors["prod_volume"]["ownership_suitable"] is False
    census = collectors["prod_volume"]["ownership_recursive"]
    assert census["uid_counts"] == {"1000": 1, "root": 1}


def test_preflight_census_probe_failure_is_uncertain_blocked() -> None:
    # R1-5：递归普查无法完成（helper 失败）→ 不确定 → fail-closed 按 blocked
    def handler(argv):
        if "/bin/ls" in argv and "-lnAR" in argv:
            return 1, "", "ls: unrecognized option\n"
        return default_handler(argv)

    collectors, results = mia.run_preflight(FakeRunner(handler), PROJECT)
    assert results["adoption"] == "blocked"
    failed = [c for c in results["checks"] if c["check_id"] == "prod-volume-ownership"]
    assert failed and failed[0]["severity"] == "critical"
    assert "不可判" in failed[0]["detail"]
    assert collectors["prod_volume"]["ownership_suitable"] is None
    assert collectors["prod_volume"]["ownership_recursive"] is None
    assert collectors["prod_volume"]["ownership_recursive_failure"] is not None


def test_preflight_missing_image_blocks_without_any_docker_run() -> None:
    def handler(argv):
        if argv[:3] == ("docker", "image", "inspect"):
            return 1, "", "No such image\n"
        if argv[:2] == ("docker", "inspect") or argv[:2] == ("docker", "volume"):
            return default_handler(argv)
        raise AssertionError(f"镜像缺失时不应执行: {' '.join(argv)}")

    runner = FakeRunner(handler)
    _collectors, results = mia.run_preflight(runner, PROJECT)
    assert results["adoption"] == "blocked"
    present = [c for c in results["checks"] if c["check_id"] == "local-image-present"]
    assert present[0]["severity"] == "critical"
    assert mia.LOCAL_IMAGE_MISSING in present[0]["detail"]
    assert not [c for c in runner.calls if c[:2] == ("docker", "run")]


@pytest.mark.parametrize("bad,check_id", [
    ("uid", "runtime-uid"),
    ("entrypoint", "image-entrypoint"),
    ("user", "image-config-user"),
])
def test_preflight_identity_failures_block(bad: str, check_id: str) -> None:
    def handler(argv):
        if argv[:3] == ("docker", "image", "inspect") and "{{json .Config}}" in argv:
            payload = {"User": "minio:minio", "Entrypoint": ["/usr/bin/minio"]}
            if bad == "entrypoint":
                payload["Entrypoint"] = ["/bin/sh"]
            elif bad == "user":
                payload["User"] = ""
            return 0, json.dumps(payload), ""
        if bad == "uid" and "id -u" in argv:
            return 0, "0\n", ""
        return default_handler(argv)

    _collectors, results = mia.run_preflight(FakeRunner(handler), PROJECT)
    assert results["adoption"] == "blocked"
    failed = [c for c in results["checks"] if c["check_id"] == check_id]
    assert failed and failed[0]["severity"] == "critical"


def test_preflight_version_mismatch_blocks() -> None:
    def handler(argv):
        if "--version" in argv:
            return 0, "minio version OTHER (commit-id=other)\n", ""
        return default_handler(argv)

    _collectors, results = mia.run_preflight(FakeRunner(handler), PROJECT)
    assert results["adoption"] == "blocked"
    failed = [c for c in results["checks"] if c["check_id"] == "version-metadata"]
    assert failed[0]["severity"] == "critical"


def test_preflight_unhealthy_stack_blocks() -> None:
    def handler(argv):
        if argv[:2] == ("docker", "inspect") and "{{json .State}}" in argv:
            health = "unhealthy" if "-api-1" in " ".join(argv) else "healthy"
            return 0, json.dumps({"Status": "running", "Health": {"Status": health}}), ""
        return default_handler(argv)

    _collectors, results = mia.run_preflight(FakeRunner(handler), PROJECT)
    assert results["adoption"] == "blocked"
    failed = [c for c in results["checks"] if c["check_id"] == "stack-health"]
    assert failed[0]["severity"] == "critical"


def test_preflight_anchor_drift_blocks(tmp_path: Path, monkeypatch) -> None:
    drifted = tmp_path / "docker-compose.yml"
    drifted.write_text("services: {minio: {image: aios/minio:WRONG}}", encoding="utf-8")
    monkeypatch.setattr(mia, "COMPOSE_FILE", drifted)
    _collectors, results = mia.run_preflight(FakeRunner(default_handler), PROJECT)
    assert results["adoption"] == "blocked"
    failed = [c for c in results["checks"] if c["check_id"] == "anchor-config"]
    assert failed[0]["severity"] == "critical"


def test_preflight_uncertain_ownership_blocks() -> None:
    def handler(argv):
        if "/bin/ls" in argv:
            return 0, "", ""  # 空卷/无输出 → 属主不可判
        return default_handler(argv)

    _collectors, results = mia.run_preflight(FakeRunner(handler), PROJECT)
    assert results["adoption"] == "blocked"
    failed = [c for c in results["checks"] if c["check_id"] == "prod-volume-ownership"]
    assert "不可判" in failed[0]["detail"]


# ---------------------------------------------------------------- 纯解析


@pytest.mark.parametrize("stdout,entries,uids", [
    ("total 8\ndrwxr-xr-x 3 root root 96 t .minio.sys\n", 1, {"root": 1}),
    ("drwxr-xr-x 2 1000 1000 40 t a\n-rw-r--r-- 1 1000 1000 5 t b\n", 2, {"1000": 2}),
    ("total 0\n", 0, {}),
    ("garbage line without perms\n", 0, {}),
])
def test_parse_ls_lan_ownership(stdout: str, entries: int, uids: dict) -> None:
    ownership = mia.parse_ls_lan_ownership(stdout)
    assert ownership["entries"] == entries
    assert ownership["uid_counts"] == uids


def test_ownership_suitable_semantics() -> None:
    assert mia.ownership_suitable({"entries": 1, "uid_counts": {"1000": 1}}) is True
    assert mia.ownership_suitable({"entries": 2, "uid_counts": {"root": 1, "1000": 1}}) is False
    assert mia.ownership_suitable({"entries": 0, "uid_counts": {}}) is None
    assert mia.ownership_suitable({"entries": 1, "uid_counts": {}}) is None


def test_parse_recursive_uid_census() -> None:
    stdout = ("total 8\n"
              "drwxr-xr-x 3 1000 1000 96 Sep 14 17:02 .minio.sys\n"
              "\n"
              "/probe/.minio.sys:\n"
              "total 4\n"
              "-rw-r--r-- 1 root root 1 Sep 14 17:02 format.json\n")
    census = mia.parse_recursive_uid_census(stdout)
    assert census["entries"] == 2
    assert census["uid_counts"] == {"1000": 1, "root": 1}
    assert census["unparsed"] == 0  # 目录头/total/空行跳过，不计未解析


def test_recursive_census_unparsed_lines_counted_not_dropped() -> None:
    census = mia.parse_recursive_uid_census("/probe/sub:\ndrwxr-xr-x 2 1000 1000 96 t x\ngarbage\n")
    assert census["entries"] == 1
    assert census["unparsed"] == 1
    # 存在未解析行 = 普查不完整 → 不确定（fail-closed），绝不判 True
    assert mia.recursive_ownership_suitable(census) is None


def test_recursive_ownership_suitable_semantics() -> None:
    ok = {"entries": 3, "uid_counts": {"1000": 3}, "unparsed": 0}
    assert mia.recursive_ownership_suitable(ok) is True
    mixed = {"entries": 3, "uid_counts": {"1000": 2, "root": 1}, "unparsed": 0}
    assert mia.recursive_ownership_suitable(mixed) is False
    assert mia.recursive_ownership_suitable({"entries": 0, "uid_counts": {}, "unparsed": 0}) is None


def test_version_probe_ok_requires_both_pins() -> None:
    assert mia.version_probe_ok(version_output()) is True
    assert mia.version_probe_ok(f"minio version {mia.MINIO_RELEASE}\n") is False
    assert mia.version_probe_ok(f"commit-id={mia.MINIO_COMMIT}\n") is False


def test_validate_project_name() -> None:
    assert mia.validate_project_name(PROJECT) is None
    assert mia.validate_project_name("") is not None
    assert mia.validate_project_name("../evil") is not None
    assert mia.validate_project_name("a" * 65) is not None


@pytest.mark.parametrize("other", [
    "aios-other-project",          # 语法合法的其它项目名
    "aios-m14-03-production",      # 前缀相近
    "production",                  # 简短合法名
])
def test_any_project_other_than_default_rejected(other: str) -> None:
    # 本工具唯一预期生产面：--project 只允许 DEFAULT_PROJECT，语法合法也不行
    assert mia.validate_project_name(other) is not None
    assert mia.validate_project_name(mia.DEFAULT_PROJECT) is None


def test_main_rejects_syntactically_valid_other_project(tmp_path: Path) -> None:
    rc = mia.main(["--artifact-dir", str(tmp_path), "preflight", "--execute",
                   "--confirm", mia.CONFIRM_PREFLIGHT,
                   "--project", "aios-other-valid-name"])
    assert rc == mia.EXIT_REJECT


# ---------------------------------------------------------------- pin 交叉锁定 + 源码契约


def test_pins_cross_locked_with_compose_recovery_and_dockerfile() -> None:
    import yaml

    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    assert compose["services"]["minio"]["image"] == mia.SELF_IMAGE_REF
    assert mia.SELF_IMAGE_REF in pr.LOCAL_BUILD_IMAGE_REFS
    assert mia.LOCAL_IMAGE_MISSING == pr.LOCAL_IMAGE_MISSING
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert f"ARG MINIO_RELEASE={mia.MINIO_RELEASE}" in dockerfile
    assert f"ARG MINIO_COMMIT={mia.MINIO_COMMIT}" in dockerfile
    assert f"USER {mia.EXPECTED_CONFIG_USER}" in dockerfile


def test_smoke_ports_never_collide_with_compose_ports() -> None:
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    # 冒烟端口 19000/19001 与 compose 任何端口绑定（含 env 默认值面）恒不冲突
    assert "19000" not in compose_text
    assert "19001" not in compose_text


def test_source_contract_no_mutation_docker_vocabulary() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for banned in ('"docker", "compose"', '"docker", "pull"', '"docker", "stop"',
                   '"docker", "restart"', '"docker", "kill"', '"docker", "exec"'):
        assert banned not in source, f"mutation 词汇出现: {banned}"
    # 生产卷挂载在源码里恒只读（:/probe 只以 :ro 形态出现）
    assert ":/probe:ro" in source
    assert ':/probe"' not in source


def test_tool_reads_only_the_build_proxy_env_var() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    # R1-6 后唯一放宽：仅 build 模式读取 AIOS_MINIO_BUILD_HTTPS_PROXY（非
    # secret、操作者显式提供）；除此之外零环境变量读取（绝无
    # infra/env.production-recovery 面）
    assert source.count("environ") == 1
    assert "os.environ.get(BUILD_PROXY_ENV" in source


def test_build_proxy_env_is_the_only_new_env_surface() -> None:
    # main 仅在 build 分支消费该值；默认（未设置）argv 形态不变已由
    # test_build_without_proxy_keeps_default_shape 锁定
    assert mia.BUILD_PROXY_ENV == "AIOS_MINIO_BUILD_HTTPS_PROXY"


def test_no_secret_shapes_in_new_tool_source() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for pattern in SECRET_PATTERNS:
        assert pattern not in source
