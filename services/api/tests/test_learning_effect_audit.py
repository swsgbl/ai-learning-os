"""M21 学习效果审计 CI 门禁：模拟研究可重复 + AUC 阈值（先定阈值后跑测试）。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "learning_effect_audit.py"
_spec = importlib.util.spec_from_file_location("learning_effect_audit", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["learning_effect_audit"] = mod
_spec.loader.exec_module(mod)


def test_simulation_is_deterministic() -> None:
    """固定 seed 可重复（同一 seed 两次运行 AUC 一致——CI 可重复性）。"""
    first = mod.simulate_learners(seed=mod.RANDOM_SEED)
    second = mod.simulate_learners(seed=mod.RANDOM_SEED)
    assert first["auc"] == second["auc"]
    assert first["sensitivity_mastered"] == second["sensitivity_mastered"]


def test_mastery_rule_chain_auc_passes_gate() -> None:
    """掌握度规则链 AUC ≥ 0.7（预定义阈值——禁止事后移动门槛）。"""
    result = mod.simulate_learners()
    assert result["auc"] >= mod.MIN_MODEL_AUC, "AUC below gate"
    assert result["pass"] is True


def test_mastery_false_positive_low_by_design() -> None:
    """mastered 判定假阳性 ≤ 5%（保守设计意图的客观验证）。"""
    result = mod.simulate_learners()
    assert result["false_positive_mastered"] <= 0.05


def test_auc_helper_and_effect_size_math() -> None:
    """AUC 与效应量数学正确性（手算对照）。"""
    assert mod.auc([2, 3], [1, 0]) == 1.0
    assert mod.auc([0], [1]) == 0.0
    assert mod.auc([1], [1]) == 0.5
    # 完美分离配对设计 d → 大；相同前后 d=0
    d, _g, _ci = mod.cohens_d([50.0, 50.0], [50.0, 50.0])
    assert d == 0.0
    d2, _g2, _ = mod.cohens_d([10.0, 10.0, 10.0], [90.0, 90.0, 90.0])
    assert d2 > 1.0
