r"""M14-66 本地 SearXNG 栈静态契约：compose 服务定义 + 仓库 settings + 漂移锁。

不访问网络、不启动容器（PyYAML 静态解析，CI 可跑）；渲染面断言（profile
门控 / secret 插值 / 端点接线）见 test_compose_profiles.py 与
test_compose_restart_policy.py，两者互补。

锁定面：
- searxng 服务：digest 精确 pin（不可变供应链锚点）、恒 127.0.0.1:8878:8080
  （8080 被本机无关进程占用，宿主绝不映射 8080、绝不超 loopback）、:ro 配置
  挂载、命名缓存卷、/healthz 健康检查、独立 search profile、unless-stopped；
- infra/searxng/settings.yml：use_default_settings 继承上游默认 + 最小覆盖
  （html+json formats——JSON API 是 CloudWebProvider 可用性硬前提；
  limiter/public_instance false——私有本地实例语义）；
- secret 回退一致性：compose 插值默认字面量 == settings.yml secret_key 字面量
  （两处漂移任一即破坏 fallback 链，此锁防「改一处忘一处」）；
- 出站代理显式控制：三槽位 = AIOS_ 单链插值、默认恒空（直连出站）、无通用
  回落、仅大写单形进容器；searxng 服务定义零硬编码代理地址；
- api 接线槽位：SEARCH_CLOUD_ENDPOINT 默认恒空（fail-closed——providers.py
  三门判定缺一不启用，非搜索路径不隐式出站）；compose 注释含网络内端点配方；
- 仓库文件零 secret 形态（真实凭据只放部署 secret/.env）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.yml"
SETTINGS_FILE = REPO_ROOT / "infra" / "searxng" / "settings.yml"

#: supervisor 核验的官方镜像 digest pin——升级 = 显式改此值
SEARXNG_IMAGE = (
    "docker.io/searxng/searxng@sha256:"
    "6869f20676fd91e3f856bcaefc510bc363fdd126f7bd860f49f2ffcb3b305da0"
)
#: dev 占位 secret（compose 插值默认与 settings.yml 回退的同一字面量）
SEARXNG_FALLBACK_SECRET = "aios-searxng-local-secret-8e4b2c91d7f3"
#: compose 网络内 API 侧端点（宿主端口不经由）
INTERNAL_ENDPOINT = "http://searxng:8080"

#: M14-66 出站代理容器侧槽位 → 插值形态（AIOS_ 单链、空默认、无通用回落
#: ——宿主通用代理 env 绝不隐式进容器）
SEARXNG_PROXY_SLOTS = {
    "HTTP_PROXY": "${AIOS_SEARXNG_HTTP_PROXY:-}",
    "HTTPS_PROXY": "${AIOS_SEARXNG_HTTPS_PROXY:-}",
    "NO_PROXY": "${AIOS_SEARXNG_NO_PROXY:-}",
}

SECRET_PATTERNS = ("sk-", "AKIA", "ghp_", "xoxb-", "-----BEGIN")


@pytest.fixture(scope="module")
def compose() -> dict:
    yaml = pytest.importorskip("yaml", reason="PyYAML 不可用——跳过 compose 静态断言")
    model = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    assert isinstance(model, dict) and isinstance(model.get("services"), dict)
    return model


@pytest.fixture(scope="module")
def searxng(compose: dict) -> dict:
    return compose["services"]["searxng"]


@pytest.fixture(scope="module")
def settings() -> dict:
    yaml = pytest.importorskip("yaml", reason="PyYAML 不可用——跳过 settings 静态断言")
    model = yaml.safe_load(SETTINGS_FILE.read_text(encoding="utf-8"))
    assert isinstance(model, dict)
    return model


# ---------------------------------------------------------------- searxng 服务


def test_image_is_digest_pinned(searxng: dict) -> None:
    """官方镜像 digest 精确 pin（无浮动 tag；升级 = 显式改 digest）。"""
    assert searxng["image"] == SEARXNG_IMAGE


def test_host_exposure_loopback_8878_only(searxng: dict) -> None:
    """宿主暴露恒为 127.0.0.1:8878 → 容器 8080：唯一映射、恒 loopback
    （绝不 LAN/外网），宿主侧绝不出现 8080（被本机无关进程占用）。"""
    assert searxng["ports"] == ["127.0.0.1:8878:8080"]


def test_repo_config_mounted_readonly(searxng: dict) -> None:
    """仓库受控配置只读挂载 /etc/searxng（容器不得回写仓库文件）。"""
    assert "./searxng/settings.yml:/etc/searxng/settings.yml:ro" in searxng["volumes"]


def test_named_cache_volume_declared(searxng: dict, compose: dict) -> None:
    """命名缓存卷：容器重建/镜像升级不丢搜索缓存；顶层 volumes 同步声明。"""
    assert "searxng-cache:/var/cache/searxng" in searxng["volumes"]
    assert "searxng-cache" in compose["volumes"]


def test_healthcheck_probes_healthz(searxng: dict) -> None:
    """健康检查 = wget 探官方 /healthz（镜像含 /usr/sbin/wget，supervisor 核验）。"""
    hc = searxng["healthcheck"]
    assert hc["test"] == ["CMD-SHELL", "wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1"]
    assert hc["interval"] == "10s"
    assert hc["timeout"] == "5s"
    assert hc["retries"] == 5


def test_gated_behind_search_profile(searxng: dict) -> None:
    """独立 search profile（显式部署控制）：不挂语音 profile，不随基础栈隐式拉起。"""
    assert searxng["profiles"] == ["search"]
    assert searxng["restart"] == "unless-stopped"


def test_secret_interpolation_fallback_literal(searxng: dict) -> None:
    """secret 注入链字面量：AIOS_SEARXNG_SECRET 优先 → 通用 SEARXNG_SECRET →
    占位（`:-` 嵌套插值；真实 secret 只放部署 secret/.env，不入库）。"""
    assert searxng["environment"]["SEARXNG_SECRET"] == (
        "${AIOS_SEARXNG_SECRET:-${SEARXNG_SECRET:-" + SEARXNG_FALLBACK_SECRET + "}}"
    )


def test_outbound_proxy_slots_aios_only_empty_default(searxng: dict) -> None:
    """出站代理三槽位 = AIOS_ 单链插值、默认恒空（直连出站，空值被 urllib
    getproxies 忽略不产生代理行为）：不嵌套通用 HTTP_PROXY 回落——代理启用
    恒为部署显式行为，绝不隐式继承宿主 shell 代理 env。仅大写单形进容器
    （httpx 经 urllib 大小写不敏感读取，单形即全量生效；渲染面透传断言见
    test_compose_profiles.py）。"""
    for slot, form in SEARXNG_PROXY_SLOTS.items():
        assert searxng["environment"][slot] == form, slot
    for lower in ("http_proxy", "https_proxy", "no_proxy"):
        assert lower not in searxng["environment"], lower


def test_no_hardcoded_proxy_endpoint_in_searxng_service(searxng: dict) -> None:
    """代理值只经部署 env 注入：searxng 服务定义零硬编码代理地址——无
    host.docker.internal、无 7892 端口（本机核验所用的代理具体地址/端口
    绝不入 compose）。host.docker.internal 在 api 服务有既有合法用途
    （extra_hosts host-gateway），故断言锚定 searxng 服务面；livekit UDP
    端口段 7882-7892 是无关既有事实（范围端点形态），全文件只禁「:7892」
    端口直缀形态。"""
    blob = json.dumps(searxng, ensure_ascii=False)
    assert "host.docker.internal" not in blob
    assert "7892" not in blob
    assert ":7892" not in COMPOSE_FILE.read_text(encoding="utf-8")


# ---------------------------------------------------------------- settings.yml


def test_settings_inherit_upstream_defaults(settings: dict) -> None:
    """use_default_settings: true——只覆盖私有实例必需最小键，其余继承上游。"""
    assert settings["use_default_settings"] is True


def test_settings_enable_html_and_json_formats(settings: dict) -> None:
    """formats 必须显式含 html+json：官方默认只 html，JSON API 未启用即 403
    （json 是 CloudWebProvider 可用性硬前提）。"""
    formats = settings["search"]["formats"]
    assert "html" in formats and "json" in formats


def test_settings_private_instance_semantics(settings: dict) -> None:
    """私有本地实例：limiter false（bot 防护面向公网部署）、public_instance false。"""
    assert settings["server"]["limiter"] is False
    assert settings["server"]["public_instance"] is False


def test_secret_fallback_literals_match_across_files(searxng: dict, settings: dict) -> None:
    """漂移锁：compose 插值默认占位 == settings.yml secret_key 回退——同一
    字面量两处声明，任一单边修改即破坏 fallback 链（此锁强制成对修改）。"""
    compose_text = str(searxng["environment"]["SEARXNG_SECRET"])
    match = re.search(r"\$\{SEARXNG_SECRET:-(.+)\}\}$", compose_text)
    assert match, f"插值默认形态漂移: {compose_text}"
    assert match.group(1) == settings["server"]["secret_key"] == SEARXNG_FALLBACK_SECRET


# ---------------------------------------------------------------- api 接线槽位


def test_api_search_endpoint_slot_stays_empty_by_default(compose: dict) -> None:
    """api SEARCH_CLOUD_ENDPOINT 默认恒空：fail-closed（providers.py 三门判定
    缺一不启用 cloud-web，非搜索路径不隐式启用 context 出站）。"""
    assert compose["services"]["api"]["environment"]["SEARCH_CLOUD_ENDPOINT"] == "${AIOS_SEARCH_CLOUD_ENDPOINT:-}"


def test_compose_documents_internal_endpoint_recipe() -> None:
    """compose 文本含网络内端点接线配方（API 经 compose 网络访问 searxng，
    不经宿主端口）。"""
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    assert INTERNAL_ENDPOINT in text
    assert "AIOS_SEARCH_MODE=cloud" in text


def test_no_secret_shapes_in_repo_files() -> None:
    """compose 与 settings 两文件零 secret 形态（真实凭据只放部署 secret/.env）。"""
    for file in (COMPOSE_FILE, SETTINGS_FILE):
        text = file.read_text(encoding="utf-8")
        for pattern in SECRET_PATTERNS:
            assert pattern not in text, f"{file.name} 含 secret 形态 {pattern!r}"
