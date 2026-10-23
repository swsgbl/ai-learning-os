"""M18-003 私有导入 API 契约测试（审计 Prompt 3：幂等/权利声明/隔离/失败如实）。

模式：auth-off client 播种目录（require_admin 放行）→ 同一 sqlite 文件
auth-on client 测导入（私有导入必须登录——owner 语义）。
TestClient 必须进 with（lifespan 建 sessionmaker——直构造无 state）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

_PDF_PART1 = b"%PDF-1.4 hello "
_PDF_PART2 = b"world footer"


class _Env:
    """同库双模式 client 工厂（anon=auth-off 播种；authed=登录导入）。"""

    def __init__(self, tmp_path, tree: list, parts: list[bytes] | None, monkeypatch: pytest.MonkeyPatch):
        from app.core.config import get_settings

        self.url = f"sqlite+aiosqlite:///{tmp_path / 'import.db'}"
        import app.api.routes.catalog as catalog_route
        import app.api.routes.library as library_route

        monkeypatch.setattr(catalog_route, "_fetch_tree", lambda *a, **k: ("sha-sync-1", tree))
        if parts is not None:
            monkeypatch.setattr(library_route, "_download_parts", lambda c, m, **k: parts)
        get_settings.cache_clear()
        self._get_settings = get_settings

    def open_anon(self) -> TestClient:
        return TestClient(create_app(self.url))

    def open_authed(self, monkeypatch: pytest.MonkeyPatch):
        """contextmanager：client 进 with（lifespan）后再注册登录。"""
        import contextlib

        @contextlib.contextmanager
        def _ctx():
            monkeypatch.setenv("AUTH_SECRET", "m18-003-import-secret-0123456789abcd")
            self._get_settings.cache_clear()
            with TestClient(create_app(self.url)) as client:
                tokens = {}
                for name in ("alice", "bob"):
                    client.post(
                        "/api/v1/auth/register", json={"username": name, "password": "password-123"}
                    )
                    tokens[name] = client.post(
                        "/api/v1/auth/login", json={"username": name, "password": "password-123"}
                    ).json()["access_token"]
                yield client, tokens

        return _ctx()

    def cleanup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTH_SECRET", raising=False)
        self._get_settings.cache_clear()


def _seed_and_get_resource(env: _Env, query: str) -> str:
    with env.open_anon() as anon:
        seed = anon.post("/api/v1/catalog/sync-runs", json={})
        assert seed.status_code == 201, seed.text
        rows = anon.get("/api/v1/catalog/search", params={"q": query}).json()
        assert rows, "播种后应能搜到条目"
        return rows[0]["id"]


def test_import_happy_path_idempotent_and_isolated(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """声明权利 → 下载分片 → 组装校验 → ready + blob 落库 + 重复导入幂等 + 跨用户隔离。"""
    env = _Env(
        tmp_path,
        monkeypatch=monkeypatch,
        tree=[("大学/线性代数/课本.pdf.1", "b1", 1), ("大学/线性代数/课本.pdf.2", "b2", 1)],
        parts=[_PDF_PART1, _PDF_PART2],
    )
    resource_id = _seed_and_get_resource(env, "课本")
    with env.open_authed(monkeypatch) as (client, tokens):
        try:
            created = client.post(
                "/api/v1/library/imports",
                json={"resource_id": resource_id, "permission_statement": True},
                headers={"Authorization": f"Bearer {tokens['alice']}"},
            )
            assert created.status_code == 201, created.text
            job = created.json()
            assert job["status"] == "ready"
            assert job["parts_total"] == 2 and job["parts_fetched"] == 2
            assert len(job["sha256"]) == 64
            assert job["size_bytes"] == len(_PDF_PART1) + len(_PDF_PART2)

            again = client.post(
                "/api/v1/library/imports",
                json={"resource_id": resource_id, "permission_statement": True},
                headers={"Authorization": f"Bearer {tokens['alice']}"},
            )
            assert again.status_code == 201 and again.json()["id"] == job["id"]

            mine = client.get(
                f"/api/v1/library/imports/{job['id']}",
                headers={"Authorization": f"Bearer {tokens['alice']}"},
            )
            assert mine.status_code == 200 and mine.json()["status"] == "ready"
            # 租户隔离：bob 查 alice 的 job 404
            other = client.get(
                f"/api/v1/library/imports/{job['id']}",
                headers={"Authorization": f"Bearer {tokens['bob']}"},
            )
            assert other.status_code == 404
        finally:
            env.cleanup(monkeypatch)


def test_import_requires_permission_statement(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """无权利声明 422（审计 Mode B 边界：用户确认使用权）。"""
    env = _Env(tmp_path, monkeypatch=monkeypatch, tree=[("a/书.pdf", "b1", 1)], parts=[b"%PDF-1.4 ok"])
    resource_id = _seed_and_get_resource(env, "书")
    with env.open_authed(monkeypatch) as (client, tokens):
        try:
            denied = client.post(
                "/api/v1/library/imports",
                json={"resource_id": resource_id, "permission_statement": False},
                headers={"Authorization": f"Bearer {tokens['alice']}"},
            )
            assert denied.status_code == 422
            assert "合法使用权" in denied.json()["detail"]
        finally:
            env.cleanup(monkeypatch)


def test_import_missing_part_reports_incomplete(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """缺片：409 如实（目录层缺口传导到导入拒绝——不虚构完整）。"""
    env = _Env(
        tmp_path,
        monkeypatch=monkeypatch,
        tree=[("a/书.pdf.1", "b1", 1), ("a/书.pdf.3", "b3", 1)],
        parts=[_PDF_PART1, _PDF_PART2],
    )
    resource_id = _seed_and_get_resource(env, "书")
    with env.open_authed(monkeypatch) as (client, tokens):
        try:
            failed = client.post(
                "/api/v1/library/imports",
                json={"resource_id": resource_id, "permission_statement": True},
                headers={"Authorization": f"Bearer {tokens['alice']}"},
            )
            assert failed.status_code == 409
            assert "缺片" in failed.json()["detail"]
        finally:
            env.cleanup(monkeypatch)


def test_import_download_failure_reports_502(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """下载失败：502 如实（不虚构成功）。"""
    import httpx

    env = _Env(tmp_path, monkeypatch=monkeypatch, tree=[("a/书.pdf", "b1", 1)], parts=None)
    import app.api.routes.library as library_route

    def _boom(commit, manifest, **kw):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(library_route, "_download_parts", _boom)
    resource_id = _seed_and_get_resource(env, "书")
    with env.open_authed(monkeypatch) as (client, tokens):
        try:
            failed = client.post(
                "/api/v1/library/imports",
                json={"resource_id": resource_id, "permission_statement": True},
                headers={"Authorization": f"Bearer {tokens['alice']}"},
            )
            assert failed.status_code == 502
        finally:
            env.cleanup(monkeypatch)


# ---------------------------------------------------------------- M18-004 解析闭环


def _make_textbook_pdf() -> bytes:
    """两页带文本 + outline 的教材形态 PDF（fitz 生成）。"""
    import pymupdf

    doc = pymupdf.open()
    for index in range(1, 3):
        page = doc.new_page()
        page.insert_text((72, 96), f"第{index}章 内容。" + "正文段落，含数学符号 -1/2 与 x²。" * 30)
    doc.set_toc([[1, "第一章 集合", 1], [1, "第二章 函数", 2]])
    data = doc.tobytes()
    doc.close()
    return data


def test_parse_after_import_lands_page_anchored_chunks(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """导入 → 解析：chunks 落库（页锚点）+ toc 透出 + resources parse_status=parsed。"""
    env = _Env(tmp_path, monkeypatch=monkeypatch, tree=[("a/书.pdf", "b1", 1)], parts=[_make_textbook_pdf()])
    resource_id = _seed_and_get_resource(env, "书")
    with env.open_authed(monkeypatch) as (client, tokens):
        header = {"Authorization": f"Bearer {tokens['alice']}"}
        try:
            job = client.post(
                "/api/v1/library/imports",
                json={"resource_id": resource_id, "permission_statement": True},
                headers=header,
            ).json()
            assert job["status"] == "ready"
            parsed = client.post(f"/api/v1/library/imports/{job['id']}/parse", headers=header)
            assert parsed.status_code == 200, parsed.text
            result = parsed.json()
            assert result["status"] == "parsed"
            assert result["parser_name"] == "pymupdf"
            assert result["page_count"] == 2
            assert result["chunk_count"] >= 1
            assert result["low_text_pages"] == 0
            assert result["toc"][0]["title"] == "第一章 集合"
            # 重跑幂等（整资源替换——不残留旧块）
            again = client.post(f"/api/v1/library/imports/{job['id']}/parse", headers=header)
            assert again.status_code == 200 and again.json()["chunk_count"] == result["chunk_count"]
            # 跨用户隔离：bob 解析 alice 的 job 404
            denied = client.post(
                f"/api/v1/library/imports/{job['id']}/parse",
                headers={"Authorization": f"Bearer {tokens['bob']}"},
            )
            assert denied.status_code == 404
        finally:
            env.cleanup(monkeypatch)


def test_parse_scanned_pdf_flags_needs_ocr(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """全扫描形态（无文本层）：needs_ocr 如实（不虚构文本）。"""
    import pymupdf

    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()  # 空页
    data = doc.tobytes()
    doc.close()

    env = _Env(tmp_path, monkeypatch=monkeypatch, tree=[("a/扫描书.pdf", "b1", 1)], parts=[data])
    resource_id = _seed_and_get_resource(env, "扫描书")
    with env.open_authed(monkeypatch) as (client, tokens):
        header = {"Authorization": f"Bearer {tokens['alice']}"}
        try:
            job = client.post(
                "/api/v1/library/imports",
                json={"resource_id": resource_id, "permission_statement": True},
                headers=header,
            ).json()
            parsed = client.post(f"/api/v1/library/imports/{job['id']}/parse", headers=header)
            assert parsed.status_code == 200
            result = parsed.json()
            assert result["status"] == "needs_ocr"
            assert result["low_text_pages"] == 3
        finally:
            env.cleanup(monkeypatch)


# ---------------------------------------------------------------- M18-005 结构提取闭环


def test_structure_from_parsed_outline(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """解析 → 结构提取（候选树+parent 嵌套）→ 确认 → 重提取幂等。"""
    env = _Env(tmp_path, monkeypatch=monkeypatch, tree=[("a/书.pdf", "b1", 1)], parts=[_make_textbook_pdf()])
    resource_id = _seed_and_get_resource(env, "书")
    with env.open_authed(monkeypatch) as (client, tokens):
        header = {"Authorization": f"Bearer {tokens['alice']}"}
        try:
            job = client.post(
                "/api/v1/library/imports",
                json={"resource_id": resource_id, "permission_statement": True},
                headers=header,
            ).json()
            client.post(f"/api/v1/library/imports/{job['id']}/parse", headers=header)
            structure = client.post(f"/api/v1/library/imports/{job['id']}/structure", headers=header)
            assert structure.status_code == 201, structure.text
            tree = structure.json()
            assert tree["unit_count"] == 2  # 两章 outline
            assert tree["confirmed_count"] == 0
            assert all(u["status"] == "candidate" for u in tree["units"])
            assert tree["units"][0]["title"] == "第一章 集合"
            assert tree["units"][0]["page"] == 1

            # 确认第一个单元
            confirmed = client.post(
                f"/api/v1/library/imports/{job['id']}/structure/{tree['units'][0]['id']}/confirm",
                headers=header,
            )
            assert confirmed.status_code == 200 and confirmed.json()["status"] == "confirmed"

            # 重提取 = 整树替换（confirmed 重置为候选——重提取即重建）
            rebuilt = client.post(f"/api/v1/library/imports/{job['id']}/structure", headers=header)
            assert rebuilt.status_code == 201
            assert rebuilt.json()["confirmed_count"] == 0

            # GET 查询
            got = client.get(f"/api/v1/library/imports/{job['id']}/structure", headers=header)
            assert got.status_code == 200 and got.json()["unit_count"] == 2
        finally:
            env.cleanup(monkeypatch)


def test_structure_requires_outline(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """无 outline（扫描书）：409 如实。"""
    import pymupdf

    doc = pymupdf.open()
    for _ in range(2):
        doc.new_page()
    data = doc.tobytes()
    doc.close()

    env = _Env(tmp_path, monkeypatch=monkeypatch, tree=[("a/扫描.pdf", "b1", 1)], parts=[data])
    resource_id = _seed_and_get_resource(env, "扫描")
    with env.open_authed(monkeypatch) as (client, tokens):
        header = {"Authorization": f"Bearer {tokens['alice']}"}
        try:
            job = client.post(
                "/api/v1/library/imports",
                json={"resource_id": resource_id, "permission_statement": True},
                headers=header,
            ).json()
            client.post(f"/api/v1/library/imports/{job['id']}/parse", headers=header)
            denied = client.post(f"/api/v1/library/imports/{job['id']}/structure", headers=header)
            assert denied.status_code == 409
            assert "outline" in denied.json()["detail"]
        finally:
            env.cleanup(monkeypatch)


# ---------------------------------------------------------------- M18-006 概念候选闭环


def test_concept_extraction_confirm_reject(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """提取（stub LLM）→ 列表（含边）→ confirm/reject → 重提取不覆盖已决。"""
    import app.api.routes.library as library_route
    from app.core.config import get_settings

    env = _Env(tmp_path, monkeypatch=monkeypatch, tree=[("a/书.pdf", "b1", 1)], parts=[_make_textbook_pdf()])
    resource_id = _seed_and_get_resource(env, "书")

    class _StubGateway:
        def __init__(self, **kwargs):
            pass

        def chat(self, messages, temperature=0.0, max_tokens=1024):
            import json as jsonlib

            return jsonlib.dumps(
                {
                    "concepts": [
                        {"id": "math:set-intersection", "canonical_name": "集合的交集",
                         "subject": "数学", "description": "同时属于两个集合的元素", "difficulty": 2,
                         "confidence": 0.9, "page": 1},
                        {"id": "math:set-union", "canonical_name": "集合的并集",
                         "subject": "数学", "description": "至少属于一个集合的元素", "difficulty": 2,
                         "confidence": 0.85, "page": 1},
                    ],
                    "edges": [
                        {"from": "math:set-intersection", "to": "math:set-union",
                         "relation": "prerequisite_of", "confidence": 0.7},
                    ],
                },
            )

    monkeypatch.setenv("LLM_ENDPOINT", "http://stub.local/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-stub-0123456789")
    monkeypatch.setenv("LLM_MODEL", "stub-model")
    get_settings.cache_clear()
    monkeypatch.setattr(library_route, "LlmGateway", _StubGateway)
    try:
        with env.open_authed(monkeypatch) as (client, tokens):
            header = {"Authorization": f"Bearer {tokens['alice']}"}
            try:
                job = client.post(
                    "/api/v1/library/imports",
                    json={"resource_id": resource_id, "permission_statement": True},
                    headers=header,
                ).json()
                client.post(f"/api/v1/library/imports/{job['id']}/parse", headers=header)
                extracted = client.post(f"/api/v1/library/imports/{job['id']}/concepts", headers=header)
                assert extracted.status_code == 201, extracted.text
                result = extracted.json()
                assert result["concept_count"] == 2 and result["edge_count"] == 1

                listed = client.get(f"/api/v1/library/imports/{job['id']}/concepts", headers=header)
                assert listed.status_code == 200
                concepts = listed.json()["concepts"]
                edges = listed.json()["edges"]
                assert len(concepts) == 2 and all(c["status"] == "candidate" for c in concepts)
                assert edges[0]["relation"] == "prerequisite_of"

                # confirm 一个 / reject 另一个
                confirmed = client.post(
                    f"/api/v1/library/imports/{job['id']}/concepts/{concepts[0]['id']}/confirm",
                    headers=header,
                )
                assert confirmed.json()["status"] == "confirmed"
                rejected = client.post(
                    f"/api/v1/library/imports/{job['id']}/concepts/{concepts[1]['id']}/reject",
                    headers=header,
                )
                assert rejected.json()["status"] == "rejected"

                # 重提取：已决候选不被覆盖（仍 2 条、状态保持）
                again = client.post(f"/api/v1/library/imports/{job['id']}/concepts", headers=header)
                assert again.status_code == 201
                relisted = client.get(f"/api/v1/library/imports/{job['id']}/concepts", headers=header)
                statuses = {c["concept_id"]: c["status"] for c in relisted.json()["concepts"]}
                assert statuses["math:set-intersection"] == "confirmed"
                assert statuses["math:set-union"] == "rejected"
            finally:
                env.cleanup(monkeypatch)
    finally:
        monkeypatch.delenv("LLM_ENDPOINT", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        get_settings.cache_clear()
