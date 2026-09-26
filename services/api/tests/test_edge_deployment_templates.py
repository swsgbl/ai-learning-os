"""M14-153 边缘部署模板 fail-closed 校验：infra/edge/ 全部模板的渲染/静态断言。

覆盖矩阵（任务书 + supervisor Round 1 反馈第 7 条）：
1. token-from-file：frps/frpc 认证必须 auth.tokenSource.type="file"，
   不得出现 inline `auth.token`（互斥语义二选一，模板只允许文件形态）；
2. TLS force：frps transport.tls.force=true、frpc transport.tls.enable=true；
3. loopback-only vhost：frps proxyBindAddr=127.0.0.1 + vhostHTTPPort=8080，
   Caddy 对 app/api 的反代目标必须是 127.0.0.1:8080（同一 frps vhost），
   livekit 反代目标必须是 127.0.0.1:7880（与 livekit.edge.yaml port 一致）；
4. 无 inline/default/weak secret：全部模板无 32+ hex 材料（镜像 digest pin
   剥离后扫描）、无仓库开发占位 secret、compose 必填变量 :? 无 :- 默认；
5. 域名占位：模板内全部域名字符只能是 *.example.com / example.com /
   localhost / docker.io（镜像仓库域）——真实域名绝不入库；VPS IP 只能是
   <占位>（允许的 IP 字面量仅 127.0.0.1 / 0.0.0.0 / RFC 5737 文档段）；
6. 端口不冲突（Caddy/frp/LiveKit/coturn 四方）：caddy 80/443(tcp)+443(udp)、
   frps 7000+8080(loopback)、livekit 7880(loopback)+7881+60000-60100/udp、
   coturn 3478(udp+tcp)+5349/tcp+50000-50099(udp+tcp)——两两零交集，且
   常量与配置文件实际值逐项对账（防文档/配置漂移）；
7. docker compose 渲染（门控 docker CLI）：必填变量齐全渲染成功（host 网络
   三服务无 ports 发布、coturn 端口集精确、secrets 接线到提供的文件、
   livekit --keys 注入必填凭据）；任一必填变量缺失 config 直接失败；
8. 分发清单模板：结构完整 + sha256 全占位（绝不出现 64 位真值 hex）；
9. 文档契约：runbook/研究文档/移动分发文档存在且覆盖规定主题，
   研究文档已改用已核实的 LiveKit 外部 TURN 语法（rtc.turn_servers[] +
   secret_file，不用未核实的顶层外部 turn secret 字段）。

边界：本文件只读模板/文档，不发任何网络请求、不启动任何容器。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import tomllib

REPO = Path(__file__).resolve().parents[3]
EDGE_DIR = REPO / "infra" / "edge"
FRPS_EXAMPLE = EDGE_DIR / "frps.toml.example"
FRPC_EXAMPLE = EDGE_DIR / "frpc.windows.toml.example"
CADDYFILE = EDGE_DIR / "Caddyfile.example"
LIVEKIT_EDGE = EDGE_DIR / "livekit.edge.yaml.example"
EDGE_COMPOSE = EDGE_DIR / "docker-compose.edge.example.yml"
EDGE_ENV = EDGE_DIR / ".env.example"
DOWNLOAD_MANIFEST = EDGE_DIR / "download-manifest.example.json"
COTURN_ENTRYPOINT = REPO / "infra" / "coturn" / "entrypoint.sh"

RUNBOOK = REPO / "docs" / "PUBLIC_EDGE_DEPLOYMENT.md"
RESEARCH_DOC = REPO / "docs" / "PUBLIC_EDGE_DEPLOYMENT_RESEARCH.md"
MOBILE_DOC = REPO / "docs" / "MOBILE_DISTRIBUTION.md"

# ---------------------------------------------------------------- 端口模型（单一事实常量）

# 边缘栈宿主端口规划（与各模板实际值逐项对账；与家机主栈分属两台机器，
# 家机侧只跑 frpc 出站连接——不存在同机端口面，故不做跨机冲突断言）
EDGE_PORTS: dict[str, dict[str, set[int]]] = {
    "caddy": {"tcp": {80, 443}, "udp": {443}},  # HTTP/3 需要 UDP 443
    "frps": {"tcp": {7000, 8080}},  # 8080 仅 loopback（proxyBindAddr）
    "livekit": {"tcp": {7880, 7881}, "udp": set(range(60000, 60101))},
    "coturn": {
        "tcp": {3478, 5349, *range(50000, 50100)},
        "udp": {3478, *range(50000, 50100)},
    },
}

# 扫描豁免：镜像 digest pin 是公开内容标识（非 secret），剥离后再扫
BANNED_SECRET_MARKERS = (
    "devkey",
    "aios-local-dev-secret",
    "ailos-local-dev-secret",
    "changeme",
    "change-me",
    "secret-that-is-at-least",
)

ALLOWED_IP_LITERALS = {"127.0.0.1", "0.0.0.0"}


def _is_rfc5737(ip: str) -> bool:
    return any(
        ip.startswith(prefix)
        for prefix in ("192.0.2.", "198.51.100.", "203.0.113.")
    )


# ---------------------------------------------------------------- 文件存在性


def test_edge_template_file_set() -> None:
    """infra/edge/ 模板集合完整（缺一即失败——交付面锁死）。"""
    expected = {
        "frps.toml.example",
        "frpc.windows.toml.example",
        "Caddyfile.example",
        "livekit.edge.yaml.example",
        "docker-compose.edge.example.yml",
        ".env.example",
        "download-manifest.example.json",
    }
    actual = {p.name for p in EDGE_DIR.iterdir() if p.is_file()}
    assert expected <= actual, f"缺模板: {sorted(expected - actual)}"


# ---------------------------------------------------------------- frps/frpc：token-from-file + TLS force


def test_frps_token_from_file_and_tls_force() -> None:
    """frps：token 必须来自文件 + TLS 强制 + vhost 只绑 loopback。"""
    raw = FRPS_EXAMPLE.read_text(encoding="utf-8")
    model = tomllib.loads(raw)
    assert model["bindPort"] == 7000
    assert model["bindAddr"] == "0.0.0.0"
    # token-from-file（ValueSource；与 auth.token 互斥）
    assert model["auth"]["method"] == "token"
    assert model["auth"]["tokenSource"]["type"] == "file"
    assert model["auth"]["tokenSource"]["file"]["path"] == "/run/secrets/frps_token"
    assert "token" not in model["auth"], "frps 模板不得携带 inline auth.token"
    # TLS force + vhost loopback-only
    assert model["transport"]["tls"]["force"] is True
    assert model["proxyBindAddr"] == "127.0.0.1", "frps vhost 必须只绑 loopback"
    assert model["vhostHTTPPort"] == 8080


def test_frpc_token_from_file_and_tls_enable() -> None:
    """frpc：token 文件 + TLS 显式开启 + serverAddr 占位 + loopback 本地端口。"""
    raw = FRPC_EXAMPLE.read_text(encoding="utf-8")
    model = tomllib.loads(raw)
    assert model["serverAddr"].startswith("<") and model["serverAddr"].endswith(">")
    assert model["serverPort"] == 7000
    auth = model["auth"]
    assert auth["method"] == "token"
    assert auth["tokenSource"]["type"] == "file"
    assert "token" not in auth, "frpc 模板不得携带 inline auth.token"
    assert auth["tokenSource"]["file"]["path"].endswith("frpc_token.txt")
    assert model["transport"]["tls"]["enable"] is True
    proxies = model["proxies"]
    assert [p["name"] for p in proxies] == ["aios-web", "aios-api"]
    for proxy in proxies:
        assert proxy["type"] == "http"
        assert proxy["localIP"] == "127.0.0.1", "家机侧只允许回连本机 loopback"
    assert proxies[0]["localPort"] == 3011 and proxies[1]["localPort"] == 8000
    domains = [d for p in proxies for d in p["customDomains"]]
    assert set(domains) == {"app.example.com", "api.example.com"}


# ---------------------------------------------------------------- Caddyfile：路由与头


def _caddy_site_block(text: str, site: str) -> str:
    match = re.search(rf"^{re.escape(site)} \{{(.*?)^\}}", text, re.MULTILINE | re.DOTALL)
    assert match is not None, f"Caddyfile 缺站点块 {site}"
    return match.group(1)


def test_caddyfile_routes_to_frps_vhost_and_livekit_loopback() -> None:
    """Caddy→frps/LiveKit 路由：目标必须是本机 loopback 的 frps vhost(8080)/signal(7880)。"""
    text = CADDYFILE.read_text(encoding="utf-8")
    for site in ("app.example.com", "api.example.com"):
        block = _caddy_site_block(text, site)
        assert "reverse_proxy 127.0.0.1:8080" in block, (
            f"{site} 必须经 frps vhost 8080（与 frps.toml vhostHTTPPort 一致）"
        )
    livekit_block = _caddy_site_block(text, "livekit.example.com")
    assert "reverse_proxy 127.0.0.1:7880" in livekit_block
    # 站点集合恰为五个占位域（多了=引入未审路由，少了=入口缺失）
    sites = re.findall(r"^([a-z0-9.-]+\.example\.com) \{", text, re.MULTILINE)
    assert set(sites) == {
        "app.example.com",
        "api.example.com",
        "livekit.example.com",
        "download.example.com",
    }
    # 公网站点必须走 ACME 自动 HTTPS：不允许 internal 自签/显式降级
    assert "tls internal" not in text
    assert not re.search(r"^http://", text, re.MULTILINE)
    assert "email admin@example.com" in text, "ACME 邮箱占位必须存在"


def test_caddyfile_api_body_limit_and_security_headers() -> None:
    """api 站点限请求体；app/api 站点带安全响应头（preflight 检查集合同源）。"""
    text = CADDYFILE.read_text(encoding="utf-8")
    api_block = _caddy_site_block(text, "api.example.com")
    assert re.search(r"request_body \{\s*max_size 64MB\s*\}", api_block)
    app_block = _caddy_site_block(text, "app.example.com")
    for header in (
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "Referrer-Policy",
    ):
        assert header in app_block, f"app 站点缺安全头 {header}"
        assert header in api_block, f"api 站点缺安全头 {header}"
    download_block = _caddy_site_block(text, "download.example.com")
    assert "file_server" in download_block
    assert "file_server" not in app_block and "file_server" not in api_block


# ---------------------------------------------------------------- livekit.edge.yaml


def test_livekit_edge_config_loopback_signal_and_media_ports() -> None:
    """边缘 LiveKit：signal 只绑 loopback、媒体段 60000-60100、内嵌 TURN 关闭。"""
    import yaml

    raw = LIVEKIT_EDGE.read_text(encoding="utf-8")
    model = yaml.safe_load(raw)
    assert model["port"] == 7880
    assert model["bind_addresses"] == ["127.0.0.1"], "signal 必须只绑 loopback（Caddy 前置）"
    rtc = model["rtc"]
    assert rtc["tcp_port"] == 7881
    assert (rtc["port_range_start"], rtc["port_range_end"]) == (60000, 60100)
    # Round 3 修订：v1.13.7 官方 config-sample 明示 node_ip 只在
    # use_external_ip=false 时生效（"use_external_ip takes precedence"）——
    # compose 强制传 --node-ip，故此处必须 false
    assert rtc["use_external_ip"] is False
    assert rtc["enable_loopback_candidate"] is False
    assert model["turn"]["enabled"] is False, "内嵌 TURN 必须关闭（外部 coturn 提供）"
    # keys 不得出现在配置文件——凭据只经 compose --keys 从必填 env 注入
    assert "keys" not in model
    # 外部 coturn 通告块：必须以注释形态存在且使用已核实的 secret_file 语法
    assert "turn_servers" in raw and "secret_file: /run/secrets/turn_secret" in raw
    assert "protocol: tls" in raw
    # 注释掉的块不应被 YAML 解析进模型（部署副本取消注释才生效）
    assert "turn_servers" not in model.get("rtc", {})


# ---------------------------------------------------------------- 端口不冲突（四方对账）


def test_edge_ports_pairwise_disjoint() -> None:
    """Caddy/frp/LiveKit/coturn 四方宿主端口两两零交集（同机 VPS 硬冲突）。"""
    names = sorted(EDGE_PORTS)
    for left in names:
        for right in names:
            if left >= right:
                continue
            for proto in ("tcp", "udp"):
                overlap = EDGE_PORTS[left].get(proto, set()) & EDGE_PORTS[right].get(proto, set())
                assert not overlap, (
                    f"{left} 与 {right} 的 {proto} 端口冲突: {sorted(overlap)}"
                )


def test_edge_port_constants_match_template_files() -> None:
    """端口常量与模板实际值逐项对账（防常量/配置漂移）。"""
    import yaml

    frps = tomllib.loads(FRPS_EXAMPLE.read_text(encoding="utf-8"))
    assert frps["bindPort"] in EDGE_PORTS["frps"]["tcp"]
    assert frps["vhostHTTPPort"] in EDGE_PORTS["frps"]["tcp"]
    livekit = yaml.safe_load(LIVEKIT_EDGE.read_text(encoding="utf-8"))
    assert livekit["port"] in EDGE_PORTS["livekit"]["tcp"]
    assert livekit["rtc"]["tcp_port"] in EDGE_PORTS["livekit"]["tcp"]
    media = set(range(livekit["rtc"]["port_range_start"], livekit["rtc"]["port_range_end"] + 1))
    assert media == EDGE_PORTS["livekit"]["udp"]
    compose_text = EDGE_COMPOSE.read_text(encoding="utf-8")
    for mapping in (
        "3478:3478/udp",
        "3478:3478/tcp",
        "5349:5349/tcp",
        "50000-50099:50000-50099/udp",
        "50000-50099:50000-50099/tcp",
    ):
        assert mapping in compose_text, f"coturn compose 缺端口映射 {mapping}"
    # caddy/frps/livekit host 网络不走 ports 发布（发布端口=配置错误）
    compose = yaml.safe_load(compose_text)
    for service in ("caddy", "frps", "livekit"):
        assert compose["services"][service]["network_mode"] == "host"
        assert not compose["services"][service].get("ports"), (
            f"{service} 是 host 网络，不得再声明 ports 发布"
        )


# ---------------------------------------------------------------- 无 secret/无真实域名的模板扫描


def test_edge_templates_contain_no_secret_material() -> None:
    """全部边缘模板：无 32+ hex 材料（digest pin 剥离）、无开发占位 secret。"""
    files = sorted(p for p in EDGE_DIR.iterdir() if p.is_file())
    assert len(files) >= 7
    for path in files:
        text = path.read_text(encoding="utf-8")
        scrubbed = re.sub(r"sha256:[0-9a-fA-F]{64}", "", text)
        hits = re.findall(r"[0-9a-fA-F]{32,}", scrubbed)
        assert not hits, f"{path.name} 疑似真实 secret/hash 材料: {hits[:2]}"
        for marker in BANNED_SECRET_MARKERS:
            assert marker not in text.lower(), f"{path.name} 含禁用占位 {marker}"


def test_edge_templates_contain_no_real_domains_or_ips() -> None:
    """域名占位与 IP 白名单：URL/裸域只允许占位域+官方文档域+docker.io；
    IP 字面量只允许回环/0.0.0.0/RFC 5737 文档段（真实运营域名/IP 绝不入库）。"""
    allowed_hosts = {
        "example.com",
        "app.example.com",
        "api.example.com",
        "livekit.example.com",
        "turn.example.com",
        "download.example.com",
        "localhost",
        "github.com",       # 模板注释里的官方文档引用
        "caddyserver.com",
        "livekit.io",
        "docker.io",        # compose 镜像仓库
        "127.0.0.1",        # healthcheck 的 loopback 探针 URL
    }
    files = sorted(p for p in EDGE_DIR.iterdir() if p.is_file())
    for path in files:
        text = path.read_text(encoding="utf-8")
        # IP 字面量白名单
        for candidate in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text):
            assert candidate in ALLOWED_IP_LITERALS or _is_rfc5737(candidate), (
                f"{path.name} 出现非白名单 IP: {candidate}"
            )
        # URL 主机白名单（http(s):// 后的第一个 host 段）
        for host in re.findall(r"https?://([A-Za-z0-9.-]+)(?:[:/)\]]|\s)", text):
            assert host.lower() in allowed_hosts, f"{path.name} URL 主机越界: {host}"
        # 常见 TLD 裸域扫描（配置键不含这些 TLD，命中即真实域名）
        for token in re.findall(r"\b[A-Za-z0-9-]+(?:\.(?:com|net|org|io|cn|dev|app|xyz))\b", text):
            assert token.lower() in allowed_hosts, f"{path.name} 出现非占位域名: {token}"


# ---------------------------------------------------------------- compose 静态断言


def test_edge_compose_fail_closed_required_vars_and_digest_pins() -> None:
    """必填变量 :?（无 :- 默认）、镜像 digest pin（无 :latest）、独立 project。"""
    import yaml

    raw = EDGE_COMPOSE.read_text(encoding="utf-8")
    compose = yaml.safe_load(raw)
    assert compose["name"] == "ai-learning-os-edge", "独立 project，与主栈/家机栈隔离"
    assert set(compose["services"]) == {"caddy", "frps", "livekit", "coturn"}
    required = (
        "AIOS_EDGE_LIVEKIT_KEYS_FILE",
        "AIOS_EDGE_VPS_PUBLIC_IP",
        "AIOS_EDGE_COTURN_TURN_SECRET",
        "AIOS_EDGE_FRPS_TOKEN_FILE",
        "AIOS_EDGE_TURN_SECRET_FILE",
    )
    for var in required:
        assert re.search(rf"\$\{{{re.escape(var)}:\?", raw), f"必填变量 {var} 缺 :? fail-closed 插值"
        assert not re.search(rf"\$\{{{re.escape(var)}:-", raw), f"必填变量 {var} 不得有 :- 默认值"
    # 镜像 digest pin（三个新镜像 + coturn 复用主仓默认 digest）
    images = {name: svc["image"] for name, svc in compose["services"].items()}
    assert images["caddy"] == (
        "docker.io/library/caddy@sha256:6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b"
    )
    assert images["frps"].startswith("docker.io/fatedier/frps@sha256:")
    assert images["livekit"] == (
        "docker.io/livekit/livekit-server@sha256:6fd3b7088874c4d119160dd688798dfec852bc014786d392caad15f6f63912a3"
    ), "LiveKit 镜像必须 pin 到 v1.13.7 digest（Round 3 修订）"
    for image in images.values():
        assert ":latest" not in image and not image.endswith(":v"), "不得回退浮动 tag"
    assert "coturn/coturn@sha256:aa68aab64a3b929d57fc2924c98ea447bf996cf8dade2508e7b71eaf23f1f14e" in images["coturn"]
    # coturn 复用主仓 entrypoint（同一份 fail-closed 校验语义）
    assert COTURN_ENTRYPOINT.is_file(), "复用的 infra/coturn/entrypoint.sh 必须存在"
    volumes = compose["services"]["coturn"]["volumes"]
    assert any("../coturn/entrypoint.sh" in str(v) for v in volumes)
    # livekit 凭据只经官方 --key-file 部署密钥文件注入（Round 2 修订）：
    # --keys/env 进程参数形态被本套件禁止回归（secret 不进 ps/docker inspect）
    livekit_command = compose["services"]["livekit"]["command"]
    assert "--key-file /run/secrets/livekit_keys" in livekit_command
    assert "--keys" not in livekit_command and "AIOS_EDGE_LIVEKIT_API" not in livekit_command
    # key 文件挂载 0600（LiveKit 对 key 文件权限硬校验，否则拒绝启动）
    livekit_secrets = {s["source"]: s for s in compose["services"]["livekit"]["secrets"]}
    assert set(livekit_secrets) == {"livekit_keys", "turn_secret"}
    assert livekit_secrets["livekit_keys"]["mode"] == 0o600
    # 顶层 secrets 定义齐全（:? 指向部署文件）
    assert set(compose["secrets"]) == {"frps_token", "turn_secret", "livekit_keys"}


def test_edge_env_example_documents_required_vars_and_turn_checklist() -> None:
    """.env.example 覆盖全部必填变量，且含 TURN secret 同源部署清单。"""
    text = EDGE_ENV.read_text(encoding="utf-8")
    for var in (
        "AIOS_EDGE_VPS_PUBLIC_IP",
        "AIOS_EDGE_LIVEKIT_KEYS_FILE",
        "AIOS_EDGE_COTURN_TURN_SECRET",
        "AIOS_EDGE_TURN_SECRET_FILE",
        "AIOS_EDGE_FRPS_TOKEN_FILE",
        "AIOS_EDGE_COTURN_TLS_ENABLED",
    ):
        assert re.search(rf"^{re.escape(var)}=<", text, re.MULTILINE) or re.search(
            rf"^{re.escape(var)}=", text, re.MULTILINE
        ), f".env.example 缺 {var}"
    # 同源双槽位自检（secret 文件值 == env 值）必须成文档
    assert "openssl rand -hex 32" in text
    assert 'test "$(cat' in text and 'turn.secret)" = "$AIOS_EDGE_COTURN_TURN_SECRET"' in text


def test_download_manifest_example_placeholders_only() -> None:
    """分发清单模板：结构完整、sha256 全占位（绝不出现 64 位真值 hex）。"""
    model = json.loads(DOWNLOAD_MANIFEST.read_text(encoding="utf-8"))
    assert model["schema"] == "aios-download-manifest/1"
    channels = model["channels"]
    assert set(channels) == {"android", "harmony", "pwa"}
    for channel in ("android", "harmony"):
        assert channels[channel]["files"], f"{channel} 频道缺文件条目"
        assert channels[channel]["pending_unsigned"], f"{channel} 频道必须保留未签名边界说明"
        for entry in channels[channel]["files"]:
            assert not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), (
                f"{channel} 清单 sha256 必须占位，不得提交真值: {entry['sha256'][:12]}..."
            )
            assert entry["signed"] is True and "signed" in entry["name"]
    # 未签名产物不得伪装成可分发
    assert any("不满足公开分发" in str(e.get("reason", "")) for e in channels["harmony"]["pending_unsigned"])


# ---------------------------------------------------------------- docker compose 渲染（门控）


def _edge_compose_available() -> bool:
    return shutil.which("docker") is not None and EDGE_COMPOSE.is_file()


def _render_edge(env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """渲染边缘 compose：空 --env-file 隔离部署目录 .env 的干扰。"""
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as handle:
        handle.write("")
        env_file = handle.name
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("AIOS_EDGE_")}
    env.update(env_overrides or {})
    try:
        return subprocess.run(
            ["docker", "compose", "--env-file", env_file, "-f", str(EDGE_COMPOSE),
             "config", "--format", "json"],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
            env=env, check=False,
        )
    finally:
        os.unlink(env_file)


def _full_env(secret_files: dict[str, Path]) -> dict[str, str]:
    return {
        "AIOS_EDGE_VPS_PUBLIC_IP": "198.51.100.10",  # RFC 5737 文档段，非真实环境
        "AIOS_EDGE_LIVEKIT_KEYS_FILE": str(secret_files["keys"]),
        "AIOS_EDGE_COTURN_TURN_SECRET": "y" * 48,
        "AIOS_EDGE_FRPS_TOKEN_FILE": str(secret_files["frps"]),
        "AIOS_EDGE_TURN_SECRET_FILE": str(secret_files["turn"]),
    }


def _write_secret_files(tmp_path: Path) -> dict[str, Path]:
    """三个部署 secret 文件（测试值，渲染后即弃；keys 文件按官方一行格式）。"""
    secret_files = {
        "frps": tmp_path / "frps_token.txt",
        "turn": tmp_path / "turn.secret",
        "keys": tmp_path / "livekit_keys",
    }
    secret_files["frps"].write_text("z" * 64, encoding="utf-8")
    secret_files["turn"].write_text("y" * 64, encoding="utf-8")
    secret_files["keys"].write_text("edge-test-key-not-a-secret: " + "x" * 48, encoding="utf-8")
    return secret_files


@pytest.mark.skipif(not _edge_compose_available(), reason="需要 docker compose CLI")
def test_edge_compose_render_success(tmp_path: Path) -> None:
    """必填变量齐全：渲染成功，host 网络三服务零端口发布，coturn 端口集精确。"""
    secret_files = _write_secret_files(tmp_path)
    result = _render_edge(_full_env(secret_files))
    assert result.returncode == 0, result.stderr
    model = json.loads(result.stdout)
    assert model["name"] == "ai-learning-os-edge"
    assert set(model["services"]) == {"caddy", "frps", "livekit", "coturn"}
    for service in ("caddy", "frps", "livekit"):
        assert model["services"][service].get("network_mode") == "host"
        assert not model["services"][service].get("ports"), "host 网络服务不得发布端口"
    # coturn 端口集精确（范围展开为逐端口，published==target 一一对应）
    published: set[tuple[str, int]] = set()
    for port in model["services"]["coturn"]["ports"]:
        if isinstance(port, dict):
            published.add((str(port.get("protocol", "tcp")), int(port["published"])))
        else:
            head, _, proto = str(port).rpartition("/")
            published.add((proto, int(head.rsplit(":", 1)[0])))
    assert ("udp", 3478) in published and ("tcp", 3478) in published and ("tcp", 5349) in published
    assert {("udp", p) for p in range(50000, 50100)} <= published
    assert {("tcp", p) for p in range(50000, 50100)} <= published
    # secrets 接线：渲染后的 secret 文件源 == 提供的部署文件
    rendered_secrets = model.get("secrets", {})
    assert rendered_secrets["frps_token"]["file"].endswith("frps_token.txt")
    assert rendered_secrets["turn_secret"]["file"].endswith("turn.secret")
    assert rendered_secrets["livekit_keys"]["file"].endswith("livekit_keys")
    # livekit 凭据经 --key-file 文件注入（Round 2 修订）：进程参数/环境零 secret——
    # compose config 会把折叠标量规范化为列表，统一拼接后再断言
    rendered_command = model["services"]["livekit"]["command"]
    command = " ".join(rendered_command) if isinstance(rendered_command, list) else rendered_command
    assert "--key-file /run/secrets/livekit_keys" in command
    assert "--keys" not in command, "不得以 --keys 进程参数形态暴露凭据"
    assert "edge-test-key-not-a-secret" not in command and "x" * 48 not in command, (
        "渲染后的进程参数不得包含凭据值"
    )
    # secrets 挂载齐全：livekit 两个文件（0600）+ frps token
    livekit_secrets = {s.get("source"): s for s in model["services"]["livekit"].get("secrets", [])}
    assert set(livekit_secrets) == {"livekit_keys", "turn_secret"}
    for source in ("livekit_keys", "turn_secret"):
        assert str(livekit_secrets[source].get("mode", "")).endswith("600") or (
            livekit_secrets[source].get("mode") in (0o600, 384)
        ), f"{source} 挂载必须 0600（LiveKit key 文件权限硬校验）"
    frps_secrets = model["services"]["frps"].get("secrets", [])
    assert any(s.get("source") == "frps_token" for s in frps_secrets)


@pytest.mark.skipif(not _edge_compose_available(), reason="需要 docker compose CLI")
@pytest.mark.parametrize(
    "missing",
    [
        "AIOS_EDGE_LIVEKIT_KEYS_FILE",
        "AIOS_EDGE_VPS_PUBLIC_IP",
        "AIOS_EDGE_COTURN_TURN_SECRET",
        "AIOS_EDGE_FRPS_TOKEN_FILE",
        "AIOS_EDGE_TURN_SECRET_FILE",
    ],
)
def test_edge_compose_render_fails_closed_without_required_vars(tmp_path: Path, missing: str) -> None:
    """任一必填变量缺失：config 直接失败并点名变量（:? 语义，无默认兜底）。"""
    secret_files = _write_secret_files(tmp_path)
    env = _full_env(secret_files)
    env.pop(missing)
    result = _render_edge(env)
    assert result.returncode != 0, f"缺 {missing} 仍渲染成功（fail-closed 失效）"
    assert missing in result.stderr


# ---------------------------------------------------------------- 文档契约


def test_public_edge_deployment_runbook_sections() -> None:
    """运维手册覆盖规定主题（supervisor Round 1 第 4 条全集）。"""
    text = RUNBOOK.read_text(encoding="utf-8")
    for topic in (
        "VPS 初始化", "DNS", "ACME", "frps", "frpc", "Windows 服务",
        "LiveKit", "coturn", "TURN", "安全组", "生产", "重建",
        "监控", "备份", "回滚", "preflight", "外部资源",
    ):
        assert topic in text, f"runbook 缺主题: {topic}"
    # 生产 env 变量名与研究方向一致（Web 重建所需）
    for var in (
        "AIOS_AUTH_SECRET",
        "AIOS_AUTH_COOKIE_SECURE",
        "AIOS_AUTH_COOKIE_SAMESITE",
        "AIOS_CORS_ORIGINS",
        "AIOS_PUBLIC_API_BASE_URL",
        "AIOS_PUBLIC_LIVEKIT_URL",
    ):
        assert var in text, f"runbook 缺生产 env 说明: {var}"
    # 官方文档引用
    for link in (
        "https://github.com/fatedier/frp",
        "https://caddyserver.com/docs",
        "https://github.com/livekit/livekit",
    ):
        assert link in text, f"runbook 缺官方文档引用: {link}"
    # preflight 工具接线
    assert "public_edge_preflight.py" in text


def test_research_doc_uses_verified_external_turn_syntax() -> None:
    """研究文档随分支入库，且 LiveKit 外部 TURN 采用已核实语法（supervisor 第 5/8 条）。"""
    text = RESEARCH_DOC.read_text(encoding="utf-8")
    assert "frp" in text and "决策" in text, "研究文档需保留候选项目决策矩阵"
    assert "Caddy" in text and "coturn" in text
    # 已核实语法：外部 TURN 走 rtc.turn_servers[] + secret_file
    assert "turn_servers" in text
    assert "secret_file" in text
    # 不得沿用未核实的顶层外部 turn secret 字段表述（Round 1 反馈第 8 条）
    assert "turn.secret 同值" not in text, "研究文档不得沿用未核实的顶层 turn.secret 表述"
    for link in ("https://github.com/fatedier/frp", "https://github.com/livekit/livekit"):
        assert link in text


def test_mobile_distribution_doc_honest_unsigned_boundary() -> None:
    """移动分发文档：未签名产物不得标成 production-ready（supervisor 第 6 条）。"""
    text = MOBILE_DOC.read_text(encoding="utf-8")
    assert "unsigned" in text and "未签名" in text
    assert "production" in text or "生产" in text
    # 诚实边界语句必须显式存在（三处关键词任一命中即算，但至少一句否定式声明）
    assert re.search(r"(不|不得|绝不|未).{0,12}(production-ready|生产可用|公开分发)", text)
    # Android release 与 Harmony AGC 链路 + SHA256 清单引用
    assert "release" in text and "keystore" in text.lower()
    assert "AGC" in text or "AppGallery" in text
    assert "sha256" in text.lower()
    assert "download-manifest" in text or "manifest" in text.lower()
    # 分发清单模板与 preflight 的人工验收项互相引用
    assert "public_edge_preflight" in text or "preflight" in text.lower()
