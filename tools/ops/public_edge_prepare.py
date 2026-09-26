"""M14-154 边缘部署准备与渲染（fail-closed，绝不部署）。

输入是显式 JSON manifest（结构见 tools/ops/public_edge_prepare.example.json）：
- 只允许 secret **文件引用**（local_secret_files.*），拒绝任何内联 secret 值
  （键名策略 + 高熵值扫描双防线）；
- 校验部署环境（cn-production / hk-beta，对应研究定版的备案/香港两路径）、
  VPS 公网 IPv4、app/api/livekit/download 四个公网 HTTPS origin、TURN 主机、
  ACME 联系邮箱、家机 Web/API loopback 端口、显式输出目录、真实输入确认
  （acknowledge_real_inputs === true）；
- 拒绝：私网/回环/链路本地/保留域端点、重复 origin、畸形主机名、
  origin 携带 path/query/userinfo/非 443 端口（不安全端口组合）、
  IP 字面量 origin/TURN 主机（ACME 需域名）、缺失/弱（<32）/非 UTF-8/
  占位形态的 secret 文件、仓库内输出目录；
- 错误安全契约（承接 M14-153 Round 4/5）：异常文本绝不包含 secret 内容、
  原始敏感 URL、secret 文件路径（读取失败只透出错误类别）；域名/邮箱/
  端口等非 secret 字段可展示以便排障；
- `--check-only`：只校验零写入；`--render`：把 Caddyfile / compose .env /
  frps.toml / frpc.windows.toml / preflight 命令与人工清单渲染进显式输出
  目录（默认必须在仓库外；原子写 + 0600/0644 安全模式）；渲染后自审计：
  任何产物不得包含任一 secret 文件的内容或 32+ hex 高熵串；
- `--dns-check`：可选只读 DNS 核对（五主机解析必须等于 VPS 公网 IP），
  独立于部署的显式检查，不默认运行；
- 绝不打印/持久化 secret 值；绝不启动/停止任何服务；渲染 ≠ 部署。

Exit codes: 0 = 所请求动作成功；1 = 校验/渲染失败（fail-closed）；
2 = 用法错误（argparse）。
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import socket
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

import tomllib

try:  # 包导入（pytest）与脚本直跑两种形态
    from tools.ops import public_edge_preflight as preflight
except ImportError:  # python tools/ops/public_edge_prepare.py
    import public_edge_preflight as preflight

SCHEMA = "aios-public-edge-prepare/1"
TOOL_NAME = "public_edge_prepare"
REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "infra" / "edge"
EXAMPLE_MANIFEST = REPO_ROOT / "tools" / "ops" / "public_edge_prepare.example.json"

ALLOWED_ENVIRONMENTS = ("cn-production", "hk-beta")  # 研究定版：备案生产 / 香港 Beta
ORIGIN_KEYS = ("app", "api", "livekit", "download")
LOCAL_SECRET_KEYS = ("frps_token", "turn_secret", "livekit_keys", "frpc_token")
REQUIRED_TOP_KEYS = (
    "schema", "environment", "acknowledge_real_inputs", "vps_public_ip",
    "origins", "turn_host", "contact_email", "home", "output_dir",
    "vps_secrets_dir", "local_secret_files",
)
MANIFEST_MAX_BYTES = 64 * 1024
SECRET_MIN_LEN = 32
SECRET_MAX_BYTES = 4096  # 超上限显式拒绝（Round 2：不做静默截断）
HOME_PORT_MIN, HOME_PORT_MAX = 1024, 65535
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
# 键名策略：含这些词且不以 _file 结尾的 manifest 键一律视为内联 secret
SECRET_KEY_WORDS = ("token", "secret", "password", "passphrase", "private_key", "api_key")
PLACEHOLDER_MARKERS = ("changeme", "change-me", "<", "placeholder", "todo", "dummy")

EXIT_OK = 0
EXIT_FAILURE = 1

RENDER_FILE_MODE = 0o600
RENDER_DOC_MODE = 0o644


class PrepareError(Exception):
    """manifest/输入非法或渲染受阻（fail-closed；文本已按脱敏契约构造）。"""


# ---------------------------------------------------------------- manifest 装载


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise PrepareError(f"manifest 含重复键: {key}")
        seen.add(key)
    return dict(pairs)


def load_manifest(path: str) -> dict[str, Any]:
    """读取 manifest JSON：UTF-8、体积上限、重复键拒绝；错误不回显路径/字节。"""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MANIFEST_MAX_BYTES + 1)
    except OSError as cause:
        raise PrepareError(f"manifest 不可读: {type(cause).__name__}") from cause
    if len(raw) > MANIFEST_MAX_BYTES:
        raise PrepareError(f"manifest 超过 {MANIFEST_MAX_BYTES} 字节上限")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as cause:
        raise PrepareError(f"manifest 非 UTF-8: {type(cause).__name__}") from cause
    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as cause:
        raise PrepareError(f"manifest 非法 JSON: {cause}") from cause
    if not isinstance(payload, dict):
        raise PrepareError("manifest 必须是 JSON 对象")
    return payload


# ---------------------------------------------------------------- 校验矩阵


def _is_secret_like_key(key: str) -> bool:
    return any(word in key.lower() for word in SECRET_KEY_WORDS) and not key.endswith("_file")


def reject_inline_secrets(manifest: dict[str, Any]) -> None:
    """内联 secret 双防线（Round 2 作用域感知版）。

    1. 键名策略：secret 形态键名（token/secret/password/... 且非 *_file 结尾）
       只允许出现在 local_secret_files **内部**（那是文档化的四个文件引用名）；
       顶层或任意其他嵌套位置出现（包括与 LOCAL_SECRET_KEYS 同名的顶层键，
       如顶层 frps_token="低熵值"）一律拒绝——secret 值唯一合法形态就是
       local_secret_files.* 的文件路径引用。拒绝消息只含键路径不含值。
    2. 高熵值扫描：与渲染审计同一策略（≥32 hex 或 40+ base64 形态全匹配）；
       local_secret_files.* 的值是路径（由 validate_secret_file 另行校验）跳过。
    """
    documented_non_value_keys = {"local_secret_files", "vps_secrets_dir"}

    def _walk(node: Any, path: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if path == () and key in documented_non_value_keys:
                    _walk(value, (key,))
                    continue
                inside_refs = bool(path) and path[0] == "local_secret_files"
                if not inside_refs and _is_secret_like_key(key):
                    dotted = ".".join((*path, key))
                    raise PrepareError(
                        f"manifest 键 {dotted} 疑似内联 secret——secret 值只允许"
                        f" local_secret_files.* 文件引用"
                    )
                _walk(value, (*path, key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                _walk(value, path)

    _walk(manifest, ())
    for key_path, value in _iter_strings(manifest):
        if key_path.split(".")[0].split("[")[0] == "local_secret_files":
            continue
        if re.search(r"[0-9a-fA-F]{32,}", value) or re.fullmatch(r"[A-Za-z0-9+/_=-]{40,}", value):
            raise PrepareError(
                f"manifest 值（键 {key_path}）呈高熵 secret 形态——真实凭据只进 secret 文件"
            )


def _iter_strings(node: Any, prefix: str = "") -> list[tuple[str, str]]:
    if isinstance(node, dict):
        out: list[tuple[str, str]] = []
        for key, value in node.items():
            out.extend(_iter_strings(value, f"{prefix}.{key}" if prefix else key))
        return out
    if isinstance(node, list):
        out = []
        for index, value in enumerate(node):
            out.extend(_iter_strings(value, f"{prefix}[{index}]"))
        return out
    if isinstance(node, str):
        return [(prefix, node)]
    return []


def validate_environment(value: Any) -> str:
    if value not in ALLOWED_ENVIRONMENTS:
        allowed = " / ".join(ALLOWED_ENVIRONMENTS)
        raise PrepareError(f"environment 必须是 {allowed}（收到 {value!r}）")
    return str(value)


def validate_vps_public_ip(value: Any) -> str:
    """VPS 公网 IPv4：全局可路由、拒绝 RFC5737 文档段/CGNAT/私网/回环。"""
    if not isinstance(value, str):
        raise PrepareError("vps_public_ip 必须是 IPv4 字符串")
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError as cause:
        raise PrepareError(f"vps_public_ip 非法 IPv4: {cause}") from cause
    if ip.version != 4:
        raise PrepareError("vps_public_ip 必须是 IPv4（本模板生产路径不支持 IPv6）")
    reserved_doc = (
        ipaddress.ip_network("192.0.2.0/24"),
        ipaddress.ip_network("198.51.100.0/24"),
        ipaddress.ip_network("203.0.113.0/24"),
    )
    if not ip.is_global or any(ip in net for net in reserved_doc):
        raise PrepareError(
            "vps_public_ip 不是公网地址（私网/回环/链路本地/CGNAT/文档段均拒绝）"
        )
    return str(ip)


def _require_dns_name(host: str, label: str) -> None:
    """常规域名要求：拒绝 IP 字面量（v4/v6——数字段也能过主机名正则）与畸形主机名。"""
    try:
        ipaddress.ip_address(host)
        raise PrepareError(f"{label} 必须是常规域名（拒绝 IP 字面量；ACME/证书 CN 需域名）")
    except ValueError:
        pass  # 不是 IP——继续形态检查
    if not re.fullmatch(
        r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+", host
    ):
        raise PrepareError(f"{label} 主机名畸形（拒绝非常规字符/单段名）")


def validate_origins(origins: Any) -> dict[str, preflight.Endpoint]:
    """四个公网 HTTPS origin：复用 preflight 的 origin-only 解析 + 域名/去重/端口约束。"""
    if not isinstance(origins, dict) or set(origins) != set(ORIGIN_KEYS):
        raise PrepareError(f"origins 必须恰好含键 {list(ORIGIN_KEYS)}")
    endpoints: dict[str, preflight.Endpoint] = {}
    for key in ORIGIN_KEYS:
        raw = origins[key]
        if not isinstance(raw, str):
            raise PrepareError(f"origins.{key} 必须是 https:// 字符串")
        try:
            endpoint = preflight.parse_public_https_url(raw)
        except preflight.PreflightError as cause:
            raise PrepareError(f"origins.{key} 非法: {cause}") from cause
        if endpoint.port != 443:
            raise PrepareError(
                f"origins.{key} 端口 {endpoint.port} 不安全（边缘入口统一 Caddy 443）"
            )
        _require_dns_name(endpoint.host, f"origins.{key}")
        endpoints[key] = endpoint
    hosts = [endpoints[key].host for key in ORIGIN_KEYS]
    duplicated = {host for host in hosts if hosts.count(host) > 1}
    if duplicated:
        raise PrepareError(f"origin 主机重复（每个服务必须独立主机）: {sorted(duplicated)}")
    return endpoints


def validate_turn_host(value: Any) -> str:
    if not isinstance(value, str):
        raise PrepareError("turn_host 必须是主机名字符串")
    try:
        host = preflight.parse_public_turn_host(value)
    except preflight.PreflightError as cause:
        raise PrepareError(f"turn_host 非法: {cause}") from cause
    _require_dns_name(host, "turn_host")
    return host


def validate_contact_email(value: Any) -> str:
    if not isinstance(value, str) or not EMAIL_RE.fullmatch(value.strip()) or any(ch.isspace() for ch in value):
        raise PrepareError("contact_email 必须是合法邮箱（ACME 到期通知用，无空白）")
    return value.strip()


def validate_home_ports(home: Any) -> tuple[int, int]:
    if not isinstance(home, dict) or set(home) != {"web_port", "api_port"}:
        raise PrepareError("home 必须恰好含 web_port/api_port")
    ports: list[int] = []
    for key in ("web_port", "api_port"):
        value = home[key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise PrepareError(f"home.{key} 必须是整数")
        if not HOME_PORT_MIN <= value <= HOME_PORT_MAX:
            raise PrepareError(f"home.{key}={value} 越界（{HOME_PORT_MIN}-{HOME_PORT_MAX}）")
        ports.append(value)
    if ports[0] == ports[1]:
        raise PrepareError("home.web_port 与 api_port 不得相同（家机两服务需独立端口）")
    return ports[0], ports[1]


def validate_output_dir(value: Any) -> Path:
    """显式输出目录：拒绝仓库内路径与文件系统根（渲染产物绝不污染仓库/根）。"""
    if not isinstance(value, str) or not value.strip():
        raise PrepareError("output_dir 必须显式提供（渲染产物目录）")
    resolved = Path(value.strip()).expanduser().resolve()
    if resolved == Path(resolved.anchor):
        raise PrepareError("output_dir 不得是文件系统根目录")
    try:
        resolved.relative_to(REPO_ROOT)
    except ValueError:
        return resolved  # 仓库外——通过
    raise PrepareError("output_dir 不得位于仓库内（渲染产物必须落在仓库外）")


# Round 2 路径注入防线：本地（Windows 家机）与 VPS（POSIX）路径分别限定字符集
# ——引号/$/#/反引号/控制字符等会破坏 TOML 字符串、注入 .env 行或注释掉指令；
# `..` 段一律拒绝。盘符冒号只允许出现在本地路径首位。
_LOCAL_PATH_CHARS = re.compile(r"^[A-Za-z]:[\\/][A-Za-z0-9 ._:\\/-]*$")
_POSIX_PATH_CHARS = re.compile(r"^/[A-Za-z0-9._/-]*$")


def _reject_dotdot(segments: list[str], label: str) -> None:
    if any(segment == ".." for segment in segments):
        raise PrepareError(f"{label} 路径不得含 .. 段")


def validate_vps_secrets_dir(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("/") or any(ch.isspace() for ch in value):
        raise PrepareError("vps_secrets_dir 必须是 VPS 侧绝对路径（无空白），如 /opt/aios-edge/secrets")
    if not _POSIX_PATH_CHARS.fullmatch(value):
        raise PrepareError(
            "vps_secrets_dir 含不安全字符（只允许字母数字 . _ - /；"
            "引号/$/#/反引号/控制字符拒绝——防 .env/文档注入）"
        )
    _reject_dotdot(value.split("/"), "vps_secrets_dir")
    return value.rstrip("/")


def validate_secret_file(reference: Any, name: str) -> str:
    """secret 文件引用：路径形态/存在/常规文件/UTF-8/长度上下限/占位形态。

    Round 2：本地路径限定安全字符集（引号/$/#/反引号/控制字符/.. 段拒绝
    ——该值会被替换进 frpc TOML 引号字符串）；文件超过支持上限（4096 字节）
    显式拒绝而非静默截断。错误绝不回显路径与字节。
    """
    if not isinstance(reference, str) or not reference.strip() or any(ch.isspace() for ch in reference):
        raise PrepareError(f"local_secret_files.{name} 必须是无空白文件路径")
    if "://" in reference:
        raise PrepareError(f"local_secret_files.{name} 必须是本地文件路径（拒绝 URL）")
    if not _LOCAL_PATH_CHARS.fullmatch(reference):
        raise PrepareError(
            f"local_secret_files.{name} 路径含不安全字符（只允许盘符冒号、斜杠、"
            f"字母数字、空格、. _ -；引号/$/#/反引号/控制字符拒绝）"
        )
    without_drive = reference[2:] if re.match(r"^[A-Za-z]:", reference) else reference
    _reject_dotdot(re.split(r"[\\/]+", without_drive), f"local_secret_files.{name}")
    try:
        path = Path(reference).expanduser().resolve()
        if path.is_symlink() or not path.is_file():
            raise OSError("not a regular file")
        if os.name == "posix":  # POSIX 权限语义（Windows ACL 不等价，跳过该项）
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                raise PrepareError(
                    f"local_secret_files.{name} 权限过宽（others/group 可读；要求 600 语义）"
                )
        with open(path, "rb") as handle:
            raw = handle.read(SECRET_MAX_BYTES + 1)
        if len(raw) > SECRET_MAX_BYTES:
            raise PrepareError(
                f"local_secret_files.{name} 超过 {SECRET_MAX_BYTES} 字节支持上限"
                f"（拒绝静默截断；请核对是否指向了正确文件）"
            )
    except OSError as cause:
        raise PrepareError(f"local_secret_files.{name} 不可读: {type(cause).__name__}") from cause
    try:
        content = raw.decode("utf-8")
    except UnicodeError as cause:
        raise PrepareError(f"local_secret_files.{name} 非 UTF-8: {type(cause).__name__}") from cause
    stripped = content.strip()
    if len(stripped) < SECRET_MIN_LEN:
        raise PrepareError(
            f"local_secret_files.{name} 内容过短（<{SECRET_MIN_LEN} 字符，弱 secret 拒绝）"
        )
    lowered = stripped.lower()
    if any(marker in lowered for marker in PLACEHOLDER_MARKERS):
        raise PrepareError(f"local_secret_files.{name} 内容呈占位形态（拒绝假 secret）")
    if "\n" in stripped:
        raise PrepareError(
            f"local_secret_files.{name} 必须是单值文件（livekit_keys 除外也只允许一行）"
        )
    return stripped


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """全量校验；返回规范化视图（含 secret 文件内容仅驻内存，绝不写入产物）。"""
    missing = [key for key in REQUIRED_TOP_KEYS if key not in manifest]
    if missing:
        raise PrepareError(f"manifest 缺必填键: {missing}")
    unknown = sorted(set(manifest) - set(REQUIRED_TOP_KEYS) - {"notes"})
    if unknown:
        raise PrepareError(
            f"未知顶层键 {unknown}（schema 只允许文档化字段与可选 notes；"
            f"内联 secret 一律拒绝）"
        )
    if manifest["schema"] != SCHEMA:
        raise PrepareError(f"schema 必须是 {SCHEMA}")
    if manifest.get("acknowledge_real_inputs") is not True:
        raise PrepareError(
            "acknowledge_real_inputs 必须为 true——确认清单内是真实部署输入"
            "（占位值不得进入渲染）"
        )
    reject_inline_secrets(manifest)
    local = manifest["local_secret_files"]
    if not isinstance(local, dict) or set(local) != set(LOCAL_SECRET_KEYS):
        raise PrepareError(f"local_secret_files 必须恰好含键 {list(LOCAL_SECRET_KEYS)}")
    secrets = {name: validate_secret_file(local[name], name) for name in LOCAL_SECRET_KEYS}
    return {
        "environment": validate_environment(manifest["environment"]),
        "vps_public_ip": validate_vps_public_ip(manifest["vps_public_ip"]),
        "endpoints": validate_origins(manifest["origins"]),
        "turn_host": validate_turn_host(manifest["turn_host"]),
        "contact_email": validate_contact_email(manifest["contact_email"]),
        "home_web_port": validate_home_ports(manifest["home"])[0],
        "home_api_port": validate_home_ports(manifest["home"])[1],
        "output_dir": validate_output_dir(manifest["output_dir"]),
        "vps_secrets_dir": validate_vps_secrets_dir(manifest["vps_secrets_dir"]),
        "local_secret_paths": {name: str(Path(local[name]).expanduser().resolve()) for name in LOCAL_SECRET_KEYS},
        "secret_values": secrets,  # 仅驻内存供自审计，绝不落盘
    }


# ---------------------------------------------------------------- 可选 DNS 只读检查


def check_dns(view: dict[str, Any]) -> list[dict[str, str]]:
    """显式只读 DNS 核对：五主机必须解析到 VPS 公网 IP。不修改任何状态。"""
    vps_ip = view["vps_public_ip"]
    hosts = [view["endpoints"][key].host for key in ORIGIN_KEYS] + [view["turn_host"]]
    results: list[dict[str, str]] = []
    for host in hosts:
        try:
            infos = socket.getaddrinfo(host, None, socket.AF_INET)
            addresses = sorted({info[4][0] for info in infos})
        except socket.gaierror as cause:
            results.append({"host": host, "status": "fail", "detail": f"解析失败: {cause}"})
            continue
        if addresses != [vps_ip]:
            results.append(
                {"host": host, "status": "fail", "detail": f"解析 {addresses} != VPS {vps_ip}"}
            )
        else:
            results.append({"host": host, "status": "pass", "detail": vps_ip})
    return results


# ---------------------------------------------------------------- 渲染


def _substitute(template: str, replacements: dict[str, str]) -> str:
    text = template
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def render_artifacts(view: dict[str, Any]) -> dict[str, tuple[str, int]]:
    """确定性渲染：同一 manifest 恒产出同一字节（无时间戳/随机量入文）。"""
    endpoints = view["endpoints"]
    caddy = _substitute(
        (TEMPLATE_DIR / "Caddyfile.example").read_text(encoding="utf-8"),
        {
            "app.example.com": endpoints["app"].host,
            "api.example.com": endpoints["api"].host,
            "livekit.example.com": endpoints["livekit"].host,
            "download.example.com": endpoints["download"].host,
            "admin@example.com": view["contact_email"],
        },
    )
    frps = (TEMPLATE_DIR / "frps.toml.example").read_text(encoding="utf-8")
    frpc = _substitute(
        (TEMPLATE_DIR / "frpc.windows.toml.example").read_text(encoding="utf-8"),
        {
            "<VPS 公网 IPv4>": view["vps_public_ip"],
            "D:/AI Learning OS/secrets/frpc_token.txt": view["local_secret_paths"]["frpc_token"].replace("\\", "/"),
            "app.example.com": endpoints["app"].host,
            "api.example.com": endpoints["api"].host,
        },
    )
    vps_secrets = view["vps_secrets_dir"]
    # .env 不落任何 secret 值：TURN secret 是 VPS 侧显式回填项（同源双槽位清单）
    env = (
        f"# 由 {TOOL_NAME} 渲染（schema {SCHEMA}；environment={view['environment']}）\n"
        f"# 值来自操作者 manifest；secret 值一律不落本文件——按 runbook §6 回填。\n"
        f"AIOS_EDGE_VPS_PUBLIC_IP={view['vps_public_ip']}\n"
        f"AIOS_EDGE_LIVEKIT_KEYS_FILE={vps_secrets}/livekit_keys\n"
        f"AIOS_EDGE_TURN_SECRET_FILE={vps_secrets}/turn.secret\n"
        f"AIOS_EDGE_FRPS_TOKEN_FILE={vps_secrets}/frps_token.txt\n"
        f"AIOS_EDGE_COTURN_TURN_SECRET=<<<在 VPS 上执行: cat {vps_secrets}/turn.secret 的输出>>>\n"
        f"AIOS_EDGE_COTURN_TLS_ENABLED=false\n"
    )
    origins_flags = "\n".join(
        f"  --{flag}-url {endpoints[key].url}"
        for flag, key in (("app", "app"), ("api", "api"), ("livekit", "livekit"))
    )
    checklist_items = "\n".join(
        f"- [ ] {item['id']}: {item['title']}——{item['detail']}" for item in preflight.MANUAL_CHECKLIST
    )
    # Round 2 自保障：frpc 替换后的 TOML 必须仍可解析（路径字符集防线之上的
    # 最后一道——任何残留注入形态在此中止渲染）
    tomllib.loads(frpc)
    preflight_doc = (
        f"# 公网验收 preflight（由 {TOOL_NAME} 渲染；命令不含 secret）\n\n"
        f"```bash\n"
        f"python tools/ops/public_edge_preflight.py \\\n"
        f"{origins_flags} \\\n"
        f"  --turn-host {view['turn_host']} \\\n"
        f"  --livekit-token-file <token 文件> \\\n"
        f"  --login-credentials-file <凭据 JSON> \\\n"
        f"  --mobile-attested-file <签认 JSON> \\\n"
        f"  --output preflight-report.json\n"
        f"```\n\n"
        f"exit 0 = 自动+人工全过；1 = FAIL；3 = 人工清单待签认。\n\n"
        f"## 人工 4G/5G 清单（逐项签认后才可宣称公网生产可用）\n\n{checklist_items}\n"
    )
    return {
        "Caddyfile": (caddy, RENDER_DOC_MODE),
        "frps.toml": (frps, RENDER_DOC_MODE),
        "frpc.windows.toml": (frpc, RENDER_FILE_MODE),
        "dot-env": (env, RENDER_FILE_MODE),  # 写出文件名 .env（键名避开点号歧义）
        "PREFLIGHT.md": (preflight_doc, RENDER_DOC_MODE),
    }


FILE_NAME_MAP = {"dot-env": ".env"}


def _atomic_write(directory: Path, name: str, content: str, mode: int) -> None:
    """原子写（同目录 temp + os.replace + 显式 chmod）；失败不留半成品。"""
    target = directory / FILE_NAME_MAP.get(name, name)
    fd, temp_name = tempfile.mkstemp(dir=str(directory), prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.chmod(temp_name, mode)
        os.replace(temp_name, target)
    except OSError:
        try:
            os.unlink(temp_name)
        finally:
            raise


# 渲染自审计的高熵豁免（文档化）：静态豁免当前为空（五个模板均无 ≥32 hex
# 或 40+ base64 形态的合法长串需求）。动态豁免 = 本视图合法替换进产物的
# 长 token（secret 文件路径、VPS secret 目录）——render_to_directory 传入；
# 它们是操作者声明的部署路径，不是 secret 材料。未来模板确需静态豁免时
# 在 AUDIT_ENTROPY_ALLOWLIST 登记并注明理由。
AUDIT_ENTROPY_ALLOWLIST: tuple[str, ...] = ()


def _iter_entropy_tokens(text: str):
    """与 manifest 高熵策略同款：≥32 hex 或 40+ base64 形态串。

    base64 类的路径形态豁免：token 含 `/` 或 `\\` 视为路径（.env 的
    `VAR=/opt/.../name` 行、frpc 的 token 文件路径都是合法长串）——
    hex 类不豁免（hex secret 从不含斜杠，路径也不会含 32+ 连续 hex）；
    真正泄漏的 secret 内容另由 audit_rendered 的精确匹配兜底。
    """
    for token in re.findall(r"[0-9a-fA-F]{32,}|[A-Za-z0-9+/_=-]{40,}", text):
        if token in AUDIT_ENTROPY_ALLOWLIST:
            continue
        if "/" in token or "\\" in token:
            continue  # 路径形态（见 docstring）
        yield token


def audit_rendered(
    artifacts: dict[str, tuple[str, int]],
    secret_values: dict[str, str],
    permitted_long_tokens: tuple[str, ...] = (),
) -> None:
    """渲染自审计（fail-closed）：

    1. 精确匹配：产物不得含任一 secret 文件内容；
    2. 高熵策略（Round 2 对齐 manifest）：产物不得含 ≥32 hex 或 40+ base64
       形态串（精确匹配漏掉的 base64 长 secret 由这条兜住）。豁免仅两类：
       静态 AUDIT_ENTROPY_ALLOWLIST（模板合法长串，登记+理由）与本视图
       合法替换的长 token（secret 路径/VPS 目录——permitted_long_tokens）。
    """
    for name, (content, _mode) in artifacts.items():
        for secret_name, value in secret_values.items():
            if value and value in content:
                raise PrepareError(f"渲染产物 {name} 泄漏 secret 内容（{secret_name}）——中止写出")
        for token in _iter_entropy_tokens(content):
            if any(token in permitted for permitted in permitted_long_tokens):
                continue
            raise PrepareError(f"渲染产物 {name} 含高熵串（疑似 secret）: {token[:8]}...")


EXPECTED_ARTIFACT_NAMES = frozenset({"Caddyfile", "frps.toml", "frpc.windows.toml", ".env", "PREFLIGHT.md"})


def validate_render_target(directory: Path) -> None:
    """Round 2 渲染目标防线（mkdir/写入之前执行）：

    拒绝文件系统根与仓库根；目录若已存在：必须是真目录（非符号链接）、
    只含恰好预期的五个产物名（常规文件、非符号链接）——多余条目拒绝，
    防止盲写污染无关目录；对同名产物保持幂等覆写。
    """
    resolved = directory.resolve()
    if directory.is_symlink():  # 先于 resolve 检查原路径（resolve 会跟随链接）
        raise PrepareError("输出目录不得是符号链接")
    if resolved == Path(resolved.anchor):
        raise PrepareError("输出目录不得是文件系统根目录")
    if resolved == REPO_ROOT or str(resolved).startswith(str(REPO_ROOT) + os.sep):
        raise PrepareError("输出目录不得位于仓库内")
    if not resolved.exists():
        return  # 不存在——由渲染创建（父目录链由操作者显式给出）
    if not resolved.is_dir():
        raise PrepareError("输出路径存在但不是目录")
    unexpected = sorted(p.name for p in resolved.iterdir() if p.name not in EXPECTED_ARTIFACT_NAMES)
    if unexpected:
        raise PrepareError(
            f"输出目录含非渲染产物条目 {unexpected}（拒绝写入无关目录；"
            f"只允许恰好的五个产物名幂等覆写）"
        )
    for entry in resolved.iterdir():
        if entry.is_symlink() or not entry.is_file():
            raise PrepareError(f"渲染产物 {entry.name} 必须是常规文件（拒绝符号链接）")


def render_to_directory(view: dict[str, Any]) -> list[str]:
    """渲染并原子写出到 output_dir（目标先过 validate_render_target 防线）。"""
    directory: Path = view["output_dir"]
    validate_render_target(directory)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    artifacts = render_artifacts(view)
    # 本视图合法替换的长 token（家机 secret 路径/VPS secret 目录的两种斜杠
    # 形态）作为高熵审计的动态豁免——它们是路径不是 secret 材料
    frpc_path = view["local_secret_paths"]["frpc_token"]
    permitted = (frpc_path, frpc_path.replace("\\", "/"), view["vps_secrets_dir"])
    audit_rendered(artifacts, view["secret_values"], permitted_long_tokens=permitted)
    written: list[str] = []
    for name, (content, mode) in artifacts.items():
        _atomic_write(directory, name, content, mode)
        written.append(FILE_NAME_MAP.get(name, name))
    return written


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "边缘部署准备/渲染（fail-closed，不部署）：显式 JSON manifest 校验"
            "（--check-only）与模板渲染（--render，产物只落仓库外显式输出目录）；"
            "可选 --dns-check 只读核对 DNS。"
        ),
    )
    parser.add_argument("--manifest", required=True, help="manifest JSON 路径（结构见 public_edge_prepare.example.json）")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--check-only", action="store_true", help="只校验，零写入")
    actions.add_argument("--render", action="store_true", help="校验并渲染到 output_dir")
    parser.add_argument("--dns-check", action="store_true",
                        help="附加只读 DNS 核对（五主机解析须等于 VPS 公网 IP）")
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
        view = validate_manifest(manifest)
        if args.dns_check:
            for item in check_dns(view):
                marker = "PASS" if item["status"] == "pass" else "FAIL"
                print(f"[{TOOL_NAME}] dns {marker} {item['host']}: {item['detail']}")
                if item["status"] != "pass":
                    return EXIT_FAILURE
    except PrepareError as cause:
        print(f"[{TOOL_NAME}] FAIL: {cause}", file=sys.stderr)
        return EXIT_FAILURE

    if args.check_only:
        print(f"[{TOOL_NAME}] CHECK-ONLY PASS: manifest 校验通过（零写入）")
        return EXIT_OK

    try:
        written = render_to_directory(view)
    except (PrepareError, OSError) as cause:
        print(f"[{TOOL_NAME}] FAIL: 渲染失败: {type(cause).__name__} {cause}", file=sys.stderr)
        return EXIT_FAILURE
    for name in written:
        print(f"[{TOOL_NAME}] rendered: {name}")
    print(f"[{TOOL_NAME}] RENDER OK（产物含 secret 引用不含 secret 值；渲染 ≠ 部署）")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
