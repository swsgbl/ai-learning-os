"""M7-01 Production Compose profile：一条命令启动生产本地版；profile 分离 local/hybrid/cloud。

- 快测（无门控）：docker compose config 渲染断言——profile 决定服务集与 VOICE_MODE
  插值（AIOS_VOICE_MODE），不启动任何容器；
- 门控真启动测试（AIOS_COMPOSE_SMOKE=1）：--profile local up -d --build 全栈 healthy
  后 API /health + /papers + voice providers + Web 首页真实可达，结束后 down。

profile 语义（与 README 生产本地版节一致）：
- 基础服务 postgres/redis/minio/api/web 不挂 profile，恒启动；
- livekit（语音基础设施）挂 local/hybrid/cloud 三个命名 profile；
- VOICE_MODE 由 AIOS_VOICE_MODE 插值，默认 local；hybrid/cloud 必须显式设置，
  未设置时如实回退 local（不虚报云端能力，provider 视图透出实际路由）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

COMPOSE_FILE = Path(__file__).resolve().parents[3] / "infra" / "docker-compose.yml"


def _compose_available() -> bool:
    return shutil.which("docker") is not None and COMPOSE_FILE.is_file()


def _render(
    profile: str | None = None,
    extra_env: dict[str, str] | None = None,
    unset: tuple[str, ...] | None = None,
) -> dict:
    """docker compose config --format json：渲染合并后的 compose model（不启动容器）。

    unset 显式剔除宿主侧变量（默认渲染断言不受本机环境污染）。
    """
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE)]
    if profile:
        cmd += ["--profile", profile]
    cmd += ["config", "--format", "json"]
    merged = dict(os.environ)
    for key in unset or ():
        merged.pop(key, None)
    merged.update(extra_env or {})
    result = subprocess.run(
        cmd, check=False, capture_output=True, text=True, encoding="utf-8", timeout=120, env=merged,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _services(model: dict) -> set[str]:
    return set(model.get("services", {}))


def _voice_mode(model: dict) -> str:
    env = model["services"]["api"]["environment"]
    entries = env if isinstance(env, list) else [f"{k}={v}" for k, v in env.items()]
    for entry in entries:
        if entry.startswith("VOICE_MODE="):
            return entry.split("=", 1)[1]
    raise AssertionError(f"api environment 缺 VOICE_MODE: {entries}")


@pytest.mark.skipif(not _compose_available(), reason="需要 docker compose CLI")
def test_local_profile_full_stack() -> None:
    """--profile local：全栈 6 服务 + VOICE_MODE=local + api/web healthcheck。"""
    model = _render("local")
    assert _services(model) == {"postgres", "redis", "minio", "api", "web", "livekit"}
    assert _voice_mode(model) == "local"
    assert model["services"]["api"]["healthcheck"]
    assert model["services"]["web"]["healthcheck"]


@pytest.mark.skipif(  # 渲染断言需要 docker compose CLI
    not _compose_available(), reason="需要 docker compose CLI"
)
def test_no_profile_minimal_stack_without_voice() -> None:
    """无 profile：最小栈 5 服务（不含 livekit）——纯后端部署形态。"""
    model = _render(None)
    assert _services(model) == {"postgres", "redis", "minio", "api", "web"}


@pytest.mark.skipif(not _compose_available(), reason="需要 docker compose CLI")
def test_hybrid_cloud_profile_voice_mode_injection() -> None:
    """hybrid/cloud profile + 显式 AIOS_VOICE_MODE：VOICE_MODE 按注入值渲染。"""
    hybrid = _render("hybrid", {"AIOS_VOICE_MODE": "hybrid"})
    assert _voice_mode(hybrid) == "hybrid"
    assert "livekit" in _services(hybrid)
    docker = _render("cloud", {"AIOS_VOICE_MODE": "cloud"})
    assert _voice_mode(docker) == "cloud"
    assert "livekit" in _services(docker)


@pytest.mark.skipif(not _compose_available(), reason="需要 docker compose CLI")
def test_hybrid_without_env_falls_back_to_local() -> None:
    """hybrid 未设 AIOS_VOICE_MODE：如实回退 local（不虚报云端能力）。"""
    model = _render("hybrid")
    assert _voice_mode(model) == "local"


def _api_env(model: dict) -> dict[str, str]:
    env = model["services"]["api"]["environment"]
    return env if isinstance(env, dict) else dict(entry.split("=", 1) for entry in env)


@pytest.mark.skipif(  # 渲染断言需要 docker compose CLI
    not _compose_available(), reason="需要 docker compose CLI"
)
def test_local_voice_env_passthrough_and_host_gateway() -> None:
    """M14-01：AIOS_*_LOCAL_* 插值透传到 api 容器 env + host.docker.internal host-gateway。

    容器形态下本地引擎经 host.docker.internal 访问宿主 WSL 的 127.0.0.1 服务
    （可达性实证脚本 tools/voice/compose_voice_reachability.sh）；默认留空 =
    降级替身透出 fallback。
    """
    model = _render("local", {
        "AIOS_ASR_LOCAL_ENDPOINT": "http://host.docker.internal:8010/v1",
        "AIOS_TTS_LOCAL_ENDPOINT": "http://host.docker.internal:8011/v1",
    })
    env = _api_env(model)
    assert env["ASR_LOCAL_ENDPOINT"] == "http://host.docker.internal:8010/v1"
    assert env["TTS_LOCAL_ENDPOINT"] == "http://host.docker.internal:8011/v1"
    assert env["ASR_LOCAL_MODEL"] == "sensevoice"
    assert env["TTS_LOCAL_MODEL"] == "Fun-CosyVoice3-0.5B-2512"
    assert env["ASR_LOCAL_API_KEY"] == ""
    assert env["TTS_LOCAL_API_KEY"] == ""
    # Linux 引擎上 host.docker.internal 需显式 host-gateway（Docker Desktop 自带）；
    # compose config JSON 归一化为 "name=ip" 形态（compose 文件内是 "name:ip"）
    extra_hosts = model["services"]["api"]["extra_hosts"]
    assert any(entry.replace("=", ":") == "host.docker.internal:host-gateway" for entry in extra_hosts)

    defaults = _render("local")
    default_env = _api_env(defaults)
    assert default_env["ASR_LOCAL_ENDPOINT"] == ""  # 默认留空 → 降级替身（不虚报）
    assert default_env["TTS_LOCAL_ENDPOINT"] == ""


# M14-64: cloud/search/LLM provider 与隐私路由透传的宿主侧 AIOS_* 变量全集
# （默认渲染测试显式剔除，隔离本机环境——compose 子进程本就继承宿主 env）
PROVIDER_PASSTHROUGH_ENV_KEYS = (
    "AIOS_MODEL_ROUTE",
    "AIOS_SEARCH_MODE",
    "AIOS_PRIVACY_STORE_AUDIO",
    "AIOS_PRIVACY_SEND_CONTEXT_TO_CLOUD",
    "AIOS_ASR_PROVIDER",
    "AIOS_TTS_PROVIDER",
    "AIOS_EMBEDDING_PROVIDER",
    "AIOS_ASR_CLOUD_ENDPOINT",
    "AIOS_ASR_CLOUD_API_KEY",
    "AIOS_ASR_CLOUD_MODEL",
    "AIOS_TTS_CLOUD_ENDPOINT",
    "AIOS_TTS_CLOUD_API_KEY",
    "AIOS_TTS_CLOUD_MODEL",
    "AIOS_TTS_CLOUD_VOICE",
    "AIOS_LLM_PROVIDER",
    "AIOS_LLM_ENDPOINT",
    "AIOS_LLM_API_KEY",
    "AIOS_LLM_MODEL",
    "AIOS_RUBRIC_JUDGE",
    "AIOS_SEARCH_PROVIDER",
    "AIOS_SEARCH_CLOUD_ENDPOINT",
    "AIOS_SEARCH_CLOUD_API_KEY",
)


@pytest.mark.skipif(not _compose_available(), reason="需要 docker compose CLI")
def test_provider_env_passthrough_synthetic_injection() -> None:
    """M14-64：AIOS_* provider/隐私路由注入逐项透传到 api 容器 environment。

    测试值全部为 synthetic（example.invalid / synthetic-* marker）——只验证
    插值链路，不含任何真实 endpoint/key/model。
    """
    model = _render("local", {
        "AIOS_MODEL_ROUTE": "cloud",
        "AIOS_SEARCH_MODE": "cloud",
        "AIOS_PRIVACY_STORE_AUDIO": "true",
        "AIOS_PRIVACY_SEND_CONTEXT_TO_CLOUD": "false",
        "AIOS_ASR_PROVIDER": "synthetic-asr-provider",
        "AIOS_TTS_PROVIDER": "synthetic-tts-provider",
        "AIOS_ASR_CLOUD_ENDPOINT": "https://asr.example.invalid/v1",
        "AIOS_ASR_CLOUD_API_KEY": "synthetic-asr-cloud-key",
        "AIOS_ASR_CLOUD_MODEL": "synthetic-asr-cloud-model",
        "AIOS_TTS_CLOUD_ENDPOINT": "https://tts.example.invalid/v1",
        "AIOS_TTS_CLOUD_API_KEY": "synthetic-tts-cloud-key",
        "AIOS_TTS_CLOUD_MODEL": "synthetic-tts-cloud-model",
        "AIOS_TTS_CLOUD_VOICE": "synthetic-tts-cloud-voice",
        "AIOS_LLM_PROVIDER": "synthetic-llm-provider",
        "AIOS_LLM_ENDPOINT": "https://llm.example.invalid/v1",
        "AIOS_LLM_API_KEY": "synthetic-llm-key",
        "AIOS_LLM_MODEL": "synthetic-llm-model",
        "AIOS_RUBRIC_JUDGE": "llm",
        "AIOS_EMBEDDING_PROVIDER": "synthetic-embedding-provider",
        "AIOS_SEARCH_PROVIDER": "synthetic-search-provider",
        "AIOS_SEARCH_CLOUD_ENDPOINT": "https://search.example.invalid",
        "AIOS_SEARCH_CLOUD_API_KEY": "synthetic-search-key",
    })
    env = _api_env(model)
    # 模式与隐私路由按注入值渲染
    assert env["MODEL_ROUTE"] == "cloud"
    assert env["SEARCH_MODE"] == "cloud"
    assert env["PRIVACY_STORE_AUDIO"] == "true"
    assert env["PRIVACY_SEND_CONTEXT_TO_CLOUD"] == "false"
    # 显式 provider selector 逐项透传（routing.py：覆盖 VOICE_MODE 默认选择）
    assert env["ASR_PROVIDER"] == "synthetic-asr-provider"
    assert env["TTS_PROVIDER"] == "synthetic-tts-provider"
    assert env["EMBEDDING_PROVIDER"] == "synthetic-embedding-provider"
    # 云端语音槽位逐项透传
    assert env["ASR_CLOUD_ENDPOINT"] == "https://asr.example.invalid/v1"
    assert env["ASR_CLOUD_API_KEY"] == "synthetic-asr-cloud-key"
    assert env["ASR_CLOUD_MODEL"] == "synthetic-asr-cloud-model"
    assert env["TTS_CLOUD_ENDPOINT"] == "https://tts.example.invalid/v1"
    assert env["TTS_CLOUD_API_KEY"] == "synthetic-tts-cloud-key"
    assert env["TTS_CLOUD_MODEL"] == "synthetic-tts-cloud-model"
    assert env["TTS_CLOUD_VOICE"] == "synthetic-tts-cloud-voice"
    # LLM 槽位逐项透传
    assert env["LLM_PROVIDER"] == "synthetic-llm-provider"
    assert env["LLM_ENDPOINT"] == "https://llm.example.invalid/v1"
    assert env["LLM_API_KEY"] == "synthetic-llm-key"
    assert env["LLM_MODEL"] == "synthetic-llm-model"
    # M14-70: 判分 judge selector 显式注入切换 LLM judge（keyword 默认见下方 defaults 测试）
    assert env["RUBRIC_JUDGE"] == "llm"
    # 云端搜索槽位逐项透传
    assert env["SEARCH_PROVIDER"] == "synthetic-search-provider"
    assert env["SEARCH_CLOUD_ENDPOINT"] == "https://search.example.invalid"
    assert env["SEARCH_CLOUD_API_KEY"] == "synthetic-search-key"


@pytest.mark.skipif(not _compose_available(), reason="需要 docker compose CLI")
def test_provider_env_defaults_do_not_enable_cloud() -> None:
    """M14-64 默认渲染：cloud/LLM/search 的 endpoint/key 全空，模式与模型默认
    与 Settings 应用默认一致（不虚报云端能力）。显式剔除宿主 AIOS_* 变量，
    断言结果不依赖本机环境。
    """
    model = _render("local", unset=PROVIDER_PASSTHROUGH_ENV_KEYS)
    env = _api_env(model)
    # 云端语音/LLM/搜索的 endpoint 与 key 默认全空（未注入即不启用）
    assert env["ASR_CLOUD_ENDPOINT"] == ""
    assert env["ASR_CLOUD_API_KEY"] == ""
    assert env["TTS_CLOUD_ENDPOINT"] == ""
    assert env["TTS_CLOUD_API_KEY"] == ""
    assert env["LLM_PROVIDER"] == ""
    assert env["LLM_ENDPOINT"] == ""
    assert env["LLM_API_KEY"] == ""
    assert env["LLM_MODEL"] == ""
    # M14-70: 判分 judge selector 默认 keyword（与 config.py rubric_judge 应用默认
    # 严格一致——keyword = 内置确定性 judge，不隐式启用 LLM judge）
    assert env["RUBRIC_JUDGE"] == "keyword"
    # provider selector 默认空 = 不覆盖模式默认路由（不启用任何云端能力）
    assert env["ASR_PROVIDER"] == ""
    assert env["TTS_PROVIDER"] == ""
    assert env["EMBEDDING_PROVIDER"] == ""
    assert env["SEARCH_PROVIDER"] == ""
    assert env["SEARCH_CLOUD_ENDPOINT"] == ""
    assert env["SEARCH_CLOUD_API_KEY"] == ""
    # 模式/隐私/模型默认与 config.py Settings 应用默认一致
    assert env["MODEL_ROUTE"] == "hybrid"
    assert env["SEARCH_MODE"] == "local"
    assert env["PRIVACY_STORE_AUDIO"] == "false"
    assert env["PRIVACY_SEND_CONTEXT_TO_CLOUD"] == "true"
    assert env["ASR_CLOUD_MODEL"] == "whisper-1"
    assert env["TTS_CLOUD_MODEL"] == "tts-1"
    # M14-65: 云端 TTS 音色默认与 config.py Settings 应用默认一致（tongtong）
    assert env["TTS_CLOUD_VOICE"] == "tongtong"
    # local voice 透传不受本次变更影响（M14-01 原有契约保持）
    assert env["VOICE_MODE"] == "local"


@pytest.mark.skipif(not _compose_available(), reason="需要 docker compose CLI")
def test_provider_env_empty_tts_voice_falls_back_to_default() -> None:
    """M14-65：AIOS_TTS_CLOUD_VOICE 置空 → `:-` 插值语义回落默认 tongtong
    （与 TTS_CLOUD_MODEL 同款行为锁定；容器形态无「不带 voice」的空值形态）。"""
    model = _render("local", {"AIOS_TTS_CLOUD_VOICE": ""}, unset=PROVIDER_PASSTHROUGH_ENV_KEYS)
    env = _api_env(model)
    assert env["TTS_CLOUD_VOICE"] == "tongtong"


# ------------------------------------------------- M14-66 本地 SearXNG 搜索栈

#: searxng secret 注入链的宿主侧变量（渲染断言显式剔除，隔离本机环境）
SEARXNG_SECRET_ENV_KEYS = ("AIOS_SEARXNG_SECRET", "SEARXNG_SECRET")

#: M14-66 出站代理透传的宿主侧变量（默认渲染断言显式剔除，隔离本机环境）
SEARXNG_PROXY_ENV_KEYS = (
    "AIOS_SEARXNG_HTTP_PROXY",
    "AIOS_SEARXNG_HTTPS_PROXY",
    "AIOS_SEARXNG_NO_PROXY",
)

#: compose 插值默认回落占位（与 infra/searxng/settings.yml 的 server.secret_key
#: 同一字面量——静态一致性锁见 test_searxng_local_provider.py）
SEARXNG_FALLBACK_SECRET = "aios-searxng-local-secret-8e4b2c91d7f3"

#: supervisor 核验的官方镜像 digest pin（不可变供应链锚点）
SEARXNG_IMAGE = (
    "docker.io/searxng/searxng@sha256:"
    "6869f20676fd91e3f856bcaefc510bc363fdd126f7bd860f49f2ffcb3b305da0"
)


def _searxng_env(model: dict) -> dict[str, str]:
    env = model["services"]["searxng"]["environment"]
    return env if isinstance(env, dict) else dict(entry.split("=", 1) for entry in env)


@pytest.mark.skipif(not _compose_available(), reason="需要 docker compose CLI")
def test_search_profile_renders_local_searxng_provider() -> None:
    """M14-66：--profile search 渲染 searxng（digest pin / loopback 8878 /
    unless-stopped）——基础 5 服务 + searxng，语音 livekit 不隐含（两独立开关）。"""
    model = _render("search")
    assert _services(model) == {"postgres", "redis", "minio", "api", "web", "searxng"}
    svc = model["services"]["searxng"]
    assert svc["image"] == SEARXNG_IMAGE
    assert svc["restart"] == "unless-stopped"
    # 宿主暴露恒为 loopback:8878 → 容器 8080（8080 被本机无关进程占用，绝不映射）
    (port,) = svc["ports"]
    assert port["host_ip"] == "127.0.0.1"
    assert port["published"] == "8878"
    assert port["target"] == 8080


@pytest.mark.skipif(not _compose_available(), reason="需要 docker compose CLI")
def test_searxng_absent_without_search_profile() -> None:
    """M14-66：searxng 仅随 --profile search 渲染——无 profile 与三个语音
    profile（local/hybrid/cloud）均不含它（搜索是显式部署控制，不随栈隐式拉起，
    默认渲染零变化）。"""
    for profile in (None, "local", "hybrid", "cloud"):
        assert "searxng" not in _services(_render(profile)), profile


@pytest.mark.skipif(not _compose_available(), reason="需要 docker compose CLI")
def test_searxng_secret_passthrough_and_fallback() -> None:
    """M14-66：secret 注入链 = AIOS_SEARXNG_SECRET 优先 → 通用 SEARXNG_SECRET
    回落 → 双未设回落占位（`:-` 语义：置空同回落）。真实 secret 只经部署
    secret/.env 注入，占位仅供本地 dev/直连 docker run。"""
    aios = _render("search", {"AIOS_SEARXNG_SECRET": "synthetic-aios-secret"}, unset=SEARXNG_SECRET_ENV_KEYS)
    assert _searxng_env(aios)["SEARXNG_SECRET"] == "synthetic-aios-secret"
    generic = _render("search", {"SEARXNG_SECRET": "synthetic-generic-secret"}, unset=SEARXNG_SECRET_ENV_KEYS)
    assert _searxng_env(generic)["SEARXNG_SECRET"] == "synthetic-generic-secret"
    fallback = _render("search", unset=SEARXNG_SECRET_ENV_KEYS)
    assert _searxng_env(fallback)["SEARXNG_SECRET"] == SEARXNG_FALLBACK_SECRET
    emptied = _render("search", {"AIOS_SEARXNG_SECRET": ""}, unset=SEARXNG_SECRET_ENV_KEYS)
    assert _searxng_env(emptied)["SEARXNG_SECRET"] == SEARXNG_FALLBACK_SECRET


@pytest.mark.skipif(not _compose_available(), reason="需要 docker compose CLI")
def test_local_searxng_endpoint_injection_renders_internal_url() -> None:
    """M14-66：本地 SearXNG 接线配方渲染验证——显式注入 AIOS_SEARCH_MODE=cloud +
    AIOS_SEARCH_CLOUD_ENDPOINT=http://searxng:8080（compose 网络内端点，API 不经
    宿主端口）后两项透传 api env；不注入则默认空（providers.py 三门判定
    fail-closed，非搜索路径不隐式启用 context 出站）。"""
    wired = _render("search", {
        "AIOS_SEARCH_MODE": "cloud",
        "AIOS_SEARCH_CLOUD_ENDPOINT": "http://searxng:8080",
    }, unset=PROVIDER_PASSTHROUGH_ENV_KEYS)
    env = _api_env(wired)
    assert env["SEARCH_MODE"] == "cloud"
    assert env["SEARCH_CLOUD_ENDPOINT"] == "http://searxng:8080"

    defaults = _render("search", unset=PROVIDER_PASSTHROUGH_ENV_KEYS)
    default_env = _api_env(defaults)
    assert default_env["SEARCH_MODE"] == "local"
    assert default_env["SEARCH_CLOUD_ENDPOINT"] == ""


@pytest.mark.skipif(not _compose_available(), reason="需要 docker compose CLI")
def test_searxng_outbound_proxy_defaults_render_direct() -> None:
    """M14-66 默认渲染：出站代理三槽位恒空 = 直连出站（空值被 httpx/urllib
    忽略，不产生代理行为）——未注入部署变量时 compose 不引入任何代理；且
    代理控制只属 searxng 出站栈，api 服务不沾代理变量（api→searxng 走
    compose 网络内直连，绝不经代理路由）。"""
    model = _render("search", unset=SEARXNG_PROXY_ENV_KEYS)
    env = _searxng_env(model)
    assert env["HTTP_PROXY"] == ""
    assert env["HTTPS_PROXY"] == ""
    assert env["NO_PROXY"] == ""
    api_env = _api_env(model)
    assert "HTTP_PROXY" not in api_env
    assert "HTTPS_PROXY" not in api_env


@pytest.mark.skipif(not _compose_available(), reason="需要 docker compose CLI")
def test_searxng_outbound_proxy_explicit_passthrough() -> None:
    """M14-66 显式注入：AIOS_SEARXNG_*_PROXY / NO_PROXY → 容器侧三槽位
    原样透传（合成值断言，不涉真实代理地址）；容器侧仅大写单形——httpx
    经 urllib getproxies 大小写不敏感读取，单形即全量生效（镜像小写双形
    只添漂移面）；槽位独立，可只注入其一。"""
    synthetic = "http://synthetic-proxy.invalid:1080"
    wired = _render("search", {
        "AIOS_SEARXNG_HTTP_PROXY": synthetic,
        "AIOS_SEARXNG_HTTPS_PROXY": synthetic,
        "AIOS_SEARXNG_NO_PROXY": "10.0.0.0/8,.internal.example",
    }, unset=SEARXNG_PROXY_ENV_KEYS)
    env = _searxng_env(wired)
    assert env["HTTP_PROXY"] == synthetic
    assert env["HTTPS_PROXY"] == synthetic
    assert env["NO_PROXY"] == "10.0.0.0/8,.internal.example"
    for lower in ("http_proxy", "https_proxy", "no_proxy"):
        assert lower not in env, lower
    # 注入代理后 api 服务仍不沾代理变量（作用域限定 searxng 出站栈）
    api_env = _api_env(wired)
    assert "HTTP_PROXY" not in api_env
    assert "HTTPS_PROXY" not in api_env
    # 槽位独立：仅注入 HTTPS 代理（最常见形态）时其余槽位保持空
    https_only = _render(
        "search", {"AIOS_SEARXNG_HTTPS_PROXY": synthetic}, unset=SEARXNG_PROXY_ENV_KEYS,
    )
    partial = _searxng_env(https_only)
    assert partial["HTTPS_PROXY"] == synthetic
    assert partial["HTTP_PROXY"] == ""
    assert partial["NO_PROXY"] == ""


# ---------------------------------------------------------------- 门控真启动冒烟

def _wait_healthy(deadline_s: float = 420.0) -> dict[str, str]:
    """轮询 compose ps 直到目标服务全部 healthy/running（build 冷启动预算 7 分钟）。"""
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE), "ps", "--format", "json"]
    deadline = time.monotonic() + deadline_s
    last: dict[str, str] = {}
    while time.monotonic() < deadline:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8", timeout=60)
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        state: dict[str, str] = {}
        for line in lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            state[str(row.get("Service", ""))] = str(row.get("Health") or row.get("State") or "")
        last = state
        needed = {"postgres", "redis", "minio", "api", "web", "livekit"}
        ready = needed.issubset(state) and all(state[name] in ("healthy", "running") for name in needed)
        if ready:
            return state
    raise AssertionError(f"服务未在时限内 healthy: {last}")


@pytest.mark.skipif(os.environ.get("AIOS_COMPOSE_SMOKE") != "1", reason="需 AIOS_COMPOSE_SMOKE=1（真启动全栈）")
def test_production_local_up_smoke() -> None:
    """一条命令真启动生产本地版：全栈 healthy + API/Web 真实可达。"""
    base = ["docker", "compose", "-f", str(COMPOSE_FILE)]
    up = subprocess.run(
        base + ["--profile", "local", "up", "-d", "--build"],
        check=False, capture_output=True, text=True, encoding="utf-8", timeout=600,
    )
    assert up.returncode == 0, up.stderr
    checks = 0
    try:
        state = _wait_healthy()
        assert state["api"] == "healthy" and state["web"] == "healthy"
        checks += 1

        import httpx

        api = httpx.Client(trust_env=False, timeout=30)
        assert api.get("http://127.0.0.1:8000/health").status_code == 200
        assert api.get("http://127.0.0.1:8000/api/v1/papers").status_code == 200
        providers = api.get("http://127.0.0.1:8000/api/v1/voice/providers").json()
        assert providers.get("voice_mode") == "local"
        web = httpx.Client(trust_env=False, timeout=30)
        web_port = os.environ.get("AIOS_WEB_PORT", "3000")
        assert web.get(f"http://127.0.0.1:{web_port}/").status_code == 200
        checks = 5
    finally:
        subprocess.run(
            base + ["--profile", "local", "down"],
            check=False, capture_output=True, text=True, timeout=120,
        )
    print(f"compose smoke: {checks}/5 checks PASS")
