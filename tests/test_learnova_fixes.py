"""Regression tests for the Learnova stabilization pass.

Covers the concrete defects fixed in this change set:
  * crypto.randomUUID crash in study modes (shared safe helper)
  * grade not persisting on set update
  * raw LaTeX not rendered on dynamic study surfaces (KaTeX re-render sites)
  * dark mode not applied to the authenticated page background / legacy sheets
  * German/English label mixing (missing catalog entries)
  * misleading "coming soon" import text vs. the working importer
"""

import os
import tempfile
import unittest
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_fixes_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.translations import translate  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
TEMPLATES = ROOT / "templates"


def read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


class UuidFallbackStaticTests(unittest.TestCase):
    """The study engine must never call a bare crypto.randomUUID() (undefined in non-secure contexts)."""

    def test_shared_safe_uuid_helper_exists(self):
        dom = read("static", "js", "dom.js")
        self.assertIn("export function safeUUID", dom)
        # falls back to getRandomValues when randomUUID is unavailable
        self.assertIn("getRandomValues", dom)

    def test_mode_engine_uses_safe_uuid_not_bare_call(self):
        engine = read("static", "js", "flashcards", "mode-engine.js")
        self.assertIn("safeUUID()", engine)
        self.assertNotIn("crypto.randomUUID()", engine)

    def test_no_bare_random_uuid_anywhere_in_js(self):
        for path in (STATIC / "js").rglob("*.js"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(
                "crypto.randomUUID()", text,
                f"bare crypto.randomUUID() in {path.relative_to(ROOT)}")


class KatexRenderSiteTests(unittest.TestCase):
    """renderMath must run after every dynamic innerHTML update that can contain formulas."""

    def test_mode_engine_renders_math_on_dynamic_surfaces(self):
        engine = read("static", "js", "flashcards", "mode-engine.js")
        for target in ("#standardQuestion", "#matchBoard", "#resultReview", "#modeAnswer"):
            self.assertIn(f'renderMath($("{target}"))', engine, f"missing renderMath for {target}")
        self.assertIn("renderMath(feedback)", engine)

    def test_render_math_supports_all_delimiters_and_safe_fallback(self):
        common = read("static", "js", "flashcards", "common.js")
        for left in ("$$", "\\\\[", "$", "\\\\("):
            self.assertIn(f'left: "{left}"', common)
        self.assertIn("throwOnError: false", common)  # readable fallback for malformed LaTeX


class DarkModeWiringTests(unittest.TestCase):
    def test_base_links_dark_override_sheet_last(self):
        base = read("templates", "base.html")
        self.assertIn("css/learnova-dark.css", base)
        self.assertLess(base.index("learnova-pages.css"), base.index("learnova-dark.css"))

    def test_authenticated_background_is_theme_aware(self):
        motion = read("static", "css", "motion.css")
        self.assertIn(".authenticated-app", motion)
        self.assertIn("var(--ln-canvas", motion)  # no longer a hardcoded light #f6f5f1

    def test_dark_sheet_defines_dark_surfaces_and_inputs(self):
        dark = read("static", "css", "learnova-dark.css")
        self.assertIn('[data-theme="dark"]', dark)
        self.assertIn(".study-card-face", dark)
        self.assertIn(".auth-card", dark)

    def test_study_planner_dark_uses_data_theme_not_os_only(self):
        planner = read("static", "css", "study-planner.css")
        self.assertIn(':root[data-theme="dark"] .planner-page', planner)
        self.assertNotIn("prefers-color-scheme:dark){.planner-page", planner)


class LocalizationTests(unittest.TestCase):
    def test_study_mode_config_labels_are_translated_to_german(self):
        pairs = {
            "Card direction": "Kartenrichtung",
            "Front to back": "Vorderseite zu Rückseite",
            "Back to front": "Rückseite zu Vorderseite",
            "Source": "Quelle",
            "Your personal study tutor": "Dein persönlicher Lern-Tutor",
        }
        for english, german in pairs.items():
            self.assertEqual(translate(english, "de"), german)

    def test_community_sort_labels_are_translated(self):
        for english in ("Most saved", "Highest AI rating", "Trending", "Search"):
            self.assertNotEqual(translate(english, "de"), english, f"{english!r} still English in de")


class CreatorImportLinkTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "carol", "email": "carol@example.com", "password": "correct-horse-battery"})

    def test_no_coming_soon_and_links_to_importer(self):
        html = self.client.get("/flashcards/create").data
        self.assertNotIn(b"coming soon", html.lower())
        self.assertIn(b"/flashcards/import", html)


class GradePersistenceTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "dave", "email": "dave@example.com", "password": "correct-horse-battery"})

    def _cards(self):
        return [{"type": "question_answer", "front": "Q", "back": "A"}]

    def test_grade_persists_on_create_and_update(self):
        created = self.client.post("/api/flashcards/sets", json={
            "title": "Set", "subject": "Biology", "grade": "8", "cards": self._cards()})
        set_id = created.get_json()["id"]
        detail = self.client.get(f"/api/flashcards/sets/{set_id}").get_json()["set"]
        self.assertEqual(detail["grade"], "8")
        # update the grade and confirm it persists (regression: PUT previously ignored grade)
        self.client.put(f"/api/flashcards/sets/{set_id}", json={"grade": "11"})
        reloaded = self.client.get(f"/api/flashcards/sets/{set_id}").get_json()["set"]
        self.assertEqual(reloaded["grade"], "11")


class OcrPipelineTests(unittest.TestCase):
    def test_normalize_surfaces_suggested_corrections_and_uncertain_regions(self):
        from learnova.ocr.service import normalize_recognition
        result = normalize_recognition({"blocks": [
            {"type": "handwriting", "content": "recieve", "confidence": 0.4,
             "suggested_correction": "receive"},
            {"type": "printed_text", "content": "The cell", "confidence": 0.95},
        ]})
        self.assertEqual(len(result["suggested_corrections"]), 1)
        self.assertEqual(result["suggested_corrections"][0]["suggestion"], "receive")
        # the low-confidence handwriting block is flagged as an uncertain region
        self.assertTrue(any(r["content"] == "recieve" for r in result["uncertain_regions"]))
        # the high-confidence printed block is not
        self.assertFalse(any(r["content"] == "The cell" for r in result["uncertain_regions"]))

    def test_otsu_threshold_splits_a_bimodal_image(self):
        from PIL import Image
        from learnova.ocr.service import _otsu_threshold
        img = Image.new("L", (20, 10), 30)          # dark left half
        img.paste(220, (10, 0, 20, 10))             # bright right half
        cut = _otsu_threshold(img)
        self.assertTrue(30 < cut < 220)

    def test_grayscale_transform_removes_colour(self):
        import io
        from PIL import Image
        from learnova.ocr.service import preprocess_document_image
        src = Image.new("RGB", (900, 700), (200, 40, 40))  # strongly red
        buf = io.BytesIO(); src.save(buf, format="PNG")
        processed = preprocess_document_image(buf.getvalue(), {"grayscale": True})
        out = Image.open(io.BytesIO(processed.data)).convert("RGB")
        r, g, b = out.getpixel((10, 10))
        self.assertTrue(abs(r - g) <= 2 and abs(g - b) <= 2)  # channels equalised -> grayscale


if __name__ == "__main__":
    unittest.main()
