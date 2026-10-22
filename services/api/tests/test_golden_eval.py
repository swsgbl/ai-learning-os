"""M16-009 黄金集离线评测门禁（升级方案 Phase 7 验收：CI 可重复）。

直接复用 scripts/run_golden_eval.py 的 main()——CI 中任何蓝图/判分/
掌握度规则回归都会在此暴露（纯离线：无网络、无 LLM、无 DB）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_main():
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "run_golden_eval.py"
    )
    spec = importlib.util.spec_from_file_location("run_golden_eval", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_golden_eval"] = module
    spec.loader.exec_module(module)
    return module.main


def test_golden_eval_all_pass() -> None:
    main = _load_main()
    assert main() == 0, "黄金集评测存在失败项（见输出明细）"
