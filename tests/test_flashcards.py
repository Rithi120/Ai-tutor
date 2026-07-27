import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_flashcards_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.flashcards import service as flashcards  # noqa: E402
from learnova.ai_services.contracts import validate_output, AIValidationError  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self.output_text = json.dumps(payload)
        self.usage = None
        self.model = "test-model"


GENERATED = {
    "title": "Cell Biology Basics",
    "cards": [
        {"type": "question_answer", "front": "What is the main function of mitochondria?",
         "back": "They produce most of the cell's ATP.", "explanation": "Cellular respiration.",
         "hint": "energy", "tags": ["cells", "energy"], "sourceReference": "Page 4", "difficulty": "medium"},
        {"type": "term_definition", "front": "Define nucleus",
         "back": "The control centre of the cell.", "difficulty": "easy"},
        {"type": "question_answer", "front": "What is the main function of mitochondria?",
         "back": "duplicate front should be dropped", "difficulty": "medium"},
        {"type": "multiple_choice", "front": "ATP stands for?", "back": "Adenosine triphosphate",
         "options": ["Adenosine triphosphate", "Adenine", "ATPase"], "difficulty": "medium"},
        {"type": "question_answer", "front": "", "back": "missing front should be dropped"},
    ],
}


class FlashcardServiceTests(unittest.TestCase):
    def test_normalize_drops_invalid_and_duplicate_cards(self):
        cards = flashcards.normalize_cards(GENERATED["cards"], limit=10)
        fronts = [card["front"] for card in cards]
        self.assertEqual(len(cards), 3)  # duplicate + empty-front removed
        self.assertEqual(fronts.count("What is the main function of mitochondria?"), 1)
        multiple_choice = next(card for card in cards if card["type"] == "multiple_choice")
        self.assertEqual(len(multiple_choice["options"]), 3)

    def test_normalize_respects_limit_and_type_fallback(self):
        cards = flashcards.normalize_cards(GENERATED["cards"], limit=1)
        self.assertEqual(len(cards), 1)
        weird = flashcards.normalize_cards([{"type": "nonsense", "front": "a", "back": "b"}], limit=5)
        self.assertEqual(weird[0]["type"], "question_answer")

    def test_new_schedule_is_due_now_and_new(self):
        now = datetime(2026, 7, 21, tzinfo=timezone.utc)
        schedule = flashcards.new_schedule(now)
        self.assertEqual(schedule["mastery_level"], "new")
        self.assertEqual(schedule["next_review_at"], now)
        self.assertEqual(schedule["repetition_count"], 0)

    def test_review_good_grows_interval_and_schedules_future(self):
        now = datetime(2026, 7, 21, tzinfo=timezone.utc)
        state = flashcards.new_schedule(now)
        first = flashcards.review(state, "good", now)
        self.assertEqual(first["interval"], 1)
        self.assertEqual(first["correct_count"], 1)
        self.assertGreater(first["next_review_at"], now)
        second = flashcards.review(first, "good", now)
        self.assertEqual(second["interval"], 6)

    def test_review_again_resets_and_counts_incorrect(self):
        now = datetime(2026, 7, 21, tzinfo=timezone.utc)
        state = flashcards.review(flashcards.new_schedule(now), "good", now)
        again = flashcards.review(state, "again", now)
        self.assertEqual(again["interval"], 0)
        self.assertEqual(again["repetition_count"], 0)
        self.assertEqual(again["incorrect_count"], 1)
        self.assertEqual(again["next_review_at"], now)  # same-session re-review

    def test_easy_interval_exceeds_hard(self):
        now = datetime(2026, 7, 21, tzinfo=timezone.utc)
        base = flashcards.review(flashcards.review(flashcards.new_schedule(now), "good", now), "good", now)
        easy = flashcards.review(base, "easy", now)
        hard = flashcards.review(base, "hard", now)
        self.assertGreater(easy["interval"], hard["interval"])

    def test_contract_rejects_cardless_and_accepts_valid(self):
        version = "flashcard_generation:v1"
        with self.assertRaises(AIValidationError):
            validate_output("flashcard_generation", json.dumps({"title": "x", "cards": []}), version, {})
        report = validate_output("flashcard_generation", json.dumps(GENERATED), version, {})
        self.assertTrue(report.valid)


class FlashcardApiTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "carol", "email": "carol@example.com", "password": "correct-horse-battery",
        })

    def test_generate_preview_then_save_review_and_delete(self):
        with patch.object(application, "create_response", return_value=FakeResponse(GENERATED)):
            generated = self.client.post("/api/flashcards/generate", json={
                "source_kind": "text", "subject": "Biology", "grade": "8",
                "difficulty": "medium", "card_type": "mixed", "count": 10,
                "text": "Mitochondria are the powerhouse of the cell and produce ATP. The nucleus controls the cell.",
            })
        self.assertEqual(generated.status_code, 200)
        preview = generated.get_json()
        self.assertTrue(preview["ok"])
        self.assertEqual(len(preview["cards"]), 3)  # deduped/validated

        saved = self.client.post("/api/flashcards/sets", json={
            "title": preview["title"], "subject": "Biology", "difficulty": "medium",
            "card_type": "mixed", "source_kind": "text", "cards": preview["cards"],
        })
        self.assertEqual(saved.status_code, 201)
        set_id = saved.get_json()["id"]

        listed = self.client.get("/api/flashcards/sets").get_json()
        self.assertEqual(len(listed["sets"]), 1)
        self.assertEqual(listed["sets"][0]["total"], 3)
        self.assertEqual(listed["sets"][0]["due"], 3)  # all new cards due now

        full = self.client.get(f"/api/flashcards/sets/{set_id}").get_json()
        card_id = full["set"]["cards"][0]["id"]

        reviewed = self.client.post(f"/api/flashcards/cards/{card_id}/review", json={"grade": "good"})
        self.assertEqual(reviewed.status_code, 200)
        card = reviewed.get_json()["card"]
        self.assertEqual(card["correct_count"], 1)
        self.assertGreaterEqual(card["interval"], 1)

        after = self.client.get("/api/flashcards/sets").get_json()["sets"][0]
        self.assertEqual(after["due"], 2)  # reviewed card scheduled to the future

        deleted = self.client.delete(f"/api/flashcards/sets/{set_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(len(self.client.get("/api/flashcards/sets").get_json()["sets"]), 0)

    def test_generate_requires_source(self):
        response = self.client.post("/api/flashcards/generate", json={"source_kind": "text", "text": ""})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "missing_source")

    def test_review_rejects_bad_grade(self):
        with patch.object(application, "create_response", return_value=FakeResponse(GENERATED)):
            self.client.post("/api/flashcards/generate", json={"source_kind": "text", "text": "x" * 60})
        saved = self.client.post("/api/flashcards/sets", json={
            "title": "T", "subject": "Biology", "cards": flashcards.normalize_cards(GENERATED["cards"], 5),
        })
        set_id = saved.get_json()["id"]
        card_id = self.client.get(f"/api/flashcards/sets/{set_id}").get_json()["set"]["cards"][0]["id"]
        bad = self.client.post(f"/api/flashcards/cards/{card_id}/review", json={"grade": "maybe"})
        self.assertEqual(bad.status_code, 400)


if __name__ == "__main__":
    unittest.main()
