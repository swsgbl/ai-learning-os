"""M15-015 per-user 判分（BYOK LLM）：上下文切换 / 全局回落 / 个人构建幂等。"""
from __future__ import annotations

from typing import Any

from app.llm.rubric_judge import (
    UserAwareRubricJudge,
    grading_judge_ctxvar,
)


class _FakeJudge:
    """可辨识 judge：model 名唯一，judge() 回显模型名。"""

    def __init__(self, name: str) -> None:
        self._name = name
        self.calls: list[str] = []

    @property
    def judge_model(self) -> str:
        return self._name

    def judge(self, stem: str, rubric_points: tuple[str, ...], given: str) -> Any:
        self.calls.append(given)
        return self._name


def test_wrapper_falls_back_to_global_judge():
    global_judge = _FakeJudge("global-model")
    wrapper = UserAwareRubricJudge(global_judge)
    assert wrapper.judge_model == "global-model"
    assert wrapper.judge("题", ("点",), "答") == "global-model"
    assert global_judge.calls == ["答"]


def test_wrapper_global_none_means_no_llm_grading():
    wrapper = UserAwareRubricJudge(None)
    assert wrapper.judge("题", ("点",), "答") is None


def test_context_switches_to_personal_judge():
    global_judge = _FakeJudge("global-model")
    personal = _FakeJudge("deepseek-chat")
    wrapper = UserAwareRubricJudge(global_judge)
    token = grading_judge_ctxvar.set(personal)
    try:
        assert wrapper.judge_model == "deepseek-chat"
        assert wrapper.judge("题", ("点",), "答") == "deepseek-chat"
        assert global_judge.calls == []
    finally:
        grading_judge_ctxvar.reset(token)
    # 上下文外回到全局
    assert wrapper.judge_model == "global-model"


def test_context_reset_restores_global_for_repository_scope():
    """模拟 submit 路由的 try/finally 语义：块内个人、块外全局。"""
    global_judge = _FakeJudge("global")
    wrapper = UserAwareRubricJudge(global_judge)
    personal = _FakeJudge("personal")
    token = grading_judge_ctxvar.set(personal)
    grading_judge_ctxvar.reset(token)
    assert wrapper.judge("s", ("p",), "g") == "global"
    assert personal.calls == []
