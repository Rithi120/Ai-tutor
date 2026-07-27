"""Secure temporary flashcard-import storage and deterministic extraction."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from werkzeug.utils import secure_filename

from learnova.ocr.service import detected_mime_type


EXTENSION_MIME = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
ACTIVE_STATUSES = {
    "uploaded", "extracting", "extracted", "ready_for_review", "generating", "generated",
}


@dataclass
class ImportProblem(ValueError):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass
class ValidatedUpload:
    data: bytes
    original_filename: str
    sanitized_filename: str
    mime_type: str
    source_type: str
    page_count: int
    sha256: str


def validate_upload(
    data: bytes,
    filename: str,
    declared_mime: str | None,
    *,
    max_pdf_size: int,
    max_image_size: int,
    max_pdf_pages: int,
    max_image_pixels: int,
) -> ValidatedUpload:
    original = (filename or "").strip()
    safe = secure_filename(original)[:255] or "import"
    extension = Path(safe).suffix.lower()
    if not data:
        raise ImportProblem("empty_file", "Empty files cannot be uploaded.")
    expected = EXTENSION_MIME.get(extension)
    if not expected:
        raise ImportProblem("unsupported_extension", "Choose a PDF, JPG, JPEG, PNG, or WebP file.")
    detected = detected_mime_type(data)
    if detected not in set(EXTENSION_MIME.values()):
        raise ImportProblem("unsupported_file", "The file content is not a supported PDF or image.")
    if expected != detected:
        raise ImportProblem("type_mismatch", "The filename extension does not match the file content.")
    if declared_mime and declared_mime not in {detected, "application/octet-stream"}:
        raise ImportProblem("type_mismatch", "The browser file type does not match the file content.")

    page_count = 1
    source_type = "pdf" if detected == "application/pdf" else "image"
    size_limit = max_pdf_size if source_type == "pdf" else max_image_size
    if len(data) > size_limit:
        raise ImportProblem("file_too_large", "The selected file exceeds the configured size limit.")
    if source_type == "pdf":
        try:
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted:
                try:
                    unlocked = reader.decrypt("")
                except Exception:
                    unlocked = 0
                if not unlocked:
                    raise ImportProblem("pdf_password_protected", "Password-protected PDFs are not supported.")
            page_count = len(reader.pages)
            if page_count < 1:
                raise ImportProblem("pdf_corrupt", "The PDF has no readable pages.")
            if page_count > max_pdf_pages:
                raise ImportProblem("too_many_pages", "The PDF has too many pages.")
            # Force page-tree parsing while the upload is still in memory.
            _ = reader.pages[page_count - 1]
        except ImportProblem:
            raise
        except Exception as error:
            raise ImportProblem("pdf_corrupt", "The PDF is damaged or cannot be read.") from error
    else:
        try:
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                if width < 1 or height < 1:
                    raise ImportProblem("image_corrupt", "The image is damaged or cannot be read.")
                if width * height > max_image_pixels:
                    raise ImportProblem("image_too_large", "The image dimensions exceed the safety limit.")
                image.verify()
        except ImportProblem:
            raise
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
            raise ImportProblem("image_corrupt", "The image is damaged or cannot be read.") from error
    return ValidatedUpload(
        data=data, original_filename=original[:255], sanitized_filename=safe,
        mime_type=detected, source_type=source_type, page_count=page_count,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def private_storage_key(owner_id: int, document_id: str, suffix: str) -> str:
    token = uuid.uuid4().hex
    return f"{owner_id}/{document_id}/{token}{suffix}"


def store_private(root: str | Path, storage_key: str, data: bytes) -> None:
    base = Path(root).resolve()
    target = (base / storage_key).resolve()
    if base not in target.parents:
        raise ValueError("Unsafe storage target")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def read_private(root: str | Path, storage_key: str) -> bytes:
    base = Path(root).resolve()
    target = (base / storage_key).resolve()
    if base not in target.parents:
        raise ValueError("Unsafe storage target")
    return target.read_bytes()


def delete_private(root: str | Path, storage_key: str) -> None:
    base = Path(root).resolve()
    target = (base / storage_key).resolve()
    if base not in target.parents:
        return
    if target.is_file():
        target.unlink()
    parent = target.parent
    if parent != base and parent.exists():
        shutil.rmtree(parent, ignore_errors=True)


def extract_pdf_pages(data: bytes) -> list[dict[str, Any]]:
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ImportProblem("pdf_password_protected", "Password-protected PDFs are not supported.")
        pages = []
        for number, page in enumerate(reader.pages, start=1):
            warnings: list[str] = []
            try:
                text = (page.extract_text() or "").strip()
                count = len(text)
                if count == 0:
                    status, confidence = "needs_ocr", 0.0
                    warnings.append("No native text was found; this page may be scanned.")
                elif count < 40:
                    status, confidence = "low_text", 0.35
                    warnings.append("Very little native text was found; review or run OCR.")
                else:
                    status, confidence = "success", 0.8
                pages.append({
                    "page_number": number, "text": text, "native_text": text,
                    "ocr_text": "", "character_count": count, "status": status,
                    "confidence": confidence, "warnings": warnings, "source": "native",
                    "selected": bool(text),
                })
            except Exception:
                pages.append({
                    "page_number": number, "text": "", "native_text": "", "ocr_text": "",
                    "character_count": 0, "status": "failed", "confidence": 0.0,
                    "warnings": ["Native text extraction failed for this page."],
                    "source": "native", "selected": False,
                })
        return pages
    except ImportProblem:
        raise
    except Exception as error:
        raise ImportProblem("pdf_corrupt", "The PDF is damaged or cannot be read.") from error


def reviewed_text(pages: list[dict[str, Any]]) -> tuple[str, list[int]]:
    selected = [page for page in pages if page.get("selected")]
    page_numbers = [int(page["page_number"]) for page in selected]
    text = "\n\n".join(
        f"[Page {page['page_number']}]\n{str(page.get('text') or '').strip()}"
        for page in selected if str(page.get("text") or "").strip()
    )
    return text.strip(), page_numbers


def json_list(value: str | None) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []
