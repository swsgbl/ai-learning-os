#!/usr/bin/env python3
"""M15-012 国内下载通道同步：GitHub Release 资产 → VPS ndtool.cn 静态目录。

发布后本地一键执行（在仓库根）：
    .venv/Scripts/python.exe tools/android_release/publish_to_ndtool.py --tag v0.1.3

步骤（fail-closed，任一步失败即中止）：
1. gh 下载该 tag 的 APK + manifest.json 到临时目录；
2. 本地重算 sha256 与 manifest 比对（不一致即失败——绝不上传未校验资产）；
3. scp 到 VPS /var/www/aios-downloads/（M14-176 既定公共下载面：/android/ 前缀，同目录更新 manifest.json）；
4. SSH 端复算 sha256（传输完整性）；
5. 提示：更新 infra/env.production-recovery 的 AIOS_LATEST_ANDROID_* 并重启 api
   （本脚本不自动改 env——版本切换是运维决策，含 rollback 语义）。

依赖：gh 已登录、ssh root@113.45.64.145 免密可达。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = "swsgbl/ai-learning-os"
VPS = "root@113.45.64.145"
REMOTE_DIR = "/var/www/aios-downloads/android"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if proc.returncode != 0:
        sys.exit(f"[FAIL] {' '.join(cmd)}\n{proc.stderr or proc.stdout}")
    return proc


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True, help="GitHub Release tag（如 v0.1.3）")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        print(f"[1/4] gh 下载 {args.tag} 资产 …")
        run(["gh", "release", "download", args.tag, "-R", REPO, "-p", "*.apk", "-p", "manifest.json",
             "-D", str(tmp_dir)])

        manifest = json.loads((tmp_dir / "manifest.json").read_text(encoding="utf-8"))
        android = manifest["channels"]["android"]
        apk_name = android["files"][0]["name"]
        apk = tmp_dir / apk_name

        print("[2/4] sha256 校验（本地 vs manifest）…")
        local = sha256_of(apk)
        if local != android["files"][0]["sha256"]:
            sys.exit(f"[FAIL] sha256 不一致：local={local} manifest={android['files'][0]['sha256']}")
        print(f"      OK {local[:16]}…  ({apk.stat().st_size} bytes)")

        print(f"[3/4] scp → {VPS}:{REMOTE_DIR}/ …")
        run(["ssh", "-o", "ConnectTimeout=10", VPS, f"mkdir -p {REMOTE_DIR}"])
        # APK 进 android/ 子目录；manifest 在仓库根（/aios/download-manifest.json 语义）
        run(["scp", "-o", "ConnectTimeout=15", str(apk), f"{VPS}:{REMOTE_DIR}/"])
        run(["scp", "-o", "ConnectTimeout=15", str(tmp_dir / "manifest.json"), f"{VPS}:/var/www/aios-downloads/"])

        print("[4/4] VPS 端 sha256 复算 …")
        proc = run(["ssh", "-o", "ConnectTimeout=10", VPS, f"sha256sum {REMOTE_DIR}/{apk_name}"])
        remote = proc.stdout.split()[0]
        if remote != local:
            sys.exit(f"[FAIL] VPS sha256 不一致：{remote}")
        print(f"      OK {remote[:16]}…")

    print(
        f"\n[DONE] {apk_name} 已上线 https://ndtool.cn/downloads/android/{apk_name}\n"
        f"下一步（手动）：infra/env.production-recovery 更新 "
        f"AIOS_LATEST_ANDROID_VERSION/_CODE/_DOWNLOAD_URL 后\n"
        f"  docker compose -f infra/docker-compose.yml -p aios-m14-03-production-rehearsal "
        f"--env-file infra/env.production-recovery up -d api"
    )


if __name__ == "__main__":
    main()
