r"""M14-41 契约：MinIO 生产卷属主迁移工具（零真实 Docker / 零网络）。

被测对象 ``tools/ops/minio_volume_adoption.py``（单文件纯标准库；Runner/
FS/Clock 注入）。全部用 FakeRunner/FakeFS 锁行为，零真实 Docker：

- plan：零 subprocess、零文件写入，stdout 输出纯 JSON（targets/risks/
  stages/argv_templates 全部来自固定常量）；
- 确认短语：execute/rollback 缺失或近似（大小写/双空格/截断）→ 拒绝且
  零 subprocess、零写入；
- argv 结构门：白名单形态逐 token 匹配；pull/build/down/kill/prune/
  rm/exec/restart/他服务 stop/错误镜像/非 :ro 普查/插入 token 一律在
  任何执行之前拒绝；
- execute 状态机：root 属主 → 备份路径先校验/预备（停容器之前，路径失败
  零容器变更）→ 停 minio → tar 备份+sha256+size → chown
  -R 1000:1000 → 递归复核 → compose --env-file env.production-recovery
  --profile local up -d --no-build --no-deps minio → 健康 → 重建 minio
  容器实际镜像 ID 必须精确等于预检本地固定镜像 ID → 除 minio 外五容器
  ID 不变；全 uid1000 → 跳过 chown 但必须备份；
  mixed/unknown/普查失败/镜像缺失/容器不健康 → 变更前 fail-closed；
  备份失败不 chown、chown 复核失败不 up；
- rollback：路径/文件名/伴生 .sha256/校验和/manifest（含 old image id）
  任一不满足 → 拒绝并输出人工恢复指引，零 Docker 副作用；manifest
  old_image_id ≠ 当前本地固定镜像 id → 在 stop/restore 之前 fail-closed；
  预检只要求 minio 容器存在（记录 ID，不要求 healthy——失败 execute 后
  minio 可能 stopped/unhealthy），其余五容器存在且 healthy；全部满足才允许
  仅 minio 服务/卷的受控恢复；
- 路径安全：helper/报告/备份路径限制在 gitignored
  .verify/artifacts/m14-41-minio-volume-adoption/ 内，symlink/越界拒绝；
  词法边界先行 + 从 REPO_ROOT 沿未 resolve 的原始组件逐级 symlink 检查
  （resolve 折叠不掉 symlinked root/component）；
- 容器 ID 归一化：记录与比较前一律 .strip()；
- secret：env 文件绝不读取（源级契约），--env-file 只携带路径锚定；
  stderr 尾行入报告前必须脱敏；
- R2 修正 1/2：全部 helper 容器恒显式 --user 0:0（本地固定镜像默认
  minio:minio，root 属主生产卷上隐式用户不安全）；挂载最小化——普查/备份
  卷只读，备份宿主目的地可写，恢复卷可写、宿主备份源只读；
- R2 修正 3：argv 白名单 / plan 模板 / 阶段表与真实命令逐 token 同步；
- R2 修正 4：execute/rollback 停 minio 之前先核对 COMPOSE_FILE/ENV_FILE
  仅元数据（存在 + 常规文件 + 非 symlink；内容绝不读取）——不满足零停机；
- R2 修正 5：rollback 对备份 tar/.sha256/.manifest.json 三工件独立
  fail-closed（resolve 边界 + symlink + 常规文件，缺一即拒绝零 Docker 副作用）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "minio_volume_adoption.py"

STAMP = "20260917-045300"
TAR_BYTES = b"FAKE-TAR-BYTES-m14-41"
TAR_SHA = hashlib.sha256(TAR_BYTES).hexdigest()
STACK_SERVICES = ("postgres", "redis", "minio", "api", "web", "livekit")
OTHER_SERVICES = ("postgres", "redis", "api", "web", "livekit")
SECRET_STDERR = "MINIO_ROOT_PASSWORD=hunter2secret99\n"


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


mva = _load_module(SCRIPT, "minio_volume_adoption_under_test")

TAR = mva.ARTIFACT_DIR / f"minio-data-backup-{STAMP}.tar"
SHA_FILE = mva.ARTIFACT_DIR / f"minio-data-backup-{STAMP}.tar.sha256"
MANIFEST_FILE = mva.ARTIFACT_DIR / f"minio-data-backup-{STAMP}.tar.manifest.json"
REPORT_JSON = mva.ARTIFACT_DIR / f"execute-{STAMP}.json"
ROLLBACK_REPORT_JSON = mva.ARTIFACT_DIR / f"rollback-{STAMP}.json"

EXPECTED_CENSUS = ("docker", "run", "--rm", "--user", "0:0",
                   "--network", "none",
                   "-v", f"{mva.VOLUME_NAME}:/probe:ro",
                   "--entrypoint", "/bin/ls", mva.SELF_IMAGE_REF, "-lnAR", "/probe")
EXPECTED_STOP = ("docker", "stop", mva.MINIO_CONTAINER)
EXPECTED_BACKUP = ("docker", "run", "--rm", "--user", "0:0",
                   "-v", f"{mva.VOLUME_NAME}:/data:ro",
                   "-v", f"{mva.ARTIFACT_DIR}:/backup",
                   "--entrypoint", "/bin/tar", mva.SELF_IMAGE_REF, "-cf",
                   f"/backup/minio-data-backup-{STAMP}.tar", "-C", "/data", ".")
EXPECTED_CHOWN = ("docker", "run", "--rm", "--user", "0:0",
                  "-v", f"{mva.VOLUME_NAME}:/data",
                  "--entrypoint", "/bin/chown", mva.SELF_IMAGE_REF,
                  "-R", "1000:1000", "/data")
EXPECTED_UP = ("docker", "compose", "-f", str(mva.COMPOSE_FILE),
               "--project-name", mva.PROJECT,
               "--env-file", str(mva.ENV_FILE),
               "--profile", "local",
               "up", "-d", "--no-build", "--no-deps", "minio")
EXPECTED_RESTORE = ("docker", "run", "--rm", "--user", "0:0",
                    "-v", f"{mva.VOLUME_NAME}:/data",
                    "-v", f"{mva.ARTIFACT_DIR}:/backup:ro",
                    "--entrypoint", "/bin/tar", mva.SELF_IMAGE_REF, "-xf",
                    f"/backup/{TAR.name}", "-C", "/data")
# R2-4：停 minio 前仅元数据核对的控制面文件（FakeFS 中恒为占位存在，
# 各失败分支用 without/not_regular/symlinks 刻画）
CONTROL_FILES = (("compose_file", mva.COMPOSE_FILE), ("env_file", mva.ENV_FILE))


# ---------------------------------------------------------------- fakes


class FakeRunner:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, *, timeout: float = 60.0, encoding: str | None = None):
        argv = tuple(str(item) for item in argv)
        self.calls.append(argv)
        rc, stdout, stderr = self.handler(argv)
        return mva.CommandResult(argv, rc, stdout, stderr)


class FakeSleeper:
    def __init__(self) -> None:
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


class FakeClock:
    def utc_now_iso(self) -> str:
        return "2026-09-17T04:53:00Z"

    def stamp(self) -> str:
        return STAMP


class FakeFS:
    def __init__(self, files: dict[Path, bytes] | None = None,
                 symlinks: set[Path] | None = None,
                 not_regular: set[Path] | None = None) -> None:
        self.files: dict[Path, bytes] = dict(files or {})
        self.symlinks = set(symlinks or ())
        # R2：存在但非常规文件（目录/FIFO 画像）——exists 真、is_file 假
        self.not_regular = set(not_regular or ())
        self.writes: list[Path] = []
        self.created_dirs: list[Path] = []

    def exists(self, path) -> bool:
        candidate = Path(path)
        return candidate in self.files or candidate in self.not_regular

    def is_file(self, path) -> bool:
        candidate = Path(path)
        return candidate in self.files and candidate not in self.not_regular

    def is_symlink(self, path) -> bool:
        return Path(path) in self.symlinks

    def size(self, path) -> int:
        return len(self.files[Path(path)])

    def sha256(self, path) -> str:
        return hashlib.sha256(self.files[Path(path)]).hexdigest()

    def read_text(self, path) -> str:
        return self.files[Path(path)].decode("utf-8")

    def write_text_atomic(self, path, text: str) -> None:
        target = Path(path)
        self.files[target] = text.encode("utf-8")
        self.writes.append(target)

    def mkdirs(self, path) -> None:
        self.created_dirs.append(Path(path))


CENSUS_STDOUT = {
    "root": ("total 8\n"
             "drwxr-xr-x 3 0 0 96 Sep 14 17:02 .minio.sys\n"
             "-rw-r--r-- 1 0 0 4 Sep 14 17:02 .minio.sys/format.json\n"),
    "uid1000": ("total 8\n"
                "drwxr-xr-x 3 1000 1000 96 Sep 14 17:02 .minio.sys\n"
                "-rw-r--r-- 1 1000 1000 4 Sep 14 17:02 .minio.sys/format.json\n"),
    "mixed": ("total 8\n"
              "drwxr-xr-x 3 0 0 96 Sep 14 17:02 .minio.sys\n"
              "-rw-r--r-- 1 1000 1000 4 Sep 14 17:02 .minio.sys/format.json\n"),
    "unknown": "total 0\n",
}


class Handler:
    """全绿生产画像默认值；kwargs 注入各 gate 失败与分支。"""

    def __init__(self, *, census: tuple[str, ...] = ("root", "uid1000"), census_rc: int = 0,
                 image_ok: bool = True, image_id: str = "sha256:imgm1441\n",
                 post_minio_image: str = "sha256:imgm1441\n",
                 unhealthy: tuple[str, ...] = (),
                 missing: tuple[str, ...] = (), stop_rc: int = 0, tar_rc: int = 0,
                 tar_stderr: str = "", chown_rc: int = 0, up_rc: int = 0,
                 restore_rc: int = 0, replace_after_up: tuple[str, ...] = (),
                 minio_sick_after_up: bool = False) -> None:
        self.census = list(census)
        self.census_rc = census_rc
        self.census_runs = 0
        self.image_ok = image_ok
        self.image_id = image_id
        self.post_minio_image = post_minio_image
        self.unhealthy = set(unhealthy)
        self.missing = set(missing)
        self.stop_rc = stop_rc
        self.tar_rc = tar_rc
        self.tar_stderr = tar_stderr
        self.chown_rc = chown_rc
        self.up_rc = up_rc
        self.restore_rc = restore_rc
        self.replace_after_up = set(replace_after_up)
        self.minio_sick_after_up = minio_sick_after_up
        self.up_seen = False

    def __call__(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
        if argv[:2] == ("docker", "version"):
            return 0, "29.7.2\n", ""
        if argv[:3] == ("docker", "image", "inspect"):
            if self.image_ok:
                return 0, self.image_id, ""
            return 1, "", "Error: No such image"
        if argv[:2] == ("docker", "inspect"):
            service = argv[2][len(mva.PROJECT) + 1:-2]
            if service in self.missing:
                return 1, "", "Error: No such object"
            if argv[4] == "{{.Id}}":
                if self.up_seen and service in self.replace_after_up:
                    return 0, f"sha256:{service}-replaced\n", ""
                return 0, f"sha256:{service}-base\n", ""
            if argv[4] == "{{.Image}}":
                # 重建后 minio 容器实际运行的镜像 ID（仅 minio 允许该格式）
                return 0, self.post_minio_image, ""
            if (self.up_seen and self.minio_sick_after_up
                    and service == "minio"):
                return 0, "unhealthy\n", ""
            # unhealthy 仅刻画 up 前状态（execute/rollback 预检面）；
            # up 后的病态由 minio_sick_after_up 表达
            status = ("unhealthy" if not self.up_seen and service in self.unhealthy
                      else "healthy")
            return 0, f"{status}\n", ""
        if argv[:2] == ("docker", "run"):
            if "-lnAR" in argv:
                if self.census_rc:
                    return self.census_rc, "", "ls failed"
                index = min(self.census_runs, len(self.census) - 1)
                self.census_runs += 1
                return 0, CENSUS_STDOUT[self.census[index]], ""
            if "/bin/chown" in argv:
                return self.chown_rc, "", "chown failed"
            if "-cf" in argv:
                return self.tar_rc, "", self.tar_stderr
            if "-xf" in argv:
                return self.restore_rc, "", "tar extract failed"
        if argv[:2] == ("docker", "stop"):
            return self.stop_rc, "", "stop failed"
        if argv[:2] == ("docker", "compose"):
            self.up_seen = True
            return self.up_rc, "", "compose up failed"
        raise AssertionError(f"unexpected argv: {argv}")


def good_manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "tool": mva.TOOL_NAME,
        "volume": mva.VOLUME_NAME,
        "project": mva.PROJECT,
        "image_id": "sha256:imgm1441",
        "pre_migration_owner_class": "root",
        "tar_file": TAR.name,
        "sha256": TAR_SHA,
        "size_bytes": len(TAR_BYTES),
        "created_at_utc": "2026-09-17T04:53:00Z",
    }


def make_fs(*, tar: bool = True, sha: str | None = TAR_SHA,
            manifest: dict | str | None | object = ...,
            extra: dict[Path, bytes] | None = None,
            symlinks: set[Path] | None = None,
            without: set[Path] | tuple[Path, ...] = (),
            not_regular: set[Path] | tuple[Path, ...] = ()) -> FakeFS:
    files: dict[Path, bytes] = {}
    if tar:
        files[TAR] = TAR_BYTES
    if sha is not None:
        files[SHA_FILE] = f"{sha}  {TAR.name}\n".encode()
    if manifest is ...:
        files[MANIFEST_FILE] = json.dumps(good_manifest()).encode()
    elif isinstance(manifest, (dict, str)):
        text = json.dumps(manifest) if isinstance(manifest, dict) else manifest
        files[MANIFEST_FILE] = text.encode()
    # 控制面文件占位：R2-4 停 minio 前仅元数据核对（exists/is_file/
    # is_symlink），内容绝不读取——空字节占位即可（源级契约测试锁定）
    files[mva.COMPOSE_FILE] = b""
    files[mva.ENV_FILE] = b""
    files.update(extra or {})
    for missing in without:
        files.pop(Path(missing), None)
    return FakeFS(files, symlinks=symlinks, not_regular=set(not_regular))


def run_main(args, handler=None, fs=None):
    runner = FakeRunner(handler or Handler())
    fake_fs = fs if fs is not None else make_fs()
    sleeper = FakeSleeper()
    rc = mva.main(args, runner=runner, fs=fake_fs, clock=FakeClock(),
                  sleeper=sleeper)
    return rc, runner, fake_fs, sleeper


def mutations(runner: FakeRunner) -> list[tuple[str, ...]]:
    """状态变更类 argv：stop / compose / 非 census 的 docker run。"""
    return [a for a in runner.calls
            if a[:2] in (("docker", "stop"), ("docker", "compose"))
            or (a[:2] == ("docker", "run") and "-lnAR" not in a)]


def report_payload(fs: FakeFS, mode: str) -> dict:
    path = mva.ARTIFACT_DIR / f"{mode}-{STAMP}.json"
    assert path in fs.files, f"report not written: {path}"
    return json.loads(fs.files[path].decode("utf-8"))


# ---------------------------------------------------------------- 常量与源级契约


def test_constants_locked() -> None:
    assert mva.PROJECT == "aios-m14-03-production-rehearsal"
    assert mva.VOLUME_NAME == "aios-m14-03-production-rehearsal_minio-data"
    assert mva.SELF_IMAGE_REF == "aios/minio:RELEASE.2025-10-15T17-29-55Z"
    assert mva.MINIO_CONTAINER == f"{mva.PROJECT}-minio-1"
    assert mva.ARTIFACT_DIR == mva.REPO_ROOT / ".verify" / "artifacts" / "m14-41-minio-volume-adoption"
    assert mva.ENV_FILE.name == "env.production-recovery"
    assert mva.COMPOSE_FILE.name == "docker-compose.yml"
    assert mva.COMPOSE_FILE.parent.name == "infra"
    assert sorted(mva.STACK_SERVICES) == sorted(STACK_SERVICES)


def test_source_env_secret_file_never_read() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'env.production-recovery' in source  # 仅作为锚定常量存在
    assert "ENV_FILE.read" not in source
    assert "open(ENV_FILE" not in source
    assert "MINIO_ROOT_PASSWORD" not in source
    assert "MINIO_ROOT_USER" not in source


# ---------------------------------------------------------------- plan


def test_plan_zero_subprocess_zero_writes_pure_json_stdout(capsys) -> None:
    def boom(argv):
        raise AssertionError("plan must not run any command")

    rc, runner, fs, _ = run_main(["plan"], handler=boom)
    assert rc == 0
    assert runner.calls == []
    assert fs.writes == []
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "plan"
    assert payload["targets"]["project"] == mva.PROJECT
    assert payload["targets"]["volume"] == mva.VOLUME_NAME
    assert payload["targets"]["image"] == mva.SELF_IMAGE_REF
    assert payload["targets"]["minio_container"] == mva.MINIO_CONTAINER
    assert payload["risks"] and payload["stages"]
    assert payload["targets"]["argv_whitelist_note"]


def test_plan_argv_templates_match_gate_shapes(capsys) -> None:
    rc, _, _, _ = run_main(["plan"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    templates = payload["argv_templates"]
    assert templates["census"] == list(EXPECTED_CENSUS)
    assert templates["stop"] == list(EXPECTED_STOP)
    assert templates["chown"] == list(EXPECTED_CHOWN)
    assert templates["compose_up"] == list(EXPECTED_UP)
    assert templates["backup_tar"]["image"] == mva.SELF_IMAGE_REF
    assert templates["backup_tar"]["tar_member"] == "/backup/minio-data-backup-<stamp>.tar"
    assert templates["backup_tar"]["host_bind"] == str(mva.ARTIFACT_DIR)


def test_plan_tar_templates_pin_user_and_mount_modes(capsys) -> None:
    """R2-1/2/3：plan 模板与真实 helper argv 同步——恒 --user 0:0 +
    挂载模式（备份宿主目的地可写 / 恢复宿主备份源只读）。"""
    rc, _, _, _ = run_main(["plan"])
    assert rc == 0
    templates = json.loads(capsys.readouterr().out)["argv_templates"]
    backup, restore = templates["backup_tar"], templates["restore_tar"]
    assert backup.get("user") == "0:0"
    assert restore.get("user") == "0:0"
    assert backup["volume_mount"] == f"{mva.VOLUME_NAME}:/data:ro"
    assert backup.get("host_mount") == f"{mva.ARTIFACT_DIR}:/backup"  # 目的地可写
    assert restore["volume_mount"] == f"{mva.VOLUME_NAME}:/data"  # 恢复需写卷
    assert restore.get("host_mount") == f"{mva.ARTIFACT_DIR}:/backup:ro"  # 源只读


def test_plan_stages_verify_control_files_before_stop(capsys) -> None:
    """R2-3/4：plan 阶段表与真实状态机同步——stop-minio 之前有控制面文件
    仅元数据核对阶段。"""
    rc, _, _, _ = run_main(["plan"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    ids = [stage["id"] for stage in payload["stages"]]
    assert "control-files-verify" in ids
    assert ids.index("control-files-verify") < ids.index("stop-minio")


def test_up_argv_pins_env_file_and_profile_local() -> None:
    """修正 1：up 必须显式 --env-file 锚定 ENV_FILE + --profile local
    （对齐 production_recovery compose 纪律）；只允许路径锚定，绝不读取
    /输出 env 值。"""
    up = list(mva.UP_ARGV)
    i_env = up.index("--env-file")
    i_profile = up.index("--profile")
    i_up = up.index("up")
    assert up[i_env + 1] == str(mva.ENV_FILE)  # 路径锚定，非内容
    assert up[i_profile + 1] == "local"
    assert i_env < i_profile < i_up  # 都在 up 之前
    assert mva.UP_ARGV in mva.FIXED_ARGV_SHAPES  # 门内固定形态同步
    # plan 模板与真实 argv 逐 token 一致
    assert mva.UP_ARGV == EXPECTED_UP


def test_up_argv_and_reports_never_carry_env_values() -> None:
    """修正 1 附带契约：argv/报告只携带 env 文件路径，绝不携带 env 值。"""
    rc, runner, fs, _ = run_main(["execute", "--confirm", mva.CONFIRM_EXECUTE])
    assert rc == 0
    for argv in runner.calls:
        if "--env-file" in argv:
            assert argv[argv.index("--env-file") + 1] == str(mva.ENV_FILE)
    payload = json.loads(fs.files[REPORT_JSON].decode("utf-8"))
    assert payload["config"]["env_file"] == mva.ENV_FILE.name  # 仅文件名锚定
    text = fs.files[REPORT_JSON].decode("utf-8")
    assert "aios12345" not in text  # compose 中的 env 值绝不出现
    assert "MINIO_ROOT_PASSWORD" not in text


@pytest.mark.parametrize("extract", [False, True])
def test_helper_containers_pin_user_0_0_and_minimal_mounts(extract: bool) -> None:
    """R2-1/2：全部 helper 容器恒显式 --user 0:0（本地固定镜像默认
    minio:minio，root 属主卷上隐式用户不安全），且挂载最小化——备份
    （extract=False）卷 :ro + 宿主目的地可写；恢复（extract=True）卷可写 +
    宿主备份源 :ro。"""
    argv = mva.tar_helper_argv(TAR.name, mva.ARTIFACT_DIR, extract=extract)
    assert "--user" in argv
    assert argv[argv.index("--user") + 1] == "0:0"
    assert argv.index("--user") < argv.index(mva.SELF_IMAGE_REF)  # 镜像之前
    mounts = {argv[i + 1] for i, tok in enumerate(argv) if tok == "-v"}
    if extract:
        assert mounts == {f"{mva.VOLUME_NAME}:/data",
                           f"{mva.ARTIFACT_DIR}:/backup:ro"}
    else:
        assert mounts == {f"{mva.VOLUME_NAME}:/data:ro",
                           f"{mva.ARTIFACT_DIR}:/backup"}


def test_fixed_helper_argvs_pin_user_0_0_and_probe_readonly() -> None:
    """R2-1/2：CENSUS/CHOWN 固定形态同样恒 --user 0:0；普查卷只读。"""
    for argv in (mva.CENSUS_ARGV, mva.CHOWN_ARGV):
        assert "--user" in argv, argv
        assert argv[argv.index("--user") + 1] == "0:0"
        assert argv.index("--user") < argv.index(mva.SELF_IMAGE_REF)
    assert f"{mva.VOLUME_NAME}:/probe:ro" in mva.CENSUS_ARGV  # 普查 :ro
    assert f"{mva.VOLUME_NAME}:/data" in mva.CHOWN_ARGV  # chown 须写卷
    assert mva.CENSUS_ARGV == EXPECTED_CENSUS  # 与白名单断言同步
    assert mva.CHOWN_ARGV == EXPECTED_CHOWN


# ---------------------------------------------------------------- 确认短语门


@pytest.mark.parametrize("phrase", ["", "EXECUTE MINIO VOLUME ADOPT",
                                    "execute minio volume adoption",
                                    "EXECUTE  MINIO VOLUME ADOPTION"])
def test_execute_rejects_nonexact_confirm(phrase: str) -> None:
    args = ["execute", "--confirm", phrase] if phrase else ["execute"]
    rc, runner, fs, _ = run_main(args)
    assert rc == 2
    assert runner.calls == []
    assert fs.writes == []


@pytest.mark.parametrize("phrase", ["", "EXECUTE MINIO VOLUME ROLLBACK NOW"])
def test_rollback_rejects_nonexact_confirm(phrase: str) -> None:
    args = ["rollback", "--backup-file", str(TAR)]
    if phrase:
        args += ["--confirm", phrase]
    rc, runner, fs, _ = run_main(args)
    assert rc == 2
    assert runner.calls == []
    assert fs.writes == []


# ---------------------------------------------------------------- execute 状态机


def test_execute_root_full_sequence_exact_argv_order() -> None:
    rc, runner, fs, _ = run_main(["execute", "--confirm", mva.CONFIRM_EXECUTE])
    assert rc == 0
    calls = runner.calls
    assert EXPECTED_CENSUS in calls
    assert EXPECTED_STOP in calls
    assert EXPECTED_BACKUP in calls
    assert EXPECTED_CHOWN in calls
    assert EXPECTED_UP in calls
    i_census1 = calls.index(EXPECTED_CENSUS)
    i_stop = calls.index(EXPECTED_STOP)
    i_backup = calls.index(EXPECTED_BACKUP)
    i_chown = calls.index(EXPECTED_CHOWN)
    i_census2 = calls.index(EXPECTED_CENSUS, i_census1 + 1)
    i_up = calls.index(EXPECTED_UP)
    assert i_census1 < i_stop < i_backup < i_chown < i_census2 < i_up
    # 停止动作有且只有一个，且只针对 minio 容器
    assert [a for a in calls if a[:2] == ("docker", "stop")] == [EXPECTED_STOP]
    # compose up 只重建 minio 服务
    assert [a for a in calls if a[:2] == ("docker", "compose")] == [EXPECTED_UP]
    # 备份伴生文件 + 报告均已写盘
    assert SHA_FILE in fs.writes and MANIFEST_FILE in fs.writes
    assert REPORT_JSON in fs.writes


def test_execute_uid1000_skips_chown_but_backups() -> None:
    rc, runner, fs, _ = run_main(
        ["execute", "--confirm", mva.CONFIRM_EXECUTE],
        handler=Handler(census=("uid1000",)))
    assert rc == 0
    assert EXPECTED_BACKUP in runner.calls
    assert EXPECTED_CHOWN not in runner.calls
    census_calls = [a for a in runner.calls if a == EXPECTED_CENSUS]
    assert len(census_calls) == 1  # 无 chown 则无需二次复核普查
    assert EXPECTED_UP in runner.calls
    payload = report_payload(fs, "execute")
    assert payload["backup"]["performed"] is True
    assert payload["backup"]["skipped_chown"] is True
    manifest = json.loads(fs.files[MANIFEST_FILE].decode())
    assert manifest["pre_migration_owner_class"] == "uid1000"


def test_execute_mixed_fails_closed_before_mutation() -> None:
    rc, runner, fs, _ = run_main(
        ["execute", "--confirm", mva.CONFIRM_EXECUTE],
        handler=Handler(census=("mixed",)))
    assert rc == 2
    assert mutations(runner) == []
    payload = report_payload(fs, "execute")
    assert payload["status"] == "fail"
    assert any("mixed" in str(p) for p in payload["problems"])


def test_execute_unknown_census_fails_closed() -> None:
    rc, runner, _, _ = run_main(
        ["execute", "--confirm", mva.CONFIRM_EXECUTE],
        handler=Handler(census=("unknown",)))
    assert rc == 2
    assert mutations(runner) == []


def test_execute_census_probe_failure_fails_closed() -> None:
    rc, runner, _, _ = run_main(
        ["execute", "--confirm", mva.CONFIRM_EXECUTE],
        handler=Handler(census_rc=1))
    assert rc == 2
    assert mutations(runner) == []


def test_execute_image_missing_short_circuits_no_docker_run() -> None:
    rc, runner, fs, _ = run_main(
        ["execute", "--confirm", mva.CONFIRM_EXECUTE],
        handler=Handler(image_ok=False))
    assert rc == 2
    assert not [a for a in runner.calls if a[:2] == ("docker", "run")]
    payload = report_payload(fs, "execute")
    assert any("minio-local-image-missing" in str(p) for p in payload["problems"])


def test_execute_unhealthy_container_blocks() -> None:
    rc, runner, _, _ = run_main(
        ["execute", "--confirm", mva.CONFIRM_EXECUTE],
        handler=Handler(unhealthy=("web",)))
    assert rc == 2
    assert mutations(runner) == []


def test_execute_missing_container_blocks() -> None:
    rc, runner, _, _ = run_main(
        ["execute", "--confirm", mva.CONFIRM_EXECUTE],
        handler=Handler(missing=("api",)))
    assert rc == 2
    assert mutations(runner) == []


def test_execute_backup_tar_failure_skips_chown_and_redacts_secret() -> None:
    rc, runner, fs, _ = run_main(
        ["execute", "--confirm", mva.CONFIRM_EXECUTE],
        handler=Handler(tar_rc=1, tar_stderr=SECRET_STDERR))
    assert rc == 2
    assert EXPECTED_CHOWN not in runner.calls
    assert EXPECTED_UP not in runner.calls
    text = fs.files[REPORT_JSON].decode("utf-8")
    assert "hunter2secret99" not in text
    assert "[REDACTED:credential]" in text


def test_execute_backup_file_missing_on_host_skips_chown() -> None:
    rc, runner, _, _ = run_main(
        ["execute", "--confirm", mva.CONFIRM_EXECUTE], fs=make_fs(tar=False))
    assert rc == 2
    assert EXPECTED_CHOWN not in runner.calls
    assert EXPECTED_UP not in runner.calls


def test_execute_chown_verify_failure_blocks_up() -> None:
    rc, runner, _, _ = run_main(
        ["execute", "--confirm", mva.CONFIRM_EXECUTE],
        handler=Handler(census=("root", "root")))
    assert rc == 2
    assert EXPECTED_UP not in runner.calls
    assert EXPECTED_CHOWN in runner.calls


def test_execute_compose_up_failure_preserves_backup_facts() -> None:
    rc, _, fs, _ = run_main(
        ["execute", "--confirm", mva.CONFIRM_EXECUTE],
        handler=Handler(up_rc=1))
    assert rc == 2
    payload = report_payload(fs, "execute")
    assert payload["backup"]["performed"] is True
    assert payload["backup"]["sha256"] == TAR_SHA
    assert any("compose-up-failed" in str(p) for p in payload["problems"])


def test_execute_post_container_replaced_detected() -> None:
    rc, runner, fs, _ = run_main(
        ["execute", "--confirm", mva.CONFIRM_EXECUTE],
        handler=Handler(replace_after_up=("api",)))
    assert rc == 2
    assert EXPECTED_UP in runner.calls
    payload = report_payload(fs, "execute")
    assert any("container-replaced" in str(p) for p in payload["problems"])
    assert payload["backup"]["performed"] is True  # 已完成事实保留


def test_execute_health_wait_timeout_fails() -> None:
    rc, _, fs, sleeper = run_main(
        ["execute", "--confirm", mva.CONFIRM_EXECUTE],
        handler=Handler(minio_sick_after_up=True))
    assert rc == 2
    assert len(sleeper.slept) >= 1
    payload = report_payload(fs, "execute")
    assert any("health-wait" in str(p) for p in payload["problems"])


def test_execute_post_minio_image_mismatch_fails_closed() -> None:
    """修正 2：up 后必须复核重建 minio 容器实际镜像 ID == 预检本地固定
    镜像 ID；不一致 fail-closed（即便容器已 healthy）。"""
    rc, runner, fs, _ = run_main(
        ["execute", "--confirm", mva.CONFIRM_EXECUTE],
        handler=Handler(post_minio_image="sha256:drifted-other\n"))
    assert rc == 2
    assert EXPECTED_UP in runner.calls  # 已 up，后验阶段发现漂移
    payload = report_payload(fs, "execute")
    assert any("post-minio-image" in str(p) for p in payload["problems"])
    assert payload["backup"]["performed"] is True  # 已完成事实保留
    # 报告不回显漂移镜像的完整 stderr 之类敏感面——仅问题码语义


def test_execute_minio_unhealthy_still_blocks() -> None:
    """修正 5 回归：execute 预检仍要求六容器全 healthy（含 minio）。"""
    rc, runner, _, _ = run_main(
        ["execute", "--confirm", mva.CONFIRM_EXECUTE],
        handler=Handler(unhealthy=("minio",)))
    assert rc == 2
    assert mutations(runner) == []


def test_execute_backup_path_validated_before_stop_zero_mutation() -> None:
    """修正 4：备份路径校验/预备移到停容器之前——路径失败零容器变更。"""
    rc, runner, fs, _ = run_main(
        ["execute", "--confirm", mva.CONFIRM_EXECUTE],
        fs=make_fs(symlinks={TAR}))
    assert rc == 2
    assert mutations(runner) == []  # 无 stop、无非 census docker run
    assert EXPECTED_STOP not in runner.calls
    payload = report_payload(fs, "execute")
    assert any("backup-path-symlink-target" in str(p) for p in payload["problems"])


def test_execute_symlinked_artifact_root_zero_mutation(capsys) -> None:
    """修正 4：锚定目录本身为 symlink → 拒绝且零容器变更（该目录连报告
    也 fail-closed 拒写——问题码走日志可见）。"""
    rc, runner, _, _ = run_main(
        ["execute", "--confirm", mva.CONFIRM_EXECUTE],
        fs=make_fs(symlinks={mva.ARTIFACT_DIR}))
    assert rc == 2
    assert mutations(runner) == []
    assert "backup-path-symlink-in-path" in capsys.readouterr().out


def test_execute_report_schema_v1_and_sidecars() -> None:
    rc, _, fs, _ = run_main(["execute", "--confirm", mva.CONFIRM_EXECUTE])
    assert rc == 0
    payload = report_payload(fs, "execute")
    assert payload["schema_version"] == 1
    assert payload["mode"] == "execute"
    assert payload["started_at_utc"] and payload["ended_at_utc"]
    assert payload["status"] == "pass"
    assert payload["checks"] and payload["problems"] == []
    # R2-4：成功路径记录控制面文件元数据核对（停机之前）
    assert any(c["check_id"] == "control-files-verified" for c in payload["checks"])
    assert payload["census"]["classification"] == "root"
    backup = payload["backup"]
    assert backup["performed"] is True
    assert backup["sha256"] == TAR_SHA
    assert backup["size_bytes"] == len(TAR_BYTES)
    assert sorted(payload["baseline"]["containers"]) == sorted(STACK_SERVICES)
    assert sorted(payload["post"]["containers"]) == sorted(STACK_SERVICES)
    for service in STACK_SERVICES:
        # 容器 ID 记录前 .strip() 归一化（无尾随换行）
        assert payload["baseline"]["containers"][service] == f"sha256:{service}-base"
        assert payload["post"]["containers"][service] == f"sha256:{service}-base"
    hint = payload["rollback_hint"]
    assert TAR.name in json.dumps(hint)
    assert mva.CONFIRM_ROLLBACK in json.dumps(hint)
    manifest = json.loads(fs.files[MANIFEST_FILE].decode())
    assert manifest["image_id"] == "sha256:imgm1441"
    assert manifest["pre_migration_owner_class"] == "root"
    assert manifest["volume"] == mva.VOLUME_NAME


# ---------------------------------------------------------------- argv 门


def surface(**overrides):
    base = {"stamp": STAMP, "helper_tar_name": TAR.name,
            "host_backup_dir": mva.ARTIFACT_DIR}
    base.update(overrides)
    return mva.Surface(**base)


EVIL_ARGV = [
    ("docker", "pull", "minio/minio:latest"),
    ("docker", "pull", mva.SELF_IMAGE_REF),
    ("docker", "build", "-t", "evil", "."),
    ("docker", "compose", "-f", str(mva.COMPOSE_FILE), "--project-name",
     mva.PROJECT, "down"),
    ("docker", "compose", "-f", str(mva.COMPOSE_FILE), "--project-name",
     mva.PROJECT, "kill", "minio"),
    ("docker", "system", "prune", "-f"),
    ("docker", "volume", "prune", "-f"),
    ("docker", "rm", f"{mva.PROJECT}-api-1"),
    ("docker", "rm", mva.MINIO_CONTAINER),
    ("docker", "stop", f"{mva.PROJECT}-api-1"),
    ("docker", "stop", f"{mva.PROJECT}-postgres-1"),
    ("docker", "restart", mva.MINIO_CONTAINER),
    ("docker", "exec", mva.MINIO_CONTAINER, "sh"),
    # 普查缺 --network none / 缺 :ro
    ("docker", "run", "--rm", "-v", f"{mva.VOLUME_NAME}:/probe:ro",
     "--entrypoint", "/bin/ls", mva.SELF_IMAGE_REF, "-lnAR", "/probe"),
    ("docker", "run", "--rm", "--network", "none",
     "-v", f"{mva.VOLUME_NAME}:/probe",
     "--entrypoint", "/bin/ls", mva.SELF_IMAGE_REF, "-lnAR", "/probe"),
    # R2-1：helper 缺显式 --user 0:0（隐式镜像默认用户）一律拒绝
    ("docker", "run", "--rm", "--network", "none",
     "-v", f"{mva.VOLUME_NAME}:/probe:ro",
     "--entrypoint", "/bin/ls", mva.SELF_IMAGE_REF, "-lnAR", "/probe"),
    ("docker", "run", "--rm", "--user", "1000:1000", "--network", "none",
     "-v", f"{mva.VOLUME_NAME}:/probe:ro",
     "--entrypoint", "/bin/ls", mva.SELF_IMAGE_REF, "-lnAR", "/probe"),
    ("docker", "run", "--rm", "-v", f"{mva.VOLUME_NAME}:/data",
     "--entrypoint", "/bin/chown", mva.SELF_IMAGE_REF,
     "-R", "1000:1000", "/data"),
    # R2-2：备份卷可写（无 :ro）/ 备份宿主目的地 :ro → 拒绝
    ("docker", "run", "--rm", "--user", "0:0",
     "-v", f"{mva.VOLUME_NAME}:/data",
     "-v", f"{mva.ARTIFACT_DIR}:/backup",
     "--entrypoint", "/bin/tar", mva.SELF_IMAGE_REF, "-cf",
     f"/backup/{TAR.name}", "-C", "/data", "."),
    ("docker", "run", "--rm", "--user", "0:0",
     "-v", f"{mva.VOLUME_NAME}:/data:ro",
     "-v", f"{mva.ARTIFACT_DIR}:/backup:ro",
     "--entrypoint", "/bin/tar", mva.SELF_IMAGE_REF, "-cf",
     f"/backup/{TAR.name}", "-C", "/data", "."),
    # R2-2：恢复宿主备份源可写（无 :ro）/ 恢复卷 :ro → 拒绝
    ("docker", "run", "--rm", "--user", "0:0",
     "-v", f"{mva.VOLUME_NAME}:/data",
     "-v", f"{mva.ARTIFACT_DIR}:/backup",
     "--entrypoint", "/bin/tar", mva.SELF_IMAGE_REF, "-xf",
     f"/backup/{TAR.name}", "-C", "/data"),
    ("docker", "run", "--rm", "--user", "0:0",
     "-v", f"{mva.VOLUME_NAME}:/data:ro",
     "-v", f"{mva.ARTIFACT_DIR}:/backup:ro",
     "--entrypoint", "/bin/tar", mva.SELF_IMAGE_REF, "-xf",
     f"/backup/{TAR.name}", "-C", "/data"),
    # 错误镜像 / 插入 token
    ("docker", "run", "--rm", "--network", "none",
     "-v", f"{mva.VOLUME_NAME}:/probe:ro", "--entrypoint", "/bin/ls",
     "minio/minio:latest", "-lnAR", "/probe"),
    ("docker", "run", "--rm", "--network", "none", "--privileged",
     "-v", f"{mva.VOLUME_NAME}:/probe:ro", "--entrypoint", "/bin/ls",
     mva.SELF_IMAGE_REF, "-lnAR", "/probe"),
    # chown 错误 uid / 备份写非备份目录 / 尾部注入
    ("docker", "run", "--rm", "-v", f"{mva.VOLUME_NAME}:/data",
     "--entrypoint", "/bin/chown", mva.SELF_IMAGE_REF, "-R", "0:0", "/data"),
    ("docker", "run", "--rm", "-v", f"{mva.VOLUME_NAME}:/data:ro",
     "-v", "C:/evil:/backup", "--entrypoint", "/bin/tar", mva.SELF_IMAGE_REF,
     "-cf", f"/backup/{TAR.name}", "-C", "/data", "."),
    ("docker", "stop", mva.MINIO_CONTAINER, "&&", "docker", "rm", "x"),
    ("docker", "compose", "-f", str(mva.COMPOSE_FILE), "--project-name",
     mva.PROJECT, "up", "-d", "--no-build", "minio"),
    ("docker", "compose", "-f", str(mva.COMPOSE_FILE), "--project-name",
     mva.PROJECT, "up", "-d", "--no-build", "--no-deps", "api"),
    # 缺 --env-file / --profile local（不满足生产恢复 compose 纪律）
    ("docker", "compose", "-f", str(mva.COMPOSE_FILE), "--project-name",
     mva.PROJECT, "up", "-d", "--no-build", "--no-deps", "minio"),
    ("docker", "compose", "-f", str(mva.COMPOSE_FILE), "--project-name",
     mva.PROJECT, "--profile", "local",
     "up", "-d", "--no-build", "--no-deps", "minio"),
    # {{.Image}} 是 minio-only 镜像复核格式——其它容器一律拒绝
    ("docker", "inspect", f"{mva.PROJECT}-api-1",
     "--format", "{{.Image}}"),
    ("docker", "inspect", f"{mva.PROJECT}-postgres-1",
     "--format", "{{.Image}}"),
]


@pytest.mark.parametrize("evil", EVIL_ARGV)
def test_gate_rejects_every_mutation_or_drift_shape(evil: tuple[str, ...]) -> None:
    assert mva.is_allowed_docker_argv(evil, surface()) is False


def test_gate_accepts_exactly_legitimate_shapes() -> None:
    legit = [
        ("docker", "version", "--format", "{{.Server.Version}}"),
        ("docker", "image", "inspect", mva.SELF_IMAGE_REF, "--format", "{{.Id}}"),
        ("docker", "inspect", mva.MINIO_CONTAINER, "--format", "{{.Id}}"),
        ("docker", "inspect", f"{mva.PROJECT}-api-1",
         "--format", "{{.State.Health.Status}}"),
        ("docker", "inspect", mva.MINIO_CONTAINER, "--format", "{{.Image}}"),
        EXPECTED_CENSUS, EXPECTED_STOP, EXPECTED_BACKUP, EXPECTED_CHOWN,
        EXPECTED_UP, EXPECTED_RESTORE,
    ]
    for argv in legit:
        assert mva.is_allowed_docker_argv(argv, surface()) is True, argv
    # 备份目录未在 Surface 上登记时，备份形态同样拒绝
    assert mva.is_allowed_docker_argv(
        EXPECTED_BACKUP, surface(helper_tar_name=None, host_backup_dir=None)) is False


def test_gate_runner_raises_before_execution() -> None:
    def unreachable(argv):
        raise AssertionError("must not execute a rejected argv")

    gated = mva.GateRunner(FakeRunner(unreachable), surface())
    with pytest.raises(mva.CommandNotAllowedError):
        gated.run(("docker", "pull", "minio/minio:latest"))


# ---------------------------------------------------------------- 路径安全


def test_validate_artifact_path_boundaries() -> None:
    ok, problem = mva.validate_artifact_path(TAR)
    assert problem is None and ok is not None
    # "C:/Windows/evil.tar" 在 POSIX 上是相对路径（会锚定进 ARTIFACT_DIR），
    # 须按平台选取真正的目录外绝对路径
    outside = Path("C:/Windows/evil.tar") if os.name == "nt" else Path("/tmp/evil.tar")
    _, problem = mva.validate_artifact_path(outside)
    assert problem == "outside-artifact-dir"
    _, problem = mva.validate_artifact_path(mva.ARTIFACT_DIR / ".." / "evil.tar")
    assert problem is not None
    _, problem = mva.validate_artifact_path(Path(TAR.name))  # 裸文件名 → 锚定目录内
    assert problem is None


def test_validate_artifact_path_lexical_boundary_hardened() -> None:
    """修正 4：词法边界先行——即使 resolve 能把 symlinked root/component
    折叠掉，原始路径不在锚定目录内即拒绝（不依赖 resolve 结果放行）。"""
    # 仓库内但锚定目录之外的绝对路径 → 词法拒绝（不进入 resolve 比较）
    _, problem = mva.validate_artifact_path(mva.REPO_ROOT / "services" / "evil.tar")
    assert problem == "outside-artifact-dir"
    _, problem = mva.validate_artifact_path(
        mva.ARTIFACT_DIR.parent / "m14-40-other" / "evil.tar")
    assert problem == "outside-artifact-dir"
    # 任何 ".." 组件 → path-escape（词法先行，resolve 之前）
    _, problem = mva.validate_artifact_path(
        mva.ARTIFACT_DIR / "sub" / ".." / ".." / "evil.tar")
    assert problem == "path-escape"


def test_artifact_symlink_problem_detected() -> None:
    assert mva.artifact_symlink_problem(TAR, make_fs(symlinks={TAR})) == "symlink-target"
    mid = mva.ARTIFACT_DIR / "minio-data-backup-20260917-045300.tar"
    fs = FakeFS(symlinks={mva.ARTIFACT_DIR})
    assert mva.artifact_symlink_problem(mid, fs) == "symlink-in-path"
    assert mva.artifact_symlink_problem(TAR, make_fs()) is None


def test_artifact_symlink_component_walk_hardened() -> None:
    """修正 4：从 REPO_ROOT 沿未 resolve 的原始组件逐级检查——锚定目录的
    任何祖先组件（.verify / artifacts）为 symlink 也必须拒绝，resolve
    折叠不掉。"""
    for evil in (mva.REPO_ROOT / ".verify", mva.ARTIFACT_DIR.parent,
                 mva.ARTIFACT_DIR):
        fs = FakeFS(symlinks={evil})
        assert mva.artifact_symlink_problem(TAR, fs) == "symlink-in-path", evil
        assert mva.artifact_symlink_problem(Path(TAR.name), fs) == "symlink-in-path"


# ------------------------------------------------- 控制面文件预停机门（R2-4）


CONTROL_MODES = (
    ("missing", "control-file-missing:{}"),
    ("not_regular", "control-file-not-regular:{}"),
    ("symlink", "control-file-symlink:{}"),
)


def control_fs(path: Path, mode: str) -> FakeFS:
    """按失败模式构造控制面文件画像（missing/not_regular/symlink）。"""
    if mode == "missing":
        return make_fs(without=(path,))
    if mode == "not_regular":
        return make_fs(not_regular=(path,))
    assert mode == "symlink"
    return make_fs(symlinks={path})


@pytest.mark.parametrize("label,path", CONTROL_FILES)
@pytest.mark.parametrize("mode,problem_tpl", CONTROL_MODES)
def test_execute_control_file_gate_blocks_before_stop(
        label: str, path: Path, mode: str, problem_tpl: str) -> None:
    """R2-4：execute 停 minio 之前先核对 COMPOSE_FILE/ENV_FILE 仅元数据
    （存在 + 常规文件 + 非 symlink）；任一不满足零停机零变更。"""
    rc, runner, fake_fs, _ = run_main(
        ["execute", "--confirm", mva.CONFIRM_EXECUTE], fs=control_fs(path, mode))
    assert rc == 2
    assert EXPECTED_STOP not in runner.calls  # 零停机
    assert mutations(runner) == []
    payload = report_payload(fake_fs, "execute")
    assert any(str(p.get("code")) == problem_tpl.format(label)
               for p in payload["problems"])


@pytest.mark.parametrize("label,path", CONTROL_FILES)
@pytest.mark.parametrize("mode,problem_tpl", CONTROL_MODES)
def test_rollback_control_file_gate_blocks_before_stop(
        label: str, path: Path, mode: str, problem_tpl: str) -> None:
    """R2-4：rollback 停 minio 之前同样核对控制面文件元数据；不满足零停机
    零 Docker 副作用。"""
    rc, runner, fake_fs, _ = run_main(
        ["rollback", "--backup-file", str(TAR), "--confirm", mva.CONFIRM_ROLLBACK],
        fs=control_fs(path, mode))
    assert rc == 2
    assert EXPECTED_STOP not in runner.calls  # 零停机
    assert EXPECTED_RESTORE not in runner.calls
    assert mutations(runner) == []
    payload = report_payload(fake_fs, "rollback")
    assert any(str(p.get("code")) == problem_tpl.format(label)
               for p in payload["problems"])


# ---------------------------------------------------------------- rollback


def test_rollback_backup_file_missing() -> None:
    rc, runner, _, _ = run_main(
        ["rollback", "--backup-file", str(TAR), "--confirm", mva.CONFIRM_ROLLBACK],
        fs=make_fs(tar=False))
    assert rc == 2
    assert runner.calls == []


def test_rollback_sidecar_missing() -> None:
    rc, runner, _, _ = run_main(
        ["rollback", "--backup-file", str(TAR), "--confirm", mva.CONFIRM_ROLLBACK],
        fs=make_fs(sha=None))
    assert rc == 2
    assert runner.calls == []


def test_rollback_checksum_mismatch() -> None:
    fs = make_fs(sha=("0" * 64))
    rc, runner, _, _ = run_main(
        ["rollback", "--backup-file", str(TAR), "--confirm", mva.CONFIRM_ROLLBACK],
        fs=fs)
    assert rc == 2
    assert runner.calls == []
    assert any("checksum-mismatch" in str(p) for p in report_payload(fs, "rollback")["problems"])


# ------------------------------------------------ rollback 工件独立 fail-closed（R2-5）


def test_rollback_sha_sidecar_symlink_refuses_zero_docker() -> None:
    """R2-5：.sha256 为 symlink → 独立拒绝，零 Docker 副作用。"""
    fs = make_fs(symlinks={SHA_FILE})
    rc, runner, fake_fs, _ = run_main(
        ["rollback", "--backup-file", str(TAR), "--confirm", mva.CONFIRM_ROLLBACK],
        fs=fs)
    assert rc == 2
    assert runner.calls == []
    payload = report_payload(fake_fs, "rollback")
    assert any(str(p.get("code")) == "checksum-sidecar-symlink-target"
               for p in payload["problems"])


def test_rollback_sha_sidecar_not_regular_refuses_zero_docker() -> None:
    """R2-5：.sha256 非常规文件（目录/FIFO 画像）→ 独立拒绝。"""
    fs = make_fs(not_regular={SHA_FILE})
    rc, runner, fake_fs, _ = run_main(
        ["rollback", "--backup-file", str(TAR), "--confirm", mva.CONFIRM_ROLLBACK],
        fs=fs)
    assert rc == 2
    assert runner.calls == []
    payload = report_payload(fake_fs, "rollback")
    assert any(str(p.get("code")) == "checksum-sidecar-not-regular-file"
               for p in payload["problems"])


def test_rollback_manifest_symlink_refuses_with_guidance() -> None:
    """R2-5：.manifest.json 为 symlink → 独立拒绝 + 人工恢复指引。"""
    fs = make_fs(symlinks={MANIFEST_FILE})
    rc, runner, fake_fs, _ = run_main(
        ["rollback", "--backup-file", str(TAR), "--confirm", mva.CONFIRM_ROLLBACK],
        fs=fs)
    assert rc == 2
    assert runner.calls == []
    payload = report_payload(fake_fs, "rollback")
    assert any(str(p.get("code")) == "manifest-symlink-target"
               for p in payload["problems"])
    assert "manual_recovery_guidance" in payload


def test_rollback_manifest_not_regular_refuses_with_guidance() -> None:
    """R2-5：.manifest.json 非常规文件 → 独立拒绝 + 人工恢复指引。"""
    fs = make_fs(not_regular={MANIFEST_FILE})
    rc, runner, fake_fs, _ = run_main(
        ["rollback", "--backup-file", str(TAR), "--confirm", mva.CONFIRM_ROLLBACK],
        fs=fs)
    assert rc == 2
    assert runner.calls == []
    payload = report_payload(fake_fs, "rollback")
    assert any(str(p.get("code")) == "manifest-not-regular-file"
               for p in payload["problems"])
    assert "manual_recovery_guidance" in payload


def test_rollback_outside_artifact_dir(tmp_path: Path) -> None:
    evil = tmp_path / "evil.tar"
    rc, runner, _, _ = run_main(
        ["rollback", "--backup-file", str(evil), "--confirm", mva.CONFIRM_ROLLBACK],
        fs=make_fs(extra={evil: TAR_BYTES}))
    assert rc == 2
    assert runner.calls == []


def test_rollback_bad_filename_stem() -> None:
    evil = mva.ARTIFACT_DIR / "evil.tar"
    rc, runner, _, _ = run_main(
        ["rollback", "--backup-file", str(evil), "--confirm", mva.CONFIRM_ROLLBACK],
        fs=make_fs(extra={evil: TAR_BYTES}))
    assert rc == 2
    assert runner.calls == []


def test_rollback_manifest_missing_refuses_with_manual_guidance() -> None:
    fs = make_fs(manifest=None)
    rc, runner, _, _ = run_main(
        ["rollback", "--backup-file", str(TAR), "--confirm", mva.CONFIRM_ROLLBACK],
        fs=fs)
    assert rc == 2
    assert runner.calls == []  # 零 Docker 副作用
    payload = report_payload(fs, "rollback")
    assert any("manifest-missing" in str(p) for p in payload["problems"])
    assert "manual_recovery_guidance" in payload


def test_rollback_manifest_missing_old_image_id_refuses() -> None:
    broken = good_manifest()
    broken.pop("image_id")
    rc, runner, _, _ = run_main(
        ["rollback", "--backup-file", str(TAR), "--confirm", mva.CONFIRM_ROLLBACK],
        fs=make_fs(manifest=broken))
    assert rc == 2
    assert runner.calls == []


def test_rollback_manifest_target_mismatch_refuses() -> None:
    broken = good_manifest()
    broken["volume"] = "other-project_minio-data"
    rc, runner, _, _ = run_main(
        ["rollback", "--backup-file", str(TAR), "--confirm", mva.CONFIRM_ROLLBACK],
        fs=make_fs(manifest=broken))
    assert rc == 2
    assert runner.calls == []


def test_rollback_image_id_mismatch_refuses_before_mutation() -> None:
    """修正 3：manifest old_image_id ≠ 当前本地固定镜像 id → 在任何 Docker
    变更（stop/restore）之前 fail-closed，并给人工恢复指引。"""
    drifted = Handler(image_id="sha256:local-drifted-now\n")
    rc, runner, fs, _ = run_main(
        ["rollback", "--backup-file", str(TAR), "--confirm", mva.CONFIRM_ROLLBACK],
        handler=drifted)
    assert rc == 2
    assert mutations(runner) == []  # stop/restore 前拒绝
    assert EXPECTED_STOP not in runner.calls
    assert EXPECTED_RESTORE not in runner.calls
    payload = report_payload(fs, "rollback")
    assert any("image-id-mismatch" in str(p) for p in payload["problems"])
    assert "manual_recovery_guidance" in payload


def test_rollback_proceeds_when_minio_unhealthy() -> None:
    """修正 5 回归：rollback 预检只要求 minio 容器存在（记录 ID），不要求
    healthy——失败 execute 后 minio 可能 stopped/unhealthy。"""
    rc, runner, fs, _ = run_main(
        ["rollback", "--backup-file", str(TAR), "--confirm", mva.CONFIRM_ROLLBACK],
        handler=Handler(unhealthy=("minio",)))
    assert rc == 0
    assert EXPECTED_STOP in runner.calls
    assert EXPECTED_RESTORE in runner.calls
    payload = report_payload(fs, "rollback")
    assert payload["status"] == "pass"
    assert payload["baseline"]["containers"]["minio"] == "sha256:minio-base"


def test_rollback_minio_missing_refuses() -> None:
    """修正 5：minio 容器连 ID 都拿不到 → 拒绝（其余五容器存在且 healthy
    仍是必要条件）。"""
    rc, runner, _, _ = run_main(
        ["rollback", "--backup-file", str(TAR), "--confirm", mva.CONFIRM_ROLLBACK],
        handler=Handler(missing=("minio",)))
    assert rc == 2
    assert mutations(runner) == []


def test_rollback_other_service_unhealthy_still_refuses() -> None:
    """修正 5：除 minio 外五容器仍须存在且 healthy。"""
    rc, runner, _, _ = run_main(
        ["rollback", "--backup-file", str(TAR), "--confirm", mva.CONFIRM_ROLLBACK],
        handler=Handler(unhealthy=("web",)))
    assert rc == 2
    assert mutations(runner) == []


def test_rollback_full_restore_sequence() -> None:
    rc, runner, fs, _ = run_main(
        ["rollback", "--backup-file", str(TAR), "--confirm", mva.CONFIRM_ROLLBACK])
    assert rc == 0
    calls = runner.calls
    assert EXPECTED_STOP in calls
    assert EXPECTED_RESTORE in calls
    assert EXPECTED_CENSUS in calls
    assert EXPECTED_UP in calls
    i_stop = calls.index(EXPECTED_STOP)
    i_restore = calls.index(EXPECTED_RESTORE)
    i_census = calls.index(EXPECTED_CENSUS)
    i_up = calls.index(EXPECTED_UP)
    assert i_stop < i_restore < i_census < i_up
    assert [a for a in calls if a[:2] == ("docker", "stop")] == [EXPECTED_STOP]
    assert [a for a in calls if a[:2] == ("docker", "compose")] == [EXPECTED_UP]
    assert ROLLBACK_REPORT_JSON in fs.writes
    # R2-4：成功路径记录控制面文件元数据核对（停机之前）
    payload = report_payload(fs, "rollback")
    assert any(c["check_id"] == "control-files-verified" for c in payload["checks"])


def test_rollback_restore_verify_failure_blocks_up() -> None:
    rc, runner, _, _ = run_main(
        ["rollback", "--backup-file", str(TAR), "--confirm", mva.CONFIRM_ROLLBACK],
        handler=Handler(census=("uid1000",)))
    assert rc == 2
    assert EXPECTED_UP not in runner.calls
    assert EXPECTED_RESTORE in runner.calls


# ---------------------------------------------------------------- 脱敏


def test_redact_secrets_shapes() -> None:
    assert "hunter2secret99" not in mva.redact_secrets(SECRET_STDERR)
    assert "hunter2secret99" not in mva.redact_secrets("api_key=abc123456789\n")
    assert "tok_abcdef123456" not in mva.redact_secrets("Bearer tok_abcdef123456")
    assert "正常文本" == mva.redact_secrets("正常文本")
