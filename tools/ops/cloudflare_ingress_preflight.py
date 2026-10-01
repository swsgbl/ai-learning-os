"""M14-205 Cloudflare ingress 凭据/zone 只读 preflight —— E1 前置安全检查。

适用场景：M14-203 E1（Cloudflare 代理 DNS 前置现有 origin）执行前的
凭据与 zone 可用性验证——确认 API token active、目标 zone 存在且唯一、
SSL 模式可读、DNS 记录面可读，为受控 E1 DNS 变更提供安全前置。本工具
不改变任何云侧状态（只读 GET），不做任何 DNS/生产变更。

Fail-closed 契约（违反任何一条都不放行）：
- 配置只来自显式 --config <path> 或环境变量 AIOS_CLOUDFLARE_CONFIG；
  两者都没有 → blocked/missing-config（exit 2），绝不发明默认凭据路径；
- 配置文件必须是常规文件（lstat 校验：拒绝 symlink/reparse point/
  目录/设备），大小硬顶 8 KiB（先 stat 后限额读取双保险），严格 JSON
  （重复 key 拒绝），schema 固定（schema_version=1 + zone_name +
  api_token，可选 execute_timeout_s/user_agent），未知字段拒绝；
- zone_name 仅接受公网 DNS 名称形态（≥2 个合法 label、总长 ≤253、
  禁 IP 字面量、禁 scheme/userinfo/path、禁 RFC 2606/6761 保留域）；
  api_token 非空但值绝不进入任何输出/日志/异常文本（报告只记
  api_token_present 布尔）；
- 默认/plan 模式零网络：只解析与本地校验配置，绝不构造 Transport、
  绝不发请求（测试以"构造即抛错"的注入点断言）；
- execute 门：必须同时 --execute + --confirm 精确短语
  EXECUTE CLOUDFLARE INGRESS READONLY PREFLIGHT + 可用配置；缺一即
  exit 2 且零网络；
- execute 只做只读 GET，恰好 4 个请求、单次、零重试、固定超时：
  1) GET /user/tokens/verify（要求 result.status=active）；
  2) GET /zones?name=<zone_name>（要求 result 恰 1 条；zone id 只
     用于后续请求路径，绝不进入输出）；
  3) GET /zones/{id}/settings/ssl（记录 ssl_mode：API 值 strict 归一
     显示为 full_strict——M14-204 V5a；未知名记 unknown；读取失败记
     unknown 且 FAIL）；
  4) GET /zones/{id}/dns_records?per_page=100（只统计记录数与
     candidate hostname 是否存在于首页；绝不输出任何 record
     content/IP/target）。zone 查找失败时 3/4 显式记 skipped 且
     FAIL，绝不静默跳过；
- 方法白名单三层：编排层只发 GET、RealTransport 结构上拒绝非 GET、
  测试 FakeTransport 逐请求断言 method/path 形态/query；
- HTTP 错误只给 status 码与 Cloudflare error code 数字分类，绝不回显
  response body / error message；
- 输出 JSON 确定性：无时间戳、无 token、无 zone/account id、无
  record value、无本机绝对路径；请求路径只记 endpoint 标签
  （不含 zone id）；
- RealTransport 走 urllib 默认 opener（跟随执行环境的标准代理 env——
  验收对象是 Cloudflare API 凭据而非本机网络路径，与
  public_edge_preflight 直连契约的差异是有意为之并在此声明）。

Exit codes: 0 = 全部通过（plan 模式 = ready-to-execute）；1 = 任一
检查 FAIL；2 = blocked（配置缺失/不可读/schema 非法/确认缺失/参数
非法——argparse 参数错误沿用其默认码 2）。
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import stat as stat_module
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_BLOCKED = 2

SCHEMA = "aios-cloudflare-ingress-preflight/1"
TOOL_NAME = "cloudflare_ingress_preflight"
CONFIRM_PHRASE = "EXECUTE CLOUDFLARE INGRESS READONLY PREFLIGHT"

#: 配置来源唯一环境变量（--config 优先于它；两者皆无 = missing-config）
CONFIG_ENV_VAR = "AIOS_CLOUDFLARE_CONFIG"

#: 配置文件硬顶（字节）：schema 本身 <1 KiB，8 KiB 已是 generously 上限
MAX_CONFIG_BYTES = 8192
#: execute 响应体读取硬顶（字节）：dns_records 首页 100 条远小于此
MAX_RESPONSE_BYTES = 4 << 20

API_BASE_URL = "https://api.cloudflare.com/client/v4"
DEFAULT_USER_AGENT = f"{TOOL_NAME}/1"
DEFAULT_EXECUTE_TIMEOUT_S = 10.0
MIN_EXECUTE_TIMEOUT_S = 1.0
MAX_EXECUTE_TIMEOUT_S = 60.0

#: config schema：必填字段固定，可选字段白名单；其余未知字段拒绝
REQUIRED_CONFIG_FIELDS = ("schema_version", "zone_name", "api_token")
OPTIONAL_CONFIG_FIELDS = ("execute_timeout_s", "user_agent")
CONFIG_SCHEMA_VERSION = 1
MAX_USER_AGENT_LEN = 200

#: Cloudflare settings/ssl 的 value 枚举（M14-204 V5a：API 值 strict 即
#: dashboard 的 Full (strict)）——白名单外记 unknown
SSL_MODE_DISPLAY = {
    "off": "off",
    "flexible": "flexible",
    "full": "full",
    "strict": "full_strict",
}
SSL_MODE_UNKNOWN = "unknown"

# RFC 2606/6761 保留：占位域不可能是真实 Cloudflare zone（沿
# public_edge_preflight 的裸后缀匹配——裸域与其全部子孙域一并拒绝）。
RESERVED_DOMAIN_SUFFIXES = (
    "example.com",
    "example.net",
    "example.org",
    "example",
    "invalid",
    "localhost",
    "test",
)

#: DNS label：首尾字母数字，中间可含连字符，≤63 字符（RFC 1035）
_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
MAX_DNS_NAME_LEN = 253

#: blocked 原因码（报告只出现码与脱敏 detail，绝不出现路径/token 值）
REASON_MISSING_CONFIG = "missing-config"
REASON_MISSING_CONFIRM = "missing-confirm"
REASON_NOT_REGULAR_FILE = "config-not-regular-file"
REASON_TOO_LARGE = "config-too-large"
REASON_UNREADABLE = "config-unreadable"
REASON_INVALID_JSON = "config-invalid-json"
REASON_DUPLICATE_KEY = "config-duplicate-key"
REASON_SCHEMA_INVALID = "config-schema-invalid"
REASON_ZONE_NAME_INVALID = "zone-name-invalid"

#: load_config/PreflightError 文本的可识别 reason 前缀集合（main 据此
#: 归类 blocked_reasons；无匹配前缀的 schema 错误归 config-schema-invalid）
_KNOWN_REASONS = frozenset(
    {
        REASON_NOT_REGULAR_FILE,
        REASON_TOO_LARGE,
        REASON_UNREADABLE,
        REASON_INVALID_JSON,
        REASON_DUPLICATE_KEY,
        REASON_SCHEMA_INVALID,
        REASON_ZONE_NAME_INVALID,
    }
)

STATUS_PASS = "pass"
STATUS_FAIL = "fail"


class PreflightError(Exception):
    """配置/参数非法（fail-closed，直接进入 blocked 路径，不发任何请求）。

    异常文本只含类别与字段名，绝不包含配置值、token、文件路径或响应体。
    """


# ---------------------------------------------------------------- 名称校验


def validate_public_dns_name(raw: Any, what: str = "zone_name") -> str:
    """公网 DNS 名称校验：非法即抛 PreflightError，返回 canonical 小写值。

    只接受纯 DNS 名称形态（≥2 个合法 label、总长 ≤253）：携带
    scheme/userinfo/path/query、IP 字面量（v4/v6）、RFC 2606/6761 保留
    域（裸后缀及其子孙域）一律拒绝——拿占位域或 IP 做 zone 验证属于
    配置错误，必须在入口拦截且不回显原始值。
    """
    if not isinstance(raw, str) or not raw:
        raise PreflightError(f"{what} 必须是非空字符串")
    if len(raw) > MAX_DNS_NAME_LEN:
        raise PreflightError(f"{what} 超长（>{MAX_DNS_NAME_LEN}）")
    if any(marker in raw for marker in ("://", "/", "?", "#", "@", " ")) or any(
        ch.isspace() for ch in raw
    ):
        raise PreflightError(
            f"{what} 必须是纯 DNS 名称（不得携带 scheme/path/userinfo/query/空白）"
        )
    lowered = raw.lower().rstrip(".")
    if not lowered:
        raise PreflightError(f"{what} 不能仅为根点")
    try:
        ipaddress.ip_address(lowered)
    except ValueError:
        pass  # 非 IP 字面量——继续按 DNS 名称校验
    else:
        raise PreflightError(f"{what} 不得是 IP 字面量")
    labels = lowered.split(".")
    if len(labels) < 2:
        raise PreflightError(f"{what} 至少需要两个 label（zone 必有 TLD）")
    for label in labels:
        if not _DNS_LABEL_RE.match(label):
            raise PreflightError(
                f"{what} 含非法 label（仅允许字母/数字/连字符且首尾为字母数字）"
            )
    for suffix in RESERVED_DOMAIN_SUFFIXES:
        if lowered == suffix or lowered.endswith("." + suffix):
            raise PreflightError(f"{what} 是保留/占位域（RFC 2606/6761），拒绝验证")
    return lowered


# ---------------------------------------------------------------- 配置加载


@dataclass(frozen=True)
class PreflightConfig:
    """校验通过的配置（api_token 只在内存中流转，绝不进入报告）。"""

    zone_name: str
    api_token: str
    execute_timeout_s: float
    user_agent: str


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """json object_pairs_hook：任何对象内重复 key 直接拒绝（严格 JSON）。"""
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise PreflightError(f"配置 JSON 含重复字段: {key}")
        seen.add(key)
    return dict(pairs)


def _is_regular_file(path: str) -> bool:
    """lstat 级常规文件判定：symlink/reparse point/目录/设备一律拒绝。"""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if not stat_module.S_ISREG(st.st_mode):
        return False  # symlink（S_IFLNK）/目录/设备/套接字都非常规文件
    reparse = (
        getattr(st, "st_file_attributes", 0) & stat_module.FILE_ATTRIBUTE_REPARSE_POINT
    )
    return not reparse


def _schema_error(message: str) -> PreflightError:
    """schema 错误统一前缀（main 据前缀归类 blocked reason）。"""
    return PreflightError(f"{REASON_SCHEMA_INVALID}: {message}")


def _validate_schema(payload: Any) -> PreflightConfig:
    """schema 校验：必填齐备、类型正确、未知字段拒绝；失败抛 PreflightError。"""
    if not isinstance(payload, dict):
        raise _schema_error("配置必须是 JSON 对象")
    unknown = sorted(
        set(payload) - set(REQUIRED_CONFIG_FIELDS) - set(OPTIONAL_CONFIG_FIELDS)
    )
    if unknown:
        raise _schema_error(f"配置含未知字段: {unknown}")
    if payload.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise _schema_error(
            f"schema_version 必须是 {CONFIG_SCHEMA_VERSION}（收到 "
            f"{type(payload.get('schema_version')).__name__}）"
        )
    api_token = payload.get("api_token")
    if not isinstance(api_token, str) or not api_token:
        raise _schema_error("api_token 必须是非空字符串")
    if (
        "zone_name" not in payload
    ):  # 缺字段归 schema 错误；形态错误才归 zone-name-invalid
        raise _schema_error(f"缺少必填字段 zone_name（必填: {REQUIRED_CONFIG_FIELDS}）")
    timeout = DEFAULT_EXECUTE_TIMEOUT_S
    if "execute_timeout_s" in payload:
        raw_timeout = payload["execute_timeout_s"]
        if not isinstance(raw_timeout, (int, float)) or isinstance(raw_timeout, bool):
            raise _schema_error("execute_timeout_s 必须是数值")
        timeout = float(raw_timeout)
        if not MIN_EXECUTE_TIMEOUT_S <= timeout <= MAX_EXECUTE_TIMEOUT_S:
            raise _schema_error(
                f"execute_timeout_s 越界（{MIN_EXECUTE_TIMEOUT_S}-{MAX_EXECUTE_TIMEOUT_S}）"
            )
    user_agent = DEFAULT_USER_AGENT
    if "user_agent" in payload:
        user_agent = payload["user_agent"]
        if (
            not isinstance(user_agent, str)
            or not user_agent
            or len(user_agent) > MAX_USER_AGENT_LEN
        ):
            raise _schema_error(f"user_agent 必须是非空字符串（≤{MAX_USER_AGENT_LEN}）")
    try:
        zone_name = validate_public_dns_name(payload.get("zone_name"))
    except PreflightError as cause:
        raise PreflightError(f"{REASON_ZONE_NAME_INVALID}: {cause}") from cause
    return PreflightConfig(
        zone_name=zone_name,
        api_token=api_token,
        execute_timeout_s=timeout,
        user_agent=user_agent,
    )


def load_config(path: str) -> PreflightConfig:
    """读取并校验配置文件；任何失败抛 PreflightError（文本已脱敏）。

    安全序：lstat 常规文件判定（拒绝 symlink/reparse point/目录）→
    stat 大小硬顶 → 限额读取（双保险）→ 严格 JSON（重复 key 拒绝）→
    schema 校验。OSError/解码错误只透出错误类别，绝不回显路径或字节。
    """
    if not _is_regular_file(path):
        raise PreflightError(REASON_NOT_REGULAR_FILE)
    try:
        if os.stat(path).st_size > MAX_CONFIG_BYTES:
            raise PreflightError(REASON_TOO_LARGE)
        with open(path, "rb") as handle:
            raw = handle.read(MAX_CONFIG_BYTES + 1)
    except OSError as cause:
        raise PreflightError(f"{REASON_UNREADABLE}: {type(cause).__name__}") from cause
    if len(raw) > MAX_CONFIG_BYTES:
        raise PreflightError(REASON_TOO_LARGE)
    try:
        payload = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except UnicodeError as cause:
        raise PreflightError(
            f"{REASON_INVALID_JSON}: {type(cause).__name__}"
        ) from cause
    except json.JSONDecodeError as cause:
        # JSONDecodeError 文本只含行/列位置，不含路径与配置值——可透出
        raise PreflightError(f"{REASON_INVALID_JSON}: {cause}") from cause
    except PreflightError as cause:  # object_pairs_hook 抛出的重复 key
        raise PreflightError(f"{REASON_DUPLICATE_KEY}: {cause}") from cause
    return _validate_schema(payload)


# ---------------------------------------------------------------- Transport


@dataclass(frozen=True)
class TransportResponse:
    """Transport 应答：HTTP 状态码 + 已解析的 JSON（不可解析为 None）。

    body 只供编排层消费结论字段，绝不整体进入报告/日志。
    """

    status: int
    body: Any = None


class TransportError(Exception):
    """网络层失败（DNS/连接/超时/TLS）——文本在构造点已脱敏。"""


class Transport(Protocol):
    """请求注入点：execute 编排只依赖此协议，测试注入 FakeTransport。"""

    def request(
        self, method: str, path: str, query: list[tuple[str, str]] | None = None
    ) -> TransportResponse: ...


class RealTransport:
    """真实 Cloudflare API transport：GET-only、单次、固定超时、零重试。

    - method 白名单（第二层）：非 GET 直接拒绝——本工具结构上不存在
      任何写路径；
    - Authorization 头只在构造时拼装一次，异常文本对 URL 做占位替换
      （token 在头不在 URL，URL 占位是额外纵深）；
    - HTTPError 保留状态码并尝试解析 body（供 error code 分类），解析
      失败不影响状态码结论；
    - 2xx + 畸形/不可解码 JSON 同样归一为 body=None（rework round 1：
      绝不以 JSONDecodeError 裸逃逸——编排层按 malformed 分类 FAIL，
      状态码保留）；
    - 走 urllib 默认 opener（跟随标准代理 env；见模块 docstring 声明）。
    """

    def __init__(self, token: str, user_agent: str, timeout_s: float) -> None:
        self._token = token
        self._user_agent = user_agent
        self._timeout_s = timeout_s

    def request(
        self, method: str, path: str, query: list[tuple[str, str]] | None = None
    ) -> TransportResponse:
        if method != "GET":
            raise PreflightError(f"RealTransport 只允许 GET（收到 {method}）")
        url = API_BASE_URL + path + (("?" + urlencode(query)) if query else "")
        request = Request(
            url,
            method="GET",
            headers={
                "Authorization": f"Bearer {self._token}",
                "User-Agent": self._user_agent,
                "Accept": "application/json",
            },
        )
        try:
            with build_opener().open(request, timeout=self._timeout_s) as response:
                status = int(response.status)
                body = _parse_json_body(response.read(MAX_RESPONSE_BYTES))
        except HTTPError as cause:
            try:
                body = _parse_json_body(cause.read(MAX_RESPONSE_BYTES))
            except (OSError, ValueError):
                body = None
            return TransportResponse(status=int(cause.code), body=body)
        except (URLError, OSError, TimeoutError) as cause:
            detail = str(cause).replace(url, "<url>")
            raise TransportError(f"{type(cause).__name__} {detail}") from cause
        return TransportResponse(status=status, body=body)


def _parse_json_body(data: bytes) -> Any:
    """响应体解析：空/不可解码/非 JSON 一律返回 None（fail-closed 归一）。

    rework round 1：HTTP 2xx + 畸形 JSON 此前会以 JSONDecodeError 裸
    逃逸（不在任何 except 契约内）；现在统一归一为 body=None——编排层
    按既有 malformed 分类落 FAIL 检查，HTTP 状态码不丢，零重试零
    额外请求。ValueError 覆盖 json.JSONDecodeError；decode 用 replace
    不抛 UnicodeError（防御性同归一）。
    """
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        return None


def _api_error_codes(body: Any) -> list[int]:
    """提取 Cloudflare 错误码数字列表（只取 code，绝不取 message）。"""
    if not isinstance(body, dict):
        return []
    errors = body.get("errors")
    if not isinstance(errors, list):
        return []
    codes: list[int] = []
    for item in errors:
        if isinstance(item, dict) and isinstance(item.get("code"), int):
            codes.append(item["code"])
    return sorted(set(codes))


# ---------------------------------------------------------------- execute 编排


def _check(
    name: str,
    passed: bool,
    detail: str,
    http_status: int | None = None,
    api_error_codes: list[int] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """统一 check 记录：fail-closed 结论 + 状态码/错误码分类，无响应体。"""
    record: dict[str, Any] = {
        "name": name,
        "status": STATUS_PASS if passed else STATUS_FAIL,
        "detail": detail,
    }
    if http_status is not None:
        record["http_status"] = http_status
    if api_error_codes:
        record["api_error_codes"] = api_error_codes
    if extra:
        record.update(extra)
    return record


def _request_once(
    transport: Transport, path: str, query: list[tuple[str, str]] | None
) -> TransportResponse:
    """单次请求（结构上无循环=零重试）；网络层失败抛 TransportError。"""
    return transport.request("GET", path, query)


def _result_object(body: Any) -> dict[str, Any]:
    """Cloudflare 应答 result 字段：必须是对象，否则空 dict（malformed 归一）。

    rework round 1：返回 None 会让调用方 `.get()` 抛 AttributeError
    （与 RealTransport 畸形 JSON 同族的逃逸路径——HTTPError 空 body
    下原本即潜伏）。恒返回 dict 使编排层零解引用风险，malformed 一律
    走既有 FAIL 分类。
    """
    if isinstance(body, dict) and isinstance(body.get("result"), dict):
        return body["result"]
    return {}


def run_execute_checks(
    config: PreflightConfig,
    transport: Transport,
    candidate_hostname: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """execute 编排：恰好 4 个只读 GET，逐项 fail-closed 判定。

    返回 (checks, requests_made)；requests_made 只含 endpoint 标签
    （zone id 绝不进入）。zone 查找失败时 ssl/dns 显式记 skipped 且
    FAIL——fail-closed 不静默跳过。
    """
    checks: list[dict[str, Any]] = []
    requests: list[str] = []

    # 1) token verify：要求 active
    requests.append("user/tokens/verify")
    resp = _request_once(transport, "/user/tokens/verify", None)
    result = _result_object(resp.body)
    token_active = resp.status == 200 and result.get("status") == "active"
    checks.append(
        _check(
            "token-verify",
            token_active,
            "token status=active"
            if token_active
            else "token 非 active（inactive/无效或读取失败）",
            http_status=resp.status,
            api_error_codes=_api_error_codes(resp.body),
        )
    )

    # 2) zone lookup：要求恰 1 条（zone id 只进后续请求路径，绝不进输出）
    requests.append("zones/lookup")
    resp = _request_once(transport, "/zones", [("name", config.zone_name)])
    zone_id: str | None = None
    zone_found = False
    if resp.status == 200 and isinstance(resp.body, dict):
        zones = resp.body.get("result")
        if isinstance(zones, list):
            zone_found = len(zones) >= 1
            if len(zones) == 1:
                candidate = zones[0].get("id") if isinstance(zones[0], dict) else None
                if isinstance(candidate, str) and candidate:
                    zone_id = candidate
    if zone_id:
        checks.append(
            _check(
                "zone-lookup",
                True,
                "zone 存在且唯一（id 不入报告）",
                http_status=resp.status,
            )
        )
    else:
        detail = (
            "zone 不存在（name 精确过滤后 0 条）"
            if not zone_found
            else "zone 匹配非唯一或响应畸形（拒绝继续依赖 zone id）"
        )
        checks.append(
            _check(
                "zone-lookup",
                False,
                detail,
                http_status=resp.status,
                api_error_codes=_api_error_codes(resp.body),
            )
        )

    # 3) SSL 模式：读取成功记 value（strict 归一 full_strict），失败记 unknown
    requests.append("zones/settings/ssl")
    if zone_id is None:
        checks.append(
            _check("ssl-mode", False, "skipped：zone 查找失败，无 zone id 可用")
        )
    else:
        try:
            resp = _request_once(transport, f"/zones/{zone_id}/settings/ssl", None)
        except TransportError as cause:
            checks.append(_check("ssl-mode", False, f"transport-error: {cause}"))
        else:
            result = _result_object(resp.body)
            raw_mode = result.get("value")  # _result_object 恒 dict（malformed→空）
            ssl_mode = SSL_MODE_DISPLAY.get(
                raw_mode if isinstance(raw_mode, str) else "", SSL_MODE_UNKNOWN
            )
            passed = resp.status == 200 and ssl_mode != SSL_MODE_UNKNOWN
            checks.append(
                _check(
                    "ssl-mode",
                    passed,
                    f"ssl_mode={ssl_mode}",
                    http_status=resp.status,
                    api_error_codes=_api_error_codes(resp.body),
                    extra={"ssl_mode": ssl_mode},
                )
            )

    # 4) DNS 记录面：只统计计数与 candidate hostname 存在性
    requests.append("zones/dns_records")
    if zone_id is None:
        checks.append(
            _check("dns-records", False, "skipped：zone 查找失败，无 zone id 可用")
        )
    else:
        try:
            resp = _request_once(
                transport, f"/zones/{zone_id}/dns_records", [("per_page", "100")]
            )
        except TransportError as cause:
            checks.append(_check("dns-records", False, f"transport-error: {cause}"))
        else:
            checks.append(_dns_records_check(resp, candidate_hostname))
    return checks, requests


def _dns_records_check(
    resp: TransportResponse, candidate_hostname: str | None
) -> dict[str, Any]:
    """DNS 记录检查：只产出计数与 candidate 存在性，绝不输出记录内容。

    per_page=100 只取首页：计数优先取 result_info.total_count（全量），
    candidate 扫描范围仅首页并在 detail 注明（诚实边界）。
    """
    if resp.status != 200 or not isinstance(resp.body, dict):
        return _check(
            "dns-records",
            False,
            "DNS 记录面读取失败",
            http_status=resp.status,
            api_error_codes=_api_error_codes(resp.body),
        )
    records = resp.body.get("result")
    if not isinstance(records, list):
        return _check(
            "dns-records",
            False,
            "DNS 记录应答畸形（result 非数组）",
            http_status=resp.status,
        )
    record_count = len(records)
    info = resp.body.get("result_info")
    if isinstance(info, dict) and isinstance(info.get("total_count"), int):
        record_count = info["total_count"]
    extra: dict[str, Any] = {
        "dns_record_count": record_count,
        "dns_records_scanned": len(records),
    }
    if candidate_hostname is not None:
        present = any(
            isinstance(item, dict) and item.get("name") == candidate_hostname
            for item in records
        )
        extra["candidate_hostname_present"] = present
        detail = (
            f"candidate={candidate_hostname} {'存在于首页' if present else '不存在于首页'}"
            f"；count={record_count}"
        )
    else:
        detail = f"count={record_count}（未指定 candidate）"
    return _check("dns-records", True, detail, http_status=resp.status, extra=extra)


# ---------------------------------------------------------------- CLI


def _candidate_hostname_arg(raw: str) -> str:
    """--hostname 的 argparse type：非法即 exit 2（沿 argparse 默认码）。"""
    try:
        return validate_public_dns_name(raw, "hostname")
    except PreflightError as cause:
        raise argparse.ArgumentTypeError(str(cause)) from cause


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Cloudflare ingress 凭据/zone 只读 preflight（fail-closed、value-free、"
            "默认零网络）：plan 模式只做本地配置校验；execute 需 --execute + "
            f'--confirm "{CONFIRM_PHRASE}"，只发 4 个只读 GET，零重试。'
        ),
    )
    parser.add_argument(
        "--config", help=f"配置 JSON 文件路径（或环境变量 {CONFIG_ENV_VAR}）"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="execute 模式（默认 plan：零网络本地校验）",
    )
    parser.add_argument(
        "--confirm",
        help=f'execute 必配 --confirm "{CONFIRM_PHRASE}"（精确匹配）',
    )
    parser.add_argument(
        "--hostname",
        type=_candidate_hostname_arg,
        help="candidate hostname（可选）：检查 zone 首页 DNS 记录是否存在同名记录",
    )
    parser.add_argument("--output", help="JSON 报告输出路径（原子写；缺省仅 stdout）")
    return parser


def _resolve_config_source(args: argparse.Namespace) -> tuple[str | None, str | None]:
    """配置来源解析：--config 优先于环境变量；返回 (path, source 标签)。"""
    if args.config:
        return args.config, "cli"
    env_value = os.environ.get(CONFIG_ENV_VAR, "").strip()
    if env_value:
        return env_value, "env"
    return None, None


def _write_report_atomic(path: str, report: dict[str, Any]) -> None:
    """原子写报告（temp + os.replace），失败时不清除旧文件。"""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, suffix=".tmp", delete=False
    ) as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temp_name = handle.name
    try:
        os.replace(temp_name, path)
    except OSError:
        try:
            os.unlink(temp_name)
        finally:
            raise


def main(argv: list[str] | None = None, transport_factory: Any = None) -> int:
    """CLI 入口；transport_factory 是测试注入点（生产缺省 RealTransport）。

    计划模式与 execute 门失败路径绝不触碰 transport_factory——测试以
    "调用即抛错"的工厂断言 plan 零 Transport。
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    config_path, config_source = _resolve_config_source(args)
    blocked_reasons: list[str] = []
    blocked_details: list[str] = []
    config: PreflightConfig | None = None
    if args.execute and args.confirm != CONFIRM_PHRASE:
        blocked_reasons.append(REASON_MISSING_CONFIRM)
        blocked_details.append("execute 需精确确认短语（--confirm）")
    if config_path is None:
        blocked_reasons.append(REASON_MISSING_CONFIG)
        blocked_details.append(f"未提供 --config 或 {CONFIG_ENV_VAR}")
    else:
        try:
            config = load_config(config_path)
        except PreflightError as cause:
            reason = str(cause).split(":", 1)[0]
            blocked_reasons.append(
                reason if reason in _KNOWN_REASONS else REASON_SCHEMA_INVALID
            )
            blocked_details.append(str(cause))

    mode = "execute" if args.execute else "plan"
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "tool": TOOL_NAME,
        "mode": mode,
        "config_source": config_source,
        "config_present": config is not None,
        "candidate_hostname": args.hostname,
    }
    if blocked_reasons:
        report.update(
            {
                "status": "blocked",
                "blocked_reasons": blocked_reasons,
                "blocked_details": blocked_details,
                "exit_code": EXIT_BLOCKED,
            }
        )
        _emit(report, args)
        return EXIT_BLOCKED

    assert config is not None  # blocked 路径已 return
    report["zone_name"] = config.zone_name
    report["zone_name_shape"] = "valid-public-dns"
    report["api_token_present"] = True

    if not args.execute:
        report.update({"status": "ready-to-execute", "exit_code": EXIT_OK})
        _emit(report, args)
        return EXIT_OK

    factory = transport_factory or (
        lambda: RealTransport(
            config.api_token, config.user_agent, config.execute_timeout_s
        )
    )
    try:
        checks, requests = run_execute_checks(config, factory(), args.hostname)
    except TransportError as cause:
        report.update(
            {
                "status": "fail",
                "checks": [],
                "requests_made": [],
                "failure_class": "transport-error",
                "failure_detail": str(cause),  # 构造点已脱敏（URL 占位替换）
                "exit_code": EXIT_FAILURE,
            }
        )
        print(f"[{TOOL_NAME}] FAIL: transport-error: {cause}", file=sys.stderr)
        _emit(report, args)
        return EXIT_FAILURE
    passed = all(check["status"] == STATUS_PASS for check in checks)
    report.update(
        {
            "status": STATUS_PASS if passed else "fail",
            "checks": checks,
            "requests_made": requests,
            "exit_code": EXIT_OK if passed else EXIT_FAILURE,
        }
    )
    _emit(report, args)
    return report["exit_code"]


def _emit(report: dict[str, Any], args: argparse.Namespace) -> None:
    """确定性输出：stdout 一行紧凑 JSON + stderr 摘要；--output 原子写。"""
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True)
    print(payload)
    summary = report.get("status", "?")
    print(
        f"[{TOOL_NAME}] {summary} -> exit {report.get('exit_code', '?')}",
        file=sys.stderr,
    )
    if args.output:
        try:
            _write_report_atomic(args.output, report)
        except OSError as cause:
            print(
                f"[{TOOL_NAME}] FAIL: 报告写出失败: {type(cause).__name__}",
                file=sys.stderr,
            )
            raise SystemExit(EXIT_FAILURE) from cause


if __name__ == "__main__":
    sys.exit(main())
