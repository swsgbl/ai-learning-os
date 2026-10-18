#!/usr/bin/env python
"""M15-006 CI 发布流水线的 download manifest 生成器（无第三方依赖）。

输入已构建的签名 APK，从坏境读真实值（aapt2 badging 的包名/versionCode/
versionName、文件 sha256/size），生成 schema ``aios-download-manifest/1``
的 android 频道清单（与 ``infra/edge/download-manifest.example.json`` 契约
一致），供 ``verify_artifact.py`` 门禁与 GitHub Release 附件使用。

诚实边界：
- ``signed: true`` 只在调用方（CI）已完成签名构建的前提下书写——本脚本
  不验签（那是 verify_artifact 的职责），只如实转写 APK 事实；
- sha256/size 由本脚本当场重算（不信任任何外部传入值）；
- 失败即非零退出（fail-closed），绝不输出半成品清单。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "aios-download-manifest/1"
MIN_API_LEVEL = 26
SIGNATURE_SCHEME = "v2+v3"


def find_aapt2(android_home: str | None) -> str:
    """定位 aapt2：显式 --aapt2 > ANDROID_HOME/build-tools 最新版；找不到 fail-closed。"""
    roots = []
    if android_home:
        roots.append(Path(android_home))
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = __import__("os").environ.get(env)
        if value:
            roots.append(Path(value))
    found: list[Path] = []
    for root in roots:
        found.extend(root.glob("build-tools/*/aapt2"))
    if not found:
        raise SystemExit("aapt2 not found (set --android-home or ANDROID_HOME)")
    return str(sorted(found)[-1])


def dump_badging(aapt2: str, apk: Path) -> dict[str, str]:
    out = subprocess.run(
        [aapt2, "dump", "badging", str(apk)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    info: dict[str, str] = {}
    for line in out.splitlines():
        if line.startswith("package:"):
            for part in line.split():
                if part.startswith("name="):
                    info["package"] = part.split("=", 1)[1].strip("'")
                elif part.startswith("versionCode="):
                    info["versionCode"] = part.split("=", 1)[1].strip("'")
                elif part.startswith("versionName="):
                    info["versionName"] = part.split("=", 1)[1].strip("'")
            break
    missing = {"package", "versionCode", "versionName"} - info.keys()
    if missing:
        raise SystemExit(f"aapt2 badging missing fields: {sorted(missing)}")
    return info


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--alias", required=True, help="release keystore alias（不涉密）")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--aapt2", default=None)
    parser.add_argument("--android-home", default=None)
    args = parser.parse_args(argv)

    if not args.apk.is_file():
        raise SystemExit(f"apk not found: {args.apk}")

    aapt2 = args.aapt2 or find_aapt2(args.android_home)
    info = dump_badging(aapt2, args.apk)
    size = args.apk.stat().st_size
    digest = sha256_of(args.apk)
    asset_name = f"ai-learning-os-{info['versionName']}-release-signed.apk"

    manifest = {
        "schema": SCHEMA,
        "version": info["versionName"],
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "channels": {
            "android": {
                "package_name": info["package"],
                "versionCode": int(info["versionCode"]),
                "versionName": info["versionName"],
                "min_api_level": MIN_API_LEVEL,
                "files": [
                    {
                        "name": asset_name,
                        "url": f"/android/{asset_name}",
                        "sha256": digest,
                        "size_bytes": size,
                        "signed": True,
                        "keystore_alias": args.alias,
                        "signature_scheme": SIGNATURE_SCHEME,
                    }
                ],
                "pending_unsigned": [],
            }
        },
    }

    args.out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"manifest written: {args.out} (package={info['package']} "
          f"versionCode={info['versionCode']} versionName={info['versionName']})")
    print(f"sha256={digest} size={size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
