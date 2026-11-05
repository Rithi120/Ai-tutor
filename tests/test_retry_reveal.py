"""PF4 L1/L2/L11: "Try again" never shows or ships the answer (offline, every model call mocked).

The live run of 2026-11-01 (scratchpad prod/live_lessons.md §3) showed the answer on the Try
again screen in 4 of 6 subjects: the evaluation feedback ("…also Option **b**"), the diagnosis
panel ("Die richtige Antwort ist b, weil…", 'Die richtige Form ist "I saw him yesterday"'), a
paraphrase of the right option (History) and a teacher tip in the JSON ("Sonne = Idee des
Guten"). `tests/fixtures/retry_reveal_2026-11-01.json` holds the six questions and the exact
texts the page received (synthetic notes written for that test, no student data).

Pinned here:
* the detector (`reveals_answer`, and `retry_reveals` with the paraphrase guard for the retry
  fields) finds every leak of the live run and still passes the teaching texts that did not leak;
* a retry response carries only the generic line, the scrubbed retry fields and a diagnosis
  without explanation (no teacher_tip, no exception_note); without a surviving retry field
  the answer is graded as usual with the correction;
* an answer the grader backs with a sentence of the student's source is not retried (L2);
* an `insufficient_evidence` diagnosis shows no explanation (L11).
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_retry_reveal_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.lessons import lesson_context  # noqa: E402
from tests import test_lesson_teaching_engine as teaching  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "retry_reveal_2026-11-01.json"
CASES = {case["subject"]: case for case in json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]}
GENERIC_RETRY = "Not quite yet. Read the explanation below and try the same question again."

# What the live Try again screen (or its JSON) gave away, per subject and field.
LIVE_LEAKS = {
    "math": ("evaluation.feedback", "diagnosis.explanation"),
    "ethics": ("evaluation.teacher_tip",),
    "english": ("diagnosis.explanation",),
    "history": ("evaluation.feedback", "diagnosis.explanation", "retry.reteach"),
    "geography": ("evaluation.feedback", "diagnosis.explanation", "retry.reteach", "retry.simpler_example"),
}
# Teaching texts of the same run that do not give the answer away: they must survive.
LIVE_KEPT = {
    "math": ("retry.misunderstanding", "retry.reteach", "retry.simpler_example"),
    "ethics": ("retry.misunderstanding", "retry.reteach", "retry.simpler_example"),
    "english": ("retry.misunderstanding", "retry.reteach", "retry.simpler_example"),
    "nwt": ("retry.misunderstanding", "retry.reteach"),
    "history": ("retry.misunderstanding",),
    "geography": ("retry.misunderstanding",),
}

# Review B B-3: the questions of the reviewer's adversarial probe.
SLOPE = CASES["math"]["question"]                     # multiple choice, b = "2"
SUN = CASES["ethics"]["question"]                     # multiple choice, b = "Die Idee des Guten"
UTILITY = {"type": "checkboxes", "prompt": "Welche Aussagen über den Utilitarismus stimmen? (mehrere)",
           "options": [{"id": "a", "label": "Folgen zählen"}, {"id": "b", "label": "Pflicht zählt"},
                       {"id": "c", "label": "Größtes Glück der größten Zahl"},
                       {"id": "d", "label": "Absichten zählen allein"}], "expected_answer": ["a", "c"]}
GYGES_ORDER = {"type": "ordering", "prompt": "Bringe die Schritte in die richtige Reihenfolge.",
               "options": [{"id": "a", "label": "Gyges wird unsichtbar"}, {"id": "b", "label": "Gyges findet den Ring"},
                           {"id": "c", "label": "Gyges tut ungestraft Unrecht"},
                           {"id": "d", "label": "Glaukon stellt seine Frage"}], "expected_answer": ["b", "a", "c", "d"]}
WRITTEN_SLOPE = {"type": "text", "prompt": "Berechne die Steigung der Geraden durch (3|4) und (7|12).", "options": [],
                 "expected_answer": "2"}
WRITTEN_NEGATIVE = {"type": "text", "prompt": "Bestimme b für y = 2x + b durch A(2|1).", "options": [],
                    "expected_answer": "-3"}
WRITTEN_DECIMAL = {"type": "text", "prompt": "Bestimme die Nullstelle von y = 2x + 1.", "options": [],
                   "expected_answer": "-0,5"}
SUN_FR = {"type": "multiple_choice", "prompt": "Que symbolise le soleil dans l'allégorie de la caverne ?",
          "options": [{"id": "a", "label": "Le feu"}, {"id": "b", "label": "L'idée du Bien"},
                      {"id": "c", "label": "Les ombres"}], "expected_answer": "b"}
SUN_ES = {"type": "multiple_choice", "prompt": "¿Qué simboliza el sol en la alegoría de la caverna?",
          "options": [{"id": "a", "label": "El fuego"}, {"id": "b", "label": "La idea del Bien"},
                      {"id": "c", "label": "Las sombras"}], "expected_answer": "b"}
TENSE = {"type": "dropdown", "prompt": "Wähle die Zeitform: I ___ him yesterday.",
         "options": [{"id": "1", "label": "have seen"}, {"id": "2", "label": "saw"}, {"id": "3", "label": "seen"}],
         "expected_answer": "2"}
ADVERSARIAL_LEAKS = [
    (SLOPE, "Die Antwort ist b."), (SLOPE, "The answer is b."), (SLOPE, "Die Lösung lautet b."), (SLOPE, "Wähle b."),
    (SLOPE, "Du hättest b wählen sollen."), (SLOPE, "b stimmt."), (SLOPE, "Antwort b stimmt."), (SLOPE, "Es ist b."),
    (SLOPE, "Antwort: b"), (SLOPE, "→ b"), (SLOPE, "2 ist richtig."), (SLOPE, "Die Steigung: 2."),
    (SLOPE, "Ergebnis 2"), (SLOPE, "Correct: b"),
    (SUN, "Die Sonne ist die Idee vom Guten."), (SUN, "Die Sonne steht für das Gute als Idee."),
    (SUN, "Die Sonne = Idee des Gute"),
    (UTILITY, "a und c sind richtig."), (UTILITY, "Both a and c are correct."), (UTILITY, "Die Antworten a und c stimmen."),
    (UTILITY, "Folgen zählen – das ist eine richtige Aussage."),
    (GYGES_ORDER, "Die Reihenfolge ist b, a, c, d."), (GYGES_ORDER, "b – a – c – d"), (GYGES_ORDER, "Richtig: b a c d"),
    (WRITTEN_SLOPE, "Die Steigung ist 2."), (WRITTEN_SLOPE, "m = 8/4 = 2"), (WRITTEN_SLOPE, "Das Ergebnis lautet 2."),
    (WRITTEN_NEGATIVE, "b = −3"), (WRITTEN_NEGATIVE, "Also ist b gleich –3."),
    (WRITTEN_DECIMAL, "Die Nullstelle ist −0,5."), (WRITTEN_DECIMAL, "x = -0.5"),
    (SUN_FR, "C'est la réponse b."), (SUN_FR, "Il fallait choisir b."),
    (SUN_ES, "La respuesta es b."), (SUN_ES, "Debiste elegir la b."),
    (TENSE, "Simple Past: saw."), (TENSE, "Richtig wäre saw."), (TENSE, "I saw him yesterday."),
]
# Teaching texts that do not give the answer away (the probe's, and the ways a reteach talks
# about options, numbers and steps without naming the right one).
LEGITIMATE = [
    (SLOPE, "Für die Punkte (1|2) und (4|5) ist die Steigung 1."), (SLOPE, "Rechne zuerst 9 - 1 und dann 6 - 2."),
    (SLOPE, "Die Steigung 4 ist zu groß, du hast nicht geteilt."), (SLOPE, "In Schritt 2 teilst du."),
    (SLOPE, "Die Antwort ist noch nicht vollständig."), (SLOPE, "Prüfe jede Antwort einzeln, bevor du wählst."),
    (SLOPE, "Die Antwort d ist falsch, weil du die Differenzen vertauscht hast."), (SLOPE, "b stimmt nicht."),
    (SLOPE, "Schritt 2: Teile die Differenz der y-Werte durch die der x-Werte."),
    (SLOPE, "Die Steigung 2 ist falsch."), (SLOPE, "Rechne nach: Ist die Antwort plausibel?"),
    (SUN, "Die Sonne steht für die höchste Wahrheit, das Feuer nur für die Schatten."),
    (SUN, "Denk daran, was außerhalb der Höhle alles sichtbar macht."),
    (UTILITY, "a ist leider nicht richtig: Es kommt auf die Folgen an."),
    (GYGES_ORDER, "Denk daran, was zuerst passieren muss, bevor Gyges unsichtbar werden kann."),
    (WRITTEN_SLOPE, "Teile die Differenz der y-Werte durch die der x-Werte."),
    (WRITTEN_SLOPE, "Für (0|0) und (1|3) ist die Steigung 3."),
    (WRITTEN_NEGATIVE, "Setze die Koordinaten von A ein: 1 = 2 · 2 + b."),
    (WRITTEN_DECIMAL, "Setze y = 0 und löse nach x auf."),
    (TENSE, "Achte auf das Signalwort: Das Ergebnis ist jetzt wichtig."),
    (TENSE, "Yesterday ist ein Signalwort für das Simple Past."),
    (SUN_FR, "Pense à ce qui éclaire tout hors de la caverne."),
    (SUN_ES, "Piensa en lo que ilumina todo fuera de la caverna."),
]


class DetectorTests(unittest.TestCase):
    """live_lessons §3: every row was False on the detector of 2026-11-01."""

    def test_the_rows_of_the_live_report_are_detected(self):
        rows = [
            ("geography", "Die richtige Antwort ist b."),
            ("math", "Damit ist die Steigung 2, also Option **b**."),
            ("math", "Damit ist die richtige Steigung 2, also Option **b**."),
            ("english", 'Die richtige Form ist "I saw him yesterday".'),
            ("ethics", "Sonne = Idee des Guten"),
        ]
        for subject, text in rows:
            with self.subTest(subject=subject, text=text):
                self.assertTrue(lesson_context.reveals_answer(text, CASES[subject]["question"]))

    def test_every_live_leak_is_detected(self):
        for subject, fields in LIVE_LEAKS.items():
            case = CASES[subject]
            for field in fields:
                with self.subTest(subject=subject, field=field):
                    self.assertTrue(lesson_context.retry_reveals(case["texts"][field], case["question"]),
                                    case["texts"][field][:160])

    def test_the_history_paraphrase_is_caught_by_the_paraphrase_guard_only(self):
        case = CASES["history"]
        feedback = case["texts"]["evaluation.feedback"]
        self.assertFalse(lesson_context.reveals_answer(feedback, case["question"]),
                         "the label itself is not in the text: only the paraphrase guard sees it")
        self.assertTrue(lesson_context.retry_reveals(feedback, case["question"]))

    def test_the_teaching_texts_that_did_not_leak_survive(self):
        for subject, fields in LIVE_KEPT.items():
            case = CASES[subject]
            for field in fields:
                with self.subTest(subject=subject, field=field):
                    self.assertFalse(lesson_context.retry_reveals(case["texts"][field], case["question"]),
                                     case["texts"][field][:160])

    def test_option_ids_with_markdown_brackets_and_inflections(self):
        question = teaching.QUESTION          # correct option "a"
        for text in ("Die richtige Antwort ist a.", "Richtig wäre Option **a**.", "Also Antwort (a).",
                     "Die korrekte Lösung ist „a“.", "Deshalb Option `a`, weil Gyges unsichtbar ist.",
                     "The correct option is a, because nobody sees Gyges."):
            with self.subTest(text=text):
                self.assertTrue(lesson_context.reveals_answer(text, question))
        for text in ("b ist leider falsch.", "The correct answer is a statement about justice.",
                     "Das ist richtig gedacht, aber unvollständig.", "So a student could think that.",
                     "Richtig erkannt hast du, dass a eine Aussage über Gyges ist."):
            with self.subTest(text=text):
                self.assertFalse(lesson_context.reveals_answer(text, question))

    def test_short_numeric_labels_need_a_statement(self):
        question = CASES["math"]["question"]          # correct option b = "2"
        for text in ("Die Steigung ist 2.", "Also gilt $$m=\\frac{8}{4}=2$$", "Die richtige Steigung beträgt 2, "
                     "nicht 4.", "Die Steigung der Geraden ist 2, nicht 4."):
            with self.subTest(text=text):
                self.assertTrue(lesson_context.reveals_answer(text, question))
        for text in ("Für die Punkte (1|2) und (4|5) ist die Steigung: $$m=\\frac{5-2}{4-1}=\\frac{3}{3}=1$$",
                     "Der erste Schritt ist leicht. Schritt 2: teile die Differenzen.",
                     "Die Steigung 2 ist falsch.", "Rechne 9 - 1 und 6 - 2 aus."):
            with self.subTest(text=text):
                self.assertFalse(lesson_context.reveals_answer(text, question))
        written = {"type": "text", "options": [], "expected_answer": "3"}
        self.assertFalse(lesson_context.reveals_answer("x = 3 ist falsch", written), "choice questions only")

    def test_labels_without_their_article_or_with_punctuation_outside_quotes(self):
        question = CASES["ethics"]["question"]        # "Die Idee des Guten"
        self.assertTrue(lesson_context.reveals_answer("Die Sonne steht für die „Idee des Guten“.", question))
        self.assertTrue(lesson_context.reveals_answer("Sonne → *Idee des Guten*", question))
        self.assertFalse(lesson_context.reveals_answer("Die Sonne steht für die höchste Wahrheit.", question))

    def test_the_paraphrase_guard_needs_three_new_words(self):
        question = {"type": "multiple_choice", "prompt": "Welches Material dämmt am besten?",
                    "options": [{"id": "a", "label": "Styropor"}, {"id": "b", "label": "Wolle"}],
                    "expected_answer": "a"}
        self.assertFalse(lesson_context.retry_reveals(
            "Das Material mit der höchsten Endtemperatur hat die beste Dämmwirkung.", question))

    def test_the_reviewers_adversarial_phrasings_are_detected(self):
        # Review B B-3 (prodrev_b/probes/adversarial_reveal.py): each of these was missed.
        for question, text in ADVERSARIAL_LEAKS:
            with self.subTest(text=text):
                self.assertTrue(lesson_context.retry_reveals(text, question), text)

    def test_legitimate_reteaches_survive(self):
        for question, text in LEGITIMATE:
            with self.subTest(text=text):
                self.assertFalse(lesson_context.retry_reveals(text, question), text)

    def test_quotes_source(self):
        source = ("[Blatt, p. 1] Der Reichspräsident darf vorübergehend die festgesetzten Grundrechte ganz "
                  "oder zum Teil außer Kraft setzen.")
        self.assertTrue(lesson_context.quotes_source(
            'Deine Antwort passt: „die festgesetzten Grundrechte ganz oder zum Teil außer Kraft setzen“.', source))
        self.assertFalse(lesson_context.quotes_source("Die Grundrechte werden außer Kraft gesetzt.", source),
                         "five shared words in another order are no quote")
        self.assertFalse(lesson_context.quotes_source("Irgendein Text", ""))


class RetryPayloadTests(teaching.TeachingEngineCase):
    """Through the real /api/answer, with the model mocked."""

    def setUp(self):
        super().setUp()
        _, self.section_id, _ = self.make_project("Ethik", [("Ethik-Blatt.pdf", [teaching.GYGES])],
                                                  teaching.ethics_topic)
        self.lesson_parts = [teaching.part("objective", "Ziel."), teaching.part("thought_experiment", "Gyges ..."),
                             teaching.part("guided_exercise", "Erkläre."), teaching.part("summary", "Kurz.")]
        self.session_id = self.start(self.section_id)

    def evaluation(self, score, **kwargs):
        payload = teaching.evaluation(score, **kwargs)
        payload["evaluation"]["teacher_tip"] = "Merke dir: Niemand ist freiwillig gerecht."
        payload["evaluation"]["exception_note"] = "Eine Ausnahme gibt es nicht."
        return payload

    def assert_retry_payload(self, data):
        self.assertTrue(data["retry"]["allowed"])
        self.assertEqual(set(data["evaluation"]), {"is_correct", "score", "feedback", "skill_status"})
        self.assertEqual(data["evaluation"]["feedback"], GENERIC_RETRY)
        diagnosis = data["diagnosis"]
        self.assertEqual(diagnosis["explanation"], "")
        self.assertFalse(diagnosis["missing_evidence"])
        self.assertEqual(diagnosis["missing_evidence_reason"], "")
        for key in ("statement", "misconception", "recommended_intervention"):
            self.assertEqual(diagnosis["detail"][key], "", key)
        self.assertEqual(diagnosis["detail"]["rubric"], [])
        self.assertNotIn("attempt_id", diagnosis)

    def test_a_retry_sends_only_the_generic_line_and_the_retry_fields(self):
        self.diagnosis_explanation = "Schau dir an, was der Hirte tut."
        self.answers = [self.evaluation(20, feedback="Schau dir an, was Gyges tut, wenn ihn niemand sieht.",
                                        retry=dict(teaching_retry()))]
        data = self.answer(self.session_id, "b")
        self.assert_retry_payload(data)
        self.assertEqual(data["retry"]["misunderstanding"], teaching_retry()["misunderstanding"])
        self.assertNotIn("Niemand ist freiwillig gerecht", json.dumps(without_question(data), ensure_ascii=False),
                         "the option labels are the question itself; nothing else names the answer")

    def test_feedback_alone_is_no_retry_and_the_correction_is_shown(self):
        self.answers = [self.evaluation(20, feedback="Schau dir an, was Gyges tut, wenn ihn niemand sieht.",
                                        correction="Richtig ist a: Niemand ist freiwillig gerecht.")]
        data = self.answer(self.session_id, "b")
        self.assertFalse(data.get("retry"))
        self.assertIn("Niemand ist freiwillig gerecht", data["evaluation"]["correction"])
        self.assertTrue(data["next_question"])

    def test_the_why_panel_of_a_pending_retry_has_no_explanation(self):
        self.diagnosis_explanation = "Schau dir an, was der Hirte tut."
        self.answers = [self.evaluation(20, retry=dict(teaching_retry()))]
        self.assertTrue(self.answer(self.session_id, "b")["retry"]["allowed"])
        with application.app.app_context():
            attempt_id = teaching.db.session.scalar(teaching.db.select(application.Attempt.id))
        detail = self.client.get(f"/api/diagnosis/{attempt_id}").get_json()["diagnosis"]
        self.assertEqual(detail["explanation"], "")
        self.assertEqual(detail["detail"]["statement"], "")

    def replay(self, subject, *, keep):
        """The live first wrong answer of `subject`, replayed through the route."""

        case = CASES[subject]
        application.SESSIONS[self.session_id]["current_question"] = {
            **case["question"], "hint": "Ein Hinweis.", "difficulty": 1, "subject": "Ethics"}
        texts = case["texts"]
        self.diagnosis_explanation = texts.get("diagnosis.explanation", "")
        payload = teaching.evaluation(0, feedback=texts["evaluation.feedback"], correction="Lösung.", retry={
            "misunderstanding": texts.get("retry.misunderstanding", ""), "reteach": texts.get("retry.reteach", ""),
            "simpler_example": texts.get("retry.simpler_example", "")})
        payload["evaluation"]["teacher_tip"] = texts.get("evaluation.teacher_tip", "")
        self.answers = [payload]
        data = self.answer(self.session_id, case["student_answer"])
        self.assert_retry_payload(data)
        shown = {key: value for key, value in data["retry"].items() if key in ("misunderstanding", "reteach",
                                                                               "simpler_example") and value}
        self.assertEqual(sorted(shown), sorted(keep))
        for text in [data["evaluation"]["feedback"], data["diagnosis"]["explanation"], *shown.values()]:
            self.assertFalse(lesson_context.retry_reveals(text, case["question"]), text[:160])
        return data

    def test_the_live_math_retry_keeps_its_teaching_and_drops_the_answer(self):
        data = self.replay("math", keep=("misunderstanding", "reteach", "simpler_example"))
        self.assertNotIn("Option **b**", json.dumps(without_question(data), ensure_ascii=False))

    def test_the_live_history_retry_keeps_only_the_misunderstanding(self):
        data = self.replay("history", keep=("misunderstanding",))
        self.assertNotIn("ohne Zustimmung des Reichstags", json.dumps(without_question(data), ensure_ascii=False))

    def test_the_live_geography_retry_keeps_only_the_misunderstanding(self):
        data = self.replay("geography", keep=("misunderstanding",))
        self.assertNotIn("richtige Antwort ist b", json.dumps(without_question(data), ensure_ascii=False))

    def test_a_source_backed_answer_is_graded_instead_of_retried(self):
        # L2: the grader quotes the student's notes for the answer; the key may be wrong, so the
        # retry (which would reteach against the student's own source) is not offered.
        feedback = ('Teilweise richtig. In deinen Notizen steht: „Er nutzt ihn, um ungestraft Unrecht zu tun.“ '
                    "Das passt zu deiner Wahl.")
        self.answers = [self.evaluation(60, feedback=feedback, correction="Richtig ist a.",
                                        retry=dict(teaching_retry()))]
        data = self.answer(self.session_id, "b")
        self.assertFalse(data.get("retry"))
        self.assertEqual(data["evaluation"]["feedback"], feedback)
        self.assertTrue(data["evaluation"]["correction"])

    def test_a_low_score_with_a_quote_is_still_retried(self):
        feedback = 'In deinen Notizen steht: „Er nutzt ihn, um ungestraft Unrecht zu tun.“'
        self.answers = [self.evaluation(20, feedback=feedback, retry=dict(teaching_retry()))]
        self.assertTrue(self.answer(self.session_id, "b")["retry"]["allowed"])

    def stored_feedback(self):
        with application.app.app_context():
            return [attempt.feedback for attempt in teaching.db.session.scalars(
                teaching.db.select(application.Attempt).order_by(application.Attempt.id))]

    def pages_shown(self):
        with application.app.app_context():
            lesson_id = teaching.db.session.scalar(teaching.db.select(application.Lesson.id).where(
                application.Lesson.session_id == self.session_id))
        return [self.client.get(path).get_data(as_text=True) for path in ("/dashboard", f"/lessons/{lesson_id}",
                                                                           "/insights/mistakes")]

    def test_the_first_try_keeps_the_graders_feedback_once_the_retry_is_answered(self):
        # Review B B-4: the first try of a retried question was stored with the generic line, so
        # the Mistakes card, the lesson history, similar-mistake practice and the adaptive
        # context lost the grader's explanation for good.
        real = "Die Pointe (Merkwort Kronjuwel): Niemand ist freiwillig gerecht."
        self.answers = [self.evaluation(20, feedback=real, retry=dict(teaching_retry())),
                        self.evaluation(30, feedback="Noch nicht ganz.")]
        data = self.answer(self.session_id, "b")
        self.assert_retry_payload(data)
        self.assertNotIn("Kronjuwel", json.dumps(data, ensure_ascii=False))
        # While the second try is open nothing stored or shown has it.
        self.assertEqual(self.stored_feedback(), [GENERIC_RETRY])
        for page in self.pages_shown():
            self.assertNotIn("Kronjuwel", page)
        self.answer(self.session_id, "c", retry_count=1)
        self.assertEqual(self.stored_feedback(), [real, "Noch nicht ganz."])
        self.assertTrue(any("Kronjuwel" in page for page in self.pages_shown()), "the history shows it now")

    def test_the_lesson_chat_never_sees_the_diagnosis_while_the_retry_is_open(self):
        # The session history feeds the lesson chat's recent_results. Its diagnosis_note is the
        # grader's student-facing explanation, which can state the answer, so a student who
        # asked the chat during the second try could be told it.
        self.diagnosis_explanation = "Richtig ist a: Niemand ist freiwillig gerecht (Kronjuwel)."
        self.answers = [self.evaluation(20, retry=dict(teaching_retry())),
                        self.evaluation(30, feedback="Noch nicht ganz.")]
        self.assertTrue(self.answer(self.session_id, "b")["retry"]["allowed"])
        history = application.SESSIONS[self.session_id]["history"]
        self.assertNotIn("Kronjuwel", json.dumps(history, ensure_ascii=False))
        self.answer(self.session_id, "c", retry_count=1)
        self.assertEqual(len(application.SESSIONS[self.session_id]["history"]), 2)

    def test_finishing_with_the_retry_open_keeps_the_graders_feedback(self):
        real = "Die Pointe (Merkwort Kronjuwel): Niemand ist freiwillig gerecht."
        self.answers = [self.evaluation(20, feedback=real, retry=dict(teaching_retry()))]
        self.assertTrue(self.answer(self.session_id, "b")["retry"]["allowed"])
        application.SESSIONS[self.session_id]["min_questions"] = 1
        application.app.config["TEST_MIN_QUESTIONS"] = 1
        response = self.client.post("/api/finish", json={"session_id": self.session_id})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
        self.assertEqual(self.stored_feedback(), [real], "the test is over: the retry is resolved")


class InsufficientEvidenceTests(teaching.TeachingEngineCase):
    def respond(self, *, task_type, **kwargs):
        if task_type == "answer_diagnosis":
            self.calls.append({"task_type": task_type, **kwargs})
            payload = teaching.diagnosis(False, "Die Steigung ist 2, nicht 4. Du hast vertauscht.")
            payload["correctness_status"] = "insufficient_evidence"
            payload["primary_diagnosis"] = {"tag": "insufficient_evidence", "statement": "", "evidence_ids": []}
            payload["missing_evidence"] = True
            payload["missing_evidence_reason"] = "Zu wenig Rechenweg."
            return teaching.FakeResponse(payload)
        return super().respond(task_type=task_type, **kwargs)

    def test_an_insufficient_evidence_diagnosis_shows_no_explanation(self):
        _, section_id, _ = self.make_project("Ethik", [("Ethik-Blatt.pdf", [teaching.GYGES])], teaching.ethics_topic)
        self.lesson_parts = [teaching.part("objective", "Ziel."), teaching.part("summary", "Kurz.")]
        session_id = self.start(section_id)
        application.SESSIONS[session_id]["teaching_first"] = False       # graded at once, no retry
        self.answers = [teaching.evaluation(20)]
        data = self.answer(session_id, "b")
        self.assertIsNone(data["retry"])
        self.assertEqual(data["diagnosis"]["correctness_status"], "insufficient_evidence")
        self.assertEqual(data["diagnosis"]["explanation"], "", "a guess is not shown as the reason")
        self.assertTrue(data["diagnosis"]["missing_evidence"], "what is missing is still said")


def without_question(data):
    """The response without the public question of the retry (its options are on screen anyway)."""

    copy = json.loads(json.dumps(data))
    copy["retry"].pop("question", None)
    return copy


def teaching_retry():
    return {"misunderstanding": "Du hast übersehen, dass Gyges ungestraft handeln kann.",
            "reteach": "Stell dir vor, niemand sieht dich: Würdest du dich anders verhalten?",
            "simpler_example": "Ein Kind nimmt Süßigkeiten, wenn die Mutter nicht hinsieht."}


if __name__ == "__main__":
    unittest.main()
