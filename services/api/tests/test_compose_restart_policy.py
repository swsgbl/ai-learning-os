r"""M14-06 生产韧性静态契约：compose restart 策略 + 恢复 env 模板/防泄漏护栏。

- restart 策略：所有长驻服务（postgres/redis/minio/api/livekit/web）恒
  `restart: unless-stopped` —— 经 docker compose config 渲染断言（local/
  hybrid/cloud/无 profile 四形态，不启动任何容器）；无 Docker CLI 时回退
  PyYAML 静态解析（CI 可跑），两者互补。
- env 模板护栏：模板存在、含全部 pin 必需键、占位值非真实 secret；真实
  env 文件名被 .gitignore 覆盖（绝无入库路径）；git 索引中不存在真实 env
  文件（防「先填后忘」事故）。
- 不变量：unless-stopped 语义 = 崩溃/引擎重启自动拉起，显式 stop/down 仍被
  尊重——与恢复编排（tools/ops/production_recovery.py）的容器面兜底分工。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.yml"
ENV_TEMPLATE = REPO_ROOT / "infra" / "env.production-recovery.example"
REAL_ENV_FILE = "infra/env.production-recovery"
LONG_RUNNING_SERVICES = ("postgres", "redis", "minio", "api", "livekit", "web")
PIN_KEYS = (
    "AIOS_IMAGE_TAG",
    "AIOS_WEB_IMAGE_TAG",
    "AIOS_APP_ENV",
    "AIOS_WEB_PORT",
    "AIOS_AUTH_SECRET",
    "AIOS_LIVEKIT_API_SECRET",
)
SECRET_PATTERNS = ("sk-", "AKIA", "ghp_", "xoxb_", "-----BEGIN")


def _compose_available() -> bool:
    return shutil.which("docker") is not None and COMPOSE_FILE.is_file()


def _render(profile: str | None) -> dict:
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE)]
    if profile:
        cmd += ["--profile", profile]
    cmd += ["config", "--format", "json"]
    result = subprocess.run(
        cmd, check=False, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stderr
    import json

    return json.loads(result.stdout)


@pytest.mark.skipif(not _compose_available(), reason="需要 docker compose CLI")
@pytest.mark.parametrize("profile,expected_services", [
    ("local", {"postgres", "redis", "minio", "api", "web", "livekit"}),
    ("hybrid", {"postgres", "redis", "minio", "api", "web", "livekit"}),
    ("cloud", {"postgres", "redis", "minio", "api", "web", "livekit"}),
    (None, {"postgres", "redis", "minio", "api", "web"}),
])
def test_rendered_services_carry_unless_stopped(profile, expected_services) -> None:
    """docker compose config 渲染：每个长驻服务 restart=unless-stopped（零容器改动）。"""
    model = _render(profile)
    services = set(model.get("services", {}))
    assert services == expected_services
    for name in services:
        restart = model["services"][name].get("restart")
        assert restart == "unless-stopped", f"服务 {name} restart={restart!r} ≠ unless-stopped"


def _static_yaml_services() -> dict[str, dict]:
    yaml = pytest.importorskip("yaml", reason="PyYAML 不可用——跳过静态回退断言")
    model = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    assert isinstance(model, dict) and isinstance(model.get("services"), dict)
    return model["services"]


def test_static_yaml_every_long_running_service_has_unless_stopped() -> None:
    """无 Docker CLI 的静态回退：YAML 直解，六个长驻服务全部声明 restart: unless-stopped。"""
    services = _static_yaml_services()
    assert set(services) == set(LONG_RUNNING_SERVICES)
    for name, body in services.items():
        assert body.get("restart") == "unless-stopped", f"{name} 缺 restart: unless-stopped"


def test_static_yaml_image_tag_anchors_are_split() -> None:
    """M14-09 镜像 tag 锚点静态契约：api 只读 AIOS_IMAGE_TAG，web 只读
    AIOS_WEB_IMAGE_TAG（各自默认 local）——web 不得引用 AIOS_IMAGE_TAG
    （防 Web-only 升级混用两代镜像/破坏 recovery pin）。"""
    services = _static_yaml_services()
    api_image = services["api"]["image"]
    web_image = services["web"]["image"]
    assert api_image == "aios/api:${AIOS_IMAGE_TAG:-local}"
    assert web_image == "aios/web:${AIOS_WEB_IMAGE_TAG:-local}"
    assert "AIOS_IMAGE_TAG" not in web_image.replace("AIOS_WEB_IMAGE_TAG", "")


def test_env_template_exists_with_pin_keys_and_placeholders() -> None:
    assert ENV_TEMPLATE.is_file(), f"模板缺失: {ENV_TEMPLATE}"
    text = ENV_TEMPLATE.read_text(encoding="utf-8")
    for key in PIN_KEYS:
        assert f"{key}=" in text, f"模板缺 pin 键 {key}"
    # 占位值不得形似真实 secret（防「照抄模板即泄漏/即生效」）
    for pattern in SECRET_PATTERNS:
        assert pattern not in text
    assert "绝不提交" in text or "不得提交" in text  # 模板自带纪律提示


def test_gitignore_covers_real_env_file() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert REAL_ENV_FILE in gitignore, "真实 env 文件名必须被 .gitignore 显式覆盖"
    # 路径式条目按前缀目录解析——逐行精确匹配（防通配意外失效）
    assert any(line.strip() == REAL_ENV_FILE for line in gitignore.splitlines())


@pytest.mark.skipif(shutil.which("git") is None, reason="需要 git CLI")
def test_real_env_file_never_tracked() -> None:
    """git 索引不得包含真实 env 文件（防「先填后忘」提交事故；含全部历史路径）。"""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", REAL_ENV_FILE],
        check=False, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert not result.stdout.strip(), f"真实 env 文件已被跟踪: {result.stdout}"


def test_real_env_file_absent_or_gitignored_on_disk() -> None:
    """本机磁盘上若已存在真实 env 文件，必须处于被忽略状态（git check-ignore 命中）。"""
    real = REPO_ROOT / REAL_ENV_FILE
    if not real.exists() or shutil.which("git") is None:
        pytest.skip("真实 env 文件尚未创建（部署时由 supervisor 创建）或无 git CLI")
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", REAL_ENV_FILE],
        check=False, capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={**os.environ, "GIT_REQUIRED": "1"},
    )
    assert result.returncode == 0, "真实 env 文件存在但未被 gitignore 覆盖"
