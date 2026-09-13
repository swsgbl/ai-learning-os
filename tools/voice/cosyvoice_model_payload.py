#!/usr/bin/env python
"""M14-19：CosyVoice ModelScope 下载载荷契约（fail-closed allow_patterns 白名单）。

根因（2026-09-13 生产实证）：bootstrap 的 snapshot_download(model_id,
local_dir=...) 不带过滤 → 整仓下载。ModelScope API 实测
（GET /api/v1/models/FunAudioLLM/Fun-CosyVoice3-0.5B-2512/repo/files?Recursive=true，
2026-09-13）仓库共 19 文件 ≈ 9.85GB，其中 ≈ 4.44GB 与所选运行时无关：
llm.rl.pt 2.02GB / flow.decoder.estimator.fp32.onnx 1.33GB /
speech_tokenizer_v3.batch.onnx 0.97GB / asset/dingding.png + README.md +
.gitattributes + configuration.json 元数据。

所选运行时（bootstrap MODEL_ID=FunAudioLLM/Fun-CosyVoice3-0.5B-2512 →
bridge AutoModel → CosyVoice3，CosyVoice 固定 commit 074ca6d）加载闭包
逐点核实（源码行号）+ 实证：
- cosyvoice3.yaml：cosyvoice/cli/cosyvoice.py:196（AutoModel 分发 + 构造）；
- llm.pt / flow.pt / hift.pt：cosyvoice.py:213-215 CosyVoiceModel.load；
- campplus.onnx / speech_tokenizer_v3.onnx：cosyvoice/cli/frontend.py:45-46
  无条件 onnxruntime.InferenceSession（spk2info.pt 不在仓内，frontend
  os.path.exists 容忍缺席）；
- CosyVoice-BlankEN/*：cosyvoice.py:200 经 override
  qwen_pretrain_path=<model_dir>/CosyVoice-BlankEN 注入 yaml →
  (a) llm 段 !new:cosyvoice.llm.llm.Qwen2Encoder（HyperPyYAML !new: 急切
  构造）→ llm.py:229 Qwen2ForCausalLM.from_pretrained(pretrain_path)；
  (b) get_tokenizer !name: → functools.partial(tokenizer.py:317
  get_qwen_tokenizer, token_path=BlankEN) → frontend.py:39 调用 →
  CosyVoice3Tokenizer → AutoTokenizer.from_pretrained(BlankEN)。
  2026-09-13 WSL 生产 venv 探针实证：BlankEN 缺 model.safetensors 时
  from_pretrained 抛 OSError「Error no file named pytorch_model.bin,
  model.safetensors, ... found」→ BlankEN 全目录属真正必需载荷
  （任务书将其列为 optional 的假设被源码 + 实证推翻）。

排除项证据（固定 commit 全仓 grep + 逐点核实，2026-09-13）：
- llm.rl.pt：全仓零引用（RL 后训练变体，推理路径不加载）；
- speech_tokenizer_v3.batch.onnx：仅 cosyvoice/utils/onnx.py online_feature
  单例（env onnx_path 设置时的训练/数据准备路径，本运行时不设）引用——
  推理 frontend 用 speech_tokenizer_v3.onnx；
- flow.decoder.estimator.fp32.onnx：仅 load_trt=True 分支（cosyvoice.py:219；
  bridge 默认 AutoModel(model_dir=...) 不启用）与 runtime/triton_trtllm、
  bin/export_onnx.py 工具引用；
- asset/dingding.png / README.md / .gitattributes / configuration.json：
  运行时代码零引用（WebUI 宣传图 / 仓库自述 / git 元数据 / ModelScope 卡片
  元数据）。

过滤策略 = allow_patterns 精确路径白名单（modelscope==1.20.0 受支持参数：
hub/snapshot_download.py 允许列表 fnmatch 全路径匹配，空列表语义 = 不过滤
= 整仓下载 → 调用前必须 fail-closed 校验）。选白名单而非 ignore 列表：
上游未来新增文件默认被排除（fail-closed），ignore 列表对新增文件 fail-open。
未来运行时（如启用 load_trt 的 TensorRT 形态、或不需要 BlankEN 的轻量
运行时）= 在 RUNTIME_PAYLOADS 显式注册自己的载荷；未注册的 model_id 一律
拒绝下载（绝不静默退化为整仓）。

仅标准库（fnmatch/typing.NamedTuple）——canonical 测试 venv 与 CosyVoice
venv（Python 3.10）均可直接导入；bootstrap 下载段经 sys.path 注入本模块
所在目录导入（tools/voice 不是包，与 bridge 测试同款加载方式）。
"""
from __future__ import annotations

import fnmatch
from typing import NamedTuple

#: bootstrap_cosyvoice_wsl.sh 当前配置的唯一运行时。下载前按 MODEL_ID 查
#: 注册表；未注册即 fail-closed 拒绝（防未定义载荷的整仓回退）。
MODEL_ID = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"


class RuntimePayload(NamedTuple):
    """一个运行时的下载载荷契约。

    - allow_patterns：传给 modelscope snapshot_download 的白名单（repo 相对
      路径，modelscope 1.20 _download_filter 语义 = 对全路径 fnmatch）。本
      契约全部为精确路径——零通配、零文件名解析（防误伤/漏选）。
    - required_files：该运行时加载闭包必需的文件集（validate 强校验
      required_files ⊆ allow_patterns 匹配集；bootstrap 下载后逐一存在性
      校验也用它——上游布局变更即点名失败）。
    """

    allow_patterns: tuple[str, ...]
    required_files: tuple[str, ...]


#: CosyVoice3 运行时白名单（12 精确路径，逐项依据见模块 docstring）。
#: generation_config.json 为 from_pretrained 标准布局件（242B，transformers
#: 缺省容忍但保持 BlankEN 目录完整）——在白名单中、不在 strict 必需集中。
_COSYVOICE3_ALLOW: tuple[str, ...] = (
    "cosyvoice3.yaml",
    "llm.pt",
    "flow.pt",
    "hift.pt",
    "campplus.onnx",
    "speech_tokenizer_v3.onnx",
    "CosyVoice-BlankEN/config.json",
    "CosyVoice-BlankEN/generation_config.json",
    "CosyVoice-BlankEN/merges.txt",
    "CosyVoice-BlankEN/model.safetensors",
    "CosyVoice-BlankEN/tokenizer_config.json",
    "CosyVoice-BlankEN/vocab.json",
)
_COSYVOICE3_REQUIRED: tuple[str, ...] = tuple(
    path for path in _COSYVOICE3_ALLOW
    if path != "CosyVoice-BlankEN/generation_config.json"
)

RUNTIME_PAYLOADS: dict[str, RuntimePayload] = {
    MODEL_ID: RuntimePayload(
        allow_patterns=_COSYVOICE3_ALLOW,
        required_files=_COSYVOICE3_REQUIRED,
    ),
}


class PayloadContractError(RuntimeError):
    """载荷契约不满足（未知运行时 / 畸形白名单 / 必需集为空 / 必需文件未被白名单覆盖）。"""


def _validated_entries(what: str, entries) -> tuple[str, ...]:
    """共通 fail-closed 条目校验：容器形态 + 非空 + 每项非空字符串。

    str/bytes 一律拒绝：modelscope 允许 str 单模式形态，契约拒绝该歧义
    （单一字符串展开错误难发现；必须显式 list/tuple）。"""
    if isinstance(entries, (str, bytes)) or not isinstance(entries, (list, tuple)):
        raise PayloadContractError(
            f"{what} 必须为 list/tuple（收到 {type(entries).__name__}——"
            "str 单模式形态为歧义输入，契约要求显式序列）",
        )
    if not entries:
        raise PayloadContractError(
            f"{what} 为空不是合法配置——契约要求显式枚举"
            "（allow_patterns 空列表在 modelscope 1.20 语义下 = 不过滤 = 整仓下载）",
        )
    for entry in entries:
        if not isinstance(entry, str) or not entry.strip():
            raise PayloadContractError(
                f"{what} 含非法条目 {entry!r}（必须为非空字符串路径）",
            )
    return tuple(entries)


def validate_allow_patterns(patterns) -> tuple[str, ...]:
    """校验 allow_patterns（fail-closed）并规范化为 tuple。

    modelscope 1.20 空列表语义 = 不过滤（整仓下载）——空/畸形白名单必须在
    上游拦截，绝不能以静默回退下发到 snapshot_download。"""
    return _validated_entries("allow_patterns", patterns)


def validate_payload_contract(runtime_payload: RuntimePayload) -> tuple[str, ...]:
    """组合校验单个运行时载荷：白名单合法 + 必需集非空合法 + 必需文件逐一
    被白名单覆盖（未来运行时的 glob 笔误在此暴露，而不是漏下载后拖到
    bridge 加载失败）。返回校验后的 allow_patterns。"""
    patterns = validate_allow_patterns(runtime_payload.allow_patterns)
    required = _validated_entries("required_files", runtime_payload.required_files)
    uncovered = [
        path for path in required
        if not any(fnmatch.fnmatch(path, pattern) for pattern in patterns)
    ]
    if uncovered:
        raise PayloadContractError(
            f"必需载荷未被 allow_patterns 覆盖: {uncovered}"
            "（白名单漏项会漏下载必需文件）",
        )
    return patterns


def runtime_payload_for(model_id: str) -> RuntimePayload:
    """按 model_id 查运行时载荷注册表（fail-closed：未注册即拒）。"""
    try:
        return RUNTIME_PAYLOADS[model_id]
    except KeyError:
        raise PayloadContractError(
            f"model_id {model_id!r} 未注册下载载荷契约"
            f"（RUNTIME_PAYLOADS 仅 {sorted(RUNTIME_PAYLOADS)}）——"
            "先在 cosyvoice_model_payload.py 为该运行时显式定义必需载荷，"
            "拒绝退化为整仓下载",
        ) from None


def validate_runtime_payload(model_id: str) -> tuple[str, ...]:
    """bootstrap 下载段入口：查注册表 + 全量契约校验，返回 allow_patterns。"""
    return validate_payload_contract(runtime_payload_for(model_id))


def required_files_for(model_id: str) -> tuple[str, ...]:
    """该运行时的必需文件集（已注册 + 校验合法）；下载后存在性校验用。"""
    return _validated_entries(
        "required_files", runtime_payload_for(model_id).required_files,
    )


def filter_repo_files(repo_files, allow_patterns) -> list[str]:
    """modelscope 1.20 _download_filter 同语义镜像（fnmatch 全路径匹配，保序）。

    仅供测试与证据预演——bootstrap 实际过滤由 modelscope 自身执行，语义
    以本函数锁定一致（树节点跳过同理：入参应只含文件路径）。空/畸形
    白名单同样 fail-closed（不得退化为全选）。"""
    patterns = validate_allow_patterns(allow_patterns)
    return [
        path for path in repo_files
        if any(fnmatch.fnmatch(path, pattern) for pattern in patterns)
    ]
