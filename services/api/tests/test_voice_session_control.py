"""M14-271 VAD/barge-in/pause-resume 服务端权威会话控制契约测试（fake-only）。

覆盖（与切片验收面一一对应）：
1  服务端权威：VoiceSessionController 包装 M4-03 FSM——状态迁移
   只经服务端 submit 发生；客户端只消费 snapshot（本模块零
   客户端概念）；
2  barge-in：播报期（READING_QUESTION/READING_OPTIONS）→
   WAITING_ANSWER（停止播报进倾听）；倾听期自环幂等；
   ANSWER_COMMITTED/SESSION_READY/NEXT_QUESTION 拒绝（FSM 矩阵
   口径）；
3  VAD 信号映射：speech_start 在播报期等价 barge_in（迁移）/
   倾听期幂等自环；speech_end 零状态迁移（只记审计——答案
   提交属 ASR/transcript 链）；未知信号拒绝；
4  pause/resume：任意非终态自环、重复幂等（状态不变每次记
   审计）、终态拒绝；
5  事件幂等：同 (状态, 事件) 恒同输出（迁移与投影确定性）；
6  revision 乐观并发：正确 revision 应用且 +1；不匹配拒绝
   （状态不变、零审计残留）；缺省直接应用；
7  审计可查：seq 单调、有界环形（超上限淘汰最旧 + 全量计数）、
   事件内容白名单（event/from_state/to_state/revision——无
   凭据/无音频/无文本）、快照全新拷贝；
8  终态：REPORT_READY 后一切事件确定性拒绝；
9  非法输入：未知事件名/未知初始状态/非法 audit 上限拒绝；
10 源码卫生（AST+文本）：纯标准库 + 域内 FSM 引用；无网络/
   文件/线程/asyncio/HTTP；无凭据形态。

全部零网络零真实连接；FSM 矩阵为 M4-03 既有权威口径。
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from app.domain.voice_session_control import (
    VAD_SPEECH_END,
    VAD_SPEECH_START,
    VoiceSessionControlError,
    VoiceSessionController,
)
from app.domain.voice_session_fsm import (
    ANSWER_COMMITTED,
    NEXT_QUESTION,
    READING_OPTIONS,
    READING_QUESTION,
    REPORT_READY,
    SESSION_READY,
    WAITING_ANSWER,
)

FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.sig-sig-sig"
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = (
    REPO_ROOT / "services" / "api" / "app" / "domain" / "voice_session_control.py"
)
MODULE_SOURCE = MODULE_PATH.read_text(encoding="utf-8")


def at(controller: VoiceSessionController, state: str) -> VoiceSessionController:
    """便捷：新建一个处于指定状态的控制器（经合法迁移到达）。"""
    controller = VoiceSessionController()
    if state == READING_QUESTION:
        controller.submit("start_reading")
    elif state == READING_OPTIONS:
        controller.submit("start_reading")
        controller.submit("question_read")
    elif state == WAITING_ANSWER:
        controller.submit("start_reading")
        controller.submit("question_read")
        controller.submit("options_read")
    elif state == ANSWER_COMMITTED:
        controller.submit("start_reading")
        controller.submit("question_read")
        controller.submit("options_read")
        controller.submit("answer_proposed")
    elif state == NEXT_QUESTION:
        controller.submit("start_reading")
        controller.submit("question_read")
        controller.submit("options_read")
        controller.submit("answer_proposed")
        controller.submit("commit_confirmed")
    elif state == REPORT_READY:
        controller.submit("end")
    return controller


# ---------- 9：构造与非法输入 ----------


def test_constructor_rejects_unknown_initial_state() -> None:
    with pytest.raises(VoiceSessionControlError):
        VoiceSessionController("NOT_A_STATE")


@pytest.mark.parametrize("bad", [0, -1])
def test_constructor_rejects_invalid_audit_cap(bad) -> None:
    with pytest.raises(VoiceSessionControlError):
        VoiceSessionController(max_audit_events=bad)


def test_unknown_event_rejected() -> None:
    controller = VoiceSessionController()
    with pytest.raises(VoiceSessionControlError):
        controller.submit("not_an_event")


# ---------- 1：服务端权威 + 全链冒烟 ----------


def test_full_lifecycle_transitions() -> None:
    controller = VoiceSessionController()
    assert controller.snapshot()["state"] == SESSION_READY
    assert controller.submit("start_reading")["state"] == READING_QUESTION
    assert controller.submit("question_read")["state"] == READING_OPTIONS
    assert controller.submit("options_read")["state"] == WAITING_ANSWER
    assert controller.submit("answer_proposed")["state"] == ANSWER_COMMITTED
    assert controller.submit("commit_confirmed")["state"] == NEXT_QUESTION
    assert controller.submit("start_reading")["state"] == READING_QUESTION
    assert controller.submit("end")["state"] == REPORT_READY
    assert controller.snapshot()["revision"] == 8


# ---------- 2：barge-in ----------


@pytest.mark.parametrize(
    "broadcast_state", [READING_QUESTION, READING_OPTIONS]
)
def test_barge_in_from_broadcast_to_listening(broadcast_state) -> None:
    controller = at(VoiceSessionController(), broadcast_state)
    result = controller.submit("barge_in")
    assert result["state"] == WAITING_ANSWER
    audit = controller.snapshot()["audit_events"]
    assert audit[-1]["event"] == "barge_in"
    assert audit[-1]["from_state"] == broadcast_state
    assert audit[-1]["to_state"] == WAITING_ANSWER


def test_barge_in_in_listening_idempotent_selfloop() -> None:
    controller = at(VoiceSessionController(), WAITING_ANSWER)
    first = controller.submit("barge_in")
    second = controller.submit("barge_in")
    assert first["state"] == WAITING_ANSWER
    assert second["state"] == WAITING_ANSWER  # 幂等自环


@pytest.mark.parametrize(
    "state", [SESSION_READY, ANSWER_COMMITTED, NEXT_QUESTION]
)
def test_barge_in_rejected_outside_broadcast_or_listening(state) -> None:
    controller = at(VoiceSessionController(), state)
    with pytest.raises(VoiceSessionControlError):
        controller.submit("barge_in")
    assert controller.snapshot()["state"] == state  # 状态不变
    assert controller.snapshot()["revision"] > 0  # 之前迁移仍在
    # 被拒事件不留审计
    events = [e["event"] for e in controller.snapshot()["audit_events"]]
    assert "barge_in" not in events


# ---------- 3：VAD 信号映射 ----------


@pytest.mark.parametrize("broadcast_state", [READING_QUESTION, READING_OPTIONS])
def test_vad_speech_start_triggers_barge_in(broadcast_state) -> None:
    controller = at(VoiceSessionController(), broadcast_state)
    result = controller.submit_vad(VAD_SPEECH_START)
    assert result["state"] == WAITING_ANSWER
    last = controller.snapshot()["audit_events"][-1]
    assert last["event"] == "vad_speech_start"
    assert last["to_state"] == WAITING_ANSWER


def test_vad_speech_start_in_listening_idempotent() -> None:
    controller = at(VoiceSessionController(), WAITING_ANSWER)
    result = controller.submit_vad(VAD_SPEECH_START)
    assert result["state"] == WAITING_ANSWER
    assert result["applied"] is True  # 幂等自环仍记审计


def test_vad_speech_end_no_transition_only_audit() -> None:
    controller = at(VoiceSessionController(), WAITING_ANSWER)
    before = controller.snapshot()
    result = controller.submit_vad(VAD_SPEECH_END)
    assert result["state"] == WAITING_ANSWER  # 零迁移
    after = controller.snapshot()
    assert after["audit_event_count"] == before["audit_event_count"] + 1
    assert after["audit_events"][-1]["event"] == "vad_speech_end"


def test_vad_speech_end_in_broadcast_also_allowed_no_transition() -> None:
    """speech_end 在播报期：仅审计（播报中断由 speech_start 负责）。"""
    controller = at(VoiceSessionController(), READING_QUESTION)
    result = controller.submit_vad(VAD_SPEECH_END)
    assert result["state"] == READING_QUESTION
    assert controller.snapshot()["audit_events"][-1]["event"] == "vad_speech_end"


def test_vad_unknown_signal_rejected() -> None:
    controller = VoiceSessionController()
    with pytest.raises(VoiceSessionControlError):
        controller.submit_vad("loud_noise")
    assert controller.snapshot()["audit_event_count"] == 0


def test_vad_signal_rejected_in_terminal() -> None:
    controller = at(VoiceSessionController(), REPORT_READY)
    with pytest.raises(VoiceSessionControlError):
        controller.submit_vad(VAD_SPEECH_START)


# ---------- 4：pause/resume ----------


@pytest.mark.parametrize(
    "state", [SESSION_READY, READING_QUESTION, WAITING_ANSWER, ANSWER_COMMITTED]
)
def test_pause_resume_selfloop_any_nonterminal(state) -> None:
    controller = at(VoiceSessionController(), state)
    paused = controller.submit("pause")
    resumed = controller.submit("resume")
    assert paused["state"] == state and resumed["state"] == state
    events = [e["event"] for e in controller.snapshot()["audit_events"][-2:]]
    assert events == ["pause", "resume"]


def test_repeated_pause_idempotent_state_stable() -> None:
    controller = at(VoiceSessionController(), READING_QUESTION)
    for _ in range(3):
        assert controller.submit("pause")["state"] == READING_QUESTION
    # 状态不变，审计每次记录（3 次 pause）
    pauses = [
        e for e in controller.snapshot()["audit_events"] if e["event"] == "pause"
    ]
    assert len(pauses) == 3


def test_pause_rejected_in_terminal() -> None:
    controller = at(VoiceSessionController(), REPORT_READY)
    with pytest.raises(VoiceSessionControlError):
        controller.submit("pause")


# ---------- 5：事件幂等（确定性） ----------


def test_same_state_event_deterministic_output() -> None:
    for _ in range(2):
        controller = at(VoiceSessionController(), READING_QUESTION)
        result = controller.submit("barge_in")
        assert result["state"] == WAITING_ANSWER
        assert result["revision"] == controller.snapshot()["revision"]


# ---------- 6：revision 乐观并发 ----------


def test_revision_optimistic_concurrency() -> None:
    controller = at(VoiceSessionController(), WAITING_ANSWER)
    revision = controller.snapshot()["revision"]
    result = controller.submit("answer_proposed", revision=revision)
    assert result["state"] == ANSWER_COMMITTED
    assert result["revision"] == revision + 1


def test_revision_mismatch_rejected_no_residue() -> None:
    controller = at(VoiceSessionController(), WAITING_ANSWER)
    revision = controller.snapshot()["revision"]
    with pytest.raises(VoiceSessionControlError):
        controller.submit("answer_proposed", revision=revision + 5)
    snap = controller.snapshot()
    assert snap["state"] == WAITING_ANSWER  # 状态不变
    assert snap["revision"] == revision  # revision 不变
    applied = [
        e for e in snap["audit_events"] if e["event"] == "answer_proposed"
    ]
    assert applied == []  # 零审计残留


def test_revision_omitted_applies_directly() -> None:
    controller = at(VoiceSessionController(), WAITING_ANSWER)
    result = controller.submit("skip")
    assert result["state"] == NEXT_QUESTION


@pytest.mark.parametrize("bad_revision", [0, -1])
def test_revision_nonpositive_rejected(bad_revision) -> None:
    controller = VoiceSessionController()
    with pytest.raises(VoiceSessionControlError):
        controller.submit("start_reading", revision=bad_revision)


# ---------- 7：审计可查 ----------


def test_audit_seq_monotonic_bounded_ring() -> None:
    controller = VoiceSessionController(max_audit_events=4)
    controller.submit("start_reading")
    controller.submit("question_read")
    controller.submit("options_read")
    controller.submit("pause")
    controller.submit("resume")
    controller.submit("barge_in")
    snap = controller.snapshot()
    assert len(snap["audit_events"]) == 4  # 环形
    assert snap["audit_event_count"] == 6  # 全量计数
    seqs = [e["seq"] for e in snap["audit_events"]]
    assert seqs == sorted(seqs)
    assert [e["event"] for e in snap["audit_events"]] == [
        "options_read", "pause", "resume", "barge_in",
    ]


def test_audit_snapshot_is_fresh_copy() -> None:
    controller = VoiceSessionController()
    controller.submit("start_reading")
    controller.snapshot()["audit_events"][0]["event"] = "tampered"
    assert controller.snapshot()["audit_events"][0]["event"] == "start_reading"


def test_audit_whitelist_fields_no_secrets() -> None:
    controller = at(VoiceSessionController(), READING_QUESTION)
    controller.submit("barge_in")
    snap = controller.snapshot()
    for event in snap["audit_events"]:
        assert set(event) == {"seq", "event", "from_state", "to_state", "revision"}
    serialized = json.dumps(snap)
    assert JWT_RE.search(serialized) is None
    assert "token" not in serialized.lower()


# ---------- 8：终态 ----------


def test_terminal_rejects_everything_with_audit_zero_residue() -> None:
    controller = at(VoiceSessionController(), REPORT_READY)
    count_before = controller.snapshot()["audit_event_count"]
    for event in ("start_reading", "pause", "barge_in", "answer_proposed"):
        with pytest.raises(VoiceSessionControlError):
            controller.submit(event)
    assert controller.snapshot()["audit_event_count"] == count_before
    assert controller.snapshot()["state"] == REPORT_READY


# ---------- 10：源码卫生 ----------


def test_source_hygiene_module() -> None:
    tree = ast.parse(MODULE_SOURCE)
    allowed_roots = {"__future__", "typing", "collections"}
    allowed_domain = {"app.domain.voice_session_fsm"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in allowed_roots, alias.name
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            root = node.module or ""
            assert root.split(".")[0] in allowed_roots or root in allowed_domain, root
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else ""
            attr = func.attr if isinstance(func, ast.Attribute) else ""
            assert name not in {
                "open", "system", "popen", "run", "sleep", "getenv",
                "ensure_future", "create_task", "start_new_thread",
            }, name
            assert attr not in {
                "system", "popen", "getenv", "ensure_future",
                "create_task", "start_new_thread", "connect", "write",
            }, attr
    for forbidden in (
        "getenv(", "os.environ", "dotenv", "__import__", "eval(",
        "exec(", "socket", "subprocess", "threading", "asyncio",
        "import livekit", "from livekit", "fastapi", "pathlib",
        "httpx", "aiohttp", "requests",
    ):
        assert forbidden not in MODULE_SOURCE, forbidden
