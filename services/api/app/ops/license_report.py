"""M7-04 License report：依赖、模型、内容源与派生对象的授权清单。

四区段语义（验收：输出依赖、模型、内容源和派生对象的授权清单）：
- dependencies：API requirements 声明的包经 importlib.metadata 读运行环境的
  真实 version/license 元数据；元数据缺失一律 UNKNOWN——不猜测不虚报。
  web 侧读 package.json 依赖声明 + node_modules 实际安装的 license
  （node_modules 缺失时如实标注未核实，不冒充已核实）。
- models：Settings 槽位的选型记录（None -> not_configured）；本地实现
  （fake-asr/tone-tts/keyword judge）为内置零依赖实现，无外部模型授权。
- content_sources / derived_objects：sources 全表 license_state、resources
  授权快照、课程导入草稿准入快照——直接来自数据库，可溯源到来源记录。
清单是运行时聚合：依赖来自环境、内容来自库，不缓存不虚构。
"""
from __future__ import annotations

import json
import re
from importlib import metadata as importlib_metadata
from pathlib import Path

UNKNOWN = "UNKNOWN"
_NOT_CONFIGURED = "not_configured"

# requirements 中的包名 -> importlib 元数据常用名差异兜底
_NAME_ALIASES = {"python-multipart": "python_multipart", "livekit-api": "livekit-api"}


def parse_requirements(text: str) -> list[str]:
    """从 requirements 文本提取包名（去注释/空行/选项行，归一小写连字符）。"""
    names: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "--")):
            continue
        name = re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].strip()
        if name:
            names.append(name.lower().replace("_", "-"))
    return names


def _dist_license(dist) -> str:
    meta = dist.metadata
    lic = (meta.get("License") or "").strip()
    if lic:
        return lic.splitlines()[0].strip()
    for cls in meta.get_all("Classifier") or []:
        if cls.startswith("License ::"):
            return cls.split("::")[-1].strip()
    return UNKNOWN


def license_of(name: str) -> tuple[str | None, str | None]:
    """(version, license)；未安装返回 (None, None)——调用方如实标注。"""
    for candidate in (name, name.replace("-", "_")):
        try:
            dist = importlib_metadata.distribution(candidate)
        except importlib_metadata.PackageNotFoundError:
            continue
        return dist.version, _dist_license(dist)
    return None, None


def collect_api_dependencies(requirements_text: str) -> list[dict]:
    out: list[dict] = []
    for name in parse_requirements(requirements_text):
        version, lic = license_of(name)
        installed = version is not None
        out.append({
            "name": name,
            "version": version,
            "license": (lic or UNKNOWN) if installed else UNKNOWN,
            "installed": installed,
        })
    return out


def collect_web_dependencies(web_dir: Path | None = None) -> dict:
    """web 侧依赖清单：package.json 声明 + node_modules 实测 license。"""
    if web_dir is None:
        # npm workspace：manifest 在 apps/web，依赖 hoist 到仓库根 node_modules
        web_dir = Path(__file__).resolve().parents[4] / "apps" / "web"
    pkg_json = web_dir / "package.json"
    if not pkg_json.exists():
        return {"status": "unavailable", "note": f"web 目录不可用: {web_dir}", "packages": []}
    manifest = json.loads(pkg_json.read_text(encoding="utf-8"))
    declared: dict[str, str] = {}
    for section in ("dependencies", "devDependencies"):
        declared.update(manifest.get(section) or {})
    # workspace 布局下依赖可能 hoist 到上级目录的 node_modules
    node_modules = web_dir / "node_modules"
    if not node_modules.exists():
        node_modules = web_dir.parents[1] / "node_modules"
    packages: list[dict] = []
    for name in sorted(declared):
        meta_path = node_modules / name / "package.json"
        lic = UNKNOWN
        installed = meta_path.exists()
        if installed:
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                lic = str(meta.get("license") or UNKNOWN) or UNKNOWN
            except (json.JSONDecodeError, OSError):
                lic = UNKNOWN
        packages.append({
            "name": name,
            "declared_range": declared[name],
            "license": lic,
            "installed": installed,
        })
    note = None if node_modules.exists() else "node_modules 未安装，license 未核实"
    return {"status": "ok", "note": note, "packages": packages}


LOCAL_MODEL_NOTE = {
    "asr": "fake-asr：内置零依赖实现（显式 [fake] 标记，不冒充真实 ASR）",
    "tts": "tone-tts：stdlib wave 合成，内置零依赖实现",
    "rubric": "keyword：内置确定性判分 judge，无外部模型",
}


def collect_model_slots(settings) -> list[dict]:
    """模型选型记录：槽位 -> 配置值或 not_configured（不虚报部署了什么）。"""
    rows = [
        ("llm", settings.llm_provider),
        ("embedding", settings.embedding_provider),
        ("asr_cloud_endpoint", settings.asr_cloud_endpoint),
        ("asr_cloud_model", settings.asr_cloud_model if settings.asr_cloud_endpoint else None),
        ("tts_cloud_endpoint", settings.tts_cloud_endpoint),
        ("tts_cloud_model", settings.tts_cloud_model if settings.tts_cloud_endpoint else None),
        ("rubric_judge", settings.rubric_judge or None),
    ]
    return [{"slot": slot, "value": value or _NOT_CONFIGURED} for slot, value in rows]


def build_license_report(
    *,
    dependencies: list[dict],
    web: dict,
    model_slots: list[dict],
    content_sources: list[dict],
    resources: list[dict],
    course_import_drafts: list[dict],
) -> dict:
    return {
        "dependencies": {"api": dependencies, "web": web},
        "models": model_slots,
        "content_sources": content_sources,
        "derived_objects": {
            "resources": resources,
            "course_import_drafts": course_import_drafts,
        },
        "note": "清单来自运行环境与数据库实时读取；UNKNOWN/not_configured 表示未声明或未配置，不虚报",
    }


def source_view(record) -> dict:
    return {
        "id": record.id,
        "name": record.name,
        "source_type": record.source_type,
        "license_state": record.license_state.value,
    }


def resource_view(record) -> dict:
    return {
        "id": record.id,
        "title": record.title,
        "source_id": record.source_id,
        "license_state": record.license_state.value,
        "access_state": record.access_state,
    }
