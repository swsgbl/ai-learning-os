"""M19-001 OCR 后备（审计升级包 Prompt 4：扫描页文本化）。

设计边界（审计 02 §4.3）：
- RapidOCR（onnxruntime 版 PaddleOCR 模型，Apache-2.0）——轻量 CPU
  推理（无 paddle 依赖），中文实测逐字准确（求根公式句样例）；
- 仅对**低文本页**（扫描形态）识别——文本层页永不走 OCR（省算力
  且不引入噪声）；
- 页均置信度（bbox 置信均值）随页返回：低于阈值的页上层标记人工
  复核（审计：低置信不自动驱动题目生成——解析完成但限制在上层）；
- 逐页串行 + 页数上限（防大书 OCR 风暴——超限如实截断报告）；
- 延迟导入：rapidocr 未装的部署 ParserUnavailable 语义。
"""
from __future__ import annotations

from dataclasses import dataclass

#: 页均 OCR 置信低于该值 → 上层标 low_ocr_confidence（人工复核信号）
OCR_CONFIDENCE_THRESHOLD = 0.80

#: 单次调用最大 OCR 页数（超限截断——防长书风暴；上层可分批重跑）
DEFAULT_MAX_OCR_PAGES = 50

#: 渲染 DPI（150 兼顾速度与识别率——200 实测准确但慢 ~2x）
DEFAULT_DPI = 150


class OcrUnavailable(RuntimeError):
    """OCR 依赖未安装或输入非法。"""


@dataclass(frozen=True, slots=True)
class OcrPage:
    """一页 OCR 结果（页码 1-based；置信为该页 bbox 置信均值）。"""

    page: int
    text: str
    mean_confidence: float
    line_count: int


def ocr_pages(
    data: bytes,
    page_numbers: list[int],
    *,
    dpi: int = DEFAULT_DPI,
    max_pages: int = DEFAULT_MAX_OCR_PAGES,
) -> list[OcrPage]:
    """对指定页（1-based）渲染→OCR（串行；超 max_pages 如实截断）。

    失败页（渲染/识别异常）跳过并继续——返回成功页结果；上层以
    「请求数 vs 返回数」判断缺页。
    """
    try:
        import numpy as np
        import pymupdf
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as cause:  # pragma: no cover - 部署差异
        raise OcrUnavailable(f"OCR 依赖未安装：{cause}") from cause

    engine = RapidOCR()
    results: list[OcrPage] = []
    for page_number in page_numbers[:max_pages]:
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
            if page_number < 1 or page_number > doc.page_count:
                doc.close()
                continue
            pix = doc[page_number - 1].get_pixmap(dpi=dpi)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
            doc.close()
            lines, _ = engine(img)
        except (ValueError, RuntimeError, OSError):
            # 单页失败不炸整批（渲染/识别异常如实跳过——返回数缺页即信号）
            continue
        if not lines:
            results.append(OcrPage(page=page_number, text="", mean_confidence=0.0, line_count=0))
            continue
        texts = [str(line[1]) for line in lines]
        confs = [float(line[2]) for line in lines if len(line) > 2]
        mean_conf = sum(confs) / len(confs) if confs else 0.0
        results.append(
            OcrPage(
                page=page_number,
                text="\n".join(texts),
                mean_confidence=round(mean_conf, 3),
                line_count=len(texts),
            )
        )
    return results

