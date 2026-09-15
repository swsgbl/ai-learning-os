"""M14-27 语音健康来源切换（loopback → sidecar）契约测试。

微步 1（既有契约锚点）：加载 tools/ops/production_monitor.py，锁定
固定五端点画像的 endpoint_id → URL 逐字全等——M14-27 的任何来源切换
改动不得改变 loopback 基线的这五个事实。

微步 2（TDD RED）：sidecar 清单严格校验（schema/service/pid/ports/
RFC1918 bind）、精确 sidecar URL 构建、sidecar 语音 URL fail-closed
校验——生产代码未实现，本轮预期 AttributeError/失败（零网络、tmp_path）。

微步 4（TDD RED，CLI/main）：--voice-health-source 默认 loopback、
sidecar plan 报告 config、execute 缺清单 fail-closed（零 Runner/零
Transport/零工件残留）、--only 单选 sidecar 端点——main 未接线，预期
AttributeError/断言失败（全部 I/O 限 tmp_path，零网络）。
"""
from __future__ import annotations

import importlib.util
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "production_monitor.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


pm = _load_module(SCRIPT, "production_monitor_m14_27_under_test")


def test_legacy_endpoint_profile_five_ids_and_urls_exact() -> None:
    """既有契约：五端点画像 endpoint_id → URL 逐字全等（顺序一并锁定）。"""
    assert [(e.endpoint_id, e.url, e.group) for e in pm.ENDPOINTS] == [
        ("web-root", "http://127.0.0.1:3011/", "web"),
        ("web-login", "http://127.0.0.1:3011/login", "web"),
        ("api-health", "http://127.0.0.1:8000/health", "api"),
        ("funasr-health", "http://127.0.0.1:8010/health", "voice"),
        ("cosyvoice-health", "http://127.0.0.1:8011/health", "voice"),
    ]


# ---------------------------------------------------------------- sidecar 清单


def _write_manifest(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "sidecar-manifest.json"
    path.write_text(text, encoding="utf-8")
    return path


def _valid_manifest_text(bind: str = "192.168.8.23") -> str:
    return json.dumps({
        "schema_version": 1,
        "service": "voice-health-sidecar",
        "pid": 1234,
        "bind": bind,
        "ports": [18010, 18011],
        "started_at": "2026-09-15T03:00:00Z",
        "log": ".verify/artifacts/m14-26-voice-health-sidecar/sidecar.log",
    })


def test_load_sidecar_manifest_valid_returns_bind(tmp_path) -> None:
    """(a) 合法清单 → bind 原样返回、无错误。"""
    path = _write_manifest(tmp_path, _valid_manifest_text())
    bind, error = pm.load_sidecar_manifest(path)
    assert bind == "192.168.8.23"
    assert error is None


@pytest.mark.parametrize(("case", "text", "expected_error"), [
    ("invalid-json", "not-json{", "sidecar-manifest-invalid-json"),
    ("wrong-schema", _valid_manifest_text().replace('"schema_version": 1', '"schema_version": 2'),
     "sidecar-manifest-schema-version"),
    ("wrong-service", _valid_manifest_text().replace("voice-health-sidecar", "other-sidecar"),
     "sidecar-manifest-service"),
    ("wrong-pid", _valid_manifest_text().replace('"pid": 1234', '"pid": 0'),
     "sidecar-manifest-pid"),
    ("wrong-ports", _valid_manifest_text().replace("[18010, 18011]", "[8010, 8011]"),
     "sidecar-manifest-ports"),
    ("bind-loopback", _valid_manifest_text(bind="127.0.0.1"), "sidecar-manifest-bind"),
    ("bind-any", _valid_manifest_text(bind="0.0.0.0"), "sidecar-manifest-bind"),
    ("bind-link-local", _valid_manifest_text(bind="169.254.1.1"), "sidecar-manifest-bind"),
    ("bind-public", _valid_manifest_text(bind="8.8.8.8"), "sidecar-manifest-bind"),
    ("bind-not-ip", _valid_manifest_text(bind="not-an-ip"), "sidecar-manifest-bind"),
])
def test_load_sidecar_manifest_rejects_invalid(tmp_path, case, text, expected_error) -> None:
    """(b) 每类非法清单 → 固定安全错误类别、零 bind（fail-closed）。"""
    path = _write_manifest(tmp_path, text)
    bind, error = pm.load_sidecar_manifest(path)
    assert bind is None
    assert error == expected_error


# ---------------------------------------------------------------- sidecar 端点面


def test_build_sidecar_endpoints_keeps_web_api_swaps_voice(tmp_path) -> None:
    """(c) sidecar 画像：web/api 三端点原样 + 语音双端点换 sidecar 精确 URL。"""
    endpoints = pm.build_sidecar_endpoints("192.168.8.23")
    assert [(e.endpoint_id, e.url, e.group) for e in endpoints] == [
        ("web-root", "http://127.0.0.1:3011/", "web"),
        ("web-login", "http://127.0.0.1:3011/login", "web"),
        ("api-health", "http://127.0.0.1:8000/health", "api"),
        ("funasr-health", "http://192.168.8.23:18010/health", "voice"),
        ("cosyvoice-health", "http://192.168.8.23:18011/health", "voice"),
    ]


@pytest.mark.parametrize("url", [
    "http://192.168.8.23:18010/health",
    "http://192.168.8.23:18011/health",
    "http://172.20.0.5:18010/health",
    "http://10.1.2.3:18011/health",
])
def test_validate_sidecar_voice_url_accepts_exact_forms(url) -> None:
    """(d) 仅接受 RFC1918 bind + 18010/18011 + 精确 /health。"""
    assert pm.validate_sidecar_voice_url(url) is None


@pytest.mark.parametrize(("url", "expected_error"), [
    ("http://sidecar.wsl:18010/health", "host-not-literal-ip"),
    ("http://8.8.8.8:18010/health", "host-not-rfc1918"),
    ("http://127.0.0.1:18010/health", "host-not-rfc1918"),
    ("http://[fe80::1]:18010/health", "host-not-rfc1918"),
    ("http://192.168.8.23:18010/health?x=1", "query-or-fragment"),
    ("http://192.168.8.23:18010/health#f", "query-or-fragment"),
    ("http://user:pw@192.168.8.23:18010/health", "userinfo-present"),
    ("http://192.168.8.23:8010/health", "port-not-allowed"),
    ("http://192.168.8.23:18010/health/live", "path-not-allowed"),
    ("https://192.168.8.23:18010/health", "scheme-not-http"),
    ("http://192.168.8.23/health", "no-explicit-port"),
])
def test_validate_sidecar_voice_url_rejects(url, expected_error) -> None:
    """(d) DNS/公网/回环/IPv6/query/fragment/userinfo/错端口/错路径全部拒绝。"""
    assert pm.validate_sidecar_voice_url(url) == expected_error


# ---------------------------------------------------------------- CLI / main


def _forbid_real_io(*_args, **_kwargs):
    raise AssertionError("real-io-touched（测试契约：零 subprocess/零 socket）")


def _patch_valid_manifest(tmp_path, monkeypatch) -> Path:
    manifests = tmp_path / "manifests"
    manifests.mkdir(exist_ok=True)
    path = _write_manifest(manifests, _valid_manifest_text())
    monkeypatch.setattr(pm, "SIDECAR_MANIFEST_PATH", path)
    return path


def _read_latest_report(artifacts: Path, prefix: str) -> dict:
    matches = sorted(artifacts.glob(f"{prefix}-*.json"))
    assert matches, f"未写任何 {prefix} 报告"
    return json.loads(matches[-1].read_text(encoding="utf-8"))


def test_parser_default_voice_health_source_loopback() -> None:
    """CLI 默认值恒为 loopback（既有生产行为不变）。"""
    args = pm.build_parser().parse_args([])
    assert args.voice_health_source == "loopback"


def test_sidecar_plan_report_records_source_and_exact_urls(tmp_path, monkeypatch) -> None:
    """sidecar plan：合法清单 → 退出 0，报告 config 记 source=sidecar 与
    18010/18011 精确语音 URL（全 I/O 限 tmp_path，零网络）。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _patch_valid_manifest(tmp_path, monkeypatch)
    code = pm.main(["--voice-health-source", "sidecar",
                    "--artifact-dir", str(artifacts)])
    assert code == pm.EXIT_OK
    config = _read_latest_report(artifacts, "plan")["config"]
    assert config["voice_health_source"] == "sidecar"
    voice = [(e["endpoint_id"], e["url"]) for e in config["endpoints"]
             if e["group"] == "voice"]
    assert voice == [
        ("funasr-health", "http://192.168.8.23:18010/health"),
        ("cosyvoice-health", "http://192.168.8.23:18011/health"),
    ]


def test_sidecar_execute_missing_manifest_fails_closed_pre_runner(
        tmp_path, monkeypatch) -> None:
    """execute + 正确 confirm 但清单缺失 → EXIT_USAGE；零 RealRunner/
    RealTransport 构造、零工件残留（subprocess/socket 全程封死）。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    monkeypatch.setattr(pm, "SIDECAR_MANIFEST_PATH",
                        tmp_path / "manifests" / "absent.json")
    constructed: list[str] = []
    monkeypatch.setattr(pm, "RealRunner", lambda: constructed.append("runner"))
    monkeypatch.setattr(pm, "RealTransport", lambda: constructed.append("transport"))
    monkeypatch.setattr(pm.subprocess, "run", _forbid_real_io)
    monkeypatch.setattr(socket, "socket", _forbid_real_io)
    code = pm.main(["--voice-health-source", "sidecar", "--execute",
                    "--confirm", pm.CONFIRM_PHRASE,
                    "--artifact-dir", str(artifacts)])
    assert code == pm.EXIT_USAGE
    assert constructed == []
    assert list(artifacts.iterdir()) == []


def test_sidecar_only_funasr_selects_single_sidecar_endpoint(
        tmp_path, monkeypatch) -> None:
    """--only funasr-health：仅选中该端点，URL 为 sidecar 精确形态。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _patch_valid_manifest(tmp_path, monkeypatch)
    code = pm.main(["--voice-health-source", "sidecar", "--only", "funasr-health",
                    "--artifact-dir", str(artifacts)])
    assert code == pm.EXIT_OK
    config = _read_latest_report(artifacts, "plan")["config"]
    assert [(e["endpoint_id"], e["url"]) for e in config["endpoints"]] == [
        ("funasr-health", "http://192.168.8.23:18010/health"),
    ]


# ------------------------------------------- 清单体积硬顶（读前 stat 预检 + 读后 len 复核）


class _FakeManifestPath:
    """Path-like 假件：探测（is_symlink/exists/is_file/stat）与读取结果
    可控，并计数 read_bytes 调用——锁定读前拒绝顺序（零真实 I/O）。"""

    def __init__(self, *, st_size: int, data: bytes | None = None) -> None:
        self._st_size = st_size
        self._data = data
        self.read_bytes_calls = 0

    def is_symlink(self) -> bool:
        return False

    def exists(self) -> bool:
        return True

    def is_file(self) -> bool:
        return True

    def stat(self) -> SimpleNamespace:
        return SimpleNamespace(st_size=self._st_size)

    def read_bytes(self) -> bytes:
        self.read_bytes_calls += 1
        if self._data is None:
            raise AssertionError("read_bytes must not be called for oversize stat")
        return self._data


def test_load_sidecar_manifest_rejects_oversize_stat_before_read() -> None:
    """(e) stat 体积超顶（65537）→ 读前即拒（sidecar-manifest-oversize），
    read_bytes 绝不被调用——失控文件绝不读入内存。"""
    fake = _FakeManifestPath(st_size=pm.SIDECAR_MANIFEST_MAX_BYTES + 1)
    bind, error = pm.load_sidecar_manifest(fake)
    assert bind is None
    assert error == "sidecar-manifest-oversize"
    assert fake.read_bytes_calls == 0


def test_load_sidecar_manifest_rejects_post_read_size_drift() -> None:
    """(e) stat 恰在硬顶（65536）放行读前预检，但读回 65537 字节（stat 与
    读取之间漂移/假件失真）→ 读后 len 复核仍拒绝（防线保留）。"""
    fake = _FakeManifestPath(st_size=pm.SIDECAR_MANIFEST_MAX_BYTES,
                             data=b"x" * (pm.SIDECAR_MANIFEST_MAX_BYTES + 1))
    bind, error = pm.load_sidecar_manifest(fake)
    assert bind is None
    assert error == "sidecar-manifest-oversize"
    assert fake.read_bytes_calls == 1


# ---------------------------------------------------------------- 报告边界注记


def test_report_boundaries_describe_voice_health_targets() -> None:
    """(f) 边界注记如实描述两类健康检查目标：web/api 恒字面 loopback
    GET-only；sidecar 语音仅由 canonical manifest 派生（字面 RFC1918 +
    固定 18010/18011 + 精确 /health），并显式排除 DNS/query/fragment/
    userinfo/任意 URL/清单路径注入/loopback 回退。"""
    joined = "\n".join(pm.REPORT_BOUNDARIES)
    assert "loopback GET-only" in joined
    assert "canonical sidecar manifest" in joined
    assert "literal RFC1918 IPv4" in joined
    assert "fixed ports 18010/18011" in joined
    assert "exact path /health" in joined
    assert "no DNS" in joined
    assert "no arbitrary URLs" in joined
    assert "no manifest path injection" in joined
    assert "no loopback fallback" in joined
    # 旧的不准确措辞不得残留：sidecar 模式下 HTTP 目标不全是 loopback
    assert "GET-only loopback HTTP" not in joined
