"""M7-03 Privacy disclosure guards: every claim in docs/PRIVACY.md is backed.

Acceptance (backlog M7-03): clarify the boundaries and switches for local
storage, cloud processing, search and voice data. Each disclosure statement
must be backed by a real configuration item or code behavior:

1. config-name guard -- every upper-case config name mentioned in the doc
   resolves to a Settings field (env name); no doc-only phantom switches,
   and every privacy-relevant Settings field is disclosed (no omissions);
2. defaults guard -- documented defaults equal Settings defaults;
3. guard-module guard -- every test module cited as evidence exists;
4. end-to-end -- the self-verification path in the doc actually works on a
   live app (providers views, transcribe audio_stored=false by default,
   transcripts read-back, search cloud-web disabled with reason).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
DOC = Path(__file__).resolve().parents[3] / "docs" / "PRIVACY.md"

# compose 专用插值变量（不属于 Settings）
COMPOSE_VARS = {"AIOS_VOICE_MODE", "AIOS_WEB_PORT"}
# 排除的通用大写词（非配置项）
GENERIC = {"GET", "POST", "PUT", "HTTP", "HTTPS", "JSON", "URL", "API", "DB"}

# 隐私相关的 Settings 字段（env 名）——披露不得遗漏
PRIVACY_RELEVANT = {
    "MODEL_ROUTE", "VOICE_MODE", "SEARCH_MODE",
    "PRIVACY_STORE_AUDIO", "PRIVACY_SEND_CONTEXT_TO_CLOUD",
    "ASR_PROVIDER", "TTS_PROVIDER",
    "ASR_CLOUD_ENDPOINT", "ASR_CLOUD_API_KEY", "ASR_CLOUD_MODEL",
    "TTS_CLOUD_ENDPOINT", "TTS_CLOUD_API_KEY", "TTS_CLOUD_MODEL",
    "SEARCH_CLOUD_ENDPOINT", "SEARCH_CLOUD_API_KEY",
    "FETCH_RATE_LIMIT_PER_MINUTE", "RUBRIC_JUDGE",
}

_CLOUD_ENV_KEYS = (
    "ASR_CLOUD_ENDPOINT", "ASR_CLOUD_API_KEY",
    "TTS_CLOUD_ENDPOINT", "TTS_CLOUD_API_KEY",
    "SEARCH_CLOUD_ENDPOINT", "SEARCH_CLOUD_API_KEY",
)

# 文档默认值声明短语（与 Settings 默认联动锁定）
_DEFAULT_PHRASES = (
    "PRIVACY_STORE_AUDIO`（默认 false）",
    "RUBRIC_JUDGE` 默认 `keyword",
    "FETCH_RATE_LIMIT_PER_MINUTE`（默认 30/分钟",
)


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _doc_config_names(doc: str) -> set[str]:
    """Inline-code tokens that look like env config names."""
    names: set[str] = set()
    for token in re.findall(r"`([^`]+)`", doc):
        if re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", token) and token not in GENERIC:
            names.add(token)
    return names


def test_privacy_doc_config_names_resolve_and_no_omissions() -> None:
    doc = _doc()
    names = _doc_config_names(doc)
    assert names, "doc extraction found no config names - regex broken"
    known = {f.upper(): f for f in Settings.model_fields}
    known_set = set(known) | COMPOSE_VARS
    unknown = sorted(names - known_set)
    assert not unknown, f"doc mentions config items that do not exist: {unknown}"
    missing = PRIVACY_RELEVANT - names
    assert not missing, f"privacy-relevant settings not disclosed: {sorted(missing)}"


def test_privacy_doc_defaults_match_settings() -> None:
    s = Settings(_env_file=None)
    assert s.voice_mode == "local"
    assert s.search_mode == "local"
    assert s.model_route == "hybrid"
    assert s.privacy_store_audio is False
    assert s.privacy_send_context_to_cloud is True
    assert s.fetch_rate_limit_per_minute == 30
    assert s.rubric_judge == "keyword"
    doc = _doc()
    for phrase in _DEFAULT_PHRASES:
        assert phrase in doc, f"doc default phrase missing: {phrase}"


def test_privacy_doc_guard_modules_exist() -> None:
    doc = _doc()
    cited = sorted(set(re.findall(r"tests/test_[a-z_]+\.py", doc)))
    assert cited, "doc cites no guard test modules"
    root = Path(__file__).resolve().parents[1]
    missing = [m for m in cited if not (root / m).exists()]
    assert not missing, f"doc cites non-existent test modules: {missing}"


@pytest.fixture()
def client(monkeypatch):
    for key in _CLOUD_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("VOICE_MODE", "local")
    monkeypatch.setenv("PRIVACY_STORE_AUDIO", "false")
    get_settings.cache_clear()
    with TestClient(create_app(SQLITE_URL)) as c:
        yield c
    get_settings.cache_clear()


def test_privacy_claims_end_to_end(client: TestClient) -> None:
    """Doc section 4 self-verification path, replayed end to end."""
    vp = client.get("/api/v1/voice/providers")
    assert vp.status_code == 200
    view = vp.json()
    assert view["voice_mode"] == "local"
    assert view["privacy_store_audio"] is False
    assert view["privacy_send_context_to_cloud"] is True
    assert view["asr"]["provider"] == "fake"
    assert view["tts"]["provider"] == "tone"

    from app.voice.providers import _sine_wav

    wav = _sine_wav(0.5)
    tr = client.post(
        "/api/v1/voice/transcribe",
        files={"audio": ("clip.wav", wav, "audio/wav")},
    )
    assert tr.status_code == 200, tr.text
    body = tr.json()
    assert body["audio_stored"] is False
    assert body["audio_object_key"] is None

    # 转写文本可回查（追加式落库）
    tl = client.get("/api/v1/voice/transcripts")
    assert tl.status_code == 200
    texts = [item["text"] for item in tl.json()["items"]]
    assert body["text"] in texts

    # 检索视图：本地恒可用、cloud-web 未配置不虚报
    sp = client.get("/api/v1/search/providers")
    assert sp.status_code == 200
    providers = {p["name"]: p for p in sp.json()["items"]}
    assert providers["local-corpus"]["enabled"] is True
    cw = providers["cloud-web"]
    assert cw["enabled"] is False
    assert cw["unavailable_reason"]
