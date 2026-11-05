"""Transactional project-upload ingestion.

The web layer validates request fields and builds the upload list. This service
owns file validation, de-duplication, PDF expansion, image checks, and the
database rows of one upload.

One damaged file used to discard the whole upload (DR-01). With `partial=True` every
file is judged on its own: everything that can fail (validation, the PDF inspection, the
page limit, the picture check, and - only with `preprocess` - rendering) happens before
any row of that file is written, so a failure leaves no half-made rows, the valid files
are kept, and the failed file is stored as a document row with no pages (its bytes kept
for a retry) plus a translatable, file-named error in analysis_json["file_errors"].
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from werkzeug.utils import secure_filename

from learnova.config import DEFAULT_PROJECT_MAX_PAGES
from learnova.ocr.service import (
    IMAGE_DAMAGED_MSGID, MAX_FILE_BYTES, PASSWORD_PDF_MSGID, UNREADABLE_PDF_MSGID, DocumentUploadError,
    detected_mime_type, has_usable_text_layer, inspect_pdf, pdf_page_pictures, preprocess_document_image,
    probe_image, render_pdf_page, validate_document_upload,
)

# Said only where nothing is saved (partial=False: the old all-or-nothing callers).
UNREADABLE_PDF_NOTHING_SAVED_MSGID = ("“{filename}” could not be read as a PDF. Nothing was saved – export it "
                                      "again or upload photos of its pages.")
# The camera scanner's per-photo metadata that the first render applies (DR-08).
UPLOAD_TRANSFORM_KEYS = ("rotation", "crop", "deskew", "autocrop", "mode")
# Failures a retry of the same stored bytes can never fix: no "Try this file again" for them.
NO_RETRY_CODES = frozenset({"too_large", "empty", "not_supported"})


class ProjectPageLimitError(ValueError):
    """An upload would take a project over its page limit.

    The whole upload is still rolled back, but the student is told which file crossed
    the limit: the web layer translates `msgid` with `params` (tr(error.msgid,
    **error.params)); str(error) is the English sentence for logs and old callers.
    """

    msgid = ("“{filename}” would take this project over {limit} pages. "
             "Split the material into two projects or remove some pages.")
    code = "page_limit"

    def __init__(self, filename: str, limit: int):
        self.filename = filename
        self.limit = limit
        super().__init__(self.msgid.format(filename=filename, limit=limit))

    @property
    def params(self) -> dict[str, Any]:
        return {"filename": self.filename, "limit": self.limit}


class NoNewPagesError(ValueError):
    """Nothing in the upload became a page: every file was empty or already there.

    Carries the duplicates so even this case is not silent, and, like
    ProjectPageLimitError, a translatable `msgid` (tr(error.msgid, **error.params)) that
    says which of the two happened; str(error) keeps the old English text for old callers.
    """

    def __init__(self, skipped_duplicates: Iterable[str] = ()):
        self.skipped_duplicates = tuple(skipped_duplicates)
        super().__init__("No unique supported pages were uploaded")

    @property
    def msgid(self) -> str:
        if self.skipped_duplicates:
            return "Every file you chose is already in this project, so no pages were added."
        return "These files contain no page Learnova can add."

    @property
    def params(self) -> dict[str, Any]:
        return {}


@dataclass
class FailedFile:
    """One file of an upload that could not be used, said so the student knows which one.

    `msgid` names the file ({filename}); the web layer shows tr(msgid, **params). `file_id`
    is the stored 0-page ProjectFile row (None when nothing was stored: partial=False or
    record_failures=False)."""

    filename: str
    code: str        # unreadable_pdf | password | empty | type_mismatch | not_supported | too_large | image_damaged | page_limit
    msgid: str
    params: dict[str, Any]
    file_id: int | None = None

    @property
    def retryable(self) -> bool:
        return self.code not in NO_RETRY_CODES


class NothingUsableError(ValueError):
    """No file of a new project could be used: nothing is saved, and each file is named.

    `failed_files` carries one FailedFile per file (the web layer flashes tr(f.msgid,
    **f.params) for each, after this error's own msgid)."""

    msgid = "None of the files could be used, so nothing was saved."
    code = "nothing_usable"

    def __init__(self, failed_files: Iterable[FailedFile] = ()):
        self.failed_files = tuple(failed_files)
        super().__init__(self.msgid)

    @property
    def params(self) -> dict[str, Any]:
        return {}


@dataclass
class UploadResult:
    """What one upload added, and what it deliberately left out."""

    pages_added: int = 0
    files_added: int = 0
    # Files skipped because their bytes match a file already in the project or earlier
    # in the same upload, by the stored (sanitised) filename, in upload order.
    skipped_duplicates: list[str] = field(default_factory=list)
    # Files that could not be used (partial=True), in upload order.
    failed_files: list[FailedFile] = field(default_factory=list)


def create_project_with_uploads(
    database,
    project_model,
    file_model,
    page_model,
    *,
    user_id: int,
    title: str,
    subject: str,
    exam_date,
    uploads: Iterable[tuple[Any, str, dict[str, Any]]],
    max_pages: int = DEFAULT_PROJECT_MAX_PAGES,
):
    """Create the project and its pages in one transaction; returns (project, UploadResult)."""

    project = project_model(
        user_id=user_id,
        title=title,
        subject=subject,
        exam_date=exam_date,
        status="uploaded",
    )
    database.session.add(project)
    database.session.flush()
    result = ingest_uploads(database, project, file_model, page_model, uploads=uploads,
                            max_pages=max_pages, commit=False)
    database.session.commit()
    return project, result


def create_project_from_uploads(
    database,
    project_model,
    file_model,
    page_model,
    *,
    user_id: int,
    title: str,
    subject: str,
    exam_date,
    uploads: Iterable[tuple[Any, str, dict[str, Any]]],
    max_pages: int = DEFAULT_PROJECT_MAX_PAGES,
):
    """create_project_with_uploads for callers that only need the project."""

    project, _result = create_project_with_uploads(
        database, project_model, file_model, page_model, user_id=user_id, title=title,
        subject=subject, exam_date=exam_date, uploads=uploads, max_pages=max_pages)
    return project


def add_uploads_to_project(database, project, file_model, page_model, *, uploads,
                           max_pages: int = DEFAULT_PROJECT_MAX_PAGES, commit: bool = True) -> int:
    """ingest_uploads for callers that only need the number of pages added."""

    return ingest_uploads(database, project, file_model, page_model, uploads=uploads,
                          max_pages=max_pages, commit=commit).pages_added


def ingest_uploads(database, project, file_model, page_model, *, uploads,
                   max_pages: int = DEFAULT_PROJECT_MAX_PAGES, commit: bool = True, partial: bool = False,
                   record_failures: bool = True, preprocess: bool = False) -> UploadResult:
    """Add uploaded files to a project as new pages after the existing ones.

    Files already in the project (same sha256) are skipped, so re-uploading a photo never
    duplicates a page; their names are returned in `skipped_duplicates` so the student can
    be told. `max_pages` is app.config["PROJECT_MAX_PAGES"].

    partial=False (the default, for old callers): all or nothing. A file that cannot be
    used raises (DocumentUploadError or another ValueError, ProjectPageLimitError naming
    the file) and the caller rolls back; NoNewPagesError when nothing new was uploaded.

    partial=True: each file on its own. A file that cannot be used becomes a FailedFile in
    `failed_files` and - with `record_failures` - a stored ProjectFile with no pages (its
    bytes kept for a retry, sha256 "" so it never counts as "already in this project")
    plus analysis_json["file_errors"][file id]. A file whose bytes match an earlier failed
    row of this project is that row again: read now, the old row is removed; failing
    again, the old row's error is updated (no second row). NoNewPagesError only when no
    page was added and nothing failed. Database errors propagate unchanged.

    preprocess=False (DR-08): no page is rendered or preprocessed here. PDF pages keep
    their text layer and their damage marks (recognition_json pdf_damaged/pdf_repaired);
    photos keep the camera scanner's transform (recognition_json upload_transform). The
    picture is made when a page is first read or shown (app.ensure_processed_page_image).
    preprocess=True (PROJECT_PREPROCESS_ON_UPLOAD) renders at upload as before."""

    result = UploadResult()
    existing_pages = len(project.pages)
    page_order = existing_pages + 1
    seen_hashes: set[str] = {item.sha256 for item in project.files if item.sha256}
    file_errors = _file_errors(project) if partial else {}
    failed_rows = {str(entry.get("sha256")): file_id for file_id, entry in file_errors.items()
                   if isinstance(entry, dict) and entry.get("sha256")}
    errors_changed = False
    filename = ""
    for upload, source_kind, transform in uploads:
        data = upload.read()
        filename = secure_filename(upload.filename or "material")[:255] or "material"
        digest = hashlib.sha256(data).hexdigest()
        transform = transform if isinstance(transform, dict) else {}
        if not partial:
            mime_type = validate_document_upload(data, filename, upload.mimetype)
            if digest in seen_hashes:
                result.skipped_duplicates.append(filename)
                continue
            seen_hashes.add(digest)
            mime_type, pages = _prepare_file(data, filename, mime_type, transform, page_order=page_order,
                                             max_pages=max_pages, preprocess=preprocess, nothing_saved=True)
            _store_file(database, project, file_model, page_model, filename=filename, mime_type=mime_type,
                        data=data, source_kind=source_kind, digest=digest, pages=pages, page_order=page_order)
            result.files_added += 1
            page_order += len(pages)
            continue
        if digest in seen_hashes:
            result.skipped_duplicates.append(filename)
            continue
        seen_hashes.add(digest)
        previous = failed_rows.get(digest)
        try:
            mime_type = validate_document_upload(data, filename, upload.mimetype)
            mime_type, pages = _prepare_file(data, filename, mime_type, transform, page_order=page_order,
                                             max_pages=max_pages, preprocess=preprocess, nothing_saved=False)
        except (DocumentUploadError, ProjectPageLimitError, ValueError) as error:
            failure = _failure(error, filename, data)
            if previous is not None:
                # The same bytes failed before: that row is this file; say the newest reason.
                failure.file_id = int(previous)
                file_errors[previous] = _error_entry(failure, digest)
                errors_changed = True
            elif record_failures:
                stored = file_model(
                    project_id=project.id, original_filename=filename,
                    mime_type=detected_mime_type(data) or "application/octet-stream",
                    original_data=data if 0 < len(data) <= MAX_FILE_BYTES else b"",
                    source_kind=source_kind, sha256="")
                database.session.add(stored)
                database.session.flush()
                failure.file_id = stored.id
                file_errors[str(stored.id)] = _error_entry(failure, digest)
                errors_changed = True
            result.failed_files.append(failure)
            continue
        _store_file(database, project, file_model, page_model, filename=filename, mime_type=mime_type,
                    data=data, source_kind=source_kind, digest=digest, pages=pages, page_order=page_order)
        result.files_added += 1
        page_order += len(pages)
        if previous is not None:
            # Read this time: the failed row of the same bytes has done its job.
            old = database.session.get(file_model, int(previous))
            if old is not None and not old.pages:
                database.session.delete(old)
            file_errors.pop(previous, None)
            errors_changed = True
    if errors_changed:
        _store_file_errors(project, file_errors)
        database.session.flush()
    if page_order == existing_pages + 1 and not result.failed_files:
        raise NoNewPagesError(result.skipped_duplicates)
    if page_order - 1 > max_pages:   # unreachable while every file is checked above
        raise ProjectPageLimitError(filename, max_pages)
    result.pages_added = page_order - 1 - existing_pages
    if commit:
        database.session.commit()
    return result


def _prepare_file(data: bytes, filename: str, mime_type: str, transform: dict[str, Any], *, page_order: int,
                  max_pages: int, preprocess: bool, nothing_saved: bool) -> tuple[str, list[dict[str, Any]]]:
    """Everything about one file that can fail, before any row of it is written.

    Returns (mime type, one dict of ProjectPage columns per page). Raises
    DocumentUploadError (file-named, with a code), ProjectPageLimitError, or - only while
    rendering with `preprocess` - a ValueError from PIL or MuPDF."""

    if mime_type != "application/pdf":
        if page_order > max_pages:
            raise ProjectPageLimitError(filename, max_pages)
        rotation = _rotation(transform)
        if preprocess:
            processed = preprocess_document_image(data, transform)
            return mime_type, [{
                "page_number": 1, "processed_data": processed.data, "processed_mime_type": processed.mime_type,
                "image_width": processed.width, "image_height": processed.height, "rotation": rotation,
                "processing_stage": "improved", "warning": " ".join(processed.warnings)}]
        probe_image(data, filename)
        kept = {key: transform[key] for key in UPLOAD_TRANSFORM_KEYS if key in transform}
        return mime_type, [{
            "page_number": 1, "rotation": rotation, "processing_stage": "uploaded",
            "recognition_json": json.dumps({"upload_transform": kept}, ensure_ascii=False) if kept else "{}"}]

    unreadable = UNREADABLE_PDF_NOTHING_SAVED_MSGID if nothing_saved else UNREADABLE_PDF_MSGID
    try:
        inspection = inspect_pdf(data, filename=filename)
    except DocumentUploadError as error:
        raise DocumentUploadError(unreadable, code="unreadable_pdf", filename=filename) from error
    if inspection.needs_password:
        raise DocumentUploadError(PASSWORD_PDF_MSGID, code="password", filename=filename)
    if page_order - 1 + len(inspection.pages) > max_pages:
        raise ProjectPageLimitError(filename, max_pages)
    pictures = pdf_page_pictures(data) if preprocess else []
    pages = []
    for number, info in enumerate(inspection.pages, start=1):
        notes = {key: True for key, flag in (("pdf_damaged", info.damaged), ("pdf_repaired", inspection.repaired))
                 if flag}
        page = {"page_number": number, "extracted_text": info.text, "processing_stage": "uploaded",
                "recognition_json": json.dumps(notes) if notes else "{}"}
        if preprocess:
            # A born-digital page is rendered white paper: the glare and blur advisories
            # describe a photo and were false on every such page. They stay for a scan,
            # unless its text layer already holds the words.
            is_picture = pictures[number - 1] if number <= len(pictures) else True
            try:
                processed = preprocess_document_image(
                    render_pdf_page(data, number - 1),
                    quality_warnings=is_picture and not has_usable_text_layer(info.text))
            except ValueError as error:
                # PyMuPDF listed the page, so a failure to draw it is the file's damage.
                raise DocumentUploadError(unreadable, code="unreadable_pdf", filename=filename) from error
            page.update(processed_data=processed.data, processed_mime_type=processed.mime_type,
                        image_width=processed.width, image_height=processed.height,
                        processing_stage="improved", warning=" ".join(processed.warnings))
        pages.append(page)
    return mime_type, pages


def _store_file(database, project, file_model, page_model, *, filename: str, mime_type: str, data: bytes,
                source_kind: str, digest: str, pages: list[dict[str, Any]], page_order: int) -> None:
    source_file = file_model(project_id=project.id, original_filename=filename, mime_type=mime_type,
                             original_data=data, source_kind=source_kind, sha256=digest)
    database.session.add(source_file)
    database.session.flush()
    for offset, columns in enumerate(pages):
        database.session.add(page_model(project_id=project.id, file_id=source_file.id,
                                        page_order=page_order + offset, extraction_status="pending", **columns))


def _rotation(transform: dict[str, Any]) -> int:
    try:
        return int(transform.get("rotation", 0) or 0) % 360
    except (TypeError, ValueError):
        return 0


def _failure(error: Exception, filename: str, data: bytes) -> FailedFile:
    """The FailedFile of an error, with a file-named msgid whatever raised it."""

    msgid = getattr(error, "msgid", None)
    params = getattr(error, "params", None)
    code = str(getattr(error, "code", "") or "")
    if isinstance(msgid, str) and isinstance(params, dict) and "filename" in params and code:
        return FailedFile(filename=filename, code=code, msgid=msgid, params=dict(params))
    # A plain ValueError from PIL or MuPDF (only while rendering at upload) names nothing.
    if detected_mime_type(data) == "application/pdf":
        return FailedFile(filename=filename, code="unreadable_pdf", msgid=UNREADABLE_PDF_MSGID,
                          params={"filename": filename})
    return FailedFile(filename=filename, code="image_damaged", msgid=IMAGE_DAMAGED_MSGID,
                      params={"filename": filename})


def _error_entry(failure: FailedFile, digest: str) -> dict[str, Any]:
    return {"code": failure.code, "msgid": failure.msgid, "params": failure.params, "sha256": digest,
            "at": datetime.now(timezone.utc).isoformat()}


def _file_errors(project) -> dict[str, Any]:
    """analysis_json["file_errors"]: {str(file id): {code, msgid, params, sha256, at}}."""

    try:
        analysis = json.loads(project.analysis_json or "{}")
    except (TypeError, ValueError):
        return {}
    errors = analysis.get("file_errors") if isinstance(analysis, dict) else None
    return {str(key): value for key, value in errors.items() if isinstance(value, dict)} \
        if isinstance(errors, dict) else {}


def _store_file_errors(project, errors: dict[str, Any]) -> None:
    try:
        analysis = json.loads(project.analysis_json or "{}")
    except (TypeError, ValueError):
        analysis = {}
    analysis = analysis if isinstance(analysis, dict) else {}
    if errors:
        analysis["file_errors"] = errors
    else:
        analysis.pop("file_errors", None)
    project.analysis_json = json.dumps(analysis, ensure_ascii=False)


def file_upload_errors(project) -> dict[str, Any]:
    """The failed-upload entries of a project (read-only; see ingest_uploads)."""

    return _file_errors(project)


def forget_file_upload_error(project, file_id: int) -> None:
    """Remove one failed file's entry (the row was read on a retry, replaced or removed)."""

    errors = _file_errors(project)
    if errors.pop(str(file_id), None) is not None:
        _store_file_errors(project, errors)


def update_file_upload_error(project, file_id: int, failure: FailedFile) -> None:
    """Say the newest reason a failed file could not be used (a retry that failed again)."""

    errors = _file_errors(project)
    digest = str((errors.get(str(file_id)) or {}).get("sha256") or "")
    errors[str(file_id)] = _error_entry(failure, digest)
    _store_file_errors(project, errors)
