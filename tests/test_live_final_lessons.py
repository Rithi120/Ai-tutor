"""PF_FIX2 F2-F7: lesson defects of the final live run, replayed from its stored outputs (offline).

Fixture: tests/fixtures/live_final_2026-11-02/lessons.json - for each live lesson the notes as
sent, the analysed topic map, the raw lesson output, the first question shown and the raw
grading/diagnosis outputs of the answers given (scratchpad prod/live_final.md §3).

* F2 (keys against the notes). (a) A choice question whose key a notes sentence contradicts,
  or whose wrong option a notes sentence literally says while none says the key, is not asked
  as it is (lesson_context.choice_key_conflict). Measured on the live questions: none of them
  is a literal conflict - History #2's option b ("Es schränkte die Grundrechte der Bürger stark
  ein") paraphrases the source ("Grundrechte ganz oder zum Teil außer Kraft setzen"), which a
  word check cannot see - and English options are example sentences, never judged. (b) A choice
  the notes literally say is never graded 0 or retried (chosen_option_support). (c) A key code
  can compute - the slope through two given points - is repaired: Math Q1 keyed 3 is re-keyed
  to "2", the written follow-ups keyed "b" get "2" (question_spec.repair_computed_key).
* F3: a retry text that names the right option by one of its own words ("…, aber er hebt sich
  an") is withheld (retry_reveals).
* F4: a diagram part is held to the notes like the other explaining parts.
* F5: a written question keyed with an option letter and an ordering task keyed with one id
  (items "1, 2, 3, 4") are not asked (question_spec.shape_problems); the gate generates instead.
* F6: a first question that asks the guided exercise again is replaced by a question on the
  topic's Fachbegriffe (lesson_context.term_question), without another AI call.
* F7: a guided exercise without its task gets its first hint as the task (or is dropped); a
  lesson sentence that says the opposite of a notes sentence (negation flip) is not "from your
  notes". The NwT inversion ("Je schlechter … desto länger warm") is an antonym, not a negation:
  not detectable this way (pinned as such).
"""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_live_final_lessons_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.diagnostics import question_spec  # noqa: E402
from learnova.lessons import lesson_context, lesson_plan  # noqa: E402
from tests import test_lesson_teaching_engine as teaching  # noqa: E402

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "live_final_2026-11-02" / "lessons.json")
                     .read_text(encoding="utf-8"))
CASES = FIXTURE["cases"]
STRATEGY = {"nwt": "sciences", "history1": "history", "history2": "history", "geography": "geography",
            "math": "mathematics", "english": "languages"}


def notes(key):
    return "\n\n".join(page["text"] for page in CASES[key]["notes"])


def parsed(key, field):
    return ai_service.parse_json(CASES[key][field])


def retry_texts(key):
    from learnova.ai_services.contracts import repair_retry
    return repair_retry(parsed(key, "evaluation_raw_text"))


# A choice question on the live History notes that the notes decide: "ohne Sperrklausel (keine
# 5-Prozent-Hürde)". Built for the guard, as the live questions held no literal conflict.
WAHLRECHT = {"id": "q1", "concept": "Weimarer Verfassung", "difficulty": 1, "type": "multiple_choice",
             "prompt": "Was stimmt über das Wahlrecht der Weimarer Republik?", "hint": "",
             "options": [{"id": "a", "label": "Der Reichstag wurde mit einer 5-Prozent-Hürde gewählt."},
                         {"id": "b", "label": "Der Reichstag wurde ohne Sperrklausel gewählt."},
                         {"id": "c", "label": "Der Kaiser bestimmte die Abgeordneten des Reichstags."},
                         {"id": "d", "label": "Nur Männer durften den Reichstag wählen."}],
             "expected_answer": "a"}


class KeysAgainstTheNotesTests(unittest.TestCase):
    """F2 (a)/(b), measured on the stored History #2, English and Math cases."""

    def test_the_live_first_questions_hold_no_literal_conflict(self):
        for key in ("history1", "history2", "geography", "nwt", "math", "english"):
            with self.subTest(key=key):
                self.assertIsNone(lesson_context.choice_key_conflict(
                    CASES[key]["question_shown"], notes(key), strategy_key=STRATEGY[key]))

    def test_english_example_sentences_are_not_judged_as_claims(self):
        # "Richtig: I saw him yesterday." is in the notes; it is not the answer to "Welcher Satz
        # verwendet das Present Perfect korrekt?" (key c "I have seen him." is right).
        question = CASES["english"]["question_shown"]
        self.assertEqual(lesson_context.choice_key_conflict(question, notes("english"))["option"], "a",
                         "judged as a claim, the example sentence would knock out a correct question")
        self.assertIsNone(lesson_context.choice_key_conflict(question, notes("english"), strategy_key="languages"))

    def test_history2_option_b_is_a_paraphrase_the_word_check_cannot_see(self):
        question = CASES["history2"]["question_shown"]
        self.assertEqual(CASES["history2"]["answer"], "b")
        self.assertEqual(lesson_context.chosen_option_support(question, "b", notes("history2"),
                                                              strategy_key="history"), "")
        self.assertIn("Grundrechte ganz oder zum Teil außer Kraft setzen", " ".join(notes("history2").split()))

    def test_a_key_the_notes_contradict_and_a_distractor_they_state(self):
        conflict = lesson_context.choice_key_conflict(WAHLRECHT, notes("history1"), strategy_key="history")
        self.assertEqual(conflict["reason"], "key_contradicted")
        self.assertIn("ohne Sperrklausel", conflict["sentence"])
        other = {**WAHLRECHT, "expected_answer": "c"}
        conflict = lesson_context.choice_key_conflict(other, notes("history1"), strategy_key="history")
        self.assertEqual((conflict["reason"], conflict["option"]), ("distractor_supported", "b"))
        self.assertIsNone(lesson_context.choice_key_conflict({**WAHLRECHT, "expected_answer": "b"}, notes("history1"),
                                                             strategy_key="history"))

    def test_a_chosen_option_the_notes_say(self):
        sentence = lesson_context.chosen_option_support({**WAHLRECHT, "expected_answer": "c"}, "b", notes("history1"),
                                                        strategy_key="history")
        self.assertIn("ohne Sperrklausel", sentence)
        self.assertEqual(lesson_context.chosen_option_support(WAHLRECHT, "c", notes("history1")), "",
                         "an option the notes do not say")
        self.assertEqual(lesson_context.chosen_option_support({**WAHLRECHT, "expected_answer": "b"}, "b",
                                                              notes("history1")), "", "the key itself")


class ComputedKeyTests(unittest.TestCase):
    """F2 (c) and F5 on the live Math questions."""

    def test_the_live_first_question_keyed_3_is_rekeyed_to_2(self):
        question = copy.deepcopy(CASES["math"]["question_shown"])
        self.assertEqual(question["expected_answer"], "b")              # label "3"; (12-4):(7-3) = 2
        self.assertEqual(question_spec.slope_from_points(question["prompt"]), 2)
        self.assertEqual(question_spec.repair_computed_key(question), "rekeyed")
        self.assertEqual(question["expected_answer"], "a")
        self.assertEqual(question_spec.repair_computed_key(question), "", "now right")

    def test_the_live_written_follow_ups_keyed_b_get_the_computed_answer(self):
        for field in ("evaluation_raw_text", "second_evaluation_raw_text"):
            question = parsed("math", field)["next_question"]
            with self.subTest(prompt=question["prompt"]):
                self.assertEqual((question["type"], question["expected_answer"]), ("text", "b"))
                self.assertEqual(question_spec.shape_problems(question),
                                 ["a written question's expected_answer is an option letter"])
                self.assertEqual(question_spec.repair_computed_key(question), "fixed")
                self.assertEqual(question["expected_answer"], "2")
                self.assertEqual(question_spec.shape_problems(question), [])

    def test_slopes_only_when_the_question_asks_for_one(self):
        slope = question_spec.slope_from_points
        self.assertEqual(slope("Bestimme die Steigung durch P(1 | 3) und Q(4 | 9)."), 2)
        self.assertEqual(slope("Steigung der Geraden durch (−2 | 1) und (2 | −1)?"), question_spec.Fraction(-1, 2))
        self.assertEqual(slope("Find the slope through (0, 0) and (3, 1.5)."), question_spec.Fraction(1, 2))
        self.assertEqual(slope("Steigung durch (0,5 | 1) und (1,5 | 4)?"), 3)
        self.assertIsNone(slope("Bestimme die Funktionsgleichung durch (2 | 1) und (6 | 9)."))
        self.assertIsNone(slope("Steigung und y-Achsenabschnitt durch (2 | 1) und (6 | 9)?"))
        self.assertIsNone(slope("Welche Steigung hat die Gerade durch (2 | 1) und (2 | 9)?"), "vertical")
        self.assertIsNone(slope("Liegt (2 | 1) auf der Geraden?"))
        flawed = {"type": "multiple_choice", "prompt": "Welche Steigung hat die Gerade durch (3 | 4) und (7 | 12)?",
                  "options": [{"id": "a", "label": "3"}, {"id": "b", "label": "4"}], "expected_answer": "a"}
        self.assertEqual(question_spec.repair_computed_key(flawed), "flawed")

    def test_ordering_and_letter_keys(self):
        history1_next = parsed("history1", "evaluation_raw_text")["next_question"]
        self.assertEqual(len(question_spec.shape_problems(history1_next)), 2, "one id, items '1, 2, 3, 4'")
        history2_next = parsed("history2", "evaluation_raw_text")["next_question"]
        self.assertEqual(question_spec.shape_problems(history2_next), [],
                         "a,d,b,c is an order of all items (its subjectivity is not a shape problem)")
        gap = {"type": "text", "prompt": "I have ___ dog.", "expected_answer": "a"}
        self.assertEqual(question_spec.shape_problems(gap), [], "a gap whose answer is the word 'a'")
        spec = question_spec.spec_from_constraints({"difficulty": 1}, fallback_concept="Steigung m",
                                                   fallback_type="text")
        validation = question_spec.validate_question(
            {**parsed("math", "evaluation_raw_text")["next_question"], "expected_answer": "b",
             "prompt": "Wie heißt der Fachbegriff für m in y = m · x + b?"}, spec)
        self.assertIn("a written question's expected_answer is an option letter", validation.failures)


class RetryContrastTests(unittest.TestCase):
    """F3 on the stored retry texts of 2026-11-02."""

    def test_the_geography_misunderstanding_states_the_answer_by_contrast(self):
        question = CASES["geography"]["question_shown"]
        text = retry_texts("geography")["misunderstanding"]
        self.assertIn("aber er hebt sich an", text)
        self.assertFalse(lesson_context.reveals_answer(text, question))
        self.assertTrue(lesson_context.retry_reveals(text, question, "misunderstanding"))
        self.assertFalse(lesson_context.retry_reveals("Du dachtest, der Boden sinkt.", question, "misunderstanding"))

    def test_the_history2_analogy_points_at_option_a(self):
        question = CASES["history2"]["question_shown"]
        texts = retry_texts("history2")
        self.assertTrue(lesson_context.retry_reveals(texts["simpler_example"], question, "simpler_example"))  # Zustimmung
        self.assertFalse(lesson_context.retry_reveals(texts["misunderstanding"], question, "misunderstanding"))

    def test_every_retry_text_of_the_final_run_is_judged_as_before_or_as_a_reveal(self):
        verdicts = {}
        for key in ("history1", "geography", "history2", "math"):
            question = CASES[key]["question_shown"]
            for field, text in retry_texts(key).items():
                if text:
                    verdicts[f"{key}.{field}"] = lesson_context.retry_reveals(text, question, field)
        self.assertEqual(verdicts, {
            "geography.misunderstanding": True, "geography.reteach": True, "geography.simpler_example": True,
            "history2.misunderstanding": False, "history2.reteach": True, "history2.simpler_example": True,
            "math.misunderstanding": False, "math.reteach": False, "math.simpler_example": False,
        })
        # Measured against the detector before PF_FIX2 (scratchpad pf_fix2/reveal_fp2.py): only
        # geography.misunderstanding and history2.simpler_example changed, both from False to True.

    def test_words_the_other_options_share_are_not_distinctive(self):
        question = CASES["geography"]["question_shown"]
        self.assertEqual(lesson_context._distinctive_words(question), {"hebt", "angehob"})
        for text in ("Denk an das Wasser über dem Meeresboden.", "Was passiert mit dem Meeresboden beim Seebeben?",
                     "Der Boden sinkt nicht einfach so."):
            with self.subTest(text=text):
                self.assertFalse(lesson_context.retry_reveals(text, question))
        gyges = teaching.QUESTION                     # "Niemand ist freiwillig gerecht"
        self.assertFalse(lesson_context.retry_reveals("Stell dir vor, niemand sieht dich.", gyges, "reteach"),
                         "a pronoun is no option's own word")


def lesson_parts(key):
    return lesson_plan.repair_parts(CASES[key]["lesson_raw"].get("parts"))


def guarded(key):
    kinds = CASES[key]["plan_item_kinds"]
    topic_map = CASES[key]["section"]["topic_map"]
    checked = lesson_context.plan_item_kinds(topic_map) or []
    verified = {teaching.term_key(str(term["term"])) for term in topic_map.get("terms") or [] if term.get("in_notes")}
    return lesson_context.guard_parts(lesson_parts(key), item_kinds=kinds, verified_terms=verified,
                                      source_text=notes(key), heading_only_kinds=set(kinds) - set(checked))


class NotesGuardTests(unittest.TestCase):
    """F4 and F7 on the stored lessons."""

    def test_the_invented_diagram_part_is_no_longer_from_the_notes(self):
        diagram = [part for part in lesson_parts("geography") if part["kind"] == "diagram"]
        self.assertTrue(diagram and diagram[0]["from_notes"])
        self.assertIn("Wellenhöhe vs. Zeit", diagram[0]["body"])
        after = [part for part in guarded("geography") if part["kind"] == "diagram"]
        self.assertTrue(after)
        self.assertFalse(any(part["from_notes"] and "Wellenhöhe" in part["body"] for part in after))

    def test_the_faithful_parts_of_the_six_lessons_stay_from_the_notes(self):
        for key in ("nwt", "history1", "geography", "math", "english", "history2"):
            with self.subTest(key=key):
                for part in guarded(key):
                    if part["kind"] in ("process", "timeline", "example", "experiment", "terms") and part["from_notes"]:
                        self.assertTrue(part["body"] or part["items"])
                kept = [part for part in guarded(key) if part["kind"] == "process" and part["from_notes"]]
                if key == "geography":
                    self.assertEqual(len(kept[0]["items"]), 7, "the notes' own process, all seven steps")

    def test_a_guided_exercise_without_its_task_gets_its_first_hint(self):
        raw = next(part for part in CASES["nwt"]["lesson_raw"]["parts"] if part["kind"] == "guided_exercise")
        self.assertEqual(raw.get("body"), "")
        exercise = next(part for part in lesson_parts("nwt") if part["kind"] == "guided_exercise")
        self.assertTrue(exercise["body"].startswith("Denke daran, welche Variable du ändern würdest"))
        self.assertEqual(len(exercise["items"]), 1)
        self.assertTrue(exercise["answer"])
        dropped = lesson_plan.repair_parts([{"kind": "guided_exercise", "body": "", "items": ["Hinweis"]}])
        self.assertEqual(dropped, [], "no task and no solution: nothing to practise")

    def test_a_sentence_the_notes_say_the_other_way_round_is_not_from_them(self):
        part = {"kind": "explanation", "title": "Wahlrecht", "from_notes": True, "source_pages": [1], "items": [],
                "body": ("Der Reichstag wurde mit einer 5-Prozent-Hürde gewählt. Ohne Sperrklausel kamen viele "
                         "kleine Parteien in den Reichstag.")}
        parts = lesson_context.guard_parts([part], item_kinds=["term"], source_text=notes("history1"))
        own = [entry for entry in parts if entry["from_notes"]]
        added = [entry for entry in parts if not entry["from_notes"]]
        self.assertEqual([entry["body"] for entry in own], ["Ohne Sperrklausel kamen viele kleine Parteien in den Reichstag."])
        self.assertEqual([entry["body"] for entry in added], ["Der Reichstag wurde mit einer 5-Prozent-Hürde gewählt."])

    def test_the_nwt_inversion_is_an_antonym_the_guard_cannot_see(self):
        explanation = next(part for part in guarded("nwt") if part["kind"] == "explanation" and part["from_notes"])
        self.assertIn("Je schlechter ein Material diese Übertragungen verhindert", explanation["body"],
                      "documented limit: 'schlechter' for 'besser' is no negation")


class FirstQuestionTests(unittest.TestCase):
    def test_the_history_first_questions_repeat_the_guided_exercise(self):
        for key, expected in (("history1", True), ("history2", True), ("math", False), ("geography", False),
                              ("nwt", False), ("english", False)):
            with self.subTest(key=key):
                self.assertEqual(lesson_context.repeats_exercise(CASES[key]["question_shown"], lesson_parts(key)),
                                 expected)

    def test_a_term_question_from_the_notes(self):
        question = lesson_context.term_question(
            CASES["history1"]["section"]["topic_map"]["terms"], prompt=lambda name: f"Welche Definition passt zu „{name}“?",
            concept="Art. 48", source=notes("history1"))
        self.assertEqual(question["prompt"], "Welche Definition passt zu „Weimarer Verfassung“?")
        labels = {option["id"]: option["label"] for option in question["options"]}
        self.assertEqual(len(labels), 4)
        self.assertTrue(labels[question["expected_answer"]].startswith("Die Verfassung der deutschen Republik von 1919"))
        self.assertIsNone(lesson_context.term_question([], prompt=str, concept="x"))


class PromptAndPageRuleTests(unittest.TestCase):
    """The prompt rules that go with the guards (lesson_generation v9, answer_evaluation v8,
    answer_diagnosis v4) and the lesson page's formula regions (F9)."""

    def test_the_map_step_retells_the_notes_and_never_invents_a_chart(self):
        from learnova.lessons.strategies import REGISTRY
        step = next(step for step in REGISTRY["geography"].steps if step.kind == "diagram")
        self.assertIn("When the notes only describe a map in words, retell that description", step.instruction)
        self.assertIn("never describe a chart, axes or legend the notes do not contain", step.instruction)

    def test_the_lesson_shape_says_a_guided_exercise_body_is_its_task(self):
        self.assertIn('"body":"the explanation text (guided_exercise: the task itself)"', application.LESSON_JSON_SHAPE)

    def test_the_grading_and_diagnosis_rules(self):
        from learnova.diagnostics.prompts import diagnosis_system_prompt
        source = (Path(application.__file__)).read_text(encoding="utf-8")
        self.assertIn("only what the student thought or missed, never the right alternative", source)
        self.assertIn("the written answer itself (never an option letter)", source)
        self.assertIn("Every `statement` is shown to the student too", diagnosis_system_prompt("History", "German"))
        self.assertEqual(application.PROMPT_VERSIONS["lesson_generation"], "lesson_generation:v9")
        self.assertEqual(application.PROMPT_VERSIONS["answer_evaluation"], "answer_evaluation:v8")
        self.assertEqual(application.PROMPT_VERSIONS["answer_diagnosis"], "answer_diagnosis:v4")

    def test_the_lesson_page_marks_its_scrolling_formulas(self):
        script = (Path(application.__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('import {markFormulaScrollers, renderMath} from "./js/math.js";', script)
        self.assertIn("renderFormulas(lessonView);", script)
        self.assertIn('renderFormulas(document.querySelector("#questionCard"));', script)
        self.assertIn('markFormulaScrollers(root, t("asstScrollFormula"))', script)
        css = (Path(application.__file__).parent / "static" / "css" / "learnova-content.css").read_text(encoding="utf-8")
        self.assertIn("[data-scroller]:is(.katex-display, .ln-math-display)", css)


def remapped_topic(key):
    """The stored section's topic map with its page ids pointed at the test project's page."""

    def build(page_ids):
        topic = json.loads(json.dumps(CASES[key]["section"]["topic_map"]))
        for entry in (topic.get("terms") or []) + (topic.get("items") or []):
            entry["page_id"], entry["page_ids"] = page_ids[0], [page_ids[0]]
        return {"title": CASES[key]["section"]["title"], **topic}
    return build


class LiveReplayTests(teaching.TeachingEngineCase):
    """The stored outputs through the real start_section_lesson and /api/answer."""

    def respond(self, *, task_type, **kwargs):
        if task_type == "lesson_generation" and getattr(self, "raw_lesson", None) is not None:
            self.calls.append({"task_type": task_type, **kwargs})
            return teaching.FakeResponse(self.raw_lesson)
        if task_type == "answer_diagnosis" and getattr(self, "raw_diagnosis", None):
            self.calls.append({"task_type": task_type, **kwargs})
            return teaching.FakeResponse(self.raw_diagnosis)
        if task_type == "diagnosis_verification":
            self.calls.append({"task_type": task_type, **kwargs})
            return teaching.FakeResponse({"agrees": True, "reason": "supported", "better_tag": ""})
        if task_type in ("question_generation", "adaptive_practice"):
            # The gate's own generator (select_next_question): a well-formed written question.
            self.calls.append({"task_type": task_type, **kwargs})
            return teaching.FakeResponse({"question": {
                "id": "g1", "concept": "Art. 48", "difficulty": 1, "type": "text", "hint": "Lies den Abschnitt.",
                "prompt": "Erkläre mit eigenen Worten, was Art. 48 dem Reichspräsidenten erlaubte.", "options": [],
                "expected_answer": "Notverordnungen zu erlassen und am Parlament vorbei zu regieren."}})
        return super().respond(task_type=task_type, **kwargs)

    def replay_lesson(self, key):
        case = CASES[key]
        _, section_id, _ = self.make_project(case["subject"], [(case["file"], [notes(key)])], remapped_topic(key))
        self.raw_lesson = case["lesson_raw"]
        with self.assertLogs(application.app.logger, level="INFO") as logs:
            session_id = self.start(section_id)
            application.app.logger.info("test.replay_started")
        return session_id, logs.output

    def test_history_the_first_question_is_not_the_guided_exercise_again(self):
        session_id, logs = self.replay_lesson("history1")
        state = application.SESSIONS[session_id]
        question = state["current_question"]
        self.assertTrue(any("lesson.first_question_replaced reason=repeats_exercise" in line for line in logs))
        self.assertNotIn("Schwachstelle", question["prompt"])
        self.assertEqual(question["type"], "multiple_choice")
        self.assertIs(state["lesson"]["question"], question)
        self.assertEqual(question["concept"], CASES["history1"]["question_shown"]["concept"])       # "Art. 48"
        labels = {option["id"]: option["label"] for option in question["options"]}
        self.assertTrue(labels[question["expected_answer"]].startswith("Die Verfassung der deutschen Republik"))
        with application.app.app_context():
            saved = teaching.db.session.scalar(teaching.db.select(application.Lesson).where(
                application.Lesson.session_id == session_id))
            self.assertEqual(json.loads(saved.content_json)["question"]["prompt"], question["prompt"])
        self.assertEqual([call["task_type"] for call in self.calls], ["lesson_generation"], "no extra AI call")

    def test_math_the_wrongly_keyed_first_question_is_rekeyed(self):
        session_id, logs = self.replay_lesson("math")
        question = application.SESSIONS[session_id]["current_question"]
        self.assertEqual(question["prompt"], CASES["math"]["question_shown"]["prompt"])
        self.assertEqual(question["expected_answer"], "a")                  # label "2"
        self.assertTrue(any("lesson.first_question_rekeyed" in line for line in logs))

    def test_math_the_follow_up_keyed_b_is_asked_with_the_computed_answer(self):
        session_id, _ = self.replay_lesson("math")
        self.answers = [parsed("math", "evaluation_raw_text")]
        self.raw_diagnosis = CASES["math"]["diagnosis_raw_text"]
        data = self.answer(session_id, "d")
        self.assertEqual(data["next_question"]["prompt"],
                         "Welche Steigung hat die Gerade, die die Punkte (1|2) und (4|8) enthält?")
        current = application.SESSIONS[session_id]["current_question"]
        self.assertEqual(current["expected_answer"], "2")

    def test_history_the_broken_ordering_follow_up_is_replaced_by_the_gate(self):
        session_id, _ = self.replay_lesson("history1")
        application.SESSIONS[session_id]["current_question"] = copy.deepcopy(CASES["history1"]["question_shown"])
        self.answers = [parsed("history1", "evaluation_raw_text")]
        self.raw_diagnosis = CASES["history1"]["diagnosis_raw_text"]
        with self.assertLogs(application.app.logger, level="INFO") as logs:
            data = self.answer(session_id, "a", correct=True)
        self.assertTrue(any("question.replaced source=grader reason=shape" in line for line in logs.output))
        self.assertNotIn("1, 3, 2, 4", json.dumps(data["next_question"], ensure_ascii=False))
        self.assertTrue({"question_generation", "adaptive_practice"} & {call["task_type"] for call in self.calls},
                        "the gate's own generator wrote the next question")

    def test_geography_the_contrast_misunderstanding_is_withheld(self):
        session_id, _ = self.replay_lesson("geography")
        application.SESSIONS[session_id]["current_question"] = copy.deepcopy(CASES["geography"]["question_shown"])
        self.answers = [parsed("geography", "evaluation_raw_text")]
        self.raw_diagnosis = CASES["geography"]["diagnosis_raw_text"]
        data = self.answer(session_id, "a")
        # All three retry texts name the answer (misunderstanding by contrast, reteach and example
        # outright): nothing is left to retry with, so the answer is graded as usual.
        self.assertIsNone(data.get("retry"))
        self.assertTrue(data["evaluation"]["correction"])

    def test_a_choice_the_notes_say_is_never_graded_wrong_or_retried(self):
        session_id, _ = self.replay_lesson("history2")
        application.SESSIONS[session_id]["current_question"] = {**WAHLRECHT, "expected_answer": "c",
                                                                "subject": "History"}
        graded = parsed("history2", "evaluation_raw_text")                  # score 0, retry, "falsch"
        graded["next_question"] = None
        self.answers = [graded]
        self.raw_diagnosis = CASES["history2"]["diagnosis_raw_text"]
        with self.assertLogs(application.app.logger, level="INFO") as logs:
            data = self.answer(session_id, "b")
        self.assertFalse(data.get("retry"))
        self.assertEqual(data["evaluation"]["score"], 50)
        self.assertIn("ohne Sperrklausel", data["evaluation"]["feedback"])
        self.assertEqual(data["diagnosis"]["explanation"], "")
        self.assertTrue(any("answer.notes_backed_option" in line for line in logs.output))
        with application.app.app_context():
            attempt = teaching.db.session.scalar(teaching.db.select(application.Attempt))
            self.assertEqual(attempt.score, 50)


if __name__ == "__main__":
    unittest.main()
