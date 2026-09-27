"""M14-159 公共 Web base path —— Nginx 边缘片段模板 fail-closed 校验。

覆盖矩阵（任务书第 4/3/5 条）：
1. 片段形态：只含 location 块（无 server/listen/server_name/root/alias/
   try_files、无正则 location）——include 进既有 443 站点后**结构上不可能**
   遮蔽既有 ndtool.cn 站点、既有 /api/v1/ 或既有静态资源；
2. 路由契约：`= /~!frp` WebSocket 升级透传到 frps 控制面 7000；
   `= /aios` 301 到 /aios/；`^~ /aios/api/` 尾斜杠 proxy_pass 仅剥 /aios
   （/aios/api/v1/foo → /api/v1/foo）；`^~ /aios/` 无 URI proxy_pass 原样
   保留完整路径给 Next；
3. 匹配语义仿真：按 nginx 精确优先/最长前缀实现一个最小匹配器，逐用例
   断言上游 URI 与 Host 改写；站点自身路径（/、/api/v1/*、静态）全部
   落空（不遮蔽）；
4. 跨工件契约：Nginx Host 改写的内部 vhost 名与 frpc 模板 customDomains
   精确对齐（frps 按 Host 路由）；上游恒为 loopback 7000/8080；
5. 公共构建契约锁定：Dockerfile/compose 注入 NEXT_PUBLIC_BASE_PATH（默认
   空 = 根路径构建不变），runbook 记录公共 Beta 入口
   （8443 公网不可达 → 443 /aios/）、NEXT_PUBLIC_BASE_PATH=/aios 与
   NEXT_PUBLIC_API_BASE_URL=https://ndtool.cn/aios 的精确组合。

边界：本文件只读模板/文档，不发任何网络请求、不启动任何容器、不渲染
真实站点配置。
"""
from __future__ import annotations

import re
from pathlib import Path

import tomllib

REPO = Path(__file__).resolve().parents[3]
EDGE_DIR = REPO / "infra" / "edge"
NGINX_BASE_PATH = EDGE_DIR / "nginx.public-base-path.example.conf"
FRPC_EXAMPLE = EDGE_DIR / "frpc.windows.toml.example"
WEB_DOCKERFILE = REPO / "apps" / "web" / "Dockerfile"
WEB_NEXT_CONFIG = REPO / "apps" / "web" / "next.config.ts"
WEB_API_TS = REPO / "apps" / "web" / "src" / "lib" / "api.ts"
ROOT_COMPOSE = REPO / "infra" / "docker-compose.yml"
RUNBOOK = REPO / "docs" / "PUBLIC_EDGE_DEPLOYMENT.md"

LOCATION_RE = re.compile(
    r"^location\s+(?P<modifier>=|\^~)\s*(?P<path>\S+)\s*\{$", re.MULTILINE
)
PROXY_PASS_RE = re.compile(
    r"proxy_pass\s+https?://(?P<authority>[^/\s]+)(?P<uri>[^;\s]*);"
)


def _text() -> str:
    return NGINX_BASE_PATH.read_text(encoding="utf-8")


def _stripped(text: str) -> str:
    """去注释行——shape 断言只看生效指令，注释里的示例不算数。"""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def _parse_locations(text: str) -> list[dict[str, str]]:
    """解析顶层 location 块（modifier / path / body）。"""
    blocks: list[dict[str, str]] = []
    for match in LOCATION_RE.finditer(text):
        body_start = match.end()
        depth, pos = 1, body_start
        while depth and pos < len(text):
            if text[pos] == "{":
                depth += 1
            elif text[pos] == "}":
                depth -= 1
            pos += 1
        assert depth == 0, f"location {match.group('path')} 花括号不闭合"
        blocks.append(
            {
                "modifier": match.group("modifier"),
                "path": match.group("path"),
                "body": text[body_start : pos - 1],
            }
        )
    return blocks


def _match_location(blocks: list[dict[str, str]], uri: str) -> dict[str, str] | None:
    """nginx 匹配语义最小实现：精确（=）优先，其次最长 ^~ 前缀。"""
    for block in blocks:
        if block["modifier"] == "=" and uri == block["path"]:
            return block
    matches = [b for b in blocks if b["modifier"] == "^~" and uri.startswith(b["path"])]
    return max(matches, key=lambda b: len(b["path"])) if matches else None


def _proxy_target(block: dict[str, str]) -> tuple[str, str]:
    match = PROXY_PASS_RE.search(block["body"])
    assert match is not None, f"{block['path']} 缺 proxy_pass"
    return match.group("authority"), match.group("uri")


def _forwarded_uri(block: dict[str, str], uri: str) -> str:
    """nginx URI 语义：proxy_pass 带 URI（尾斜杠形态）则替换被匹配前缀，
    不带 URI 则原样透传完整请求路径。查询串由 nginx 自动保留。"""
    _, uri_part = _proxy_target(block)
    if uri_part == "":
        return uri
    return uri_part + uri[len(block["path"]) :]


def _host_header(block: dict[str, str]) -> str:
    match = re.search(r"proxy_set_header\s+Host\s+(\S+);", block["body"])
    assert match is not None, f"{block['path']} 缺 Host 改写"
    return match.group(1)


# ---------------------------------------------------------------- 片段形态：不可遮蔽既有站点


def test_template_file_exists_in_edge_dir() -> None:
    assert NGINX_BASE_PATH.is_file(), "infra/edge/nginx.public-base-path.example.conf 缺失"


def test_fragment_is_location_only_cannot_shadow_host_site() -> None:
    """只允许 location 块：无 server/listen/server_name/静态指令/正则——
    include 进既有 443 server 后不可能接管站点根、既有 API 或静态资源。"""
    stripped = _stripped(_text())
    for banned in (
        "server_name",
        "listen ",
        "server {",
        "root ",
        "alias ",
        "try_files",
        "rewrite ",
    ):
        assert banned not in stripped, f"片段不得含 server 级/静态指令: {banned}"
    assert not re.search(r"location\s+(~\*?)\s", stripped), "片段不得引入正则 location（^~ 语义可被正则抢占破坏）"
    for block in _parse_locations(stripped):
        assert block["path"] not in (
            "/", "/api", "/api/", "/api/v1", "/api/v1/",
            "/static", "/static/", "/favicon.ico",
        ), f"location {block['path']} 会遮蔽既有站点路径"


def test_fragment_location_inventory_and_order() -> None:
    """声明面恰为四个 location，顺序与任务书一致（= /~!frp → = /aios →
    ^~ /aios/api/ → ^~ /aios/；语义上精确/最长前缀本就无歧义，锁顺序便于审查）。"""
    signatures = [
        (b["modifier"], b["path"]) for b in _parse_locations(_stripped(_text()))
    ]
    assert signatures == [
        ("=", "/~!frp"),
        ("=", "/aios"),
        ("^~", "/aios/api/"),
        ("^~", "/aios/"),
    ]


# ---------------------------------------------------------------- 路由契约


def test_frp_websocket_entry_preserved() -> None:
    """= /~!frp：WebSocket 升级透传到 frps 控制面 loopback 7000。"""
    block = _match_location(_parse_locations(_stripped(_text())), "/~!frp")
    assert block is not None, "缺 = /~!frp（frpc WebSocket 入口，既有行为）"
    authority, _ = _proxy_target(block)
    assert authority == "127.0.0.1:7000"
    assert "proxy_http_version 1.1;" in block["body"]
    assert "proxy_set_header Upgrade $http_upgrade;" in block["body"]
    assert 'proxy_set_header Connection "upgrade";' in block["body"]


def test_bare_aios_redirects_301_to_slash_form() -> None:
    block = _match_location(_parse_locations(_stripped(_text())), "/aios")
    assert block is not None, "缺 = /aios（裸入口 301）"
    assert re.search(r"return\s+301\s+/aios/;", block["body"]), "必须 301 到 /aios/"


def test_aios_api_prefix_maps_to_upstream_api() -> None:
    """^~ /aios/api/：尾斜杠 proxy_pass 只剥 /aios 一段，Host 改写内部 API vhost。"""
    blocks = _parse_locations(_stripped(_text()))
    block = _match_location(blocks, "/aios/api/v1/papers")
    assert block is not None and block["path"] == "/aios/api/"
    authority, uri_part = _proxy_target(block)
    assert authority == "127.0.0.1:8080"
    assert uri_part == "/api/", "proxy_pass 必须带尾斜杠 URI（.../api/）"
    assert _host_header(block) == "api.internal.aios"
    # 逐用例：公共 API 契约 https://ndtool.cn/aios/api/v1/... → /api/v1/...
    for public, upstream in (
        ("/aios/api/v1/papers", "/api/v1/papers"),
        ("/aios/api/v1/auth/me", "/api/v1/auth/me"),
        ("/aios/api/v1/exams/e1/answers", "/api/v1/exams/e1/answers"),
        ("/aios/api/v1/voice/token", "/api/v1/voice/token"),
        ("/aios/api/v1/audit?limit=100", "/api/v1/audit?limit=100"),
    ):
        assert _forwarded_uri(block, public) == upstream, f"{public} 映射错误"


def test_aios_web_prefix_preserves_full_path_for_next() -> None:
    """^~ /aios/：proxy_pass 无 URI → 完整 /aios/... 原样交给 Next（basePath 构建）。"""
    blocks = _parse_locations(_stripped(_text()))
    block = _match_location(blocks, "/aios/login")
    assert block is not None and block["path"] == "/aios/"
    authority, uri_part = _proxy_target(block)
    assert authority == "127.0.0.1:8080"
    assert uri_part == "", "Web 路由的 proxy_pass 不得带 URI（会截断 /aios 前缀）"
    assert _host_header(block) == "app.internal.aios"
    for public, forwarded in (
        ("/aios/", "/aios/"),
        ("/aios/login", "/aios/login"),
        ("/aios/_next/static/chunks/main.js", "/aios/_next/static/chunks/main.js"),
        ("/aios/exam", "/aios/exam"),
    ):
        assert _forwarded_uri(block, public) == forwarded, f"{public} 透传错误"


def test_api_prefix_wins_over_web_prefix() -> None:
    """最长前缀语义：/aios/api/* 永远进 API 路由，不会被 /aios/ 抢占。"""
    blocks = _parse_locations(_stripped(_text()))
    for uri in ("/aios/api/v1/health", "/aios/api/"):
        assert _match_location(blocks, uri)["path"] == "/aios/api/"


def test_host_site_paths_fall_through_unshadowed() -> None:
    """既有站点路径不被任何片段 location 接管（不遮蔽 ndtool.cn 站点/既有
    /api/v1//静态资源——这些由既有 server 自己的 location 继续服务）。"""
    blocks = _parse_locations(_stripped(_text()))
    for uri in (
        "/",
        "/index.html",
        "/api/v1/papers",
        "/api/v1/",
        "/static/app.js",
        "/favicon.ico",
        "/aios-extra",  # 非 /aios/ 前缀（无尾斜杠裸串仅 = /aios 精确命中）
    ):
        assert _match_location(blocks, uri) is None, f"{uri} 被片段遮蔽"


def test_upstreams_are_loopback_only() -> None:
    for block in _parse_locations(_stripped(_text())):
        if "proxy_pass" not in block["body"]:
            continue  # 301 跳转块无上游
        authority, _ = _proxy_target(block)
        assert authority in {"127.0.0.1:7000", "127.0.0.1:8080"}, (
            f"{block['path']} 上游越界: {authority}"
        )


# ---------------------------------------------------------------- 跨工件契约


def test_nginx_host_rewrites_match_frpc_internal_vhosts() -> None:
    """Nginx Host 改写的内部 vhost 名必须落在 frpc customDomains 内
    （frps vhost 按 Host 精确路由到家机代理）。"""
    hosts = {
        _host_header(b)
        for b in _parse_locations(_stripped(_text()))
        if b["path"].startswith("/aios/") and "proxy_pass" in b["body"]
    }
    frpc = tomllib.loads(FRPC_EXAMPLE.read_text(encoding="utf-8"))
    domains = {d for p in frpc["proxies"] for d in p["customDomains"]}
    assert hosts == {"app.internal.aios", "api.internal.aios"}
    assert hosts <= domains, f"frpc customDomains 缺内部 vhost: {hosts - domains}"


# ---------------------------------------------------------------- 公共构建契约（任务书第 3/5 条）


def test_web_build_wires_validated_base_path() -> None:
    """Dockerfile 构建期注入（默认空 = 根路径构建不变）；next.config 经
    校验器接线 basePath；api.ts 登录跳转显式带前缀（无双重叠加）。"""
    dockerfile = WEB_DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^ARG NEXT_PUBLIC_BASE_PATH=$", dockerfile, re.MULTILINE), (
        "ARG NEXT_PUBLIC_BASE_PATH 必须默认空（根路径构建不变）"
    )
    assert "ENV NEXT_PUBLIC_BASE_PATH=" in dockerfile
    next_config = WEB_NEXT_CONFIG.read_text(encoding="utf-8")
    assert "normalizeBasePath(process.env.NEXT_PUBLIC_BASE_PATH)" in next_config
    assert "basePath" in next_config
    api_ts = WEB_API_TS.read_text(encoding="utf-8")
    assert "${BASE_PATH}/login" in api_ts, "登录跳转必须复用校验后的 BASE_PATH（防双重前缀）"
    assert 'window.location.href = "/login"' not in api_ts, "根路径字面量跳转不得残留"


def test_compose_passes_base_path_build_arg() -> None:
    compose = ROOT_COMPOSE.read_text(encoding="utf-8")
    assert "NEXT_PUBLIC_BASE_PATH: ${AIOS_PUBLIC_WEB_BASE_PATH:-}" in compose, (
        "compose 必须把 AIOS_PUBLIC_WEB_BASE_PATH 透传为构建 ARG（默认空）"
    )


def test_runbook_documents_public_beta_entry_contract() -> None:
    """runbook §3E：8443 公网不可达 → 443 /aios/ 为公共 Beta 入口；
    Web/API 精确 URL 与构建 env 组合落档。"""
    text = RUNBOOK.read_text(encoding="utf-8")
    for needle in (
        "8443",
        "https://ndtool.cn/aios/",
        "https://ndtool.cn/aios/api/v1/",
        "NEXT_PUBLIC_BASE_PATH=/aios",
        "NEXT_PUBLIC_API_BASE_URL=https://ndtool.cn/aios",
        "nginx.public-base-path.example.conf",
        "app.internal.aios",
        "api.internal.aios",
    ):
        assert needle in text, f"runbook §3E 缺公共契约要素: {needle}"
