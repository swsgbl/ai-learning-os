"""M14-254 verify_voice_studio_livekit_input.py 契约测试（零网络/零浏览器/零运行时服务）。

锁定 harness 的全部可测纯函数与文本契约（真实栈运行保留给 supervisor，
本套件绝不发网络请求、不启动浏览器、不装依赖、不读 secret）：
1  select_paper：显式 override 精确命中 / 确定性取第一份 / 空列表与
   空 override 与缺失 override 全部 ENV-BLOCKED fail-closed；
2  expected_token_room / token_request_summary：DOM session id →
   voice-<id> 房间派生；网络事实只保留白名单键（method/path/status/
   room），请求体其余部分不进入摘要；
3  transcribe_request_facts：字节计数/顶层 content-type/part 布尔
   保留，原始音频字节绝不返回；multipart 判定含 boundary 形态；
4  verify_network_facts：token 恰一次 + room 绑定 + transcribe 恰一次
   200 + multipart/wav/answer.wav/非空字节；任何元数据缺失/多余/不匹配
   → AssertionError（fail-closed，不猜不补）；
5  find_jwt / report_has_jwt / sanitize_tree：JWT 形态检测与递归脱敏
   （报告/DOM 最后防线）；
6  phase 契约：MIC_PHASES 四态 + phase_valid 拒绝未知值 + DOM 属性
   绑定契约（data-mic-phase/data-voice-session-id/data-voice-studio
   存在于 voice-studio.tsx，且属性绑定不含 token/jwt/identity）；
7  进程回收复用：harness 调用邻居模块 stop_process_tree（精确 PID
   树），自身源码零按端口/进程名扫杀模式（/IM、pkill、killall、
   netstat、Get-Process、wmic 等）；
8  真实浏览器流（run_browser/main）不在此套件执行——文本契约钉住
   关键语义（网络监听先于语音链安装、分窗 console 断言、报告递归
   脱敏 + JWT 自检改判 failed、autoplay flag 显式记录于报告）；
9  login-only 认证边界（supervisor 修正 1）：login_verification_
   learner 只调 /api/v1/auth/login（成功返回内存 token，失败
   ENV-BLOCKED）；harness 源码零 ensure_user 复用、零注册端点
   调用（零播种边界）；
10 落盘防线顺序（supervisor 修正 2）：finalize_results 先对未脱敏
   报告 JWT 自检（脏报告必须以 failed + 退出码 1 + 自检标记收场，
   不可能以 passed 落盘）、后递归脱敏——函数体内检测先于脱敏。
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "verify_voice_studio_livekit_input.py"
VOICE_STUDIO_TSX = REPO_ROOT / "apps" / "web" / "src" / "components" / "voice-studio.tsx"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_voice_studio_livekit_input", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def harness():
    return _load_module()


#: 模块级单例：零网络加载（harness 顶层只有常量与纯函数），供
#: _ok_token/_ok_transcribe 等模块级 helper 使用。
_MODULE = _load_module()


SOURCE = SCRIPT.read_text(encoding="utf-8")
VOICE_SOURCE = VOICE_STUDIO_TSX.read_text(encoding="utf-8")

PAPERS = [
    {"id": "paper-b", "title": "B 卷"},
    {"id": "paper-a", "title": "A 卷"},
]

FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.sig-sig-sig"


# ---------- select_paper：确定性选卷（零播种、fail-closed） ----------


def test_select_paper_override_hits_exact_id(harness):
    assert harness.select_paper(PAPERS, "paper-a") == {"id": "paper-a", "title": "A 卷"}


def test_select_paper_default_is_deterministic_first(harness):
    assert harness.select_paper(PAPERS, None) == PAPERS[0]
    assert harness.select_paper(PAPERS, None) == PAPERS[0]  # 重复调用同结果


def test_select_paper_empty_list_blocks(harness):
    with pytest.raises(SystemExit, match="ENV-BLOCKED"):
        harness.select_paper([], None)


def test_select_paper_empty_override_blocks(harness):
    with pytest.raises(SystemExit, match="ENV-BLOCKED"):
        harness.select_paper(PAPERS, "")


def test_select_paper_missing_override_id_blocks(harness):
    with pytest.raises(SystemExit, match="ENV-BLOCKED"):
        harness.select_paper(PAPERS, "paper-not-exist")


# ---------- token 房间派生与网络摘要净化 ----------


def test_expected_token_room_binds_dom_session_id(harness):
    assert harness.expected_token_room("0f1e-2d3c") == "voice-0f1e-2d3c"
    assert harness.expected_token_room("s1") == "voice-s1"


def test_token_request_summary_whitelist_only(harness):
    entry = harness.token_request_summary(
        "POST", "/api/v1/voice/token", 200, "voice-0f1e-2d3c"
    )
    assert entry == {
        "kind": "token",
        "method": "POST",
        "path": "/api/v1/voice/token",
        "status": 200,
        "room": "voice-0f1e-2d3c",
    }
    # 摘要结构上不存在响应体/JWT/authorization/cookie 键
    for forbidden in ("token", "jwt", "authorization", "cookie", "ws_url", "body"):
        assert forbidden not in {key.lower() for key in entry}


# ---------- transcribe 事实净化（原始音频字节绝不返回） ----------


def _multipart_body(audio: bytes = b"\x52\x49\x46\x46\x01\x02") -> bytes:
    return (
        b"------WebKitFormBoundaryABC\r\n"
        b'Content-Disposition: form-data; name="audio"; filename="answer.wav"\r\n'
        b"Content-Type: audio/wav\r\n\r\n" + audio + b"\r\n------WebKitFormBoundaryABC--\r\n"
    )


def test_transcribe_facts_keeps_counts_and_flags_not_bytes(harness):
    body = _multipart_body()
    facts = harness.transcribe_request_facts(
        "POST",
        "/api/v1/voice/transcribe",
        200,
        "multipart/form-data; boundary=----WebKitFormBoundaryABC",
        body,
    )
    assert facts["multipart"] is True
    assert facts["wav_part"] is True
    assert facts["answer_wav_filename"] is True
    assert facts["byte_count"] == len(body)
    # 净化边界：返回值不含原始音频字节（只含计数与布尔）
    serialized = json.dumps(facts)
    for chunk in ("RIFF", "WebKitFormBoundaryABC\r\nContent-Disposition"):
        assert chunk not in serialized


def test_transcribe_facts_non_multipart_flagged(harness):
    facts = harness.transcribe_request_facts(
        "POST", "/api/v1/voice/transcribe", 200, "application/json", b"{}"
    )
    assert facts["multipart"] is False
    assert facts["wav_part"] is False
    assert facts["answer_wav_filename"] is False


def test_transcribe_facts_missing_content_type_flagged(harness):
    facts = harness.transcribe_request_facts(
        "POST", "/api/v1/voice/transcribe", 200, None, _multipart_body()
    )
    assert facts["multipart"] is False  # 缺失即不满足——fail-closed 由汇总断言兜住


# ---------- verify_network_facts：fail-closed 汇总断言 ----------

SESSION = "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"


def _ok_token(room: str | None = None):
    return _MODULE.token_request_summary("POST", "/api/v1/voice/token", 200, room or f"voice-{SESSION}")


def _ok_transcribe():
    return _MODULE.transcribe_request_facts(
        "POST", "/api/v1/voice/transcribe", 200,
        "multipart/form-data; boundary=x", _multipart_body(),
    )


def test_verify_network_facts_happy_path(harness):
    facts = harness.verify_network_facts([_ok_token()], [_ok_transcribe()], SESSION)
    assert facts["token"]["room"] == f"voice-{SESSION}"
    assert facts["token"]["expected_room"] == f"voice-{SESSION}"
    assert facts["transcribe"]["byte_count"] > 0


def test_verify_network_facts_room_not_bound_to_session_fails(harness):
    with pytest.raises(AssertionError, match="room"):
        harness.verify_network_facts([_ok_token("voice-other-session")], [_ok_transcribe()], SESSION)


def test_verify_network_facts_missing_room_fails(harness):
    entry = harness.token_request_summary("POST", "/api/v1/voice/token", 200, None)
    with pytest.raises(AssertionError, match="room"):
        harness.verify_network_facts([entry], [_ok_transcribe()], SESSION)


def test_verify_network_facts_duplicate_token_fails(harness):
    with pytest.raises(AssertionError, match="恰一次"):
        harness.verify_network_facts([_ok_token(), _ok_token()], [_ok_transcribe()], SESSION)


def test_verify_network_facts_zero_token_fails(harness):
    with pytest.raises(AssertionError, match="恰一次"):
        harness.verify_network_facts([], [_ok_transcribe()], SESSION)


def test_verify_network_facts_transcribe_non_200_fails(harness):
    bad = dict(_ok_transcribe(), status=502)
    with pytest.raises(AssertionError, match="transcribe 请求 HTTP 502"):
        harness.verify_network_facts([_ok_token()], [bad], SESSION)


def test_verify_network_facts_transcribe_missing_wav_part_fails(harness):
    body = b"------x\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"clip.bin\"\r\n\r\n\x00\x01\r\n------x--\r\n"
    bad = harness.transcribe_request_facts(
        "POST", "/api/v1/voice/transcribe", 200,
        "multipart/form-data; boundary=x", body,
    )
    with pytest.raises(AssertionError, match="audio/wav part 或 answer.wav"):
        harness.verify_network_facts([_ok_token()], [bad], SESSION)


def test_verify_network_facts_transcribe_empty_body_fails(harness):
    bad = harness.transcribe_request_facts(
        "POST", "/api/v1/voice/transcribe", 200, "multipart/form-data; boundary=x", b""
    )
    with pytest.raises(AssertionError, match="音频字节缺失"):
        harness.verify_network_facts([_ok_token()], [bad], SESSION)


def test_verify_network_facts_missing_transcribe_entry_fails(harness):
    with pytest.raises(AssertionError, match="恰一次"):
        harness.verify_network_facts([_ok_token()], [], SESSION)


def test_verify_network_facts_non_multipart_transcribe_fails(harness):
    bad = harness.transcribe_request_facts(
        "POST", "/api/v1/voice/transcribe", 200, "application/json", b"{}"
    )
    with pytest.raises(AssertionError, match="multipart"):
        harness.verify_network_facts([_ok_token()], [bad], SESSION)


# ---------- JWT 防线：find_jwt / report_has_jwt / sanitize_tree ----------


def test_find_jwt_detects_and_passes_clean(harness):
    assert harness.find_jwt(f"error: {FAKE_JWT}") is not None
    assert harness.find_jwt("<html>干净的 DOM</html>") is None
    assert harness.find_jwt("voice-0f1e-2d3c session id 非 JWT") is None


def test_report_has_jwt_recursive_and_clean(harness):
    dirty = {"nested": {"list": [f"token={FAKE_JWT}"]}}
    assert harness.report_has_jwt(dirty) is True
    clean = {"room": "voice-s1", "status": 200, "bytes": 1024}
    assert harness.report_has_jwt(clean) is False


def test_sanitize_tree_redacts_without_mutating_input(harness):
    original = {"a": [f"leak {FAKE_JWT}"]}
    sanitized = harness.sanitize_tree(original)
    assert harness.report_has_jwt(sanitized) is False
    assert FAKE_JWT in original["a"][0]  # 原值不被改写
    assert "REDACTED-JWT" in sanitized["a"][0]


def test_report_has_jwt_unserializable_is_dirty(harness):
    assert harness.report_has_jwt({"bad": object()}) is True


# ---------- phase 契约（DOM 属性绑定 + 四态校验） ----------


def test_mic_phase_contract_four_states(harness):
    assert harness.MIC_PHASES == ("idle", "connecting", "recording", "transcribing")


def test_phase_valid_accepts_contract_rejects_unknown(harness):
    for phase in harness.MIC_PHASES:
        assert harness.phase_valid(phase) is True
    for bad in ("recording ", "RECORDING", "", "stopped", None):
        assert harness.phase_valid(bad) is False


def test_voice_studio_dom_contract_attributes_present():
    assert re.search(r"data-voice-studio\b", VOICE_SOURCE)
    assert re.search(r"data-mic-phase=\{micPhase\}", VOICE_SOURCE)
    assert re.search(r"data-voice-session-id=\{view\.session\.session_id\}", VOICE_SOURCE)


def test_voice_studio_dom_contract_binds_no_credentials():
    # 任何 data-* 属性绑定不得引用 token/jwt/identity（凭据面零暴露）
    for match in re.finditer(r"data-[\w-]+=\{([^}]*)\}", VOICE_SOURCE):
        binding = match.group(1).lower()
        assert not re.search(r"token|jwt|identity", binding), match.group(0)


# ---------- 进程回收：复用邻居精确 PID 树，零扫杀模式 ----------


def test_harness_reuses_neighbor_stop_process_tree(harness):
    neighbor = harness.VWLC
    assert callable(neighbor.stop_process_tree)
    # harness 自身不定义杀进程逻辑——只经 VWLC 复用
    assert "VWLC.stop_process_tree(proc)" in SOURCE


def test_harness_has_no_port_or_name_killing_patterns():
    for pattern in ("/IM", "pkill", "killall", "netstat", "Get-Process", "wmic", "tasklist /svc"):
        assert pattern not in SOURCE, pattern


def test_harness_starts_web_via_neighbor(harness):
    # web 进程同样复用邻居的 node 直启 + PID 树回收（不自建第二套）
    assert "VWLC.start_web(port)" in SOURCE


# ---------- 真实浏览器流：文本契约钉住关键语义 ----------


def test_network_listeners_installed_before_voice_navigation():
    # 监听器注册必须先于 goto /voice/<paperId>（否则漏采 token/transcribe）
    listener_index = SOURCE.index('page.on("requestfinished", on_request_finished)')
    goto_index = SOURCE.index('page.goto(f"{web_base}/voice/{paper_id}")')
    assert listener_index < goto_index


def test_console_windows_are_split_login_vs_verification():
    # R1 分窗：登录窗预期噪声单独记录（login_console_errors），验证窗清零
    assert "login_console_errors = list(console_errors)" in SOURCE
    assert "console_errors.clear()" in SOURCE
    assert SOURCE.count("console_errors.clear()") == 1


def test_token_response_body_never_read():
    # 响应体（房间 JWT）只取 status；源码不存在读 token 响应体的模式
    assert ".body()" not in SOURCE
    assert ".text()" not in SOURCE


def test_report_self_check_downgrades_on_jwt_leak():
    # 落盘防线 = finalize_results：主流程调用它，其内部先自检后脱敏
    assert "results, exit_code = finalize_results(results)" in SOURCE


def test_jwt_detection_runs_before_sanitization():
    # finalize_results 函数体内：report_has_jwt 必须先于 sanitize_tree
    # （先脱敏后检测会把真实泄漏替换成占位符后静默通过——修正 2 根因）
    body = SOURCE[SOURCE.index("def finalize_results"):SOURCE.index("def login_verification_learner")]
    assert body.index("report_has_jwt(raw)") < body.index("sanitize_tree(raw)")


def test_finalize_results_dirty_report_cannot_finish_passed(harness):
    # 脏报告（passed + 含 JWT）：必须以 failed + 退出码 1 + 自检标记
    # 收场，且落盘树中原始 JWT 已被脱敏——真实泄漏不可能以 passed 落盘
    dirty = {"verdict": "passed", "error": f"leak {FAKE_JWT}"}
    out, code = harness.finalize_results(dirty)
    assert out["verdict"] == "failed"
    assert code == 1
    assert out["jwt_leak_self_check"] == "REDACTED-BY-SELF-CHECK"
    assert FAKE_JWT not in json.dumps(out)


def test_finalize_results_clean_pass_preserved(harness):
    clean = {"verdict": "passed", "room": "voice-s1", "status": 200}
    out, code = harness.finalize_results(clean)
    assert out["verdict"] == "passed"
    assert code == 0
    assert "jwt_leak_self_check" not in out


def test_finalize_results_failure_verdict_stays_failed(harness):
    out, code = harness.finalize_results({"verdict": "failed"})
    assert out["verdict"] == "failed" and code == 1


# ---------- login-only 认证边界（修正 1：零注册/零播种） ----------


def test_login_only_helper_succeeds_via_login_endpoint(harness, monkeypatch):
    calls = []

    def fake_api_call(method, path, body=None, headers=None):
        calls.append((method, path, body))
        return 200, {"access_token": "in-memory-token"}

    monkeypatch.setattr(harness.VWLC, "api_call", fake_api_call)
    assert harness.login_verification_learner() == "in-memory-token"
    method, path, body = calls[0]
    assert (method, path) == ("POST", "/api/v1/auth/login")  # 只调登录端点
    assert body == {"username": harness.LEARNER, "password": harness.PASSWORD}


def test_login_only_helper_fails_closed_on_bad_login(harness, monkeypatch):
    for status, payload in ((401, {}), (200, {}), (500, {"detail": "x"})):
        monkeypatch.setattr(
            harness.VWLC, "api_call", lambda *a, status=status, payload=payload, **k: (status, payload)
        )
        with pytest.raises(SystemExit, match="ENV-BLOCKED"):
            harness.login_verification_learner()


def test_harness_never_registers_or_seeds_users():
    # login-only 边界：harness 源码零 ensure_user 复用、零注册端点调用
    assert "ensure_user" not in SOURCE
    assert "auth/register" not in SOURCE
    assert "/api/v1/auth/login" in SOURCE  # 唯一认证端点是业务登录


def test_autoplay_flag_recorded_in_report():
    # autoplay flag 是 harness 侧浏览器配置差异——必须在报告中显式可审计
    assert '"autoplay_flag": AUTOPLAY_FLAG' in SOURCE
    assert "no-user-gesture-required" in SOURCE


def test_hard_assertions_present():
    for anchor in (
        'data-mic-phase',  # DOM 契约等待
        "语音作答",
        "停止录音并提交识别",
        "verify_network_facts(",
        "find_jwt(dom) is None",
    ):
        assert anchor in SOURCE, anchor
