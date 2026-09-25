r"""M14-127 tools/ops/production_drift_watch.py 契约测试：production
drift watch——只读校验生产栈七服务健康与 API/Web 运行镜像是否仍锚定
M14-124 已批准 tag+sha256 digest。默认 plan 零副作用；execute 需旗标+
精确确认短语；digest fail-closed（tag 相同绝不冒充 digest 通过）；漂移
或任何失败非零退出；报告仅安全元数据并原子落盘。

覆盖（绝不触碰真实 Docker/生产容器/DB/MinIO/语音服务；全部采集行为经
FakeRunner 注入；socket 与 subprocess 双阻断下 plan 照常成功）：
- 结构契约：AST import 白名单（零网络/零 env 模块）、源码零 env 读取
  token（environ/getenv）、零 shell=True、零动态执行；ops README 与
  证据 README 文档化；
- plan 面：默认零 subprocess/零 Docker/零网络（socket+subprocess 双
  阻断下照常出计划与 plan 报告）；plan 报告零状态宣称（无 drift/
  checks/counts）；stdout 不回显任何绝对路径；
- execute 门禁 fail-closed：缺确认/短语不精确/非法 --project（plan 与
  execute 双路径）→ EXIT 2 且零采集（Runner 零构造）；被拒项目名绝不
  回显；
- 子进程白名单门：仅 compose ps --format json / docker inspect
  --format <四事实> <单容器> / docker image inspect --format {{.Id}}
  <单引用> 三形态放行；stop/start/restart/rm/kill/down/exec/up/build/
  pull/logs/裸 ps/错格式串/多对象/非 docker 程序一律在任何执行之前
  拒绝；
- 采集与判定语义：compose ps 三形态解析（数组/单对象/JSONL）+ 垃圾
  拒绝；七服务存在+healthy；七容器 state/health；API/Web 三重 digest
  比对（运行 tag/运行镜像 ID/锚点 tag 本地解析）；healthy 全绿 →
  drift=false exit 0；digest mismatch / tag mismatch / tag 解析漂移 /
  digest 形态非法 / 采集失败（rc≠0、inspect 缺失、image inspect 缺失）
  → drift=true exit 2 且固定词汇 reasons（缺失绝不当作匹配）；
- 报告：schema 键、UTC 时间戳、边界声明、锚点摘要；防御性脱敏（标记
  值绝不落入 JSON/Markdown/stdout）；原子写（tmp+os.replace、无残留、
  同 stem 覆盖干净）与越界 stem 拒绝。
"""
from __future__ import annotations

import ast
import importlib.util
import json
import socket
import subprocess as subprocess_module
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "production_drift_watch.py"
OPS_README = REPO_ROOT / "tools" / "ops" / "README.md"
EVIDENCE_README = (REPO_ROOT / "docs" / "evidence"
                   / "m14-127-production-drift-watch" / "README.md")

#: 标记值（注入 fake docker 输出，断言其绝不进入任何输出）
MARK_TOKEN = "sk-ZXmarker4567890123"
MARK_CREDS = "password=ZXpwdmarker99887766"

PROJECT = "aios-m14-03-production-rehearsal"
SERVICES = ("postgres", "redis", "minio", "api", "web", "livekit", "searxng")


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


pdw = _load_module(SCRIPT, "production_drift_watch_under_test")

COMPOSE = "infra/docker-compose.yml"
API_TAG = pdw.EXPECTED_ANCHORS["api"].tag
WEB_TAG = pdw.EXPECTED_ANCHORS["web"].tag
API_DIGEST = pdw.EXPECTED_ANCHORS["api"].digest_ref
WEB_DIGEST = pdw.EXPECTED_ANCHORS["web"].digest_ref
OTHER_DIGEST = "sha256:" + "1" * 64


# ---------------------------------------------------------------- fakes


class FakeRunner:
    """伪 Docker 命令面：按 argv 形态回放预制结果，记录全部调用。

    container_facts 键为 service，值为四事实串或 None（None → rc≠0）；
    image_ids 键为镜像引用，值为 image ID 串或 None（None → rc≠0）。
    """

    def __init__(self, *, project: str = PROJECT,
                 ps_rows: list[dict[str, str]] | None = None,
                 ps_stdout: str | None = None,
                 ps_rc: int = 0,
                 container_facts: dict[str, str | None] | None = None,
                 image_ids: dict[str, str | None] | None = None) -> None:
        self.project = project
        self.ps_rows = ps_rows if ps_rows is not None else [
            {"Service": svc, "Health": "healthy", "State": "running"} for svc in SERVICES
        ]
        self.ps_stdout = ps_stdout
        self.ps_rc = ps_rc
        base_images = {
            "postgres": "postgres:17-alpine",
            "redis": "redis:7-alpine",
            "minio": "aios/minio:RELEASE.2025-10-15T17-29-55Z",
            "livekit": "livekit/livekit-server:latest",
            "searxng": "docker.io/searxng/searxng@sha256:6869f20676fd",
        }
        self.container_facts = container_facts if container_facts is not None else {
            **{svc: f"running\thealthy\t{img}\tsha256:{('a' * 64)}"
               for svc, img in base_images.items()},
            "api": f"running\thealthy\t{API_TAG}\t{API_DIGEST}",
            "web": f"running\thealthy\t{WEB_TAG}\t{WEB_DIGEST}",
        }
        self.image_ids = image_ids if image_ids is not None else {
            API_TAG: API_DIGEST, WEB_TAG: WEB_DIGEST,
        }
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, *, timeout: float = 60.0, encoding: str | None = None) -> pdw.CommandResult:
        argv = tuple(str(item) for item in argv)
        self.calls.append(argv)
        if argv[0] != "docker":
            return pdw.CommandResult(argv, 127, "", "not-found")
        if argv[1] == "compose":
            stdout = self.ps_stdout if self.ps_stdout is not None else json.dumps(self.ps_rows)
            stderr = "" if self.ps_rc == 0 else "compose failed"
            return pdw.CommandResult(argv, self.ps_rc, stdout, stderr)
        if argv[1] == "inspect":
            service = argv[-1][len(self.project) + 1:].rsplit("-", 1)[0]
            facts = self.container_facts.get(service, None)
            if facts is None:
                return pdw.CommandResult(argv, 1, "", "no such object")
            return pdw.CommandResult(argv, 0, facts, "")
        if argv[1] == "image":
            digest = self.image_ids.get(argv[-1], None)
            if digest is None:
                return pdw.CommandResult(argv, 1, "", "no such image")
            return pdw.CommandResult(argv, 0, digest, "")
        return pdw.CommandResult(argv, 125, "", "unsupported")


def _block_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network is forbidden in this test")

    monkeypatch.setattr(socket, "socket", _forbidden)


def _block_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess is forbidden in this test")

    monkeypatch.setattr(subprocess_module, "run", _forbidden)


def _patch_gate(monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> dict[str, int]:
    """把 main 的 Runner 构造替换为计数工厂（零真实执行）。"""
    constructions = {"runner": 0}

    def make_runner() -> FakeRunner:
        constructions["runner"] += 1
        return runner

    monkeypatch.setattr(pdw, "RealRunner", make_runner)
    return constructions


def _latest_json(tmp_path: Path) -> dict[str, object]:
    files = sorted(tmp_path.glob("drift-watch-*.json"))
    assert files, "execute 报告未落盘"
    return json.loads(files[-1].read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 结构契约


def test_ast_import_allowlist_no_env_no_dynamic_exec() -> None:
    """AST 结构锁定：import 模块白名单（零网络模块）、零 env 读取 token、
    零 shell=True、零动态执行。"""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    allowed = {"__future__", "argparse", "json", "os", "re", "subprocess",
               "sys", "dataclasses", "datetime", "pathlib", "typing"}
    assert imported <= allowed, f"越界 import: {imported - allowed}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "run"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "subprocess"):
                assert all(kw.arg != "shell" for kw in node.keywords), \
                    "subprocess.run 禁止 shell= 关键字"
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("os.environ", ".environ", "environ[", "getenv", "urlopen",
                      "eval(", "exec(", "__import__"):
        assert forbidden not in source, f"源码出现禁用 token: {forbidden}"


def test_docs_document_the_tool() -> None:
    """ops README 与证据 README 登记本工具。"""
    ops_text = OPS_README.read_text(encoding="utf-8")
    assert "production_drift_watch.py" in ops_text
    assert "M14-127" in ops_text
    assert pdw.CONFIRM_PHRASE in ops_text
    evidence_text = EVIDENCE_README.read_text(encoding="utf-8")
    assert "production_drift_watch.py" in evidence_text
    assert pdw.CONFIRM_PHRASE in evidence_text


# ---------------------------------------------------------------- plan 面


def test_plan_mode_zero_side_effects(monkeypatch, tmp_path) -> None:
    """plan 默认零 subprocess/零 Docker/零网络（双阻断下照常出计划）。"""
    _block_sockets(monkeypatch)
    _block_subprocess(monkeypatch)
    rc = pdw.main(["--artifact-dir", str(tmp_path)])
    assert rc == 0
    plans = list(tmp_path.glob("plan-*.json"))
    assert plans, "plan 报告未落盘"


def test_plan_report_makes_no_status_claims(monkeypatch, tmp_path) -> None:
    """plan 报告零状态宣称：无 drift/checks/counts/collectors 事实。"""
    _block_sockets(monkeypatch)
    _block_subprocess(monkeypatch)
    rc = pdw.main(["--artifact-dir", str(tmp_path)])
    assert rc == 0
    report = json.loads(max(tmp_path.glob("plan-*.json")).read_text(encoding="utf-8"))
    assert report["mode"] == "plan"
    for absent in ("drift", "drift_reasons", "checks", "counts"):
        assert absent not in report
    assert report["collectors"]["status"] == "planned"


def test_plan_stdout_reveals_no_absolute_paths(monkeypatch, tmp_path, capsys) -> None:
    """plan stdout 不回显任何绝对本地路径。"""
    _block_sockets(monkeypatch)
    _block_subprocess(monkeypatch)
    pdw.main(["--artifact-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert str(tmp_path) not in captured.out
    assert str(SCRIPT.parents[2]) not in captured.out


# ---------------------------------------------------------------- execute 门禁


def test_execute_without_confirm_refused_zero_collection(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    runner = FakeRunner()
    constructions = _patch_gate(monkeypatch, runner)
    rc = pdw.main(["--execute", "--artifact-dir", str(tmp_path)])
    assert rc == 2
    assert constructions["runner"] == 0, "确认缺失时 Runner 必须零构造"
    assert not list(tmp_path.glob("drift-watch-*.json"))


@pytest.mark.parametrize("phrase", [
    "", "EXECUTE READ-ONLY PRODUCTION DRIFT WATCH ", " EXECUTE READ-ONLY PRODUCTION DRIFT WATCH",
    "execute read-only production drift watch", "EXECUTE READ-ONLY PRODUCTION DRIFT",
    "EXECUTE READ-ONLY PRODUCTION MONITORING",
])
def test_execute_wrong_phrase_refused(monkeypatch, tmp_path, phrase: str) -> None:
    _block_sockets(monkeypatch)
    runner = FakeRunner()
    constructions = _patch_gate(monkeypatch, runner)
    rc = pdw.main(["--execute", "--confirm", phrase, "--artifact-dir", str(tmp_path)])
    assert rc == 2
    assert constructions["runner"] == 0
    assert not list(tmp_path.glob("drift-watch-*.json"))


@pytest.mark.parametrize("bad_project", [
    "", " ", "_lead", "has space", "has/slash",
    "has\\backslash", "has:colon", "a" * 65, "名字", "proj\ndrop", "proj;rm",
])
def test_project_name_whitelist_rejected(monkeypatch, tmp_path, bad_project: str) -> None:
    """非法项目名在 plan 与 fully-confirmed-execute 双路径 fail-closed，
    被拒值绝不回显。"""
    _block_sockets(monkeypatch)
    runner = FakeRunner()
    constructions = _patch_gate(monkeypatch, runner)
    for argv_extra in ([], ["--execute", "--confirm", pdw.CONFIRM_PHRASE]):
        rc = pdw.main(["--project", bad_project, *argv_extra,
                       "--artifact-dir", str(tmp_path)])
        assert rc == 2, f"应拒绝非法项目名: {bad_project!r}"
        assert constructions["runner"] == 0


def test_leading_dash_project_rejected_by_argparse(monkeypatch, tmp_path) -> None:
    """前导 '-' 形态在 argparse 层即被拒（usage exit 2，零副作用）。"""
    _block_sockets(monkeypatch)
    with pytest.raises(SystemExit) as excinfo:
        pdw.main(["--project", "-lead", "--artifact-dir", str(tmp_path)])
    assert excinfo.value.code == 2


def test_rejected_project_value_not_echoed(monkeypatch, tmp_path, capsys) -> None:
    _block_sockets(monkeypatch)
    secret_project = "pwned/PROJECT-name-X"
    rc = pdw.main(["--project", secret_project, "--artifact-dir", str(tmp_path)])
    assert rc == 2
    captured = capsys.readouterr()
    assert secret_project not in captured.out


def test_valid_custom_project_accepted_in_plan(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    _block_subprocess(monkeypatch)
    rc = pdw.main(["--project", "aios-m14-127-drift-check", "--artifact-dir", str(tmp_path)])
    assert rc == 0


# ---------------------------------------------------------------- 子进程白名单门


def test_whitelist_allows_three_readonly_forms() -> None:
    compose_argv = ["docker", "compose", "-f", COMPOSE, "-p", PROJECT,
                    "--profile", "local", "--profile", "search", "ps",
                    "--format", "json"]
    assert pdw.is_readonly_docker_command(compose_argv)
    inspect_argv = ["docker", "inspect", "--format", pdw.CONTAINER_INSPECT_FORMAT,
                    f"{PROJECT}-api-1"]
    assert pdw.is_readonly_docker_command(inspect_argv)
    image_argv = ["docker", "image", "inspect", "--format", pdw.IMAGE_ID_FORMAT,
                  API_TAG]
    assert pdw.is_readonly_docker_command(image_argv)


@pytest.mark.parametrize("argv", [
    ["docker", "stop", f"{PROJECT}-api-1"],
    ["docker", "start", f"{PROJECT}-api-1"],
    ["docker", "restart", f"{PROJECT}-api-1"],
    ["docker", "rm", f"{PROJECT}-api-1"],
    ["docker", "kill", f"{PROJECT}-api-1"],
    ["docker", "compose", "-f", COMPOSE, "-p", PROJECT, "down"],
    ["docker", "compose", "-f", COMPOSE, "-p", PROJECT, "up", "-d"],
    ["docker", "compose", "-f", COMPOSE, "-p", PROJECT, "build", "api"],
    ["docker", "compose", "-f", COMPOSE, "-p", PROJECT, "pull"],
    ["docker", "compose", "-f", COMPOSE, "-p", PROJECT, "restart", "api"],
    ["docker", "compose", "-f", COMPOSE, "-p", PROJECT, "exec", "api", "sh"],
    ["docker", "compose", "-f", COMPOSE, "-p", PROJECT, "logs", "api"],
    ["docker", "compose", "-f", COMPOSE, "-p", PROJECT, "ps"],
    ["docker", "compose", "-f", COMPOSE, "-p", PROJECT, "ps", "--format", "json", "--all"],
    ["docker", "compose", "-f", COMPOSE, "-p", PROJECT, "ps", "--format", "table"],
    ["docker", "ps"],
    ["docker", "ps", "-a"],
    ["docker", "logs", "--tail", "10", f"{PROJECT}-api-1"],
    ["docker", "inspect", f"{PROJECT}-api-1"],
    ["docker", "inspect", "--format", "{{.Id}}", f"{PROJECT}-api-1"],
    ["docker", "inspect", "--format", pdw.CONTAINER_INSPECT_FORMAT + "\t{{.Name}}",
     f"{PROJECT}-api-1"],
    ["docker", "inspect", "--format", pdw.CONTAINER_INSPECT_FORMAT,
     f"{PROJECT}-api-1", f"{PROJECT}-web-1"],
    ["docker", "inspect", "--format", pdw.CONTAINER_INSPECT_FORMAT,
     f"{PROJECT}-api-1", "--size"],
    ["docker", "image", "inspect", API_TAG],
    ["docker", "image", "inspect", "--format", "{{.RepoDigests}}", API_TAG],
    ["docker", "image", "inspect", "--format", pdw.IMAGE_ID_FORMAT, API_TAG, WEB_TAG],
    ["docker", "image", "rm", API_TAG],
    ["docker", "image", "pull", API_TAG],
    ["docker", "images"],
    ["docker", "build", "."],
    ["docker", "exec", f"{PROJECT}-api-1", "env"],
    ["docker", "version"],
    ["kubectl", "get", "pods"],
    ["powershell", "-Command", "docker ps"],
    ["docker"],
    ["docker", "compose"],
])
def test_whitelist_rejects_everything_else(argv: list[str]) -> None:
    assert not pdw.is_readonly_docker_command(argv)


def test_readonly_runner_gate_blocks_before_execution() -> None:
    """白名单门在任何执行之前拒绝（ReadonlyRunner 直测）。"""
    runner = FakeRunner()

    class Boom:
        def run(self, *a, **k):  # pragma: no cover - 不应被触达
            raise AssertionError("inner runner must not be reached")

    gated = pdw.ReadonlyRunner(Boom())  # type: ignore[arg-type]
    with pytest.raises(pdw.CommandNotAllowedError):
        gated.run(["docker", "restart", f"{PROJECT}-api-1"])
    assert runner.calls == []


# ---------------------------------------------------------------- 采集与判定语义


def test_compose_ps_parser_accepts_three_shapes() -> None:
    normalized = [{"service": "api", "health": "healthy", "state": "running"}]
    rows = [{"Service": "api", "Health": "healthy", "State": "running"}]
    assert pdw.parse_compose_ps_rows(json.dumps(rows)) == normalized
    assert pdw.parse_compose_ps_rows(json.dumps(rows[0])) == normalized
    jsonl = "\n".join(json.dumps(r) for r in rows)
    assert pdw.parse_compose_ps_rows(jsonl) == normalized
    for garbage in ("", "not json", "[{broken", '{"NoService": 1}'):
        assert pdw.parse_compose_ps_rows(garbage) == []


def test_execute_healthy_no_drift_exit_zero(monkeypatch, tmp_path) -> None:
    """全绿快照：drift=false、exit 0、检查全 pass、报告落盘。"""
    _block_sockets(monkeypatch)
    runner = FakeRunner()
    _patch_gate(monkeypatch, runner)
    rc = pdw.main(["--execute", "--confirm", pdw.CONFIRM_PHRASE,
                   "--artifact-dir", str(tmp_path)])
    assert rc == 0
    report = _latest_json(tmp_path)
    assert report["drift"] is False
    assert report["drift_reasons"] == []
    counts = report["counts"]
    assert isinstance(counts, dict)
    assert counts["fail"] == 0
    assert counts["pass"] > 0
    # 采集面形状：compose ps 一次 + 7 容器 inspect + 2 镜像解析
    compose_calls = [c for c in runner.calls if c[1] == "compose"]
    inspect_calls = [c for c in runner.calls if c[1] == "inspect"]
    image_calls = [c for c in runner.calls if c[1] == "image"]
    assert len(compose_calls) == 1 and len(inspect_calls) == 7 and len(image_calls) == 2


def test_execute_drift_on_running_digest_mismatch(monkeypatch, tmp_path) -> None:
    """API 容器运行镜像 ID 与锚点不符 → drift + 固定原因 + exit 2。"""
    _block_sockets(monkeypatch)
    facts = dict(FakeRunner().container_facts)
    facts["api"] = f"running\thealthy\t{API_TAG}\t{OTHER_DIGEST}"
    runner = FakeRunner(container_facts=facts)
    _patch_gate(monkeypatch, runner)
    rc = pdw.main(["--execute", "--confirm", pdw.CONFIRM_PHRASE,
                   "--artifact-dir", str(tmp_path)])
    assert rc == 2
    report = _latest_json(tmp_path)
    assert report["drift"] is True
    assert "image-digest-mismatch:api" in report["drift_reasons"]


def test_execute_drift_on_running_tag_mismatch(monkeypatch, tmp_path) -> None:
    """Web 容器运行 tag 与锚点不符 → drift（即使 digest 侥幸相同）。"""
    _block_sockets(monkeypatch)
    facts = dict(FakeRunner().container_facts)
    facts["web"] = f"running\thealthy\taios/web:m14-117-production\t{WEB_DIGEST}"
    runner = FakeRunner(container_facts=facts)
    _patch_gate(monkeypatch, runner)
    rc = pdw.main(["--execute", "--confirm", pdw.CONFIRM_PHRASE,
                   "--artifact-dir", str(tmp_path)])
    assert rc == 2
    report = _latest_json(tmp_path)
    assert report["drift"] is True
    assert "image-tag-mismatch:web" in report["drift_reasons"]


def test_execute_drift_on_tag_resolution_mismatch(monkeypatch, tmp_path) -> None:
    """锚点 tag 本地解析到别的镜像（tag 被移动）→ drift。"""
    _block_sockets(monkeypatch)
    runner = FakeRunner(image_ids={API_TAG: API_DIGEST, WEB_TAG: OTHER_DIGEST})
    _patch_gate(monkeypatch, runner)
    rc = pdw.main(["--execute", "--confirm", pdw.CONFIRM_PHRASE,
                   "--artifact-dir", str(tmp_path)])
    assert rc == 2
    report = _latest_json(tmp_path)
    assert report["drift"] is True
    assert "image-tag-resolution-mismatch:web" in report["drift_reasons"]


def test_execute_fail_closed_when_tag_resolution_missing(monkeypatch, tmp_path) -> None:
    """image inspect 失败（本地 tag 不可解析）→ fail-closed：即使容器运行
    tag 与 digest 全部吻合，缺失的第三重证据也判 drift——tag 相同绝不
    冒充 digest/解析证明通过。"""
    _block_sockets(monkeypatch)
    runner = FakeRunner(image_ids={API_TAG: API_DIGEST, WEB_TAG: None})
    _patch_gate(monkeypatch, runner)
    rc = pdw.main(["--execute", "--confirm", pdw.CONFIRM_PHRASE,
                   "--artifact-dir", str(tmp_path)])
    assert rc == 2
    report = _latest_json(tmp_path)
    assert report["drift"] is True
    assert "image-tag-resolution-unavailable:web" in report["drift_reasons"]


def test_execute_fail_closed_when_anchor_container_facts_missing(monkeypatch, tmp_path) -> None:
    """API 容器 inspect 失败 → 锚点三检查全 fail（缺失绝不当作匹配）。"""
    _block_sockets(monkeypatch)
    facts = dict(FakeRunner().container_facts)
    facts["api"] = None
    runner = FakeRunner(container_facts=facts)
    _patch_gate(monkeypatch, runner)
    rc = pdw.main(["--execute", "--confirm", pdw.CONFIRM_PHRASE,
                   "--artifact-dir", str(tmp_path)])
    assert rc == 2
    report = _latest_json(tmp_path)
    assert report["drift"] is True
    assert "container-facts-missing:api" in report["drift_reasons"]
    assert "anchor-facts-missing:api" in report["drift_reasons"]


def test_execute_fail_closed_on_malformed_digest(monkeypatch, tmp_path) -> None:
    """运行镜像 ID 非 sha256:<64hex> 精确形态（如仅 tag 字符串）→
    digest-unobtainable，绝不按 tag 冒充通过。"""
    _block_sockets(monkeypatch)
    facts = dict(FakeRunner().container_facts)
    facts["api"] = f"running\thealthy\t{API_TAG}\t{API_TAG}"
    runner = FakeRunner(container_facts=facts)
    _patch_gate(monkeypatch, runner)
    rc = pdw.main(["--execute", "--confirm", pdw.CONFIRM_PHRASE,
                   "--artifact-dir", str(tmp_path)])
    assert rc == 2
    report = _latest_json(tmp_path)
    assert report["drift"] is True
    assert "image-digest-unobtainable:api" in report["drift_reasons"]


@pytest.mark.parametrize("value,expected", [
    ("sha256:" + "a" * 64, "a" * 64),
    ("sha256:" + "0" * 64, "0" * 64),
    (" sha256:" + "a" * 64 + " ", "a" * 64),
    ("sha256:" + "A" * 64, None),
    ("sha256:" + "a" * 63, None),
    ("sha256:" + "a" * 65, None),
    ("a" * 64, None),
    ("aios/api:m14-124-production", None),
    ("", None),
])
def test_normalize_image_digest_matrix(value: str, expected: str | None) -> None:
    assert pdw.normalize_image_digest(value) == expected


def test_execute_drift_on_unhealthy_service(monkeypatch, tmp_path) -> None:
    """postgres unhealthy（compose ps 与 inspect 双通道）→ drift。"""
    _block_sockets(monkeypatch)
    ps_rows = [{"Service": svc, "Health": "healthy" if svc != "postgres" else "unhealthy",
                "State": "running"} for svc in SERVICES]
    facts = dict(FakeRunner().container_facts)
    facts["postgres"] = "running\tunhealthy\tpostgres:17-alpine\tsha256:" + "a" * 64
    runner = FakeRunner(ps_rows=ps_rows, container_facts=facts)
    _patch_gate(monkeypatch, runner)
    rc = pdw.main(["--execute", "--confirm", pdw.CONFIRM_PHRASE,
                   "--artifact-dir", str(tmp_path)])
    assert rc == 2
    report = _latest_json(tmp_path)
    assert report["drift"] is True
    assert "compose-service-unhealthy:postgres" in report["drift_reasons"]
    assert "container-not-healthy:postgres" in report["drift_reasons"]


def test_execute_drift_on_missing_service(monkeypatch, tmp_path) -> None:
    """compose ps 缺 searxng → compose-service-missing（即使容器 inspect
    侧照常回放健康事实——存在性缺失即 drift）。"""
    _block_sockets(monkeypatch)
    ps_rows = [{"Service": svc, "Health": "healthy", "State": "running"}
               for svc in SERVICES if svc != "searxng"]
    runner = FakeRunner(ps_rows=ps_rows)
    _patch_gate(monkeypatch, runner)
    rc = pdw.main(["--execute", "--confirm", pdw.CONFIRM_PHRASE,
                   "--artifact-dir", str(tmp_path)])
    assert rc == 2
    report = _latest_json(tmp_path)
    assert report["drift"] is True
    assert "compose-service-missing:searxng" in report["drift_reasons"]


def test_execute_fail_closed_on_compose_ps_garbage(monkeypatch, tmp_path) -> None:
    """compose ps 输出不可解析 → 采集失败如实入档 + drift（缺失≠健康）。"""
    _block_sockets(monkeypatch)
    runner = FakeRunner(ps_stdout="not json at all")
    _patch_gate(monkeypatch, runner)
    rc = pdw.main(["--execute", "--confirm", pdw.CONFIRM_PHRASE,
                   "--artifact-dir", str(tmp_path)])
    assert rc == 2
    report = _latest_json(tmp_path)
    assert report["drift"] is True
    assert "collector-failed:compose-ps" in report["drift_reasons"]


def test_execute_fail_closed_on_compose_ps_nonzero(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    runner = FakeRunner(ps_rc=1)
    _patch_gate(monkeypatch, runner)
    rc = pdw.main(["--execute", "--confirm", pdw.CONFIRM_PHRASE,
                   "--artifact-dir", str(tmp_path)])
    assert rc == 2
    report = _latest_json(tmp_path)
    assert "collector-failed:compose-ps" in report["drift_reasons"]


def test_execute_drift_on_container_state_not_running(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    facts = dict(FakeRunner().container_facts)
    facts["redis"] = "exited\thealthy\tredis:7-alpine\tsha256:" + "a" * 64
    runner = FakeRunner(container_facts=facts)
    _patch_gate(monkeypatch, runner)
    rc = pdw.main(["--execute", "--confirm", pdw.CONFIRM_PHRASE,
                   "--artifact-dir", str(tmp_path)])
    assert rc == 2
    report = _latest_json(tmp_path)
    assert "container-not-healthy:redis" in report["drift_reasons"]


def test_execute_uses_jsonl_compose_output(monkeypatch, tmp_path) -> None:
    """真实 compose 5.x 的 JSONL 形态同样可解析（healthy 全绿）。"""
    _block_sockets(monkeypatch)
    rows = [{"Service": svc, "Health": "healthy", "State": "running"} for svc in SERVICES]
    jsonl = "\n".join(json.dumps(r) for r in rows)
    runner = FakeRunner(ps_stdout=jsonl)
    _patch_gate(monkeypatch, runner)
    rc = pdw.main(["--execute", "--confirm", pdw.CONFIRM_PHRASE,
                   "--artifact-dir", str(tmp_path)])
    assert rc == 0
    assert _latest_json(tmp_path)["drift"] is False


# ---------------------------------------------------------------- 报告纪律


def test_execute_report_has_timestamps_boundaries_and_anchors(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    runner = FakeRunner()
    _patch_gate(monkeypatch, runner)
    pdw.main(["--execute", "--confirm", pdw.CONFIRM_PHRASE,
              "--artifact-dir", str(tmp_path)])
    report = _latest_json(tmp_path)
    assert report["schema_version"] == pdw.REPORT_SCHEMA_VERSION
    assert report["tool"] == pdw.TOOL_NAME
    assert report["milestone"] == "M14-127"
    assert report["mode"] == "execute"
    for key in ("started_at_utc", "ended_at_utc"):
        value = report[key]
        assert isinstance(value, str) and value.endswith("Z")
    anchors = report["config"]["anchors"]
    assert isinstance(anchors, dict)
    assert anchors["api"]["expected_digest"] == API_DIGEST
    assert anchors["web"]["expected_digest"] == WEB_DIGEST
    boundaries = report["boundaries"]
    assert isinstance(boundaries, list) and boundaries


def test_report_redaction_terminal_defense(monkeypatch, tmp_path, capsys) -> None:
    """fake docker 输出注入标记 token/凭据形态值（进入被保留字段：Health 与
    运行 tag）→ 写盘/打印终防线全部脱敏，绝不以原形进入 JSON/MD/stdout。"""
    _block_sockets(monkeypatch)
    ps_rows = [{"Service": svc,
                "Health": MARK_TOKEN if svc == "postgres" else "healthy",
                "State": "running"} for svc in SERVICES]
    facts = dict(FakeRunner().container_facts)
    facts["web"] = f"running\thealthy\t{WEB_TAG};{MARK_CREDS}\t{WEB_DIGEST}"
    runner = FakeRunner(ps_rows=ps_rows, container_facts=facts)
    _patch_gate(monkeypatch, runner)
    rc = pdw.main(["--execute", "--confirm", pdw.CONFIRM_PHRASE,
                   "--artifact-dir", str(tmp_path)])
    assert rc == 2  # postgres health 非 healthy + web tag 漂移 → 如实 drift
    json_text = max(tmp_path.glob("drift-watch-*.json")).read_text(encoding="utf-8")
    md_text = max(tmp_path.glob("drift-watch-*.md")).read_text(encoding="utf-8")
    captured = capsys.readouterr()
    for output in (json_text, md_text, captured.out):
        assert MARK_TOKEN not in output
        assert "ZXpwdmarker99887766" not in output


def test_execute_report_contains_no_absolute_paths(monkeypatch, tmp_path) -> None:
    """compose ps 原始 Labels/Ports（含绝对路径）绝不进入报告。"""
    _block_sockets(monkeypatch)
    secret_path = r"D:\\leak\\env.production-recovery"
    rows = []
    for svc in SERVICES:
        row = {"Service": svc, "Health": "healthy", "State": "running",
               "Labels": f"com.docker.compose.project.environment_file={secret_path}",
               "Ports": "127.0.0.1:8000->8000/tcp"}
        rows.append(row)
    runner = FakeRunner(ps_stdout="\n".join(json.dumps(r) for r in rows))
    _patch_gate(monkeypatch, runner)
    rc = pdw.main(["--execute", "--confirm", pdw.CONFIRM_PHRASE,
                   "--artifact-dir", str(tmp_path)])
    assert rc == 0
    json_text = max(tmp_path.glob("drift-watch-*.json")).read_text(encoding="utf-8")
    md_text = max(tmp_path.glob("drift-watch-*.md")).read_text(encoding="utf-8")
    assert "leak" not in json_text and "leak" not in md_text
    assert "environment_file" not in json_text and "environment_file" not in md_text


def _plan_style_report() -> dict[str, object]:
    return pdw.build_report(
        mode="plan", started_utc="2026-09-25T00:00:00Z",
        ended_utc="2026-09-25T00:00:00Z",
        config=pdw.build_config(project=PROJECT, profiles=pdw.COMPOSE_PROFILES,
                                compose_file=pdw.COMPOSE_FILE,
                                services=pdw.STACK_SERVICES,
                                anchors=pdw.EXPECTED_ANCHORS))


def test_atomic_write_no_tmp_residue_and_overwrite(monkeypatch, tmp_path) -> None:
    """tmp+os.replace 原子写：无 .tmp 残留；同 stem 重写干净覆盖。"""
    report = _plan_style_report()
    json_path, md_path = pdw.write_reports_atomic(report, tmp_path, "stem-1")
    assert json_path.exists() and md_path.exists()
    assert not list(tmp_path.glob("*.tmp"))
    pdw.write_reports_atomic(report, tmp_path, "stem-1")
    assert not list(tmp_path.glob("*.tmp"))
    assert json.loads(json_path.read_text(encoding="utf-8"))["tool"] == pdw.TOOL_NAME


@pytest.mark.parametrize("bad_stem", ["../evil", "a/b", "a\\b", "..", "."])
def test_bad_report_stem_rejected(tmp_path, bad_stem: str) -> None:
    report = _plan_style_report()
    with pytest.raises(pdw.ReportPathError):
        pdw.write_reports_atomic(report, tmp_path, bad_stem)
    assert not list(tmp_path.glob("*.json"))
