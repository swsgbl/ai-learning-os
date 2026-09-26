#!/usr/bin/env python
"""M14-155 边缘部署操作包工具：对 public_edge_prepare 的渲染产物做
inspect / seal / verify（零第三方依赖；零网络；零服务操作）。

包生命周期：``public_edge_prepare --render`` 产出五个产物 → 本工具
``seal`` 生成 SHA256SUMS 与 DEPLOYMENT_PACKAGE.md（交接操作说明，明确
真实命令由 supervisor 在取得真实资源后执行）→ 交接后 ``verify``
fail-closed 复核（哈希 + 全部结构/占位/安全检查重跑）。

设计纪律（承接 M14-153/154 fail-closed 契约）：
- 目录边界：包目录必须在仓库外、非符号链接、条目恰为预期产物名集
  （inspect/seal 前恰五件；verify 时恰七件），多余条目/符号链接/
  非常规文件一律拒绝；
- 占位不回落：Caddyfile 四站点、frpc serverAddr、.env 的 VPS IP、
  PREFLIGHT 的 turn host 均不得残留 *.example.com/RFC 5737/私网/
  占位形态——解析失败即 fail-closed；
- 安全不变量复检：frps vhost 只绑 loopback + TLS force + token-from-file；
  frpc 家机侧只回连 127.0.0.1 + 无 inline token；.env 的 TURN secret
  必须仍是显式回填标记（绝不落值）；
- 高熵审计：五个产物经 prepare 的同款熵策略复扫（许可 token 从产物
  自身的合法路径值精确推导），任何 secret 形态串在 inspect/seal/verify
  全路径上拒收；报告只含结构事实（域名/端口/IP/文件名），绝不含
  secret 内容或 secret 文件内容；
- 确定性：seal 产物字节确定（同一渲染目录恒产同一 SHA256SUMS 与
  DEPLOYMENT_PACKAGE.md——无时间戳/随机量入文）；
- 本工具不 SSH、不上传、不启停任何服务、不读任何真实 env/secret 文件。

Exit codes: 0 = 所请求动作成功；1 = 检查失败（fail-closed）；
2 = 用法错误（argparse）。
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import tomllib

try:  # 包导入（pytest）与脚本直跑两种形态
    from tools.ops import public_edge_preflight as preflight
    from tools.ops import public_edge_prepare as prepare
except ImportError:  # python tools/ops/public_edge_package.py
    import public_edge_preflight as preflight
    import public_edge_prepare as prepare

TOOL_NAME = "public_edge_package"
REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "aios-public-edge-package/1"

RENDER_ARTIFACTS = ("Caddyfile", "frps.toml", "frpc.windows.toml", ".env", "PREFLIGHT.md")
SEAL_ARTIFACTS = ("DEPLOYMENT_PACKAGE.md", "SHA256SUMS")
EXPECTED_SEALED = frozenset(RENDER_ARTIFACTS + SEAL_ARTIFACTS)
EXPECTED_RENDERED = frozenset(RENDER_ARTIFACTS)

DNS_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
ENV_REQUIRED_KEYS = (
    "AIOS_EDGE_VPS_PUBLIC_IP",
    "AIOS_EDGE_LIVEKIT_KEYS_FILE",
    "AIOS_EDGE_TURN_SECRET_FILE",
    "AIOS_EDGE_FRPS_TOKEN_FILE",
    "AIOS_EDGE_COTURN_TURN_SECRET",
    "AIOS_EDGE_COTURN_TLS_ENABLED",
)

EXIT_OK = 0
EXIT_FAILURE = 1


class PackageError(Exception):
    """包结构/内容校验失败（fail-closed；文本不含 secret）。"""


# ---------------------------------------------------------------- 目录与文件边界


def _check_package_dir(directory: Path, allowed: frozenset[str]) -> None:
    """包目录防线：仓库外、非符号链接、条目恰为允许集、全常规非链接文件。"""
    resolved = directory.resolve()
    if directory.is_symlink():
        raise PackageError("包目录不得是符号链接")
    if resolved == Path(resolved.anchor):
        raise PackageError("包目录不得是文件系统根目录")
    if resolved == REPO_ROOT or str(resolved).startswith(str(REPO_ROOT) + os.sep):
        raise PackageError("包目录不得位于仓库内")
    if not resolved.is_dir():
        raise PackageError("包目录不存在或不是目录")
    names = {p.name for p in resolved.iterdir()}
    if names != allowed:
        missing = sorted(allowed - names)
        extra = sorted(names - allowed)
        raise PackageError(f"包目录条目集不符（缺 {missing}；多 {extra}）")
    for entry in resolved.iterdir():
        if entry.is_symlink() or not entry.is_file():
            raise PackageError(f"包内条目 {entry.name} 必须是常规文件（拒绝符号链接/目录）")


def _read_text(directory: Path, name: str) -> str:
    try:
        return (directory / name).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as cause:
        raise PackageError(f"产物 {name} 不可读/非 UTF-8: {type(cause).__name__}") from cause


# ---------------------------------------------------------------- 产物解析与不变量


def _require_dns_host(host: str, label: str) -> None:
    if not DNS_NAME_RE.fullmatch(host):
        raise PackageError(f"{label} 主机名畸形: {host!r}")
    if host == host.lower().rstrip(".") and host.lower().endswith(".example.com"):
        raise PackageError(f"{label} 残留占位域（example.com）: {host}")
    try:
        ipaddress.ip_address(host)
        raise PackageError(f"{label} 不得是 IP 字面量（需域名）: {host}")
    except ValueError:
        pass


def _require_public_ipv4(value: str, label: str) -> None:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError as cause:
        raise PackageError(f"{label} 不是合法 IPv4: {value!r}") from cause
    reserved_doc = (
        ipaddress.ip_network("192.0.2.0/24"),
        ipaddress.ip_network("198.51.100.0/24"),
        ipaddress.ip_network("203.0.113.0/24"),
    )
    if ip.version != 4 or not ip.is_global or any(ip in net for net in reserved_doc):
        raise PackageError(f"{label} 不是公网 IPv4（私网/文档段/IPv6/占位拒绝）: {value}")


def _parse_env(text: str) -> dict[str, str]:
    """Round 1 缺口 5：重复键拒绝——不同 dotenv 实现的覆盖语义不一致，
    封包前必须确保键唯一。"""
    env: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise PackageError(f".env 存在无 '=' 的行: {line[:20]}...")
        key = key.strip()
        if key in env:
            raise PackageError(f".env 含重复键 {key}（不同 dotenv 的覆盖语义不一致——拒绝）")
        env[key] = value.strip()
    return env


def collect_facts(directory: Path) -> dict[str, Any]:
    """解析五个产物并复检全部安全不变量；返回结构事实（无 secret）。

    任何一项不合格即 PackageError（inspect/seal/verify 共用同一判定）。
    """
    facts: dict[str, Any] = {}
    caddy = _read_text(directory, "Caddyfile")
    sites = re.findall(r"^([a-z0-9.-]+) \{$", caddy, re.MULTILINE)
    if len(sites) != 4:
        raise PackageError(f"Caddyfile 站点数 {len(sites)} != 4")
    if len(set(sites)) != len(sites):
        raise PackageError("Caddyfile 站点主机重复")
    for host in sites:
        _require_dns_host(host, "Caddyfile 站点")
    if "app.example.com" in sites or any(s.endswith(".example.com") for s in sites):
        raise PackageError("Caddyfile 残留占位域")
    if caddy.count("reverse_proxy 127.0.0.1:8080") < 2 or "reverse_proxy 127.0.0.1:7880" not in caddy:
        raise PackageError("Caddyfile 反代目标不完整（frps vhost 8080 ×2 / livekit 7880）")
    facts["caddy_hosts"] = sorted(sites)

    try:
        frps = tomllib.loads(_read_text(directory, "frps.toml"))
    except tomllib.TOMLDecodeError as cause:
        raise PackageError(f"frps.toml 不可解析: {cause}") from cause
    if frps.get("bindPort") != 7000 or frps.get("proxyBindAddr") != "127.0.0.1":
        raise PackageError("frps 端口/绑定不变量破坏（bindPort 7000 / vhost 仅 loopback）")
    if frps.get("vhostHTTPPort") != 8080:
        raise PackageError("frps vhostHTTPPort != 8080")
    if frps.get("transport", {}).get("tls", {}).get("force") is not True:
        raise PackageError("frps transport.tls.force != true")
    auth = frps.get("auth", {})
    if auth.get("tokenSource", {}).get("type") != "file" or "token" in auth:
        raise PackageError("frps 认证不变量破坏（token 必须来自文件且无 inline token）")

    try:
        frpc = tomllib.loads(_read_text(directory, "frpc.windows.toml"))
    except tomllib.TOMLDecodeError as cause:
        raise PackageError(f"frpc.windows.toml 不可解析: {cause}") from cause
    _require_public_ipv4(str(frpc.get("serverAddr", "")), "frpc serverAddr")
    if frpc.get("serverPort") != 7000:
        raise PackageError("frpc serverPort != 7000")
    if frpc.get("transport", {}).get("tls", {}).get("enable") is not True:
        raise PackageError("frpc transport.tls.enable != true")
    frpc_auth = frpc.get("auth", {})
    if frpc_auth.get("tokenSource", {}).get("type") != "file" or "token" in frpc_auth:
        raise PackageError("frpc 认证不变量破坏（token 仅文件引用且无 inline token）")
    token_path = str(frpc_auth.get("tokenSource", {}).get("file", {}).get("path", ""))
    if not token_path:
        raise PackageError("frpc tokenSource 文件路径缺失")
    proxies = frpc.get("proxies", [])
    if not proxies or not all(p.get("type") == "http" and p.get("localIP") == "127.0.0.1" for p in proxies):
        raise PackageError("frpc 代理不变量破坏（家机侧只允许 http + 127.0.0.1 回连）")
    for proxy in proxies:
        for domain in proxy.get("customDomains", []):
            _require_dns_host(domain, "frpc customDomains")
    facts["frpc_server"] = str(frpc["serverAddr"])
    facts["frpc_domains"] = sorted(d for p in proxies for d in p.get("customDomains", []))

    env = _parse_env(_read_text(directory, ".env"))
    missing_keys = [key for key in ENV_REQUIRED_KEYS if key not in env]
    if missing_keys:
        raise PackageError(f".env 缺必填键: {missing_keys}")
    _require_public_ipv4(env["AIOS_EDGE_VPS_PUBLIC_IP"], ".env VPS IP")
    if "<<<" not in env["AIOS_EDGE_COTURN_TURN_SECRET"]:
        raise PackageError(".env 的 TURN secret 必须仍是显式回填标记（绝不落值）")
    for key, value in env.items():
        if "example.com" in value:
            raise PackageError(f".env 键 {key} 残留占位域")
        if key == "AIOS_EDGE_COTURN_TURN_SECRET":
            continue  # 回填标记（<<<...>>>）已由上方专检覆盖，不走通用占位规则
        if value.startswith("<"):
            raise PackageError(f".env 键 {key} 残留占位值")
    facts["vps_public_ip"] = env["AIOS_EDGE_VPS_PUBLIC_IP"]
    facts["vps_secrets_dir"] = env["AIOS_EDGE_TURN_SECRET_FILE"].rsplit("/", 1)[0]

    preflight_doc = _read_text(directory, "PREFLIGHT.md")
    turn_match = re.search(r"--turn-host ([a-z0-9.-]+)", preflight_doc)
    if not turn_match:
        raise PackageError("PREFLIGHT.md 缺 --turn-host")
    _require_dns_host(turn_match.group(1), "PREFLIGHT turn host")
    for item in preflight.MANUAL_CHECKLIST:
        if item["id"] not in preflight_doc:
            raise PackageError(f"PREFLIGHT.md 缺人工清单条目 {item['id']}")
    facts["turn_host"] = turn_match.group(1)

    # 高熵审计（与 prepare 同策略；许可 token 从产物自身合法路径值精确推导。
    # .env 的 TURN 回填标记含 <<< 与中文，天然不构成高熵连跑）
    permitted: list[str] = [token_path, token_path.replace("\\", "/"), facts["vps_secrets_dir"]]
    for key in ("AIOS_EDGE_LIVEKIT_KEYS_FILE", "AIOS_EDGE_TURN_SECRET_FILE", "AIOS_EDGE_FRPS_TOKEN_FILE"):
        permitted.append(env[key])
    for name in RENDER_ARTIFACTS:
        content = _read_text(directory, name)
        for token in prepare._iter_entropy_tokens(content, tuple(permitted)):
            raise PackageError(f"产物 {name} 含高熵串（疑似 secret）: {token[:8]}...")
    return facts


# ---------------------------------------------------------------- seal / verify


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_sums(directory: Path) -> str:
    lines = [f"{_sha256_file(directory / name)}  {name}" for name in sorted(RENDER_ARTIFACTS)]
    return "\n".join(lines) + "\n"


def _build_package_doc(facts: dict[str, Any]) -> str:
    """交接操作说明（确定性：同一 facts 恒产同一字节；真实命令由 supervisor 执行）。"""
    hosts = facts["caddy_hosts"]
    domains = facts["frpc_domains"]
    turn = facts["turn_host"]
    ip = facts["vps_public_ip"]
    secrets_dir = facts["vps_secrets_dir"]
    verify_cmd = "sha256sum -c SHA256SUMS"
    return f"""# AI Learning OS 边缘部署操作包（DEPLOYMENT_PACKAGE）

- schema：{SCHEMA}（由 {TOOL_NAME} seal 生成；字节确定性——重跑恒产同文）
- 包内容：{", ".join(sorted(RENDER_ARTIFACTS))} + SHA256SUMS + 本文件
- 入口事实：Caddy 站点 {hosts}；TURN 主机 {turn}；VPS 公网 IP {ip}
  （结构校验已确认非占位）；frp 回连域名 {domains}

## 诚实边界（不可逾越）

本包**不含任何 secret 值**（.env 的 TURN secret 是显式回填标记；frps/frpc
token 均为文件引用）。本包的生成过程**没有发生任何真实部署**：未 SSH、
未上传、未启动/停止任何服务、未改 DNS。在真实域名/VPS/DNS 控制权/TURN
证书/4G 手机验收落地之前，**不得宣称公网生产上线**。下述真实命令全部由
supervisor 在取得真实资源后执行。

## 0. 包完整性（交接双方）

- 交接后先校验：`{verify_cmd}`（五产物哈希必须逐一 OK）；
- 结构复核：`python tools/ops/public_edge_package.py verify --dir <本包目录>`
  （fail-closed：任何漂移/占位回落/安全不变量破坏即非零退出）。

## 1. VPS 侧（supervisor 执行；详见 docs/PUBLIC_EDGE_DEPLOYMENT.md §3-§7）

1. 上传五个产物到部署目录（如 /opt/aios-edge），Caddyfile/frps.toml/
   livekit 配置按 runbook 就位；
2. secrets 就位（{secrets_dir}/frps_token.txt、turn.secret、livekit_keys；
   全部 600，单值/一行 key: secret——按 runbook §3/§6 生成，绝不外传）；
3. .env 的 TURN secret 回填（VPS 上 `cat {secrets_dir}/turn.secret` 的输出）；
4. `docker compose config`（fail-closed 预检）→ `docker compose up -d`；
5. DNS：五条 A 记录 → VPS 公网 IP；防火墙/安全组按 runbook §7 两层一致。

## 2. Windows 家机侧（supervisor 执行）

1. 官方 release 下载 frpc.exe（与 frps 同版本）；
2. 控制器预检/计划：
   `python tools/ops/frpc_windows_controller.py preflight --frpc-exe <exe> --config <渲染 frpc.windows.toml>`
   （零写入；loopback 回连/token 文件仅路径引用等全部不变量复核）；
3. 审查计划输出后，按确认短语纪律执行 install（见控制器文档；本包不代跑）。

## 3. 验收与回滚

- 公网验收：按包内 PREFLIGHT.md 命令 + 4G/5G 人工清单逐项签认
  （未全绿不宣称生产可用）；
- 回滚：runbook §10（边缘 compose down / 停 frpc / DNS 摘除）。
"""


def _atomic_write(directory: Path, name: str, content: str) -> None:
    fd, temp_name = tempfile.mkstemp(dir=str(directory), prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, directory / name)
    except OSError:
        try:
            os.unlink(temp_name)
        finally:
            raise


def cmd_inspect(directory: Path) -> dict[str, Any]:
    _check_package_dir(directory, EXPECTED_RENDERED)
    facts = collect_facts(directory)
    facts["status"] = "inspect-ok"
    return facts


def cmd_seal(directory: Path) -> dict[str, Any]:
    _check_package_dir(directory, EXPECTED_RENDERED)
    facts = collect_facts(directory)
    _atomic_write(directory, "SHA256SUMS", _build_sums(directory))
    _atomic_write(directory, "DEPLOYMENT_PACKAGE.md", _build_package_doc(facts))
    _check_package_dir(directory, EXPECTED_SEALED)
    facts["status"] = "sealed"
    return facts


def cmd_verify(directory: Path) -> dict[str, Any]:
    _check_package_dir(directory, EXPECTED_SEALED)
    sums = _read_text(directory, "SHA256SUMS")
    entries: dict[str, str] = {}
    for line in sums.splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        if name not in EXPECTED_RENDERED:
            raise PackageError(f"SHA256SUMS 含未知名条目: {name}")
        if name in entries:
            raise PackageError(f"SHA256SUMS 重复条目: {name}")
        entries[name] = digest
    if set(entries) != EXPECTED_RENDERED:
        raise PackageError("SHA256SUMS 未覆盖全部五产物")
    mismatched = [n for n, d in sorted(entries.items()) if _sha256_file(directory / n) != d]
    if mismatched:
        raise PackageError(f"哈希不匹配（内容漂移）: {mismatched}")
    doc = _read_text(directory, "DEPLOYMENT_PACKAGE.md")
    if f"schema：{SCHEMA}" not in doc:
        raise PackageError("DEPLOYMENT_PACKAGE.md 缺 schema 标识")
    facts = collect_facts(directory)
    if _build_sums(directory) != sums:
        raise PackageError("SHA256SUMS 与产物实际内容不一致（重算漂移）")
    if _build_package_doc(facts) != doc:
        raise PackageError("DEPLOYMENT_PACKAGE.md 与产物事实不一致（重算漂移）")
    facts["status"] = "verify-ok"
    return facts


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "边缘部署操作包工具（inspect/seal/verify；fail-closed；零网络零服务操作）。"
            "seal 生成 SHA256SUMS 与交接说明 DEPLOYMENT_PACKAGE.md——真实命令由"
            "supervisor 在取得真实资源后执行。"
        ),
    )
    parser.add_argument("command", choices=["inspect", "seal", "verify"])
    parser.add_argument("--dir", required=True, help="渲染产物目录（必须在仓库外）")
    args = parser.parse_args(argv)
    directory = Path(args.dir).expanduser()
    try:
        if args.command == "inspect":
            facts = cmd_inspect(directory)
        elif args.command == "seal":
            facts = cmd_seal(directory)
        else:
            facts = cmd_verify(directory)
    except PackageError as cause:
        print(f"[{TOOL_NAME}] FAIL: {cause}", file=sys.stderr)
        return EXIT_FAILURE
    for key in ("status", "caddy_hosts", "frpc_server", "frpc_domains", "turn_host", "vps_public_ip"):
        print(f"[{TOOL_NAME}] {key}: {facts.get(key)}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
