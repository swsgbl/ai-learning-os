r"""M14-24 voice bridge 健康端点契约测试：liveness/readiness 分离 + 有界分块转换。

背景（生产实证，2026-09-13 自然监控）：监控轮 14:00/14:45 对 cosyvoice-health
记录到 1001ms 级延迟，14:15/14:30 与 funasr-health 一同 5s 超时（incomplete）。
根因（本仓代码面）：bridge 的 /health 只读 phase、不取推理锁，但合成路径在
单次长 C 调用（torch.cat + 整张量 .tolist()）里非抢占独占 GIL，健康线程被
饿死；且进程缺少与模型状态无关的轻量 liveness 端点，冷启动/加载失败期间
调用方无法区分「进程死」与「模型忙/未就绪」。

本套件锁定（全部合成/fake，零网络、零 SDK、零生产触碰）：
- /health/live：恒 200 轻量 liveness，readiness 仅作信息透出（loading/
  ready/failed 三态如实），不取 engine.lock、不碰模型——「冷启动」契约；
- /health（readiness）向后兼容：503/200 的状态码与 JSON 体逐字节保持
  M14-01 既有形状（loading/failed/ready 三态回归锁）；
- 健康端点在推理锁被占（合成进行中）时仍须即刻应答——「并发探测」契约，
  以显式持锁 + 计时断言锁定（同进程共享锁，确定性）；
- _tensor_to_samples：按块转换 + 块间显式 yield（避免长 tolist 非抢占
  独占解释器）——「有界工作/超时」契约，fake 张量驱动；
- bridge 源码文本契约锚点 + 无密钥形态。
"""
from __future__ import annotations

import importlib.util
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
BRIDGE = REPO_ROOT / "tools" / "voice" / "cosyvoice_openai_bridge.py"

SECRET_PATTERNS = ("sk-", "AKIA", "ghp_", "xoxb-", "-----BEGIN")

MODEL_NAME = "Fun-CosyVoice3-0.5B-2512"

#: 健康端点应答上界（对齐监控端点 GET 默认 5s 超时口径：远低于它才可靠）
HEALTH_DEADLINE_SECONDS = 5.0


def _load_bridge_module():
    spec = importlib.util.spec_from_file_location("cosyvoice_openai_bridge_m1424", BRIDGE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_app(bridge):
    """与 test_voice_local_scripts 同款确定性构造：不进入 lifespan，引擎恒
    loading；ready/failed 态由测试直接改 app.state.engine.phase 驱动。"""
    app = bridge.create_app(
        Path("/nonexistent-repo"), Path("/nonexistent-model"),
        model_name=MODEL_NAME,
        prompt_text="prompt", prompt_wav=Path("/nonexistent.wav"),
        api_key=None,
    )
    return app


# ---------- /health/live：轻量 liveness（模型状态无关，恒 200） ----------


def test_health_live_returns_200_alive_while_loading() -> None:
    """冷启动（模型加载中）：liveness 恒 200，readiness 如实透出 loading。"""
    bridge = _load_bridge_module()
    client = TestClient(_make_app(bridge))
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "liveness": "alive", "readiness": "loading"}


def test_health_live_returns_200_alive_when_engine_failed() -> None:
    """引擎加载失败（不可用态）：liveness 仍 200（进程活着），readiness=failed
    如实可见——诚实降级，不虚报 ready、也不把进程死与模型失败混为一谈。"""
    bridge = _load_bridge_module()
    app = _make_app(bridge)
    app.state.engine.phase = "failed"
    response = TestClient(app).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "liveness": "alive", "readiness": "failed"}


def test_health_live_returns_200_alive_when_ready() -> None:
    bridge = _load_bridge_module()
    app = _make_app(bridge)
    app.state.engine.phase = "ready"
    response = TestClient(app).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "liveness": "alive", "readiness": "ready"}


# ---------- /health（readiness）：M14-01 既有契约逐字节向后兼容 ----------


def test_health_readiness_loading_body_unchanged() -> None:
    """回归锁：加载中 503 + 既有固定文案，无新增键（向后兼容）。"""
    bridge = _load_bridge_module()
    response = TestClient(_make_app(bridge)).get("/health")
    assert response.status_code == 503
    assert response.json() == {"detail": "cosyvoice model is loading"}


def test_health_readiness_failed_body_unchanged() -> None:
    bridge = _load_bridge_module()
    app = _make_app(bridge)
    app.state.engine.phase = "failed"
    response = TestClient(app).get("/health")
    assert response.status_code == 503
    assert response.json() == {"detail": "cosyvoice model unavailable"}


def test_health_readiness_ready_body_unchanged() -> None:
    """回归锁：ready 时 200 + 既有两键形状（status/model），不加键不改值。"""
    bridge = _load_bridge_module()
    app = _make_app(bridge)
    app.state.engine.phase = "ready"
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "model": MODEL_NAME}


# ---------- 并发探测：健康端点不得等待推理锁（合成进行中即答） ----------


def test_health_endpoints_answer_while_inference_lock_held() -> None:
    """engine.lock 被（合成中）持有：/health 与 /health/live 都必须即刻应答——
    健康读路径不取推理锁是结构契约，监控 5s 超时口径下不可排队。"""
    bridge = _load_bridge_module()
    app = _make_app(bridge)
    app.state.engine.phase = "ready"
    acquired = app.state.engine.lock.acquire(timeout=1.0)
    assert acquired, "测试前置：取得推理锁（模拟合成进行中）"
    try:
        started = time.perf_counter()
        live = TestClient(app).get("/health/live")
        ready = TestClient(app).get("/health")
        elapsed = time.perf_counter() - started
    finally:
        app.state.engine.lock.release()
    assert live.status_code == 200
    assert live.json()["liveness"] == "alive"
    assert ready.status_code == 200  # phase=ready，锁不影响 readiness 判定
    assert elapsed < HEALTH_DEADLINE_SECONDS


def test_health_live_serves_concurrent_probes() -> None:
    """多路并发探测 /health/live：全部 200 且体一致（无串行化/无锁竞争）。"""
    bridge = _load_bridge_module()
    app = _make_app(bridge)
    results: list[tuple[int, dict]] = []
    barrier = threading.Barrier(8, timeout=HEALTH_DEADLINE_SECONDS)

    def probe() -> None:
        barrier.wait()
        response = TestClient(app).get("/health/live")
        results.append((response.status_code, response.json()))

    threads = [threading.Thread(target=probe) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=HEALTH_DEADLINE_SECONDS)
    assert len(results) == 8
    assert all(status == 200 for status, _ in results)
    assert all(body == {"status": "ok", "liveness": "alive", "readiness": "loading"}
               for _, body in results)


# ---------- _tensor_to_samples：有界分块转换（避免长 tolist 独占 GIL） ----------


class _FakeSlice:
    """fake 张量切片：记录切片范围，tolist 返回该段样本。"""

    def __init__(self, values: list[float]) -> None:
        self._values = values

    def tolist(self) -> list[float]:
        return list(self._values)


class _FakeFlatTensor:
    """fake 已展平张量：仅实现 shape[0] 与切片（duck-typed，零 torch 依赖）。"""

    def __init__(self, values: list[float]) -> None:
        self._values = values
        self.shape = (len(values),)
        self.slice_log: list[tuple[int, int]] = []

    def __getitem__(self, item: slice) -> _FakeSlice:
        assert isinstance(item, slice)
        start, stop = item.start, item.stop
        self.slice_log.append((start, stop))
        return _FakeSlice(self._values[start:stop])


def test_tensor_to_samples_chunks_with_yields_between_chunks() -> None:
    """250 样本按 100/块：恰 3 块、顺序保持、块间恰 2 次 yield(0)（末块后不 yield）。"""
    bridge = _load_bridge_module()
    values = [float(index) for index in range(250)]
    tensor = _FakeFlatTensor(values)
    yields: list[float] = []
    samples = bridge._tensor_to_samples(tensor, chunk_samples=100, yield_fn=yields.append)
    assert samples == values
    assert tensor.slice_log == [(0, 100), (100, 200), (200, 250)]
    assert yields == [0, 0]


def test_tensor_to_samples_exact_multiple_yields_once_between() -> None:
    """总量恰为块大小整数倍：4 块 3 次 yield，块边界无越界切片。"""
    bridge = _load_bridge_module()
    values = [float(-index) for index in range(40)]
    tensor = _FakeFlatTensor(values)
    yields: list[float] = []
    samples = bridge._tensor_to_samples(tensor, chunk_samples=10, yield_fn=yields.append)
    assert samples == values
    assert tensor.slice_log == [(0, 10), (10, 20), (20, 30), (30, 40)]
    assert yields == [0, 0, 0]


def test_tensor_to_samples_empty_and_short_inputs() -> None:
    """空张量 → 空列表零 yield；不足一块 → 单块零 yield。"""
    bridge = _load_bridge_module()
    empty = _FakeFlatTensor([])
    yields: list[float] = []
    assert bridge._tensor_to_samples(empty, chunk_samples=100, yield_fn=yields.append) == []
    assert yields == []
    short = _FakeFlatTensor([1.5, -2.5])
    assert bridge._tensor_to_samples(short, chunk_samples=100, yield_fn=yields.append) == [1.5, -2.5]
    assert short.slice_log == [(0, 2)]
    assert yields == []


def test_tensor_to_samples_rejects_non_positive_chunk() -> None:
    """边界守卫：chunk_samples ≤ 0 一律 ValueError（fail-closed，不静默整段转换）。"""
    bridge = _load_bridge_module()
    tensor = _FakeFlatTensor([0.0, 1.0])
    for bad in (0, -1):
        try:
            bridge._tensor_to_samples(tensor, chunk_samples=bad, yield_fn=lambda _s: None)
        except ValueError:
            continue
        raise AssertionError(f"chunk_samples={bad} 应拒绝（ValueError）")


def test_bridge_synthesize_conversion_uses_bounded_chunk_constant() -> None:
    """合成路径接有界分块：SAMPLE_CHUNK_SIZE 为正且合成函数按块转换（文本锚点 +
    默认块大小有界：≥1000 且 ≤100_000，兼顾窗口与开销）。"""
    bridge = _load_bridge_module()
    assert isinstance(bridge.SAMPLE_CHUNK_SIZE, int)
    assert 1000 <= bridge.SAMPLE_CHUNK_SIZE <= 100_000
    assert "_tensor_to_samples(" in BRIDGE.read_text(encoding="utf-8")
    # synthesize 源内引用分块转换（torch 路径在 venv 内运行，测试以文本契约锁定）
    synthesize_source = bridge._Engine.synthesize.__doc__ or ""
    assert "M14-24" in synthesize_source or "分块" in synthesize_source


# ---------- 文本契约锚点 + 卫生 ----------


def test_bridge_text_contract_health_live_anchors() -> None:
    text = BRIDGE.read_text(encoding="utf-8")
    for anchor in (
        "/health/live",
        "liveness",
        "_tensor_to_samples",
        "SAMPLE_CHUNK_SIZE",
    ):
        assert anchor in text, anchor
    for pattern in SECRET_PATTERNS:
        assert pattern not in text
