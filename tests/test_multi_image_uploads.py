"""Project Mode with many images (spec Phase 3): batches, order, JSON answers, thumbnails.

The multi-image form (static/js/image-collection.js) sends forty photos as several small
requests: the first creates the project (POST /projects, Accept: application/json), the
others join it (POST /projects/<id>/pages/add). These tests drive the real routes with the
model patched (nothing reaches a provider) and pin what the page relies on:

* JSON answers per file, in the order sent, and never a redirect for a script;
* `materials_meta` keeps gallery photos and camera scans in the order the student
  arranged them (they used to be stored materials first, scans after), with the scanner's
  rotation and an optional label (project_page.label, migration 045);
* a batch sent twice adds nothing twice; the page limit counts across batches;
* a request over MAX_CONTENT_LENGTH is a JSON 413 for a script (it was a redirect that
  threw away every camera scan held in the page) and still a redirect for a plain form;
* the per-request file cap; the cached 'thumb' variant, private to its owner;
* the workspace page gallery, and the translated refusal to delete a page topics cite.
"""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_multi_image_uploads_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

from PIL import Image  # noqa: E402

import app as application  # noqa: E402
from tests.config_isolation import keep_attribute, keep_config  # noqa: E402
from tests import pdf_factory as factory  # noqa: E402

db = application.db
JSON = {"Accept": "application/json"}


def photo(shade: int, size=(900, 1200)) -> bytes:
    """A distinct, valid photo (its own bytes, so no two are duplicates)."""

    image = Image.new("RGB", size, (250, 250, 248))
    for x in range(40, size[0] - 40, 60):
        for y in range(60 + shade % 50, size[1] - 60, 80):
            image.putpixel((x, y), (shade % 255, 20, 20))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def no_model(**kwargs):
    raise AssertionError(f"an upload called the AI gateway for {kwargs.get('task_type')}")


class Base(unittest.TestCase):
    def setUp(self):
        keep_config(self, application.app.config, "TESTING", "AI_MODE", "WTF_CSRF_ENABLED", "RATELIMIT_ENABLED",
                    "PROJECT_MAX_PAGES", "PROJECT_MAX_FILES_PER_REQUEST", "MAX_CONTENT_LENGTH")
        keep_attribute(self, application.limiter, "enabled")
        application.app.config.update(TESTING=True, AI_MODE="cached", WTF_CSRF_ENABLED=False,
                                      RATELIMIT_ENABLED=False, PROJECT_MAX_PAGES=40)
        application.limiter.enabled = False
        application._PAGE_THUMBNAILS.clear()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = self.register("mia")
        patcher = patch.object(application, "create_response", side_effect=no_model)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def register(name):
        client = application.app.test_client()
        client.post("/register", data={"username": name, "email": f"{name}@example.com",
                                       "password": "correct-horse-battery", "grade": "10"})
        return client

    def create(self, files, meta=None, *, client=None, headers=JSON, **fields):
        data = {"title": "Biologie", "subject": "Biology", **fields,
                "materials": [(io.BytesIO(content), name, mime) for name, content, mime in files]}
        if meta is not None:
            data["materials_meta"] = json.dumps(meta)
        return (client or self.client).post("/projects", data=data, headers=headers,
                                            content_type="multipart/form-data")

    def add(self, project_id, files, meta=None, *, headers=JSON):
        data = {"materials": [(io.BytesIO(content), name, mime) for name, content, mime in files]}
        if meta is not None:
            data["materials_meta"] = json.dumps(meta)
        return self.client.post(f"/projects/{project_id}/pages/add", data=data, headers=headers,
                                content_type="multipart/form-data")

    @staticmethod
    def pages(project_id):
        with application.app.app_context():
            rows = db.session.scalars(db.select(application.ProjectPage).where(
                application.ProjectPage.project_id == project_id).order_by(application.ProjectPage.page_order)).all()
            return [{"id": page.id, "order": page.page_order, "file": page.source_file.original_filename,
                     "kind": page.source_file.source_kind, "rotation": page.rotation, "label": page.label,
                     "transform": json.loads(page.recognition_json or "{}").get("upload_transform")}
                    for page in rows]

    @staticmethod
    def project_count():
        with application.app.app_context():
            return db.session.scalar(db.select(db.func.count(application.LearningProject.id)))


class BatchedUploadTests(Base):
    def test_the_first_batch_creates_the_project_and_answers_in_json(self):
        response = self.create([("p1.jpg", photo(1), "image/jpeg"), ("p2.jpg", photo(2), "image/jpeg")])
        self.assertEqual(response.status_code, 201, response.data[:300])
        body = response.get_json()
        self.assertTrue(body["ok"])
        project_id = body["project_id"]
        self.assertEqual(body["add_pages_url"], f"/projects/{project_id}/pages/add")
        self.assertIn(f"/projects/{project_id}/start", body["start_url"])
        self.assertEqual(body["pages_added"], 2)
        self.assertEqual([(row["index"], row["status"]) for row in body["results"]], [(0, "added"), (1, "added")])
        self.assertEqual(body["max_pages"], 40)

    def test_what_the_json_does_not_carry_is_said_on_the_next_page(self):
        # Merge seam (exam lane x uploads lane): the script answer used to return before the
        # "only the first 40 topics" notice, so typed topics over the limit vanished silently.
        topics = "\n".join(f"Thema {n}" for n in range(1, 46))
        response = self.create([("p1.jpg", photo(1), "image/jpeg")], topics=topics)
        self.assertEqual(response.status_code, 201, response.data[:300])
        with self.client.session_transaction() as session:
            flashes = [message for _category, message in session.get("_flashes", [])]
        self.assertEqual(len(flashes), 1, flashes)
        self.assertIn("40", flashes[0])
        self.assertIn("45", flashes[0])
        # The notes photo itself is answered in the JSON only, never flashed as well.
        self.assertEqual([row["status"] for row in response.get_json()["results"]], ["added"])

    def test_batches_append_in_order_and_a_resent_batch_adds_nothing(self):
        first = self.create([(f"p{n}.jpg", photo(n), "image/jpeg") for n in (1, 2, 3)]).get_json()
        project_id = first["project_id"]
        second = self.add(project_id, [(f"p{n}.jpg", photo(n), "image/jpeg") for n in (4, 5, 6)])
        self.assertEqual(second.status_code, 200, second.data[:300])
        self.assertEqual(second.get_json()["pages_added"], 3)
        third = self.add(project_id, [(f"p{n}.jpg", photo(n), "image/jpeg") for n in (7,)])
        self.assertEqual(third.get_json()["pages"], 7)
        self.assertEqual([page["file"] for page in self.pages(project_id)], [f"p{n}.jpg" for n in range(1, 8)])
        self.assertEqual([page["order"] for page in self.pages(project_id)], list(range(1, 8)))
        # The answer to batch 2 was lost and the page sent it again: nothing doubles.
        again = self.add(project_id, [(f"p{n}.jpg", photo(n), "image/jpeg") for n in (4, 5, 6)])
        self.assertEqual(again.status_code, 200, again.data[:300])
        body = again.get_json()
        self.assertEqual([row["status"] for row in body["results"]], ["duplicate"] * 3)
        self.assertEqual(body["pages_added"], 0)
        self.assertEqual(len(self.pages(project_id)), 7)
        self.assertEqual(self.project_count(), 1)

    def test_the_create_batch_sent_twice_is_the_same_project(self):
        files = [("p1.jpg", photo(1), "image/jpeg")]
        first = self.create(files).get_json()
        again = self.create(files)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.get_json()["project_id"], first["project_id"])
        self.assertTrue(again.get_json()["duplicate"])
        self.assertEqual(self.project_count(), 1)

    def test_materials_meta_keeps_camera_scans_between_gallery_photos(self):
        files = [("gallery-a.jpg", photo(1), "image/jpeg"), ("camera-page-1.jpg", photo(2), "image/jpeg"),
                 ("gallery-b.jpg", photo(3), "image/jpeg")]
        meta = [{"kind": "upload", "label": "  Arbeitsblatt\n 3 "},
                {"kind": "camera", "rotation": 90, "crop": {"left": 0.1, "right": 0, "top": 0, "bottom": 0},
                 "label": "x" * 200, "evil": "<script>"},
                {"kind": "upload"}]
        response = self.create(files, meta)
        self.assertEqual(response.status_code, 201, response.data[:300])
        pages = self.pages(response.get_json()["project_id"])
        self.assertEqual([(page["file"], page["kind"]) for page in pages],
                         [("gallery-a.jpg", "upload"), ("camera-page-1.jpg", "camera"), ("gallery-b.jpg", "upload")])
        self.assertEqual(pages[1]["rotation"], 90)
        self.assertEqual(pages[1]["transform"], {"rotation": 90, "crop": {"left": 0.1, "right": 0, "top": 0, "bottom": 0}},
                         "only the scanner's transform keys are kept")
        self.assertEqual(pages[0]["label"], "Arbeitsblatt 3")
        self.assertEqual(pages[1]["label"], "x" * 80)
        self.assertEqual(pages[2]["label"], "")

    def test_without_meta_the_old_form_order_still_holds(self):
        data = {"title": "Bio", "subject": "Biology",
                "materials": [(io.BytesIO(photo(1)), "a.jpg", "image/jpeg")],
                "camera_scans": [(io.BytesIO(photo(2)), "camera-page-1.jpg", "image/jpeg")],
                "scan_metadata": json.dumps([{"rotation": 180}])}
        response = self.client.post("/projects", data=data, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302, "a plain form still gets its redirect")
        with application.app.app_context():
            project_id = db.session.scalar(db.select(application.LearningProject.id))
        pages = self.pages(project_id)
        self.assertEqual([(page["file"], page["kind"], page["rotation"]) for page in pages],
                         [("a.jpg", "upload", 0), ("camera-page-1.jpg", "camera", 180)])

    def test_the_page_limit_counts_across_batches(self):
        application.app.config["PROJECT_MAX_PAGES"] = 3
        project_id = self.create([(f"p{n}.jpg", photo(n), "image/jpeg") for n in (1, 2)]).get_json()["project_id"]
        body = self.add(project_id, [(f"p{n}.jpg", photo(n), "image/jpeg") for n in (3, 4)]).get_json()
        self.assertEqual([row["status"] for row in body["results"]], ["added", "failed"])
        self.assertEqual(body["results"][1]["code"], "page_limit")
        self.assertIn("p4.jpg", body["results"][1]["message"])
        self.assertEqual(len(self.pages(project_id)), 3)

    def test_a_batch_of_only_damaged_files_creates_nothing_and_names_each(self):
        response = self.create([("a.jpg", photo(1)[:400], "image/jpeg"), ("b.jpg", b"not a picture", "image/jpeg")])
        self.assertEqual(response.status_code, 422)
        body = response.get_json()
        self.assertEqual(body["code"], "nothing_usable")
        results = body["details"]["results"]
        self.assertEqual([row["status"] for row in results], ["failed", "failed"])
        self.assertIn("a.jpg", results[0]["message"])
        self.assertEqual(self.project_count(), 0)

    def test_one_request_may_carry_only_so_many_files(self):
        application.app.config["PROJECT_MAX_FILES_PER_REQUEST"] = 2
        files = [(f"p{n}.jpg", photo(n), "image/jpeg") for n in (1, 2, 3)]
        response = self.create(files)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "too_many_files")
        self.assertEqual(self.project_count(), 0)
        project_id = self.create(files[:2]).get_json()["project_id"]
        refused = self.add(project_id, files)
        self.assertEqual(refused.get_json()["code"], "too_many_files")
        form = self.create(files, headers={})
        self.assertEqual(form.status_code, 200)
        self.assertIn("Send at most 2 files at once.", form.get_data(as_text=True))

    def test_a_request_over_the_size_limit_is_json_for_a_script_and_a_redirect_for_a_form(self):
        application.app.config["MAX_CONTENT_LENGTH"] = 50_000
        big = [("p1.jpg", photo(1) + b"\0" * 60_000, "image/jpeg")]
        scripted = self.create(big, headers={**JSON, "Referer": "http://localhost/projects"})
        self.assertEqual(scripted.status_code, 413)
        self.assertEqual(scripted.get_json()["code"], "upload_too_large")
        project_id = None
        application.app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024
        project_id = self.create([("p0.jpg", photo(9), "image/jpeg")]).get_json()["project_id"]
        application.app.config["MAX_CONTENT_LENGTH"] = 50_000
        added = self.add(project_id, big)
        self.assertEqual(added.status_code, 413)
        self.assertEqual(added.get_json()["code"], "upload_too_large")
        form = self.create(big, headers={"Referer": "http://localhost/projects",
                                         "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
        self.assertEqual(form.status_code, 302, "a browser navigation keeps the redirect")
        self.assertEqual(self.project_count(), 1)

    def test_an_expired_form_and_the_rate_limit_are_json_for_a_script(self):
        # Review fix: these redirected for /projects and /pages/add, so the batch's XHR followed
        # the redirect, showed only "upload failed" and the flash surfaced on a later page.
        project_id = self.create([("p0.jpg", photo(9), "image/jpeg")]).get_json()["project_id"]
        application.app.config.update(TESTING=False, WTF_CSRF_ENABLED=True)
        expired = self.add(project_id, [("p1.jpg", photo(1), "image/jpeg")])
        self.assertEqual(expired.status_code, 400)
        self.assertEqual(expired.get_json()["code"], "csrf_failed")
        form = self.add(project_id, [("p1.jpg", photo(1), "image/jpeg")],
                        headers={"Referer": "http://localhost/projects", "Accept": "text/html,*/*;q=0.8"})
        self.assertEqual(form.status_code, 302, "a browser navigation keeps the redirect")
        application.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with application.app.test_request_context(f"/projects/{project_id}/pages/add", method="POST", headers=JSON):
            response, status = application.rate_limit_error(None)
            self.assertEqual(status, 429)
            self.assertEqual(response.get_json()["code"], "rate_limited")
        with application.app.test_request_context(f"/projects/{project_id}/pages/add", method="POST",
                                                  headers={"Accept": "text/html,*/*;q=0.8"}):
            self.assertEqual(application.rate_limit_error(None).status_code, 302)

    def test_the_form_without_javascript_still_redirects(self):
        response = self.create([("p1.jpg", photo(1), "image/jpeg")], headers={})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/start", response.headers["Location"])
        project_id = int(response.headers["Location"].split("/projects/")[1].split("/")[0])
        added = self.add(project_id, [("p2.jpg", photo(2), "image/jpeg")], headers={})
        self.assertEqual(added.status_code, 302)


class ThumbnailTests(Base):
    def setUp(self):
        super().setUp()
        body = self.create([("p1.jpg", photo(1, size=(1800, 2400)), "image/jpeg"),
                            ("notes.pdf", factory.text_pdf("Photosynthese wandelt Licht in Energie um."),
                             "application/pdf")],
                           [{"kind": "camera", "rotation": 90}, {"kind": "upload"}]).get_json()
        self.project_id = body["project_id"]
        self.page_ids = [page["id"] for page in self.pages(self.project_id)]

    def url(self, page_id, variant="thumb"):
        return f"/projects/{self.project_id}/pages/{page_id}/image/{variant}"

    def test_a_thumbnail_is_a_small_private_cached_jpeg(self):
        response = self.client.get(self.url(self.page_ids[0]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/jpeg")
        image = Image.open(io.BytesIO(response.data))
        self.assertLessEqual(max(image.size), 360)
        self.assertGreater(image.width, image.height, "the scanner's 90° turn is applied")
        self.assertLess(len(response.data), 60_000)
        cache = response.headers["Cache-Control"]
        self.assertIn("private", cache)
        self.assertIn("max-age=3600", cache)
        self.assertNotIn("no-store", cache)
        etag = response.headers["ETag"]
        again = self.client.get(self.url(self.page_ids[0]), headers={"If-None-Match": etag})
        self.assertEqual(again.status_code, 304)
        # The original and processed pictures stay uncached.
        self.assertIn("no-store", self.client.get(self.url(self.page_ids[0], "original")).headers["Cache-Control"])

    def test_a_pdf_page_has_a_thumbnail_too(self):
        response = self.client.get(self.url(self.page_ids[1]))
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(max(Image.open(io.BytesIO(response.data)).size), 360)

    def test_only_the_owner_sees_a_thumbnail(self):
        other = self.register("tom")
        self.assertEqual(other.get(self.url(self.page_ids[0])).status_code, 404)
        self.assertEqual(self.client.get(self.url(999999)).status_code, 404)
        self.assertEqual(self.client.get(self.url(self.page_ids[0], "huge")).status_code, 404)
        self.assertEqual(application.app.test_client().get(self.url(self.page_ids[0])).status_code, 302,
                         "not logged in: the login page")

    def test_the_workspace_shows_every_page_as_a_lazy_thumbnail(self):
        html = self.client.get(f"/projects/{self.project_id}").get_data(as_text=True)
        self.assertIn('id="projectPageGallery"', html)
        for page_id in self.page_ids:
            self.assertIn(f'/pages/{page_id}/image/thumb" alt="" width="270" height="360" loading="lazy"', html)
            self.assertIn(f'/pages/{page_id}/delete"', html, "no topics yet: each page can be deleted")
            self.assertIn(f'name="replacement_{page_id}"', html)
        self.assertIn(f'data-reorder-url="/projects/{self.project_id}/pages/reorder"', html)
        self.assertIn('id="addPagesForm"', html)
        self.assertIn("js/project-pages.js", html)
        self.assertIn('capture="environment"', html, "a separate camera button")

    def test_once_topics_exist_pages_are_not_deletable_and_the_page_says_why(self):
        with application.app.app_context():
            project = db.session.get(application.LearningProject, self.project_id)
            db.session.add(application.LearningSection(project_id=project.id, position=1, title="Photosynthese",
                                                       main_topic="Photosynthese", topic_key="photosynthese",
                                                       source_page_ids_json=json.dumps(self.page_ids)))
            db.session.commit()
        html = self.client.get(f"/projects/{self.project_id}").get_data(as_text=True)
        self.assertNotIn(f'/pages/{self.page_ids[0]}/delete"', html)
        self.assertIn("Pages cannot be deleted once the topics exist", html)
        self.client.post("/settings/language", data={"language": "de"})
        refused = self.client.post(f"/projects/{self.project_id}/pages/{self.page_ids[0]}/delete",
                                   follow_redirects=True)
        self.assertIn("Sobald es Themen gibt, kann eine Seite nicht mehr entfernt werden", refused.get_data(as_text=True))
        self.assertEqual(len(self.pages(self.project_id)), 2)


if __name__ == "__main__":
    unittest.main()
