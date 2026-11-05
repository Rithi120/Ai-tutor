"""Damaged and odd PDFs never cost the student the rest of an upload (FIX_PLAN PF1).

Each test drives the real routes (the model patched at `app.create_response`, nothing
reaches a provider) and pins one defect of the document-reliability investigation:

* DR-01 one damaged file discarded the whole upload: the valid files are kept, the damaged
  one becomes its own document row with a translated, file-named error and Retry, Replace
  and Remove; nothing already read or analysed is paid for again;
* DR-02 repairable PDFs (truncated downloads, AES owner-only exports) were rejected;
* DR-03 a corrupted page object misaligned the pages; DR-04 a damaged page was "blank";
* DR-05 the text-layer shortcut ignored pictures; D5 a maths layer that lost its layout;
* DR-06 errors without the file name, in English, or with the wrong reason;
* DR-08 the upload request rendered every page.
"""

import io
import json
import os
import tempfile
import threading
import time
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_upload_robustness_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import pymupdf  # noqa: E402  pyright: ignore[reportMissingImports]

import app as application  # noqa: E402
from tests.config_isolation import keep_attribute, keep_config  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.uploads import service as upload_service  # noqa: E402
from tests import pdf_factory as factory  # noqa: E402

db = application.db
ROOT = Path(__file__).resolve().parents[1]
pymupdf.TOOLS.mupdf_display_errors(False)

TUGEND = ("Aristoteles beschreibt die Tugend als die richtige Mitte zwischen zwei Extremen. Tapferkeit "
          "liegt zwischen Feigheit und Tollkuehnheit, Grosszuegigkeit zwischen Geiz und Verschwendung. "
          "Tugenden erwirbt man durch Gewohnheit: wer gerecht handelt, wird gerecht. Das Ziel des Lebens "
          "ist die Glueckseligkeit, die Eudaimonia, die man durch ein tugendhaftes Leben erreicht.")
PDF = "application/pdf"

# The German texts the student reads (learnova/translations: PF1 entries).
DE_UNREADABLE = ("„{filename}“ konnte nicht als PDF gelesen werden. Exportiere die Datei noch einmal oder lade "
                 "Fotos ihrer Seiten hoch.")
DE_PASSWORD = ("„{filename}“ ist mit einem Passwort geschützt. Speichere oder drucke die Datei als PDF ohne "
               "Passwort und lade sie noch einmal hoch.")
DE_EMPTY = "„{filename}“ ist leer. Wähle die Datei noch einmal aus."
DE_IMAGE = "„{filename}“ ist beschädigt oder kein vollständiges Bild. Mache oder speichere das Foto noch einmal."


class FakeResponse:
    usage = None
    model = "test-model"

    def __init__(self, payload):
        self.output_text = json.dumps(payload, ensure_ascii=False)


def reading(text, confidence=0.9):
    return FakeResponse({"blocks": [{
        "type": "handwriting", "content": text, "confidence": confidence, "bbox": [],
        "crossed_out": False, "important_candidate": False, "teacher_highlight_candidate": False,
        "nearby_text": ""}], "detected_page_number": "", "warning": ""})


def rate_limited(**_kwargs):
    raise ai_service.AIProviderError("provider_rate_limit", "The AI provider rate limit was reached.")


class FakeModel:
    """Answers like a well-behaved provider and counts every call by task."""

    def __init__(self, vision=None):
        self.calls: list[str] = []
        self.vision = vision

    def count(self, task_type):
        return self.calls.count(task_type)

    def __call__(self, *, task_type, **kwargs):
        self.calls.append(task_type)
        if task_type == "ocr_document_recognition":
            if self.vision is not None:
                return self.vision(**kwargs)
            return reading("Handschrift: Pflicht und Neigung bei Kant, die Maxime als Grundsatz des Wollens.")
        if task_type == "handwriting_region_review":
            return FakeResponse({"readings": []})
        if task_type == "project_section_generation":
            prompt = str(kwargs.get("input", ""))
            batch = json.loads(prompt.split("\nPages: ", 1)[1].split("\nReturn JSON exactly as ", 1)[0])
            return FakeResponse({"sections": [topic for topic in map(self.topic_for, batch) if topic]})
        raise AssertionError(f"unexpected AI task {task_type}")

    @staticmethod
    def topic_for(entry):
        words = [word.strip(".,:;()") for word in str(entry["text"]).split() if len(word) > 3]
        if not words:
            return None
        title = " ".join(words[:3])
        page_id = entry["page_id"]
        return {"title": title, "main_topic": "Ethik", "kind": "main", "summary": f"{title}.",
                "simple_summary": f"{title} kurz.", "learning_goals": [f"{title} erklaeren"],
                "source_page_ids": [page_id],
                "terms": [{"term": words[0], "definition": "aus den Notizen", "simple_definition": "einfach",
                           "example": "", "page_id": page_id, "quote": words[0]}],
                "items": [], "exam_questions": [f"Erklaere {title}."], "related_topics": [], "prerequisites": [],
                "difficulty": "medium", "importance": "medium", "estimated_minutes": 15}


class Base(unittest.TestCase):
    def setUp(self):
        # Put back what this setUp changes, so it cannot leak into later modules.
        keep_config(self, application.app.config, "AI_MODE", "WTF_CSRF_ENABLED", "RATELIMIT_ENABLED",
                    "PROJECT_MAX_PAGES", "FEATURE_HANDWRITING_SECOND_LOOK")
        keep_attribute(self, application.limiter, "enabled")
        application.app.config.update(TESTING=True, AI_MODE="cached", WTF_CSRF_ENABLED=False,
                                      RATELIMIT_ENABLED=False, PROJECT_MAX_PAGES=40,
                                      FEATURE_HANDWRITING_SECOND_LOOK=False)
        self.addCleanup(application.app.config.update, PROJECT_MAX_PAGES=40, FEATURE_HANDWRITING_SECOND_LOOK=True,
                        PROJECT_PREPROCESS_ON_UPLOAD=False)
        application.limiter.enabled = False
        application.SESSIONS.clear()
        ai_service._RECENT_RESULTS.clear()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = self.register("dora")

    @staticmethod
    def register(name):
        client = application.app.test_client()
        client.post("/register", data={"username": name, "email": f"{name}@example.com",
                                       "password": "correct-horse-battery", "grade": "10"})
        return client

    def german(self, client=None):
        (client or self.client).post("/settings/language", data={"language": "de"})

    def upload(self, files, *, client=None, title="Ethik", **fields):
        data = {"title": title, "subject": "Ethics", **fields,
                "materials": [(io.BytesIO(content), name, mime) for name, content, mime in files]}
        return (client or self.client).post("/projects", data=data, content_type="multipart/form-data")

    def add(self, project_id, files):
        return self.client.post(f"/projects/{project_id}/pages/add", content_type="multipart/form-data",
                                data={"materials": [(io.BytesIO(content), name, mime) for name, content, mime in files]})

    def follow(self, response, client=None):
        self.assertIn(response.status_code, (301, 302, 303), response.get_data(as_text=True)[:400])
        with patch.object(application, "create_response", side_effect=AssertionError("a GET called a model")):
            return (client or self.client).get(response.headers["Location"]).get_data(as_text=True)

    @staticmethod
    def project_id():
        value = db.session.scalar(db.select(application.LearningProject.id).order_by(
            application.LearningProject.id.desc()))
        assert value is not None, "no project was created"
        return value

    @staticmethod
    def files(project_id):
        return db.session.scalars(db.select(application.ProjectFile).where(
            application.ProjectFile.project_id == project_id).order_by(application.ProjectFile.id)).all()

    @staticmethod
    def pages(project_id):
        return db.session.scalars(db.select(application.ProjectPage).where(
            application.ProjectPage.project_id == project_id).order_by(application.ProjectPage.page_order)).all()

    @staticmethod
    def counts():
        return {name: db.session.scalar(db.select(db.func.count(model.id))) or 0
                for name, model in (("projects", application.LearningProject), ("files", application.ProjectFile),
                                    ("pages", application.ProjectPage))}

    @staticmethod
    def file_errors(project_id):
        project = db.session.get(application.LearningProject, project_id)
        assert project is not None
        return json.loads(project.analysis_json or "{}").get("file_errors") or {}

    def report(self, project_id, client=None):
        response = (client or self.client).get(f"/projects/{project_id}/analysis")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
        return response.get_json()

    def read_pages(self, project_id, model):
        """What static/js/project-start.js does: one recognize request per page of the start page."""

        with application.app.app_context():
            project = db.session.get(application.LearningProject, project_id)
            ids = application.project_pages_needing_recognition(project)
        results = {}
        with patch.object(application, "create_response", side_effect=model):
            for page_id in ids:
                response = self.client.post(f"/projects/{project_id}/pages/{page_id}/recognize", json={})
                results[page_id] = (response.status_code, response.get_json() or {})
        return results

    def quick_start(self, project_id, model):
        with patch.object(application, "create_response", side_effect=model):
            return self.client.post(f"/projects/{project_id}/quick-start", json={},
                                    headers={"Accept": "application/json"})


class DamagedFileKeepsTheRestTests(Base):
    """DR-01: valid documents continue, the damaged one is identified separately."""

    def test_six_documents_with_a_damaged_fourth_keep_five_and_name_the_fourth(self):
        self.german()
        response = self.upload(factory.ethics_six())
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])
        self.assertIn("/start", response.headers["Location"])
        start = self.follow(response)
        message = DE_UNREADABLE.format(filename="ethik-4-abgeschnitten.pdf")
        self.assertIn(message, start, "a flash names the file, in German")
        self.assertIn("Diese Datei noch einmal versuchen", start, "the start page offers a retry")
        self.assertEqual(start.count('class="start-file" data-file-id='), 6, "one row per document, the failed one too")
        with application.app.app_context():
            project_id = self.project_id()
            files = self.files(project_id)
            self.assertEqual(len(files), 6)
            self.assertEqual([len(item.pages) for item in files], [2, 1, 1, 0, 2, 1])
            failed = files[3]
            self.assertEqual((failed.original_filename, failed.sha256), ("ethik-4-abgeschnitten.pdf", ""))
            self.assertGreater(len(failed.original_data), 0, "the bytes are kept for a retry")
            errors = self.file_errors(project_id)
            self.assertEqual(list(errors), [str(failed.id)])
            self.assertEqual(errors[str(failed.id)]["code"], "unreadable_pdf")
            failed_id = failed.id
        report = self.report(project_id)
        row = report["files"][3]
        self.assertEqual((row["file_id"], row["position"], row["label"]), (failed_id, 4, "Dokument 4"))
        self.assertEqual(row["reading_state"], "unreadable")
        self.assertEqual(row["upload_error"], {"code": "unreadable_pdf", "message": message})
        self.assertIn(message, row["status_text"])
        self.assertFalse(report["complete"])
        self.assertFalse(report["reading"]["complete"])
        issue = next(issue for issue in report["issues"] if issue["file_id"] == failed_id)
        self.assertEqual(issue["message"], "Dokument 4 („ethik-4-abgeschnitten.pdf“) konnte nicht gelesen werden.")

    def test_a_double_tap_with_a_damaged_file_opens_the_project_just_made(self):
        files = factory.ethics_six()
        self.assertEqual(self.upload(files).status_code, 302)
        again = self.upload(files)
        self.assertEqual(again.status_code, 302)
        self.assertIn("This project was just created. Here it is.", self.follow(again))
        with application.app.app_context():
            self.assertEqual(self.counts()["projects"], 1)

    def test_adding_two_photos_and_a_damaged_pdf_adds_the_photos_and_records_the_pdf(self):
        self.upload([("start.pdf", factory.text_pdf(factory.KANT), PDF)])
        with application.app.app_context():
            project_id = self.project_id()
        response = self.add(project_id, [
            ("neu-1.png", factory.text_image("Neue Seite eins"), "image/png"),
            ("kaputt.pdf", b"%PDF-1.4 kaputt", PDF),
            ("neu-2.jpg", factory.text_image("Neue Seite zwei", fmt="JPEG"), "image/jpeg")])
        self.assertIn(f"/projects/{project_id}/start", response.headers["Location"])
        page = self.follow(response)
        self.assertIn("2 page(s) added. Learnova reads them now.", page)
        self.assertIn("“kaputt.pdf” could not be read as a PDF. Export it again, or upload photos of its pages.", page)
        with application.app.app_context():
            self.assertEqual(self.counts(), {"projects": 1, "files": 4, "pages": 3})
            self.assertEqual([item.original_filename for item in self.files(project_id) if not item.pages], ["kaputt.pdf"])
            self.assertEqual(len(self.file_errors(project_id)), 1)

    def test_adding_only_a_damaged_file_keeps_it_for_a_retry(self):
        self.upload([("start.pdf", factory.text_pdf(factory.KANT), PDF)])
        with application.app.app_context():
            project_id = self.project_id()
        response = self.add(project_id, [("leer.pdf", b"", PDF)])
        self.assertTrue(response.headers["Location"].endswith("#pages"))
        self.assertIn("“leer.pdf” is empty. Choose the file again.", self.follow(response))
        with application.app.app_context():
            self.assertEqual(self.counts(), {"projects": 1, "files": 2, "pages": 1})

    def test_the_file_that_crosses_the_page_limit_is_a_row_and_later_files_still_fit(self):
        response = self.upload([("lang.pdf", factory.many_pages_pdf(39), PDF),
                                ("drei.pdf", factory.text_pdf(factory.KANT, factory.UTIL, factory.PLATON), PDF),
                                ("foto.png", factory.text_image("Ein Foto"), "image/png")])
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])
        self.assertIn("“drei.pdf” would take this project over 40 pages.", self.follow(response))
        with application.app.app_context():
            project_id = self.project_id()
            self.assertEqual(len(self.pages(project_id)), 40)
            files = self.files(project_id)
            self.assertEqual([(item.original_filename, len(item.pages)) for item in files],
                             [("lang.pdf", 39), ("drei.pdf", 0), ("foto.png", 1)])
            self.assertEqual(self.file_errors(project_id)[str(files[1].id)]["code"], "page_limit")

    def test_when_every_file_is_damaged_nothing_is_saved_and_each_file_is_named(self):
        photo = factory.text_image("Pflicht")
        response = self.upload([("kaputt.pdf", b"%PDF-1.4 kaputt", PDF), ("leer.pdf", b"", PDF),
                                ("IMG_2041.png", factory.half(photo), "image/png")])
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        for text in ("None of the files could be used, so nothing was saved.",
                     "“kaputt.pdf” could not be read as a PDF. Export it again, or upload photos of its pages.",
                     "“leer.pdf” is empty. Choose the file again.",
                     "“IMG_2041.png” is damaged or not a complete picture. Take or save the photo again."):
            self.assertIn(text, page)
        with application.app.app_context():
            self.assertEqual(self.counts(), {"projects": 0, "files": 0, "pages": 0})

    def test_damaged_notes_with_typed_topics_keep_the_project_and_the_row(self):
        response = self.upload([("kaputt.pdf", b"%PDF-1.4 kaputt", PDF)], topics="Kant\nUtilitarismus")
        self.assertEqual(response.status_code, 302)
        self.assertIn("#topics", response.headers["Location"])
        with application.app.app_context():
            project_id = self.project_id()
            self.assertEqual([item.original_filename for item in self.files(project_id)], ["kaputt.pdf"])
            self.assertEqual(len(db.session.get(application.LearningProject, project_id).sections), 2)


class RetryReplaceRemoveTests(Base):
    """DR-01: the student can retry, replace or remove the failed document."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.upload(factory.ethics_six()).status_code, 302)
        with application.app.app_context():
            self.project = self.project_id()
            self.failed = next(item.id for item in self.files(self.project) if not item.pages)

    def path(self, action, file_id=None):
        return f"/projects/{self.project}/files/{file_id or self.failed}/{action}"

    def test_replacing_the_failed_file_reads_only_the_new_pages(self):
        model = FakeModel()
        self.read_pages(self.project, model)
        self.assertEqual(model.count("ocr_document_recognition"), 1, "only the photo needs vision")
        response = self.quick_start(self.project, model)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        first_extraction = model.count("project_section_generation")
        self.assertEqual(first_extraction, 2, "seven pages are two batches")
        with application.app.app_context():
            sections_before = len(db.session.get(application.LearningProject, self.project).sections)
            pages_before = [page.id for page in self.pages(self.project)]

        response = self.client.post(self.path("replace"), content_type="multipart/form-data", data={
            "replacement_file": (io.BytesIO(factory.text_pdf(TUGEND)), "ethik-4-neu.pdf", PDF)})
        self.assertIn(f"/projects/{self.project}/start", response.headers["Location"])
        self.assertIn("“ethik-4-neu.pdf” was read: 1 page(s) added.", self.follow(response))
        with application.app.app_context():
            names = [item.original_filename for item in self.files(self.project)]
            self.assertNotIn("ethik-4-abgeschnitten.pdf", names, "the failed row is gone")
            self.assertEqual(self.file_errors(self.project), {})
            pages = self.pages(self.project)
            self.assertEqual([page.id for page in pages][:len(pages_before)], pages_before, "old pages untouched")
            self.assertEqual(len(pages), len(pages_before) + 1)
            self.assertEqual(application.project_pages_needing_recognition(
                db.session.get(application.LearningProject, self.project)), [pages[-1].id])

        later = FakeModel()
        self.read_pages(self.project, later)
        self.assertEqual(later.count("ocr_document_recognition"), 0, "no page that was read is read again")
        response = self.quick_start(self.project, later)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        self.assertEqual(later.count("project_section_generation"), 1, "only the new page's batch is analysed")
        self.assertEqual(later.count("ocr_document_recognition"), 0)
        with application.app.app_context():
            self.assertGreater(len(db.session.get(application.LearningProject, self.project).sections),
                               sections_before)
        report = self.report(self.project)
        self.assertEqual(len(report["files"]), 6)
        self.assertNotIn("unreadable", {row["reading_state"] for row in report["files"]})

    def test_a_retry_that_still_fails_updates_the_message_and_keeps_the_row(self):
        with application.app.app_context():
            project = db.session.get(application.LearningProject, self.project)
            analysis = json.loads(project.analysis_json)
            analysis["file_errors"][str(self.failed)].update(msgid="an old message", at="2026-01-01T00:00:00")
            project.analysis_json = json.dumps(analysis)
            db.session.commit()
        response = self.client.post(self.path("retry"))
        self.assertTrue(response.headers["Location"].endswith("#materials"))
        self.assertIn("“ethik-4-abgeschnitten.pdf” still could not be read.", self.follow(response))
        with application.app.app_context():
            entry = self.file_errors(self.project)[str(self.failed)]
            self.assertEqual(entry["code"], "unreadable_pdf")
            self.assertIn("could not be read as a PDF", entry["msgid"])
            self.assertNotEqual(entry["at"], "2026-01-01T00:00:00")
            self.assertIsNotNone(db.session.get(application.ProjectFile, self.failed))

    def test_a_retry_after_the_limit_was_raised_adds_the_pages(self):
        application.app.config["PROJECT_MAX_PAGES"] = 8
        response = self.add(self.project, [("drei.pdf", factory.text_pdf(TUGEND, factory.WERTE + " Zwei.",
                                                                          factory.UTIL + " Drei."), PDF)])
        self.assertIn("#pages", response.headers["Location"])
        with application.app.app_context():
            limited = next(item.id for item in self.files(self.project) if item.original_filename == "drei.pdf")
            self.assertEqual(self.file_errors(self.project)[str(limited)]["code"], "page_limit")
        application.app.config["PROJECT_MAX_PAGES"] = 40
        response = self.client.post(self.path("retry", limited))
        self.assertIn("auto=1", response.headers["Location"])
        self.assertIn("“drei.pdf” was read: 3 page(s) added.", self.follow(response))
        with application.app.app_context():
            self.assertIsNone(db.session.get(application.ProjectFile, limited))
            self.assertNotIn(str(limited), self.file_errors(self.project))
            self.assertEqual(len(self.pages(self.project)), 10)

    def test_removing_the_failed_file_and_the_owner_only_rule(self):
        other = self.register("mallory")
        for action in ("retry", "remove", "replace"):
            self.assertEqual(other.post(self.path(action)).status_code, 404, action)
        with application.app.app_context():
            with_pages = next(item.id for item in self.files(self.project) if item.pages)
        for action in ("retry", "remove", "replace"):
            self.assertEqual(self.client.post(self.path(action, with_pages)).status_code, 400, action)
        self.assertEqual(self.client.post(f"/projects/{self.project}/files/999999/remove").status_code, 404)
        response = self.client.post(self.path("remove"))
        self.assertTrue(response.headers["Location"].endswith("#materials"))
        self.assertIn("“ethik-4-abgeschnitten.pdf” was removed.", self.follow(response))
        with application.app.app_context():
            self.assertIsNone(db.session.get(application.ProjectFile, self.failed))
            self.assertEqual(self.file_errors(self.project), {})
            self.assertEqual(len(self.files(self.project)), 5)

    def test_the_workspace_row_offers_retry_replace_and_remove(self):
        page = self.client.get(f"/projects/{self.project}").get_data(as_text=True)
        materials = page.split('id="materials"', 1)[1]
        self.assertIn(self.path("retry"), materials)
        self.assertIn(self.path("replace"), materials)
        self.assertIn(self.path("remove"), materials)
        self.assertIn('name="replacement_file"', materials)
        self.assertIn("could not be read as a PDF. Export it again", materials)


class RepairablePdfTests(Base):
    """DR-02 and DR-06: PyMuPDF decides the page list; every error names the file."""

    def test_truncated_downloads_that_pymupdf_repairs_are_accepted(self):
        for keep in (0.8, 0.97, 0.995):
            with self.subTest(keep=keep):
                data = factory.truncated(factory.text_pdf(factory.KANT, factory.UTIL, factory.PLATON), keep)
                response = self.upload([("download.pdf", data, PDF)], title=f"Download {keep}")
                self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])
                with application.app.app_context():
                    pages = self.pages(self.project_id())
                    self.assertEqual(len(pages), 3)
                    self.assertIn("Kant", pages[0].extracted_text)
                    self.assertTrue(json.loads(pages[0].recognition_json or "{}").get("pdf_repaired"))

    def test_owner_password_only_aes_pdfs_are_accepted(self):
        for method in ("AES_128", "AES_256"):
            with self.subTest(method=method):
                response = self.upload([("arbeitsblatt.pdf", factory.owner_only_pdf(method), PDF)], title=method)
                self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])
                with application.app.app_context():
                    (page,) = self.pages(self.project_id())
                    self.assertIn("kategorische Imperativ", page.extracted_text)

    def test_a_repaired_pdf_is_read_from_its_text_layer_with_a_check_note(self):
        data = factory.truncated(factory.text_pdf(factory.KANT, factory.UTIL, factory.PLATON), 0.97)
        self.upload([("download.pdf", data, PDF)])
        with application.app.app_context():
            project_id = self.project_id()
        model = FakeModel()
        results = self.read_pages(project_id, model)
        self.assertEqual(model.calls, [])
        status, body = next(iter(results.values()))
        self.assertEqual(status, 200)
        with application.app.app_context():
            page = self.pages(project_id)[0]
            self.assertEqual(page.confidence_status, "review")
            self.assertIn("This PDF was damaged and Learnova repaired it", page.warning)

    def test_wrong_page_count_gives_two_pages_and_no_english_error(self):
        self.german()
        data = factory.wrong_count(factory.text_pdf(factory.KANT, factory.UTIL, factory.PLATON))
        response = self.upload([("gut.pdf", factory.text_pdf(factory.RAWLS), PDF), ("zaehlung.pdf", data, PDF)])
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])
        page = self.follow(response)
        self.assertNotIn("The requested PDF page does not exist.", page)
        with application.app.app_context():
            files = self.files(self.project_id())
            self.assertEqual([len(item.pages) for item in files], [1, 2])

    def test_empty_half_transferred_and_password_files_are_named_in_german(self):
        self.german()
        photo = factory.text_image("Pflicht und Neigung", fmt="JPEG")
        response = self.upload([("gut.pdf", factory.text_pdf(factory.KANT), PDF), ("leer.pdf", b"", PDF),
                                ("IMG_2041.jpg", factory.half(photo), "image/jpeg"),
                                ("passwort.pdf", factory.user_password_pdf(), PDF)])
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])
        page = self.follow(response)
        for message in (DE_EMPTY.format(filename="leer.pdf"), DE_IMAGE.format(filename="IMG_2041.jpg"),
                        DE_PASSWORD.format(filename="passwort.pdf")):
            self.assertIn(message, page)
        for english in ("Empty files cannot be uploaded.", "The image is damaged, unsupported"):
            self.assertNotIn(english, page)
        with application.app.app_context():
            project_id = self.project_id()
            codes = sorted(entry["code"] for entry in self.file_errors(project_id).values())
            self.assertEqual(codes, ["empty", "image_damaged", "password"])
            self.assertEqual(len(self.pages(project_id)), 1)


class DamagedPageTests(Base):
    """DR-03 and DR-04: a damaged page is never blank, never misaligned, never silent."""

    def one_pdf(self, data, name="blatt.pdf"):
        response = self.upload([(name, data, PDF)])
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])
        with application.app.app_context():
            return self.project_id()

    def test_a_corrupted_page_object_keeps_three_aligned_pages(self):
        project_id = self.one_pdf(factory.corrupt_page_object(
            factory.text_pdf(factory.KANT, factory.UTIL, factory.PLATON), 2))
        with application.app.app_context():
            pages = self.pages(project_id)
            self.assertEqual([page.page_number for page in pages], [1, 2, 3], "PyMuPDF's page list")
            self.assertTrue(json.loads(pages[1].recognition_json or "{}").get("pdf_damaged"))
            self.assertIn("Hoehlengleichnis", pages[2].extracted_text, "page 3 keeps its own words")
            self.assertNotIn("Hoehlengleichnis", pages[1].extracted_text)
            ids = [page.id for page in pages]
        model = FakeModel()
        results = self.read_pages(project_id, model)
        self.assertEqual(model.calls, [], "no AI call for any of the three pages")
        self.assertEqual(results[ids[1]][0], 422)
        self.assertEqual(results[ids[1]][1]["state"], "unreadable")
        self.assertIn("This page of the PDF is damaged", results[ids[1]][1]["message"])
        report = self.report(project_id)
        self.assertEqual(report["files"][0]["unreadable_page_numbers"], [2])
        self.assertFalse(report["complete"])

    def test_a_page_whose_content_cannot_be_decoded_is_unreadable_not_blank(self):
        project_id = self.one_pdf(factory.corrupt_one_page(
            factory.text_pdf(factory.KANT, factory.UTIL, factory.PLATON), 2))
        model = FakeModel()
        results = self.read_pages(project_id, model)
        self.assertEqual(model.calls, [])
        with application.app.app_context():
            page = self.pages(project_id)[1]
            self.assertEqual(page.extraction_status, "unreadable")
            self.assertNotEqual(json.loads(page.recognition_json or "{}").get("text_source"), "blank")
            self.assertNotIn("blank", results[page.id][1]["message"])
        report = self.report(project_id)
        self.assertEqual(report["files"][0]["unreadable_page_numbers"], [2])
        self.assertEqual(report["files"][0]["blank_page_numbers"], [])
        self.assertFalse(report["complete"])

    def test_an_explicit_retry_of_a_damaged_page_still_goes_to_vision(self):
        project_id = self.one_pdf(factory.corrupt_one_page(
            factory.text_pdf(factory.KANT, factory.UTIL, factory.PLATON), 2))
        self.read_pages(project_id, FakeModel())
        with application.app.app_context():
            page_id = self.pages(project_id)[1].id
        model = FakeModel()
        with patch.object(application, "create_response", side_effect=model):
            response = self.client.post(f"/projects/{project_id}/pages/{page_id}/recognize", json={"retry": True})
        self.assertEqual(model.count("ocr_document_recognition"), 1)
        self.assertEqual(response.status_code, 200)

    def test_a_genuinely_blank_page_of_a_healthy_pdf_is_still_blank(self):
        project_id = self.one_pdf(factory.text_pdf(factory.KANT, ""))
        model = FakeModel()
        self.read_pages(project_id, model)
        self.assertEqual(model.calls, [])
        self.assertEqual(self.report(project_id)["files"][0]["blank_page_numbers"], [2])


class TextLayerShortcutTests(Base):
    """DR-05 and D5: a page with pictures, ink or broken formulas is never read from its text
    layer alone and labelled as read."""

    def read_one(self, data, model):
        self.upload([("seite.pdf", data, PDF)])
        with application.app.app_context():
            project_id = self.project_id()
        results = self.read_pages(project_id, model)
        (status, body), = results.values()
        with application.app.app_context():
            return project_id, status, body, self.pages(project_id)[0]

    def test_a_scanner_export_with_an_ocr_layer_goes_to_vision(self):
        model = FakeModel()
        _project, status, body, page = self.read_one(factory.scanner_app_pdf(), model)
        self.assertEqual(model.count("ocr_document_recognition"), 1)
        self.assertEqual((status, json.loads(page.recognition_json)["text_source"]), (200, "vision"))

    def test_a_born_digital_page_with_a_raster_note_goes_to_vision(self):
        model = FakeModel()
        _project, status, body, page = self.read_one(factory.mixed_pdf(0.2), model)
        self.assertEqual(model.count("ocr_document_recognition"), 1)
        self.assertEqual(json.loads(page.recognition_json)["text_source"], "vision")

    def test_a_small_logo_keeps_the_free_text_layer_reading(self):
        model = FakeModel()
        _project, status, body, page = self.read_one(factory.mixed_pdf(0.05), model)
        self.assertEqual(model.calls, [])
        self.assertEqual((status, body["state"]), (200, "read"))

    def test_a_mixed_page_meeting_a_busy_provider_is_never_read(self):
        model = FakeModel(vision=rate_limited)
        project_id, status, body, page = self.read_one(factory.mixed_pdf(0.2), model)
        self.assertEqual(model.count("ocr_document_recognition"), 1)
        self.assertIn(body["state"], ("waiting", "needs_review"))
        self.assertNotEqual(self.report(project_id)["files"][0]["reading_state"], "read")

    def test_a_maths_layer_that_lost_its_layout_needs_review_without_a_call(self):
        model = FakeModel()
        project_id, status, body, page = self.read_one(factory.formula_pdf(), model)
        self.assertEqual(model.calls, [])
        self.assertEqual((status, body["state"]), (200, "needs_review"))
        self.assertEqual(page.confidence_status, "unclear")
        self.assertIn("The formulas on this page may have lost their layout", page.warning)

    def test_prose_is_still_read_from_the_text_layer(self):
        model = FakeModel()
        _project, status, body, page = self.read_one(factory.text_pdf(factory.KANT), model)
        self.assertEqual(model.calls, [])
        self.assertEqual((status, body["state"], page.confidence_status), (200, "read", "high"))


class LazyRenderingTests(Base):
    """DR-08: the upload request renders nothing; pictures are made when first needed."""

    def test_a_forty_page_pdf_upload_renders_nothing(self):
        from learnova.uploads import service as uploads
        data = factory.many_pages_pdf(40)
        with patch.object(uploads, "render_pdf_page", wraps=uploads.render_pdf_page) as render, \
                patch.object(uploads, "preprocess_document_image", wraps=uploads.preprocess_document_image) as prep:
            started = time.perf_counter()
            response = self.upload([("vierzig.pdf", data, PDF)])
            seconds = time.perf_counter() - started
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])
        self.assertEqual((render.call_count, prep.call_count), (0, 0))
        print(f"\n[DR-08] 40-page PDF upload took {seconds:.2f} s")
        if not os.environ.get("CI"):
            self.assertLess(seconds, 5.0)
        with application.app.app_context():
            project_id = self.project_id()
            pages = self.pages(project_id)
            self.assertEqual(len(pages), 40)
            self.assertTrue(all(page.processed_data is None for page in pages))
            self.assertTrue(all(page.extracted_text for page in pages))
            first = pages[0].id
        image = self.client.get(f"/projects/{project_id}/pages/{first}/image/processed")
        self.assertEqual(image.status_code, 200)
        self.assertIn("private", image.headers["Cache-Control"])
        self.assertIn("no-store", image.headers["Cache-Control"])
        self.assertTrue(image.data.startswith(b"\x89PNG"))
        with application.app.app_context():
            self.assertIsNotNone(db.session.get(application.ProjectPage, first).processed_data, "stored once made")
        other = self.register("mallory")
        self.assertEqual(other.get(f"/projects/{project_id}/pages/{first}/image/processed").status_code, 404)

    def test_a_camera_scan_is_rendered_with_its_rotation_and_crop_on_first_read(self):
        from learnova.ocr.service import preprocess_document_image
        photo = factory.text_image("Kant und die Pflicht", size=(900, 1300))
        metadata = {"rotation": 90, "crop": {"left": 0.05, "right": 0.05}}
        response = self.client.post("/projects", content_type="multipart/form-data", data={
            "title": "Scan", "subject": "Ethics", "camera_scans": [(io.BytesIO(photo), "camera-page-1.png", "image/png")],
            "scan_metadata": json.dumps([metadata])})
        self.assertEqual(response.status_code, 302)
        with application.app.app_context():
            project_id = self.project_id()
            (page,) = self.pages(project_id)
            self.assertIsNone(page.processed_data)
            self.assertEqual(page.rotation, 90)
            self.assertEqual(json.loads(page.recognition_json)["upload_transform"], metadata)
            page_id = page.id
        model = FakeModel()
        self.read_pages(project_id, model)
        self.assertEqual(model.count("ocr_document_recognition"), 1)
        expected = preprocess_document_image(photo, metadata)
        with application.app.app_context():
            page = db.session.get(application.ProjectPage, page_id)
            self.assertEqual((page.image_width, page.image_height), (expected.width, expected.height))
            self.assertGreater(page.image_width, page.image_height, "turned by 90 degrees")
            self.assertEqual(json.loads(page.recognition_json)["upload_transform"], metadata, "kept by the reading")

    def test_rotating_an_unrendered_page_renders_it_first(self):
        self.upload([("seite.pdf", factory.text_pdf(factory.KANT), PDF)])
        with application.app.app_context():
            project_id = self.project_id()
            page_id = self.pages(project_id)[0].id
        response = self.client.post(f"/projects/{project_id}/pages/{page_id}/rotate")
        self.assertEqual(response.status_code, 302)
        with application.app.app_context():
            page = db.session.get(application.ProjectPage, page_id)
            self.assertIsNotNone(page.processed_data)
            self.assertEqual(page.rotation, 90)
            self.assertGreater(page.image_width, page.image_height)

    def test_the_rollback_switch_restores_rendering_at_upload(self):
        application.app.config["PROJECT_PREPROCESS_ON_UPLOAD"] = True
        self.upload([("seite.pdf", factory.text_pdf(factory.KANT, factory.UTIL), PDF),
                     ("foto.png", factory.text_image("Foto"), "image/png")])
        with application.app.app_context():
            pages = self.pages(self.project_id())
            self.assertTrue(all(page.processed_data for page in pages))
            self.assertEqual({page.processing_stage for page in pages}, {"improved"})

    def test_the_settings_are_validated(self):
        from flask import Flask

        from learnova.config import configure_app
        self.assertFalse(application.app.config["PROJECT_PREPROCESS_ON_UPLOAD"])
        self.assertEqual(application.app.config["PDF_TEXT_SHORTCUT_MAX_IMAGE_SHARE"], 0.10)
        with patch.dict(os.environ, {"PROJECT_PREPROCESS_ON_UPLOAD": "true", "PDF_TEXT_SHORTCUT_MAX_IMAGE_SHARE": "0.3"}):
            configured = Flask("pf1-settings", instance_path=tempfile.mkdtemp())
            configure_app(configured, "testing")
        self.assertTrue(configured.config["PROJECT_PREPROCESS_ON_UPLOAD"])
        self.assertEqual(configured.config["PDF_TEXT_SHORTCUT_MAX_IMAGE_SHARE"], 0.3)
        with patch.dict(os.environ, {"PDF_TEXT_SHORTCUT_MAX_IMAGE_SHARE": "1.5"}):
            with self.assertRaises(RuntimeError):
                configure_app(Flask("pf1-settings", instance_path=tempfile.mkdtemp()), "testing")


class InspectionHelperTests(unittest.TestCase):
    """The pure helpers in learnova/ocr/service.py."""

    def test_inspect_pdf_follows_pymupdf_and_marks_damage(self):
        from learnova.ocr.service import DocumentUploadError, inspect_pdf
        healthy = factory.text_pdf(factory.KANT, factory.UTIL)
        inspection = inspect_pdf(healthy)
        self.assertEqual((len(inspection.pages), inspection.repaired, inspection.needs_password), (2, False, False))
        self.assertFalse(any(page.damaged for page in inspection.pages))
        broken = inspect_pdf(factory.corrupt_page_object(factory.text_pdf(factory.KANT, factory.UTIL, factory.PLATON)))
        self.assertEqual([page.damaged for page in broken.pages], [False, True, False])
        self.assertTrue(broken.repaired)
        self.assertTrue(inspect_pdf(factory.user_password_pdf()).needs_password)
        with self.assertRaises(DocumentUploadError) as caught:
            inspect_pdf(b"%PDF-1.4 kaputt", filename="kaputt.pdf")
        self.assertEqual(caught.exception.params, {"filename": "kaputt.pdf"})

    def test_image_shares_and_pictures_agree(self):
        from learnova.ocr.service import pdf_page_image_shares, pdf_page_pictures
        shares = pdf_page_image_shares(factory.mixed_pdf(0.2))
        self.assertAlmostEqual(shares[0], 0.2, places=2)
        self.assertEqual(pdf_page_image_shares(b"%PDF-1.4 kaputt"), [])
        self.assertEqual(pdf_page_pictures(factory.scanner_app_pdf()), [True])
        self.assertEqual(pdf_page_pictures(factory.mixed_pdf(0.2)), [False])

    def test_probe_image_names_the_file(self):
        from learnova.ocr.service import DocumentUploadError, probe_image
        photo = factory.text_image("Foto")
        self.assertIsNone(probe_image(photo, "foto.png"))
        with self.assertRaises(DocumentUploadError) as caught:
            probe_image(factory.half(photo), "foto.png")
        self.assertEqual((caught.exception.code, caught.exception.params), ("image_damaged", {"filename": "foto.png"}))

    def test_text_layer_layout_lost(self):
        from learnova.ocr.service import text_layer_layout_lost
        self.assertTrue(text_layer_layout_lost("Potenzen\n2\n3\n· 2\n4\n= 2\n7\n" + "Text der Seite\n" * 10))
        self.assertTrue(text_layer_layout_lost("Die Regel � gilt � immer �."))
        self.assertFalse(text_layer_layout_lost(factory.KANT))
        self.assertFalse(text_layer_layout_lost("Aufgabe 1\nAufgabe 2\n" + "Ein Satz mit Worten.\n" * 30))


REPAIRED_NOTE = "This PDF was damaged and Learnova repaired it – check that every page is complete."
CHECK_NOTE = "Some pages still need checking – the text recognition may be wrong."
MISSING_NOTE = "Not all of your material is read and analysed yet"


class ReviewFindingTests(Base):
    """Independent review A of 2026-11-02 (prod/review_a.md): F1, F2, F3, F5 and F7."""

    def test_a_repaired_pdf_needs_a_check_and_its_row_says_why(self):
        # F1: a download cut off at 80 % - PyMuPDF repairs it and the last page keeps only part
        # of its text. It was "Read · 3 of 3 pages read" and complete, with no word of the repair.
        data = factory.truncated(factory.text_pdf(factory.KANT, factory.UTIL, factory.PLATON), 0.8)
        self.upload([("repariert.pdf", data, PDF)])
        with application.app.app_context():
            project_id = self.project_id()
        model = FakeModel()
        self.read_pages(project_id, model)
        self.assertEqual(model.count("ocr_document_recognition"), 0, "read from its own text layer")
        response = self.quick_start(project_id, model)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        report = self.report(project_id)
        (row,) = report["files"]
        self.assertEqual(row["state"], "needs_ocr_review")
        self.assertEqual(row["reading_state"], "needs_review")
        self.assertEqual(row["review_page_numbers"], [1, 2, 3])
        self.assertIn(REPAIRED_NOTE, row["detail"])
        self.assertIn(REPAIRED_NOTE, row["status_text"], "the start page's row says it too")
        self.assertFalse(report["complete"])
        self.assertTrue(report["nothing_missing"], "every page was read and analysed: only a check is open")
        self.assertEqual([issue["page_numbers"] for issue in report["issues"] if issue["code"] == "needs_review"],
                         [[1, 2, 3]])
        workspace = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        self.assertIn(REPAIRED_NOTE, workspace.split('id="materialCheck"', 1)[1])
        with application.app.app_context():
            self.assertEqual(application.project_unverified_page_ids(
                db.session.get(application.LearningProject, project_id)),
                {page.id for page in self.pages(project_id)}, "nothing built from them counts as verified")
        # Checked on the review page: what the pages say, the material is complete.
        response = self.client.post(f"/projects/{project_id}/review", data={"action": "confirm"})
        self.assertEqual(response.status_code, 302)
        report = self.report(project_id)
        self.assertEqual(report["files"][0]["reading_state"], "read")
        self.assertNotIn(REPAIRED_NOTE, report["files"][0]["detail"])
        self.assertTrue(report["complete"], report["issues"])

    def test_a_page_flagged_for_review_keeps_the_material_incomplete(self):
        # F2: a photo read with confidence 0.30 ("unclear", "Needs OCR review") left the report
        # complete, so the workspace never said that anything was open.
        model = FakeModel(vision=lambda **_kwargs: reading("Pflicht und Neigung bei Kant, die Maxime.", 0.3))
        self.upload([("heft.png", factory.text_image("Pflicht und Neigung"), "image/png")])
        with application.app.app_context():
            project_id = self.project_id()
        self.read_pages(project_id, model)
        response = self.quick_start(project_id, model)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        report = self.report(project_id)
        self.assertEqual(report["files"][0]["state"], "needs_ocr_review")
        self.assertFalse(report["complete"])
        self.assertTrue(report["nothing_missing"])
        topics = self.client.get(f"/projects/{project_id}").get_data(as_text=True).split('id="topics"', 1)[1]
        self.assertIn(CHECK_NOTE, topics, "the topic list says which pages still need a check")
        self.assertNotIn(MISSING_NOTE, topics, "nothing is missing from the topic list")
        self.assertIn(f"/projects/{project_id}/review", topics.split(CHECK_NOTE, 1)[1][:600])

    def test_retry_replace_and_remove_limits_count_per_account(self):
        # F3: keyed by address, one school or family network shared 20 an hour.
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        for action in ("retry", "replace", "remove"):
            route = f'@app.post("/projects/<int:project_id>/files/<int:file_id>/{action}")\n'
            limit_line = source.split(route, 1)[1].split("\n", 1)[0]
            with self.subTest(action=action):
                self.assertTrue(limit_line.startswith("@limiter.limit("), limit_line)
                self.assertIn("key_func=user_rate_key", limit_line)
        other = self.register("mallory")
        application.app.config.update(TESTING=False)
        application.limiter.enabled = True
        application.limiter.reset()
        try:
            statuses = [other.post("/projects/999999/files/1/remove", json={}).status_code for _ in range(21)]
            mine = self.client.post("/projects/999999/files/1/remove", json={}).status_code
        finally:
            application.app.config.update(TESTING=True)
            application.limiter.enabled = False
            application.limiter.reset()
        self.assertEqual(statuses, [404] * 20 + [429])
        self.assertEqual(mine, 404, "another account behind the same address has its own 20")

    def test_retrying_a_page_limit_file_names_the_limit_again(self):
        # F5: the file is readable; it does not fit. "still could not be read" was wrong.
        self.assertEqual(self.upload(factory.ethics_six()).status_code, 302)
        with application.app.app_context():
            project_id = self.project_id()
        application.app.config["PROJECT_MAX_PAGES"] = 8
        self.add(project_id, [("drei.pdf", factory.text_pdf(TUGEND, factory.WERTE + " Zwei.",
                                                            factory.UTIL + " Drei."), PDF)])
        with application.app.app_context():
            limited = next(item.id for item in self.files(project_id) if item.original_filename == "drei.pdf")
        response = self.client.post(f"/projects/{project_id}/files/{limited}/retry")
        page = self.follow(response)
        self.assertIn("“drei.pdf” would take this project over 8 pages. Split the material into two projects or "
                      "remove some pages.", page)
        self.assertNotIn("still could not be read", page)
        with application.app.app_context():
            self.assertEqual(self.file_errors(project_id)[str(limited)]["code"], "page_limit")

    def double_tap(self, action, data):
        """Two concurrent taps on one failed row; the PDF inspection is slowed to 0.4 s, as for
        a larger file, so both requests pass the row check before either commits."""

        self.assertEqual(self.upload([("gut.pdf", factory.text_pdf(factory.KANT), PDF),
                                      ("falsch.png", factory.text_pdf(factory.PLATON, factory.RAWLS),
                                       "image/png")]).status_code, 302)
        with application.app.app_context():
            project_id = self.project_id()
            failed = next(item.id for item in self.files(project_id) if not item.pages)
            self.assertEqual(self.file_errors(project_id)[str(failed)]["code"], "type_mismatch")
        second = application.app.test_client()
        second.post("/login", data={"identifier": "dora", "password": "correct-horse-battery"})
        barrier = threading.Barrier(2)
        statuses: list[int] = []
        real_inspect = upload_service.inspect_pdf

        def slow_inspect(*args, **kwargs):
            time.sleep(0.4)
            return real_inspect(*args, **kwargs)

        def tap(client):
            barrier.wait()
            response = client.post(f"/projects/{project_id}/files/{failed}/{action}", data=data(),
                                   content_type="multipart/form-data")
            statuses.append(response.status_code)

        with warnings.catch_warnings(record=True) as caught, patch.object(upload_service, "inspect_pdf", slow_inspect):
            warnings.simplefilter("always")
            threads = [threading.Thread(target=tap, args=(client,)) for client in (self.client, second)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
        with application.app.app_context():
            files = [(item.original_filename, len(item.pages)) for item in self.files(project_id)]
            pages = len(self.pages(project_id))
            # By content, not by id: SQLite may give the new file the deleted row's id.
            still_there = any(not item.pages for item in self.files(project_id))
        deletes = [str(item.message) for item in caught if "DELETE statement on table" in str(item.message)]
        return statuses, files, pages, still_there, deletes

    def test_a_double_tap_on_try_again_adds_the_document_once(self):
        # F7: both requests passed the "0 pages" check, both added the file and its pages.
        statuses, files, pages, still_there, deletes = self.double_tap("retry", dict)
        self.assertEqual(statuses, [302, 302])
        self.assertEqual(files, [("gut.pdf", 1), ("falsch.png", 2)])
        self.assertEqual(pages, 3)
        self.assertFalse(still_there)
        self.assertEqual(deletes, [], "no request deleted a row another one had already taken")

    def test_a_double_tap_on_replace_adds_the_new_file_once(self):
        statuses, files, pages, still_there, deletes = self.double_tap("replace", lambda: {
            "replacement_file": (io.BytesIO(factory.text_pdf(factory.WERTE, factory.UTIL)), "neu.pdf", PDF)})
        self.assertEqual(statuses, [302, 302])
        self.assertEqual(files, [("gut.pdf", 1), ("neu.pdf", 2)])
        self.assertEqual(pages, 3)
        self.assertFalse(still_there)
        self.assertEqual(deletes, [])


if __name__ == "__main__":
    unittest.main()
