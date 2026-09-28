r"""M14-159/M14-161/M14-163/M14-176 公共 Web base path —— Nginx 边缘片段模板 fail-closed 校验。

覆盖矩阵（任务书第 4/3/5 条 + M14-161 健康路由 + M14-163 安全头）：
1. 片段形态：只含 location 块（无 server/listen/server_name/root/alias/
   try_files、无正则 location）——include 进既有 443 站点后**结构上不可能**
   遮蔽既有 ndtool.cn 站点、既有 /api/v1/ 或既有静态资源；
2. 路由契约：`= /~!frp` WebSocket 升级透传到 frps 控制面 7000；
   `= /aios` canonical 入口直接代理到 Next（零重定向）；`= /aios/`
   301 归一化到 /aios（恰好一跳——R3 修正：Next 16 basePath 构建默认
   trailingSlash=false，对 /aios/ 回 308 /aios，边缘不得反向 301 构成
   互逆重定向环）；`= /aios/health`（M14-161）精确转发 frps API vhost
   + 本地 API /health（公共 preflight 的 canonical 健康端点）；
   `^~ /aios/api/` 尾斜杠 proxy_pass 仅剥 /aios
   （/aios/api/v1/foo → /api/v1/foo）；`^~ /aios/` 无 URI proxy_pass 原样
   保留完整路径给 Next；
3. 匹配语义仿真：按 nginx 精确优先/最长前缀实现一个最小匹配器，逐用例
   断言上游 URI 与 Host 改写；站点自身路径（/、/api/v1/*、静态）全部
   落空（不遮蔽）；`/aios/health` 精确命中且邻近 URI（/aios/healthz、
   /aios/health/x、/aios/api/health）仍走各自最长前缀；
4. 跨工件契约：Nginx Host 改写的内部 vhost 名与 frpc 模板 customDomains
   精确对齐（frps 按 Host 路由）；上游恒为 loopback 7000/8080；
5. 公共构建契约锁定：Dockerfile/compose 注入 NEXT_PUBLIC_BASE_PATH（默认
   空 = 根路径构建不变），runbook 记录公共 Beta 入口
   （8443 公网不可达 → 443 /aios，无尾斜杠 canonical；/aios/ 经边缘
   301 归一化）、NEXT_PUBLIC_BASE_PATH=/aios 与
   NEXT_PUBLIC_API_BASE_URL=https://ndtool.cn/aios 的精确组合，
   以及 M14-161 公共 preflight 契约（/aios/health 精确健康路由）；
6. M14-163 安全响应头契约：五个公共浏览器面 location（= /aios、
   = /aios/、= /aios/health、^~ /aios/api/、^~ /aios/）各显式恒定
   三头（HSTS max-age=31536000 / X-Content-Type-Options nosniff /
   Referrer-Policy strict-origin-when-cross-origin）且全部带 always；
   frp 隧道入口不加；**边缘零 CORS**（无任何 Access-Control-*）且
   不引入 CSP/框架策略头；runbook 落档 AIOS_CORS_ORIGINS 显式包含
   公共 origin（边缘只透传）与受控 credentials file 纪律。
7. M14-176 VPS 本地下载静态路由：`= /aios/download-manifest.json`
   （Web 端 download-manifest.ts 的同源取数 URL）与 `^~ /android/`
   （清单可信条目的根相对 APK URL 前缀）在 VPS 本地
   `/var/www/aios-downloads/` 磁盘终结（alias，无 proxy_pass——不经
   frp 家机隧道；stage_download.py 产物的上传目的地）；强制内容类型
   （types{} 清空扩展映射 + default_type：manifest 一律 application/json、
   /android/ 一律 APK MIME——混入杂散文件也以下载面而非可渲染页面）、
   manifest `no-store`、APK 有界公共缓存 `public, max-age=3600`（缓存
   头只允许出现在这两个 location）、显式 `autoindex off` 且无 index/
   try_files（无目录暴露：缺文件 404、目录 URI 拒绝，绝无列表）、恒定
   三安全头；alias 只允许出现在这两个 location（其余六个 location 仍
   禁静态指令）；`/android/` 还有 **nginx 层 .apk 门禁**（R1，Codex
   review：`if ($uri !~* \.apk$) { return 404; }`——return 是 if 内的
   安全用法，rewrite 阶段终结先于静态处理）——规范化 URI 不以 .apk
   结尾（大小写不敏感）一律 404（notes.txt/foo.apk.txt/foo.html/目录
   URI 在进静态处理器前被拒，不依赖上传目录纪律；.APK/.Apk 大小写
   变体放行）；匹配边界：清单 URI 精确命中（不落 `^~ /aios/` 的
   Next 代理 404）、`/android/` 用 ^~ 阻止宿主正则/静态 location 抢占
   /android/*（普通前缀会输给宿主 regex）、裸 `/android`（无尾斜杠）/
   `/androidx`/`/download-manifest.json`（无 /aios 前缀）不被片段接管。

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
    """只允许 location 块：无 server/listen/server_name/root/try_files/
    rewrite/正则——include 进既有 443 server 后不可能接管站点根、既有
    API 或静态资源。M14-176 唯一例外：alias 允许且仅允许出现在两个
    下载静态 location（其余 location 仍禁，静态目录暴露面被钉死在
    /var/www/aios-downloads/ 的两个精确路由内）。"""
    stripped = _stripped(_text())
    for banned in (
        "server_name",
        "listen ",
        "server {",
        "root ",
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
        if (block["modifier"], block["path"]) in M14_176_DOWNLOAD_LOCATIONS:
            continue
        assert "alias " not in block["body"], (
            f"{block['path']} 不得含 alias（静态服务只允许在 M14-176 下载 location 内）"
        )


def test_fragment_location_inventory_and_order() -> None:
    """声明面恰为八个 location，顺序与 R3/M14-161/M14-176 任务书一致
    （= /~!frp → = /aios → = /aios/ → = /aios/health → ^~ /aios/api/ →
    ^~ /aios/ → = /aios/download-manifest.json → ^~ /android/；语义上
    精确/最长前缀本就无歧义，锁顺序便于审查）。"""
    signatures = [
        (b["modifier"], b["path"]) for b in _parse_locations(_stripped(_text()))
    ]
    assert signatures == [
        ("=", "/~!frp"),
        ("=", "/aios"),
        ("=", "/aios/"),
        ("=", "/aios/health"),
        ("^~", "/aios/api/"),
        ("^~", "/aios/"),
        ("=", "/aios/download-manifest.json"),
        ("^~", "/android/"),
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


def test_bare_aios_proxies_to_app_upstream_without_redirect() -> None:
    """R3：= /aios 是 canonical 入口（无尾斜杠），直接代理到 Next，零重定向
    ——Next（basePath=/aios 构建，默认 trailingSlash=false）在 /aios 直接
    渲染根页面；此处若 301 到 /aios/ 会与 Next 对 /aios/ 的 308 互逆成环。"""
    blocks = _parse_locations(_stripped(_text()))
    block = _match_location(blocks, "/aios")
    assert block is not None and block["path"] == "/aios", "缺 = /aios（canonical 入口直接代理）"
    assert "return" not in block["body"], "= /aios 不得再重定向（曾致公共重定向环）"
    authority, uri_part = _proxy_target(block)
    assert authority == "127.0.0.1:8080"
    assert uri_part == "", "proxy_pass 不得带 URI（完整 /aios 原样交给 Next）"
    assert _host_header(block) == "app.internal.aios"
    assert "proxy_http_version 1.1;" in block["body"]
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in block["body"]
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in block["body"]
    assert _forwarded_uri(block, "/aios") == "/aios"


def test_slash_form_aios_redirects_301_once_to_bare_aios() -> None:
    """R3：= /aios/ 唯一行为是 301 → /aios（斜杠形态单向归一化到 canonical
    无尾斜杠入口，恰好一跳；随后 /aios 由上一测试的直接代理到达 Next
    一次）。精确匹配优先于 ^~ /aios/ 前缀。"""
    blocks = _parse_locations(_stripped(_text()))
    block = _match_location(blocks, "/aios/")
    assert block is not None and block["path"] == "/aios/", "缺 = /aios/（斜杠归一化）"
    assert re.search(r"return\s+301\s+/aios;", block["body"]), "必须 301 到 /aios（恰好一跳归一化）"
    assert "proxy_pass" not in block["body"]


def test_no_mutually_inverse_redirects() -> None:
    """R3 回归锁：边缘不得存在互为反向的重定向对。带 /aios 前缀的
    location 里唯一允许的 return 重定向恰为 = /aios/ → /aios；目标
    /aios 自身必须无 return（直接代理）——若重新引入 = /aios 301 →
    /aios/，会与 Next 对 /aios/ 的 308 → /aios 构成 /aios/ → /aios →
    /aios/ 的公共重定向环。"""
    blocks = _parse_locations(_stripped(_text()))
    redirects: dict[str, str] = {}
    for block in blocks:
        if not block["path"].startswith("/aios"):
            continue
        match = re.search(r"return\s+(\d{3})\s+(\S+);", block["body"])
        if match:
            redirects[block["path"]] = match.group(2)
        else:
            assert "proxy_pass" in block["body"] or (
                (block["modifier"], block["path"]) in M14_176_DOWNLOAD_LOCATIONS
                and "alias " in block["body"]
            ), (
                f"{block['path']} 非重定向块就必须 proxy_pass"
                "（唯一例外：M14-176 下载清单静态 alias 路由）"
            )
    assert redirects == {"/aios/": "/aios"}, "唯一允许的重定向是 /aios/ 301 归一化到 /aios"
    for source, target in redirects.items():
        assert target not in redirects, (
            f"{source} → {target} 与 {target} 上的重定向互为反向（重定向环）"
        )


def test_aios_health_exact_route_forwards_to_local_api_health() -> None:
    """M14-161：`= /aios/health` 精确转发 frps API vhost（Host 改写
    api.internal.aios）+ 本地 API `/health`（proxy_pass URI 显式 /health，
    精确匹配下整串替换），保留 X-Forwarded-* 与 API 前缀路由一致——
    公共 preflight 在 /aios 拓扑的 canonical 健康探测端点。"""
    blocks = _parse_locations(_stripped(_text()))
    block = _match_location(blocks, "/aios/health")
    assert block is not None and block["path"] == "/aios/health", "缺 = /aios/health 精确健康路由"
    assert "return" not in block["body"], "健康路由直接代理，不重定向"
    authority, uri_part = _proxy_target(block)
    assert authority == "127.0.0.1:8080", "上游必须是 frps vhost loopback 8080"
    assert uri_part == "/health", "上游 URI 必须是本地 API /health（非 /api/health）"
    assert _host_header(block) == "api.internal.aios"
    assert "proxy_http_version 1.1;" in block["body"]
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in block["body"]
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in block["body"]
    assert _forwarded_uri(block, "/aios/health") == "/health"


def test_aios_health_exact_match_wins_without_shadowing() -> None:
    """M14-161：精确匹配优先于 `^~ /aios/`（Web）前缀；同时不吞邻近 URI——
    /aios/healthz、/aios/health/x 仍走 Web 前缀，/aios/api/health 仍走
    API 前缀，站点自身 /health 不受影响。"""
    blocks = _parse_locations(_stripped(_text()))
    assert _match_location(blocks, "/aios/health")["path"] == "/aios/health", (
        "整串 /aios/health 必须被精确路由抢先（否则落 Web 前缀 404）"
    )
    assert _match_location(blocks, "/aios/healthz")["path"] == "/aios/"
    assert _match_location(blocks, "/aios/health/status")["path"] == "/aios/"
    assert _match_location(blocks, "/aios/api/health")["path"] == "/aios/api/"
    assert _match_location(blocks, "/health") is None, "站点自身 /health 不被片段接管"


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
        ("/aios/api/v1/foo", "/api/v1/foo"),
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
            continue  # = /aios/ 归一化块无上游
        authority, _ = _proxy_target(block)
        assert authority in {"127.0.0.1:7000", "127.0.0.1:8080"}, (
            f"{block['path']} 上游越界: {authority}"
        )


# ------------------------------------- M14-163 公共边缘安全响应头契约

SECURITY_HEADER_DIRECTIVES = (
    'add_header Strict-Transport-Security "max-age=31536000" always;',
    'add_header X-Content-Type-Options "nosniff" always;',
    'add_header Referrer-Policy "strict-origin-when-cross-origin" always;',
)

M14_163_PUBLIC_LOCATIONS = {
    ("=", "/aios"),
    ("=", "/aios/"),
    ("=", "/aios/health"),
    ("^~", "/aios/api/"),
    ("^~", "/aios/"),
}

# M14-176：两个下载静态 location 同属公共浏览器面（/download 页与
# 下载器直接命中），同享 M14-163 恒定三安全头纪律。
M14_176_DOWNLOAD_LOCATIONS = {
    ("=", "/aios/download-manifest.json"),
    ("^~", "/android/"),
}

ALL_PUBLIC_BROWSER_LOCATIONS = M14_163_PUBLIC_LOCATIONS | M14_176_DOWNLOAD_LOCATIONS


def test_public_locations_carry_constant_security_headers_always() -> None:
    """M14-163/M14-176：七个公共浏览器面 location（五路由 + 两下载静态
    路由）各显式恒定三安全头——`always` 保证 301/4xx 等非 2xx 响应
    同样携带（preflight 对任何状态码判定，下载面 404 也不例外）；
    逐 location 显式不依赖宿主 server 级继承（nginx 规则：location 内
    出现任何 add_header 即令 server 级全部失效）；HSTS
    max-age=31536000 ≥ preflight 门槛 15552000。"""
    blocks = _parse_locations(_stripped(_text()))
    seen: set[tuple[str, str]] = set()
    for block in blocks:
        signature = (block["modifier"], block["path"])
        if signature not in ALL_PUBLIC_BROWSER_LOCATIONS:
            continue
        seen.add(signature)
        for directive in SECURITY_HEADER_DIRECTIVES:
            assert directive in block["body"], (
                f"{block['path']} 缺安全头指令: {directive}"
            )
    assert seen == ALL_PUBLIC_BROWSER_LOCATIONS, (
        "七个公共浏览器面 location 必须全部携带三安全头（缺一即 preflight FAIL 面）"
    )


def test_frp_tunnel_entry_has_no_security_headers() -> None:
    """① frpc WebSocket 入口是隧道控制面、非浏览器响应面——不加
    安全头（锁定"不加"本身是 M14-163 设计的一部分，防误扩散）。"""
    block = _match_location(_parse_locations(_stripped(_text())), "/~!frp")
    assert block is not None
    assert "add_header" not in block["body"], "frp 隧道入口不得携带浏览器面安全头"


def test_edge_adds_no_cors_and_no_content_policy_headers() -> None:
    """M14-163 边界纪律：边缘零 CORS（绝无任何 Access-Control-* 头——
    CORS 判定权在家机 API 的 AIOS_CORS_ORIGINS，边缘只透传）；不引入
    CSP/X-Frame-Options 等内容策略头（不放宽也不收紧，宿主与上游
    保持权威）。"""
    stripped = _stripped(_text())
    for block in _parse_locations(stripped):
        for line in block["body"].splitlines():
            directive = line.strip()
            if not directive.startswith("add_header"):
                continue
            for banned in (
                "Access-Control",
                "Content-Security-Policy",
                "X-Frame-Options",
                "X-Content-Type-Policy",
            ):
                assert banned not in directive, (
                    f"{block['path']} 的边缘 add_header 越界（{banned}）: {directive}"
                )


def test_every_add_header_carries_always_flag() -> None:
    """M14-163：片段内所有 add_header 必须带 always——漏标会使 301/4xx
    响应丢头（preflight 对非 2xx 一样判定安全头）。"""
    stripped = _stripped(_text())
    for block in _parse_locations(stripped):
        for line in block["body"].splitlines():
            directive = line.strip()
            if directive.startswith("add_header"):
                assert directive.endswith("always;"), (
                    f"{block['path']} 的 add_header 缺 always 标志: {directive}"
                )


# ------------------------------------- M14-176 VPS 本地下载静态路由

DOWNLOADS_FS_ROOT = "/var/www/aios-downloads"
APK_MIME = "application/vnd.android.package-archive"


def _download_block(uri: str) -> dict[str, str]:
    """取 uri 命中的下载 location（并断言它确属 M14-176 两个路由之一）。"""
    block = _match_location(_parse_locations(_stripped(_text())), uri)
    assert block is not None, f"{uri} 未被片段路由（M14-176 下载路由缺失）"
    assert (block["modifier"], block["path"]) in M14_176_DOWNLOAD_LOCATIONS, (
        f"{uri} 必须命中 M14-176 下载路由，实际命中 {block['path']}"
    )
    return block


def test_download_manifest_route_serves_staged_manifest_from_vps_disk() -> None:
    """M14-176：`= /aios/download-manifest.json` 是 Web 端
    download-manifest.ts 的同源取数 URL（basePath 前缀拼接），在 VPS
    本地磁盘终结：alias 精确指向 stage_download.py 产出的
    `<staging>/manifest.json` 的上传位 `<root>/manifest.json`；无
    proxy_pass（不经 frp 家机隧道——清单更新不需要家机/Web 镜像参与）、
    无 return（直接静态服务）；types{} 清空扩展映射 + default_type
    强制 application/json；Cache-Control no-store（运行时事实来源
    绝不缓存——web 端 fetch 亦 no-store，双侧一致）。"""
    block = _download_block("/aios/download-manifest.json")
    assert block["modifier"] == "=", "清单路由必须精确匹配（否则落 ^~ /aios/ 的 Next 代理 404）"
    assert "proxy_pass" not in block["body"], "清单必须 VPS 本地静态服务，不经家机隧道"
    assert "return" not in block["body"], "清单路由直接静态服务，不重定向"
    alias = re.search(r"alias\s+(\S+);", block["body"])
    assert alias is not None, "缺 alias"
    assert alias.group(1) == f"{DOWNLOADS_FS_ROOT}/manifest.json", (
        "alias 必须钉死 stage_download.py manifest 的上传目的地"
    )
    assert re.search(r"types\s*\{\s*\}", block["body"]), "必须清空扩展映射（types{}）"
    assert "default_type application/json;" in block["body"]
    assert 'add_header Cache-Control "no-store" always;' in block["body"]


def test_android_prefix_serves_only_staged_apks_from_vps_disk() -> None:
    """M14-176：`^~ /android/` 只服务 stage_download.py staged APK 的
    上传目录 `<root>/android/`（manifest 可信条目 URL 恒为根相对
    /android/<file>，与 verify_artifact/web 端 ENTRY_URL_RE 同语义）；
    无 proxy_pass；types{} + default_type 强制 APK MIME——即便混入
    杂散文件也以不可渲染的下载面呈现（fail-closed）。"""
    block = _download_block("/android/ai-learning-os-0.1.0-release-signed.apk")
    assert block["modifier"] == "^~", (
        "必须 ^~ 前缀：普通前缀 location 会输给宿主正则/静态 location"
        "（如 ~ \\.apk$）；^~ 在最长前缀命中时阻止正则抢占，"
        "/android/* 保证由本路由服务"
    )
    assert "proxy_pass" not in block["body"], "APK 必须 VPS 本地静态服务，不经家机隧道"
    alias = re.search(r"alias\s+(\S+);", block["body"])
    assert alias is not None, "缺 alias"
    assert alias.group(1) == f"{DOWNLOADS_FS_ROOT}/android/", (
        "alias 必须钉死 staged APK 上传目录（目录形态带尾斜杠与 location 配对）"
    )
    assert re.search(r"types\s*\{\s*\}", block["body"]), "必须清空扩展映射（types{}）"
    assert f"default_type {APK_MIME};" in block["body"]


def test_download_routes_match_semantics_and_boundaries() -> None:
    """匹配语义与边界：清单 URI 精确命中（优先于 `^~ /aios/`），邻近
    URI（多一个字符/多一段）仍走 Web 前缀不受影响；`/android/<file>`
    命中下载前缀；反向边界——裸 /android（无尾斜杠）、/androidx、
    大小写变体 /Android/、无 /aios 前缀的 /download-manifest.json、
    清单 harmony 频道的 /harmony/* URL 一律不被片段接管（宿主路径
    不变；harmony 不在本切片服务面内，web 端也不会信任其条目）。"""
    blocks = _parse_locations(_stripped(_text()))
    assert _match_location(blocks, "/aios/download-manifest.json")["path"] == (
        "/aios/download-manifest.json"
    ), "清单 URI 必须被精确路由抢先（否则落 Web 前缀 404）"
    for uri in ("/aios/download-manifest.jsonx", "/aios/download-manifest.json/"):
        assert _match_location(blocks, uri)["path"] == "/aios/", (
            f"{uri} 应仍走 Web 前缀（精确匹配只接整串）"
        )
    assert _match_location(
        blocks, "/android/ai-learning-os-0.1.0-release-signed.apk"
    )["path"] == "/android/"
    for uri in (
        "/android",
        "/androidx",
        "/Android/",
        "/download-manifest.json",
        "/harmony/ai-learning-os-0.1.0-signed.hap",
    ):
        assert _match_location(blocks, uri) is None, f"{uri} 被片段遮蔽"


def test_cache_policy_confined_to_download_locations() -> None:
    """缓存策略只存在于两个下载 location：manifest 恰为 no-store、
    APK 恰为 public, max-age=3600（有界公共缓存——文件名带版本且
    stage_download.py 拒绝覆盖已存在目标，URL 内容不可变）；既有
    五路由与 frp 入口保持零 Cache-Control（上游/宿主语义权威，边缘
    不越权改代理路由的缓存行为）。"""
    for block in _parse_locations(_stripped(_text())):
        policies = re.findall(
            r'add_header\s+Cache-Control\s+"([^"]+)"\s+always;', block["body"]
        )
        if (block["modifier"], block["path"]) in M14_176_DOWNLOAD_LOCATIONS:
            expected = (
                "no-store"
                if block["path"] == "/aios/download-manifest.json"
                else "public, max-age=3600"
            )
            assert policies == [expected], (
                f"{block['path']} 缓存策略必须恰为 {expected}: {policies}"
            )
        else:
            assert not policies, f"{block['path']} 不得引入 Cache-Control"


def test_no_autoindex_no_index_no_directory_exposure() -> None:
    """无目录暴露（纵深防御）：目录 URI 已由 .apk 门禁在 rewrite 阶段
    确定性 404（见上一测试），autoindex off + 无 index 是第二道——防
    宿主 server 级 autoindex on 继承、防磁盘上意外出现的目录（如恰好
    命名 x.apk 的目录）在静态处理器里给出列表/索引；无 try_files 兜底，
    缺文件 404，下载目录结构不可枚举。"""
    android = _download_block("/android/ai-learning-os-0.1.0-release-signed.apk")
    assert "autoindex off;" in android["body"], "下载前缀必须显式 autoindex off"
    for block in _parse_locations(_stripped(_text())):
        assert not re.search(r"^\s*index\s", block["body"], re.MULTILINE), (
            f"{block['path']} 不得有 index 指令（目录 URI 必须拒绝）"
        )
        assert not re.search(r"^\s*autoindex\s+on", block["body"], re.MULTILINE), (
            f"{block['path']} 不得开 autoindex"
        )


def test_android_prefix_apk_gate_pinned_and_fail_closed() -> None:
    r"""R1（Codex review）：/android/ 在 **nginx 层**强制只服务 .apk
    常规路径——`if ($uri !~* \.apk$) { return 404; }`（if 内 return 是
    nginx 认可的安全用法：rewrite 阶段终结请求，先于静态处理器，不触碰
    "if is evil" 的内容处理面）。规范化后的 $uri 不以 .apk 结尾（大小写
    不敏感）一律 404：上传目录纪律不再是唯一防线；目录 URI（含裸
    /android/）与尾斜杠形态也在此被拒（确定性 404）。本测试钉住指令
    形态 + 用模板里的同一正则做语义仿真。"""
    block = _download_block("/android/ai-learning-os-0.1.0-release-signed.apk")
    gate = re.search(
        r'if \(\$uri !~\* (?P<pattern>[^)\s]+)\)\s*\{\s*return\s+404;\s*\}',
        block["body"],
    )
    assert gate is not None, (
        "location /android/ 缺 .apk 门禁（if ($uri !~* ...) { return 404; }）"
    )
    assert gate.group("pattern") == r"\.apk$", (
        "门禁正则必须锚定 .apk 后缀（$），不得放宽为子串匹配"
    )
    apk_gate = re.compile(gate.group("pattern"), re.IGNORECASE)  # ~* = 大小写不敏感
    # 非法形态：rewrite 阶段 404，绝不到静态处理器（不依赖上传目录纪律）
    for uri in (
        "/android/notes.txt",       # Codex review 点名：非 .apk 文本
        "/android/foo.apk.txt",     # Codex review 点名：.apk 只在中间
        "/android/foo.html",        # Codex review 点名：可渲染页面形态
        "/android/",                # 裸前缀（目录 URI）
        "/android/foo.apk/",        # 尾斜杠（目录形态）
        "/android/manifest.json",   # 清单文件名也不得经此路由泄露
    ):
        assert apk_gate.search(uri) is None, f"{uri} 必须被 .apk 门禁 404"
    # 合法形态：.apk 大小写不敏感放行（随后由静态处理器按文件存在性 200/404）
    for uri in (
        "/android/ai-learning-os-0.1.0-release-signed.apk",
        "/android/foo.APK",
        "/android/foo.Apk",
    ):
        assert apk_gate.search(uri) is not None, (
            f"{uri} 的 .apk 大小写不敏感形态必须放行（~* 语义）"
        )


def test_download_locations_no_cors_no_credentials_no_tunnel() -> None:
    """下载静态路由零 CORS（同源取数——页面与 /android/<file> 同
    origin，边缘绝不代答 Access-Control-*）、零认证（无 auth_basic——
    公共下载就是匿名静态文件）、零隧道（无任何 proxy_ 指令——VPS
    本地磁盘终结，家机不承担下载流量）。"""
    for block in _parse_locations(_stripped(_text())):
        if (block["modifier"], block["path"]) not in M14_176_DOWNLOAD_LOCATIONS:
            continue
        assert "Access-Control" not in block["body"], "下载路由不得代答 CORS"
        assert not re.search(r"^\s*auth_basic", block["body"], re.MULTILINE), (
            "下载路由不得引入认证（公共匿名下载）"
        )
        assert not re.search(r"^\s*proxy_\w+", block["body"], re.MULTILINE), (
            "下载路由不得有任何 proxy 指令（VPS 本地终结，不经隧道）"
        )


# ---------------------------------------------------------------- 跨工件契约


def test_nginx_host_rewrites_match_frpc_internal_vhosts() -> None:
    """Nginx Host 改写的内部 vhost 名必须落在 frpc customDomains 内
    （frps vhost 按 Host 精确路由到家机代理）。"""
    hosts = {
        _host_header(b)
        for b in _parse_locations(_stripped(_text()))
        if (b["path"] == "/aios" or b["path"].startswith("/aios/"))
        and "proxy_pass" in b["body"]
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
    """runbook §3E：8443 公网不可达 → 443 /aios（无尾斜杠 canonical）为
    公共 Beta 入口，/aios/ 301 归一化到 /aios；Web/API 精确 URL 与构建
    env 组合落档；旧的带尾斜杠整 URL 入口形态不得再出现。M14-161：
    /aios/health 精确健康路由与公共 preflight base path 契约（探测
    /aios、/aios/health）落档 §3E/§9。"""
    text = RUNBOOK.read_text(encoding="utf-8")
    for needle in (
        "8443",
        "| 公共 Web 入口 | `https://ndtool.cn/aios`",
        "301 归一化到 `/aios`",
        "https://ndtool.cn/aios/api/v1/",
        "NEXT_PUBLIC_BASE_PATH=/aios",
        "NEXT_PUBLIC_API_BASE_URL=https://ndtool.cn/aios",
        "nginx.public-base-path.example.conf",
        "app.internal.aios",
        "api.internal.aios",
        # M14-161：健康路由 + preflight base path 契约
        "`= /aios/health`",
        "--app-url https://ndtool.cn/aios",
        "--api-url https://ndtool.cn/aios",
        "探测 `/aios/health`",
    ):
        assert needle in text, f"runbook §3E 缺公共契约要素: {needle}"
    assert "`https://ndtool.cn/aios/`" not in text, (
        "公共 Web 入口必须是无尾斜杠 https://ndtool.cn/aios"
        "（/aios/ 仅经边缘 301 归一化，不是 canonical 入口）"
    )


def test_runbook_documents_security_headers_and_cors_contract() -> None:
    """M14-163：runbook 落档安全头恒定三头（always/max-age=31536000/
    逐 location 显式）、AIOS_CORS_ORIGINS 显式包含公共 origin、边缘
    只透传零 CORS、受控 credentials file 纪律（真实凭据绝不入库）。"""
    text = RUNBOOK.read_text(encoding="utf-8")
    for needle in (
        # §3E 安全头契约
        "公共边缘安全响应头契约（M14-163）",
        "max-age=31536000",
        "always",
        "边缘零 CORS",
        # §8 CORS allowlist + 凭据纪律
        "公共拓扑 CORS allowlist（M14-163 定版）",
        "AIOS_CORS_ORIGINS=https://ndtool.cn",
        "边缘只透传",
        "仓库外受控文件",
        "Cookie 验收凭据纪律",
        # §9 前置条件
        "检查通过的前置条件（M14-163 口径）",
    ):
        assert needle in text, f"runbook 缺 M14-163 契约要素: {needle}"
