"""M10-05 外部 coturn 部署模板：独立 compose、fail-closed 配置、端口不冲突、secret 不入库。

覆盖矩阵：
1. 静态断言（无 docker 依赖）：主栈 compose 不含 coturn（yaml 服务集 + 无 include +
   原文无 coturn 字样）；coturn compose 为独立 project、secret/external-ip 用 :? 插值
   （无字面默认值）；模板文件无真实 secret 材料（32+ hex / 仓库开发占位）；
   entrypoint.sh 为纯 LF（容器内 /bin/sh 无法执行 CRLF）；
2. 端口不冲突：coturn 默认端口（3478/5349/50000-50099）与主栈宿主端口
   （5433/6379/9000/9001/8000/3000/7880/7881 + LiveKit UDP 7882-7892）零交集，
   且 entrypoint 内置 7882-7892 同机硬冲突检查；
3. entrypoint 行为矩阵（bash 子进程直跑，COTURN_CONFIG_ONLY 打印脱敏配置）：
   缺必填/占位 secret/弱 secret/占位与 loopback external-ip/relay 与监听端口
   落入 7882-7892/relay 段倒置/超宽/非数字/TLS 启用缺证书 → 一律 FAIL-CLOSED；
   合法输入 → 配置生成且 stdout 不含真实 secret；豁免开关与 TLS 真实证书路径通过；
4. docker compose 渲染：必填变量齐全渲染成功（服务/端口/卷）；任一必填变量缺失
   时 config 直接失败（:? 语法，无默认值兜底）；主栈 --profile local 渲染不含 coturn；
5. 门控冒烟（AIOS_COTURN_SMOKE=1）：隔离环境（loopback 豁免 + 随机测试 secret +
   仅绑 127.0.0.1 + 缩小 relay 段）真启动容器，STUN Binding 探测 UDP 3478 有响应
   后 down——只验证本机监听，不构成 TURN 可用性验收（见 docs/COTURN_DEPLOYMENT.md）。
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import struct
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
MAIN_COMPOSE = REPO / "infra" / "docker-compose.yml"
COTURN_DIR = REPO / "infra" / "coturn"
COTURN_COMPOSE = COTURN_DIR / "docker-compose.coturn.yml"
ENTRYPOINT = COTURN_DIR / "entrypoint.sh"
ENV_EXAMPLE = COTURN_DIR / ".env.example"
CONF_EXAMPLE = COTURN_DIR / "turnserver.conf.example"

# 主栈宿主端口事实（M9-05/M9-07/M9-08）：数据面固定 loopback，边缘面跟随 AIOS_BIND_IP。
# LiveKit 媒体面 UDP 7882-7892 是 coturn relay 段必须避开的硬边界。
LIVEKIT_UDP_RANGE = (7882, 7892)
MAIN_HOST_PORTS = {5433, 6379, 9000, 9001, 8000, 3000, 7880, 7881, *range(7882, 7893)}
COTURN_DEFAULT_PORTS = {3478, 5349, *range(50000, 50100)}

# entrypoint 校验矩阵共用的合法基线（TEST-NET-2 文档 IP，非任何真实环境）；
# secret 每次运行随机生成——仓库与测试代码不含任何固定 secret 材料
VALID_EXTERNAL_IP = "198.51.100.10"


# ---------------------------------------------------------------- 静态断言


def test_main_compose_has_no_coturn_service() -> None:
    """主栈 compose：无 coturn 服务、无 include 引入、原文无 coturn 字样。"""
    import yaml

    raw = MAIN_COMPOSE.read_text(encoding="utf-8")
    assert "coturn" not in raw.lower()
    model = yaml.safe_load(raw)
    assert set(model["services"]) == {"postgres", "redis", "minio", "api", "livekit", "web"}
    assert "include" not in model, "主栈不得经 include 引入 coturn 文件"


def test_coturn_compose_is_separate_fail_closed_project() -> None:
    """独立 project 名 + 单一 coturn 服务；必填变量用 :?（缺失即失败），无字面默认。"""
    import yaml

    raw = COTURN_COMPOSE.read_text(encoding="utf-8")
    model = yaml.safe_load(raw)
    assert model["name"] == "ai-learning-os-coturn", "独立 project，与主栈 ai-learning-os 互不影响"
    assert set(model["services"]) == {"coturn"}
    # :? —— required 变量；:- 形式（带默认值）不允许出现在两个必填变量上
    assert re.search(r"COTURN_STATIC_AUTH_SECRET: \$\{COTURN_STATIC_AUTH_SECRET:\?", raw)
    assert re.search(r"COTURN_EXTERNAL_IP: \$\{COTURN_EXTERNAL_IP:\?", raw)
    assert not re.search(r"\$\{COTURN_STATIC_AUTH_SECRET:-", raw)
    assert not re.search(r"\$\{COTURN_EXTERNAL_IP:-", raw)


def test_template_files_contain_no_secret_material() -> None:
    """infra/coturn 全部模板文件：无 32+ hex 随机串、无仓库开发占位 secret；entrypoint 纯 LF。"""
    files = sorted(p for p in COTURN_DIR.iterdir() if p.is_file())
    assert {p.name for p in files} >= {
        "docker-compose.coturn.yml",
        "entrypoint.sh",
        ".env.example",
        "turnserver.conf.example",
    }
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"[0-9a-fA-F]{32,}", text), f"{path.name} 疑似含真实 secret 材料"
        # 精确到完整占位值：entrypoint 的拒绝分支合法引用 aios-local-dev 前缀模式本身
        assert "aios-local-dev-secret" not in text, f"{path.name} 含仓库开发占位 secret"
    assert b"\r" not in ENTRYPOINT.read_bytes(), "entrypoint.sh 必须纯 LF（容器 /bin/sh 不认 CRLF）"
    # .env.example 的必填变量保持占位形态（<...>），不得出现看似可用的默认值
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert re.search(r"^COTURN_STATIC_AUTH_SECRET=<", env_example, re.MULTILINE)
    assert re.search(r"^COTURN_EXTERNAL_IP=<", env_example, re.MULTILINE)


def test_example_conf_documents_placeholders_and_optional_tls() -> None:
    """turnserver.conf.example：external-ip/secret 为占位、TLS 行保持注释（不做假 TLS）。"""
    text = CONF_EXAMPLE.read_text(encoding="utf-8")
    assert "external-ip=<" in text
    assert "static-auth-secret=<" in text
    assert re.search(r"^#tls-listening-port=", text, re.MULTILINE)
    assert "min-port=50000" in text and "max-port=50099" in text


def test_default_ports_do_not_conflict_with_main_stack() -> None:
    """coturn 默认端口（listening/TLS/relay）与主栈全部宿主端口零交集。"""
    assert not (COTURN_DEFAULT_PORTS & MAIN_HOST_PORTS), (
        f"端口冲突: {sorted(COTURN_DEFAULT_PORTS & MAIN_HOST_PORTS)}"
    )
    # 主栈 compose 里 livekit 的 UDP 媒体面端口段与常量保持同步（防止文档/常量漂移）
    import yaml

    livekit_ports = yaml.safe_load(MAIN_COMPOSE.read_text(encoding="utf-8"))["services"]["livekit"]["ports"]
    udp_ranges = [p for p in livekit_ports if isinstance(p, str) and p.endswith("/udp")]
    assert any("7882-7892:7882-7892/udp" in p for p in udp_ranges)
    # entrypoint 内置同机硬冲突检查（7882-7892 与 relay 段/listening 口都不允许重叠）
    entry = ENTRYPOINT.read_text(encoding="utf-8")
    assert "7882" in entry and "7892" in entry


# ---------------------------------------------------------------- entrypoint 行为矩阵


@lru_cache(maxsize=1)
def _find_bash() -> str | None:
    """解析可执行 POSIX sh 的 bash。

    Windows 下 CreateProcess 搜索顺序中 System32 先于 PATH，会把 `bash` 解析成
    WSL 启动器（不认 D:/... 路径参数、非真实部署环境）——显式跳过 System32/
    SysNative/WindowsApps，取 PATH 中真正的 Git Bash。
    """
    if os.name != "nt":
        return shutil.which("bash")
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        lowered = directory.lower().replace("\\", "/")
        if "system32" in lowered or "sysnative" in lowered or "windowsapps" in lowered:
            continue
        candidate = Path(directory) / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def _bash_available() -> bool:
    return _find_bash() is not None


def _entrypoint_env(**overrides: str) -> dict[str, str]:
    """干净环境：剥离宿主 COTURN_*，再叠加用例显式值（含随机 secret）。"""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("COTURN_")}
    secret = overrides.pop("secret", None) or secrets.token_hex(32)
    if secret != "__unset__":
        env["COTURN_STATIC_AUTH_SECRET"] = secret
    if "external_ip" in overrides:
        external = overrides.pop("external_ip")
        if external != "__unset__":
            env["COTURN_EXTERNAL_IP"] = external
    else:
        env["COTURN_EXTERNAL_IP"] = VALID_EXTERNAL_IP
    env.update(overrides)
    return env


def _run_entrypoint(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    bash = _find_bash()
    assert bash is not None, "未找到非 WSL bash"
    env = dict(env)
    try:
        with tempfile.NamedTemporaryFile(prefix="aios-turnserver-", suffix=".conf", delete=False) as conf:
            conf_name = conf.name
        # MSYS bash 对正斜杠 Windows 路径的重定向/判断最可靠
        env.setdefault("COTURN_CONF_PATH", conf_name.replace("\\", "/"))
        result = subprocess.run(
            [bash, ENTRYPOINT.as_posix()],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            env=env,
            check=False,
        )
    finally:
        os.unlink(conf_name)
    return result


def _ok_config_only(**env: str) -> subprocess.CompletedProcess[str]:
    result = _run_entrypoint(_entrypoint_env(COTURN_CONFIG_ONLY="true", **env))
    assert result.returncode == 0, result.stderr
    return result


def _rejected(**env: str) -> subprocess.CompletedProcess[str]:
    result = _run_entrypoint(_entrypoint_env(COTURN_CONFIG_ONLY="true", **env))
    assert result.returncode != 0, f"预期 FAIL-CLOSED，实际 exit 0；stdout:\n{result.stdout}"
    assert "FAIL-CLOSED" in result.stderr, result.stderr
    return result


@pytest.mark.skipif(not _bash_available(), reason="需要 bash（POSIX sh 子进程）")
def test_entrypoint_requires_secret_and_external_ip() -> None:
    """缺任一必填变量：拒绝启动且提示变量名（compose :? 之外的第二道防线）。"""
    missing_secret = _rejected(secret="__unset__")
    assert "COTURN_STATIC_AUTH_SECRET" in missing_secret.stderr
    missing_ip = _rejected(external_ip="__unset__")
    assert "COTURN_EXTERNAL_IP" in missing_ip.stderr


@pytest.mark.skipif(not _bash_available(), reason="需要 bash（POSIX sh 子进程）")
@pytest.mark.parametrize(
    "weak",
    [
        "changeme",
        "<openssl rand -hex 32 的输出>",
        "aios-local-dev-secret-0f4c9a1e7b2d",
        "aios-local-dev-secret-7d21b9e4c8a3",
        "short-but-16byte-ok!!",
    ],
)
def test_entrypoint_rejects_placeholder_or_weak_secret(weak: str) -> None:
    """占位/仓库开发占位/长度不足 32 的 secret 一律拒绝。"""
    result = _rejected(secret=weak)
    assert "STATIC_AUTH_SECRET" in result.stderr


@pytest.mark.skipif(not _bash_available(), reason="需要 bash（POSIX sh 子进程）")
def test_entrypoint_rejects_placeholder_or_loopback_external_ip() -> None:
    """占位 external-ip 拒绝；127.0.0.1 需显式豁免开关（生产禁用）。"""
    placeholder = _rejected(external_ip="<宿主公网/局域网 IP>")
    assert "COTURN_EXTERNAL_IP" in placeholder.stderr
    loopback = _rejected(external_ip="127.0.0.1")
    assert "loopback" in loopback.stderr
    # 显式豁免（本机监听冒烟专用）可通过，且配置如实写入 127.0.0.1
    allowed = _ok_config_only(external_ip="127.0.0.1", COTURN_ALLOW_LOOPBACK_EXTERNAL="true")
    assert "external-ip=127.0.0.1" in allowed.stdout


@pytest.mark.skipif(not _bash_available(), reason="需要 bash（POSIX sh 子进程）")
def test_entrypoint_rejects_livekit_port_overlap() -> None:
    """relay 段/listening 口与主栈 LiveKit UDP 7882-7892 同机冲突：拒绝；跨机豁免可通过。"""
    relay_overlap = _rejected(COTURN_RELAY_PORT_START="7882", COTURN_RELAY_PORT_END="7890")
    assert "7882-7892" in relay_overlap.stderr or "LiveKit" in relay_overlap.stderr
    listen_overlap = _rejected(COTURN_LISTEN_PORT="7885")
    assert "7882-7892" in listen_overlap.stderr or "LiveKit" in listen_overlap.stderr
    _ok_config_only(
        COTURN_RELAY_PORT_START="7882",
        COTURN_RELAY_PORT_END="7890",
        COTURN_ALLOW_LIVEKIT_PORT_OVERLAP="true",  # 跨机部署显式豁免
    )


@pytest.mark.skipif(not _bash_available(), reason="需要 bash（POSIX sh 子进程）")
@pytest.mark.parametrize(
    ("overrides", "keyword"),
    [
        ({"COTURN_RELAY_PORT_START": "50100", "COTURN_RELAY_PORT_END": "50099"}, "RELAY_PORT"),
        ({"COTURN_RELAY_PORT_START": "50000", "COTURN_RELAY_PORT_END": "52000"}, "2000"),
        ({"COTURN_RELAY_PORT_START": "1023"}, "1024"),
        ({"COTURN_LISTEN_PORT": "34x8"}, "纯数字"),
    ],
)
def test_entrypoint_rejects_malformed_port_config(overrides: dict[str, str], keyword: str) -> None:
    """relay 段倒置/超宽（compose 映射开销）、特权端口、非数字端口：拒绝。"""
    result = _rejected(**overrides)
    assert keyword in result.stderr


@pytest.mark.skipif(not _bash_available(), reason="需要 bash（POSIX sh 子进程）")
def test_entrypoint_tls_fail_closed_without_real_cert(tmp_path: Path) -> None:
    """COTURN_TLS_ENABLED=true 但证书缺失：拒绝（不做假 TLS 声明）；真实文件存在才生成 TLS 配置。"""
    rejected = _rejected(COTURN_TLS_ENABLED="true")
    assert "证书不存在" in rejected.stderr or "CERT" in rejected.stderr.upper()

    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_bytes(b"dummy")  # CONFIG_ONLY 只校验存在性，不校验证书内容
    key.write_bytes(b"dummy")
    # MSYS bash 对 C:/... 形式路径的 [ -f ] 判定可靠（反斜杠形式可能失真）
    cert_path, key_path = cert.as_posix(), key.as_posix()
    ok = _ok_config_only(
        COTURN_TLS_ENABLED="true",
        COTURN_CERT_FILE=cert_path,
        COTURN_PKEY_FILE=key_path,
    )
    assert "tls-listening-port=5349" in ok.stdout
    assert f"cert={cert_path}" in ok.stdout
    assert f"pkey={key_path}" in ok.stdout
    # 默认（未启用 TLS）不得出现任何 TLS 配置行
    plain = _ok_config_only()
    assert "tls-listening-port" not in plain.stdout


@pytest.mark.skipif(not _bash_available(), reason="需要 bash（POSIX sh 子进程）")
def test_entrypoint_generates_config_with_redacted_secret() -> None:
    """合法输入：配置生成、关键行齐全；CONFIG_ONLY 输出脱敏 secret（真实值绝不回显）。"""
    secret = secrets.token_hex(32)
    result = _ok_config_only(secret=secret)
    text = result.stdout
    for expected in (
        "listening-port=3478",
        f"external-ip={VALID_EXTERNAL_IP}",
        "min-port=50000",
        "max-port=50099",
        "realm=ai-learning-os",
        "fingerprint",
        "lt-cred-mech",
        "static-auth-secret=<redacted len=64>",
        "no-multicast-peers",
    ):
        assert expected in text, f"配置缺 {expected}:\n{text}"
    # 当前 coturn 镜像已移除的选项不得再生成（实测会报 Bad configuration format）
    for removed in ("no-cli", "no-tlsv1", "no-tlsv1_1", "no-loopback-peers"):
        assert not re.search(rf"^{removed}$", text, re.MULTILINE), f"已移除选项 {removed} 仍在配置中"
    assert secret not in text, "CONFIG_ONLY 输出泄漏真实 secret"
    # CRLF 防御：.env 值尾部带 \r（Windows autocrlf 复制场景）被剥离，长度不虚增
    crlf = _ok_config_only(secret=secret + "\r")
    assert "static-auth-secret=<redacted len=64>" in crlf.stdout
    verbose = _ok_config_only(COTURN_VERBOSE="true")
    assert "verbose" in verbose.stdout


# ---------------------------------------------------------------- docker compose 渲染


def _compose_available() -> bool:
    return shutil.which("docker") is not None and COTURN_COMPOSE.is_file()


def _render(env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """渲染 coturn compose：空 --env-file 隔离开发者本机 infra/coturn/.env 的干扰。"""
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as handle:
        handle.write("")
        env_file = handle.name
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("COTURN_")}
    env.update(env_overrides or {})
    try:
        return subprocess.run(
            ["docker", "compose", "--env-file", env_file, "-f", str(COTURN_COMPOSE), "config", "--format", "json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            env=env,
            check=False,
        )
    finally:
        os.unlink(env_file)


def _published_ports(model: dict) -> set[tuple[str, int]]:
    """归一化 (protocol, 宿主 published 端口) 集合。

    本机 docker compose（v5.5）把端口范围展开为逐端口对象列表（target 为 int、
    published 为 str）；同时兼容短字符串形态，防 compose 版本差异。
    """
    out: set[tuple[str, int]] = set()
    for port in model["services"]["coturn"].get("ports", []):
        if isinstance(port, dict):
            out.add((str(port.get("protocol", "tcp")), int(port["published"])))
        else:
            head, _, proto = str(port).rpartition("/")
            host_part = head.rsplit(":", 2)[-2] if head.count(":") >= 2 else head.rsplit(":", 1)[0]
            out.add((proto, int(host_part)))
    return out


def _entrypoint_volume(model: dict) -> dict:
    """取 entrypoint.sh 的挂载对象（config 输出为长语法对象形态）。"""
    for volume in model["services"]["coturn"].get("volumes", []):
        if isinstance(volume, dict) and volume.get("target") == "/usr/local/bin/aios-coturn-entrypoint.sh":
            return volume
        if isinstance(volume, str) and "entrypoint.sh" in volume:
            return {"target": volume.rsplit(":", 1)[-1], "read_only": volume.endswith(":ro")}
    raise AssertionError(f"未见 entrypoint.sh 挂载: {model['services']['coturn'].get('volumes')}")


@pytest.mark.skipif(not _compose_available(), reason="需要 docker compose CLI")
def test_compose_render_succeeds_with_required_vars() -> None:
    """必填变量齐全：渲染出单服务独立 project，listening/TLS/relay 端口映射齐全。"""
    result = _render(
        {
            "COTURN_STATIC_AUTH_SECRET": secrets.token_hex(32),
            "COTURN_EXTERNAL_IP": "198.51.100.10",
        }
    )
    assert result.returncode == 0, result.stderr
    model = json.loads(result.stdout)
    assert model["name"] == "ai-learning-os-coturn"
    assert set(model["services"]) == {"coturn"}
    assert model["services"]["coturn"]["image"].startswith("coturn/coturn")
    # entrypoint 挂载只读 + entrypoint 数组形式（不被镜像默认 CMD 干扰）
    volume = _entrypoint_volume(model)
    assert volume.get("read_only") is True
    assert str(volume.get("source", "")).endswith("entrypoint.sh")
    assert model["services"]["coturn"]["entrypoint"] == ["/bin/sh", "/usr/local/bin/aios-coturn-entrypoint.sh"]
    published = _published_ports(model)
    # listening UDP+TCP、TLS TCP、relay 段（compose 把范围展开为逐端口，published==target）
    assert ("udp", 3478) in published and ("tcp", 3478) in published
    assert ("tcp", 5349) in published
    assert {("udp", p) for p in range(50000, 50100)} <= published
    assert {("tcp", p) for p in range(50000, 50100)} <= published
    # 宿主与容器端口一一对应（不做偏移映射，coturn 通告的 relay 端口才与实际一致）
    for port in model["services"]["coturn"]["ports"]:
        if isinstance(port, dict):
            assert int(port["published"]) == int(port["target"]), f"宿主/容器端口漂移: {port}"


@pytest.mark.skipif(not _compose_available(), reason="需要 docker compose CLI")
def test_compose_relay_range_follows_env_single_source() -> None:
    """relay 段跟随 env（compose 映射与 coturn min/max-port 同源插值，不会漂移）。"""
    result = _render(
        {
            "COTURN_STATIC_AUTH_SECRET": secrets.token_hex(32),
            "COTURN_EXTERNAL_IP": "198.51.100.10",
            "COTURN_RELAY_PORT_START": "50200",
            "COTURN_RELAY_PORT_END": "50204",
        }
    )
    assert result.returncode == 0, result.stderr
    model = json.loads(result.stdout)
    published = _published_ports(model)
    assert {("udp", p) for p in range(50200, 50205)} <= published
    assert {("tcp", p) for p in range(50200, 50205)} <= published
    assert not any(50000 <= port <= 50099 for _, port in published), "默认 relay 段不应再出现"
    # 容器内 min/max-port 由同一对变量喂给 entrypoint
    env_list = model["services"]["coturn"]["environment"]
    entries = env_list if isinstance(env_list, list) else [f"{k}={v}" for k, v in env_list.items()]
    assert "COTURN_RELAY_PORT_START=50200" in entries
    assert "COTURN_RELAY_PORT_END=50204" in entries


@pytest.mark.skipif(not _compose_available(), reason="需要 docker compose CLI")
@pytest.mark.parametrize(
    "overrides",
    [{}, {"COTURN_EXTERNAL_IP": "198.51.100.10"}, {"COTURN_STATIC_AUTH_SECRET": "a" * 64}],
)
def test_compose_render_fails_closed_without_required_vars(overrides: dict[str, str]) -> None:
    """任一必填变量缺失：config 直接失败并点名变量——无默认值兜底（:? 语法）。"""
    result = _render(overrides)
    assert result.returncode != 0, f"缺必填变量仍渲染成功:\n{result.stdout}"
    if "COTURN_STATIC_AUTH_SECRET" not in overrides:
        assert "COTURN_STATIC_AUTH_SECRET" in result.stderr
    if "COTURN_EXTERNAL_IP" not in overrides:
        assert "COTURN_EXTERNAL_IP" in result.stderr


@pytest.mark.skipif(not _compose_available(), reason="需要 docker compose CLI")
def test_main_stack_profile_render_excludes_coturn() -> None:
    """主栈 --profile local 渲染（真 docker compose config）：服务集与 M7-01 语义一致，无 coturn。"""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("COTURN_")}
    result = subprocess.run(
        ["docker", "compose", "-f", str(MAIN_COMPOSE), "--profile", "local", "config", "--format", "json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    services = set(json.loads(result.stdout)["services"])
    assert services == {"postgres", "redis", "minio", "api", "web", "livekit"}
    assert "coturn" not in services


# ---------------------------------------------------------------- 门控冒烟（隔离环境）


def _udp_port_free(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


@pytest.mark.skipif(not _bash_available(), reason="需要 bash")
@pytest.mark.skipif(os.environ.get("AIOS_COTURN_SMOKE") != "1", reason="需 AIOS_COTURN_SMOKE=1（本机隔离监听冒烟）")
def test_coturn_local_listen_smoke() -> None:
    """隔离冒烟：随机测试 secret + loopback 豁免 + 仅绑 127.0.0.1 + 缩小 relay 段，
    真启动容器并做 STUN Binding 探测（UDP 3478 有响应）。不碰主栈 project 与生产库；
    只验证「配置合法 + 服务在听」，不构成 TURN 可用性/公网中继验收。"""
    assert _compose_available(), "需要 docker compose CLI"
    relay_ports = list(range(50000, 50010))
    needed = [3478, 5349, *relay_ports]
    busy = [p for p in needed if not _udp_port_free(p)]
    if busy:
        pytest.skip(f"冒烟端口被占用，跳过: {busy[:5]}...")

    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as handle:
        handle.write("")
        env_file = handle.name
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("COTURN_")}
    env.update(
        {
            "COTURN_STATIC_AUTH_SECRET": secrets.token_hex(32),
            "COTURN_EXTERNAL_IP": "127.0.0.1",
            "COTURN_ALLOW_LOOPBACK_EXTERNAL": "true",
            "COTURN_BIND_IP": "127.0.0.1",
            "COTURN_RELAY_PORT_START": "50000",
            "COTURN_RELAY_PORT_END": "50009",
            "COTURN_VERBOSE": "true",
        }
    )
    base = ["docker", "compose", "--env-file", env_file, "-f", str(COTURN_COMPOSE)]
    try:
        up = subprocess.run(
            base + ["up", "-d"], capture_output=True, text=True, encoding="utf-8", timeout=420, env=env, check=False,
        )
        assert up.returncode == 0, up.stderr

        # 就绪判定 = 协议级 STUN Binding 探测轮询（当前镜像日志无稳定 banner 文本，
        # "listener opened" 属 verbose 细节；探测成功即证明 UDP 3478 真实在听且应答）
        def _stun_probe() -> bytes | None:
            txid = secrets.token_bytes(12)
            request = struct.pack("!HHI", 0x0001, 0, 0x2112A442) + txid
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.settimeout(5)
            try:
                probe.sendto(request, ("127.0.0.1", 3478))
                data, _ = probe.recvfrom(2048)
            except (TimeoutError, OSError):
                return None
            finally:
                probe.close()
            if data[:2] == b"\x01\x01" and data[4:8] == b"\x21\x12\xa4\x42" and data[8:20] == txid:
                return data
            return None

        response: bytes | None = None
        deadline = time.monotonic() + 180
        logs = ""
        while time.monotonic() < deadline and response is None:
            response = _stun_probe()
            if response is None:
                time.sleep(3)
                logs = subprocess.run(
                    base + ["logs", "--no-log-prefix", "coturn"],
                    capture_output=True, text=True, encoding="utf-8", timeout=60, env=env, check=False,
                ).stdout
        assert response is not None, f"180s 内 STUN Binding 无响应；容器日志尾:\n{logs[-2000:]}"
        # 配置错误会以 Bad configuration format 告警出现在日志——冒烟不允许存在
        assert "Bad configuration format" not in logs, f"配置含镜像不识别的选项:\n{logs[-2000:]}"
    finally:
        subprocess.run(
            base + ["down"], capture_output=True, text=True, encoding="utf-8", timeout=120, env=env, check=False,
        )
        os.unlink(env_file)
