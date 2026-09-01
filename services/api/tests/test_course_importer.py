"""M5-05 Course importer：授权门禁 -> 草稿生成 -> 人工审核队列。

- 域层：授权门禁（REUSE_ADMISSION 映射，NOT_ADMISSIBLE 全拒 + 非法值拒绝）、
  概念提取（两种确定性模式、去重保序、上限截断、无命中不虚报）、草稿构建快照；
- API：404（资源/草稿不存在）、403（门禁拒绝带原因）、201（进审核队列）、
  队列查询、approve/reject 状态迁移、终态 409、无 DB 503。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain.course_importer import (
    MAX_CONCEPTS,
    NotAdmissible,
    build_import_draft,
    extract_concepts,
)
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


# ---------- 域层 ----------


def test_gate_rejects_all_not_admissible_states() -> None:
    """UNKNOWN/ACCESS_CONTROLLED/ALL_RIGHTS_RESERVED/PROHIBITED 全拒 + 原因可见。"""
    for state in (
        "UNKNOWN",
        "ACCESS_CONTROLLED",
        "ALL_RIGHTS_RESERVED",
        "PROHIBITED",
    ):
        with pytest.raises(NotAdmissible) as exc_info:
            build_import_draft("res_x", "T", state, [])
        assert "不可复用" in str(exc_info.value) or "非法" in str(exc_info.value), state


def test_gate_rejects_invalid_license_value() -> None:
    """非法 license_state 值：拒绝（门禁只认记录快照，不接受声明）。"""
    with pytest.raises(NotAdmissible) as exc_info:
        build_import_draft("res_x", "T", "SOME_INVALID_STATE", [])
    assert "非法" in str(exc_info.value)


@pytest.mark.parametrize(
    ("license_state", "admission"),
    [
        ("PUBLIC_ACCESS", "FULL"),
        ("OPEN_LICENSE", "ATTRIBUTION_REQUIRED"),
        ("RESTRICTED_NON_COMMERCIAL", "NON_COMMERCIAL_ONLY"),
    ],
)
def test_gate_admits_licensed_resources(license_state: str, admission: str) -> None:
    """三种可复用状态：草稿生成成功，admission 快照正确。"""
    draft = build_import_draft("res_x", "线性代数讲义", license_state, [])
    assert draft["status"] == "pending_review"
    assert draft["reuse_admission"] == admission
    assert draft["source_license_state"] == license_state
    assert draft["resource_refs"] == ["res_x"]


def test_extract_concepts_two_patterns() -> None:
    """两种模式：「X 的定义/性质...」与「名词解释：X」。"""
    texts = [
        "极限的定义：当 n 趋于无穷时……",
        "名词解释：洛必达法则",
        "连续的性质与相关定理",
    ]
    names = extract_concepts(texts)
    assert "极限" in names
    assert "洛必达法则" in names
    assert "连续" in names


def test_extract_dedup_preserve_order() -> None:
    """跨 chunk 去重保序。"""
    texts = ["函数的极限的定义", "极限的性质"]
    names = extract_concepts(texts)
    # "函数的极限" 与 "极限" 是不同捕获——断言保序去重即可
    assert names == list(dict.fromkeys(names))
    assert names.index("函数的极限") < names.index("极限")


def test_extract_cap_at_max() -> None:
    """超上限截断（MAX_CONCEPTS）。"""
    texts = [chr(0x9000 + i) + "数列的定义" for i in range(MAX_CONCEPTS + 10)]
    names = extract_concepts(texts)
    assert len(names) == MAX_CONCEPTS


def test_extract_none_hit_no_fabrication() -> None:
    """无命中：空列表（不虚报概念）。"""
    assert extract_concepts(["今天天气不错"]) == []
    assert extract_concepts([]) == []


def test_build_draft_empty_concepts_note() -> None:
    """无概念命中：extraction_note 明示未识别（不虚报）。"""
    draft = build_import_draft("res_x", "T", "PUBLIC_ACCESS", ["今天天气不错"])
    assert draft["concepts"] == []
    assert draft["extraction_note"] == "未识别出候选概念"


def test_build_draft_concepts_note_count() -> None:
    """有概念命中：note 含数量。"""
    draft = build_import_draft("res_x", "T", "PUBLIC_ACCESS", ["极限的定义"])
    assert draft["concepts"] == ["极限"]
    assert draft["extraction_note"] == "识别出 1 个候选概念"


# ---------- API ----------


def _client():
    return TestClient(create_app(SQLITE_URL))


def _seed_licensed_resource(client, license_state: str, subject_hint: str = "极限") -> str:
    """创建 source（license 认定）-> upload 资源继承 license -> parse 落 chunks。

    返回 resource_id；解析等待走轮询（worker 异步消费）。
    """
    src = client.post(
        "/api/v1/sources",
        json={
            "id": "src_m505_" + license_state.lower() + "_" + str(abs(hash(subject_hint)) % 100000),
            "name": f"src-{license_state}-{subject_hint}",
            "source_type": "oer",
            "homepage": "https://example.edu/",
            "license_state": license_state,
        },
    ).json()
    payload = (
        '[{"question": "' + subject_hint + '的定义", "answer": "demo"}]'
    ).encode()
    up = client.post(
        "/api/v1/resources/upload",
        files={"file": ("doc.json", payload, "application/json")},
        data={"source_id": src["id"], "title": subject_hint + "讲义"},
    ).json()
    parsed = client.post(f"/api/v1/resources/{up['id']}/parse")
    assert parsed.status_code == 200
    return up["id"]


def test_api_full_flow_approve() -> None:
    """闭环：source(OPEN_LICENSE) -> upload -> parse -> 草稿 201 -> approve -> 终态。"""
    with _client() as client:
        rid = _seed_licensed_resource(client, "OPEN_LICENSE", "定积分")
        body = client.post("/api/v1/courses/import-drafts", json={"resource_id": rid})
        assert body.status_code == 201
        draft = body.json()
        assert draft["status"] == "pending_review"
        assert draft["source_license_state"] == "OPEN_LICENSE"
        assert draft["reuse_admission"] == "ATTRIBUTION_REQUIRED"
        assert draft["concepts"] == ["定积分"]
        assert draft["extraction_note"] == "识别出 1 个候选概念"

        ok = client.post(
            f"/api/v1/courses/import-drafts/{draft['id']}/approve",
            json={"note": "内容审核通过"},
        )
        assert ok.status_code == 200
        approved = ok.json()
        assert approved["status"] == "approved"
        assert approved["review_note"] == "内容审核通过"
        assert approved["reviewed_at"] is not None

        # 终态再审核 409
        again = client.post(
            f"/api/v1/courses/import-drafts/{draft['id']}/reject",
            json={"note": "x"},
        )
        assert again.status_code == 409


def test_api_reject_flow_and_queue_filter() -> None:
    """reject 流程 + 队列 status 过滤。"""
    with _client() as client:
        rid = _seed_licensed_resource(client, "PUBLIC_ACCESS", "向量空间")
        draft = client.post("/api/v1/courses/import-drafts", json={"resource_id": rid}).json()
        assert draft["reuse_admission"] == "FULL"

        rejected = client.post(
            f"/api/v1/courses/import-drafts/{draft['id']}/reject",
            json={"note": "概念提取质量不足"},
        ).json()
        assert rejected["status"] == "rejected"
        assert rejected["review_note"] == "概念提取质量不足"

        queue = client.get(
            "/api/v1/courses/import-drafts", params={"status": "pending_review"}
        ).json()
        assert all(item["status"] == "pending_review" for item in queue)
        assert all(item["id"] != draft["id"] for item in queue)


def test_api_gate_403_with_reason() -> None:
    """UNKNOWN 资源：403 带原因（默认上传即 UNKNOWN，门禁默认拒绝）。"""
    with _client() as client:
        payload = '[{"question": "数列的极限的定义", "answer": "a"}]'.encode()
        up = client.post(
            "/api/v1/resources/upload",
            files={"file": ("doc.json", payload, "application/json")},
        ).json()
        client.post(f"/api/v1/resources/{up['id']}/parse")
        body = client.post("/api/v1/courses/import-drafts", json={"resource_id": up["id"]})
        assert body.status_code == 403
        assert "不可复用" in body.json()["detail"]


def test_api_resource_not_found() -> None:
    """资源不存在 404。"""
    with _client() as client:
        body = client.post(
            "/api/v1/courses/import-drafts", json={"resource_id": "res_missing"}
        )
        assert body.status_code == 404


def test_api_draft_not_found() -> None:
    """草稿不存在 404 / 审核目标不存在 404。"""
    with _client() as client:
        assert (
            client.get("/api/v1/courses/import-drafts/crsd_missing").status_code == 404
        )
        assert (
            client.post(
                "/api/v1/courses/import-drafts/crsd_missing/approve", json={"note": None}
            ).status_code
            == 404
        )


def test_api_no_db_503() -> None:
    """无 DB：503。"""
    with TestClient(create_app(None)) as client:
        assert (
            client.post(
                "/api/v1/courses/import-drafts", json={"resource_id": "res_x"}
            ).status_code
            == 503
        )
