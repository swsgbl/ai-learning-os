"""M5-05 Course importer：从授权资源生成 Course/Concept/Resource 草稿并进入人工审核。

- 授权门禁（不虚报，ADR 45）：资源的 license_state 快照经 REUSE_ADMISSION 映射，
  NOT_ADMISSIBLE（UNKNOWN/ACCESS_CONTROLLED/ALL_RIGHTS_RESERVED/PROHIBITED）一律拒绝，
  拒绝原因可见；license_state 非法值同样拒绝——门禁只认记录快照，不接受调用方声明；
- 概念提取（确定性规则，规则可见）：「X 的定义/性质/运算/公式/定理」「名词解释：X」两种模式，
  去重保序，上限 MAX_CONCEPTS 截断；无命中返回空列表（不虚报概念）；
- 草稿 = Course 元数据 + Concept 候选 + Resource 引用，状态 pending_review，
  只能经人工 approve/reject 离开审核队列。
"""
from __future__ import annotations

import re

from app.domain.license import REUSE_ADMISSION, LicenseState, ReuseAdmission

MAX_CONCEPTS = 50
MAX_CONCEPT_LEN = 40
MIN_CONCEPT_LEN = 2

PATTERN_TAIL = re.compile(r"([一-龥]{2,20})的(?:定义|性质|运算|公式|定理)")
PATTERN_TERM = re.compile(r"名词解释[:：]\s*([一-龥A-Za-z0-9]{2,20})")


class NotAdmissible(Exception):
    """资源授权状态不可复用（NOT_ADMISSIBLE 或非法值），拒绝原因见 message。"""


def extract_concepts(texts: list[str]) -> list[str]:
    """从 chunk 文本提取候选概念名：两种确定性模式，去重保序，上限截断。"""
    names: list[str] = []
    for text in texts:
        for pattern in (PATTERN_TAIL, PATTERN_TERM):
            for match in pattern.finditer(text or ""):
                name = match.group(1)
                if MIN_CONCEPT_LEN <= len(name) <= MAX_CONCEPT_LEN and name not in names:
                    names.append(name)
    return names[:MAX_CONCEPTS]


def build_import_draft(
    resource_id: str,
    title: str,
    license_state: str,
    chunk_texts: list[str],
) -> dict:
    """授权门禁 + 生成草稿 dict（Course 元数据 + Concept 候选 + Resource 引用）。

    不可复用资源 raise NotAdmissible（原因可见）；草稿概念为空时
    extraction_note 明示"未识别出候选概念"（不虚报）。
    """
    try:
        state = LicenseState(license_state)
    except ValueError as cause:
        raise NotAdmissible(f"资源 license_state 非法: {license_state}（拒绝导入）") from cause
    admission = REUSE_ADMISSION[state]
    if admission is ReuseAdmission.NOT_ADMISSIBLE:
        raise NotAdmissible(
            f"资源授权状态 {license_state} 不可复用（NOT_ADMISSIBLE），拒绝生成草稿"
        )

    concepts = extract_concepts(chunk_texts)
    if concepts:
        note = f"识别出 {len(concepts)} 个候选概念"
    else:
        note = "未识别出候选概念"
    return {
        "title": title,
        "status": "pending_review",
        "source_resource_id": resource_id,
        "source_license_state": license_state,
        "reuse_admission": admission.value,
        "concepts": concepts,
        "resource_refs": [resource_id],
        "extraction_note": note,
    }
