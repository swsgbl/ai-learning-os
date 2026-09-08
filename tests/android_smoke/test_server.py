"""LoopbackMockServer 冒烟测试（M12-06 第二步 A）。

只用标准库（unittest + urllib.request）：
- 固定契约端点（health / 搜索 / 治理）与 404 边界；
- JSONL 脱敏日志字段与不含敏感内容；
- 只绑定 127.0.0.1；
- stop 后端口释放、可立即复用。
"""
from __future__ import annotations

import json
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tools.android_smoke.server import LoopbackMockServer


class LoopbackMockServerTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log_path = str(Path(tmp.name) / "requests.jsonl")
        self.srv = LoopbackMockServer(self.log_path, port=0)
        self.srv.start()
        self.addCleanup(self.srv.stop)
        self.base = f"http://{self.srv.host}:{self.srv.port}"

    # ---------- HTTP 辅助 ----------

    def request(self, path: str, method: str = "GET", body: bytes | None = None):
        req = urllib.request.Request(
            self.base + path, data=body, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def log_entries(self) -> list[dict]:
        text = Path(self.log_path).read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line]

    # ---------- 固定契约端点 ----------

    def test_health(self) -> None:
        status, body, headers = self.request("/health")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["service"], "aios-mock-android-smoke")
        self.assertTrue(
            headers.get("Content-Type", "").startswith("application/json")
        )
        self.assertEqual(headers.get("Content-Length"), str(len(body)))

    def test_search_providers_plan_queries(self) -> None:
        status, body, _ = self.request("/api/v1/search/providers")
        self.assertEqual(status, 200)
        names = [item["name"] for item in json.loads(body)["items"]]
        self.assertEqual(names, ["local-corpus", "cloud-web"])

        status, body, _ = self.request(
            "/api/v1/search/plan", method="POST", body=b'{"query": "abc"}'
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["query"], "abc")

        status, body, _ = self.request(
            "/api/v1/search/queries",
            method="POST",
            body='{"query": "2024 清华 高数 选择题"}'.encode("utf-8"),
        )
        payload = json.loads(body)
        self.assertEqual(payload["query_id"], 9001)
        self.assertEqual(payload["result_count"], len(payload["results"]))

        status, body, _ = self.request("/api/v1/search/queries/9001")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["id"], 9001)

    def test_voice_providers_readonly(self) -> None:
        # M13-07：GET /api/v1/voice/providers 确定性快照;写方法 404
        status, body, _ = self.request("/api/v1/voice/providers")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["voice_mode"], "hybrid")
        self.assertEqual(
            payload["asr"], {"requested": None, "provider": "fake", "fallback": False}
        )
        self.assertEqual(
            payload["tts"],
            {"requested": "cloud-openai-tts", "provider": "tone", "fallback": True},
        )
        self.assertEqual(payload["privacy_store_audio"], False)
        self.assertEqual(payload["privacy_send_context_to_cloud"], True)

        status, _, _ = self.request(
            "/api/v1/voice/providers", method="POST", body=b"{}"
        )
        self.assertEqual(status, 404)

    def test_exam_session_readonly(self) -> None:
        # M13-08：GET /api/v1/exams/exam-m13-08-001 确定性只读会话快照;
        # 考试域写方法与其余路径一律 404
        status, body, _ = self.request("/api/v1/exams/exam-m13-08-001")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["exam_id"], "exam-m13-08-001")
        self.assertEqual(payload["paper_id"], "paper-001")
        self.assertEqual(payload["mode"], "exam")
        self.assertEqual(payload["status"], "active")
        self.assertEqual(payload["server_remaining_seconds"], 900)
        self.assertEqual(
            [q["id"] for q in payload["questions"]],
            ["q-m13-08-001", "q-m13-08-002"],
        )
        self.assertEqual(payload["answers"], {"q-m13-08-001": "A"})
        self.assertEqual(payload["next_sequence"], 2)

        for method, path in (
            ("POST", "/api/v1/exams/exam-m13-08-001"),
            ("PUT", "/api/v1/exams/exam-m13-08-001/answers"),
            ("POST", "/api/v1/exams/exam-m13-08-001/submit"),
            ("GET", "/api/v1/exams/exam-m13-08-001/submission"),
            ("GET", "/api/v1/exams/exam-unknown-999"),
            ("POST", "/api/v1/papers/paper-001/exams"),
        ):
            status, _, _ = self.request(path, method=method, body=b"{}")
            self.assertEqual(status, 404, (method, path))

    def test_governance_version_ops_audit(self) -> None:
        status, body, _ = self.request("/api/v1/version")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["version"], "0.12.5-mock")

        status, body, _ = self.request("/api/v1/system/ops-snapshot")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["worker_running"], True)

        status, body, _ = self.request("/api/v1/audit?limit=100")
        self.assertEqual(status, 200)
        entries = json.loads(body)
        self.assertEqual([e["id"] for e in entries], [1001, 1002])

    # ---------- 404 边界 ----------

    def test_unknown_path_404(self) -> None:
        status, body, _ = self.request("/api/v1/nope")
        self.assertEqual(status, 404)
        self.assertTrue(json.loads(body)["detail"])

    def test_write_methods_out_of_contract_404(self) -> None:
        # 契约固定只读：PUT/PATCH/DELETE 无任何允许端点，一律 404
        for method in ("PUT", "PATCH", "DELETE"):
            status, _, _ = self.request("/api/v1/search/providers", method=method)
            self.assertEqual(status, 404, method)
        # 治理端点写方法 404
        status, _, _ = self.request(
            "/api/v1/version", method="POST", body=b"{}"
        )
        self.assertEqual(status, 404)
        # audit 查询参数不是 limit=100 → 404
        status, _, _ = self.request("/api/v1/audit?limit=1")
        self.assertEqual(status, 404)

    # ---------- 脱敏日志 ----------

    def test_log_fields_and_redaction(self) -> None:
        body = json.dumps(
            {"query": "私密查询内容", "token": "secret-token-should-not-appear"}
        ).encode("utf-8")
        self.request(
            "/api/v1/search/queries?client=android&secret=do-not-log",
            method="POST",
            body=body,
        )
        self.request("/no-such-path")

        entries = self.log_entries()
        self.assertEqual(len(entries), 2)
        first = entries[0]
        self.assertEqual(
            sorted(first.keys()),
            ["body_len", "method", "path", "query_keys", "timestamp_utc"],
        )
        self.assertEqual(first["method"], "POST")
        self.assertEqual(first["path"], "/api/v1/search/queries")
        self.assertEqual(first["query_keys"], ["client", "secret"])
        self.assertEqual(first["body_len"], len(body))

        raw = Path(self.log_path).read_text(encoding="utf-8")
        for secret in ("secret-token", "私密查询内容", "do-not-log", "Bearer"):
            self.assertNotIn(secret, raw)
        # path 不含 query 取值
        self.assertNotIn("client=android", raw)

    def test_log_concurrent_requests_thread_safe(self) -> None:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(
                pool.map(
                    lambda i: self.request(f"/health?i={i}"),
                    range(32),
                )
            )
        entries = self.log_entries()
        self.assertEqual(len(entries), 32)
        for entry in entries:
            self.assertEqual(entry["method"], "GET")
            self.assertEqual(entry["path"], "/health")

    # ---------- 绑定与生命周期 ----------

    def test_binds_loopback_only(self) -> None:
        self.assertEqual(self.srv.host, "127.0.0.1")
        # 非回环地址上不应监听该端口：取本机局域网地址尝试连接，应被拒绝
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("10.255.255.255", 1))
            lan_ip = probe.getsockname()[0]
        except OSError:
            self.skipTest("无法确定本机非回环地址")
        finally:
            probe.close()
        if lan_ip in ("127.0.0.1", "0.0.0.0", ""):
            self.skipTest(f"本机无非回环地址（{lan_ip}）")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        self.addCleanup(sock.close)
        with self.assertRaises(OSError):
            sock.connect((lan_ip, self.srv.port))

    def test_stop_releases_port_for_reuse(self) -> None:
        srv = LoopbackMockServer(self.log_path + ".2", port=0)
        srv.start()
        port = srv.port
        srv.stop()
        # 端口应可立即重新绑定
        new_srv = LoopbackMockServer(self.log_path + ".3", port=port)
        new_srv.start()
        try:
            self.assertEqual(new_srv.port, port)
        finally:
            new_srv.stop()

    def test_context_manager(self) -> None:
        with LoopbackMockServer(self.log_path + ".4", port=0) as srv:
            with urllib.request.urlopen(
                f"http://{srv.host}:{srv.port}/health", timeout=5
            ) as resp:
                self.assertEqual(resp.status, 200)


if __name__ == "__main__":
    unittest.main()
