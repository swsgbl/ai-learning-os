r"""M14-148 tools/ops/searxng_egress_recovery.py 契约测试：SearXNG 出站恢复
helper 的幂等/fail-closed/零服务触碰/零值回显边界，全部离线（tmp env 文件
+ importlib 加载，零 Docker、零网络、零真实 env 文件读取）。

覆盖：
- PIN_KEYS 单一事实源：helper 经 importlib 复用 production_recovery.PIN_KEYS
  （元组逐键相等——九键契约 exactly preserved，绝无第二份漂移副本）；
- 键名验证 fail-closed：env 文件缺失 / 九键缺一 → exit 1 拒绝，输出仅含
  键名（伪 secret 标记值绝不出现在任何输出行）；
- 禁用行为：激活且非空的代理槽位行 → enforce 后转为固定标记注释形态
  （原值保留在 gitignored 文件内供日后 opt-in），**其余行逐字节不变**
  （九键伪值逐键不变）；写后 PIN_KEYS 重验齐全；
- 幂等：二跑 no-op（exit 0，文件内容不再变化）；空值槽位 = 已是直连
  默认 → no-op 不改文件；无槽位 → nothing-to-do；
- dry-run：零写入（文件逐字节不变），计划输出含键名与行号；
- secret 不泄漏：伪代理值标记（socks5h://ZX-markerproxy-…）绝不出现在
  任何场景的 stdout 任何行；
- **严格 UTF-8 fail-closed（supervisor 修正 Round 1）**：非 UTF-8 文件
  在任何写入之前 exit 1 拒绝（dry-run 与 enforce 两路径），文件字节
  逐字节不变，输出不含解码内容/替换字符（U+FFFD 探针）；
- **原子替换写（supervisor 修正 Round 1）**：诱导 os.replace 失败 →
  原文件逐字节不变、目录无 ``*.tmp`` 残留、exit 1 可见失败；成功路径
  同样零临时文件残留；
- 行分类纯函数矩阵：active / active-empty / disabled（标记形态）/
  commented（原生注释）/ absent 五形态；
- 源码契约：helper 源码无 subprocess/os.system/popen（零子进程 = 零服务
  触碰的构造性保证）、无 docker/compose 命令构造；模板
  infra/env.production-recovery.example 含三槽位注释形态文档且无激活
  代理行（直连 = canonical 模板默认）；
- compose 注释与模板双载 M14-148 恢复口径（防漂移文本锚点）。
"""
from __future__ import annotations

import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "searxng_egress_recovery.py"
PRODUCTION_RECOVERY = REPO_ROOT / "tools" / "ops" / "production_recovery.py"
ENV_TEMPLATE = REPO_ROOT / "infra" / "env.production-recovery.example"
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.yml"

#: 伪 pin 值（仅存在于 tmp env 文件与断言比较——绝不该出现在任何输出）
PIN_VALUES = {
    "AIOS_IMAGE_TAG": "m14-03-prod-rehearsal",
    "AIOS_WEB_IMAGE_TAG": "m14-03-prod-rehearsal",
    "AIOS_APP_ENV": "production-zxmarker",
    "AIOS_WEB_PORT": "3011",
    "AIOS_AUTH_SECRET": "ZX-markerauth-0123456789abcdef",
    "AIOS_LIVEKIT_API_SECRET": "ZX-markerlivekit-0123456789abcdef",
    "AIOS_BIND_IP": "127.0.0.1",
    "AIOS_LIVEKIT_BIND_IP": "127.0.0.1",
    "AIOS_PUBLIC_LIVEKIT_URL": "ws://127.0.0.1:7880",
}
#: 伪代理值标记（绝不该出现在任何输出行——值不回显契约的探针）
MARK_PROXY = "socks5h://ZX-markerproxy-0123456789abcdef"


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


sr = _load_module(SCRIPT, "searxng_egress_recovery_under_test")
pr = _load_module(PRODUCTION_RECOVERY, "production_recovery_for_egress_test")


def _write_env(path: Path, *, proxy_lines: tuple[str, ...] = ()) -> Path:
    lines = [f"{key}={value}" for key, value in PIN_VALUES.items()]
    lines.extend(proxy_lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def _run(path: Path, *flags: str, capsys) -> tuple[int, str]:
    code = sr.main(["--env-file", str(path), *flags])
    captured = capsys.readouterr()
    return code, captured.out


# ------------------------------------------------------- PIN_KEYS 单一事实源

def test_pin_keys_single_source_of_truth() -> None:
    """helper.PIN_KEYS 复用 production_recovery 九键——逐键相等，零副本漂移。"""
    assert sr.PIN_KEYS == pr.PIN_KEYS
    assert len(sr.PIN_KEYS) == 9
    # 代理槽位绝不并入 PIN_KEYS（开关键 ≠ pin 键——契约原样保持）
    assert not set(sr.EGRESS_PROXY_SLOT_KEYS) & set(sr.PIN_KEYS)


# ------------------------------------------------------- 键名验证 fail-closed

def test_missing_env_file_rejected(tmp_path, capsys) -> None:
    code, out = _run(tmp_path / "absent.env", "--dry-run", capsys=capsys)
    assert code == 1
    assert "缺失" in out


def test_missing_pin_key_rejected_names_only(tmp_path, capsys) -> None:
    """九键缺一 → 拒绝且仅报键名；伪 secret 值绝不进入输出。"""
    path = _write_env(tmp_path / "env")
    lines = path.read_text(encoding="utf-8").splitlines()
    lines = [line for line in lines if not line.startswith("AIOS_BIND_IP=")]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    code, out = _run(path, "--dry-run", capsys=capsys)
    assert code == 1
    assert "AIOS_BIND_IP" in out
    assert "AIOS_SEARXNG" not in out  # 无代理槽位时计划面不该出现
    for value in PIN_VALUES.values():
        assert value not in out
    assert MARK_PROXY not in out


# ------------------------------------------------------- 禁用行为（enforce）

def test_enforce_disables_active_proxy_slots(tmp_path, capsys) -> None:
    """非空代理槽位 → 标记注释形态；其余行逐字节不变；九键伪值逐键不变。"""
    path = _write_env(
        tmp_path / "env",
        proxy_lines=(
            f"AIOS_SEARXNG_HTTP_PROXY={MARK_PROXY}",
            "AIOS_SEARXNG_NO_PROXY=127.0.0.1,localhost",
            f"AIOS_SEARXNG_HTTPS_PROXY={MARK_PROXY}",
        ),
    )
    before = path.read_text(encoding="utf-8").splitlines(keepends=True)
    code, out = _run(path, capsys=capsys)
    assert code == 0
    after = path.read_text(encoding="utf-8").splitlines(keepends=True)
    assert len(after) == len(before)
    disabled_count = 0
    for old, new in zip(before, after):
        if old.startswith(("AIOS_SEARXNG_HTTP_PROXY=", "AIOS_SEARXNG_HTTPS_PROXY=")):
            assert new == f"{sr.DISABLE_MARKER}{old}"
            disabled_count += 1
        else:
            assert new == old  # 九键与 NO_PROXY 行逐字节不变
    assert disabled_count == 2
    for key, value in PIN_VALUES.items():
        assert f"{key}={value}" in path.read_text(encoding="utf-8")
    # 值不回显：伪代理标记绝不进入输出
    assert MARK_PROXY not in out
    assert "AIOS_SEARXNG_HTTP_PROXY" in out and "AIOS_SEARXNG_HTTPS_PROXY" in out


def test_enforce_idempotent_second_run_noop(tmp_path, capsys) -> None:
    """二跑 → 已禁用形态识别为 no-op；文件内容不再变化。"""
    path = _write_env(
        tmp_path / "env",
        proxy_lines=(f"AIOS_SEARXNG_HTTP_PROXY={MARK_PROXY}",),
    )
    assert _run(path, capsys=capsys)[0] == 0
    once = path.read_text(encoding="utf-8")
    code, out = _run(path, capsys=capsys)
    assert code == 0
    assert "无需变更" in out
    assert path.read_text(encoding="utf-8") == once


def test_empty_value_slot_already_direct_noop(tmp_path, capsys) -> None:
    """空值槽位 = 直连默认 → 不禁用、不改文件（最小变更）。"""
    path = _write_env(
        tmp_path / "env",
        proxy_lines=("AIOS_SEARXNG_HTTP_PROXY=", "AIOS_SEARXNG_HTTPS_PROXY="),
    )
    before = path.read_text(encoding="utf-8")
    code, out = _run(path, capsys=capsys)
    assert code == 0
    assert "无需变更" in out
    assert path.read_text(encoding="utf-8") == before


def test_no_slots_nothing_to_do(tmp_path, capsys) -> None:
    """env 无任何代理槽位（模板默认形态）→ 直连默认成立，no-op。"""
    path = _write_env(tmp_path / "env")
    before = path.read_text(encoding="utf-8")
    code, out = _run(path, "--dry-run", capsys=capsys)
    assert code == 0
    assert "无需变更" in out
    assert path.read_text(encoding="utf-8") == before


# ------------------------------------------------------- dry-run 零写入

def test_dry_run_emits_plan_zero_write(tmp_path, capsys) -> None:
    path = _write_env(
        tmp_path / "env",
        proxy_lines=(f"AIOS_SEARXNG_HTTP_PROXY={MARK_PROXY}",),
    )
    before = path.read_text(encoding="utf-8")
    code, out = _run(path, "--dry-run", capsys=capsys)
    assert code == 0
    assert path.read_text(encoding="utf-8") == before  # 零写入
    assert "计划禁用: AIOS_SEARXNG_HTTP_PROXY" in out
    assert "第 10 行" in out  # 行号（九键后第一行，1 起算）
    assert "零写入" in out
    assert MARK_PROXY not in out
    for value in PIN_VALUES.values():
        assert value not in out


# ------------------------------------------- 严格 UTF-8 fail-closed（修正轮）

def _write_non_utf8_env(path: Path) -> bytes:
    """构造含非 UTF-8 字节的 env 文件（九键 + 槽位行带非法字节序列）。"""
    payload = "\n".join(f"{key}={value}" for key, value in PIN_VALUES.items())
    raw = payload.encode("utf-8") + b"\n" + b"\xff\xfeAIOS_SEARXNG_HTTP_PROXY=MARKERASCIIWORD\n"
    path.write_bytes(raw)
    return raw


def test_non_utf8_env_fail_closed_dry_run(tmp_path, capsys) -> None:
    """dry-run 路径：非 UTF-8 文件在任何写入之前 exit 1 拒绝；字节
    逐字节不变；输出不含解码内容/替换字符（U+FFFD 与内容探针均不出现）。"""
    path = tmp_path / "env"
    raw = _write_non_utf8_env(path)
    code, out = _run(path, "--dry-run", capsys=capsys)
    assert code == 1
    assert "UTF-8" in out
    assert "�" not in out
    assert "MARKERASCIIWORD" not in out  # 拒绝面不回显任何文件内容
    assert path.read_bytes() == raw  # 字节逐字节不变


def test_non_utf8_env_fail_closed_enforce(tmp_path, capsys) -> None:
    """enforce 路径：同样的 fail-closed 拒绝先于一切写入——文件字节
    不变且无临时文件残留。"""
    path = tmp_path / "env"
    raw = _write_non_utf8_env(path)
    code, out = _run(path, capsys=capsys)
    assert code == 1
    assert "�" not in out
    assert path.read_bytes() == raw
    assert list(tmp_path.glob("env.*.tmp")) == []


# ------------------------------------------- 原子替换写（修正轮）

def test_atomic_replace_failure_leaves_original_unchanged(
    tmp_path, capsys, monkeypatch,
) -> None:
    """诱导 os.replace 失败：原文件逐字节不变、目录无 .tmp 残留、
    exit 1 可见失败（secret 文件绝不承受半写状态）。"""

    def _boom(src: object, dst: object) -> None:
        raise OSError("induced replace failure")

    monkeypatch.setattr(os, "replace", _boom)
    path = _write_env(
        tmp_path / "env",
        proxy_lines=(f"AIOS_SEARXNG_HTTP_PROXY={MARK_PROXY}",),
    )
    before = path.read_bytes()
    code, out = _run(path, capsys=capsys)
    assert code == 1
    assert "原子替换" in out and "原文件未动" in out
    assert path.read_bytes() == before
    assert list(tmp_path.glob("env.*.tmp")) == []


def test_successful_enforce_leaves_no_temp_files(tmp_path, capsys) -> None:
    """成功路径同样零临时文件残留（temp 已被 os.replace 原子消费）。"""
    path = _write_env(
        tmp_path / "env",
        proxy_lines=(f"AIOS_SEARXNG_HTTP_PROXY={MARK_PROXY}",),
    )
    assert _run(path, capsys=capsys)[0] == 0
    assert list(tmp_path.glob("env.*.tmp")) == []
    assert path.is_file()


def test_atomic_write_preserves_permission_bits_where_supported(
    tmp_path, capsys,
) -> None:
    """成功替换后原文件权限位保留（where supported：Windows 面仅
    只读位可观察——设只读→工具 chmod 复制→替换后仍只读）。"""
    path = _write_env(
        tmp_path / "env",
        proxy_lines=(f"AIOS_SEARXNG_HTTP_PROXY={MARK_PROXY}",),
    )
    if sys.platform == "win32":
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)  # Windows 只观察读写位
    else:
        os.chmod(path, 0o600)
    mode_before = stat.S_IMODE(path.stat().st_mode)
    assert _run(path, capsys=capsys)[0] == 0
    assert stat.S_IMODE(path.stat().st_mode) == mode_before
    os.chmod(path, 0o666)  # 还原，避免 tmp 清理受只读位影响


# ------------------------------------------------------- 行分类纯函数矩阵

@pytest.mark.parametrize("raw,expected", [
    (f"AIOS_SEARXNG_HTTP_PROXY={MARK_PROXY}", "active"),
    ("AIOS_SEARXNG_HTTPS_PROXY=", "active-empty"),
    (f"{sr.DISABLE_MARKER}AIOS_SEARXNG_HTTP_PROXY=x", "disabled"),
    (f"{sr.DISABLE_MARKER}AIOS_SEARXNG_HTTPS_PROXY={MARK_PROXY}", "disabled"),
    ("# AIOS_SEARXNG_HTTP_PROXY=http://proxy:1", "commented"),
    ("# AIOS_SEARXNG_HTTPS_PROXY=", "commented"),
    ("AIOS_SEARXNG_NO_PROXY=127.0.0.1", "absent"),  # NO_PROXY 不在禁用集
    ("AIOS_AUTH_SECRET=whatever", "absent"),
    ("# 无关注释", "absent"),
    ("", "absent"),
])
def test_classify_slot_line_matrix(raw: str, expected: str) -> None:
    assert sr.classify_slot_line(raw) == expected


def test_no_proxy_slot_never_disabled(tmp_path, capsys) -> None:
    """NO_PROXY 槽位即便非空也不禁用（直连下无效但无害——最小变更）。"""
    path = _write_env(
        tmp_path / "env",
        proxy_lines=("AIOS_SEARXNG_NO_PROXY=127.0.0.1,localhost",),
    )
    before = path.read_text(encoding="utf-8")
    assert _run(path, capsys=capsys)[0] == 0
    assert path.read_text(encoding="utf-8") == before


# ------------------------------------------------------- 源码/模板契约

def test_helper_source_has_no_subprocess_or_service_commands() -> None:
    """零子进程 = 零服务触碰的构造性保证（Round 1 硬边界）。

    AST 面（docstring 里的边界描述文字不误触）：无 subprocess import、
    无进程执行属性引用、无任何以 docker/compose 起头的字符串字面量
    （服务命令构造面）。
    """
    import ast

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
            assert "subprocess" not in names, "helper 不得 import subprocess"
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "subprocess", "helper 不得 from subprocess import"
        elif isinstance(node, ast.Attribute):
            assert node.attr not in ("system", "popen", "Popen", "run"),                 f"helper 不得引用进程执行属性 .{node.attr}（零子进程契约）"
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not node.value.startswith(("docker", "compose")),                 f"helper 不得构造服务命令字面量: {node.value[:16]!r}"


def test_env_template_documents_slots_commented_direct_default() -> None:
    """模板三槽位以注释形态在档（直连 = canonical 模板默认），无激活代理行。"""
    text = ENV_TEMPLATE.read_text(encoding="utf-8")
    for key in ("AIOS_SEARXNG_HTTP_PROXY", "AIOS_SEARXNG_HTTPS_PROXY",
                "AIOS_SEARXNG_NO_PROXY"):
        assert f"# {key}=" in text  # 注释形态文档在档
        assert f"\n{key}=" not in text  # 无激活代理行
    assert "searxng_egress_recovery" in text  # 恢复工具指引在档
    # 模板槽位区在 PIN 九键之后（pin 区块不被挤压改写）
    pin_block = text.index("AIOS_IMAGE_TAG=")
    slot_block = text.index("# AIOS_SEARXNG_HTTP_PROXY=")
    assert pin_block < slot_block


def test_compose_comment_carries_recovery_contract() -> None:
    """compose searxng 注释与模板双载 M14-148 恢复口径（防漂移锚点）。"""
    compose = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "searxng_egress_recovery" in compose
    assert "可恢复的生产默认" in compose


def test_slot_keys_match_compose_contract() -> None:
    """helper 禁用集 = compose 三槽位中的两个代理启用键（NO_PROXY 除外）。"""
    compose = COMPOSE_FILE.read_text(encoding="utf-8")
    for key in sr.EGRESS_PROXY_SLOT_KEYS:
        assert f"${{{key}:-}}" in compose
