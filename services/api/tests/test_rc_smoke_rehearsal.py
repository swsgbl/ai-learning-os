r"""M14-96 RC 本地彩排冒烟契约测试：隔离 compose 文件 + fail-closed runner
+ 生产默认零漂移回归。

三层契约：

1. **infra/docker-compose.rc-smoke.yml 静态契约**（零依赖原始文本断言，无
   Docker/PyYAML 也能跑）：任务自有项目名/恰好五服务/零 build 段/
   pull_policy never/api-web tag 必填变量注入/数据面零宿主端口/边缘面
   127.0.0.1:18096+13096 独占/零 restart 策略/卷仅项目级两卷/无 profiles
   /healthcheck 与 depends_on 与生产 compose 逐块一致。
2. **生产默认零漂移回归**：infra/docker-compose.yml、
   infra/build_release_candidate.sh、infra/smoke_docker.sh 三文件 LF 归一化
   字节级 sha256 pin（M14-96 基点 c9de722 的 git blob 哈希——任何对生产
   面文件的改动都会击穿 pin，必须显式改 pin 才能通过）+ 生产语义不变式
   （name=ai-learning-os、7 服务 restart: unless-stopped）。
3. **tools/ops/rc_smoke_rehearsal.py runner 契约**（子进程/HTTP/端口
   bind/时钟全部经注入替身，绝不触碰真实 docker/git/网络）：tag 推导/
   前置拒绝面（base-sha 错位、脏构建输入、端口占用、项目残留、镜像 tag
   已存在、infra 镜像缺失、compose config 渲染失败、输出目录已存在）/
   构建与 up 命令形态（-f+-p 恒在、AIOS_* 剥离、tag env 注入）→ 健康轮询
   → 端口绑定证明 → 八项探针（/health、version==VERSION、auth_enabled、
   匿名保护读 401、匿名保护写 401、Web / 与 /login、CORS preflight 回显）
   → 无论成败恒拆本项目（down --volumes --remove-orphans）→ 前后
   docker ps/volume/network/compose-ls 快照等价（零外部漂移证明）→
   证据 + SHA256SUMS 清单。

有 Docker CLI 时追加 compose config 渲染断言（零容器、纯渲染，与
test_compose_restart_policy.py 同款只读门）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SMOKE_COMPOSE = REPO_ROOT / "infra" / "docker-compose.rc-smoke.yml"
BASE_COMPOSE = REPO_ROOT / "infra" / "docker-compose.yml"
RC_BUILD_SCRIPT = REPO_ROOT / "infra" / "build_release_candidate.sh"
SMOKE_DOCKER_SCRIPT = REPO_ROOT / "infra" / "smoke_docker.sh"
RUNNER_PATH = REPO_ROOT / "tools" / "ops" / "rc_smoke_rehearsal.py"

#: 生产面三文件的 git blob（LF）sha256——`git show <base>:<path> | sha256sum`
#: 逐字登记，LF 归一化后对 pin。初始登记于 M14-96 基点 c9de722；M14-100
#: 显式更新 docker-compose.yml pin（新增 LLM_TIMEOUT_SECONDS 空默认透传
#: 槽位——空值 = gateway 既有默认 30s，生产渲染零漂移；见
#: docs/evidence/m14-100-local-llm-timeout-budget/），另两文件仍为
#: c9de722 原始登记。
PRODUCTION_FILE_PINS = {
    "infra/docker-compose.yml":
        "d2e4f4939fa5effdbb303dff9c3502daaff057ea08a4e69aaf12cb640efdc598",
    "infra/build_release_candidate.sh":
        "3efa02586c8a93a1d4b26f1fc981fd1233a87ae94be797cd3f939e9b464778b2",
    "infra/smoke_docker.sh":
        "5f346e6b4d32315abfc1e1ef64f2a08331b9d526a2f082640eb42e32f1cec9cf",
}

BASE_SHA = "c9de72214fbe07f41f5d6706e1c72ccb7e60cac3"
PROJECT_NAME = "aios-m14-96-rc-smoke"
TASK_LABEL = "m14-96-rc-smoke"
API_PORT = 18096
WEB_PORT = 13096
SMOKE_SERVICES = ("postgres", "redis", "minio", "api", "web")
API_TAG = f"aios/api:{TASK_LABEL}-{BASE_SHA}"
WEB_TAG = f"aios/web:{TASK_LABEL}-{BASE_SHA}"
INFRA_IMAGES = {
    "postgres": "postgres:17-alpine",
    "redis": "redis:7-alpine",
    "minio": "aios/minio:RELEASE.2025-10-15T17-29-55Z",
}


# ---------------------------------------------------------------- 工具函数


def read_norm(path: Path) -> str:
    """读取并 LF 归一化（worktree 可能 CRLF 检出，pin 与文本断言统一 LF 口径）。"""
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def sha256_norm(path: Path) -> str:
    return hashlib.sha256(read_norm(path).encode("utf-8")).hexdigest()


def load_runner():
    spec = importlib.util.spec_from_file_location("rc_smoke_rehearsal", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    __import__("sys").modules["rc_smoke_rehearsal"] = module  # dataclass 需要
    spec.loader.exec_module(module)
    return module


def service_block(text: str, name: str) -> str:
    """按两空格缩进服务头切出单个服务块（含其嵌套行，直到下一个同级键）。"""
    lines = text.split("\n")
    out: list[str] = []
    inside = False
    for line in lines:
        if re.fullmatch(rf"  {re.escape(name)}:\s*", line):
            inside = True
            continue
        if inside:
            if line.startswith("  ") and not line.startswith("   ") and line.strip():
                break  # 下一个同级服务/顶级键
            if line.startswith("    ") or not line.strip():
                out.append(line)
    return "\n".join(out)


def sub_block(block: str, key: str) -> str:
    """服务块内 4 空格缩进子键块（如 healthcheck / environment / ports）。

    注释行剥离——块语义相等比较不受注释增减影响。"""
    lines = block.split("\n")
    out: list[str] = []
    inside = False
    for line in lines:
        if re.fullmatch(rf"    {re.escape(key)}:\s*", line):
            inside = True
            continue
        if inside:
            if line.startswith("    ") and not line.startswith("     ") and line.strip():
                break
            if line.startswith("     ") or not line.strip():
                if line.strip().startswith("#"):
                    continue
                out.append(line)
    return "\n".join(out)


# ================================================================ 1. compose 静态契约


def test_smoke_compose_file_exists() -> None:
    assert SMOKE_COMPOSE.is_file(), "infra/docker-compose.rc-smoke.yml 必须存在"


def test_smoke_compose_project_name_pinned_and_unique() -> None:
    text = read_norm(SMOKE_COMPOSE)
    assert re.search(r"^name: aios-m14-96-rc-smoke\s*$", text, re.MULTILINE)
    assert "ai-learning-os\n" not in text  # 绝不共用生产/开发项目名
    assert "aios-m14-03-production-rehearsal" not in text


def test_smoke_compose_service_set_exactly_five() -> None:
    text = read_norm(SMOKE_COMPOSE)
    services_section = text.split("\nservices:\n", 1)[1].split("\nvolumes:", 1)[0]
    services = re.findall(r"^  ([a-z][a-z0-9_-]*):\s*$", services_section, re.MULTILINE)
    assert set(services) == set(SMOKE_SERVICES)
    assert "livekit" not in services and "searxng" not in services
    assert "profiles" not in text  # 冒烟栈无 profile 面，恒为固定五服务


def test_smoke_compose_no_build_sections() -> None:
    text = read_norm(SMOKE_COMPOSE)
    assert not re.search(r"^    build:", text, re.MULTILINE), "冒烟 compose 零 build 段（镜像由 runner 显式构建）"


def test_smoke_compose_pull_policy_never_everywhere() -> None:
    text = read_norm(SMOKE_COMPOSE)
    assert len(re.findall(r"^    pull_policy: never\s*$", text, re.MULTILINE)) == len(SMOKE_SERVICES)


def test_smoke_compose_required_tag_interpolation() -> None:
    api = service_block(read_norm(SMOKE_COMPOSE), "api")
    web = service_block(read_norm(SMOKE_COMPOSE), "web")
    api_image = re.search(r'^    image: (.+)$', api, re.MULTILINE).group(1)  # type: ignore[union-attr]
    web_image = re.search(r'^    image: (.+)$', web, re.MULTILINE).group(1)  # type: ignore[union-attr]
    # 必填变量（:? 语义）：缺 env 即 compose 渲染失败，绝不静默回落 local/生产 tag
    assert api_image.startswith("aios/api:${AIOS_RC_SMOKE_API_TAG:")
    assert ":?" in api_image
    assert web_image.startswith("aios/web:${AIOS_RC_SMOKE_WEB_TAG:")
    assert ":?" in web_image


def test_smoke_compose_infra_images_pinned_same_as_base() -> None:
    smoke = read_norm(SMOKE_COMPOSE)
    base = read_norm(BASE_COMPOSE)
    for svc, image in INFRA_IMAGES.items():
        smoke_image = re.search(r"^    image: (.+)$", service_block(smoke, svc), re.MULTILINE)
        base_image = re.search(r"^    image: (.+)$", service_block(base, svc), re.MULTILINE)
        assert smoke_image is not None and base_image is not None, svc
        # 冒烟栈基础设施镜像与生产 compose 同一 pin（零硬编码副本漂移面）
        assert smoke_image.group(1) == base_image.group(1) == image


def test_smoke_compose_edge_ports_loopback_literal_unique() -> None:
    text = read_norm(SMOKE_COMPOSE)
    api = service_block(text, "api")
    web = service_block(text, "web")
    api_ports = sub_block(api, "ports")
    web_ports = sub_block(web, "ports")
    assert re.search(r'- "127\.0\.0\.1:18096:8000"', api_ports)
    assert re.search(r'- "127\.0\.0\.1:13096:3000"', web_ports)
    # 端口块内不得有任何其他映射行（含非 loopback 绑定）
    assert len([ln for ln in api_ports.split("\n") if ln.strip()]) == 1
    assert len([ln for ln in web_ports.split("\n") if ln.strip()]) == 1


def test_smoke_compose_data_services_publish_no_host_ports() -> None:
    text = read_norm(SMOKE_COMPOSE)
    for svc in ("postgres", "redis", "minio"):
        block = service_block(text, svc)
        assert "ports:" not in block, f"{svc} 冒烟栈不得发布宿主端口（健康经容器 healthcheck 证明）"
        assert not re.search(r'^      - "', block, re.MULTILINE), f"{svc} 不得有任何端口映射/挂载发布行"


def _base_host_ports() -> set[int]:
    """解析生产 compose 全部宿主发布端口（含 ${VAR:-default} 缺省解析与区间展开）。"""
    base = read_norm(BASE_COMPOSE)
    section = base.split("\nservices:\n", 1)[1].split("\nvolumes:", 1)[0]
    ports: set[int] = set()
    for match in re.finditer(r'^      - "?([^"\n]+?)"?$', section, re.MULTILINE):
        mapping = match.group(1)
        prev = None  # 迭代展开全部 ${VAR:-default}（含嵌套，内层优先）
        while prev != mapping:
            prev = mapping
            mapping = re.sub(r"\$\{[^{}:]*:-([^{}]*)\}", r"\1", mapping)
        parts = mapping.split(":")
        if len(parts) != 3:
            continue
        host_port = parts[1]
        if not re.fullmatch(r"\d+(?:-\d+)?", host_port):
            continue
        if "-" in host_port:
            lo, hi = (int(p) for p in host_port.split("-"))
            ports.update(range(lo, hi + 1))
        else:
            ports.add(int(host_port))
    return ports


def test_smoke_compose_ports_disjoint_from_production_compose() -> None:
    smoke_host_ports = {API_PORT, WEB_PORT}
    base_host_ports = _base_host_ports()
    # 生产 compose 的宿主端口全集（5433/6379/9000/9001/8000/3000/7880-7892/8878）
    assert {5433, 6379, 9000, 9001, 8000, 3000, 8878} <= base_host_ports
    assert 7880 in base_host_ports and 7892 in base_host_ports
    assert smoke_host_ports & base_host_ports == set(), "冒烟宿主端口必须与生产 compose 端口全集零交集"


def test_smoke_compose_no_restart_policy() -> None:
    text = read_norm(SMOKE_COMPOSE)
    assert "restart:" not in text, "一次性冒烟栈零 restart 策略（默认 no，绝不 unless-stopped 自愈混同）"


def test_smoke_compose_volumes_project_scoped_only() -> None:
    text = read_norm(SMOKE_COMPOSE)
    volumes_block = text.split("\nvolumes:\n", 1)[1]
    declared = re.findall(r"^  ([a-z][a-z0-9_-]*):\s*$", volumes_block, re.MULTILINE)
    assert set(declared) == {"postgres-data", "minio-data"}
    assert "\nnetworks:" not in text  # 无自定义网络面（项目默认网络随项目名隔离）
    # 服务块内只允许命名卷挂载（冒号后是卷名前缀），绝无宿主 bind mount
    for match in re.finditer(r"- ([A-Za-z0-9_.-]+):(/[^\s:]+)", text):
        left = match.group(1)
        assert not left.startswith(("/", ".")), f"冒烟栈禁止宿主 bind mount: {left}"


def test_smoke_compose_healthchecks_equal_base() -> None:
    smoke = read_norm(SMOKE_COMPOSE)
    base = read_norm(BASE_COMPOSE)
    for svc in SMOKE_SERVICES:
        smoke_hc = sub_block(service_block(smoke, svc), "healthcheck")
        base_hc = sub_block(service_block(base, svc), "healthcheck")
        assert smoke_hc.strip(), f"{svc} 缺 healthcheck"
        assert smoke_hc == base_hc, f"{svc} healthcheck 必须与生产 compose 逐字一致"


def test_smoke_compose_depends_on_wiring() -> None:
    smoke = read_norm(SMOKE_COMPOSE)
    api_dep = sub_block(service_block(smoke, "api"), "depends_on")
    web_dep = sub_block(service_block(smoke, "web"), "depends_on")
    base = read_norm(BASE_COMPOSE)
    base_api_dep = sub_block(service_block(base, "api"), "depends_on")
    base_web_dep = sub_block(service_block(base, "web"), "depends_on")
    assert api_dep == base_api_dep, "api depends_on 必须与生产一致（pg/redis/minio healthy）"
    assert web_dep == base_web_dep, "web depends_on 必须与生产一致（api healthy）"
    assert "livekit" not in api_dep


def test_smoke_compose_api_env_parities_with_base_except_cors() -> None:
    smoke_env = sub_block(service_block(read_norm(SMOKE_COMPOSE), "api"), "environment")
    base_env = sub_block(service_block(read_norm(BASE_COMPOSE), "api"), "environment")

    def env_map(block: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for line in block.split("\n"):
            match = re.fullmatch(r"      ([A-Z0-9_]+): (.*)", line)
            if match:
                out[match.group(1)] = match.group(2)
        return out

    smoke_map, base_map = env_map(smoke_env), env_map(base_env)
    assert set(smoke_map) == set(base_map), "api env 键集必须与生产 compose 完全一致（零增零删）"
    for key, value in base_map.items():
        if key == "CORS_ORIGINS":
            continue
        assert smoke_map[key] == value, f"api env {key} 必须与生产 compose 逐字一致"
    cors = smoke_map["CORS_ORIGINS"]
    assert "13096" in cors and "${AIOS_CORS_ORIGINS:-" in cors
    assert "3000" not in cors, "冒烟 CORS 默认只指向冒烟 Web 端口"
    base_cors = base_map["CORS_ORIGINS"]
    assert "3000" in base_cors, "生产 compose CORS 默认仍指向 3000（零漂移回归）"


def test_smoke_compose_web_env_equal_base() -> None:
    smoke_env = sub_block(service_block(read_norm(SMOKE_COMPOSE), "web"), "environment")
    base_env = sub_block(service_block(read_norm(BASE_COMPOSE), "web"), "environment")
    assert smoke_env == base_env


# ================================================================ 2. 生产默认零漂移回归


@pytest.mark.parametrize("rel_path,expected_sha", sorted(PRODUCTION_FILE_PINS.items()))
def test_production_files_byte_equivalent_to_base(rel_path: str, expected_sha: str) -> None:
    """M14-96 交付承诺：生产面三文件与基点 c9de722 字节级等价（LF 口径）。

    未来任务若有意修改生产面文件，必须显式更新 pin（并重新对齐基点）——
    pin 击穿即回归失败，不存在静默漂移路径。"""
    assert sha256_norm(REPO_ROOT / rel_path) == expected_sha


def test_production_compose_semantics_unchanged() -> None:
    text = read_norm(BASE_COMPOSE)
    assert re.search(r"^name: ai-learning-os\s*$", text, re.MULTILINE)
    restarts = re.findall(r"^    restart: unless-stopped\s*$", text, re.MULTILINE)
    assert len(restarts) == 7, "生产 compose 7 长驻服务 restart: unless-stopped 必须原样保留"


def test_smoke_compose_is_opt_in_only() -> None:
    """新增 compose 面默认 no-op：生产 compose 与 RC 构建器绝不引用冒烟文件。"""
    base = read_norm(BASE_COMPOSE)
    assert "rc-smoke" not in base
    rc_script = read_norm(RC_BUILD_SCRIPT)
    assert "rc-smoke" not in rc_script
    assert "docker-compose.rc-smoke.yml" not in rc_script


# ================================================================ 3. compose 渲染契约（有 Docker 时）


def _docker_available() -> bool:
    return shutil.which("docker") is not None and SMOKE_COMPOSE.is_file() and BASE_COMPOSE.is_file()


def _render(compose_file: Path, project: str | None, env: dict[str, str]) -> dict:
    cmd = ["docker", "compose", "-f", str(compose_file)]
    if project:
        cmd += ["-p", project]
    cmd += ["config", "--format", "json"]
    clean = {k: v for k, v in __import__("os").environ.items()
             if not k.startswith("AIOS_")}
    result = subprocess.run(
        cmd, check=False, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
        env={**clean, **env},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(not _docker_available(), reason="需要 docker compose CLI（只读 config 渲染，零容器）")
def test_smoke_compose_render_contract() -> None:
    render = _render(
        SMOKE_COMPOSE, PROJECT_NAME,
        {"AIOS_RC_SMOKE_API_TAG": "unit-api", "AIOS_RC_SMOKE_WEB_TAG": "unit-web"},
    )
    assert render["name"] == PROJECT_NAME
    assert set(render["services"]) == set(SMOKE_SERVICES)
    svc = render["services"]
    assert svc["api"]["image"] == "aios/api:unit-api"
    assert svc["web"]["image"] == "aios/web:unit-web"
    assert svc["postgres"]["image"] == INFRA_IMAGES["postgres"]
    assert svc["redis"]["image"] == INFRA_IMAGES["redis"]
    assert svc["minio"]["image"] == INFRA_IMAGES["minio"]
    for name in SMOKE_SERVICES:
        assert svc[name]["pull_policy"] == "never"
        assert "build" not in svc[name]
        assert "restart" not in svc[name]
    api_ports = svc["api"]["ports"]
    web_ports = svc["web"]["ports"]
    assert len(api_ports) == 1 and len(web_ports) == 1
    assert api_ports[0]["host_ip"] == "127.0.0.1"
    assert str(api_ports[0]["published"]) == "18096" and api_ports[0]["target"] == 8000
    assert web_ports[0]["host_ip"] == "127.0.0.1"
    assert str(web_ports[0]["published"]) == "13096" and web_ports[0]["target"] == 3000
    for name in ("postgres", "redis", "minio"):
        assert "ports" not in svc[name]


@pytest.mark.skipif(not _docker_available(), reason="需要 docker compose CLI（只读 config 渲染，零容器）")
def test_smoke_compose_render_fails_closed_without_tag_env() -> None:
    cmd = ["docker", "compose", "-f", str(SMOKE_COMPOSE), "-p", PROJECT_NAME, "config", "--quiet"]
    result = subprocess.run(
        cmd, check=False, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
        env={k: v for k, v in __import__("os").environ.items()
             if not k.startswith("AIOS_RC_SMOKE_")},
    )
    assert result.returncode != 0, "缺必填 tag 变量必须渲染失败（fail-closed，绝不回落默认 tag）"
    assert "AIOS_RC_SMOKE_API_TAG" in result.stderr


@pytest.mark.skipif(not _docker_available(), reason="需要 docker compose CLI（只读 config 渲染，零容器）")
def test_smoke_compose_render_env_and_health_parity_with_base() -> None:
    smoke = _render(SMOKE_COMPOSE, PROJECT_NAME,
                    {"AIOS_RC_SMOKE_API_TAG": "unit-api", "AIOS_RC_SMOKE_WEB_TAG": "unit-web"})
    base = _render(BASE_COMPOSE, "unit-base-render", {})
    smoke_api_env = smoke["services"]["api"]["environment"]
    base_api_env = base["services"]["api"]["environment"]
    assert set(smoke_api_env) == set(base_api_env)
    for key, value in base_api_env.items():
        if key == "CORS_ORIGINS":
            assert smoke_api_env[key] == "http://localhost:13096,http://127.0.0.1:13096"
            assert "3000" in value  # 生产默认零漂移
        else:
            assert smoke_api_env[key] == value, key
    assert (smoke["services"]["web"]["environment"]
            == base["services"]["web"]["environment"])
    for name in SMOKE_SERVICES:
        assert (smoke["services"][name]["healthcheck"]
                == base["services"][name]["healthcheck"]), name
    # depends_on 条件与生产一致（渲染面）
    assert (smoke["services"]["api"]["depends_on"]
            == base["services"]["api"]["depends_on"])
    assert (smoke["services"]["web"]["depends_on"]
            == base["services"]["web"]["depends_on"])


# ================================================================ 4. runner 契约（全替身，零真实 docker/git/网络）


class FakeCmd:
    """CommandRunner 替身：按 argv 前缀脚本化返回，记录全部调用。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.script: dict[tuple[str, ...], object] = {}

    def add(self, key: tuple[str, ...], result: object) -> None:
        self.script[key] = result

    def run(self, argv: list[str], env=None, cwd=None, timeout=None):
        self.calls.append({"argv": list(argv), "env": env, "cwd": cwd})
        result = None
        best = 0
        for key in self.script:
            if tuple(argv[:len(key)]) == key and len(key) > best:
                result = self.script[key]
                best = len(key)
        if result is None:
            return SimpleResult(1, "", f"unscripted: {' '.join(argv[:6])}")
        if callable(result):
            result = result(argv)
        if isinstance(result, Exception):
            raise result
        return result


class SimpleResult:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


COMPOSE_LS_JSON = json.dumps([
    {"Name": "aios-m14-03-production-rehearsal", "Status": "running(7)",
     "ConfigFiles": "D:/x/infra/docker-compose.yml"}])

_CID_KIND = {"c4": "api", "c5": "web"}  # compose ps -q 顺序：pg/redis/minio/api/web


class FakeProbe:
    """HTTP 探针替身：按 (method, path) 脚本化响应，记录全部请求。"""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict]] = []
        self.script: dict[str, object] = {}

    def probe(self, method: str, url: str, headers: dict):
        path = re.sub(r"^:\d+", "", url.split("127.0.0.1", 1)[1])
        self.requests.append((method, path, dict(headers)))
        response = self.script.get(f"{method} {path}", FakeResp(404, {}, b"{}"))
        if callable(response):
            response = response(method, url, headers)
        return response


class FakeResp:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status_code = status
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.body = body


def ok_probe(status: int = 200, headers: dict | None = None, body: bytes = b"{}") -> FakeResp:
    return FakeResp(status, headers or {}, body)


VERSION_TEXT = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def success_script(cmd: FakeCmd, head_sha: str = BASE_SHA) -> None:
    """脚本化一条全绿 docker/git 命令流（个别测试按需覆盖单项）。"""
    containers = "aios-m14-03-production-rehearsal-api-1\tUp 32 hours\nprod-web\tUp 32 hours"
    volumes = "ai-learning-os_postgres-data\nbuzz-prod-data"
    networks = "bridge\nhost\naios-m14-03-production-rehearsal_default"
    built: set[str] = set()

    def image_inspect(argv: list[str]) -> SimpleResult:
        ref = argv[-1]
        exists = ref in built or f"{TASK_LABEL}-" not in ref
        if not exists:
            return SimpleResult(1, "", "no such image")
        return SimpleResult(0, json.dumps({
            "Id": f"sha256:{hashlib.sha256(ref.encode()).hexdigest()[:12]}",
            "Created": "2026-09-22T12:00:00Z", "Size": 123,
            "RepoTags": [ref], "RepoDigests": []}))

    cmd.add(("git",), lambda argv: (
        SimpleResult(0, head_sha) if argv[-1] == "HEAD"
        else SimpleResult(0, "") if argv[-1] == "--porcelain"
        else SimpleResult(1, "", "unscripted git")))
    cmd.add(("docker", "ps", "-a", "--filter"), SimpleResult(0, ""))
    cmd.add(("docker", "volume", "ls", "--filter"), SimpleResult(0, ""))
    cmd.add(("docker", "image", "inspect"), image_inspect)
    cmd.add(("docker", "compose", "version"), SimpleResult(0, "Docker Compose version v5.5.0"))
    cmd.add(("docker", "ps", "-a", "--format"), SimpleResult(0, containers))
    cmd.add(("docker", "volume", "ls", "--format"), SimpleResult(0, volumes))
    cmd.add(("docker", "network", "ls", "--format"), SimpleResult(0, networks))
    cmd.add(("docker", "build"), lambda argv: (
        built.update(t for t in argv if t.startswith("aios/")),
        SimpleResult(0, "build ok"))[1])
    cmd.add(("docker", "compose"), lambda argv: _compose_flow(argv))
    cmd.add(("docker", "inspect", "--format"), lambda argv: SimpleResult(0, "healthy"))
    cmd.add(("docker", "port"), lambda argv: (
        SimpleResult(0, "8000/tcp -> 127.0.0.1:18096") if _CID_KIND.get(argv[-1]) == "api"
        else SimpleResult(0, "3000/tcp -> 127.0.0.1:13096")))
    cmd.add(("docker", "version"), SimpleResult(0, "29.7.2"))


def _compose_flow(argv: list[str]) -> SimpleResult:
    joined = " ".join(argv)
    if "ls" in argv:
        return SimpleResult(0, COMPOSE_LS_JSON)
    if argv[-2] == "-q":
        return SimpleResult(0, f"c{SMOKE_SERVICES.index(argv[-1]) + 1}")
    if " ps " in f" {joined} " or joined.endswith("ps --format json"):
        rows = [{"Name": f"{PROJECT_NAME}-{s}-1", "State": "running", "Health": "healthy",
                 "Service": s} for s in SMOKE_SERVICES]
        return SimpleResult(0, json.dumps(rows))
    if "config" in argv:
        return SimpleResult(0, json.dumps({
            "name": PROJECT_NAME,
            "services": {s: {"image": f"{s}:unit"} for s in SMOKE_SERVICES}}))
    if argv[-1] == "--no-build" or " up " in joined:
        return SimpleResult(0, "up ok")
    if "down" in argv:
        return SimpleResult(0, "down ok")
    return SimpleResult(1, "", f"unscripted compose: {joined}")


def success_probes(probe: FakeProbe) -> None:
    probe.script["GET /health"] = ok_probe(200, body=b'{"status":"ok"}')
    probe.script["GET /api/v1/version"] = ok_probe(200, body=json.dumps({"version": VERSION_TEXT}).encode())
    probe.script["GET /api/v1/auth/status"] = ok_probe(200, body=b'{"auth_enabled":true}')
    probe.script["GET /api/v1/papers"] = ok_probe(401)
    probe.script["POST /api/v1/resources/upload"] = ok_probe(401)
    probe.script["GET /"] = ok_probe(200, body=b"<html>login</html>")
    probe.script["GET /login"] = ok_probe(200, body=b"<html>login</html>")
    probe.script["OPTIONS /api/v1/papers"] = ok_probe(
        200, {"access-control-allow-origin": "http://127.0.0.1:13096"})


def run_tool(cmd: FakeCmd, tmp: Path, probe: FakeProbe | None = None, *,
             head_sha: str = BASE_SHA, base_sha: str = BASE_SHA,
             porcelain: str = "", binder=None, poll_interval: float = 0.0,
             health_timeout: float = 5.0):
    module = load_runner()
    if probe is None:
        probe = FakeProbe()
        success_probes(probe)
    output = tmp / "rc-out"
    report, code = module.run_rehearsal(
        cmd=cmd, http=probe, binder=binder or (lambda host, port: None),
        sleep=lambda _s: None, now=lambda: __import__("datetime").datetime(
            2026, 9, 22, 12, 0, 0, tzinfo=__import__("datetime").timezone.utc),
        base_sha=base_sha, output_dir=output,
        poll_interval=poll_interval, health_timeout=health_timeout)
    return module, report, code, output


def argv_contains(cmd: FakeCmd, fragment: list[str]) -> bool:
    for call in cmd.calls:
        argv = call["argv"]
        for i in range(len(argv) - len(fragment) + 1):
            if argv[i:i + len(fragment)] == fragment:
                return True
    return False


# ---------- 常量与命令形态 ----------


def test_runner_constants_match_compose_file() -> None:
    module = load_runner()
    text = read_norm(SMOKE_COMPOSE)
    assert module.PROJECT_NAME == PROJECT_NAME
    assert module.COMPOSE_FILE.name == "docker-compose.rc-smoke.yml"
    assert module.API_HOST_PORT == API_PORT and module.WEB_HOST_PORT == WEB_PORT
    assert module.SMOKE_SERVICES == SMOKE_SERVICES
    assert f"127.0.0.1:{API_PORT}:8000" in text
    assert f"127.0.0.1:{WEB_PORT}:3000" in text
    assert module.API_IMAGE_REF(BASE_SHA) == API_TAG
    assert module.WEB_IMAGE_REF(BASE_SHA) == WEB_TAG


def test_runner_source_never_references_production_compose() -> None:
    source = read_norm(RUNNER_PATH)
    assert 'infra/docker-compose.yml"' not in source
    assert "aios-m14-03-production-rehearsal" not in source
    assert "m14-70-production" not in source and "v0.1.0" not in source


def test_runner_forbidden_docker_verbs_guarded() -> None:
    module = load_runner()
    assert set(module.FORBIDDEN_DOCKER_VERBS) >= {
        "stop", "rm", "kill", "restart", "prune", "push", "login", "tag"}
    # 直接驱动守卫：无 -p 的 compose down 必须被拒
    with pytest.raises(module.GuardError):
        module.assert_safe_argv(["docker", "compose", "-f", "x.yml", "down"])
    with pytest.raises(module.GuardError):
        module.assert_safe_argv(["docker", "compose", "-p", "p", "down"])
    with pytest.raises(module.GuardError):
        module.assert_safe_argv(["docker", "stop", "anything"])
    with pytest.raises(module.GuardError):
        module.assert_safe_argv(["docker", "volume", "rm", "v"])
    with pytest.raises(module.GuardError):
        module.assert_safe_argv(["docker", "system", "prune"])
    # 合法形态：冒烟 compose + 项目名 + up/down/只读
    module.assert_safe_argv(["docker", "compose", "-f", str(module.COMPOSE_FILE),
                             "-p", PROJECT_NAME, "down", "--volumes", "--remove-orphans"])
    module.assert_safe_argv(["docker", "compose", "-f", str(module.COMPOSE_FILE),
                             "-p", PROJECT_NAME, "ps", "-q", "api"])
    module.assert_safe_argv(["docker", "image", "inspect", API_TAG])
    module.assert_safe_argv(["docker", "build", "-f", "services/api/Dockerfile", "-t", API_TAG, "."])


def test_runner_strips_aios_env_and_injects_tags() -> None:
    module = load_runner()
    env = module.child_env({"PATH": "/bin", "AIOS_IMAGE_TAG": "v0.1.0",
                            "AIOS_AUTH_SECRET": "x", "AIOS_RC_SMOKE_API_TAG": "stale"},
                           api_tag="t1", web_tag="t2")
    assert env["PATH"] == "/bin"
    assert "AIOS_IMAGE_TAG" not in env and "AIOS_AUTH_SECRET" not in env
    assert env["AIOS_RC_SMOKE_API_TAG"] == "t1" and env["AIOS_RC_SMOKE_WEB_TAG"] == "t2"


# ---------- 前置拒绝面 ----------


def test_runner_refuses_head_sha_mismatch(tmp_path: Path) -> None:
    cmd = FakeCmd()
    success_script(cmd, head_sha="0" * 40)
    module, report, code, _ = run_tool(cmd, tmp=tmp_path, base_sha=BASE_SHA)
    assert code == module.EXIT_REFUSED
    assert report["status"] == "refused"
    assert "head-sha-mismatch" in report["reasons"]
    # 拒绝面零 docker 改动调用（只允许 git 读取）
    assert not argv_contains(cmd, ["docker", "build"])
    assert not argv_contains(cmd, ["up"])


def test_runner_refuses_dirty_build_input(tmp_path: Path) -> None:
    module = load_runner()
    cmd = FakeCmd()
    success_script(cmd)
    cmd.add(("git",), lambda argv: (
        SimpleResult(0, BASE_SHA) if argv[-1] == "HEAD"
        else SimpleResult(0, " M apps/web/src/app/page.tsx\n?? tools/ops/rc_smoke_rehearsal.py")))
    _, report, code, _ = run_tool(cmd, tmp=tmp_path)
    assert code == module.EXIT_REFUSED
    assert "dirty-build-input" in report["reasons"]


def test_runner_allows_dirty_tooling_outside_build_inputs(tmp_path: Path) -> None:
    cmd = FakeCmd()
    success_script(cmd)
    cmd.add(("git",), lambda argv: (
        SimpleResult(0, BASE_SHA) if argv[-1] == "HEAD"
        else SimpleResult(0, "?? tools/ops/rc_smoke_rehearsal.py\n"
                            "?? infra/docker-compose.rc-smoke.yml\n"
                            "?? services/api/tests/test_rc_smoke_rehearsal.py\n"
                            " M docs/CHANGELOG.md")))
    _, report, code, _ = run_tool(cmd, tmp=tmp_path)
    assert report["status"] == "pass", report["reasons"]
    assert code == 0
    assert report["task"]["build_inputs_clean"] is True
    assert report["task"]["dirty_paths"] == [
        "tools/ops/rc_smoke_rehearsal.py", "infra/docker-compose.rc-smoke.yml",
        "services/api/tests/test_rc_smoke_rehearsal.py", "docs/CHANGELOG.md"]


def test_runner_build_input_path_classifier() -> None:
    module = load_runner()
    for path in ("VERSION", "package.json", "package-lock.json", ".dockerignore",
                 "services/api/Dockerfile", "apps/web/Dockerfile",
                 "services/api/requirements.txt", "services/api/alembic.ini",
                 "services/api/app/main.py", "services/api/alembic/versions/x.py",
                 "apps/web/src/x.tsx"):
        assert module.is_build_input_path(path), path
    for path in ("tools/ops/x.py", "infra/docker-compose.yml", "docs/x.md",
                 "services/api/tests/x.py", ".verify/artifacts/x", "README.md"):
        assert not module.is_build_input_path(path), path


def test_runner_refuses_port_in_use(tmp_path: Path) -> None:
    def busy(host: str, port: int) -> None:
        raise OSError(f"busy {port}")

    module = load_runner()
    cmd = FakeCmd()
    success_script(cmd)
    _, report, code, _ = run_tool(cmd, binder=busy, tmp=tmp_path)
    assert code == module.EXIT_REFUSED
    assert any(r.startswith("port-in-use") for r in report["reasons"])
    assert not argv_contains(cmd, ["docker", "build"])


def test_runner_refuses_stale_project_containers(tmp_path: Path) -> None:
    module = load_runner()
    cmd = FakeCmd()
    success_script(cmd)
    cmd.add(("docker", "ps", "-a", "--filter"), SimpleResult(0, f"{PROJECT_NAME}-api-1"))
    _, report, code, _ = run_tool(cmd, tmp=tmp_path)
    assert code == module.EXIT_REFUSED
    assert "stale-project-containers" in report["reasons"]


def test_runner_refuses_stale_project_volumes(tmp_path: Path) -> None:
    module = load_runner()
    cmd = FakeCmd()
    success_script(cmd)
    cmd.add(("docker", "volume", "ls", "--filter"), SimpleResult(0, f"{PROJECT_NAME}_postgres-data"))
    _, report, code, _ = run_tool(cmd, tmp=tmp_path)
    assert code == module.EXIT_REFUSED
    assert "stale-project-volumes" in report["reasons"]


def test_runner_refuses_existing_image_tag_never_overwrites(tmp_path: Path) -> None:
    module = load_runner()
    cmd = FakeCmd()
    success_script(cmd)
    cmd.add(("docker", "image", "inspect"), lambda argv: SimpleResult(
        0, json.dumps({"Id": "sha256:old", "Created": "t", "Size": 1,
                       "RepoTags": [argv[-1]], "RepoDigests": []})))
    _, report, code, _ = run_tool(cmd, tmp=tmp_path)
    assert code == module.EXIT_REFUSED
    assert any(r.startswith("image-tag-exists") for r in report["reasons"])
    assert not argv_contains(cmd, ["docker", "build"]), "已存在 tag 绝不重建/覆盖"


def test_runner_refuses_missing_infra_image(tmp_path: Path) -> None:
    module = load_runner()
    cmd = FakeCmd()
    success_script(cmd)
    cmd.add(("docker", "image", "inspect"), lambda argv: SimpleResult(
        0, json.dumps({"Id": "sha256:x", "Created": "t", "Size": 1,
                       "RepoTags": [argv[-1]], "RepoDigests": []}))
        if argv[-1] in ("postgres:17-alpine", "redis:7-alpine")
        else SimpleResult(1, "", "no such image"))
    _, report, code, _ = run_tool(cmd, tmp=tmp_path)
    assert code == module.EXIT_REFUSED
    assert any(r.startswith("infra-image-missing") for r in report["reasons"])
    # 绝不隐式 pull（pull_policy never + 预检拒绝）
    assert not argv_contains(cmd, ["pull"])


def test_runner_refuses_invalid_compose_render(tmp_path: Path) -> None:
    module = load_runner()
    cmd = FakeCmd()
    success_script(cmd)
    cmd.add(("docker", "compose"), lambda argv: (
        SimpleResult(1, "", "invalid interpolation") if "config" in argv
        else _compose_flow(argv)))
    _, report, code, _ = run_tool(cmd, tmp=tmp_path)
    assert code == module.EXIT_REFUSED
    assert "compose-config-invalid" in report["reasons"]
    assert not argv_contains(cmd, ["docker", "build"])


def test_runner_refuses_existing_output_dir(tmp_path: Path) -> None:
    module = load_runner()
    output = tmp_path / "exists"
    output.mkdir()
    (output / "stale.txt").write_text("x", encoding="utf-8")
    cmd = FakeCmd()
    success_script(cmd)
    report, code = module.run_rehearsal(
        cmd=cmd, http=FakeProbe(), binder=lambda h, p: None, sleep=lambda s: None,
        now=lambda: __import__("datetime").datetime(2026, 9, 22, tzinfo=__import__("datetime").timezone.utc),
        base_sha=BASE_SHA, output_dir=output)
    assert code == module.EXIT_REFUSED
    assert report["status"] == "refused"
    assert "output-dir-exists" in report["reasons"]
    assert cmd.calls == [], "输出目录拒绝必须先于一切子进程调用"


# ---------- 构建与起栈命令形态 ----------


def test_runner_builds_unique_tags_from_head_sha(tmp_path: Path) -> None:
    cmd = FakeCmd()
    success_script(cmd)
    _, report, code, _ = run_tool(cmd, tmp=tmp_path)
    assert code == 0
    assert argv_contains(cmd, ["docker", "build", "-f",
                               str(REPO_ROOT / "services" / "api" / "Dockerfile"),
                               "-t", API_TAG])
    assert argv_contains(cmd, ["--build-arg",
                               f"NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:{API_PORT}"])
    assert argv_contains(cmd, ["-t", WEB_TAG])
    assert report["images"]["api"]["tag"] == API_TAG
    assert report["images"]["web"]["tag"] == WEB_TAG
    assert report["images"]["api"]["image_id"].startswith("sha256:")
    assert report["images"]["infra"]["minio"]["image"] == INFRA_IMAGES["minio"]


def test_runner_compose_calls_always_scoped(tmp_path: Path) -> None:
    cmd = FakeCmd()
    success_script(cmd)
    _, _, code, _ = run_tool(cmd, tmp=tmp_path)
    assert code == 0
    for call in cmd.calls:
        argv = call["argv"]
        if argv[:2] == ["docker", "compose"] and "version" not in argv and " ls" not in f" {argv[2]} ":
            assert str(module_compose_file()) in argv, "compose 调用必须 -f 冒烟文件"
            assert PROJECT_NAME in argv, "compose 调用必须 -p 任务项目名"
    assert argv_contains(cmd, ["-p", PROJECT_NAME, "up", "-d", "--no-build"])
    assert argv_contains(cmd, ["-p", PROJECT_NAME,
                               "down", "--volumes", "--remove-orphans"])


def module_compose_file() -> Path:
    return load_runner().COMPOSE_FILE


def test_runner_compose_env_carries_tags_only(tmp_path: Path) -> None:
    cmd = FakeCmd()
    success_script(cmd)
    _, _, code, _ = run_tool(cmd, tmp=tmp_path)
    assert code == 0
    up_calls = [c for c in cmd.calls if "up" in c["argv"]]
    assert up_calls
    # 真实执行（M14-96 attempt-1）暴露的缺陷回归：compose 文件 image 已带
    # aios/api: 前缀，env 必须是纯 tag 后缀——双重前缀会让 up 解析失败
    tag_suffix = f"{TASK_LABEL}-{BASE_SHA}"
    for call in up_calls:
        env = call["env"]
        assert env["AIOS_RC_SMOKE_API_TAG"] == tag_suffix
        assert env["AIOS_RC_SMOKE_WEB_TAG"] == tag_suffix
        assert not any(k.startswith("AIOS_") and not k.startswith("AIOS_RC_SMOKE_")
                       for k in env), "宿主 AIOS_* 漂移变量必须全部剥离"


# ---------- 健康轮询 / 端口证明 / 探针 ----------


def test_runner_fails_when_service_never_healthy(tmp_path: Path) -> None:
    module = load_runner()
    cmd = FakeCmd()
    success_script(cmd)
    cmd.add(("docker", "inspect", "--format"), lambda argv: SimpleResult(0, "unhealthy"))
    _, report, code, _ = run_tool(cmd, tmp=tmp_path, health_timeout=0.0)
    assert code == module.EXIT_FAILED
    assert report["status"] == "failed"
    assert any(r.startswith("service-unhealthy") for r in report["reasons"])
    # 失败也必须拆栈
    assert argv_contains(cmd, ["down", "--volumes", "--remove-orphans"])


def test_runner_fails_on_wrong_port_binding(tmp_path: Path) -> None:
    module = load_runner()
    cmd = FakeCmd()
    success_script(cmd)
    cmd.add(("docker", "port"), lambda argv: SimpleResult(0, "8000/tcp -> 0.0.0.0:18096"))
    _, report, code, _ = run_tool(cmd, tmp=tmp_path)
    assert code == module.EXIT_FAILED
    assert any(r.startswith("port-binding") for r in report["reasons"])


def test_runner_probe_specs_exact_contract() -> None:
    module = load_runner()
    specs = module.build_probe_specs()
    names = [s.name for s in specs]
    assert names == [
        "api-health", "api-version", "auth-status",
        "anonymous-protected-read", "anonymous-protected-write",
        "web-root", "web-login", "cors-preflight"]
    by_name = {s.name: s for s in specs}
    assert by_name["api-health"].url == f"http://127.0.0.1:{API_PORT}/health"
    assert by_name["api-health"].method == "GET"
    assert by_name["api-version"].url.endswith("/api/v1/version")
    write_probe = by_name["anonymous-protected-write"]
    assert write_probe.method == "POST"
    assert write_probe.url.endswith("/api/v1/resources/upload")
    assert "Authorization" not in write_probe.headers, "匿名写探针绝不携带凭据"
    assert write_probe.expected_status == 401
    read_probe = by_name["anonymous-protected-read"]
    assert read_probe.method == "GET" and read_probe.expected_status == 401
    assert read_probe.url.endswith("/api/v1/papers")
    cors = by_name["cors-preflight"]
    assert cors.method == "OPTIONS"
    assert cors.headers["Origin"] == f"http://127.0.0.1:{WEB_PORT}"
    assert by_name["web-root"].url == f"http://127.0.0.1:{WEB_PORT}/"
    assert by_name["web-login"].url.endswith("/login")
    for spec in specs:
        assert spec.url.startswith("http://127.0.0.1:"), "探针只打 loopback"


def test_runner_version_probe_matches_version_file() -> None:
    module = load_runner()
    specs = {s.name: s for s in module.build_probe_specs()}
    version_spec = specs["api-version"]
    ok = module.evaluate_probe(version_spec, FakeResp(200, {}, json.dumps(
        {"version": VERSION_TEXT}).encode()))
    assert ok[0] is True
    bad = module.evaluate_probe(version_spec, FakeResp(200, {}, b'{"version":"9.9.9"}'))
    assert bad[0] is False and "9.9.9" in bad[1]


def test_runner_auth_status_probe_requires_enabled() -> None:
    module = load_runner()
    specs = {s.name: s for s in module.build_probe_specs()}
    ok = module.evaluate_probe(specs["auth-status"], FakeResp(200, {}, b'{"auth_enabled":true}'))
    assert ok[0] is True
    bad = module.evaluate_probe(specs["auth-status"], FakeResp(200, {}, b'{"auth_enabled":false}'))
    assert bad[0] is False


def test_runner_cors_probe_requires_origin_echo() -> None:
    module = load_runner()
    specs = {s.name: s for s in module.build_probe_specs()}
    ok = module.evaluate_probe(specs["cors-preflight"], FakeResp(
        200, {"Access-Control-Allow-Origin": f"http://127.0.0.1:{WEB_PORT}"}, b""))
    assert ok[0] is True
    bad = module.evaluate_probe(specs["cors-preflight"], FakeResp(200, {}, b""))
    assert bad[0] is False


def test_runner_probe_failure_fails_but_probes_all_and_tears_down(tmp_path: Path) -> None:
    module = load_runner()
    cmd = FakeCmd()
    success_script(cmd)
    probe = FakeProbe()
    success_probes(probe)
    probe.script["POST /api/v1/resources/upload"] = ok_probe(200)  # 保护写居然放行 = 契约破坏
    _, report, code, _ = run_tool(cmd, probe=probe, tmp=tmp_path)
    assert code == module.EXIT_FAILED
    assert report["status"] == "failed"
    assert any("anonymous-protected-write" in r for r in report["reasons"])
    # 八项探针全部执行（完整证据，fail-fast 只影响结论不影响采集）
    assert len(probe.requests) == 8
    assert argv_contains(cmd, ["down", "--volumes", "--remove-orphans"])


# ---------- 拆栈与零外部漂移证明 ----------


def test_runner_teardown_verifies_own_project_empty(tmp_path: Path) -> None:
    cmd = FakeCmd()
    success_script(cmd)
    cmd.add(("docker", "compose"), lambda argv: (
        SimpleResult(0, "down ok") if "down" in argv else _compose_flow(argv)))
    _, report, code, _ = run_tool(cmd, tmp=tmp_path)
    assert code == 0
    assert report["cleanup"]["down_ok"] is True
    assert report["cleanup"]["containers_remaining"] == []
    assert report["cleanup"]["volumes_remaining"] == []


def test_runner_fails_when_own_containers_survive_down(tmp_path: Path) -> None:
    module = load_runner()
    cmd = FakeCmd()
    success_script(cmd)
    state = {"down": False}

    def ps_filter(argv):
        return SimpleResult(0, f"{PROJECT_NAME}-api-1") if state["down"] else SimpleResult(0, "")

    cmd.add(("docker", "ps", "-a", "--filter"), ps_filter)
    cmd.add(("docker", "compose"), lambda argv: (
        (state.__setitem__("down", True), SimpleResult(0, "down ok"))[1]
        if "down" in argv else _compose_flow(argv)))
    _, report, code, _ = run_tool(cmd, tmp=tmp_path)
    assert code == module.EXIT_FAILED
    assert "cleanup-containers-remaining" in report["reasons"]


def test_runner_fails_on_external_container_drift(tmp_path: Path) -> None:
    module = load_runner()
    cmd = FakeCmd()
    success_script(cmd)
    state = {"phase": "before"}

    def ps_all(argv):
        text = "prod-api\tUp 32 hours"
        if state["phase"] == "after":
            text = "prod-api\tRestarting (1) 2 seconds ago"  # 外部容器被重启 = 漂移
        return SimpleResult(0, text)

    cmd.add(("docker", "ps", "-a", "--format"), ps_all)
    cmd.add(("docker", "compose"), lambda argv: (
        (state.__setitem__("phase", "after"), SimpleResult(0, "down ok"))[1]
        if "down" in argv else _compose_flow(argv)))
    _, report, code, _ = run_tool(cmd, tmp=tmp_path)
    assert code == module.EXIT_FAILED
    assert "external-container-drift" in report["reasons"]


def test_runner_passes_with_machine_snapshots_unchanged(tmp_path: Path) -> None:
    cmd = FakeCmd()
    success_script(cmd)
    _, report, code, _ = run_tool(cmd, tmp=tmp_path)
    assert code == 0 and report["status"] == "pass"
    iso = report["isolation"]
    assert iso["external_unchanged"] is True
    assert iso["container_drift"] == []
    assert "aios-m14-03-production-rehearsal-api-1" in "\n".join(iso["containers_before"])
    assert report["cleanup"]["down_ok"] is True
    assert report["services_health"] == {s: "healthy" for s in SMOKE_SERVICES}
    assert report["ports"]["api_observed"] == {"8000/tcp": [f"127.0.0.1:{API_PORT}"]}
    assert report["ports"]["web_observed"] == {"3000/tcp": [f"127.0.0.1:{WEB_PORT}"]}


# ---------- 证据与清单 ----------


def test_runner_writes_evidence_and_manifest(tmp_path: Path) -> None:
    cmd = FakeCmd()
    success_script(cmd)
    _, _, code, output = run_tool(cmd, tmp=tmp_path)
    assert code == 0
    expected = [
        "git-head.txt", "git-status-porcelain.txt",
        "docker-context-before.txt", "docker-context-after.txt",
        "docker-build-api.txt", "docker-build-web.txt",
        "image-inspect-api.json", "image-inspect-web.json",
        "compose-config.json", "compose-up.txt", "compose-ps.json",
        "service-health.txt", "docker-port.txt",
        "probes.json", "compose-down.txt",
        "rc-smoke-report.json", "rc-smoke-report.md", "SHA256SUMS.txt",
    ]
    names = {p.name for p in output.iterdir()}
    for name in expected:
        assert name in names, name
    manifest = (output / "SHA256SUMS.txt").read_text(encoding="utf-8")
    for line in manifest.strip().split("\n"):
        digest, name = line.split("  ", 1)
        data = (output / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, name
    assert "SHA256SUMS.txt" not in manifest
    listed = {line.split("  ", 1)[1] for line in manifest.strip().split("\n")}
    assert listed == names - {"SHA256SUMS.txt"}


def test_runner_report_contains_no_absolute_paths_or_env(tmp_path: Path) -> None:
    cmd = FakeCmd()
    success_script(cmd)
    _, report, code, output = run_tool(cmd, tmp=tmp_path)
    assert code == 0
    text = json.dumps(report, ensure_ascii=False) + (
        output / "rc-smoke-report.md").read_text(encoding="utf-8")
    assert not re.search(r"[A-Za-z]:\\\\", text), "报告绝无盘符绝对路径"
    assert "AIOS_AUTH_SECRET" not in text
    assert str(REPO_ROOT) not in text


def test_runner_report_boundaries_and_honesty_fields(tmp_path: Path) -> None:
    cmd = FakeCmd()
    success_script(cmd)
    _, report, code, _ = run_tool(cmd, tmp=tmp_path)
    assert code == 0
    assert report["schema_version"] == 1
    assert report["tool"].endswith("rc_smoke_rehearsal.py")
    assert report["task"]["base_sha"] == BASE_SHA
    assert report["task"]["head_sha"] == BASE_SHA
    assert any("release_ready" in b for b in report["boundaries"])
    assert any("human-only" in b for b in report["boundaries"])
    assert report["timeline"], "时间线必须逐阶段记录时间戳"


def test_runner_usage_errors_exit_code(tmp_path: Path, capsys) -> None:
    module = load_runner()
    with pytest.raises(SystemExit) as exc:
        module.main(["--base-sha", "not-a-sha"])
    assert exc.value.code == module.EXIT_USAGE
    with pytest.raises(SystemExit) as exc2:
        module.main([])
    assert exc2.value.code == module.EXIT_USAGE


def test_runner_help_has_no_absolute_paths() -> None:
    module = load_runner()
    parser = module.build_parser()
    help_text = parser.format_help()
    assert str(REPO_ROOT) not in help_text
    assert not re.search(r"[A-Za-z]:\\\\", help_text)


def test_runner_stdout_never_prints_absolute_paths(tmp_path: Path, capsys) -> None:
    module = load_runner()
    cmd = FakeCmd()
    success_script(cmd)
    output = tmp_path / "out"
    code = module.run_cli(cmd=cmd, http=FakeProbe(), binder=lambda h, p: None,
                          sleep=lambda s: None, base_sha=BASE_SHA, output_dir=output)
    captured = capsys.readouterr()
    assert not re.search(r"[A-Za-z]:\\\\", captured.out + captured.err)
    assert str(REPO_ROOT) not in captured.out
    assert code in (module.EXIT_REFUSED, module.EXIT_FAILED)
