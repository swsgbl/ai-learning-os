"""M14-210 provider smoke container：容器 /app 布局支持的四组聚焦测试。

背景：生产镜像（services/api/Dockerfile，WORKDIR /app）把 app 布局为
/app/app、VERSION 打包到 /app/VERSION、infra/smoke_*.sh 打包到
/app/infra——provider-smoke 证据编排模块与冒烟脚本都必须在这种布局下
可用，而不再只假设源码 checkout 的 parents[4] 固定偏移。

覆盖矩阵：
1. 仓库根解析（container layout）：_locate_repository_root 从源码 checkout
   模块位置与容器 /app/app/ops 模块位置都解析到带 VERSION + infra 标记
   的根；标记缺失抛明确 RuntimeError；_REPOSITORY_ROOT 常量与解析结果
   一致且四个 PROVIDERS 脚本相对根可见；模块源码不再出现 parents[N]
   固定偏移（ast 守卫）；
2. 脚本选择链（script selection）：五个选择解释器的冒烟脚本共享字节
   相同的 M14-210 选择链块（显式 PYTHON → .venv/Scripts/python.exe →
   .venv/bin/python → python → python3，显式不可用即 FAIL）；smoke_docker.sh
   恒用系统 python3（宿主侧脚本，不选仓库 venv，不在链条改造面）；行为面
   在沙箱（零网络、零真实 provider）验证代表性脚本 smoke_voice_local.sh：
   显式 PYTHON 优先（压过 venv）、venv Scripts 形态先于 bin 形态、无 venv
   时落 PATH 系统 python（且 python 先于 python3）、显式 PYTHON 不可用即
   干净 FAIL、exec 退出码透传；环境门控脚本（smoke_search.sh）在显式
   PYTHON 不可用时于任何探针之前 FAIL（loopback 假端点从未被触碰）；
3. Dockerfile 打包：services/api/Dockerfile 以 glob COPY 把 infra/smoke_*.sh
   打进 /app/infra、VERSION 进 /app/VERSION、WORKDIR /app——容器布局
   标记与 _locate_repository_root 的标记契约一致；glob 覆盖全部
   PROVIDERS 脚本；
4. 输出护栏（output guardrails）：容器根注入 _REPOSITORY_ROOT 后护栏
   语义不变——输出越界/文件名不精确 => ProviderSmokeInputError 且不运行
   冒烟；合法输出 => runner 收到 [bash, infra/smoke_*.sh]；真实
   _run_smoke_script 的 cwd 跟随解析出的容器根（python -c 探针回读）。

全部测试只用临时目录、本地文件与本地 bash 子进程（bash 缺失的环境对
行为面测试跳过）——零真实 provider、零网络、零数据库。
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.ops import provider_smoke_evidence as pse
from app.ops.provider_smoke_evidence import PROVIDERS
from tests._subprocess_utf8 import run_utf8

REPO_ROOT = Path(__file__).resolve().parents[3]
INFRA = REPO_ROOT / "infra"
API_DOCKERFILE = REPO_ROOT / "services" / "api" / "Dockerfile"

#: 携带 M14-210 选择链的五个脚本（选择链块要求逐字节一致；smoke_docker.sh
#: 恒用系统 python3，不在链条改造面——由专属测试锁定其边界）
SELECTION_SCRIPTS = (
    "smoke_search.sh",
    "smoke_llm.sh",
    "smoke_voice.sh",
    "smoke_voice_cloud.sh",
    "smoke_voice_local.sh",
)

_SELECTION_START = "# M14-210 Python 选择链"
_SELECTION_INVOCATION = "\nselect_python\n"


def _selection_block(script: Path) -> str:
    """提取脚本的 M14-210 选择链块（注释首行到 select_python 调用行）；
    缺块即 ValueError，测试响亮失败。"""
    text = script.read_text(encoding="utf-8")
    start = text.index(_SELECTION_START)
    end = text.index(_SELECTION_INVOCATION, start) + len(_SELECTION_INVOCATION)
    return text[start:end]


# --- 1. 仓库根解析（container layout） --------------------------------------------


def test_repository_root_resolves_source_checkout() -> None:
    """源码 checkout 布局：模块从 services/api/app/ops 出发解析到真仓库根
    （VERSION + infra 标记齐备），_REPOSITORY_ROOT 常量与之一致，四个
    PROVIDERS 脚本相对根可见。"""
    root = pse._locate_repository_root()
    assert root == pse._REPOSITORY_ROOT
    assert (root / "VERSION").is_file()
    assert (root / "infra").is_dir()
    for spec in PROVIDERS.values():
        assert (root / spec.script).is_file()


def test_repository_root_resolves_container_app_layout(tmp_path) -> None:
    """容器 /app 布局（Dockerfile COPY services/api/app ./app + VERSION +
    infra/smoke_*.sh）：模块位于 /app/app/ops——从该位置出发解析到 /app
    （旧 parents[4] 在此布局下越界 IndexError），四个 PROVIDERS 脚本相对
    /app 可见。"""
    app_root = tmp_path / "app"
    module_dir = app_root / "app" / "ops"
    module_dir.mkdir(parents=True)
    (app_root / "VERSION").write_text("0.0.0-container\n", encoding="utf-8")
    for spec in PROVIDERS.values():
        target = app_root / spec.script
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    resolved = pse._locate_repository_root(
        module_dir / "provider_smoke_evidence.py"
    )
    assert resolved == app_root.resolve()
    for spec in PROVIDERS.values():
        assert (resolved / spec.script).is_file()


def test_repository_root_missing_markers_raises_runtime_error(tmp_path) -> None:
    """标记缺失（无 VERSION/infra 的目录树）=> 明确 RuntimeError——不允许
    含混失败（如固定偏移越界的 IndexError）。"""
    orphan = tmp_path / "orphan" / "app" / "ops"
    orphan.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="仓库根"):
        pse._locate_repository_root(orphan / "provider_smoke_evidence.py")


def test_module_has_no_pinned_parents_offset() -> None:
    """ast 守卫：模块不得再依赖 parents[N] 固定偏移解析仓库根（容器 /app
    布局越界）——仓库根一律经 _locate_repository_root 标记查找。"""
    tree = ast.parse(Path(pse.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "parents"
        ):
            pytest.fail("模块不得使用 parents[N] 固定偏移解析仓库根（容器布局越界）")


def test_repository_root_rejects_version_only_ancestor(tmp_path) -> None:
    """强契约回归：VERSION-only 祖先（缺 infra 目录）不得解析为根——
    provider-smoke 编排冒烟脚本的契约是 VERSION + infra 齐备；弱契约
    （locate_repository_root(require_infra=False)）会放行，强 wrapper
    （require_infra=True）必须 fail-closed 抛 RuntimeError。

    场景：模块位于 tmp_path/app/ops，tmp_path/VERSION 存在但 tmp_path/infra
    不存在——模拟容器布局只打了 VERSION 漏打 infra/smoke_*.sh 的错误打包。
    """
    module_dir = tmp_path / "app" / "ops"
    module_dir.mkdir(parents=True)
    (tmp_path / "VERSION").write_text("0.0.0-no-infra\n", encoding="utf-8")
    # 弱契约放行（VERSION 标记存在）
    weak = pse.locate_repository_root(
        module_dir / "provider_smoke_evidence.py", require_infra=False
    )
    assert weak == tmp_path.resolve()
    # 强契约 fail-closed（infra 缺失）
    with pytest.raises(RuntimeError, match="infra"):
        pse._locate_repository_root(module_dir / "provider_smoke_evidence.py")


def test_repository_root_constant_uses_strong_contract() -> None:
    """常量 _REPOSITORY_ROOT 必须与强 wrapper（require_infra=True）一致——
    不静默回落到 VERSION-only 弱契约（locate_repository_root() 默认
    require_infra=False）。"""
    strong = pse._locate_repository_root()
    assert pse._REPOSITORY_ROOT == strong
    # 强契约语义：常量根同时具备 VERSION 与 infra 标记
    assert (pse._REPOSITORY_ROOT / "VERSION").is_file()
    assert (pse._REPOSITORY_ROOT / "infra").is_dir()


def test_repository_root_rejects_version_only_container_layout(tmp_path) -> None:
    """容器布局回归：/app 布局只打 VERSION 漏打 infra 不得解析为根——
    模拟生产镜像 Dockerfile 漏 COPY infra/smoke_*.sh（编排工具在容器内
    定位不到冒烟脚本即应 fail-closed，而非放行后 subprocess 找不到脚本）。"""
    app_root = tmp_path / "app"
    module_dir = app_root / "app" / "ops"
    module_dir.mkdir(parents=True)
    (app_root / "VERSION").write_text("0.0.0-container-no-infra\n", encoding="utf-8")
    # VERSION 存在但 infra 目录缺失——强契约拒绝
    with pytest.raises(RuntimeError, match="infra"):
        pse._locate_repository_root(module_dir / "provider_smoke_evidence.py")


# --- 2. 脚本选择链（script selection） ---------------------------------------------


def test_selection_block_identical_across_scripts() -> None:
    """五个脚本共享字节相同的选择链块，且链条顺序内容锁定：显式 PYTHON →
    .venv/Scripts/python.exe → .venv/bin/python → python → python3；显式
    PYTHON 不可用即 FAIL（不静默换用其它解释器）。"""
    blocks = {name: _selection_block(INFRA / name) for name in SELECTION_SCRIPTS}
    assert len(set(blocks.values())) == 1, "五个脚本必须共享字节相同的选择链块"
    block = next(iter(set(blocks.values())))
    assert block.index('"${PYTHON:-}"') < block.index(".venv/Scripts/python.exe")
    assert block.index(".venv/Scripts/python.exe") < block.index(".venv/bin/python")
    assert block.index(".venv/bin/python") < block.index("python python3")
    assert "PYTHON 指定的解释器不可用" in block


def test_old_venv_only_selection_gone() -> None:
    """旧 venv-only 选择形态必须清除：不再有「找不到项目 venv python」的
    venv-only 失败消息与 venv 缺省回落写法（容器内会误拒系统 python）。"""
    for name in SELECTION_SCRIPTS:
        text = (INFRA / name).read_text(encoding="utf-8")
        assert "找不到项目 venv python" not in text
        assert 'PYTHON="${PYTHON:-.venv/Scripts/python.exe}"' not in text


def test_smoke_docker_uses_system_python_without_venv_chain() -> None:
    """smoke_docker.sh 是宿主侧脚本（docker compose + 仓库 VERSION 文件，
    镜像内无 docker 守护进程可编排）：恒用系统 python3 做本地 JSON 解析、
    从不选择仓库 venv——不在 M14-210 选择链改造面。"""
    text = (INFRA / "smoke_docker.sh").read_text(encoding="utf-8")
    assert "select_python" not in text
    assert ".venv/" not in text


def _sandbox_app(tmp_path: Path, *script_names: str) -> Path:
    """容器形态沙箱：/app 布局（VERSION + infra 冒烟脚本副本）——行为面
    测试在此运行真实脚本（零网络：探针位与解释器全部为 fake）。"""
    app_root = tmp_path / "sandbox-app"
    (app_root / "infra").mkdir(parents=True)
    (app_root / "VERSION").write_text("0.0.0-sandbox\n", encoding="utf-8")
    for name in script_names:
        shutil.copy2(INFRA / name, app_root / "infra" / name)
    return app_root


def _write_fake_interpreter(
    path: Path, label: str, marker: Path, exit_code: int
) -> None:
    """写一个 fake 解释器（bash 包装）：记录 label 与收到的参数到 marker
    后以 exit_code 退出——被选中即留下可断言痕迹。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "#!/usr/bin/env bash",
        f"echo '{label} args='\"$*\" >> '{marker.as_posix()}'",
        f"exit {exit_code}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _is_system32_wsl_launcher(path: str) -> bool:
    """判定路径是否为 System32\\bash.exe 的 WSL 启动器（非 Git Bash，语义
    不同会让冒烟脚本行为面失真）——按归一化小写正斜杠路径含 system32 段
    或以 system32/bash.exe 结尾判定。"""
    normalized = path.lower().replace("\\", "/")
    return "/system32/" in normalized or normalized.endswith("/system32/bash.exe")


def _resolve_native_git_bash() -> str | None:
    """解析一个原生 Windows Git Bash——避开 System32\\bash.exe（WSL 启动器，
    非 Git Bash，语义不同会让冒烟脚本行为面失真）。

    顺序（确定性、可移植，不依赖固定本机安装路径）：
    1. ``CLAUDE_CODE_GIT_BASH_PATH`` 环境变量（指向存在可执行文件，且非
       System32 WSL 启动器——允许显式注入但拒绝 WSL 启动器）；
    2. PATH 上 ``shutil.which("bash")`` 的结果——排除 System32 bash；
    3. 由 ``shutil.which("git")`` 派生原生 Git Bash：检查 git.exe 同目录的
       bash.exe，以及 git 安装根下的 bin/bash.exe（覆盖 git.exe 位于根、
       bin/ 或 cmd/ 的常见布局）——排除 System32。

    都不命中 => None（调用方按行为驱动跳过子测试，不在缺 Git Bash 的
    环境伪造结论）。
    """
    explicit = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    if (
        explicit
        and Path(explicit).is_file()
        and not _is_system32_wsl_launcher(explicit)
    ):
        return explicit
    which = shutil.which("bash")
    if which is not None and not _is_system32_wsl_launcher(which):
        return which
    git = shutil.which("git")
    if git is not None:
        git_dir = Path(git).parent
        candidates = (
            git_dir / "bash.exe",  # git.exe 同目录的 bash.exe
            git_dir / "bin" / "bash.exe",  # <git-root>/bin/bash.exe
            git_dir.parent / "bin" / "bash.exe",  # <git-root>/bin/bash.exe（git.exe 在 cmd/ 下）
        )
        for candidate in candidates:
            cand = str(candidate)
            if candidate.is_file() and not _is_system32_wsl_launcher(cand):
                return cand
    return None


def _run_bash(argv: list[str], cwd: Path, env: dict[str, str]):
    """沙箱内运行冒烟脚本（UTF-8 文本模式经仓库统一 helper——Windows
    locale GBK 解码会炸在中文 FAIL 消息上）。bash 解析选原生 Git Bash
    （见 _resolve_native_git_bash）：缺原生 Git Bash 时行为驱动跳过——
    不在缺少该工具的环境伪造行为面结论。"""
    bash = _resolve_native_git_bash()
    if bash is None:
        pytest.skip(
            "此环境无原生 Git Bash（冒烟脚本行为面测试需要 Git Bash；"
            "System32\\bash.exe 的 WSL 启动器不计入）"
        )
    return run_utf8([bash, *argv], timeout=60, cwd=str(cwd), env=env)


def _script_env(**extra: str) -> dict[str, str]:
    """干净子进程环境：剥离一切显式 PYTHON（含 Windows 大小写变体），
    再叠加每用例的显式注入。"""
    env = {k: v for k, v in os.environ.items() if k.upper() != "PYTHON"}
    env.update(extra)
    return env


def test_selection_explicit_python_wins_and_exit_code_passthrough(tmp_path) -> None:
    """显式 PYTHON 优先：fake 解释器被原样启用（即便仓库 venv 同时存在），
    exec 退出码原样透传。"""
    app = _sandbox_app(tmp_path, "smoke_voice_local.sh")
    marker = tmp_path / "marker.log"
    fake = tmp_path / "explicit-python"
    _write_fake_interpreter(fake, "explicit-choice", marker, 7)
    _write_fake_interpreter(app / ".venv" / "bin" / "python", "venv-bin", marker, 0)
    proc = _run_bash(
        ["infra/smoke_voice_local.sh"],
        app,
        _script_env(PYTHON=fake.as_posix()),
    )
    assert proc.returncode == 7, "exec 的探针退出码必须原样透传"
    text = marker.read_text(encoding="utf-8")
    assert "explicit-choice args=tools/voice/smoke_local_voice.py" in text
    assert "venv-bin" not in text, "显式 PYTHON 必须压过仓库 venv"


def test_selection_venv_windows_shape_preferred_over_posix(tmp_path) -> None:
    """仓库 venv 先于系统 python，且 Windows 形态（.venv/Scripts/python.exe）
    先于 POSIX 形态（.venv/bin/python）。"""
    app = _sandbox_app(tmp_path, "smoke_voice_local.sh")
    marker = tmp_path / "marker.log"
    _write_fake_interpreter(
        app / ".venv" / "Scripts" / "python.exe", "venv-scripts", marker, 7
    )
    _write_fake_interpreter(app / ".venv" / "bin" / "python", "venv-bin", marker, 3)
    proc = _run_bash(["infra/smoke_voice_local.sh"], app, _script_env())
    assert proc.returncode == 7
    text = marker.read_text(encoding="utf-8")
    assert "venv-scripts args=" in text
    assert "venv-bin" not in text


def test_selection_venv_posix_shape_when_no_windows_shape(tmp_path) -> None:
    """无 Windows 形态 venv 时选 POSIX 形态（.venv/bin/python）。"""
    app = _sandbox_app(tmp_path, "smoke_voice_local.sh")
    marker = tmp_path / "marker.log"
    _write_fake_interpreter(app / ".venv" / "bin" / "python", "venv-bin", marker, 7)
    proc = _run_bash(["infra/smoke_voice_local.sh"], app, _script_env())
    assert proc.returncode == 7
    assert "venv-bin args=tools/voice/smoke_local_voice.py" in marker.read_text(
        encoding="utf-8"
    )


def test_selection_falls_back_to_system_python_without_venv(tmp_path) -> None:
    """无 venv（容器形态）：链条落到 PATH 系统 python——用受控 fake bin
    前置 PATH 锁定选择，且 python 先于 python3（两级 fake 均可用时选
    python 的退出码）。"""
    app = _sandbox_app(tmp_path, "smoke_voice_local.sh")
    marker = tmp_path / "marker.log"
    fake_bin = tmp_path / "fake-bin"
    _write_fake_interpreter(fake_bin / "python", "system-python-a", marker, 5)
    _write_fake_interpreter(fake_bin / "python3", "must-not-pick-b", marker, 9)
    env = _script_env(
        PATH=os.pathsep.join([str(fake_bin), os.environ.get("PATH", "")])
    )
    proc = _run_bash(["infra/smoke_voice_local.sh"], app, env)
    assert proc.returncode == 5, "应选中 PATH 上的 python（而非 python3）"
    text = marker.read_text(encoding="utf-8")
    assert "system-python-a args=tools/voice/smoke_local_voice.py" in text
    assert "must-not-pick-b" not in text


def test_selection_explicit_python_unusable_fails_cleanly(tmp_path) -> None:
    """显式 PYTHON 不可用 => 干净 FAIL（exit 1 + 明确消息），不静默换用
    仓库 venv 或系统 python（沙箱里同时放置可用 venv 证明不被回退）。"""
    app = _sandbox_app(tmp_path, "smoke_voice_local.sh")
    marker = tmp_path / "marker.log"
    _write_fake_interpreter(app / ".venv" / "bin" / "python", "venv-bin", marker, 0)
    proc = _run_bash(
        ["infra/smoke_voice_local.sh"],
        app,
        _script_env(PYTHON=(tmp_path / "no-such-python").as_posix()),
    )
    assert proc.returncode == 1
    assert "PYTHON 指定的解释器不可用" in proc.stderr
    assert not marker.exists(), "显式 PYTHON 不可用时不得回退运行其它解释器"


def test_selection_failure_preempts_any_probe_on_env_gated_script(tmp_path) -> None:
    """环境门控脚本（smoke_search.sh：先查 SEARCH_CLOUD_ENDPOINT 再选
    python）：显式 PYTHON 不可用时选择链 FAIL 先于任何探针执行——
    loopback 假端点从未被触碰（无 traceback、明确 FAIL 消息、exit 1）。"""
    app = _sandbox_app(tmp_path, "smoke_search.sh")
    env = _script_env(
        PYTHON=(tmp_path / "no-such-python").as_posix(),
        SEARCH_CLOUD_ENDPOINT="http://127.0.0.1:9",  # 即便探针越权执行也只到回环
    )
    proc = _run_bash(["infra/smoke_search.sh"], app, env)
    assert proc.returncode == 1
    assert "PYTHON 指定的解释器不可用" in proc.stderr
    assert "Traceback" not in proc.stderr, "探针不得启动（选择失败先于探针）"


# --- 3. Dockerfile 打包 -------------------------------------------------------------


def test_dockerfile_packages_smoke_scripts_into_app_infra() -> None:
    """容器布局契约与 _locate_repository_root 标记一致：WORKDIR /app、
    VERSION 打包到 /app/VERSION、infra/smoke_*.sh glob 打包到 /app/infra
    ——容器内仓库根可按 VERSION + infra 标记解析。"""
    text = API_DOCKERFILE.read_text(encoding="utf-8")
    assert "WORKDIR /app" in text
    assert "COPY VERSION ./VERSION" in text
    assert "COPY infra/smoke_*.sh ./infra/" in text


def test_dockerfile_glob_covers_all_provider_scripts() -> None:
    """glob 打包面覆盖全部 PROVIDERS 脚本（编排工具在容器内能定位每个
    注册 provider 的脚本）。"""
    packaged = {path.name for path in INFRA.glob("smoke_*.sh")}
    assert packaged, "infra 必须存在 smoke_*.sh（Dockerfile glob 打包面）"
    for spec in PROVIDERS.values():
        script = Path(spec.script)
        assert script.parent == Path("infra")
        assert script.name in packaged


# --- 4. 输出护栏（output guardrails，容器根注入） -----------------------------------


def _guard_container_root(tmp_path: Path) -> Path:
    """容器 /app 布局沙箱（VERSION + 四个 PROVIDERS 脚本副本）——供
    _REPOSITORY_ROOT 注入。"""
    app_root = tmp_path / "guard-app"
    (app_root / "infra").mkdir(parents=True)
    (app_root / "VERSION").write_text("0.0.0-guard\n", encoding="utf-8")
    for spec in PROVIDERS.values():
        shutil.copy2(REPO_ROOT / spec.script, app_root / spec.script)
    return app_root


def _install_fake_runner(monkeypatch) -> list[list[str]]:
    """fake runner + fake bash 解析（零真实子进程）；返回调用记录。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        pse.shutil, "which", lambda name: "/fake/bin/bash" if name == "bash" else None
    )

    def run(argv):
        calls.append(list(argv))
        return subprocess.CompletedProcess(args=list(argv), returncode=0)

    monkeypatch.setattr(pse, "_run_smoke_script", run)
    return calls


def test_output_guardrails_hold_under_container_root(tmp_path, monkeypatch) -> None:
    """容器根注入后输出护栏语义不变：越界（父目录非 artifacts/temp）与
    文件名不精确 => ProviderSmokeInputError 且不运行冒烟；合法输出 =>
    runner 收到 [bash, infra/smoke_*.sh]。"""
    app_root = _guard_container_root(tmp_path)
    monkeypatch.setattr(pse, "_REPOSITORY_ROOT", app_root)
    calls = _install_fake_runner(monkeypatch)
    outside = tmp_path / "outside" / "search-smoke.json"
    outside.parent.mkdir()
    with pytest.raises(pse.ProviderSmokeInputError):
        pse.build_step_evidence("search", outside, bash="/fake/bash")
    assert calls == [], "路径护栏必须先于 runner（不运行冒烟）"
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    with pytest.raises(pse.ProviderSmokeInputError):
        pse.build_step_evidence(
            "search", artifacts / "wrong-name.json", bash="/fake/bash"
        )
    assert calls == [], "文件名护栏必须先于 runner（不运行冒烟）"
    evidence, exit_code = pse.build_step_evidence(
        "search", artifacts / "search-smoke.json", bash="/fake/bash"
    )
    assert exit_code == 0
    assert evidence["result"] == "pass"
    assert calls == [["/fake/bash", "infra/smoke_search.sh"]]


def test_real_runner_cwd_tracks_resolved_container_root(tmp_path, monkeypatch) -> None:
    """真实 _run_smoke_script 的 cwd 跟随模块当前解析的仓库根（容器根注入
    后 cwd=容器根）——python -c 探针回读 cwd（零网络、无 bash 依赖）。"""
    app_root = _guard_container_root(tmp_path)
    monkeypatch.setattr(pse, "_REPOSITORY_ROOT", app_root)
    probe = tmp_path / "cwd-probe.json"
    code = (
        "import json, os\n"
        f"json.dump({{'cwd': os.getcwd()}}, open({str(probe)!r}, 'w'))\n"
    )
    proc = pse._run_smoke_script([sys.executable, "-c", code])
    assert proc.returncode == 0
    payload = json.loads(probe.read_text(encoding="utf-8"))
    assert Path(payload["cwd"]).resolve() == app_root.resolve()
