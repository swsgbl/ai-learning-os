"""M11-14 GitHub Actions 运行时主版本：checkout/setup-node/setup-python 钉 v7。

背景：官方 actions/checkout、actions/setup-node、actions/setup-python 的
v4/v5 主版本运行在 node20 runtime 上，GitHub 托管 runner 已对其打
Node.js 20 deprecation 警告；三个 action 的 v7 主版本（checkout v7.0.1、
setup-node v7.0.0、setup-python v7.0.0）切换到 node24 runtime。本切片只
把 ci.yml 与 release-candidate.yml 里这三个 action 的主版本升到 v7——
不改触发条件、权限、runner、job 结构、构建/测试命令、Node 22 /
Python 3.11 版本策略，也不升级 actions/upload-artifact（仍在 v4）。

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

# 每个 workflow 中各目标 action 的预期出现次数（ci 四个 job 各一次
# checkout（web/api/docker/android），setup-node 仅 web job，setup-python 仅
# api job，setup-java 仅 android job；RC 单 job）。
EXPECTED_USES = {
    CI_WORKFLOW: {
        "actions/checkout": 4,
        "actions/setup-node": 1,
        "actions/setup-python": 1,
        "actions/setup-java": 1,
    },
    RC_WORKFLOW: {
        "actions/checkout": 1,
        "actions/setup-python": 1,
    },
}

# M12-01：Gradle wrapper 校验 action 的钉定主版本（官方 gradle/actions
# 的 wrapper-validation 当前主流稳定主版本）。
WRAPPER_VALIDATION_ACTION = "gradle/actions/wrapper-validation"
WRAPPER_VALIDATION_MAJOR = 4
ANDROID_JOB_NAME = "android"
ANDROID_GRADLE_TASKS = ("testDebugUnitTest", "lintDebug", "assembleDebug")

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


# --- 1b. M12-01 android job 契约（Java 17 / wrapper validation / 门禁命令）------


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
