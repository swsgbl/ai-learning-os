r"""M14-118 tools/ops/post_cutover_watch.py 契约测试：post-cutover
evidence watch——把 M14-117 生产切换后的 canonical 证据完整性检查固化为
只读 fail-closed 工具。默认 plan 零副作用；--execute 才读取证据；
SHA256SUMS 严格解析（行格式/路径安全/重复/自引用）；9 项分组/关键文件
集合契约（25 文件）；逐文件哈希复核；输出仅相对路径/字节数/SHA-256/
固定词汇状态；release_ready/production_ready 恒 false。

覆盖（全部 I/O 经真实临时目录合成 fixture；绝不触碰真实 canonical、
真实 env、生产端口/容器/DB/MinIO/语音服务）：
- 结构契约：源码零子进程/零网络/零 env/零墙钟 token；AST import 白名单
  + 禁动态执行/禁直接 open；socket+subprocess 双阻断下端到端照常成功；
  ops README 与证据 README 文档化；
- plan 面：默认零读取零写入（不存在的路径同样 exit 0）；stdout 不回显
  任何绝对路径；
- happy path：25 文件真实哈希索引 → exit 1、status=verified、9 项契约
  全 pass、25 条目 verified、release_ready/production_ready false、
  JSON+MD 双报告落盘；
- 索引行级 fail-closed：空索引、坏 hex、单空格分隔、空行、大写
  hex、垃圾行；路径逃逸（../ 穿越、绝对路径、反斜杠、盘符、// 空段、
  首尾空白）；重复条目；自引用；CRLF 行尾容忍 + 行中间 CR 拒绝；
- 契约面：索引缺条目（组 missing + index-integrity fail）、索引多
  条目（组 extra + index-integrity fail）；
- 文件级：缺文件、哈希不匹配、证据文件 symlink（目标自身/祖先）；
- 结构性拒绝（零写 exit 3）：输出目录已存在、SHA256SUMS 自身
  symlink、evidence-root 缺失；
- 脱敏：文件内容与畸形行内注入标记 token 绝不进入 JSON/MD/stdout；
- 零墙钟：同输入两次运行（各自全新输出目录）JSON 逐字节相同。
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import socket
import subprocess as subprocess_module
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
WATCH_SCRIPT = REPO_ROOT / "tools" / "ops" / "post_cutover_watch.py"
OPS_README = REPO_ROOT / "tools" / "ops" / "README.md"
EVIDENCE_README = (REPO_ROOT / "docs" / "evidence"
                   / "m14-118-post-cutover-watch" / "README.md")

#: 标记值（注入文件内容与索引畸形行，断言绝不进入任何输出）
MARK_TOKEN = "sk-ZXmarker987654321"

REPORT_JSON = "post-cutover-watch.json"
REPORT_MD = "post-cutover-watch.md"
INDEX_NAME = "SHA256SUMS"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


pcw = _load_module(WATCH_SCRIPT, "post_cutover_watch_under_test")


# ---------------------------------------------------------------- 工厂


def _write_evidence_file(root: Path, rel: str,
                         content: bytes | None = None) -> bytes:
    path = root.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    if content is None:
        content = f"m14-118 fixture::{rel}\n".encode()
    path.write_bytes(content)
    return content


def _sums_line(root: Path, rel: str, content: bytes | None = None) -> str:
    if content is None:
        content = root.joinpath(*rel.split("/")).read_bytes()
    return f"{hashlib.sha256(content).hexdigest()}  {rel}"


def _make_canonical(tmp_path: Path, *, extra_files: list[str] = (),
                    skip_files: list[str] = ()) -> tuple[Path, Path]:
    """按 M14-118 契约合成 canonical 证据树 + 真实哈希 SHA256SUMS。

    返回（evidence_root, sha256sums）。extra_files 额外写入并登记；
    skip_files 物理写入但不登记（构造缺条目索引）。"""
    root = tmp_path / "canonical"
    root.mkdir()
    on_disk = sorted(set(pcw.EXPECTED_ALL_FILES) | set(extra_files)
                     | set(skip_files))
    for rel in on_disk:
        _write_evidence_file(root, rel)
    indexed = [rel for rel in on_disk if rel not in set(skip_files)]
    lines = [_sums_line(root, rel) for rel in indexed]
    sums = root / INDEX_NAME
    #: write_bytes 保证纯 LF 索引（write_text 在 Windows 会转 CRLF；
    #: CRLF 行尾是工具容忍项，行中间 CR 才拒绝——见 test_crlf_index_
    #: accepted / test_mid_line_cr_rejected）
    sums.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return root, sums


def _rewrite_sums(root: Path, sums: Path, lines: list[str]) -> None:
    #: write_bytes 保证纯 LF（同 _make_canonical）
    sums.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


def _sums_lines(sums: Path) -> list[str]:
    text = sums.read_text(encoding="utf-8")
    return [line for line in text.split("\n") if line != ""]


def _run(root: Path, sums: Path, out: Path) -> int:
    return pcw.main(["--execute", "--evidence-root", str(root),
                     "--sha256sums", str(sums), "--output-dir", str(out)])


def _report(out: Path) -> dict[str, object]:
    return json.loads((out / REPORT_JSON).read_text(encoding="utf-8"))


def _assert_zero_output(out: Path) -> None:
    assert not out.exists()


def _block_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess is forbidden in this test")

    monkeypatch.setattr(subprocess_module, "run", _forbidden)

    def _no_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("network is forbidden in this test")

    monkeypatch.setattr(socket, "socket", _no_socket)


# ---------------------------------------------------------------- 结构契约


def test_source_contract_forbidden_tokens() -> None:
    source = WATCH_SCRIPT.read_text(encoding="utf-8")
    for token in ("subprocess", "socket", "urllib", "http.client", "os.system",
                  "Popen", "environ", "requests", "urlopen", "getenv", "docker",
                  "datetime.now", "utcnow", "time.time", "perf_counter",
                  "time.sleep"):
        assert token not in source, f"禁止出现的字面量: {token}"


def test_ast_import_allowlist_and_no_dynamic_io() -> None:
    """AST 结构锁定：import 模块白名单（零网络/零子进程/零 env 模块）、
    禁动态执行、禁直接 open（一切 I/O 经注入 Store）。"""
    tree = ast.parse(WATCH_SCRIPT.read_text(encoding="utf-8"))
    allowed = {"argparse", "hashlib", "json", "re", "sys", "pathlib",
               "monitoring_history", "__future__"}
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                assert func.id not in {"open", "eval", "exec", "compile",
                                       "__import__"}, \
                    f"禁止直接调用内建: {func.id}"
            elif isinstance(func, ast.Attribute):
                owner = (func.value.id if isinstance(func.value, ast.Name)
                         else "")
                if owner != "re":  # re.compile 白名单；其余属性调用禁危险名
                    assert func.attr not in {"system", "popen", "run",
                                             "urlopen", "__import__"}, \
                        f"禁止调用: {func.attr}"
    assert imported <= allowed, f"越界 import: {imported - allowed}"
    assert "monitoring_history" in imported  # Store 注入复用单一事实源


def test_no_subprocess_no_network_end_to_end(monkeypatch, tmp_path) -> None:
    _block_side_effects(monkeypatch)
    root, sums = _make_canonical(tmp_path)
    out = tmp_path / "out"
    assert _run(root, sums, out) == pcw.EXIT_VERIFIED


def test_ops_readme_documents_tool() -> None:
    readme = OPS_README.read_text(encoding="utf-8")
    assert "post_cutover_watch.py" in readme
    assert "M14-118" in readme


def test_evidence_readme_documents_boundaries() -> None:
    readme = EVIDENCE_README.read_text(encoding="utf-8")
    assert "M14-118" in readme
    for phrase in ("未执行生产巡检", "未验证回滚", "release-approval",
                   "human-only", "release_ready"):
        assert phrase in readme, f"证据 README 缺少边界语句: {phrase}"


# ---------------------------------------------------------------- plan 面


def test_plan_mode_is_zero_side_effect(capsys, tmp_path) -> None:
    """默认（无 --execute）：零读取零写入——指向不存在的路径同样 exit 0，
    无任何输出目录；stdout 不回显绝对路径。"""
    nowhere = tmp_path / "nowhere"
    code = pcw.main(["--evidence-root", str(nowhere),
                     "--sha256sums", str(nowhere / INDEX_NAME),
                     "--output-dir", str(tmp_path / "out")])
    assert code == pcw.EXIT_PLAN
    captured = capsys.readouterr()
    assert str(tmp_path) not in captured.out
    assert str(nowhere) not in captured.out
    assert "--execute" in captured.out
    assert not (tmp_path / "out").exists()
    assert not nowhere.exists()  # 连目录都未被创建/触碰


def test_default_paths_registered() -> None:
    args = pcw.build_parser().parse_args([])
    assert args.evidence_root == (pcw.REPO_ROOT / ".verify" / "artifacts"
                                  / "m14-117-production-cutover" / "canonical")
    assert args.sha256sums == args.evidence_root / INDEX_NAME
    assert args.output_dir == (pcw.REPO_ROOT / ".verify" / "artifacts"
                               / "m14-118-post-cutover-watch")
    assert args.execute is False


# ---------------------------------------------------------------- happy path


def test_execute_verified_happy_path(tmp_path) -> None:
    root, sums = _make_canonical(tmp_path)
    out = tmp_path / "out"
    assert _run(root, sums, out) == pcw.EXIT_VERIFIED
    report = _report(out)
    assert report["status"] == "verified"
    assert report["reasons"] == []
    assert report["release_ready"] is False
    assert report["production_ready"] is False
    contract = report["contract"]
    assert isinstance(contract, dict)
    checks = contract["checks"]
    assert isinstance(checks, list) and len(checks) == 9
    names = [check["name"] for check in checks]
    expected_names = [f"group:{g}" for g in
                      ("provider-smoke", "monitor", "browser", "rc-smoke",
                       "cutover", "endpoints", "recovery")]
    expected_names += ["key-files", "index-integrity"]
    assert names == expected_names
    assert all(check["status"] == "pass" for check in checks)
    files_block = report["files"]
    assert isinstance(files_block, dict)
    counts = files_block["counts"]
    assert isinstance(counts, dict)
    assert counts == {"verified": 25, "missing": 0, "hash-mismatch": 0,
                      "symlink": 0, "read-error": 0}
    assert len(files_block["entries"]) == 25
    md = (out / REPORT_MD).read_text(encoding="utf-8")
    assert "verified" in md
    assert "release_ready=False" in md
    assert str(tmp_path) not in json.dumps(report)  # 绝无绝对路径


def test_deterministic_byte_identical_outputs(tmp_path) -> None:
    """零墙钟：同输入两次运行（各自全新输出目录）JSON 逐字节相同。"""
    root, sums = _make_canonical(tmp_path)
    out1, out2 = tmp_path / "out1", tmp_path / "out2"
    assert _run(root, sums, out1) == pcw.EXIT_VERIFIED
    assert _run(root, sums, out2) == pcw.EXIT_VERIFIED
    assert (out1 / REPORT_JSON).read_bytes() == (out2 / REPORT_JSON).read_bytes()


# ---------------------------------------------------------------- 索引行级


@pytest.mark.parametrize("bad_line", [
    "ZZZbadhash0000000000000000000000000000000000000000000000000000ff  a/b.txt",
    "0123456789abcdef" * 3 + "  a/b.txt",  # 非 64 位 hex
    "A" * 64 + "  a/b.txt",  # 大写 hex 拒绝
    "0" * 64 + " a/b.txt",  # 单空格分隔
    "",
    "not a sha line at all",
])
def test_malformed_line_rejected(tmp_path, bad_line) -> None:
    root, sums = _make_canonical(tmp_path)
    lines = _sums_lines(sums)
    lines.insert(3, bad_line)
    _rewrite_sums(root, sums, lines)
    out = tmp_path / "out"
    assert _run(root, sums, out) == pcw.EXIT_FAILED
    report = _report(out)
    assert report["status"] == "failed"
    assert "index-line-format" in report["reasons"]
    assert report["first_bad_line"] == 4
    assert report["files"]["entries"] == []


def test_empty_index_rejected(tmp_path) -> None:
    root, sums = _make_canonical(tmp_path)
    sums.write_bytes(b"")
    out = tmp_path / "out"
    assert _run(root, sums, out) == pcw.EXIT_FAILED
    report = _report(out)
    assert report["reasons"] == ["index-empty"]
    assert report["first_bad_line"] is None


@pytest.mark.parametrize("escape_path", [
    "../../escape.txt",
    "browser/../../../escape.txt",
    "/etc/passwd",
    "browser\\..\\..\\escape.txt",
    "C:/escape.txt",
    "browser//double-slash.txt",
    "browser/./dot-segment.txt",
    "browser/../monitor/escape.txt",
    " browser/leading-space.png",
    "browser/trailing-space.png ",
])
def test_index_path_escape_rejected(tmp_path, escape_path) -> None:
    root, sums = _make_canonical(tmp_path)
    lines = _sums_lines(sums)
    lines.insert(5, f"{'0' * 64}  {escape_path}")
    _rewrite_sums(root, sums, lines)
    out = tmp_path / "out"
    assert _run(root, sums, out) == pcw.EXIT_FAILED
    report = _report(out)
    assert "index-path-escape" in report["reasons"]
    assert escape_path not in json.dumps(report)


def test_duplicate_line_rejected(tmp_path) -> None:
    root, sums = _make_canonical(tmp_path)
    lines = _sums_lines(sums)
    lines.append(lines[0])  # 同 path 同 hash 重复
    _rewrite_sums(root, sums, lines)
    out = tmp_path / "out"
    assert _run(root, sums, out) == pcw.EXIT_FAILED
    report = _report(out)
    assert report["reasons"] == ["index-duplicate-path"]
    assert report["first_bad_line"] == 26


def test_self_reference_rejected(tmp_path) -> None:
    root, sums = _make_canonical(tmp_path)
    digest = hashlib.sha256(sums.read_bytes()).hexdigest()
    lines = _sums_lines(sums)
    lines.append(f"{digest}  {INDEX_NAME}")
    _rewrite_sums(root, sums, lines)
    out = tmp_path / "out"
    assert _run(root, sums, out) == pcw.EXIT_FAILED
    report = _report(out)
    assert report["reasons"] == ["index-self-reference"]


def test_crlf_index_accepted(tmp_path) -> None:
    """Windows CRLF 行尾（canonical 索引真实形态）→ 恰一个行尾 \r 容忍，
    全量 verified。"""
    root, sums = _make_canonical(tmp_path)
    lines = _sums_lines(sums)
    sums.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))
    out = tmp_path / "out"
    assert _run(root, sums, out) == pcw.EXIT_VERIFIED
    report = _report(out)
    assert report["status"] == "verified"


def test_mid_line_cr_rejected(tmp_path) -> None:
    """行中间 \r（非行尾）→ 剥离单个行尾 \r 后仍残留 → 路径不安全拒绝。"""
    root, sums = _make_canonical(tmp_path)
    lines = _sums_lines(sums)
    lines.insert(4, f"{'0' * 64}  browser/ba\rd-cr.png")
    _rewrite_sums(root, sums, lines)
    out = tmp_path / "out"
    assert _run(root, sums, out) == pcw.EXIT_FAILED
    report = _report(out)
    assert report["reasons"] == ["index-path-escape"]


# ---------------------------------------------------------------- 契约面


def test_contract_missing_entry_fails(tmp_path) -> None:
    """索引缺一个条目（文件仍在树中）→ 组 missing + index-integrity
    fail → exit 2（诚实 failed 报告落盘）。"""
    root, sums = _make_canonical(tmp_path, skip_files=["monitor/monitor-20260923-233117.md"])
    out = tmp_path / "out"
    assert _run(root, sums, out) == pcw.EXIT_FAILED
    report = _report(out)
    assert report["status"] == "failed"
    assert set(report["reasons"]) >= {"group:monitor", "index-integrity"}
    checks = {check["name"]: check for check in report["contract"]["checks"]}
    assert checks["group:monitor"]["missing"] == [
        "monitor/monitor-20260923-233117.md"]
    assert checks["group:monitor"]["status"] == "fail"
    assert checks["index-integrity"]["missing"] == [
        "monitor/monitor-20260923-233117.md"]


def test_contract_extra_entry_fails(tmp_path) -> None:
    """索引多一个未分组/组外多余条目 → 组 extra + index-integrity
    fail → exit 2。"""
    root, sums = _make_canonical(
        tmp_path, extra_files=["endpoints/extra-probe.json"])
    out = tmp_path / "out"
    assert _run(root, sums, out) == pcw.EXIT_FAILED
    report = _report(out)
    assert set(report["reasons"]) >= {"group:endpoints", "index-integrity"}
    checks = {check["name"]: check for check in report["contract"]["checks"]}
    assert checks["group:endpoints"]["extra"] == ["endpoints/extra-probe.json"]
    assert checks["index-integrity"]["extra"] == ["endpoints/extra-probe.json"]
    #: 索引条目本身合法 → 文件级仍全量校验（26 条 verified）
    assert report["files"]["counts"]["verified"] == 26


# ---------------------------------------------------------------- 文件级


def test_missing_file_fails(tmp_path) -> None:
    root, sums = _make_canonical(tmp_path)
    victim = root.joinpath(*["browser", "BROWSER-REPORT.txt"])
    victim.unlink()
    out = tmp_path / "out"
    assert _run(root, sums, out) == pcw.EXIT_FAILED
    report = _report(out)
    assert report["status"] == "failed"
    assert "files-missing" in report["reasons"]
    entry = next(e for e in report["files"]["entries"]
                 if e["path"] == "browser/BROWSER-REPORT.txt")
    assert entry["status"] == "missing"
    assert entry["actual_sha256"] is None
    assert report["files"]["counts"]["verified"] == 24


def test_hash_mismatch_fails(tmp_path) -> None:
    """哈希漂移（文件被改写）→ hash-mismatch + actual 指纹登记。"""
    root, sums = _make_canonical(tmp_path)
    victim = root.joinpath(*["rc-smoke", "rc-smoke-report.json"])
    tampered = b'{"status": "tampered"}\n'
    victim.write_bytes(tampered)
    out = tmp_path / "out"
    assert _run(root, sums, out) == pcw.EXIT_FAILED
    report = _report(out)
    assert "files-hash-mismatch" in report["reasons"]
    entry = next(e for e in report["files"]["entries"]
                 if e["path"] == "rc-smoke/rc-smoke-report.json")
    assert entry["status"] == "hash-mismatch"
    assert entry["actual_sha256"] == hashlib.sha256(tampered).hexdigest()
    assert entry["expected_sha256"] != entry["actual_sha256"]
    assert entry["byte_size"] == len(tampered)


def test_symlinked_evidence_file_fails(tmp_path) -> None:
    """索引目标文件被替换为 symlink → files-symlink（不可用平台即 skip）。"""
    root, sums = _make_canonical(tmp_path)
    victim = root.joinpath(*["recovery", "recovery-dry-run-20260924.log"])
    outside = tmp_path / "outside.log"
    outside.write_bytes(b"outside\n")
    victim.unlink()
    try:
        victim.symlink_to(outside)
    except OSError:
        pytest.skip("symlink 不可用（Windows 需开发者模式/特权）")
    out = tmp_path / "out"
    assert _run(root, sums, out) == pcw.EXIT_FAILED
    report = _report(out)
    assert "files-symlink" in report["reasons"]
    entry = next(e for e in report["files"]["entries"]
                 if e["path"] == "recovery/recovery-dry-run-20260924.log")
    assert entry["status"] == "symlink"


# ---------------------------------------------------------------- 结构性拒绝


def test_output_dir_exists_refused(tmp_path) -> None:
    root, sums = _make_canonical(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "stale.txt").write_text("stale\n", encoding="utf-8")
    assert _run(root, sums, out) == pcw.EXIT_REFUSED
    assert (out / "stale.txt").read_text(encoding="utf-8") == "stale\n"
    assert not (out / REPORT_JSON).exists()


def test_output_dir_symlink_refused(tmp_path) -> None:
    try:
        (tmp_path / "out-link").symlink_to(tmp_path / "nowhere")
    except OSError:
        pytest.skip("symlink 不可用（Windows 需开发者模式/特权）")
    root, sums = _make_canonical(tmp_path)
    assert _run(root, sums, tmp_path / "out-link") == pcw.EXIT_REFUSED
    _assert_zero_output(tmp_path / "nowhere")


def test_index_symlink_refused(tmp_path) -> None:
    """SHA256SUMS 自身是 symlink → 结构性拒绝（零写）。"""
    root, sums = _make_canonical(tmp_path)
    real = tmp_path / "real-sums"
    real.write_bytes(sums.read_bytes())
    try:
        (root / INDEX_NAME).unlink()
        (root / INDEX_NAME).symlink_to(real)
    except OSError:
        pytest.skip("symlink 不可用（Windows 需开发者模式/特权）")
    out = tmp_path / "out"
    assert _run(root, root / INDEX_NAME, out) == pcw.EXIT_REFUSED
    _assert_zero_output(out)


def test_missing_index_refused(tmp_path) -> None:
    root, _sums = _make_canonical(tmp_path)
    (root / INDEX_NAME).unlink()
    out = tmp_path / "out"
    assert _run(root, root / INDEX_NAME, out) == pcw.EXIT_REFUSED
    _assert_zero_output(out)


def test_missing_evidence_root_refused(tmp_path) -> None:
    _root, _sums = _make_canonical(tmp_path)
    out = tmp_path / "out"
    assert _run(tmp_path / "no-root", tmp_path / "no-root" / INDEX_NAME, out) \
        == pcw.EXIT_REFUSED
    _assert_zero_output(out)


def test_non_utf8_index_refused(tmp_path) -> None:
    root, sums = _make_canonical(tmp_path)
    sums.write_bytes(b"\xff\xfe\x00bad\n")
    out = tmp_path / "out"
    assert _run(root, sums, out) == pcw.EXIT_REFUSED
    _assert_zero_output(out)


# ---------------------------------------------------------------- 脱敏


def test_report_sanitized(tmp_path, capsys) -> None:
    """文件内容与畸形行内的标记 token 绝不进入 JSON/MD/stdout。"""
    root, sums = _make_canonical(tmp_path)
    victim = root.joinpath(*["cutover", "manifest.json"])
    victim.write_bytes(f'{{"db_url": "postgres://{MARK_TOKEN}:pw@h/db"}}'
                       .encode())
    lines = _sums_lines(sums)
    lines.insert(2, f"malformed {MARK_TOKEN} not-a-sum-line")
    _rewrite_sums(root, sums, lines)
    out = tmp_path / "out"
    assert _run(root, sums, out) == pcw.EXIT_FAILED
    captured = capsys.readouterr()
    report_text = (out / REPORT_JSON).read_text(encoding="utf-8")
    md_text = (out / REPORT_MD).read_text(encoding="utf-8")
    for blob in (report_text, md_text, captured.out):
        assert MARK_TOKEN not in blob
        assert "postgres://" not in blob
    #: 畸形行失败词优先截断（行内容绝不回显，行号 3 可见）
    report = json.loads(report_text)
    assert report["reasons"] == ["index-line-format"]
    assert report["first_bad_line"] == 3


def test_stdout_never_echoes_paths(tmp_path, capsys) -> None:
    """stdout 只含状态/计数/文件名——绝不回显 args 路径。"""
    root, sums = _make_canonical(tmp_path)
    out = tmp_path / "out"
    assert _run(root, sums, out) == pcw.EXIT_VERIFIED
    captured = capsys.readouterr()
    assert str(tmp_path) not in captured.out
    assert str(root) not in captured.out
    assert str(out) not in captured.out
