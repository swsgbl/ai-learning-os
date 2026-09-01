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


def _render(profile: str | None = None, extra_env: dict[str, str] | None = None) -> dict:
    """docker compose config --format json：渲染合并后的 compose model（不启动容器）。"""
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE)]
    if profile:
        cmd += ["--profile", profile]
    cmd += ["config", "--format", "json"]
    merged = dict(os.environ)
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
