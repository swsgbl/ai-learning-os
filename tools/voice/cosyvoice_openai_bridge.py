#!/usr/bin/env python
"""M14-01 CosyVoice 本地 OpenAI 兼容 bridge：/v1/audio/speech（WAV）。

设计（定版）：
- 主 API 不嵌 CosyVoice SDK——本进程跑在 CosyVoice 官方仓库隔离 venv 内
  （tools/voice/bootstrap_cosyvoice_wsl.sh），以 OpenAI 兼容形状对外服务；
- 只绑 127.0.0.1（--host 可显式传 127.0.0.1，不提供对外绑定默认值）；
- 鉴权可选：--api-key / COSYVOICE_BRIDGE_API_KEY 设置后要求 Bearer；
- 输出恒为有效 WAV（RIFF/PCM16/mono，采样率取自模型）；错误恒为 JSON 固定
  脱敏文案——不泄漏模型/仓库/文件路径、key 或内部异常文本；模型未下载、
  依赖缺失、加载中或加载失败一律 503（fail-closed，不虚报可用）；
- 模型在后台线程加载：/health 在 loading/failed 时返回 503，ready 后 200；
- 健康面分两层（M14-24）：/health 为 readiness（模型能力，503/200 既有契约
  逐字节不变）；/health/live 为轻量 liveness（恒 200、不取推理锁、不碰模型，
  readiness 仅信息透出）——进程活/模型忙两态不再混同。合成路径样本转换按
  SAMPLE_CHUNK_SIZE 分块并在块间 yield，避免长 tolist 单次非抢占独占 GIL。

零 SDK 依赖可测性：本模块顶层只依赖 fastapi（服务端）与标准库；CosyVoice/
torch 全部惰性导入（加载线程内），`_samples_to_wav_bytes` 为纯标准库实现，
可在仓库测试环境直接导入断言。

用法（由 bootstrap_cosyvoice_wsl.sh 调用；手动示例见 tools/voice/README.md）：
  python cosyvoice_openai_bridge.py \
    --repo-dir <artifacts>/cosyvoice/CosyVoice \
    --model-dir <artifacts>/cosyvoice/Fun-CosyVoice3-0.5B \
    --host 127.0.0.1 --port 8011
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import threading
import time
import traceback
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

DEFAULT_MODEL_NAME = "Fun-CosyVoice3-0.5B-2512"

# ---- wetext 离线缓存与零网络复用（M14-03 Round 2）----
# CosyVoice 固定 commit 的 frontend 以 wetext.Normalizer()（无路径参数）构造
# 文本正则化前端 → wetext 内部 snapshot_download("pengzhendong/wetext")
# （revision=master，可变）默认落用户家目录缓存，且 modelscope 1.20 的
# snapshot_download 即使缓存命中也会先经 API 做 revision/文件清单元数据请求
# （无 offline 环境变量，local_files_only 仅函数参数，API 失败无缓存回退）。
# ——宿主"已预置"仍依赖网络，复用路径也发 ModelScope 请求（M14-03 Round 1
# 被驳回的根因）。Round 2 修法（全部在受支持边界内、窄作用域、契约锁定）：
#   1. MODELSCOPE_CACHE 确定性指向 gitignored artifacts（setdefault，显式 env 优先）；
#   2. 引擎加载前预热：payload 缺失才 snapshot_download（唯一容忍网络的步骤，
#      冷机一次性；COSYVOICE_SKIP_WETEXT_WARMUP=1 显式跳过）；
#   3. payload 齐备后窄绑定：仅当 model_id == pengzhendong/wetext 时给
#      snapshot_download 传 local_files_only=True（其余 model id 原样透传，
#      不影响主模型/其它下载路径）。绑定发生在 cosyvoice/wetext 导入之前
#      （本函数在 main() 引擎线程启动前执行），wetext 顶层
#      `from modelscope import snapshot_download` 取到的即包装函数；其
#      local_files_only 分支零网络直返缓存根目录。
#   4. 不修改任何第三方包文件、不读写用户家目录 ModelScope 缓存。
# wetext==0.0.4 Normalizer(lang="auto", operator="tn", remove_erhua=False)
# 恰好打开以下四个 FST（en/zh 各 tagger+verbalizer）——payload 判定以此为准。
WETEXT_MODEL_ID = "pengzhendong/wetext"
WETEXT_PAYLOAD_FILES = (
    "en/tn/tagger.fst",
    "en/tn/verbalizer.fst",
    "zh/tn/tagger.fst",
    "zh/tn/verbalizer.fst",
)


def wetext_payload_missing(cache_root: Path) -> list[str]:
    """返回 artifacts 缓存中缺失的 wetext payload 文件（相对路径；空 = 齐备）。"""
    repo_dir = cache_root.joinpath("hub", *WETEXT_MODEL_ID.split("/"))
    return [rel for rel in WETEXT_PAYLOAD_FILES if not repo_dir.joinpath(rel).is_file()]


def _bind_wetext_snapshot_local_only() -> None:
    """窄绑定：pengzhendong/wetext 的 snapshot_download 强制 local_files_only=True。

    作用域刻意收窄（契约测试锁定）：
    - 只拦截 WETEXT_MODEL_ID 一个 model id，其余调用原样透传原始函数；
    - 只改调用参数，不替换 modelscope 模块逻辑、不落盘任何第三方文件；
    - 必须在 cosyvoice/wetext 导入前执行（wetext 顶层 from-import 绑定的是
      modelscope 包命名空间里的当前属性值）。
    """
    import modelscope

    _original_snapshot_download = modelscope.snapshot_download

    def _wetext_local_only_snapshot_download(model_id, *args, **kwargs):
        if model_id != WETEXT_MODEL_ID:
            return _original_snapshot_download(model_id, *args, **kwargs)
        kwargs["local_files_only"] = True
        return _original_snapshot_download(model_id, *args, **kwargs)

    modelscope.snapshot_download = _wetext_local_only_snapshot_download
    print("[cosyvoice-bridge] wetext snapshot_download bound LOCAL-ONLY "
          f"(model_id == {WETEXT_MODEL_ID} -> local_files_only=True; others pass through)")


def _wire_wetext_offline_cache(model_dir: Path) -> None:
    """确定性 wetext 离线缓存 + 零网络复用（M14-03 Round 2）。

    - MODELSCOPE_CACHE 未设时指向 <model-dir>/../modelscope-cache（gitignored
      artifacts 内，随检出可移植）；显式设置的环境变量优先（不覆盖）。
    - payload 齐备：直接窄绑定 local-only（复用路径零 ModelScope 网络——
      零文件下载、零元数据/API 请求）。
    - payload 缺失且未设 COSYVOICE_SKIP_WETEXT_WARMUP=1：先预热（唯一网络
      步骤，冷机一次性）再绑定；预热失败不阻断 bridge，但显式告警（此时
      不绑定——frontend 按上游行为自行尝试在线，失败会退化为无 normalizer，
      加载后内省会再告警一次）。
    - 预热/绑定均发生在引擎线程启动、/health ready 之前。
    """
    default_cache = model_dir.parent / "modelscope-cache"
    os.environ.setdefault("MODELSCOPE_CACHE", str(default_cache))
    cache_root = Path(os.environ["MODELSCOPE_CACHE"])
    skip = os.environ.get("COSYVOICE_SKIP_WETEXT_WARMUP", "") == "1"
    missing = wetext_payload_missing(cache_root)
    if not missing:
        print(f"[cosyvoice-bridge] wetext offline cache READY: {cache_root} "
              "(payload complete — binding local-only, zero ModelScope traffic)")
        _bind_wetext_snapshot_local_only()
        return
    if skip:
        print(f"[cosyvoice-bridge] wetext warmup SKIPPED (COSYVOICE_SKIP_WETEXT_WARMUP=1); "
              f"cache={cache_root} payload_missing={len(missing)} — NOT binding local-only "
              "(frontend will follow upstream online behavior)")
        return
    print(f"[cosyvoice-bridge] wetext payload incomplete ({len(missing)}/{len(WETEXT_PAYLOAD_FILES)} "
          f"files) — provisioning into {cache_root} BEFORE engine load (one-time network) ...")
    try:
        from modelscope import snapshot_download

        snapshot_download(WETEXT_MODEL_ID)
    except Exception:  # noqa: BLE001 —— 预热失败不阻断 bridge（见 docstring）
        print("[cosyvoice-bridge] WARN: wetext resource provisioning FAILED "
              "(network?) — not binding local-only; frontend will follow upstream "
              "online behavior and may degrade to no-normalizer (post-load "
              "introspection will WARN); fix network and rerun bootstrap",
              file=sys.stderr, flush=True)
        traceback.print_exc()
        return
    still = wetext_payload_missing(cache_root)
    if still:
        print(f"[cosyvoice-bridge] WARN: wetext payload still incomplete after "
              f"provisioning: {still} — NOT binding local-only (see introspection)",
              file=sys.stderr, flush=True)
        return
    print(f"[cosyvoice-bridge] wetext offline cache PROVISIONED: {cache_root}")
    _bind_wetext_snapshot_local_only()
#: 官方示例的 zero-shot 提示语（asset/zero_shot_prompt.wav 的内容，见仓库 example.py）
DEFAULT_PROMPT_TEXT = "You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。"
DEFAULT_PROMPT_WAV = "asset/zero_shot_prompt.wav"
MAX_INPUT_CHARS = 2000  # 与主 API /synthesize 的 text 上限一致

#: 合成样本分块转换的块大小（M14-24）：tolist 是单次非抢占 C 调用，整段长
#: 音频一次性转换会长时间独占 GIL，令 /health 等健康线程饿死（生产实证
#: ~1s 级延迟）。50_000 样本 ≈ 2s @24kHz，单块转换毫秒级——非抢占窗口
#: 有界、正常路径开销可忽略。
SAMPLE_CHUNK_SIZE = 50_000


def _tensor_to_samples(flat, *, chunk_samples: int, yield_fn=time.sleep) -> list[float]:
    """把已展平的张量按块转为 float 列表：块与块之间显式 yield（默认
    time.sleep(0)，释放 GIL 一拍），使健康端点线程在长合成中仍能被调度。

    duck-typed（torch 张量与测试 fake 同形）：``flat`` 仅需 ``shape[0]`` 与
    切片 ``.tolist()``。``chunk_samples ≤ 0`` 一律 ValueError（fail-closed，
    绝不静默退回整段转换）。
    """
    if chunk_samples <= 0:
        raise ValueError("chunk_samples must be positive")
    total = int(flat.shape[0])
    samples: list[float] = []
    for start in range(0, total, chunk_samples):
        stop = min(start + chunk_samples, total)
        samples.extend(flat[start:stop].tolist())
        if stop < total:
            yield_fn(0)
    return samples


class SpeechRequest(BaseModel):
    """OpenAI /v1/audio/speech 兼容入参（voice/speed 等额外字段忽略）。"""

    model: str = DEFAULT_MODEL_NAME
    input: str = Field(min_length=1, max_length=MAX_INPUT_CHARS)
    response_format: str = "wav"
    # OpenAI 兼容字段：接收但不参与推理（音色由 zero-shot prompt 决定）
    voice: str | None = None
    speed: float | None = None


def _samples_to_wav_bytes(samples: list[float], sample_rate: int) -> bytes:
    """float 样本（[-1,1] 名义域）→ PCM16/mono WAV 字节（纯标准库，可单测）。

    超界样本钳制到 [-1,1]（合成输出偶发小幅越界），避免 int16 回绕爆音。
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        frames = bytearray()
        for value in samples:
            clipped = -1.0 if value < -1.0 else (min(value, 1.0))
            frames += int(clipped * 32767.0).to_bytes(2, "little", signed=True)
        writer.writeframes(bytes(frames))
    return buffer.getvalue()


class _Engine:
    """CosyVoice 引擎句柄：后台线程加载，phase 三态（loading/ready/failed）。"""

    def __init__(self, repo_dir: Path, model_dir: Path) -> None:
        self._repo_dir = repo_dir
        self._model_dir = model_dir
        self.phase = "loading"
        self.model: Any = None
        self.lock = threading.Lock()  # 单模型串行推理（GPU 显存与 kv cache 不并发）

    def start_loading(self) -> None:
        threading.Thread(target=self._load, name="cosyvoice-load", daemon=True).start()

    def _load(self) -> None:
        try:
            # 官方用法：cosyvoice 包与 third_party/Matcha-TTS 均需在 sys.path
            sys.path.insert(0, str(self._repo_dir))
            sys.path.insert(0, str(self._repo_dir / "third_party" / "Matcha-TTS"))
            from cosyvoice.cli.cosyvoice import (
                AutoModel,  # 惰性导入（bridge 顶层零 SDK 依赖）
            )

            self.model = AutoModel(model_dir=str(self._model_dir))
            self.phase = "ready"
            print("[cosyvoice-bridge] model ready", flush=True)
            # M14-03：文本正则化前端状态内省（fail-soft 只为可观测）——上游
            # wetext/ttsfrd 构造失败会被 frontend 的裸 except 吞掉、静默退化为
            # 无 normalizer（TTS 仍可用但质量降级）；这里把降级显式打出来。
            try:
                frontend_kind = str(
                    getattr(getattr(self.model, "frontend", None), "text_frontend", "")
                    or "",
                )
                if frontend_kind:
                    print(f"[cosyvoice-bridge] text frontend active: {frontend_kind}", flush=True)
                else:
                    print("[cosyvoice-bridge] WARN: text frontend EMPTY — text "
                          "normalization DISABLED (wetext/ttsfrd unavailable; TTS "
                          "quality degraded; check wetext cache/network)",
                          file=sys.stderr, flush=True)
            except Exception:  # noqa: BLE001 —— 内省失败不影响服务
                print("[cosyvoice-bridge] text frontend status: unknown (introspection failed)",
                      flush=True)
        except Exception:  # noqa: BLE001 —— phase 即结论；完整栈只进本进程日志
            self.phase = "failed"
            print("[cosyvoice-bridge] model load FAILED（依赖缺失或模型不完整；完整栈如下）",
                  file=sys.stderr, flush=True)
            # M14-02 修复：被捕获的异常不会自动打印栈——此前提示「细节见上方栈」
            # 但从未输出，加载失败无法定位（生产实证）；必须显式打印完整栈
            traceback.print_exc()

    def synthesize(self, text: str, prompt_text: str, prompt_wav: str) -> bytes:
        """zero-shot 合成 → WAV 字节。调用方须保证 phase == ready。

        M14-24：推理锁只覆盖模型前向（GPU 显存与 kv cache 不并发的语义边界）；
        torch.cat/展平/样本转换移出锁外，并经 _tensor_to_samples 分块转换 +
        块间 yield——长音频不再以单次非抢占 C 调用独占 GIL，/health 与
        /health/live 线程在合成中仍可被调度。
        """
        import torch  # 惰性：cosyvoice venv 内可用

        with self.lock:
            outputs = self.model.inference_zero_shot(text, prompt_text, prompt_wav, stream=False)
        tensor = torch.cat([output["tts_speech"] for output in outputs], dim=-1)
        flat = tensor.detach().to(torch.float32).cpu().reshape(-1)
        samples = _tensor_to_samples(flat, chunk_samples=SAMPLE_CHUNK_SIZE)
        return _samples_to_wav_bytes(samples, self.model.sample_rate)


def create_app(repo_dir: Path, model_dir: Path, *, model_name: str, prompt_text: str,
               prompt_wav: Path, api_key: str | None = None) -> FastAPI:
    engine = _Engine(repo_dir, model_dir)

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        engine.start_loading()
        yield

    app = FastAPI(title="CosyVoice OpenAI-compatible bridge", docs_url=None, redoc_url=None,
                  lifespan=_lifespan)
    app.state.engine = engine
    app.state.model_name = model_name

    def _require_auth(request: Request) -> None:
        if not api_key:
            return
        header = request.headers.get("Authorization", "")
        if header != f"Bearer {api_key}":
            raise HTTPException(status_code=401, detail="invalid api key")

    @app.get("/health")
    def health() -> JSONResponse:
        if engine.phase == "ready":
            return JSONResponse({"status": "ok", "model": model_name})
        detail = "cosyvoice model is loading" if engine.phase == "loading" else "cosyvoice model unavailable"
        return JSONResponse({"detail": detail}, status_code=503)

    @app.get("/health/live")
    def health_live() -> JSONResponse:
        """轻量 liveness（M14-24）：进程在服务即 200，与模型能力解耦。

        readiness（engine.phase：loading/ready/failed）仅作信息透出——诚实
        可见但不改变 liveness 判定；恒不取 engine.lock、不触发模型工作，
        合成/加载进行中亦可即刻应答（与 /health 同为零锁读）。
        """
        return JSONResponse({"status": "ok", "liveness": "alive", "readiness": engine.phase})

    @app.post("/v1/audio/speech")
    def speech(payload: SpeechRequest, request: Request) -> Response:
        _require_auth(request)
        if payload.model != model_name:
            raise HTTPException(status_code=404, detail="unknown model")
        if payload.response_format != "wav":
            raise HTTPException(status_code=400, detail="only response_format=wav is supported")
        if engine.phase != "ready":
            detail = "cosyvoice model is loading" if engine.phase == "loading" else "cosyvoice model unavailable"
            raise HTTPException(status_code=503, detail=detail)
        try:
            audio = engine.synthesize(payload.input, prompt_text, str(prompt_wav))
        except Exception:  # noqa: BLE001 —— 固定脱敏文案，不透出内部异常/路径
            raise HTTPException(status_code=500, detail="tts synthesis failed") from None
        if not audio or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            # 防御：引擎输出不构成有效 WAV 时 fail-closed（不冒充 WAV）
            raise HTTPException(status_code=500, detail="tts synthesis failed")
        return Response(content=audio, media_type="audio/wav")

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="CosyVoice OpenAI-compatible bridge (local only)")
    parser.add_argument("--repo-dir", required=True, help="CosyVoice 官方仓库克隆目录")
    parser.add_argument("--model-dir", required=True, help="Fun-CosyVoice3-0.5B-2512 模型目录")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认且建议 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--prompt-text", default=DEFAULT_PROMPT_TEXT)
    parser.add_argument("--prompt-wav", default=None, help="默认 <repo-dir>/asset/zero_shot_prompt.wav")
    parser.add_argument(
        "--api-key",
        default=None,
        help="可选 Bearer 鉴权（也可经 COSYVOICE_BRIDGE_API_KEY 注入；不设置 = 无鉴权，仅本机）",
    )
    args = parser.parse_args()

    repo_dir = Path(args.repo_dir).resolve()
    model_dir = Path(args.model_dir).resolve()
    prompt_wav = Path(args.prompt_wav).resolve() if args.prompt_wav else repo_dir / DEFAULT_PROMPT_WAV
    api_key = args.api_key or os.environ.get("COSYVOICE_BRIDGE_API_KEY") or None

    if not repo_dir.is_dir():
        print("[cosyvoice-bridge] FAIL: repo-dir 不存在（先运行 bootstrap_cosyvoice_wsl.sh）", file=sys.stderr)
        return 2
    if not model_dir.is_dir():
        print("[cosyvoice-bridge] FAIL: model-dir 不存在（模型未下载——先运行 bootstrap_cosyvoice_wsl.sh）",
              file=sys.stderr)
        return 2
    if not prompt_wav.is_file():
        print("[cosyvoice-bridge] FAIL: prompt wav 不存在", file=sys.stderr)
        return 2

    # M14-03：确定性 wetext 离线缓存 + 引擎加载前预热（见 _wire_wetext_offline_cache）
    _wire_wetext_offline_cache(model_dir)

    import uvicorn

    app = create_app(
        repo_dir, model_dir,
        model_name=args.model_name, prompt_text=args.prompt_text,
        prompt_wav=prompt_wav, api_key=api_key,
    )
    print(f"[cosyvoice-bridge] serving http://{args.host}:{args.port}/v1/audio/speech (wav)")
    uvicorn.run(app, host=args.host, port=args.port, workers=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
