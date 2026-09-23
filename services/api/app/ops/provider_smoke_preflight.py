"""M14-112 provider-smoke 前置只读预检与失败归因面。

定位：provider-smoke 门（release-readiness）当前 blocked 的恢复决策前置面。
M14-104/M14-98 实证三步冒烟的失败大多**先于冒烟脚本本身**即可判定——
SearXNG 在线但上游引擎全数不可达、Ollama 端点进程缺席、Windows 注册表
系统代理劫持 loopback 健康探测——运维只能先跑冒烟再从失败 stdout 反推。
本工具在**不运行任何冒烟、不触碰任何服务**的前提下，对三个门 provider
（voice/search/llm）做有界只读 HTTP 预检并给出固定词汇的失败归因与
外部动作建议，使「下一次生产恢复决策」可检视：

- search/SearXNG：配置 base URL + 只读 ``/search?format=json`` 探测
  （与 CloudWebProvider 同 JSON 契约），响应形状就绪判定 + 上游引擎失败
  归因（``unresponsive_engines``，端点自报才透出）；
- local voice（voice_mode=local 拓扑）：ASR/TTS base URL + listener 就绪
  （与 tools/voice/smoke_local_voice.py 同 ``/health`` 契约）有界只读探测；
  cloud/hybrid 拓扑**不检查凭据**——语音槽位如实报告
  ``not_probed/external_credentials_not_inspected``（凭据由运维在冒烟
  调用时外部注入，本工具不读 secret、不探测外部端点）；
- LLM/Ollama-compatible 本地网关：base URL + 只读模型驻留探测
  （``/api/ps``——服务根上的既有健康契约，M14-104 复核用过），清晰区分
  端点缺席 / 模型缺席 / 超时 / HTTP 失败 / 响应畸形五类失败。

代理边界（M14-104 attempt1/M14-106/M14-100 教训）：loopback URL 的探测
client 恒 ``trust_env=False``（Windows 注册表/环境系统代理不得劫持本机
探测造成假失败）；非 loopback URL 保持 httpx 默认（尊重部署代理）。同时
显式探测环境/注册表的 ambient 代理压力是否覆盖 loopback host——只输出
布尔观测（绝不输出代理 URL——可能内嵌代理凭据），loopback 探测本身
不受其影响。

安全与诚实护栏（本模块全部行为面）：

- **只读**：零子进程、零文件写入、零服务生命周期变更——不启动/停止/
  重启 CC Switch/代理/Docker/Ollama/FunASR/CosyVoice/SearXNG/模拟器/
  设备/计划任务或任何用户进程；探测全部为 GET + 有界超时（10s，与
  既有 smoke 健康探测同契约）+ 不追 redirects；
- **零 secret**：不读取任何 API key/凭据环境槽位；环境访问仅限
  ``urllib.request.getproxies``/``proxy_bypass`` 的**布尔**压力判定
  （代理值可能内嵌凭据，绝不进入输出）；endpoint URL 含 userinfo
  （user-only 与 user:password 两形态）、query 或 fragment 一律
  **fail-closed 拒绝**（malformed_url，先于一切探测——凭据绝不进入探测
  请求，也不进入报告；supervisor 修正 Round 1）；远端自报字符串（引擎
  名/错误类/驻留模型名）进入报告前逐项剔除控制字符并截断到 128 码点
  （supervisor 修正 Round 2）——endpoint/model 是非敏感配置值
  （M14-104 口径），合法校验后的 endpoint 如实呈现；
- **不产生证据**：stdout-only（``--json`` 纯 JSON / 人类摘要），不写
  provider-smoke.json、不改 release-readiness 证据、绝不宣称
  provider-smoke 已通过——预检 pass 只代表**前置条件可观测且就绪**，
  不是 provider-smoke 结论；``production_ready`` 恒 false，
  ``release_readiness_evidence`` 恒 false（报告自声明字段，测试锁定）；
- **固定词汇**：status/reason/recommendation 全部闭集枚举（见
  :data:`PROVIDER_STATUSES`/:data:`REASONS`/:data:`RECOMMENDATIONS`），
  reason->recommendation 映射确定；输出键序确定（providers 恒按
  release-readiness ``SMOKE_PROVIDERS`` 序）；非法 voice_mode 先于一切
  探测 fail-closed（CLI exit 2）；malformed URL 归因为
  ``not_ready/malformed_url``（不崩溃、给 fix_endpoint_config 建议）；
- **拓扑/provider-key 无漂移**：provider 键与语音拓扑直接复用
  provider-smoke-evidence/release-readiness 既有常量（不复制第二份）。

既有 provider-smoke 行为零改动：本工具不触碰
provider_smoke_evidence.py 与三个冒烟脚本的任何判定逻辑。
"""
from __future__ import annotations

import ipaddress
import json
import unicodedata
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlsplit

# 拓扑与 provider 键直接复用权威常量（防漂移：测试交叉锁定）
from app.ops.provider_smoke_evidence import VOICE_MODES
from app.ops.release_readiness import SMOKE_PROVIDERS, SMOKE_VOICE_MODES

#: 证据自声明（报告 tool/schema_version；与 gate 证据 schema 明确不同物）
TOOL_ID = "provider-smoke-preflight"
SCHEMA_VERSION = "provider-smoke-preflight-v1"

#: providers 键序 = release-readiness SMOKE_PROVIDERS（voice/search/llm）
PROVIDER_KEYS = SMOKE_PROVIDERS

#: 有界只读探测超时（秒）：与 smoke_local_voice._probe_health / CloudWebProvider
#: 默认值同契约（10s），固定常量不开放覆写（有界性是契约的一部分）
PROBE_TIMEOUT_SECONDS = 10.0

#: 不追 redirects（有界探测；SearXNG /search?format=json 与 /health 均直答）
_FOLLOW_REDIRECTS = False

#: search 只读探测查询词（非敏感，与 infra/smoke_search.sh 默认查询同款）
SEARCH_PROBE_QUERY = "AI Learning OS GitHub"

#: 上游引擎/驻留模型名单的有界截断（防超大响应撑爆报告）
MAX_REPORTED_ENGINES = 32
MAX_REPORTED_MODELS = 16

#: 远端自报字符串（引擎名/错误类/驻留模型名）进入报告前的**逐项**边界：
#: 控制字符剔除 + 每项 128 Unicode 码点截断——任意外形/体量的 provider
#: 响应不得撑爆 JSON 报告或向终端注入控制序列（监督修正 Round 1）
MAX_REMOTE_ITEM_CODEPOINTS = 128

#: 默认端点（仓库本地部署契约的文档化默认值；CLI 旗标可覆写）：
#: - search：infra/docker-compose.yml `--profile search` 的宿主绑定 8878
#: - ASR/TTS：tools/voice/smoke_local_voice.py 的默认 base URL
#: - LLM：M14-98/104 实证的本地 Ollama OpenAI 兼容端点（/v1 由探测层剥除）
DEFAULT_SEARCH_ENDPOINT = "http://127.0.0.1:8878"
DEFAULT_ASR_ENDPOINT = "http://127.0.0.1:8010/v1"
DEFAULT_TTS_ENDPOINT = "http://127.0.0.1:8011/v1"
DEFAULT_LLM_ENDPOINT = "http://127.0.0.1:11434/v1"
#: infra/provision_ollama_model.ps1 的模型别名（模型层 num_ctx=4096）
DEFAULT_LLM_MODEL = "aios-qwen3.5-9b-4096"

# --- 固定词汇（闭集枚举；测试锁定，绝不在运行期拼装新词） ----------------------

#: provider/check 状态闭集：ready=前置可观测且就绪；not_ready=探测到
#: 不就绪（reason 必填）；not_probed=拓扑声明该检查是外部凭据面，本工具
#: 刻意不探测（诚实缺失，不冒充 ready）
PROVIDER_STATUSES = ("ready", "not_ready", "not_probed")

#: 失败归因闭集（reason）
REASONS = (
    "not_configured",  # 必需 base URL 缺失/空白
    "malformed_url",  # base URL 非 http/https + host 形态
    "endpoint_absent",  # 连接被拒/服务未监听（如 M14-104 的 Ollama 缺席）
    "endpoint_timeout",  # 有界探测超时
    "http_failure",  # HTTP 非 2xx（如 SearXNG 未启用 json format 的 403）
    "malformed_response",  # 响应体非期望 JSON 形状
    "upstream_failure",  # search：端点在线但上游引擎自报不可达
    "empty_results",  # search：响应合法但 0 结果（smoke 门需要 >=1）
    "model_absent",  # llm：端点在线但模型未驻留（/api/ps 无该模型）
    "transport_error",  # 其它 httpx 传输层错误（TLS/协议等）
    "external_credentials_not_inspected",  # cloud/hybrid 语音槽位
)

#: 外部动作建议闭集（recommendation）——全部指向**外部**动作，本工具
#: 永远不执行、不指导本工具去变更任何服务
RECOMMENDATIONS = (
    "fix_endpoint_config",
    "start_externally_then_rerun",
    "investigate_endpoint_externally_then_rerun",
    "repair_upstream_network_externally",
    "load_model_externally_then_rerun",
    "verify_external_preconditions_then_run_smoke",
)

#: reason -> recommendation 确定映射
REASON_TO_RECOMMENDATION: dict[str, str] = {
    "not_configured": "fix_endpoint_config",
    "malformed_url": "fix_endpoint_config",
    "endpoint_absent": "start_externally_then_rerun",
    "endpoint_timeout": "investigate_endpoint_externally_then_rerun",
    "http_failure": "investigate_endpoint_externally_then_rerun",
    "malformed_response": "investigate_endpoint_externally_then_rerun",
    "upstream_failure": "repair_upstream_network_externally",
    "empty_results": "investigate_endpoint_externally_then_rerun",
    "model_absent": "load_model_externally_then_rerun",
    "transport_error": "investigate_endpoint_externally_then_rerun",
    "external_credentials_not_inspected": (
        "verify_external_preconditions_then_run_smoke"
    ),
}

#: 总体状态闭集：pass=三类 provider 全 ready；blocked=存在 not_ready；
#: partial=无 not_ready 但存在 not_probed（外部凭据面无法由本工具观测）
OVERALL_STATUSES = ("pass", "blocked", "partial")

#: 报告固定边界声明（诚实边界自声明：预检结论 != 冒烟结论）
_NOTES = (
    "预检 pass 仅代表 provider-smoke 前置条件可观测且就绪，不代表"
    " provider-smoke 已通过；本输出不是 release-readiness 证据，"
    "不生成 provider-smoke.json；建议动作全部为外部运维动作，本工具"
    "只读、零服务变更"
)


class ProviderSmokePreflightInputError(Exception):
    """参数校验问题（CLI exit 2：不做任何探测）。"""


class PreflightTransportError(Exception):
    """探测传输层失败的归因载体（reason 恒为 REASONS 闭集成员）。"""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        #: detail 只含异常类型名/固定文案——不含 URL/凭据（endpoint 可能
        #: 内嵌 userinfo，绝不回显）
        self.detail = detail


# --- URL 工具 -------------------------------------------------------------------


def _is_loopback_host(host: str) -> bool:
    """host 是否 loopback（localhost / 127.0.0.0/8 / ::1；同 LLM gateway 口径）。"""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_loopback_url(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return _is_loopback_host(host)


def _classify_url(raw: str | None) -> tuple[str, str]:
    """把 base URL 归类为 (url, reason)：reason 空 = 合法。

    空白 -> not_configured；以下形态一律 **fail-closed 拒绝**（先于一切
    探测，supervisor 修正 Round 1）：非 http/https 或无 host、含 userinfo
    （``user@`` 与 ``user:pass@`` 两形态——凭据绝不进入探测，不从 URL
    构造 Basic Auth，也不进入报告）、含 query 或 fragment（含尾随裸
    ``?``/``#`` 分隔符——base URL 契约里没有合法 query/fragment 位）。
    rstrip("/") 归一（base URL 拼接方约定）。
    """
    text = (raw or "").strip()
    if not text:
        return "", "not_configured"
    split = urlsplit(text)
    if split.scheme not in ("http", "https") or not split.netloc:
        return text, "malformed_url"
    if split.username is not None or "?" in text or "#" in text:
        return text, "malformed_url"
    return text.rstrip("/"), ""


def _sanitize_remote_item(value: str) -> str | None:
    """远端自报名单条目（引擎名/错误类/驻留模型名）进入报告前的有界净化。

    剔除 Unicode Cc 控制字符（防终端/JSON 控制序列注入与不可见填充），
    全控制字符/空串条目丢弃（None，不进名单）；截断到
    :data:`MAX_REMOTE_ITEM_CODEPOINTS` 个 Unicode 码点——配合既有名单
    条数上限（32 引擎/16 模型），任意外形/体量的 provider 响应都有确定
    的报告体量上界（supervisor 修正 Round 2）。
    """
    stripped = "".join(
        ch for ch in value if unicodedata.category(ch) != "Cc"
    )
    if not stripped:
        return None
    return stripped[:MAX_REMOTE_ITEM_CODEPOINTS]


def _service_root(endpoint: str) -> str:
    """OpenAI 兼容 base URL -> 服务根（剥 trailing / 与 /v1 前缀）。

    与 tools/voice/smoke_local_voice.py 的 _health_url 同款推导——健康/
    驻留契约挂在服务根上（/health、/api/ps），不在 /v1 OpenAI 面内。
    """
    base = endpoint.rstrip("/")
    return base.removesuffix("/v1")


# --- 环境代理压力（布尔观测，绝不输出代理值） ------------------------------------


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ambient_loopback_pressure(url: str) -> bool:
    """loopback URL 是否暴露在环境/注册表系统代理压力下。

    ``urllib.request.getproxies`` 覆盖环境变量与 Windows 注册表回落两通道
    （M14-104 实证的注册表通道）；``proxy_bypass`` 覆盖 NO_PROXY/
    ProxyOverride 豁免。返回布尔观测——代理 URL 本身（可能内嵌凭据）
    绝不进入任何输出。非 loopback URL 恒 False（探测保持 httpx 默认）。
    """
    import urllib.request

    host = urlsplit(url).hostname or ""
    if not _is_loopback_host(host):
        return False
    try:
        proxies = urllib.request.getproxies()
        if not proxies:
            return False
        return not urllib.request.proxy_bypass(host)
    except OSError:
        # 注册表/环境读取失败的保守观测：无法证明无压力则如实报有
        return True


# --- 只读传输层 -------------------------------------------------------------------


def _httpx_get(
    url: str,
    *,
    timeout_seconds: float,
    trust_env: bool,
    transport: Any = None,
) -> tuple[int, str]:
    """真实传输：GET -> (status_code, body_text)；失败映射归因异常。

    httpx 异常 -> reason 闭集：TimeoutException -> endpoint_timeout、
    ConnectError -> endpoint_absent（服务未监听，M14-104 形态）、其余
    HTTPError -> transport_error。异常 detail 只含异常类型名（不回显 URL/
    headers——endpoint 可能内嵌 userinfo）。transport 注入（测试
    MockTransport）时零网络。
    """
    import httpx

    try:
        with httpx.Client(
            timeout=timeout_seconds,
            trust_env=trust_env,
            transport=transport,
            follow_redirects=_FOLLOW_REDIRECTS,
        ) as client:
            response = client.get(url)
    except httpx.TimeoutException as cause:
        raise PreflightTransportError(
            "endpoint_timeout", type(cause).__name__
        ) from cause
    except httpx.ConnectError as cause:
        raise PreflightTransportError(
            "endpoint_absent", type(cause).__name__
        ) from cause
    except httpx.HTTPError as cause:
        raise PreflightTransportError(
            "transport_error", type(cause).__name__
        ) from cause
    return response.status_code, response.text


_Get = Callable[..., tuple[int, str]]


def _probe_json(
    url: str,
    *,
    get: _Get,
) -> tuple[str, dict[str, Any] | None, int | None]:
    """探测一个 JSON 契约端点 -> (reason, payload, http_status)。

    reason 空 = HTTP 2xx 且 body 是 JSON 对象（进一步形状判定由调用方做）；
    http_status 在拿到 HTTP 响应时透出（归因与报告用）。404 等 ->
    http_failure；body 非 JSON 对象 -> malformed_response。
    """
    try:
        status, text = get(url, timeout_seconds=PROBE_TIMEOUT_SECONDS)
    except PreflightTransportError as cause:
        return cause.reason, None, None
    if not 200 <= status < 300:
        return "http_failure", None, status
    try:
        payload = json.loads(text)
    except ValueError:
        return "malformed_response", None, status
    if not isinstance(payload, dict):
        return "malformed_response", None, status
    return "", payload, status


def _parse_unresponsive_engines(
    payload: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    """解析 SearXNG ``unresponsive_engines`` 自报（宽容双形态）。

    新版是 [engine, error] 二元列表、旧版是字符串列表——统一归一为
    (sorted 去重 engine 名, sorted 去重 error 类名)。每项先经
    :func:`_sanitize_remote_item` 有界净化（控制字符剔除 + 128 码点截断，
    净化后为空的条目丢弃），再套用既有条数上限——名单体量与内容都有
    确定上界。端点没报则空列表（不猜测上游状态）。
    """
    engines: set[str] = set()
    errors: set[str] = set()

    def _add(bucket: set[str], raw_item: Any) -> None:
        if isinstance(raw_item, str):
            safe = _sanitize_remote_item(raw_item.strip())
            if safe is not None:
                bucket.add(safe)

    raw = payload.get("unresponsive_engines")
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                _add(engines, entry[0])
                _add(errors, entry[1])
            else:
                _add(engines, entry)
    return (
        sorted(engines)[:MAX_REPORTED_ENGINES],
        sorted(errors)[:MAX_REPORTED_ENGINES],
    )


# --- 各 provider 检查 -------------------------------------------------------------


def _check_search(endpoint_raw: str | None, *, get: _Get) -> dict[str, Any]:
    """search/SearXNG：base URL + 只读 /search?format=json 形状与上游归因。"""
    endpoint, url_reason = _classify_url(endpoint_raw)
    if url_reason:
        # malformed/not_configured：不回显未通过校验的输入（可能内嵌凭据/
        # 控制字符），endpoint 恒 None
        return {
            "status": "not_ready",
            "reason": url_reason,
            "recommendation": REASON_TO_RECOMMENDATION[url_reason],
            "endpoint": None,
        }
    loopback = _is_loopback_url(endpoint)
    # SearXNG JSON API 契约（与 CloudWebProvider 同款）：/search?q=..&format=json
    probe_url = (
        f"{endpoint}/search?"
        f"{urlencode({'q': SEARCH_PROBE_QUERY, 'format': 'json'})}"
    )
    reason, payload, status = _probe_json(probe_url, get=get)
    # 非 loopback 端点保持 httpx 默认 trust_env（部署代理照常生效）；本核心
    # 探测恒 loopback 直连——trust_env 语义由注入的 get 闭包携带（见
    # _loopback_get），此处只记录探测实际使用的值（报告透明）。
    result: dict[str, Any] = {
        "status": "not_ready",
        "reason": None,
        "recommendation": None,
        "endpoint": endpoint,
        "probe": "search_format_json",
        "loopback": loopback,
        "probe_trust_env": not loopback,
        "http_status": status,
        "results": None,
        "unresponsive_engines": [],
        "unresponsive_error_classes": [],
    }
    if reason:
        result["reason"] = reason
        result["recommendation"] = REASON_TO_RECOMMENDATION[reason]
        return result
    assert payload is not None
    results = payload.get("results")
    engines, error_classes = _parse_unresponsive_engines(payload)
    result["unresponsive_engines"] = engines
    result["unresponsive_error_classes"] = error_classes
    if not isinstance(results, list):
        # CloudWebProvider 同款契约：results 必须是列表
        result["reason"] = "malformed_response"
        result["recommendation"] = REASON_TO_RECOMMENDATION["malformed_response"]
        return result
    result["results"] = len(results)
    if results:
        result["status"] = "ready"
        result["reason"] = None
        return result
    # 0 结果：端点自报上游引擎不可达 -> 上游网络归因；否则 empty_results
    reason = "upstream_failure" if engines else "empty_results"
    result["reason"] = reason
    result["recommendation"] = REASON_TO_RECOMMENDATION[reason]
    return result


def _voice_health_check(
    kind: str, endpoint_raw: str | None, *, get: _Get
) -> dict[str, Any]:
    """单个本地语音引擎的 /health listener 就绪探测（10s 有界只读）。"""
    endpoint, url_reason = _classify_url(endpoint_raw)
    if url_reason:
        # 同 search：不回显未通过校验的输入
        return {
            "status": "not_ready",
            "reason": url_reason,
            "recommendation": REASON_TO_RECOMMENDATION[url_reason],
            "endpoint": None,
            "kind": kind,
        }
    health_url = f"{_service_root(endpoint)}/health"
    loopback = _is_loopback_url(endpoint)
    result: dict[str, Any] = {
        "status": "not_ready",
        "reason": None,
        "recommendation": None,
        "kind": kind,
        "endpoint": endpoint,
        "probe": "health",
        "loopback": loopback,
        "probe_trust_env": not loopback,
        "http_status": None,
        "json_body": False,
    }
    try:
        status, text = get(health_url, timeout_seconds=PROBE_TIMEOUT_SECONDS)
    except PreflightTransportError as cause:
        result["reason"] = cause.reason
        result["recommendation"] = REASON_TO_RECOMMENDATION[cause.reason]
        return result
    result["http_status"] = status
    # 健康契约与 smoke_local_voice._probe_health 同口径：HTTP 200 即 listener
    # 就绪（body 形状是观测不是门——两个引擎的 /health JSON 键不同构）
    try:
        result["json_body"] = isinstance(json.loads(text), dict)
    except ValueError:
        result["json_body"] = False
    if status == 200:
        result["status"] = "ready"
        return result
    result["reason"] = "http_failure"
    result["recommendation"] = REASON_TO_RECOMMENDATION["http_failure"]
    return result


def _check_local_voice(
    asr_endpoint: str | None, tts_endpoint: str | None, *, get: _Get
) -> dict[str, Any]:
    """voice 槽位（local 拓扑）：ASR + TTS 双 /health 探测，两者 ready 才 ready。"""
    checks = {
        "asr": _voice_health_check("asr", asr_endpoint, get=get),
        "tts": _voice_health_check("tts", tts_endpoint, get=get),
    }
    not_ready = [
        (name, check["reason"])
        for name, check in checks.items()
        if check["status"] != "ready"
    ]
    if not_ready:
        # 语音槽位 reason 取第一个不就绪子检查（asr 优先——阅读顺序确定）；
        # 两个子检查的明细都在 checks 里，不丢归因
        first_reason = not_ready[0][1]
        return {
            "status": "not_ready",
            "reason": first_reason,
            "recommendation": REASON_TO_RECOMMENDATION[first_reason],
            "checks": checks,
        }
    return {
        "status": "ready",
        "reason": None,
        "recommendation": None,
        "checks": checks,
    }


def _check_llm(
    endpoint_raw: str | None, model_raw: str | None, *, get: _Get
) -> dict[str, Any]:
    """LLM/Ollama-compatible 本地网关：base URL + /api/ps 模型驻留只读探测。"""
    model = (model_raw or "").strip() or DEFAULT_LLM_MODEL
    endpoint, url_reason = _classify_url(endpoint_raw)
    if url_reason:
        # 同 search：不回显未通过校验的输入
        return {
            "status": "not_ready",
            "reason": url_reason,
            "recommendation": REASON_TO_RECOMMENDATION[url_reason],
            "endpoint": None,
            "model": model,
        }
    ps_url = f"{_service_root(endpoint)}/api/ps"
    loopback = _is_loopback_url(endpoint)
    result: dict[str, Any] = {
        "status": "not_ready",
        "reason": None,
        "recommendation": None,
        "endpoint": endpoint,
        "model": model,
        "probe": "api_ps",
        "loopback": loopback,
        "probe_trust_env": not loopback,
        "http_status": None,
        "model_resident": False,
        "resident_models": [],
    }
    reason, payload, status = _probe_json(ps_url, get=get)
    result["http_status"] = status
    if reason:
        result["reason"] = reason
        result["recommendation"] = REASON_TO_RECOMMENDATION[reason]
        return result
    assert payload is not None
    models = payload.get("models")
    if not isinstance(models, list):
        result["reason"] = "malformed_response"
        result["recommendation"] = REASON_TO_RECOMMENDATION["malformed_response"]
        return result
    resident: list[str] = []
    found = False
    for entry in models:
        name = None
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("model")
        if isinstance(name, str) and name.strip():
            raw_name = name.strip()
            # Ollama /api/ps 的 name 形如 ``alias:latest``——按 ``:`` 前段
            # 与请求模型名比对（别名供给（provision 脚本）不带 tag）；匹配
            # 语义走原始名（既有行为），报告名单走有界净化名
            if raw_name == model or raw_name.split(":", 1)[0] == model:
                found = True
            safe = _sanitize_remote_item(raw_name)
            if safe is not None:
                resident.append(safe)
    result["resident_models"] = sorted(resident)[:MAX_REPORTED_MODELS]
    result["model_resident"] = found
    if found:
        result["status"] = "ready"
        return result
    result["reason"] = "model_absent"
    result["recommendation"] = REASON_TO_RECOMMENDATION["model_absent"]
    return result


def _voice_slot(voice_mode: str, *, get: _Get, asr: str | None, tts: str | None):
    """语音槽位按拓扑选轨：local 探测本地引擎；cloud/hybrid 不检凭据。"""
    if voice_mode == "local":
        return _check_local_voice(asr, tts, get=get)
    return {
        "status": "not_probed",
        "reason": "external_credentials_not_inspected",
        "recommendation": REASON_TO_RECOMMENDATION[
            "external_credentials_not_inspected"
        ],
        # 凭据/云端点是冒烟调用时外部注入的 precondition——本工具不读
        # secret、不探测外部端点，只如实声明该面未被观测
        "topology_note": (
            "cloud/hybrid 语音冒烟前置（云端端点+凭据）由运维外部供给，"
            "本工具不检查凭据、不探测外部端点"
        ),
    }


# --- 主入口 -----------------------------------------------------------------------


def _loopback_aware_get(get: _Get) -> _Get:
    """包装注入的 get：按 URL 是否 loopback 强制 trust_env 语义。

    loopback -> trust_env=False（M14-100/106 同口径：注册表/环境代理不得
    劫持本机探测）；非 loopback -> httpx 默认 True（部署代理照常生效）。
    真实 _httpx_get 消费该旗标；测试替身可忽略（只记录调用）。
    """

    def _wrapped(url: str, **kwargs: Any) -> tuple[int, str]:
        return get(
            url,
            timeout_seconds=kwargs.get(
                "timeout_seconds", PROBE_TIMEOUT_SECONDS
            ),
            trust_env=not _is_loopback_url(url),
        )

    return _wrapped


def run_provider_smoke_preflight(
    *,
    voice_mode: str = "local",
    search_endpoint: str | None = None,
    asr_endpoint: str | None = None,
    tts_endpoint: str | None = None,
    llm_endpoint: str | None = None,
    llm_model: str | None = None,
    get: _Get | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """执行三类 provider 的只读前置预检并组装报告（stdout-only，不落盘）。

    - ``voice_mode``：local 探测 ASR/TTS 本地 /health；cloud/hybrid 语音
      槽位 not_probed/external_credentials_not_inspected（不检凭据）。
      非法值先于一切探测抛
      :class:`ProviderSmokePreflightInputError`（CLI exit 2）。
    - 端点默认值见 ``DEFAULT_*_ENDPOINT``（仓库本地部署契约）；调用方
      显式传值以镜像冒烟实际将用的配置。
    - ``get``/``clock`` 可注入（测试零网络、零真实时间依赖）。

    返回报告 dict：providers 恒按 ``SMOKE_PROVIDERS`` 序组装；overall 为
    pass（全 ready）/ blocked（存在 not_ready）/ partial（无 not_ready 但
    存在 not_probed）。报告恒携带 ``production_ready=false`` 与
    ``release_readiness_evidence=false`` 自声明——预检输出不是
    release-readiness 证据，pass 只代表前置可观测就绪。
    """
    if voice_mode not in VOICE_MODES or voice_mode not in SMOKE_VOICE_MODES:
        raise ProviderSmokePreflightInputError(
            f"未知 voice_mode: {voice_mode!r}（合法值: {list(VOICE_MODES)}）"
        )
    transport = _loopback_aware_get(get or _httpx_get)
    search = _check_search(
        search_endpoint or DEFAULT_SEARCH_ENDPOINT, get=transport
    )
    voice = _voice_slot(
        voice_mode,
        get=transport,
        asr=asr_endpoint or DEFAULT_ASR_ENDPOINT,
        tts=tts_endpoint or DEFAULT_TTS_ENDPOINT,
    )
    llm = _check_llm(
        llm_endpoint or DEFAULT_LLM_ENDPOINT,
        llm_model or DEFAULT_LLM_MODEL,
        get=transport,
    )
    providers: dict[str, Any] = {
        "voice": voice,
        "search": search,
        "llm": llm,
    }
    statuses = {name: entry["status"] for name, entry in providers.items()}
    if any(status == "not_ready" for status in statuses.values()):
        overall = "blocked"
    elif any(status == "not_probed" for status in statuses.values()):
        overall = "partial"
    else:
        overall = "pass"
    # loopback 代理压力观测：对全部被探测的 loopback URL 逐 host 判定
    # （布尔；绝不输出代理 URL——可能内嵌代理凭据）
    loopback_urls = [
        entry.get("endpoint") or ""
        for entry in providers.values()
        if entry.get("endpoint") and entry.get("loopback")
    ]
    for entry in providers.get("voice", {}).get("checks", {}).values():
        if entry.get("endpoint") and entry.get("loopback"):
            loopback_urls.append(entry["endpoint"])
    hosts = sorted({urlsplit(url).hostname or "" for url in loopback_urls})
    pressured = sorted(
        {
            urlsplit(url).hostname or ""
            for url in loopback_urls
            if _ambient_loopback_pressure(url)
        }
    )
    return {
        "tool": TOOL_ID,
        "schema_version": SCHEMA_VERSION,
        "generated_at": (clock or _utc_now)().isoformat(),
        "topology": {"voice_mode": voice_mode},
        "providers": providers,
        "ambient_proxy": {
            "loopback_hosts_checked": hosts,
            "pressure_detected": bool(pressured),
            "pressured_hosts": pressured,
        },
        "overall_status": overall,
        # 诚实边界自声明（固定字段，测试锁定）：预检 pass != provider-smoke
        # 通过；本输出不是 release-readiness 证据
        "production_ready": False,
        "release_readiness_evidence": False,
        "notes": _NOTES,
    }


def preflight_exit_code(report: Mapping[str, Any]) -> int:
    """报告 -> CLI 退出码：pass=0；blocked/partial=1（如实不通过）。"""
    return 0 if report["overall_status"] == "pass" else 1


# --- 人类可读摘要 -----------------------------------------------------------------


def format_preflight_summary(report: Mapping[str, Any]) -> str:
    """人类可读摘要（无凭据/无代理值/无敏感配置；固定词汇 + 明确边界声明）。"""
    lines = [
        "provider 冒烟前置只读预检（provider-smoke-preflight）",
        f"完成时间: {report['generated_at']}  voice_mode={report['topology']['voice_mode']}",
    ]
    ambient = report["ambient_proxy"]
    if ambient["pressure_detected"]:
        lines.append(
            f"环境代理压力: 检测到 loopback 压力 host={ambient['pressured_hosts']}"
            "（loopback 探测已强制绕过代理，不受影响；仅环境观测）"
        )
    else:
        lines.append("环境代理压力: 未检测到 loopback 代理压力")
    for name in PROVIDER_KEYS:
        entry = report["providers"][name]
        status = entry["status"]
        reason = entry.get("reason")
        line = f"{name}: {status}"
        if reason:
            line += f"  reason={reason}"
        rec = entry.get("recommendation")
        if rec:
            line += f"  建议={rec}"
        lines.append(line)
        if status == "not_ready":
            for check_name, check in entry.get("checks", {}).items():
                lines.append(
                    f"  {check_name}: {check['status']}"
                    + (f"  reason={check['reason']}" if check.get("reason") else "")
                )
    overall = report["overall_status"]
    if overall == "pass":
        lines.append(
            "RESULT: PASS——三类 provider 前置条件可观测且就绪（注意：这是"
            "预检通过，不是 provider-smoke 已通过；请由运维按拓扑执行真实"
            "冒烟并经 provider-smoke-export/aggregate 产出门证据）。"
        )
    elif overall == "blocked":
        lines.append(
            "RESULT: BLOCKED——存在前置不就绪（见上方 reason/建议）；全部建议"
            "为外部运维动作（启动服务/修网络/载模型/修配置后重跑预检），本工具"
            "不执行任何变更。"
        )
    else:
        lines.append(
            "RESULT: PARTIAL——可探测面就绪，但 cloud/hybrid 语音凭据面不在"
            "本工具观测范围（外部供给 precondition）；如实按 partial 处理。"
        )
    lines.append(
        "边界: 本输出不是 release-readiness 证据（不生成 provider-smoke.json）；"
        "预检 pass 不代表 provider-smoke 已通过；production_ready=false；"
        "全程只读零服务变更。"
    )
    lines.append("退出码: pass=0 / blocked 或 partial=1 / 参数问题=2。")
    return "\n".join(lines)
