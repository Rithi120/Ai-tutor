"""Transactional project-upload ingestion.

The web layer validates request fields and builds the upload list. This service
owns file validation, de-duplication, PDF expansion, image preprocessing, and
the single database transaction.
"""

from __future__ import annotations

import hashlib
import io
from typing import Any, Iterable

from pypdf import PdfReader
from werkzeug.utils import secure_filename

from learnova.ocr.service import preprocess_document_image, render_pdf_page, validate_document_upload


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
):
    project = project_model(
        user_id=user_id,
        title=title,
        subject=subject,
        exam_date=exam_date,
        status="uploaded",
    )
    database.session.add(project)
    database.session.flush()
    page_order = 1
    seen_hashes: set[str] = set()
    for upload, source_kind, transform in uploads:
        data = upload.read()
        filename = secure_filename(upload.filename or "material")[:255] or "material"
        mime_type = validate_document_upload(data, filename, upload.mimetype)
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        source_file = file_model(
            project_id=project.id,
            original_filename=filename,
            mime_type=mime_type,
            original_data=data,
            source_kind=source_kind,
            sha256=digest,
        )
        database.session.add(source_file)
        database.session.flush()
        if mime_type == "application/pdf":
            try:
                reader = PdfReader(io.BytesIO(data))
                if page_order - 1 + len(reader.pages) > 20:
                    raise ValueError("Keep one project to 20 pages or fewer")
                for pdf_index, pdf_page in enumerate(reader.pages, start=1):
                    extracted = (pdf_page.extract_text() or "").strip()
                    processed = preprocess_document_image(render_pdf_page(data, pdf_index - 1))
                    database.session.add(page_model(
                        project_id=project.id,
                        file_id=source_file.id,
                        page_number=pdf_index,
                        page_order=page_order,
                        extracted_text=extracted,
                        processed_data=processed.data,
                        processed_mime_type=processed.mime_type,
                        image_width=processed.width,
                        image_height=processed.height,
                        extraction_status="pending",
                        processing_stage="improved",
                        warning=" ".join(processed.warnings),
                    ))
                    page_order += 1
            except ValueError:
                raise
            except Exception as error:
                raise ValueError(f"Could not read PDF {filename}: {error}") from error
        else:
            if page_order > 20:
                raise ValueError("Keep one project to 20 pages or fewer")
            processed = preprocess_document_image(data, transform)
            database.session.add(page_model(
                project_id=project.id,
                file_id=source_file.id,
                page_number=1,
                page_order=page_order,
                processed_data=processed.data,
                processed_mime_type=processed.mime_type,
                image_width=processed.width,
                image_height=processed.height,
                rotation=int(transform.get("rotation", 0) or 0) % 360,
                extraction_status="pending",
                processing_stage="improved",
                warning=" ".join(processed.warnings),
            ))
            page_order += 1
    if page_order == 1:
        raise ValueError("No unique supported pages were uploaded")
    if page_order - 1 > 20:
        raise ValueError("Keep one project to 20 pages or fewer")
    database.session.commit()
    return project
