"""M11-14 GitHub Actions 运行时主版本：checkout/setup-node/setup-python 钉 v7。

背景：官方 actions/checkout、actions/setup-node、actions/setup-python 的
v4/v5 主版本运行在 node20 runtime 上，GitHub 托管 runner 已对其打
Node.js 20 deprecation 警告；三个 action 的 v7 主版本（checkout v7.0.1、
setup-node v7.0.0、setup-python v7.0.0）切换到 node24 runtime。本切片只
把 ci.yml 与 release-candidate.yml 里这三个 action 的主版本升到 v7——
不改触发条件、权限、runner、job 结构、构建/测试命令、Node 22 /
Python 3.11 版本策略，也不升级 actions/upload-artifact（仍在 v4）。

M14-243 增量（CI runner/action 稳定性钉定）：
5. 两个 workflow 全部 job 的 ``runs-on:`` 恒为 ubuntu-24.04——GitHub
   已公告 ubuntu-latest 于 2026-10-19 起向 Ubuntu 26.04 迁移
   （actions/runner-images #14748 与 GitHub Blog 2026-09-17），禁止
   依赖 latest 标签让 runner 镜像随平台漂移；
6. gradle/actions/wrapper-validation 升钉 v6（v4 为旧 node20 runtime
   主版本）。

覆盖矩阵：
1. 两个 workflow 中三个目标 action 的全部 ``uses:`` 引用（YAML 解析后
   遍历 jobs.steps 断言，不做脆弱全文 substring）主版本恰为 v7——
   v4/v5 旧 node20 主版本绝迹，且出现次数与预期一致（防 action 被静默
   增删后测试仍绿）；
2. upload-artifact 主版本保持 v4（本切片明确不升级它）；
3. 版本策略不变：setup-node 的 node-version 恒 22、setup-python 的
   python-version 恒 "3.11"（不得随 action 升级漂移）；
4. release-candidate.yml 升级后的不变量复核：仍仅 workflow_dispatch、
   permissions 仍恰为 contents: read、配置行无发布动词/自动发布行为
   （与 test_release_candidate.py 既有契约测试同向，防升级切片引入漂移）。

全部测试只读仓库内两个 workflow YAML，不连接任何数据库、不发起网络
请求、不调用真实 Docker。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RC_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-candidate.yml"

# M11-14：三个 runtime action 升 v7（node24 runtime）；v4/v5 是 node20
# 旧主版本（GitHub 已打 deprecation 警告），不得再出现。
NODE24_MAJOR = 7
# 本切片明确不升级 upload-artifact（保持 v4）。
UPLOAD_ARTIFACT_MAJOR = 4

# 每个 workflow 中各目标 action 的预期出现次数（ci 五个 job 各一次
# checkout（web/api/docker/android/release-tools），setup-node 仅 web job，
# setup-python 分别在 api 与 release-tools 两个 job；RC 单 job）。
# setup-java 不在此映射——它钉 v6（上游真实最高主版本），
# 由下方独立契约锁定，不随 node24 v7 批量断言。
EXPECTED_USES = {
    CI_WORKFLOW: {
        "actions/checkout": 5,
        "actions/setup-node": 1,
        "actions/setup-python": 2,
    },
    RC_WORKFLOW: {
        "actions/checkout": 1,
        "actions/setup-python": 1,
    },
}

# M12-01 远端实证修复：首次远端 run 34034480478 的 android job 因
# actions/setup-java@v7 不存在而失败（git ls-remote 与 GitHub releases/latest
# 均确认上游当前最高主版本为 v6）。setup-java 必须钉 v6，防止再随
# node24 v7 批量升级漂移回不存在的 v7。
SETUP_JAVA_MAJOR = 6

# M12-01：Gradle wrapper 校验 action 的钉定主版本。M14-243：升钉 v6
# （上游当前稳定主版本；v4 为旧 node20 runtime 主版本，GitHub 已对其
# 打 Node.js 20 deprecation 警告）。
WRAPPER_VALIDATION_ACTION = "gradle/actions/wrapper-validation"
WRAPPER_VALIDATION_MAJOR = 6
ANDROID_JOB_NAME = "android"
ANDROID_GRADLE_TASKS = ("testDebugUnitTest", "lintDebug", "assembleDebug")

# M14-243：GitHub 已公告 ubuntu-latest 于 2026-10-19 起向 Ubuntu 26.04
# 迁移（actions/runner-images #14748 与 GitHub Blog 2026-09-17）。两个
# workflow 全部 job 钉定 ubuntu-24.04，杜绝 latest 标签漂移。
RUNNER_LABEL = "ubuntu-24.04"
EXPECTED_JOBS = {
    CI_WORKFLOW: {"web", "api", "docker", "android", "release-tools"},
    RC_WORKFLOW: {"build-release-candidate"},
}

PUBLISH_VERBS = (
    "docker push", "docker login", "git push", "git tag", "gh release",
    "kubectl", "helm ", "terraform ", "ansible-playbook", "aws ", "gcloud ",
    "az ", "ssh ", "scp ", "release/create", "packages/write",
)


# --- 测试脚手架 ---------------------------------------------------------------


def _load_workflow(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name} 必须解析为 mapping"
    return data


def _uses_refs(data: dict) -> list[str]:
    """按 job 声明顺序收集全部 step 的 ``uses:`` 引用（含 v4 旧引用）。"""
    refs: list[str] = []
    jobs = data.get("jobs")
    assert isinstance(jobs, dict) and jobs, "workflow 必须有 jobs"
    for job in jobs.values():
        assert isinstance(job, dict)
        steps = job.get("steps", [])
        assert isinstance(steps, list)
        for step in steps:
            uses = step.get("uses") if isinstance(step, dict) else None
            if isinstance(uses, str):
                refs.append(uses)
    return refs


def _refs_for(data: dict, action: str) -> list[str]:
    return [
        ref for ref in _uses_refs(data)
        if ref.rpartition("@")[0] == action
    ]


def _steps_for(data: dict, action: str) -> list[dict]:
    steps: list[dict] = []
    for job in data.get("jobs", {}).values():
        for step in job.get("steps", []):
            uses = step.get("uses") if isinstance(step, dict) else None
            if isinstance(uses, str) and uses.rpartition("@")[0] == action:
                steps.append(step)
    return steps


def _major(ref: str) -> int:
    """``owner/repo@vN[.M.K]`` -> 主版本 N。"""
    _, _, version = ref.rpartition("@")
    major = version.lstrip("v").split(".", 1)[0]
    assert major.isdigit(), f"无法解析 action 主版本: {ref}"
    return int(major)


# --- 1. 三个 runtime action 钉 v7（v4/v5 node20 主版本绝迹）---------------------


@pytest.mark.parametrize(
    "path", [CI_WORKFLOW, RC_WORKFLOW], ids=["ci", "release-candidate"]
)
def test_node24_runtime_actions_pinned_to_v7(path: Path) -> None:
    """三个目标 action 的全部引用主版本恰为 v7——出现次数与预期一致，
    v4/v5 旧 node20 主版本绝迹（YAML 解析后断言，非全文 substring）。"""
    data = _load_workflow(path)
    for action, count in EXPECTED_USES[path].items():
        refs = _refs_for(data, action)
        assert len(refs) == count, (
            f"{path.name} 中 {action} 预期 {count} 处，"
            f"实际 {len(refs)} 处: {refs}"
        )
        for ref in refs:
            assert _major(ref) == NODE24_MAJOR, (
                f"{path.name} 的 {action} 必须钉 v{NODE24_MAJOR}"
                f"（node24 runtime），发现旧 node20 主版本: {ref}"
            )


# --- 1b. M12-01 android job 契约（setup-java v6 / Java 17 / wrapper validation / 门禁命令）


def _ci_android_job() -> dict:
    data = _load_workflow(CI_WORKFLOW)
    job = data.get("jobs", {}).get(ANDROID_JOB_NAME)
    assert isinstance(job, dict), "ci.yml 必须有 android job"
    return job


def test_android_job_pins_java_17() -> None:
    steps = _steps_for(_load_workflow(CI_WORKFLOW), "actions/setup-java")
    assert len(steps) == 1, "setup-java 仅 android job 一处"
    with_block = steps[0].get("with")
    assert isinstance(with_block, dict), "setup-java 必须带 with"
    assert str(with_block.get("java-version")) == "17", (
        f"android job 必须保持 Java 17: {with_block}"
    )


def test_setup_java_pinned_to_v6_not_nonexistent_v7() -> None:
    """setup-java 必须钉 v6——上游当前最高主版本就是 v6，v7 不存在
    （远端 run 34034480478 android job 解析 @v7 失败实证）；防止回归。"""
    refs = _refs_for(_load_workflow(CI_WORKFLOW), "actions/setup-java")
    assert len(refs) == 1, f"setup-java 预期恰一处（android job）: {refs}"
    for ref in refs:
        assert _major(ref) == SETUP_JAVA_MAJOR, (
            f"setup-java 上游真实最高主版本为 v{SETUP_JAVA_MAJOR}，"
            f"v7 不存在（run 34034480478 已实证失败）: {ref}"
        )


def test_android_job_validates_gradle_wrapper_before_build() -> None:
    """wrapper validation 必须存在，且排在构建步骤之前。"""
    steps = _ci_android_job()["steps"]
    validation_indexes = [
        i for i, step in enumerate(steps)
        if isinstance(step, dict)
        and str(step.get("uses", "")).rpartition("@")[0] == WRAPPER_VALIDATION_ACTION
    ]
    assert len(validation_indexes) == 1, (
        f"android job 恰一处 {WRAPPER_VALIDATION_ACTION}: {steps}"
    )
    validation_index = validation_indexes[0]
    ref = steps[validation_index]["uses"]
    assert _major(ref) == WRAPPER_VALIDATION_MAJOR, (
        f"wrapper-validation 必须钉 v{WRAPPER_VALIDATION_MAJOR}: {ref}"
    )
    build_indexes = [
        i for i, step in enumerate(steps)
        if isinstance(step, dict) and "gradlew" in str(step.get("run", ""))
    ]
    assert build_indexes, "android job 必须有 gradlew 构建步骤"
    assert all(validation_index < i for i in build_indexes), (
        "wrapper validation 必须先于任何 gradlew 构建步骤执行"
    )


def test_android_job_runs_full_gate_without_lint_baseline() -> None:
    """构建门禁恰为三个任务，且不得引入 lint baseline 掩盖问题。"""
    run_lines = [
        str(step.get("run", ""))
        for step in _ci_android_job()["steps"] if isinstance(step, dict)
    ]
    joined = "\n".join(run_lines)
    for task in ANDROID_GRADLE_TASKS:
        assert task in joined, f"android job 门禁必须包含 {task}: {joined}"
    assert "baseline" not in joined.lower(), (
        "不得用 lint baseline 掩盖 lint 问题"
    )
    assert "updateLintBaseline" not in joined


# --- 1c. M14-173 release-tools job 契约（两套 release 工具套件都进 CI）----------

RELEASE_TOOLS_JOB_NAME = "release-tools"
RELEASE_TOOLS_SUITES = ("harmony_release", "android_release")
# 诚实边界：release-tools 是纯 Python 静态/单测 job，绝不安装 SDK/证书/
# keystore，也绝不执行真实签名或构建命令。
RELEASE_TOOLS_FORBIDDEN_TOKENS = (
    "sdkmanager", "android-sdk", "deveco", "hdc ", "keystore", "keytool",
    "apksigner", "aapt", "assemblerelease", "assembledebug", "gradlew",
)


def _ci_release_tools_job() -> dict:
    data = _load_workflow(CI_WORKFLOW)
    job = data.get("jobs", {}).get(RELEASE_TOOLS_JOB_NAME)
    assert isinstance(job, dict), "ci.yml 必须有 release-tools job"
    return job


def _release_tools_run_text() -> str:
    runs = [
        str(step.get("run", ""))
        for step in _ci_release_tools_job()["steps"]
        if isinstance(step, dict)
    ]
    assert runs, "release-tools job 必须有 run 步骤"
    return "\n".join(runs)


def test_release_tools_job_gates_both_release_suites() -> None:
    """compileall + pytest 必须同时覆盖 harmony 与 android 两套工具套件。"""
    joined = _release_tools_run_text()
    for suite in RELEASE_TOOLS_SUITES:
        assert f"python -m compileall tools/{suite}" in joined, (
            f"release-tools 必须字节编译 tools/{suite}: {joined}"
        )
        assert f"python -m pytest tests/{suite}" in joined, (
            f"release-tools 必须运行 tests/{suite}: {joined}"
        )


def test_release_tools_job_stays_tooling_only() -> None:
    """诚实边界：不装 SDK/证书/keystore，不跑签名/构建命令，不碰真实 APK。"""
    lowered = _release_tools_run_text().lower()
    for token in RELEASE_TOOLS_FORBIDDEN_TOKENS:
        assert token not in lowered, (
            f"release-tools 是纯 Python 检查 job，不得出现 {token!r}: {lowered}"
        )


# --- 1d. M14-243 runner 钉定 ubuntu-24.04（ubuntu-latest 绝迹）------------------


@pytest.mark.parametrize(
    "path", [CI_WORKFLOW, RC_WORKFLOW], ids=["ci", "release-candidate"]
)
def test_all_jobs_pin_ubuntu_24_04(path: Path) -> None:
    """M14-243：全部 job 的 ``runs-on:`` 恒为 ubuntu-24.04。

    ubuntu-latest 已进入 Ubuntu 26.04 迁移窗口（2026-10-19 起生效），
    依赖 latest 标签会让 runner 镜像随平台静默漂移；job 集合也必须与
    契约一致，防新增 job 绕过钉定。
    """
    data = _load_workflow(path)
    jobs = data.get("jobs")
    assert isinstance(jobs, dict) and jobs, "workflow 必须有 jobs"
    assert set(jobs) == EXPECTED_JOBS[path], (
        f"{path.name} job 集合与契约不符: {sorted(jobs)}"
    )
    for name, job in jobs.items():
        assert isinstance(job, dict)
        assert job.get("runs-on") == RUNNER_LABEL, (
            f"{path.name} job {name} 必须钉 runs-on: {RUNNER_LABEL}，"
            f"发现 {job.get('runs-on')!r}（ubuntu-latest 自 2026-10-19 起"
            "迁移 Ubuntu 26.04，禁止依赖 latest 标签）"
        )


# --- 2. upload-artifact 不随本切片升级 -------------------------------------------


def test_upload_artifact_stays_v4() -> None:
    """本切片明确不升级 upload-artifact：仍钉 v4。"""
    refs = _refs_for(_load_workflow(RC_WORKFLOW), "actions/upload-artifact")
    assert refs, "release-candidate.yml 必须以 upload-artifact 上传产物"
    for ref in refs:
        assert _major(ref) == UPLOAD_ARTIFACT_MAJOR, (
            f"upload-artifact 不在本切片升级范围，应保持 "
            f"v{UPLOAD_ARTIFACT_MAJOR}: {ref}"
        )


# --- 3. Node 22 / Python 3.11 版本策略不变 --------------------------------------


def test_node_version_policy_stays_22() -> None:
    steps = _steps_for(_load_workflow(CI_WORKFLOW), "actions/setup-node")
    assert steps, "ci.yml 必须有 setup-node 步骤"
    for step in steps:
        with_block = step.get("with")
        assert isinstance(with_block, dict), (
            "setup-node 必须带 with: node-version"
        )
        assert str(with_block.get("node-version")) == "22", (
            f"Node 版本策略不得随 action 升级漂移: {with_block}"
        )


@pytest.mark.parametrize(
    "path", [CI_WORKFLOW, RC_WORKFLOW], ids=["ci", "release-candidate"]
)
def test_python_version_policy_stays_311(path: Path) -> None:
    steps = _steps_for(_load_workflow(path), "actions/setup-python")
    assert steps, f"{path.name} 必须有 setup-python 步骤"
    for step in steps:
        with_block = step.get("with")
        assert isinstance(with_block, dict), (
            "setup-python 必须带 with: python-version"
        )
        assert str(with_block.get("python-version")) == "3.11", (
            f"Python 版本策略不得随 action 升级漂移: {with_block}"
        )


# --- 4. release-candidate.yml 升级后不变量复核 ----------------------------------


def test_rc_workflow_still_dispatch_only() -> None:
    """升级 action 不得引入任何自动触发器：on 恰为 workflow_dispatch。"""
    data = _load_workflow(RC_WORKFLOW)
    triggers = data.get("on", data.get(True))  # YAML 1.1 里 on 解析为 True
    assert isinstance(triggers, dict), f"on 必须是 mapping: {triggers!r}"
    assert set(triggers) == {"workflow_dispatch"}, (
        f"只允许 workflow_dispatch 触发，发现: {sorted(triggers)}"
    )


def test_rc_workflow_permissions_still_contents_read() -> None:
    assert _load_workflow(RC_WORKFLOW).get("permissions") == {
        "contents": "read"
    }, "升级 action 不得改变最小权限 contents: read"


def test_rc_workflow_config_lines_have_no_publish_verbs() -> None:
    """非 comment 配置行零发布动词（升级 action 不得引入任何发布面）。"""
    code = "\n".join(
        ln
        for ln in RC_WORKFLOW.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ).lower()
    for verb in PUBLISH_VERBS:
        assert verb not in code, f"workflow 配置行含发布动词 {verb!r}"
