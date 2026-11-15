"""Understanding the student's question (learnova/understanding) across the AI modes.

Found live on 2026-11-15: "Was bedeutet Marketingsog?" was answered as "Was bedeutet ein
Marketing-Slogan?" (and once as an invented acronym), "jüngster Teil der Reihe" as the last
number of a sequence. These tests pin the deterministic layer - which term a message asks
about, whether a reply kept it, the rules every task's contract carries - and drive the
real chat, tutor chat, Short Lesson and flashcard routes with the model patched, so the
focus block, the answer check and its single second attempt are exercised end to end.
The live behaviour of the model itself is measured separately (UNDERSTANDING_IMPLEMENTATION.md).
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_understanding_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova import understanding as u  # noqa: E402
from learnova.ai_services import prompts as ai_prompts  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402

from tests.test_diagnostics_integration import LESSON, keep_app_config  # noqa: E402


# --------------------------------------------------------------------------- focus
class FocusTests(unittest.TestCase):
    """Which word or phrase a message asks about, exactly as the student wrote it."""

    TABLE = (  # the spec's regression table
        ("Was bedeutet Marketingsog?", "meaning", ("Marketingsog",)),
        ("Was bedeutet Marketing-Slogan?", "meaning", ("Marketing-Slogan",)),
        ("Was bedeutet jüngster Teil der Reihe?", "meaning", ("jüngster Teil der Reihe",)),
        ("Was bedeutet schottisch?", "meaning", ("schottisch",)),
        ("Was bedeutet sich etwas kaum entziehen können?", "meaning", ("sich etwas kaum entziehen können",)),
        ("Was bedeutet entsprechend groß ist der Hype?", "meaning", ("entsprechend groß ist der Hype",)),
        ("Was ist der Unterschied zwischen Spannung und Stromstärke?", "difference", ("Spannung", "Stromstärke")),
        ("Was bedeutet scheinbar?", "meaning", ("scheinbar",)),
        ("Was bedeutet effizient?", "meaning", ("effizient",)),
    )

    def test_the_regression_table_keeps_every_term_as_written(self):
        for text, kind, terms in self.TABLE:
            with self.subTest(text=text):
                focus = u.find_focus(text)
                self.assertEqual((focus.kind, focus.terms), (kind, terms))

    def test_similar_terms_stay_apart(self):
        self.assertNotEqual(u.find_focus("Was bedeutet Marketingsog?").terms,
                            u.find_focus("Was bedeutet Marketing-Slogan?").terms)
        self.assertEqual(u.find_focus("Was ist der Unterschied zwischen effektiv und effizient?").terms,
                         ("effektiv", "effizient"))

    def test_a_misspelled_question_keeps_the_students_spelling(self):
        # The model may correct it - and must say so; the focus never does it silently.
        self.assertEqual(u.find_focus("was bedeuted effizent").terms, ("effizent",))

    def test_unfamiliar_compounds_are_kept_whole(self):
        for word in ("Nachhaltigkeitsbericht", "Marketingsog", "Fernweh"):
            self.assertEqual(u.find_focus(f"Was bedeutet {word}?").terms, (word,))

    def test_other_languages(self):
        cases = {
            "What does the word 'bewildered' mean?": ("bewildered",),
            "What is the meaning of 'serendipity'?": ("serendipity",),
            "Que veut dire le mot allemand « Fernweh » ?": ("Fernweh",),
            "Qu'est-ce que la photosynthèse ?": ("photosynthèse",),
            "¿Qué significa 'madrugar'?": ("madrugar",),
            "What is the difference between weather and climate?": ("weather", "climate"),
            "Quelle est la différence entre météo et climat ?": ("météo", "climat"),
            "¿Cuál es la diferencia entre ser y estar?": ("ser", "estar"),
        }
        for text, terms in cases.items():
            with self.subTest(text=text):
                self.assertEqual(u.find_focus(text).terms, terms)

    def test_other_german_forms(self):
        cases = {
            "Erkläre mir den Begriff Photosynthese": ("Photosynthese",),
            "Bedeutung von Nachhaltigkeit": ("Nachhaltigkeit",),
            "Was versteht man unter Gewaltenteilung?": ("Gewaltenteilung",),
            "Was heißt 'Fernweh' auf Englisch?": ("Fernweh",),
            "Was bedeutet hier eigentlich Sog?": ("Sog",),
            "Was ist ein Marketingsog?": ("Marketingsog",),
        }
        for text, terms in cases.items():
            with self.subTest(text=text):
                self.assertEqual(u.find_focus(text).terms, terms)

    def test_the_sentence_the_student_supplies_is_the_context(self):
        focus = u.find_focus("Was bedeutet Marketingsog in diesem Satz: „Dem Marketingsog der neuen Spielkonsole "
                             "kann man sich kaum entziehen.“")
        self.assertEqual(focus.terms, ("Marketingsog",))
        self.assertEqual(focus.context, "Dem Marketingsog der neuen Spielkonsole kann man sich kaum entziehen.")

    def test_a_long_paragraph_gives_the_sentence_that_holds_the_phrase(self):
        focus = u.find_focus(
            "Ich lese gerade diesen Text: „Die Spielereihe gibt es schon seit über zwanzig Jahren. Der jüngste Teil "
            "der Reihe erscheint im Herbst, und entsprechend groß ist der Hype. Viele Fans haben ihn schon "
            "vorbestellt.“ Was bedeutet hier jüngster Teil der Reihe?")
        self.assertEqual(focus.terms, ("jüngster Teil der Reihe",))
        # Not the first sentence ("Spielereihe" is not "Reihe"): the one with the phrase.
        self.assertEqual(focus.context, "Der jüngste Teil der Reihe erscheint im Herbst, und entsprechend groß ist der Hype.")

    def test_a_follow_up_takes_its_first_term_from_the_earlier_question(self):
        focus = u.find_focus("Und was ist der Unterschied zu Entwicklung?", previous=["Was bedeutet Wachstum?"])
        self.assertEqual((focus.kind, focus.terms), ("difference", ("Wachstum", "Entwicklung")))
        # Without an earlier term there is nothing to compare: no focus rather than a guess.
        self.assertFalse(u.find_focus("Und was ist der Unterschied zu Entwicklung?"))

    def test_questions_that_name_no_word_get_no_focus(self):
        # No focus = no block and no check: the reply is generated exactly as before.
        for text in ("Kannst du mir bei Mathe helfen?", "Was ist 7 + 5?", "Was ist der Grund für den Ersten Weltkrieg?",
                     "Was bedeutet das?", "Was ist los?", "Hallo", "", "Erkläre mir Brüche bitte noch einmal"):
            with self.subTest(text=text):
                self.assertFalse(u.find_focus(text))

    def test_a_card_front_is_the_term(self):
        self.assertEqual(u.card_focus("  Marketingsog ").terms, ("Marketingsog",))
        self.assertFalse(u.card_focus("eine viel zu lange Kartenvorderseite mit mehr als acht Wörtern darauf ok"))


# --------------------------------------------------------------------------- check
class ReplyCheckTests(unittest.TestCase):
    """The deterministic answer check: kept the term, or explained a similar-looking word?"""

    def test_the_marketingsog_failure_is_caught(self):
        check = u.check_reply(u.find_focus("Was bedeutet Marketingsog?"),
                              "**Was bedeutet ein Marketing-Slogan?** Ein Marketing-Slogan ist ein kurzer Werbespruch.")
        self.assertFalse(check.ok)
        self.assertEqual(check.missing, ("Marketingsog",))
        self.assertIn("marketingslogan", check.substitutes)

    def test_spelling_variants_of_the_same_term_pass(self):
        focus = u.find_focus("Was bedeutet Marketingsog?")
        for reply in ("Der **Marketingsog** ist die Anziehung durch Werbung.",
                      "Ein Marketing-Sog ist die Anziehung durch Werbung.",
                      "„Marketing‑Sog“ (mit geschütztem Bindestrich) ist ...",
                      "Marketing Sog: der Sog, den Werbung erzeugt."):
            with self.subTest(reply=reply):
                self.assertTrue(u.check_reply(focus, reply).ok)

    def test_inflected_phrases_pass(self):
        self.assertTrue(u.check_reply(u.find_focus("Was bedeutet jüngster Teil der Reihe?"),
                                      "Der jüngste Teil einer Reihe ist der neueste Film oder das neueste Spiel.").ok)
        self.assertTrue(u.check_reply(u.find_focus("Was bedeutet sich etwas kaum entziehen können?"),
                                      "Wenn man sich etwas kaum entziehen kann, fällt es schwer, es zu vermeiden.").ok)

    def test_a_corrected_typo_passes_and_is_marked(self):
        check = u.check_reply(u.find_focus("was bedeuted effizent"), "Du meinst sicher **effizient**: ...")
        self.assertTrue(check.ok)
        self.assertTrue(check.assumed_typo)

    def test_a_confusable_word_is_not_the_asked_one(self):
        check = u.check_reply(u.find_focus("Was bedeutet scheinbar?"), "Anscheinend bedeutet: vermutlich, wohl.")
        self.assertFalse(check.ok)
        self.assertEqual(check.missing, ("scheinbar",))

    def test_a_difference_needs_both_terms(self):
        focus = u.find_focus("Was ist der Unterschied zwischen Spannung und Stromstärke?")
        self.assertFalse(u.check_reply(focus, "Die Spannung wird in Volt gemessen.").ok)
        self.assertTrue(u.check_reply(focus, "Spannung (Volt) treibt, Stromstärke (Ampere) misst den Fluss.").ok)

    def test_nothing_to_check_passes(self):
        self.assertTrue(u.check_reply(u.QuestionFocus(), "anything").ok)
        self.assertTrue(u.check_reply(u.find_focus("Was bedeutet Sog?"), "").ok)

    def test_the_correction_note_names_both_words_and_hides_itself(self):
        focus = u.find_focus("Was bedeutet Marketingsog?")
        note = u.correction_note(focus, u.check_reply(focus, "Ein Marketing-Slogan ist ..."))
        self.assertIn("«Marketingsog»", note)
        self.assertIn("«marketingslogan»", note)
        self.assertIn("never mention it", note)


# --------------------------------------------------------------------------- block
class FocusBlockTests(unittest.TestCase):
    def test_the_block_names_the_exact_term_and_the_sentence(self):
        focus = u.find_focus("Was bedeutet Marketingsog in diesem Satz: „Dem Marketingsog der neuen Spielkonsole "
                             "kann man sich kaum entziehen.“")
        block = u.focus_block(focus)
        self.assertIn("Explain exactly «Marketingsog», spelled as written", block)
        self.assertIn("read a compound from its parts", block)
        self.assertIn("Dem Marketingsog der neuen Spielkonsole", block)
        self.assertIn("Earlier topics of this conversation do not change", block)
        self.assertIn("No exercise or check question unless the student asks", block)

    def test_without_a_sentence_the_everyday_meaning_comes_first(self):
        block = u.focus_block(u.find_focus("Was bedeutet jüngster Teil der Reihe?"))
        self.assertIn("take the everyday meaning", block)
        self.assertIn("several common meanings, name them briefly", block)

    def test_each_mode_says_what_it_needs(self):
        focus = u.find_focus("Was bedeutet Marketingsog?")
        self.assertIn("the way the lesson uses it", u.focus_block(focus, mode="tutor"))
        self.assertNotIn("Earlier topics", u.focus_block(focus, mode="tutor"))
        self.assertIn("The lesson teaches exactly this term", u.focus_block(focus, mode="lesson"))
        self.assertIn("Every suggestion defines exactly this term", u.focus_block(focus, mode="card"))
        difference = u.focus_block(u.find_focus("Was ist der Unterschied zwischen Spannung und Stromstärke?"))
        self.assertIn("how «Spannung» and «Stromstärke» differ", difference)
        self.assertEqual(u.focus_block(u.QuestionFocus()), "")

    def test_the_block_stays_small(self):
        # Paid on every word question; the longest shape (a sentence given) stays near 250 tokens.
        longest = u.focus_block(u.find_focus(
            "Was bedeutet Nachhaltigkeitsbericht in diesem Satz: „Der Konzern veröffentlicht jedes Jahr einen "
            "Nachhaltigkeitsbericht über seine Emissionen und Lieferketten.“"))
        self.assertLessEqual(ai_service._estimate_tokens(longest), 260)


# --------------------------------------------------------------------------- contracts
# Tasks that do not answer, teach, translate or read a student's words carry no rule, each
# for a reason: a new task type must be added to one side or the other.
EXEMPT = {
    "mistake_analysis": "judges a student's answer against a key; SOURCE_TERMS is about writing content",
    "answer_diagnosis": "evidence-cited diagnosis contract (diagnosis:v2), kept stable",
    "diagnosis_verification": "agrees/disagrees with a diagnosis; produces no text for the student",
    "handwriting_region_review": "re-reads cropped regions under the OCR rules of its caller",
    "content_moderation": "safety classification, never answers",
    "report_review": "moderation of a community report, never answers",
    "flashcard_review": "quality review of a finished set, never rewrites it",
    "competency_extraction": "copies Kompetenzraster statements; already bound to the source pages",
}


class ContractRuleTests(unittest.TestCase):
    def test_every_task_has_a_rule_or_a_reason(self):
        covered = u.CHAT_TASKS | u.CONTENT_TASKS | u.READING_TASKS | {"translation"}
        for task in ai_prompts.PROMPT_VERSIONS:
            with self.subTest(task=task):
                self.assertTrue((task in covered) != (task in EXEMPT), f"{task}: covered and exempt, or neither")

    def test_each_task_carries_its_rule_in_the_contract(self):
        for task in ai_prompts.PROMPT_VERSIONS:
            contract = ai_prompts.output_contract(task, "German", {"question_count": 3, "texts": ["a"]})
            with self.subTest(task=task):
                if task in u.CHAT_TASKS:
                    self.assertIn("UNDERSTANDING THE QUESTION", contract)
                elif task in u.CONTENT_TASKS:
                    self.assertIn(u.SOURCE_TERMS_RULE, contract)
                elif task == "translation":
                    self.assertIn(u.TRANSLATION_RULE, contract)
                elif task in u.READING_TASKS:
                    self.assertIn(u.READING_RULE, contract)
                else:
                    for rule in (u.UNDERSTANDING_RULE, u.SOURCE_TERMS_RULE, u.TRANSLATION_RULE, u.READING_RULE):
                        self.assertNotIn(rule, contract)

    def test_the_corrective_retry_keeps_the_rule(self):
        self.assertIn(u.SOURCE_TERMS_RULE, ai_prompts.corrective_instruction("lesson_generation", "German", "bad json"))

    def test_every_contract_with_a_rule_fits_the_gateways_allowance(self):
        # estimate_request_tokens reserves 600 tokens for the contract it cannot see.
        # (content_moderation, which carries no rule, was already at ~602 before this work.)
        for task in u.CHAT_TASKS | u.CONTENT_TASKS | u.READING_TASKS | {"translation"}:
            contract = ai_prompts.output_contract(task, "German", {"question_count": 3, "texts": ["a"]})
            with self.subTest(task=task):
                self.assertLessEqual(ai_service._estimate_tokens(contract), ai_service._CONTRACT_ALLOWANCE_TOKENS)

    def test_the_chat_rule_is_the_only_long_one(self):
        self.assertLessEqual(ai_service._estimate_tokens(u.UNDERSTANDING_RULE), 230)
        for rule in (u.SOURCE_TERMS_RULE, u.TRANSLATION_RULE, u.READING_RULE):
            self.assertLessEqual(ai_service._estimate_tokens(rule), 60)

    def test_versions_moved_with_the_contracts(self):
        self.assertEqual(ai_prompts.PROMPT_VERSIONS["assistant_chat"], "assistant_chat:v8")
        self.assertEqual(ai_prompts.PROMPT_VERSIONS["tutor_chat"], "tutor_chat:v7")
        self.assertEqual(ai_prompts.PROMPT_VERSIONS["flashcard_back_suggestion"], "flashcard_back_suggestion:v2")
        self.assertEqual(ai_prompts.PROMPT_VERSIONS["translation"], "translation:v3")

    def test_chat_photos_separate_an_unclear_photo_from_a_rare_word(self):
        self.assertIn("A word the reading shows without [?...] is what the photo says", application.CHAT_IMAGE_RULES)


# --------------------------------------------------------------------------- routes
class FakeUsage:
    def __init__(self, input_tokens=100, output_tokens=50):
        self.input_tokens, self.output_tokens, self.total_tokens = input_tokens, output_tokens, input_tokens + output_tokens


class FakeResponse:
    def __init__(self, text, input_tokens=100, output_tokens=50):
        self.output_text = text if isinstance(text, str) else json.dumps(text)
        self.model = "test-model"
        self.usage = FakeUsage(input_tokens, output_tokens)


class Script:
    """Patches app.create_response: answers in order, records every call."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, FakeResponse) else FakeResponse(answer)


SLOGAN = "**Was bedeutet ein Marketing-Slogan?** Ein Marketing-Slogan ist ein kurzer Werbespruch einer Marke."
SOG = ("**Bedeutung:** Ein **Marketingsog** ist die Anziehung, die Werbung erzeugt (Marketing + Sog).\n\n"
       "TOPIC: Economics | Marketingsog")


class RouteTestCase(unittest.TestCase):
    def setUp(self):
        keep_app_config(self, "TESTING", "FEATURE_ASSISTANT_CHAT", "ASSISTANT_MODEL", "AI_MODE",
                        "FEATURE_PRIVATE_FLASHCARDS", "WTF_CSRF_ENABLED")
        application.app.config.update(TESTING=True, FEATURE_ASSISTANT_CHAT=True, AI_MODE="cached",
                                      ASSISTANT_MODEL="openai/gpt-oss-20b", FEATURE_PRIVATE_FLASHCARDS=True)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={"username": "lena", "email": "lena@example.com",
                                            "password": "correct-horse-battery", "language": "de", "grade": "8"})
        self.client.post("/settings/language", data={"language": "de"})

    def send(self, script, message, conversation=None):
        if conversation is None:
            conversation = self.client.post("/api/assistant/conversations", json={"preset": "general"}).get_json()[
                "conversation"]["id"]
        with mock.patch.object(application, "create_response", script):
            response = self.client.post(f"/api/assistant/conversations/{conversation}/messages",
                                        json={"message": message})
        self.assertIn(response.status_code, (200, 201), response.get_data(as_text=True))
        detail = self.client.get(f"/api/assistant/conversations/{conversation}").get_json()["conversation"]
        return conversation, [m for m in detail["messages"] if m["role"] == "assistant"][-1]


class AssistantChatTests(RouteTestCase):
    def test_the_focus_goes_into_the_turn_not_the_system_prompt(self):
        script = Script(SOG)
        self.send(script, "Was bedeutet Marketingsog?")
        call = script.calls[0]
        self.assertIn("QUESTION FOCUS", call["input"])
        self.assertIn("«Marketingsog»", call["input"])
        self.assertNotIn("QUESTION FOCUS", call["instructions"])   # stays byte-identical for caching
        # The class still reaches the model next to the focus (class-level personalisation kept).
        self.assertIn("Class:", call["input"])

    def test_a_substituted_word_gets_exactly_one_second_attempt(self):
        script = Script(FakeResponse(SLOGAN, 1500, 200), FakeResponse(SOG, 1600, 180))
        _conversation, reply = self.send(script, "Was bedeutet Marketingsog?")
        self.assertEqual(len(script.calls), 2)
        second = script.calls[1]
        self.assertTrue(second["fresh"])
        self.assertIn("ANSWER CHECK", second["input"])
        self.assertIn("«marketingslogan»", second["input"])
        self.assertEqual(second["instructions"], script.calls[0]["instructions"])  # full prompt, not a stub
        self.assertIn("Marketingsog", reply["content"])
        self.assertNotIn("Slogan", reply["content"])
        self.assertNotIn("ANSWER CHECK", reply["content"])

    def test_both_attempts_failing_still_answers_and_stops(self):
        script = Script(SLOGAN, SLOGAN.replace("kurzer", "knapper"))
        _conversation, reply = self.send(script, "Was bedeutet Marketingsog?")
        self.assertEqual(len(script.calls), 2)          # never a loop
        self.assertFalse(reply.get("error"))
        self.assertIn("knapper", reply["content"])

    def test_a_correct_reply_costs_nothing_extra(self):
        script = Script(SOG)
        self.send(script, "Was bedeutet Marketingsog?")
        self.assertEqual(len(script.calls), 1)

    def test_a_provider_failure_on_the_second_attempt_keeps_the_first_reply(self):
        script = Script(SLOGAN, ai_service.AIProviderError("rate_limited", "busy"))
        _conversation, reply = self.send(script, "Was bedeutet Marketingsog?")
        self.assertFalse(reply.get("error"))
        self.assertIn("Marketing-Slogan", reply["content"])

    def test_a_provider_failure_on_the_first_call_is_handled_as_before(self):
        script = Script(ai_service.AIProviderError("provider_unavailable", "down"))
        _conversation, reply = self.send(script, "Was bedeutet Marketingsog?")
        self.assertTrue(reply.get("error"))
        self.assertEqual(len(script.calls), 1)

    def test_a_question_without_a_word_is_untouched(self):
        script = Script("Klar, wobei genau?")
        self.send(script, "Kannst du mir bei Mathe helfen?")
        self.assertEqual(len(script.calls), 1)
        self.assertNotIn("QUESTION FOCUS", script.calls[0]["input"])

    def test_a_follow_up_compares_with_the_earlier_question(self):
        conversation, _reply = self.send(Script("**Wachstum** heißt: etwas wird größer."), "Was bedeutet Wachstum?")
        script = Script("Wachstum ist mehr vom Gleichen, Entwicklung eine Veränderung der Qualität.")
        self.send(script, "Und was ist der Unterschied zu Entwicklung?", conversation)
        self.assertIn("how «Wachstum» and «Entwicklung» differ", script.calls[0]["input"])
        self.assertEqual(len(script.calls), 1)

    def test_tokens_of_both_attempts_are_recorded(self):
        script = Script(FakeResponse(SLOGAN, 1500, 200), FakeResponse(SOG, 1600, 180))
        conversation, _reply = self.send(script, "Was bedeutet Marketingsog?")
        with application.app.app_context():
            row = application.db.session.scalars(application.db.select(application.ConversationMessage).where(
                application.ConversationMessage.conversation_id == conversation,
                application.ConversationMessage.role == "assistant")).one()
            self.assertEqual((row.input_tokens, row.output_tokens), (3100, 380))


class TutorChatTests(RouteTestCase):
    def start_lesson(self):
        with mock.patch.object(application, "create_response", Script(LESSON)):
            response = self.client.post("/api/analyze", data={"study_goal": "Linear equations", "subject": "Mathematics"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["session_id"]

    def test_the_tutor_chat_checks_and_corrects_once(self):
        session_id = self.start_lesson()
        script = Script(SLOGAN, "Ein **Marketingsog** ist die Anziehung durch Werbung.")
        with mock.patch.object(application, "create_response", script):
            response = self.client.post("/api/chat", json={"session_id": session_id,
                                                           "message": "Was bedeutet Marketingsog?"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(len(script.calls), 2)
        self.assertIn("the way the lesson uses it", script.calls[0]["input"])
        self.assertIn("ANSWER CHECK", script.calls[1]["input"])
        stored = application.SESSIONS[session_id]["chat_history"][-1]["content"]
        self.assertIn("Marketingsog", stored)

    def test_a_control_button_names_no_word(self):
        session_id = self.start_lesson()
        script = Script("Einfacher gesagt: ...")
        with mock.patch.object(application, "create_response", script):
            self.client.post("/api/chat", json={"session_id": session_id, "message": "", "control": "simpler"})
        self.assertEqual(len(script.calls), 1)
        self.assertNotIn("QUESTION FOCUS", script.calls[0]["input"])


class ShortLessonTests(RouteTestCase):
    def test_a_word_goal_is_taught_as_that_word(self):
        wrong = {**LESSON, "lesson_title": "Marketing-Slogans", "concepts": [{"name": "Marketing-Slogan", "evidence": "goal"}],
                 "explanation": "Ein Marketing-Slogan ist ein kurzer Werbespruch."}
        right = {**LESSON, "lesson_title": "Was ist ein Marketingsog?", "concepts": [{"name": "Marketingsog", "evidence": "goal"}],
                 "explanation": "Der Marketingsog ist die Anziehung, die Werbung erzeugt."}
        script = Script(wrong, right)
        with mock.patch.object(application, "create_response", script):
            response = self.client.post("/api/analyze", data={"study_goal": "Was bedeutet Marketingsog?",
                                                              "subject": "German"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        first_prompt = json.dumps(script.calls[0]["input"], ensure_ascii=False)
        self.assertIn("The lesson teaches exactly this term", first_prompt)
        lesson_calls = [call for call in script.calls if call["task_type"] == "lesson_generation"]
        self.assertEqual(len(lesson_calls), 2)
        self.assertEqual(response.get_json()["lesson"]["lesson_title"], "Was ist ein Marketingsog?")

    def test_an_ordinary_goal_is_generated_once(self):
        script = Script(LESSON)
        with mock.patch.object(application, "create_response", script):
            response = self.client.post("/api/analyze", data={"study_goal": "Linear equations", "subject": "Mathematics"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(script.calls), 1)
        self.assertNotIn("QUESTION FOCUS", json.dumps(script.calls[0]["input"], ensure_ascii=False))


class FlashcardSuggestionTests(RouteTestCase):
    def test_a_lookalike_definition_is_dropped(self):
        script = Script({"suggestions": [
            {"back": "Ein Marketing-Slogan ist ein kurzer Werbespruch.", "style": "short"},
            {"back": "Die Anziehungskraft, die Werbung auf Menschen ausübt.", "style": "detailed"},
        ]})
        with mock.patch.object(application, "create_response", script):
            response = self.client.post("/api/flashcards/suggest-back", json={"front": "Marketingsog", "subject": "German"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        backs = [item["back"] for item in response.get_json()["suggestions"]]
        self.assertEqual(backs, ["Die Anziehungskraft, die Werbung auf Menschen ausübt."])
        self.assertIn("«Marketingsog»", script.calls[0]["input"])

    def test_the_real_request_fits_its_budget(self):
        # The budget was 800 tokens; the real request (tutor rules + card + contract) is
        # ~1300-1700, so every suggestion was refused before reaching a model.
        script = Script({"suggestions": [{"back": "Anziehung durch Werbung", "style": "short"}]})
        with mock.patch.object(application, "create_response", script):
            self.client.post("/api/flashcards/suggest-back", json={"front": "Photosynthese", "subject": "Mathematics"})
        call = script.calls[0]
        with application.app.app_context():
            contract = ai_prompts.output_contract("flashcard_back_suggestion", "German", {})
            estimate = ai_service._estimate_tokens({"input": call["input"],
                                                    "instructions": f"{call['instructions']}\n\n{contract}"})
            self.assertLess(estimate, ai_service._task_budget("flashcard_back_suggestion", "input"))


class RecheckTests(RouteTestCase):
    def test_an_unavailable_second_attempt_reports_the_failed_check_only(self):
        focus = u.find_focus("Was bedeutet Marketingsog?")

        def unavailable(_note):
            raise ai_service.AIProviderError("rate_limited", "busy")

        with application.app.app_context():
            result = application.recheck_reply("assistant_chat", focus, SLOGAN, unavailable)
        self.assertTrue(result.checked_failed)
        self.assertEqual(result.response_text, "")


if __name__ == "__main__":
    unittest.main()
