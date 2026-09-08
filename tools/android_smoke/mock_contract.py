"""Android 冒烟共享只读 mock 契约（M12-06 / M13-05）。

汇总 M12-04（搜索）与 M12-05（治理）两期证据 mock 的稳定只读面，
以纯函数形式（不开服务器）供 Android 冒烟与 HarmonyOS 验收复用：

- GET  /health
- GET  /api/v1/auth/status
- GET  /api/v1/system/privacy
- GET  /api/v1/search/providers
- POST /api/v1/search/plan
- POST /api/v1/search/queries
- GET  /api/v1/search/queries/9001
- GET  /api/v1/version
- GET  /api/v1/system/ops-snapshot
- GET  /api/v1/audit?limit=100  （查询参数须精确为 limit=100）
- GET  /api/v1/papers           （M13-05 新增：确定性论文列表）

范围与边界：
- 只读固定 JSON：未知路径 / 未实现方法一律 404；
  M12-05 端点额外限制为仅 GET（写方法 404）。
- 不触网、不读任何真实 provider / 生产服务 / 数据库 / 密钥。
- 请求日志只保留脱敏结构（timestamp_utc / method / path / query_keys / body_len），
  绝不记录 header、body 内容、query 取值或任何 token。
"""
from __future__ import annotations

import json
import urllib.parse
from typing import Optional

from tools.android_smoke.sanitize import sanitize_request

SERVICE = "aios-mock-android-smoke"

# ---------- M12-04 搜索面固定响应 ----------

RESULT_URL = "https://localhost/m12-04/smoke-source?trace=local&suite=android-360"
CREATED_AT = "2026-09-07T00:00:00+00:00"

RESULTS = [
    {
        "title": "2024 清华大学 高等数学（甲）期末选择题 第 1 套",
        "url": RESULT_URL,
        "snippet": "固定 mock 结果 A：用于冒烟验证结果 URL 展示断行、打开入口与 ACTION_VIEW 浏览器接管，不代表真实检索内容。",
        "source": "mock-local-corpus",
        "provider": "local-corpus",
        "authority": "official",
        "rank_reason": "本地语料命中：年份+学校+学科+题型全部匹配",
    },
    {
        "title": "2024 高等数学 选择题汇编（m12-04 冒烟样例 B）",
        "url": RESULT_URL,
        "snippet": "固定 mock 结果 B：第二条结果证明列表渲染与去重展示由客户端原样投影，不重排不改写。",
        "source": "mock-local-corpus",
        "provider": "local-corpus",
        "authority": "community",
        "rank_reason": "本地语料命中：学科+题型匹配，年份次之",
    },
]

PROVIDERS = {
    "items": [
        {
            "name": "local-corpus",
            "kind": "local-corpus",
            "enabled": True,
            "unavailable_reason": None,
        },
        {
            "name": "cloud-web",
            "kind": "web",
            "enabled": False,
            "unavailable_reason": "未配置云端检索通道（mock 固定禁用，验证不可用原因如实展示）",
        },
    ]
}

SKIPPED = [{"provider": "cloud-web", "reason": "不可用：未配置云端检索通道（mock 固定禁用）"}]

SLOTS = {
    "subject": "高等数学",
    "school": "清华大学",
    "year": "2024",
    "course": None,
    "question_type": "选择题",
    "publicity": None,
}

# ---------- M12-05 治理面固定响应 ----------

AUTH_STATUS = {"auth_enabled": False}

PRIVACY = {
    "model_route": "local",
    "voice_mode": "local",
    "search_mode": "local",
    "store_audio": False,
    "send_context_to_cloud": False,
}

VERSION = {
    "version": "0.12.5-mock",
    "git_commit": "m1205mockgit",
    "alembic_current": "m1205mockrev",
    "alembic_head": "m1205mockrev",
}

OPS_SNAPSHOT = {
    "generated_at": "2026-09-07T12:00:00+00:00",
    "database_backend": "postgresql",
    "users_by_role": {"admin": 2, "learner": 128},
    "papers_total": 34,
    "resources_by_parse_status": {"parsed": 40, "pending": 3},
    "parse_jobs_by_status": {"succeeded": 33, "queued": 2},
    "pending_review_drafts": {
        "course_import": 1,
        "course_generation": 2,
        "paper_question": 4,
        "variant_question": 5,
    },
    "voice_sessions_by_status": {"completed": 90, "failed": 2},
    "search_queries_total": 456,
    "audit_entries_total": 1024,
    "worker_running": True,
}

AUDIT = [
    {
        "id": 1001,
        "actor_id": "u-mock-admin-001",
        "actor_username": "mock_admin",
        "action": "paper.publish",
        "target_type": "paper",
        "target_id": "paper-m1205",
        "request_id": "req-m1205-1001",
        "created_at": "2026-09-07T12:00:01+00:00",
        "before": {"note": "M12_05_BEFORE_SHOULD_NOT_RENDER", "payload": "mock-before-1001"},
        "after": {"note": "M12_05_AFTER_SHOULD_NOT_RENDER", "payload": "mock-after-1001"},
    },
    {
        "id": 1002,
        "actor_id": None,
        "actor_username": None,
        "action": "worker.tick",
        "target_type": "job",
        "target_id": "job-m1205",
        "request_id": "req-m1205-1002",
        "created_at": "2026-09-07T12:00:02+00:00",
        "before": {"note": "M12_05_BEFORE_SHOULD_NOT_RENDER", "payload": "mock-before-1002"},
        "after": {"note": "M12_05_AFTER_SHOULD_NOT_RENDER", "payload": "mock-after-1002"},
    },
]

# ---------- M13-05 论文列表固定响应（PaperOut 契约） ----------

_PAPERS = [
    {
        "id": "paper-001",
        "title": "Attention Is All You Need",
        "subtitle": "Vaswani et al., NeurIPS 2017",
        "source": "NeurIPS",
        "university": None,
        "year": 2017,
        "subject": "Machine Learning",
        "difficulty": "medium",
        "duration_minutes": 45,
        "tags": ["transformer", "attention", "NLP"],
        "origin_url": "https://arxiv.org/abs/1706.03762",
    },
    {
        "id": "paper-002",
        "title": "BERT: Pre-training of Deep Bidirectional Transformers",
        "subtitle": "Devlin et al., NAACL 2019",
        "source": "NAACL",
        "university": "Google Research",
        "year": 2019,
        "subject": "Natural Language Processing",
        "difficulty": "hard",
        "duration_minutes": 60,
        "tags": ["BERT", "pre-training", "language model"],
        "origin_url": "https://arxiv.org/abs/1810.04805",
    },
    {
        "id": "paper-003",
        "title": "ImageNet Classification with Deep Convolutional Neural Networks",
        "subtitle": "Krizhevsky et al., NeurIPS 2012",
        "source": "NeurIPS",
        "university": "University of Toronto",
        "year": 2012,
        "subject": "Computer Vision",
        "difficulty": "medium",
        "duration_minutes": 50,
        "tags": ["CNN", "ImageNet", "deep learning"],
        "origin_url": None,
    },
]

NOT_FOUND = {"detail": "Not Found（mock 固定端点之外）"}
METHOD_NOT_ALLOWED = {"detail": "mock 只读：仅固定 GET 端点"}


def _extract_query(body_bytes: bytes) -> str:
    try:
        return json.loads(body_bytes.decode("utf-8") or "{}").get("query", "")
    except Exception:
        return ""


class ReadOnlyMockContract:
    """M12-04/M12-05/M13-05 稳定 mock 面的进程内实现（不开服务器）。"""

    def __init__(self) -> None:
        self._requests: list[dict] = []

    # ---------- 对外 ----------

    @property
    def requests(self) -> list[dict]:
        """脱敏后的请求记录列表（每次调用返回副本，元素为脱敏结构）。"""
        return [dict(entry) for entry in self._requests]

    def stats(self) -> dict:
        """按 method/path 汇总请求计数与总 body_len（path 去掉 query 后再作键）。"""
        summary: dict[tuple[str, str], int] = {}
        body_total = 0
        for entry in self._requests:
            bare_path = str(entry["path"]).partition("?")[0]
            key = (entry["method"], bare_path)
            summary[key] = summary.get(key, 0) + 1
            body_total += entry["body_len"]
        return {
            "total": len(self._requests),
            "body_len_total": body_total,
            "by_method_path": {
                f"{m} {p}": n for (m, p), n in sorted(summary.items())
            },
        }

    def handle(self, method: str, path: str, body_bytes: bytes = b"") -> tuple[int, Optional[dict]]:
        """处理一次请求，返回 (status, payload)；请求以脱敏结构记录。"""
        method = method.upper()
        self._requests.append(sanitize_request(method, path, body_bytes))
        try:
            return self._route(method, path, body_bytes)
        except Exception as exc:  # noqa: BLE001
            return 500, {"detail": f"mock error: {exc}"}

    # ---------- 路由 ----------

    def _route(self, method: str, path: str, body_bytes: bytes) -> tuple[int, Optional[dict]]:
        bare_path, _, raw_query = path.partition("?")

        # M12-05 治理端点：只读 GET，其余方法一律 404
        governance_paths = {
            "/api/v1/version",
            "/api/v1/system/ops-snapshot",
            "/api/v1/audit",
        }
        if bare_path in governance_paths and method != "GET":
            return 404, METHOD_NOT_ALLOWED

        if bare_path == "/api/v1/audit":
            # 规范只固定 GET /api/v1/audit?limit=100；其余查询参数一律 404
            if method == "GET" and urllib.parse.parse_qs(raw_query).get("limit") == ["100"]:
                return 200, AUDIT
            return 404, NOT_FOUND

        # M13-05 papers 端点：GET /api/v1/papers 返回确定性论文列表（顶层数组）
        if method == "GET" and bare_path == "/api/v1/papers":
            return 200, _PAPERS

        # 其余端点：忽略查询串取值（query 忽略值）
        if method == "GET" and bare_path == "/health":
            return 200, {"status": "ok", "service": SERVICE}
        if method == "GET" and bare_path == "/api/v1/auth/status":
            return 200, AUTH_STATUS
        if method == "GET" and bare_path == "/api/v1/system/privacy":
            return 200, PRIVACY
        if method == "GET" and bare_path == "/api/v1/search/providers":
            return 200, PROVIDERS
        if method == "POST" and bare_path == "/api/v1/search/plan":
            query = _extract_query(body_bytes)
            return 200, {
                "query": query,
                "slots": SLOTS,
                "plan": [
                    {
                        "provider": "local-corpus",
                        "query": query,
                        "enabled": True,
                        "unavailable_reason": None,
                    },
                    {
                        "provider": "cloud-web",
                        "query": query,
                        "enabled": False,
                        "unavailable_reason": "未配置云端检索通道（mock 固定禁用）",
                    },
                ],
            }
        if method == "POST" and bare_path == "/api/v1/search/queries":
            query = _extract_query(body_bytes)
            return 200, {
                "query_id": 9001,
                "query": query,
                "providers_requested": ["local-corpus", "cloud-web"],
                "results": RESULTS,
                "skipped": SKIPPED,
                "result_count": len(RESULTS),
                "duration_ms": 12,
            }
        if method == "GET" and bare_path == "/api/v1/search/queries/9001":
            return 200, {
                "id": 9001,
                "query": "2024 tsinghua advanced math mcq",
                "providers_requested": ["local-corpus", "cloud-web"],
                "providers_skipped": SKIPPED,
                "result_count": len(RESULTS),
                "duration_ms": 12,
                "results": RESULTS,
                "created_at": CREATED_AT,
            }
        if method == "GET" and bare_path == "/api/v1/version":
            return 200, VERSION
        if method == "GET" and bare_path == "/api/v1/system/ops-snapshot":
            return 200, OPS_SNAPSHOT

        return 404, NOT_FOUND
