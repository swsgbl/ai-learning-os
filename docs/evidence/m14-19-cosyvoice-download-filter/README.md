# M14-19 CosyVoice ModelScope 下载载荷过滤——证据留档

切片：`fix/m14-19-cosyvoice-download-filter`（基于 `main@cc782b0`，PR #93 merge）。
本回合零生产触碰：未重跑 bootstrap、未启停任何进程/容器/代理（当日生产下载进程
PID 7481 原样运行）；生产 venv 仅只读探针。所有命令在本 worktree 或只读路径执行。

## 1. 根因证据：整仓下载实测

- 生产 `artifacts/voice/cosyvoice/service/service.log`（2026-09-13，进行中）逐文件
  下载统计（`grep -a -o "Downloading \[[^]]*\]" | sort | uniq -c`）：

  ```text
  3   Downloading [CosyVoice-BlankEN/config.json]
  3   Downloading [CosyVoice-BlankEN/generation_config.json]
  4   Downloading [CosyVoice-BlankEN/merges.txt]
  652 Downloading [CosyVoice-BlankEN/model.safetensors]      ← 988MB，抓取进行中
  3   Downloading [asset/dingding.png]
  29  Downloading [campplus.onnx]
  3   Downloading [configuration.json]
  3   Downloading [cosyvoice3.yaml]
  1129 Downloading [flow.decoder.estimator.fp32.onnx]        ← 1.33GB 无关件
  1214 Downloading [flow.pt]
  82  Downloading [hift.pt]
  1880 Downloading [llm.pt]
  1883 Downloading [llm.rl.pt]                               ← 2.02GB 无关件
  ```

- ModelScope 仓库权威清单（2026-09-13 直读，只读 API）：

  ```bash
  curl -sS "https://modelscope.cn/api/v1/models/FunAudioLLM/Fun-CosyVoice3-0.5B-2512/repo/files?Recursive=true"
  ```

  19 文件（字节）：`.gitattributes` 2,572｜`CosyVoice-BlankEN/` 6 件（config.json
  659、generation_config.json 242、merges.txt 1,402,109、model.safetensors
  988,097,824、tokenizer_config.json 1,287、vocab.json 2,776,833）｜`README.md`
  11,364｜`asset/dingding.png` 122,824｜`campplus.onnx` 28,303,423｜
  `configuration.json` 47｜`cosyvoice3.yaml` 6,934｜`flow.decoder.estimator.
  fp32.onnx` 1,326,216,933｜`flow.pt` 1,329,116,148｜`hift.pt` 83,202,622｜
  `llm.pt` 2,024,669,519｜`llm.rl.pt` 2,024,682,701｜`speech_tokenizer_v3.batch.onnx`
  969,451,579｜`speech_tokenizer_v3.onnx` 969,451,503。合计 ≈9.85GB；
  白名单 12 件 ≈5.43GB（**-45%**，排除 ≈4.44GB）。

## 2. 所需载荷判定（固定 commit `074ca6d`，克隆目录直读）

| 必需件 | 依据（源码行号） |
| --- | --- |
| `cosyvoice3.yaml` | `cosyvoice/cli/cosyvoice.py:196`（AutoModel 分发 + 构造） |
| `llm.pt`/`flow.pt`/`hift.pt` | `cosyvoice.py:213-215` `CosyVoiceModel.load` |
| `campplus.onnx`/`speech_tokenizer_v3.onnx` | `cosyvoice/cli/frontend.py:45-46` 无条件 `onnxruntime.InferenceSession`（`spk2info.pt` 不在仓内，frontend `os.path.exists` 容忍缺席） |
| `CosyVoice-BlankEN/*`（6 件全） | `cosyvoice.py:200` override `qwen_pretrain_path=<model_dir>/CosyVoice-BlankEN` → (a) `cosyvoice3.yaml` `!new:Qwen2Encoder` 急切构造 → `cosyvoice/llm/llm.py:229` `Qwen2ForCausalLM.from_pretrained`；(b) `!name:get_qwen_tokenizer` → HyperPyYAML `core.py:506` `functools.partial` → `frontend.py:39` 调用 → `tokenizer.py:274 CosyVoice3Tokenizer` → `AutoTokenizer.from_pretrained` |

**BlankEN 必需性探针（2026-09-13，WSL 生产 venv 独立只读进程，未触碰 PID 7481）**：
`/tmp/blanken-probe` 只放 `config.json`+`generation_config.json`，
`Qwen2ForCausalLM.from_pretrained("/tmp/blanken-probe")` →

```text
OSError: Error no file named pytorch_model.bin, model.safetensors, tf_model.h5,
model.ckpt.index or flax_model.msgpack found in directory /tmp/blanken-probe.
```

→ 缺 `model.safetensors` 即无法构造 Qwen2Encoder = 模型加载失败。**任务书把
CosyVoice-BlankEN 列为「无关可选资产」的假设被源码 + 实证推翻**；保留下载。
未来不需要它的运行时在 `RUNTIME_PAYLOADS` 显式省略即排除（契约测试锁定该语义）。

## 3. 排除项判定（固定 commit 全仓 grep + 逐点核实）

- `llm.rl.pt`：`grep -rn "llm\.rl" --include=*.py --include=*.yaml --include=*.md .`
  **零命中**（RL 后训练变体，推理不加载）。
- `speech_tokenizer_v3.batch.onnx`：仅 `llm.py:706`/`flow.py:318` 在
  `if online_feature is True:` 分支引用；`cosyvoice/utils/onnx.py` 末段
  `online_feature` 仅当 env `onnx_path` 设置时为 True（训练/数据准备单例，
  bootstrap/bridge 均不设）；推理 frontend 用 `speech_tokenizer_v3.onnx`。
- `flow.decoder.estimator.fp32.onnx`：仅 `cosyvoice.py:219` `load_trt=True`
  分支（bridge `AutoModel(model_dir=...)` 默认 False）与 `runtime/triton_trtllm`、
  `bin/export_onnx.py` 工具。
- `asset/dingding.png`/`README.md`/`.gitattributes`/`configuration.json`：
  `cosyvoice/` 运行时代码零引用（WebUI 宣传图/仓库自述/git 元数据/ModelScope
  卡片元数据）。

## 4. 过滤参数受支持性（modelscope==1.20.0，生产 venv 内直读）

`venv/lib/python3.10/site-packages/modelscope/hub/snapshot_download.py:27-38`：
`snapshot_download(..., local_dir=None, allow_patterns=None, ignore_patterns=None)`；
`:402-406` 允许列表对 `repo_file['Path']`（repo 相对全路径）`fnmatch`；
`allow_patterns is not None and allow_patterns` 为假（含空列表）时**不过滤**——
空配置必须在上游 fail-closed 拦截（本切片契约模块实现）。`_snapshot_download`
的 cache 与 local_dir 两分支同经 `_download_file_lists(..., allow_patterns=...)`
单一路径下发——`local_dir` + `allow_patterns` 组合受支持，断点续传
（`._____temp`）与缓存命中语义不变（白名单只收窄下载集合）。

## 5. 验证命令与结果（开发回合）

```text
解释器：D:\AI Learning OS\ai-learning-os\.venv\Scripts\python.exe（Python 3.11.15 / pytest 9.1.1）
worktree：ai-learning-os-worktrees/m14-19-cosyvoice-download-filter @ fix/m14-19-cosyvoice-download-filter

1) TDD RED（实现前）：pytest test_voice_local_scripts.py -q -k "model_payload or download_filter"
   → 1 failed + 5 errors（模块缺失 + bootstrap 锚点缺失——失败原因正确）
2) GREEN：同上 → 6 passed
3) 聚焦全套：pytest services/api/tests/test_voice_local_scripts.py -q → 40 passed + 1 skipped
4) 回归：pytest services/api/tests/test_voice_service_control.py -q → 64 passed
5) ruff check services/api tools/voice/cosyvoice_model_payload.py → All checks passed!
6) bash -n tools/voice/bootstrap_cosyvoice_wsl.sh → exit 0
7) git diff --check → exit 0
8) 生产 venv 冒烟（WSL py3.10.21，只读独立进程）：
   py=3.10.21 patterns=12 required=11
   FAIL-CLOSED unknown-id: OK(PayloadContractError)
   SMOKE-OK
```

（3 的 1 skipped = M14-18 whisper 签名回归按设计 skip（canonical venv 无
whisper），与基线一致；40 = 基线 34 + 本切片 6。）

## 6. 边界（诚实口径）

- 白名单自**下一次** bootstrap 模型下载起生效；当日生产下载进程原样运行未触碰，
  其整仓下载按旧形态继续完成。
- 存量模型目录已落地的多余文件（`llm.rl.pt` 等 ≈4.44GB）不主动删除（生产变更，
  由 supervisor 决定）。
- `snapshot_download(allow_patterns=...)` 的真实网络行为未在本回合实测（生产
  下载进程独占进行中，避免干扰）；受支持性与语义以 venv 内 modelscope 1.20.0
  源码直读为据。
- 生产冷启动复验（受控重跑 bootstrap → 白名单下载 → /health 200）由 supervisor
  合并后受控执行；`production_ready=false` 不变。
