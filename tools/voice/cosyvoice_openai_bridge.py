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
- 模型在后台线程加载：/health 在 loading/failed 时返回 503，ready 后 200。

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
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

DEFAULT_MODEL_NAME = "Fun-CosyVoice3-0.5B-2512"
#: 官方示例的 zero-shot 提示语（asset/zero_shot_prompt.wav 的内容，见仓库 example.py）
DEFAULT_PROMPT_TEXT = "You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。"
DEFAULT_PROMPT_WAV = "asset/zero_shot_prompt.wav"
MAX_INPUT_CHARS = 2000  # 与主 API /synthesize 的 text 上限一致


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
        except Exception:  # noqa: BLE001 —— phase 即结论；异常细节只进本进程日志
            self.phase = "failed"
            print("[cosyvoice-bridge] model load FAILED（依赖缺失或模型不完整；细节见上方栈）",
                  file=sys.stderr, flush=True)

    def synthesize(self, text: str, prompt_text: str, prompt_wav: str) -> bytes:
        """zero-shot 合成 → WAV 字节。调用方须保证 phase == ready。"""
        import torch  # 惰性：cosyvoice venv 内可用

        with self.lock:
            outputs = self.model.inference_zero_shot(text, prompt_text, prompt_wav, stream=False)
            chunks = [output["tts_speech"] for output in outputs]
            tensor = torch.cat(chunks, dim=-1)
            samples = tensor.detach().to(torch.float32).cpu().reshape(-1).tolist()
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
