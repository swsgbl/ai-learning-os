"""M10-05 外部 coturn 部署模板：独立 compose、fail-closed 配置、端口不冲突、secret 不入库。

覆盖矩阵：
1. 静态断言（无 docker 依赖）：主栈 compose 不含 coturn（yaml 服务集 + 无 include +
   原文无 coturn 字样）；coturn compose 为独立 project、secret/external-ip 用 :? 插值
   （无字面默认值）；模板文件无真实 secret 材料（32+ hex / 仓库开发占位；镜像 digest
   pin 除外——它是公开镜像标识不是 secret）；entrypoint.sh 为纯 LF（容器内 /bin/sh
   无法执行 CRLF）且 git index mode 为 100755；默认镜像 pin digest（:latest 不得回归）；
   模板不再引用 /run/aios-turnserver.conf（运行时实际写 /tmp）；
2. 端口不冲突：coturn 默认端口（3478/5349/50000-50099）与主栈宿主端口
   （5433/6379/9000/9001/8000/3000/7880/7881 + LiveKit UDP 7882-7892）零交集，
   entrypoint 内置 LiveKit 全端口 7880-7892（TCP 7880 signal / TCP 7881 rtc /
   UDP 7882-7892 媒体）同机硬冲突检查；
3. entrypoint 行为矩阵（bash 子进程直跑，COTURN_CONFIG_ONLY 打印脱敏配置）：
   缺必填/占位 secret/弱 secret/占位与 loopback external-ip/external-ip 非
   IPv4/relay 与 listening/TLS 口落入 LiveKit 7880-7892/relay 段倒置/超宽/越界
   （<1024 或 >65535，含 65536）/端口自冲突（listening==TLS、listening/TLS 落在
   relay 段内，即使 TLS disabled 也拒绝——compose 无条件映射 TLS TCP 端口）/
   配置注入字符（realm/external-ip/证书路径换行、空白、非绝对路径）/
   无效布尔值（maybe/1/yes 一律拒绝，不静默当 false）/TLS 启用缺证书
   → 一律 FAIL-CLOSED；合法输入 → 配置生成且 stdout 不含真实 secret；
   豁免开关与 TLS 真实证书路径通过；
4. docker compose 渲染：必填变量齐全渲染成功（服务/端口/卷/默认 digest 镜像）；
   任一必填变量缺失时 config 直接失败（:? 语法，无默认值兜底）；主栈
   --profile local 渲染不含 coturn；
5. 门控冒烟（AIOS_COTURN_SMOKE=1）：隔离环境（loopback 豁免 + 随机测试 secret +
   仅绑 127.0.0.1 + 缩小 relay 段）真启动容器，STUN Binding 探测 UDP 3478 有响应
   后 down——必须断言 down exit 0 且独立 project 无容器残留。只验证本机监听，
   不构成 TURN 可用性验收（见 docs/COTURN_DEPLOYMENT.md）。
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
# LiveKit 占 TCP 7880(signal)/7881(rtc-tcp) + UDP 7882-7892(媒体)——宿主层面即连续段
# 7880-7892，是 coturn 所有端口（listening/TLS/relay，UDP+TCP 都映射）必须避开的硬边界。
LIVEKIT_HOST_PORT_RANGE = (7880, 7892)
MAIN_HOST_PORTS = {5433, 6379, 9000, 9001, 8000, 3000, 7880, 7881, *range(7882, 7893)}
COTURN_DEFAULT_PORTS = {3478, 5349, *range(50000, 50100)}

# 默认镜像 pin 到本机已实测 digest（Codex 返工缺陷 6：:latest 标签漂移不可重现）
DEFAULT_IMAGE_DIGEST = "coturn/coturn@sha256:aa68aab64a3b929d57fc2924c98ea447bf996cf8dade2508e7b71eaf23f1f14e"

# entrypoint 校验矩阵共用的合法基线（TEST-NET-2/3 文档 IP，非任何真实环境）；
# secret 每次运行随机生成——仓库与测试代码不含任何固定 secret 材料
VALID_EXTERNAL_IP = "198.51.100.10"
VALID_PUBLIC_PRIVATE_IPS = "203.0.113.5/10.0.0.5"


# ---------------------------------------------------------------- 静态断言


def test_main_compose_has_no_coturn_service() -> None:
    """主栈 compose：无 coturn 服务、无 include 引入、原文无 coturn 字样。"""
    import yaml

    raw = MAIN_COMPOSE.read_text(encoding="utf-8")
    assert "coturn" not in raw.lower()
    model = yaml.safe_load(raw)
    assert set(model["services"]) == {"postgres", "redis", "minio", "api", "livekit", "web", "searxng"}
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


def test_image_defaults_to_pinned_digest() -> None:
    """默认镜像 pin 已实测 digest；:latest 不得回归为默认（换镜像必须显式覆盖）。"""
    compose_raw = COTURN_COMPOSE.read_text(encoding="utf-8")
    assert DEFAULT_IMAGE_DIGEST in compose_raw, "compose 默认镜像必须是 digest pin"
    assert "coturn/coturn:latest" not in compose_raw, "默认镜像不得回退 :latest（标签漂移不可重现）"
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert f"COTURN_IMAGE={DEFAULT_IMAGE_DIGEST}" in env_example


def test_template_files_contain_no_secret_material() -> None:
    """infra/coturn 全部模板文件：无 32+ hex 随机串（镜像 digest pin 除外）、无仓库
    开发占位 secret；entrypoint 纯 LF。"""
    files = sorted(p for p in COTURN_DIR.iterdir() if p.is_file())
    assert {p.name for p in files} >= {
        "docker-compose.coturn.yml",
        "entrypoint.sh",
        ".env.example",
        "turnserver.conf.example",
    }
    for path in files:
        text = path.read_text(encoding="utf-8")
        # sha256:<64-hex> 是公开镜像内容标识（非 secret），剥离后再扫描真实 secret 材料
        scrubbed = re.sub(r"sha256:[0-9a-fA-F]{64}", "", text)
        assert not re.search(r"[0-9a-fA-F]{32,}", scrubbed), f"{path.name} 疑似含真实 secret 材料"
        # 精确到完整占位值：entrypoint 的拒绝分支合法引用 aios-local-dev 前缀模式本身
        assert "aios-local-dev-secret" not in text, f"{path.name} 含仓库开发占位 secret"
    assert b"\r" not in ENTRYPOINT.read_bytes(), "entrypoint.sh 必须纯 LF（容器 /bin/sh 不认 CRLF）"
    # .env.example 的必填变量保持占位形态（<...>），不得出现看似可用的默认值
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert re.search(r"^COTURN_STATIC_AUTH_SECRET=<", env_example, re.MULTILINE)
    assert re.search(r"^COTURN_EXTERNAL_IP=<", env_example, re.MULTILINE)


def test_entrypoint_keeps_executable_git_mode() -> None:
    """entrypoint.sh 的 git index mode 必须为 100755——compose 虽经 /bin/sh 调用，
    文件本身也应带 executable bit（文件语义正确；此前报告口径与实际不一致，锁定）。"""
    if not (REPO / ".git").exists():
        pytest.skip("非 git checkout 环境")
    result = subprocess.run(
        ["git", "-C", REPO.as_posix(), "ls-files", "-s", "--", "infra/coturn/entrypoint.sh"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), "entrypoint.sh 不在 git index 中"
    mode = result.stdout.split()[0]
    assert mode == "100755", f"entrypoint.sh git mode={mode}（应为 100755，可执行语义）"


def test_template_docs_match_runtime_conf_path() -> None:
    """模板文件不得引用 /run/aios-turnserver.conf——运行时实际生成于容器 /tmp
    （官方镜像非 root，/run 不可写）。"""
    for path in (ENTRYPOINT, CONF_EXAMPLE):
        text = path.read_text(encoding="utf-8")
        assert "/run/aios-turnserver.conf" not in text, f"{path.name} 残留 /run 路径描述（实际为 /tmp）"
    assert "/tmp/aios-turnserver.conf" in ENTRYPOINT.read_text(encoding="utf-8")


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
    # entrypoint 内置同机硬冲突检查：LiveKit 全部宿主端口（TCP 7880/7881 + UDP 7882-7892，
    # 即连续段 7880-7892）与 relay 段/listening/TLS 口都不允许重叠
    entry = ENTRYPOINT.read_text(encoding="utf-8")
    for port in ("7880", "7881", "7882", "7892"):
        assert port in entry, f"entrypoint 缺少 LiveKit 端口 {port} 的冲突检查"


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


def _msys_path(path: Path) -> str:
    """Windows 宿主路径转 Git Bash（MSYS）形式 C:/x → /c/x。

    entrypoint 的证书路径校验只接受以 / 开头的绝对 POSIX 路径（容器内语义），
    测试宿主机上的 C:/... 需转换后在 Git Bash 中才等价。
    """
    text = path.as_posix()
    match = re.match(r"^([A-Za-z]):/(.*)$", text)
    return f"/{match.group(1).lower()}/{match.group(2)}" if match else text


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
def test_entrypoint_rejects_injection_in_secret_value() -> None:
    """secret 值含换行/空白（可向生成的配置注入额外配置行）：拒绝。"""
    injected = _rejected(secret="a" * 40 + "\nlistening-port=1")
    assert "COTURN_STATIC_AUTH_SECRET" in injected.stderr and "注入" in injected.stderr
    spaced = _rejected(secret="a" * 40 + " b")
    assert "COTURN_STATIC_AUTH_SECRET" in spaced.stderr


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
@pytest.mark.parametrize(
    "bad_ip",
    [
        "turn.example.com",  # 主机名不是 IP
        "999.1.1.1",  # 八位组越界
        "1.2.3",  # 段数不足
        "1.2.3.4.5",  # 段数超
        "1.2.3.4\nno-multicast-peers",  # 换行注入
        "1.2.3.4 ",  # 尾部空白
        "203.0.113.5/not-an-ip",  # PUBLIC/PRIVATE 的 PRIVATE 半边非法
        "not-an-ip/10.0.0.5",  # PUBLIC/PRIVATE 的 PUBLIC 半边非法
        "2001:db8::1",  # IPv6 不支持（本模板生产路径为 IPv4）
    ],
)
def test_entrypoint_rejects_malformed_external_ip(bad_ip: str) -> None:
    """external-ip 只接受 IPv4 或 PUBLIC/PRIVATE IPv4——任意字符串/IPv6/注入拒绝。"""
    result = _rejected(external_ip=bad_ip)
    assert "COTURN_EXTERNAL_IP" in result.stderr


@pytest.mark.skipif(not _bash_available(), reason="需要 bash（POSIX sh 子进程）")
def test_entrypoint_accepts_public_private_ipv4_form() -> None:
    """1:1 NAT 的 PUBLIC/PRIVATE IPv4 形式合法，原样写入配置。"""
    allowed = _ok_config_only(external_ip=VALID_PUBLIC_PRIVATE_IPS)
    assert f"external-ip={VALID_PUBLIC_PRIVATE_IPS}" in allowed.stdout


@pytest.mark.skipif(not _bash_available(), reason="需要 bash（POSIX sh 子进程）")
@pytest.mark.parametrize(
    ("overrides", "keyword"),
    [
        # LiveKit TCP signal 7880 / rtc-tcp 7881——listening/TLS/relay 三个角色都不允许撞
        ({"COTURN_LISTEN_PORT": "7880"}, "LiveKit"),
        ({"COTURN_LISTEN_PORT": "7881"}, "LiveKit"),
        ({"COTURN_TLS_PORT": "7880"}, "LiveKit"),
        ({"COTURN_LISTEN_PORT": "7885"}, "LiveKit"),  # UDP 媒体段中部
        # relay 段只覆盖 7880/7881、不触 7882-7892 也必须拒绝（协议无关判定）
        ({"COTURN_RELAY_PORT_START": "7878", "COTURN_RELAY_PORT_END": "7881"}, "LiveKit"),
        ({"COTURN_RELAY_PORT_START": "7882", "COTURN_RELAY_PORT_END": "7890"}, "LiveKit"),
        # relay 段尾端压住 7892
        ({"COTURN_RELAY_PORT_START": "7890", "COTURN_RELAY_PORT_END": "7900"}, "LiveKit"),
    ],
)
def test_entrypoint_rejects_livekit_port_overlap(overrides: dict[str, str], keyword: str) -> None:
    """coturn 任何端口角色与主栈 LiveKit 宿主端口 7880-7892 同机冲突：拒绝。"""
    result = _rejected(**overrides)
    assert keyword in result.stderr and "7880-7892" in result.stderr


@pytest.mark.skipif(not _bash_available(), reason="需要 bash（POSIX sh 子进程）")
def test_entrypoint_livekit_overlap_exempt_only_with_explicit_flag() -> None:
    """跨机部署显式豁免可通过，且豁免只放行 LiveKit 段——端口自冲突仍拒绝。"""
    _ok_config_only(
        COTURN_RELAY_PORT_START="7882",
        COTURN_RELAY_PORT_END="7890",
        COTURN_ALLOW_LIVEKIT_PORT_OVERLAP="true",  # 跨机部署显式豁免
    )
    still_rejected = _rejected(
        COTURN_TLS_PORT="3478",
        COTURN_ALLOW_LIVEKIT_PORT_OVERLAP="true",  # 豁免救不了自冲突
    )
    assert "自冲突" in still_rejected.stderr


@pytest.mark.skipif(not _bash_available(), reason="需要 bash（POSIX sh 子进程）")
@pytest.mark.parametrize(
    "overrides",
    [
        # > 65535：超出 TCP/UDP 合法端口上限（含 65536 回归用例）
        {"COTURN_RELAY_PORT_END": "65536"},
        {"COTURN_RELAY_PORT_START": "65536"},
        {"COTURN_LISTEN_PORT": "65536"},
        {"COTURN_TLS_PORT": "65536"},
        # < 1024：特权端口
        {"COTURN_RELAY_PORT_START": "1023"},
        {"COTURN_LISTEN_PORT": "1023"},
        {"COTURN_TLS_PORT": "1023"},
        # relay 段自身非法
        {"COTURN_RELAY_PORT_START": "50100", "COTURN_RELAY_PORT_END": "50099"},  # 倒置
        {"COTURN_RELAY_PORT_START": "50000", "COTURN_RELAY_PORT_END": "52000"},  # 超宽 > 2000
        {"COTURN_LISTEN_PORT": "34x8"},  # 非数字
    ],
)
def test_entrypoint_rejects_ports_outside_legal_range(overrides: dict[str, str]) -> None:
    """端口越界（<1024 / >65535）、relay 倒置/超宽、非数字：一律拒绝。"""
    _rejected(**overrides)


@pytest.mark.skipif(not _bash_available(), reason="需要 bash（POSIX sh 子进程）")
def test_entrypoint_accepts_upper_boundary_single_relay_port() -> None:
    """合法上边界：65535 单端口 relay 段（不撞 listening/TLS/LiveKit）可通过。"""
    allowed = _ok_config_only(COTURN_RELAY_PORT_START="65535", COTURN_RELAY_PORT_END="65535")
    assert "min-port=65535" in allowed.stdout and "max-port=65535" in allowed.stdout


@pytest.mark.skipif(not _bash_available(), reason="需要 bash（POSIX sh 子进程）")
def test_entrypoint_rejects_port_self_conflicts() -> None:
    """listening/TLS/relay 自身映射冲突拒绝——即使 TLS disabled（compose 一直映射
    TLS TCP 端口），且与跨机豁免无关。"""
    listen_eq_tls = _rejected(COTURN_TLS_PORT="3478")
    assert "自冲突" in listen_eq_tls.stderr
    listen_in_relay = _rejected(COTURN_LISTEN_PORT="50050")
    assert "自冲突" in listen_in_relay.stderr
    tls_in_relay = _rejected(COTURN_TLS_PORT="50050")  # TLS 默认 false 仍拒绝
    assert "自冲突" in tls_in_relay.stderr
    # 错开的端口组合不受影响（负向保护：合法相邻端口可通过）
    _ok_config_only(COTURN_LISTEN_PORT="3478", COTURN_TLS_PORT="3479")


@pytest.mark.skipif(not _bash_available(), reason="需要 bash（POSIX sh 子进程）")
@pytest.mark.parametrize(
    ("switch", "bad"),
    [
        ("COTURN_VERBOSE", "maybe"),
        ("COTURN_TLS_ENABLED", "1"),
        ("COTURN_ALLOW_LOOPBACK_EXTERNAL", "yes"),
        ("COTURN_ALLOW_LIVEKIT_PORT_OVERLAP", "on"),
    ],
)
def test_entrypoint_rejects_invalid_boolean_switches(switch: str, bad: str) -> None:
    """布尔开关无效值（maybe/1/yes/on）拒绝启动，绝不静默当 false。"""
    result = _rejected(**{switch: bad})
    assert switch in result.stderr and "true/false" in result.stderr
    # COTURN_CONFIG_ONLY 会被 _rejected helper 占位，单独直跑
    config_only = _run_entrypoint(_entrypoint_env(COTURN_CONFIG_ONLY="maybe"))
    assert config_only.returncode != 0
    assert "COTURN_CONFIG_ONLY" in config_only.stderr


@pytest.mark.skipif(not _bash_available(), reason="需要 bash（POSIX sh 子进程）")
@pytest.mark.parametrize(
    ("overrides", "keyword"),
    [
        ({"COTURN_REALM": "aios\nlistening-port=1"}, "COTURN_REALM"),  # 换行注入配置行
        ({"COTURN_REALM": "my realm"}, "COTURN_REALM"),  # 空白
        ({"COTURN_REALM": "a&b"}, "COTURN_REALM"),  # 元字符（白名单外）
        ({"COTURN_CERT_FILE": "tls/cert.pem"}, "绝对路径"),  # 相对路径
        ({"COTURN_CERT_FILE": "/etc/coturn/tls/my cert.pem"}, "COTURN_CERT_FILE"),  # 路径含空白
        ({"COTURN_PKEY_FILE": "/etc/coturn/tls/key.pem\nx"}, "COTURN_PKEY_FILE"),  # 路径含换行
        ({"COTURN_PKEY_FILE": "key.pem"}, "绝对路径"),
    ],
)
def test_entrypoint_rejects_config_injection_values(overrides: dict[str, str], keyword: str) -> None:
    """写入配置的 realm/证书路径含换行/空白/元字符/相对路径：拒绝（防配置注入）。"""
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
    # 转为 MSYS 绝对路径：entrypoint 只接受以 / 开头的容器内绝对路径语义
    cert_path, key_path = _msys_path(cert), _msys_path(key)
    if any(re.search(r"[^A-Za-z0-9._/-]", p) for p in (cert_path, key_path)):
        pytest.skip(f"宿主临时目录路径含白名单外字符，无法经严格路径校验: {cert_path}")
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
    """必填变量齐全：渲染出单服务独立 project（默认 digest 镜像），listening/TLS/relay 端口映射齐全。"""
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
    # 未显式覆盖 COTURN_IMAGE 时必须渲染为 digest pin 默认（可重现，非 :latest）
    assert model["services"]["coturn"]["image"].startswith("coturn/coturn@sha256:"), model["services"]["coturn"]["image"]
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
def test_compose_render_image_override_replaces_digest_default() -> None:
    """显式覆盖 COTURN_IMAGE 生效（运维换镜像的唯一途径——默认不回退 :latest）。"""
    result = _render(
        {
            "COTURN_STATIC_AUTH_SECRET": secrets.token_hex(32),
            "COTURN_EXTERNAL_IP": "198.51.100.10",
            "COTURN_IMAGE": "registry.example.com/coturn@sha256:" + "b" * 64,
        }
    )
    assert result.returncode == 0, result.stderr
    model = json.loads(result.stdout)
    assert model["services"]["coturn"]["image"] == "registry.example.com/coturn@sha256:" + "b" * 64


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
    """任一必填变量缺失：config 直接失败并点名变量——无默认值兜底（:? 语法）。

    compose 的 :? 校验是 fail-fast：多个必填变量同时缺失时只报第一个就退出，
    报哪个取决于 compose 内部求值顺序，跨平台/版本不稳定（CI Ubuntu 只报
    COTURN_EXTERNAL_IP）。故只缺一个时 stderr 必须点名该变量；两个都缺时
    点名任一实际缺失必填变量即可，不依赖 fail-fast 的报错顺序——但仍要求
    stderr 命中真实缺失的变量名，不接受任意错误蒙混过关。
    """
    result = _render(overrides)
    assert result.returncode != 0, f"缺必填变量仍渲染成功:\n{result.stdout}"
    missing = [
        name
        for name in ("COTURN_STATIC_AUTH_SECRET", "COTURN_EXTERNAL_IP")
        if name not in overrides
    ]
    if len(missing) == 1:
        assert missing[0] in result.stderr
    else:
        assert any(name in result.stderr for name in missing), (
            f"stderr 未点名任一实际缺失必填变量 {missing}:\n{result.stderr}"
        )


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


def _compose_down(
    base: list[str], env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        base + ["down"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        env=env,
        check=False,
    )


@pytest.mark.skipif(not _bash_available(), reason="需要 bash")
@pytest.mark.skipif(os.environ.get("AIOS_COTURN_SMOKE") != "1", reason="需 AIOS_COTURN_SMOKE=1（本机隔离监听冒烟）")
def test_coturn_local_listen_smoke() -> None:
    """隔离冒烟：随机测试 secret + loopback 豁免 + 仅绑 127.0.0.1 + 缩小 relay 段，
    真启动容器并做 STUN Binding 探测（UDP 3478 有响应）。不碰主栈 project 与生产库；
    只验证「配置合法 + 服务在听」，不构成 TURN 可用性/公网中继验收。
    结束时 down 必须 exit 0 且独立 project 无容器残留（此前只执行不断言——Codex 返工缺陷 7）。"""
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
    down: subprocess.CompletedProcess[str] | None = None
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
        # 主体成功后立即清理并保留结果供断言（失败路径由 finally 兜底尽力清理）
        down = _compose_down(base, env)
    finally:
        if down is None:
            _compose_down(base, env)  # 尽力清理，不再断言——保留原始失败原因
        os.unlink(env_file)
    # down 必须 exit 0：清理失败会让端口/容器残留，影响后续验证（不允许静默通过）
    assert down is not None and down.returncode == 0, f"compose down 失败:\n{down.stderr if down else '未执行'}"
    # 残留断言：只读查询独立 project 的容器标签——不触碰其他 project/服务
    residual = subprocess.run(
        ["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=ai-learning-os-coturn", "-q"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert residual.returncode == 0, residual.stderr
    assert not residual.stdout.strip(), f"project ai-learning-os-coturn 容器残留: {residual.stdout.strip()}"
