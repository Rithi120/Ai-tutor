"""D2 (FIX_PLAN PF3, reading honesty): a doubtful vision reading is never "Fully analysed".

The live run of 2026-11-01 read a very messy, faint, blurred page (hw8) with a word error
rate of 0.55 and invented words ("Experimenten" for "Extremen", "folgen ziehen" for "Folgen
zählen"), yet the page confidence - the mean of the model's own block confidences - was
0.806, so it was "high", the document said "Fully analysed" and the misread line became a
gap flashcard "from your notes". These tests pin the fix:

* `reading_doubts` (learnova/ocr/service.py, pure) flags exactly that page among the 22
  stored live readings (tests/fixtures/ocr/live_sample_2026-11-01.json - synthetic
  documents made by the testers, replayed offline, no provider);
* through the real route a reading shaped like hw8 is "unclear", the page needs review and
  its document reads "Needs OCR review", never "Fully analysed"; a clean reading is read;
* flashcards and key terms from a flagged page are not presented as notes content until
  the student checks the page; after the check (saving the text as it is) they are.

Every model call is patched; no provider is reached.
"""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_reading_doubts_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

from flask_login import login_user  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

import app as application  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.ocr.service import normalize_recognition  # noqa: E402
from learnova.projects.terms import origin_identity, term_key  # noqa: E402

db = application.db
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ocr" / "live_sample_2026-11-01.json"
_MISSING = object()


def live_pages():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["pages"]


def live_page(doc):
    return next(page for page in live_pages() if page["doc"] == doc)


def doubts(blocks):
    # Imported here, not at the top: on the code before PF3 the function does not exist,
    # and the route tests below must still run (and fail on behaviour) there.
    from learnova.ocr.service import reading_doubts
    return reading_doubts(normalize_recognition({"blocks": blocks, "detected_page_number": "", "warning": ""}))


def block(content, confidence=0.95, kind="handwriting", **extra):
    return {"type": kind, "content": content, "confidence": confidence, "bbox": [], **extra}


class FakeResponse:
    usage = None
    model = "test-model"

    def __init__(self, payload):
        self.output_text = json.dumps(payload, ensure_ascii=False)


def answer_like(doc):
    """The stored live reading of a page as the vision model's answer (no boxes, so no
    close-up second look is asked for: one call per page)."""

    page = live_page(doc)
    return FakeResponse({"blocks": [block(item["content"], item["confidence"], item["type"],
                                          crossed_out=item["crossed_out"]) for item in page["blocks"]],
                         "detected_page_number": "", "warning": ""})


def png(label="Notizen"):
    image = Image.new("RGB", (900, 1200), "white")
    ImageDraw.Draw(image).text((60, 80), label, fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class LiveSampleTests(unittest.TestCase):
    """The detector against the 22 readings of the live run, replayed offline."""

    def test_the_sample_is_the_live_run(self):
        pages = live_pages()
        self.assertEqual(len(pages), 22)
        hw8 = live_page("hw8_inkfree_very_messy_faint")
        self.assertEqual((hw8["text_source"], hw8["confidence"], hw8["confidence_status"], hw8["wer"]),
                         ("vision", 0.806, "high", 0.545), "the misread page was stored as a confident reading")

    def test_only_the_misread_page_is_flagged(self):
        machine_read = [page for page in live_pages() if page["text_source"] in ("vision", "local_ocr")]
        self.assertEqual(len(machine_read), 10, "8 vision pages and 2 offline-OCR pages")
        flagged = {page["doc"] for page in machine_read if doubts(page["blocks"])}
        self.assertEqual(flagged, {"hw8_inkfree_very_messy_faint"})
        # The diagram (WER 0.79 on its page text: the labels sit in the diagram block) and
        # the table (WER 0.41: the title was dropped) are D7, not a misreading.
        for doc in ("diagram_png", "table_photo", "hw4_lucida_lowcontrast", "hw6_kristen_perspective",
                    "hw7_math_segoeprint", "hw1_inkfree_clear", "hw5_bradley_texture_rotated"):
            self.assertEqual(doubts(live_page(doc)["blocks"]), [], doc)

    def test_hw8_has_both_signals(self):
        self.assertEqual(doubts(live_page("hw8_inkfree_very_messy_faint")["blocks"]),
                         ["uncertain_words", "fragmented_text"])


class SignalTests(unittest.TestCase):
    """Each signal on its own, and what it must not mistake for a misreading."""

    def test_two_doubtful_lines_or_one_illegible_one(self):
        self.assertEqual(doubts([block("Die Zelle ist die kleinste Einheit"), block("des Lebens", 0.7),
                                 block("Zellkern und Zellmembran", 0.75)]), ["uncertain_words"])
        self.assertEqual(doubts([block("Die Zelle ist die kleinste Einheit"), block("Mitochondrien", 0.4)]),
                         ["uncertain_words"])
        self.assertEqual(doubts([block("Die Zelle ist die kleinste Einheit"), block("des Lebens", 0.7)]), [],
                         "one doubtful line is what the review labels are for")

    def test_diagram_and_crossed_out_blocks_do_not_count(self):
        self.assertEqual(doubts([block("Die Zelle ist die kleinste Einheit"), block("Kern", 0.3, "diagram"),
                                 block("falsch", 0.2, crossed_out=True)]), [])

    def test_broken_off_words(self):
        # Fragments count only on a reading the model was not sure of throughout (review B
        # B-5): one block below CONFIDENCE_HIGH is enough, as on hw8.
        self.assertEqual(doubts([block("Nicht verge:sen: Begriffe immer erklä"), block("mut", 0.75)]),
                         ["fragmented_text"])
        self.assertEqual(doubts([block("Tugendethik nach Aristoteles\nmut\nen!", 0.78)]), ["fragmented_text"])

    def test_a_confident_reading_of_short_lines_is_no_misreading(self):
        # Review B B-5 (prodrev_b/probes/doubts_adv.py): every block at 0.95, yet each was
        # "Needs OCR review" and its flashcards waited for a check.
        cases = {
            "vocabulary, one word per line": [block("Vokabeln\nder Hund\ndog\ndie Katze\ncat\ndas Ei\negg")],
            "English words as blocks": [block(text) for text in ("Unit 3", "the", "a", "an", "is", "are", "was")],
            "irregular verbs as blocks": [block(text) for text in ("go", "went", "gone", "see", "saw", "seen")],
            "French articles": [block("Les articles\nle\nla\nles\nun\nune\ndes")],
            "Spanish words": [block("Vocabulario\nsí\nno\nyo\ntú\nél")],
            "element symbols": [block("Elemente\nH\nHe\nLi\nNa\nCl")],
            "note names": [block("Tonleiter\nC\nD\nE\nF\nG")],
            "given values": [block("Gegeben:\na\nb\nc\nGesucht: x")],
            "abbreviations": [block("Merke\nz.B.\nAutos\nusw.\nbzw.")],
            "margin remarks": [block(text) for text in ("Die Zelle hat eine Membran.", "gut", "ok",
                                                        "Der Zellkern steuert die Zelle.")],
            "a header without spaces": [block("Name:Max Datum:Montag\nThema:Photosynthese\nPflanzen bilden Zucker.")],
        }
        for name, blocks in cases.items():
            with self.subTest(case=name):
                self.assertEqual(doubts(blocks), [], name)
        # The same vocabulary list read with doubt is still a list to check.
        self.assertEqual(doubts([block("Vokabeln\nder Hund\ndog\ndie Katze\ncat\ndas Ei\negg", 0.7)]),
                         ["fragmented_text"])

    def test_what_is_short_on_purpose_is_no_fragment(self):
        for text in ("Schüler:innen und Lehrer:innen lesen", "Quelle: https://example.org/a:b und www.x.de/c:d",
                     "Verhältnis a:b und x:y", "a)\nb)\n1.\n2.", "x = 5\ny = 2x + 1\nf(x)",
                     "Wasser\nH₂O"):
            self.assertEqual(doubts([block(text)]), [], text)
        for kind in ("table", "formula", "annotation"):
            self.assertEqual(doubts([block("Ja\nNein\nok", kind=kind)]), [], kind)


class Base(unittest.TestCase):
    CONFIG_KEYS = ("TESTING", "AI_MODE", "WTF_CSRF_ENABLED", "FEATURE_PRIVATE_FLASHCARDS", "RATELIMIT_ENABLED")

    def setUp(self):
        self.saved_config = {key: application.app.config.get(key, _MISSING) for key in self.CONFIG_KEYS}
        application.app.config.update(TESTING=True, AI_MODE="cached", WTF_CSRF_ENABLED=False,
                                      FEATURE_PRIVATE_FLASHCARDS=True)
        application.SESSIONS.clear()
        ai_service._RECENT_RESULTS.clear()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={"username": "nele", "email": "nele@example.com",
                                            "password": "correct-horse-battery", "language": "en"})
        with application.app.app_context():
            user = db.session.scalar(db.select(application.User).where(application.User.username == "nele"))
            assert user is not None
            self.user_id = user.id

    def tearDown(self):
        for key, value in self.saved_config.items():
            if value is _MISSING:
                application.app.config.pop(key, None)
            else:
                application.app.config[key] = value

    def upload_photo(self, name="wiederholung.png"):
        response = self.client.post("/projects", data={
            "title": "Ethik", "subject": "Other", "subject_other": "Ethik",
            "materials": [(io.BytesIO(png()), name, "image/png")]}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:300])
        with application.app.app_context():
            project = db.session.scalars(db.select(application.LearningProject).order_by(
                application.LearningProject.id.desc())).first()
            assert project is not None
            page = db.session.scalars(db.select(application.ProjectPage).where(
                application.ProjectPage.project_id == project.id)).first()
            assert page is not None
            return project.id, page.id

    def recognize(self, project_id, page_id, answer):
        with patch.object(application, "create_response", return_value=answer) as mocked:
            response = self.client.post(f"/projects/{project_id}/pages/{page_id}/recognize", json={})
        return response, mocked

    def analysed_with_topic(self, project_id, page_id, topic_map):
        """Record the page as analysed and covered by one topic (what a topic analysis leaves)."""

        with application.app.app_context():
            project = db.session.get(application.LearningProject, project_id)
            assert project is not None
            project.analysis_json = json.dumps({"analysis_version": 4, "analysed_page_ids": [page_id],
                                                "failed_page_ids": [], "not_analysed_page_ids": []})
            project.status, project.topics_confirmed = "planned", True
            db.session.add(application.LearningSection(
                project_id=project_id, position=1, title="Normative Ethik", main_topic="Ethik",
                source_page_ids_json=json.dumps([page_id]), topic_map_json=json.dumps(topic_map),
                topic_key="ethik-normativ"))
            db.session.commit()

    def report(self, project_id):
        response = self.client.get(f"/projects/{project_id}/analysis")
        self.assertEqual(response.status_code, 200)
        return response.get_json()


TELEOLOGY = {"term": "teleologisch", "definition": "folgen ziehen", "simple_definition": "", "example": "",
             "page_id": None, "page_ids": [], "quote": "teleologisch = folgen ziehen", "in_notes": True,
             "definition_from_notes": True, "key": term_key("teleologisch")}


def topic_map_on(page_id):
    entry = {**TELEOLOGY, "page_id": page_id, "page_ids": [page_id]}
    return {"summary": "Wiederholung.", "terms": [entry],
            "items": [{"kind": "theory", "name": "Tugendethik", "detail": "Mitte zwischen Feigheit und Übermut",
                       "page_id": page_id, "in_notes": True}], "exam_questions": []}


class RouteTests(Base):
    def test_a_reading_like_hw8_needs_review_and_is_never_fully_analysed(self):
        project_id, page_id = self.upload_photo()
        response, mocked = self.recognize(project_id, page_id, answer_like("hw8_inkfree_very_messy_faint"))
        self.assertEqual(mocked.call_count, 1)
        body = response.get_json()
        self.assertEqual((response.status_code, body["state"], body["text_source"]), (200, "needs_review", "vision"))
        self.assertEqual(body["file"]["reading_message"], "Document 1: Needs OCR review")
        with application.app.app_context():
            page = db.session.get(application.ProjectPage, page_id)
            assert page is not None
            self.assertEqual((page.extraction_status, page.confidence_status), ("ready", "unclear"))
            self.assertLessEqual(page.recognition_confidence, 0.79, "never a confident reading")
            self.assertIn("Some words on this page may be misread", page.warning)
            saved = json.loads(page.recognition_json)
            self.assertEqual(saved["doubts"], ["uncertain_words", "fragmented_text"])
            self.assertEqual(saved["variant"], "enhanced")
            self.assertIn("teleologisch = folgen ziehen", page.extracted_text, "the reading itself is kept")
        # After a topic analysis covered the page: still not "Fully analysed".
        self.analysed_with_topic(project_id, page_id, topic_map_on(page_id))
        file = self.report(project_id)["files"][0]
        self.assertEqual(file["state"], "needs_ocr_review")
        self.assertEqual(file["state_label"], "Needs OCR review")
        self.assertNotEqual(file["state_label"], "Fully analysed")
        workspace = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        self.assertIn("Some of your pages need a look", workspace)

    def test_a_clean_reading_is_read(self):
        project_id, page_id = self.upload_photo()
        response, mocked = self.recognize(project_id, page_id, answer_like("hw1_inkfree_clear"))
        body = response.get_json()
        self.assertEqual((response.status_code, body["state"], mocked.call_count), (200, "read", 1))
        with application.app.app_context():
            page = db.session.get(application.ProjectPage, page_id)
            assert page is not None
            self.assertEqual(page.confidence_status, "high")
            self.assertNotIn("misread", page.warning)
            self.assertNotIn("doubts", json.loads(page.recognition_json))
        self.analysed_with_topic(project_id, page_id, topic_map_on(page_id))
        self.assertEqual(self.report(project_id)["files"][0]["state_label"], "Fully analysed")


class ProvenanceTests(Base):
    """Nothing a misreading produced is shown as the notes' own until the page is checked."""

    def flagged_project(self):
        project_id, page_id = self.upload_photo()
        self.recognize(project_id, page_id, answer_like("hw8_inkfree_very_messy_faint"))
        self.analysed_with_topic(project_id, page_id, topic_map_on(page_id))
        return project_id, page_id

    def sync(self, project_id):
        with application.app.test_request_context():
            user = db.session.get(application.User, self.user_id)
            login_user(user)
            project = db.session.get(application.LearningProject, project_id)
            summary = application.sync_project_flashcards(project)
            db.session.commit()
            return summary

    def identities(self, project_id):
        with application.app.app_context():
            sets = db.session.scalars(db.select(application.FlashcardSet).where(
                application.FlashcardSet.project_id == project_id)).all()
            return {origin_identity(card.origin_key) for flashcard_set in sets for card in flashcard_set.cards
                    if card.origin_key}

    def test_no_card_from_the_flagged_page_until_it_is_checked(self):
        project_id, page_id = self.flagged_project()
        summary = self.sync(project_id)
        self.assertEqual(self.identities(project_id), set(),
                         "the misread line is no gap card 'from your notes' (live: '____ = folgen ziehen')")
        self.assertGreater(summary.get("waiting_for_check", 0), 0)
        workspace = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        self.assertIn('<span class="pm-origin is-added">From a page that still needs checking</span>', workspace)
        self.assertNotIn('<span class="pm-origin">From your notes</span>', workspace)
        self.assertIn("Flashcards from these pages are made once you have checked them", workspace)

        # The student checks the page: the text is right as it stands, so it is saved unchanged.
        with application.app.app_context():
            page = db.session.get(application.ProjectPage, page_id)
            assert page is not None
            text = page.extracted_text
        response = self.client.post(f"/projects/{project_id}/pages/{page_id}/correct", data={"text": text})
        self.assertEqual(response.status_code, 302)
        identities = self.identities(project_id)
        self.assertIn(f"term:{term_key('teleologisch')}", identities, "made by the sync after the check")
        self.assertIn(f"term_fill:{term_key('teleologisch')}", identities)
        workspace = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        self.assertIn('<span class="pm-origin">From your notes</span>', workspace)
        self.assertNotIn("From a page that still needs checking", workspace)

    def test_a_card_found_on_a_checked_page_too_is_made(self):
        project_id, page_id = self.flagged_project()
        with application.app.app_context():
            project = db.session.get(application.LearningProject, project_id)
            assert project is not None
            source = project.pages[0].source_file
            other = application.ProjectPage(project_id=project_id, file_id=source.id, page_number=2, page_order=2,
                                            extracted_text="Teleologisch heißt: nach den Folgen fragen.",
                                            extraction_status="ready", confidence_status="high",
                                            recognition_confidence=0.95,
                                            recognition_json=json.dumps({"text_source": "vision"}))
            db.session.add(other)
            db.session.flush()
            section = project.sections[0]
            topic_map = json.loads(section.topic_map_json)
            topic_map["terms"][0]["page_ids"] = [page_id, other.id]
            section.topic_map_json = json.dumps(topic_map)
            db.session.commit()
        self.sync(project_id)
        self.assertIn(f"term:{term_key('teleologisch')}", self.identities(project_id))

    def test_the_review_page_confirmation_makes_the_waiting_cards(self):
        project_id, page_id = self.flagged_project()
        self.sync(project_id)
        self.assertEqual(self.identities(project_id), set())
        response = self.client.post(f"/projects/{project_id}/review", data={"action": "confirm"})
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"term:{term_key('teleologisch')}", self.identities(project_id))


if __name__ == "__main__":
    unittest.main()
