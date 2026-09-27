"""M14-160 Web 容器健康探测路径对齐 basePath 构建 —— fail-closed 契约。

背景（M14-159 生产切换 supervisor 实证）：`aios/web:m14-159-public-edge-beta`
的 `/aios` 与 `/aios/login` 均 200、Next Ready，但 Docker health 一直
unhealthy——compose web healthcheck 固定探测 `http://127.0.0.1:3000/`，
basePath=/aios 构建下 `/` 是 404。canonical 健康路径必须随构建形态走：
root 构建 → `/`，basePath 构建 → `/aios`。

覆盖矩阵（任务书第 7 条）：
1. Dockerfile ENV 推导：run stage 声明 `ARG NEXT_PUBLIC_BASE_PATH=`（默认
   空）并固化非敏感 runtime env
   `ENV AIOS_WEB_HEALTH_PATH=${NEXT_PUBLIC_BASE_PATH:-/}`；
2. compose healthcheck 使用该 runtime env：`CMD-SHELL` + `$$` 转义 →
   容器内 shell 展开 `$${AIOS_WEB_HEALTH_PATH:-/}`（宿主侧不插值）；
3. root 默认 `/`：空/未设 build arg → ENV=/ → 探测 URL 逐字等于
   M14-159 之前的固定命令（默认行为完全不变的回归锚）；
4. basePath `/aios`：build arg=/aios → ENV=/aios → 探测
   `http://127.0.0.1:3000/aios`；
5. 非法/其他路径 fail-closed：build arg 经 next.config.ts
   normalizeBasePath 白名单（恰为 空/`/aios`）构建期抛错、镜像不产出；
   即便绕过构建注入任意其他 ENV 值，展开结果也绝不在 canonical 探测
   URL 集内（不可能误报健康）；
6. 无 secret：新增面仅 `AIOS_WEB_HEALTH_PATH`/`NEXT_PUBLIC_BASE_PATH`
   非敏感名，探测上游恒 loopback 3000，无未转义宿主插值（注入面封死），
   compose web environment 不提供 override 入口（值唯一来源是镜像 ENV）。

边界：本文件只读 Dockerfile/compose/校验器源码，不发任何网络请求、
不启动任何容器、不渲染真实 Docker 资源。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
WEB_DOCKERFILE = REPO / "apps" / "web" / "Dockerfile"
ROOT_COMPOSE = REPO / "infra" / "docker-compose.yml"
BASE_PATH_TS = REPO / "apps" / "web" / "src" / "lib" / "base-path.ts"

HEALTH_ENV = "AIOS_WEB_HEALTH_PATH"
BUILD_ARG = "NEXT_PUBLIC_BASE_PATH"
# M14-159 之前的固定探测命令 —— root 构建展开结果必须与之逐字一致
LEGACY_ROOT_PROBE = "wget -q -O- http://127.0.0.1:3000/ >/dev/null 2>&1 || exit 1"
CANONICAL_PROBE_URLS = {"http://127.0.0.1:3000/", "http://127.0.0.1:3000/aios"}


def _dockerfile_text() -> str:
    return WEB_DOCKERFILE.read_text(encoding="utf-8")


def _run_stage(dockerfile: str) -> str:
    """提取 `FROM ... AS run` 及其后的全部文本（末 stage）。"""
    match = re.search(r"^FROM node:22-alpine AS run\s*$", dockerfile, re.MULTILINE)
    assert match is not None, "Dockerfile 缺 run stage"
    return dockerfile[match.start():]


def _normalize_base_path(raw: str | None) -> str:
    """与 apps/web/src/lib/base-path.ts 契约对齐的最小实现（构建期校验）。

    未设置/空串 → ""（根路径构建）；恰为 "/aios" → "/aios"；
    其他任何值 → 抛错（构建期 fail-closed，镜像不产出）。
    """
    if raw is None or raw == "":
        return ""
    if raw != "/aios":
        raise ValueError(
            'NEXT_PUBLIC_BASE_PATH 非法：唯一允许的非空值是 "/aios"'
        )
    return "/aios"


def _dockerfile_env_expand(build_arg: str | None) -> str:
    """BuildKit ENV `${VAR:-default}` 展开语义：未设置/空 → default。"""
    return build_arg if build_arg not in (None, "") else "/"


def _compose_probe_command() -> str:
    """compose web healthcheck 的 CMD-SHELL 命令原文（YAML 反序列化后）。"""
    compose = yaml.safe_load(ROOT_COMPOSE.read_text(encoding="utf-8"))
    test = compose["services"]["web"]["healthcheck"]["test"]
    assert test[0] == "CMD-SHELL", f"web healthcheck 必须保持 CMD-SHELL: {test}"
    return test[1]


def _render_compose_dollars(cmd: str) -> str:
    """compose 渲染语义：`$$` → 字面 `$`（之后才轮到容器内 shell）。"""
    return cmd.replace("$$", "$")


def _shell_expand(cmd: str, env_value: str | None) -> str:
    """容器内 /bin/sh `${VAR:-default}` 展开：未设置/空 → default。"""
    value = env_value if env_value not in (None, "") else "/"
    return cmd.replace("${" + HEALTH_ENV + ":-/}", value)


# ---------------------------------------------------------------- Dockerfile ENV 推导


def test_run_stage_pins_health_path_env_from_base_path_build_arg() -> None:
    """run stage 固化 `AIOS_WEB_HEALTH_PATH=${NEXT_PUBLIC_BASE_PATH:-/}`：
    ARG 默认空（root 构建不变），ENV 右值唯一来源是构建期 build arg
    （无其他拼接/字面量路径）。"""
    run_stage = _run_stage(_dockerfile_text())
    assert re.search(rf"^ARG {BUILD_ARG}=$", run_stage, re.MULTILINE), (
        "run stage 必须重新声明 ARG NEXT_PUBLIC_BASE_PATH（默认空）"
    )
    assert f"ENV {HEALTH_ENV}=${{{BUILD_ARG}:-/}}" in run_stage, (
        "run stage 必须固化 ENV AIOS_WEB_HEALTH_PATH=${NEXT_PUBLIC_BASE_PATH:-/}"
    )
    occurrences = re.findall(rf"^ENV {HEALTH_ENV}=(\S+)$", _dockerfile_text(), re.MULTILINE)
    assert occurrences == ["${" + BUILD_ARG + ":-/}"], (
        f"{HEALTH_ENV} 恰一处 ENV 定义，右值只能是 ${{{BUILD_ARG}:-/}}: {occurrences}"
    )


def test_build_stage_base_path_wiring_unchanged() -> None:
    """M14-159 build stage 注入保持原样（ARG/ENV NEXT_PUBLIC_BASE_PATH）。
    """
    dockerfile = _dockerfile_text()
    assert re.search(rf"^ARG {BUILD_ARG}=$", dockerfile, re.MULTILINE)
    assert f"ENV {BUILD_ARG}=${{{BUILD_ARG}}}" in dockerfile


# ---------------------------------------------------------------- 推导矩阵：root / basePath / 非法 fail-closed


def test_env_resolution_root_default_is_slash_and_legacy_probe() -> None:
    """root 构建（build arg 未设置/空）：ENV=/，容器内展开后探测命令与
    M14-159 之前的固定命令逐字一致（默认行为完全不变的回归锚）。"""
    cmd = _render_compose_dollars(_compose_probe_command())
    for build_arg in (None, ""):
        env_value = _dockerfile_env_expand(build_arg)
        assert env_value == "/"
        expanded = _shell_expand(cmd, env_value)
        assert expanded == LEGACY_ROOT_PROBE, (
            f"root 构建探测命令漂移: {expanded!r} != {LEGACY_ROOT_PROBE!r}"
        )


def test_env_resolution_base_path_build_probes_aios() -> None:
    """basePath 构建（build arg=/aios，构建期校验通过）：ENV=/aios，
    探测 http://127.0.0.1:3000/aios（canonical 健康路径，非 404 的 /）。"""
    cmd = _render_compose_dollars(_compose_probe_command())
    assert _normalize_base_path("/aios") == "/aios"
    env_value = _dockerfile_env_expand("/aios")
    assert env_value == "/aios"
    expanded = _shell_expand(cmd, env_value)
    assert expanded == (
        "wget -q -O- http://127.0.0.1:3000/aios >/dev/null 2>&1 || exit 1"
    )


@pytest.mark.parametrize(
    "illegal",
    [
        "/aios/",  # 尾斜杠
        "aios",  # 缺前导斜杠
        "/AIOS",  # 大小写
        " /aios",  # 前导空白
        "/other",  # 其他路径
        "/aios?x=1",  # 查询串
        "/aios#f",  # 片段
        "//aios",  # 双斜杠（绝对 URL 形态）
        "/aios/;/wget",  # 命令拼接企图
        "../etc/passwd",  # 相对路径穿越
    ],
)
def test_illegal_base_paths_fail_closed_at_build(illegal: str) -> None:
    """非法/其他路径构建期 fail-closed：normalizeBasePath 白名单恰为
    {空, "/aios"}，其余一律抛错 → npm run build 失败 → 镜像不产出 →
    运行时 ENV 不可能携带非法值。"""
    with pytest.raises(ValueError):
        _normalize_base_path(illegal)


def test_validator_source_enforces_exact_whitelist() -> None:
    """校验器源码契约锁定：非空且 != "/aios" 一律抛错（白名单不可放宽），
    错误信息不回显 env 原值。"""
    source = BASE_PATH_TS.read_text(encoding="utf-8")
    assert 'if (raw !== "/aios")' in source, "校验器必须只放行恰为 /aios 的非空值"
    assert "throw new Error(" in source, "非法值必须抛错（构建期 fail-closed）"
    assert "return \"/aios\";" in source


def test_non_canonical_env_values_can_never_probe_canonical_urls() -> None:
    """纵深防御：即便绕过构建把任意值塞进 ENV（宿主 docker run -e 等），
    展开结果也绝不在 canonical 探测 URL 集内 —— wget 探测 404/畸形 URL
    返回非零 → unhealthy → fail-closed（不可能误报健康）。"""
    cmd = _render_compose_dollars(_compose_probe_command())
    for rogue in ("/", "/aios", "/other", "/aios/", "/evil;rm -rf /", "", None):
        url_match = re.search(r"http://\S+", _shell_expand(cmd, rogue))
        assert url_match is not None
        if rogue in ("/", "/aios"):
            assert url_match.group(0) in CANONICAL_PROBE_URLS
        elif rogue in ("", None):
            assert url_match.group(0) == "http://127.0.0.1:3000/"  # shell 回落 /
        else:
            assert url_match.group(0) not in CANONICAL_PROBE_URLS, (
                f"非法 ENV {rogue!r} 展开后命中 canonical URL——fail-closed 破防"
            )


# ---------------------------------------------------------------- compose healthcheck 契约


def test_compose_healthcheck_uses_runtime_env_double_dollar() -> None:
    """CMD-SHELL 命令含 `$${AIOS_WEB_HEALTH_PATH:-/}`：`$$` 转义保证该
    变量由容器内 shell 展开（读镜像 ENV），不经宿主插值。"""
    cmd = _compose_probe_command()
    assert "$${" + HEALTH_ENV + ":-/}" in cmd, (
        "healthcheck 必须经 $$ 转义读取容器内 AIOS_WEB_HEALTH_PATH"
    )


def test_compose_healthcheck_has_no_host_side_interpolation() -> None:
    """注入面封死：CMD-SHELL 命令里不存在未转义的 `${...}`（单 `$` 形态
    会被 compose 当作宿主变量插值/警告——既非意图也是注入面）。"""
    cmd = _compose_probe_command()
    assert re.search(r"(?<!\$)\$\{", cmd) is None, (
        "healthcheck 命令含未转义宿主插值 ${...}"
    )
    assert "$$" in cmd, "缺少 $$ 转义（容器内展开的前提）"


def test_compose_web_environment_has_no_health_path_override() -> None:
    """compose web environment 不提供 AIOS_WEB_HEALTH_PATH 入口：值唯一
    来源是镜像 ENV（构建期校验过的 build arg）——杜绝部署 env 覆盖出
    探测路径与构建形态漂移。"""
    compose = yaml.safe_load(ROOT_COMPOSE.read_text(encoding="utf-8"))
    environment = compose["services"]["web"].get("environment", {})
    entries = environment if isinstance(environment, list) else environment.keys()
    assert HEALTH_ENV not in entries, (
        f"compose 不得为 {HEALTH_ENV} 提供宿主 override 入口"
    )


def test_probe_upstream_is_loopback_only() -> None:
    """探测上游恒为 loopback:3000（不引入外部探测面）。"""
    for env_value in ("/", "/aios"):
        expanded = _shell_expand(
            _render_compose_dollars(_compose_probe_command()), env_value
        )
        assert "http://127.0.0.1:3000" in expanded


# ---------------------------------------------------------------- 无 secret


def test_health_surface_introduces_no_secrets() -> None:
    """新增面无 secret：run stage 与 healthcheck 命令不含密钥类字样；
    run stage 的 env 名单恰为 {NODE_ENV, NEXT_PUBLIC_BASE_PATH(仅 ARG 接收
    固化), AIOS_WEB_HEALTH_PATH}——无凭据形态、无新增敏感注入。"""
    secret_pattern = re.compile(r"SECRET|PASSWORD|PASSWD|TOKEN|PRIVATE_KEY|API_KEY")
    run_stage = _run_stage(_dockerfile_text())
    assert secret_pattern.search(run_stage) is None
    assert secret_pattern.search(_compose_probe_command()) is None
    env_names = set(re.findall(r"^ENV (\w+)=", run_stage, re.MULTILINE))
    assert env_names == {"NODE_ENV", HEALTH_ENV}, (
        f"run stage env 名单漂移（无凭据、无意外注入面）: {sorted(env_names)}"
    )
