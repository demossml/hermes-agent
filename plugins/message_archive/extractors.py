"""
Media extractors for message_archive plugin.

extract_photo      — vision_analyze_tool (async), never raises
extract_audio      — transcribe_audio (async), never raises
extract_document   — docx/xlsx/pdf text extraction, fallback marker
classify_extension — maps extension → document_pdf|docx|xlsx|other
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)


def classify_extension(file_path: str) -> str:
    """Map file extension to msg_type."""
    ext = Path(file_path).suffix.lower().lstrip(".")
    mapping = {
        "pdf": "document_pdf",
        "docx": "document_docx",
        "doc": "document_docx",
        "xlsx": "document_xlsx",
        "xls": "document_xlsx",
        "pptx": "document_other",
        "txt": "document_other",
        "csv": "document_other",
        "jpg": "photo",
        "jpeg": "photo",
        "png": "photo",
        "gif": "photo",
        "webp": "photo",
        "heic": "photo",
        "mp3": "audio",
        "ogg": "audio",
        "wav": "audio",
        "m4a": "audio",
        "mp4": "video",
        "mov": "video",
        "webm": "video",
    }
    return mapping.get(ext, "document_other")


async def extract_photo(file_path: str) -> Tuple[str, str]:
    """Extract text from photo via vision_analyze_tool.

    Returns (extracted_text, extractor_name).
    Never raises — returns ("", "error:...") on failure.
    """
    if not file_path or not os.path.exists(file_path):
        return ("", f"error: file not found: {file_path}")
    try:
        from tools.vision_tools import vision_analyze_tool
        import json

        result = vision_analyze_tool(
            image_path=file_path,
            prompt="Describe this image in detail. Extract any visible text.",
        )
        data = json.loads(result) if isinstance(result, str) else result
        text = data.get("description", "") or data.get("text", "") or ""
        return (text[:2000], "vision_analyze_tool")
    except Exception as e:
        logger.debug("extract_photo failed for %s: %s", file_path, e)
        return ("", f"error: {e}")


async def extract_audio(file_path: str) -> Tuple[str, str]:
    """Transcribe audio via transcription_tools.

    Returns (transcript, extractor_name).
    Never raises — returns ("", "error:...") on failure.
    """
    if not file_path or not os.path.exists(file_path):
        return ("", f"error: file not found: {file_path}")
    try:
        from tools.transcription_tools import transcribe_audio
        import json

        result = transcribe_audio(file_path=file_path)
        data = json.loads(result) if isinstance(result, str) else result
        text = data.get("text", "") or data.get("transcript", "") or ""
        return (text[:2000], "transcribe_audio")
    except Exception as e:
        logger.debug("extract_audio failed for %s: %s", file_path, e)
        return ("", f"error: {e}")


async def extract_document(file_path: str) -> Tuple[str, str]:
    """Extract text from document (PDF, DOCX, XLSX).

    Uses pymupdf for PDF, read_extract for DOCX/XLSX.
    Returns (extracted_text, extractor_name).
    Never raises — returns ("", "error:...") on failure.
    """
    if not file_path or not os.path.exists(file_path):
        return ("", f"error: file not found: {file_path}")

    msg_type = classify_extension(file_path)
    text = ""
    extractor = ""

    try:
        if msg_type == "document_pdf":
            try:
                import fitz  # pymupdf
                doc = fitz.open(file_path)
                parts = []
                for page in doc:
                    parts.append(page.get_text())
                doc.close()
                text = "\n".join(parts)
                extractor = "pymupdf"
            except ImportError:
                text = "[pdf: pymupdf not installed]"
                extractor = "marker-pdf"
            except Exception as e:
                text = f"[pdf: extraction failed: {e}]"
                extractor = "error"

        elif msg_type in ("document_docx", "document_xlsx"):
            try:
                from tools.read_extract import read_extract
                text = read_extract(file_path)
                extractor = "read_extract"
            except Exception:
                text = f"[{msg_type}: extraction failed]"
                extractor = "error"

        else:
            text = f"[{msg_type}: no extractor]"
            extractor = "none"

    except Exception as e:
        logger.debug("extract_document failed for %s: %s", file_path, e)
        return ("", f"error: {e}")

    return (text[:2000], extractor)


# ── Document categorization ──────────────────────────────────

CATEGORIES: dict[str, list[str]] = {
    "receipt":  ["чек", "кассовый", "фискальный", "итого", "сдача", "кассир",
                 "приход", "касса", "терминал"],
    "invoice":  ["счёт", "инвойс", "счет-фактура", "счёт-фактура",
                 "на оплату", "к оплате"],
    "contract": ["договор", "контракт", "соглашение", "доп соглашение",
                 "дополнительное соглашение"],
    "act":      ["акт", "приёма", "передачи", "выполненных", "оказанных",
                 "сверки", "приема-передачи", "акт сдачи"],
    "waybill":  ["накладная", "торг-12", "торг12", "транспортная",
                 "товарная", "ттн", "товарно-транспортная"],
    "payment":  ["платёжное", "поручение", "квитанция", "оплата", "платеж",
                 "п/п", "платёжка"],
}


def categorize_document(text: str = "", file_name: str = "") -> str:
    """Return category based on document text and/or file name.

    Returns one of: receipt, invoice, contract, act, waybill, payment,
    or empty string if no category matched.
    """
    combined = ((text or "") + " " + (file_name or "")).lower()
    if not combined.strip():
        return ""
    for category, keywords in CATEGORIES.items():
        for kw in keywords:
            if kw in combined:
                return category
    return ""


def parse_receipt(text: str) -> dict:
    """Extract structured fields from receipt OCR text.

    Returns dict with: store, inn, total, date.
    Empty dict if nothing found.
    """
    import re
    result: dict = {}

    # Store name — usually after "ООО"/"ИП"/"ЗАО"/"АО"
    store_m = re.search(r'(?:ООО|ИП|ЗАО|АО)\s*[«"](.+?)[»"]', text)
    if store_m:
        result["store"] = store_m.group(0)

    # INN: 10 or 12 digits
    inn_m = re.search(r'ИНН\s*[:\s]*(\d{10,12})', text)
    if inn_m:
        result["inn"] = inn_m.group(1)

    # Total
    total_m = re.search(
        r'(?:ИТОГО?|ИТОГ|ВСЕГО|СУММА|К\s*ОПЛАТЕ)[:\s]*([\d\s]+[.,]\d{2})',
        text, re.IGNORECASE,
    )
    if total_m:
        try:
            result["total"] = float(
                total_m.group(1).replace(' ', '').replace(',', '.')
            )
        except ValueError:
            pass

    # Date (DD.MM.YYYY or DD/MM/YYYY)
    date_m = re.search(
        r'(\d{2}[./-]\d{2}[./-](?:20)?\d{2})',
        text,
    )
    if date_m:
        result["date"] = date_m.group(1)

    return result
