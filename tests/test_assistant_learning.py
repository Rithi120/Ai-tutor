"""The assistant as a tutor (WP4): the tutoring layer, STUDENT CONTEXT, hidden trailers,
history compaction and the per-topic explanation state.

Pure parts are tested directly; the request the model receives and what is stored after
a reply are tested through the real send route with `app.create_response` patched, so no
AI call is ever made. Behaviour that only a live model shows (a direct question getting no
question back, a misconception getting one check) is an opt-in live test at the end.
"""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_assistant_learning_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import pytest  # noqa: E402

import app as application  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.ai_services.prompts import PROMPT_VERSIONS  # noqa: E402
from learnova.assistant import conversation, presets  # noqa: E402
from learnova.assistant.formatting import split_reply_trailers  # noqa: E402
from learnova.diagnostics.explanation_level import REPRESENTATIONS, REQUEST_DIRECTIVES  # noqa: E402
from tests.typing_helpers import fetch, present  # noqa: E402

db = application.db
PASSWORD = "correct-horse-battery"


def thread(*pairs):
    return [{"role": role, "content": content} for role, content in pairs]


def reading(text: str, *, summary: str = "Ein Arbeitsblatt.", unclear: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "images": [{"index": 1, "kind": "worksheet", "rotation": 0, "cut_off": False, "legibility": "clear"}],
        "blocks": [{"image": 1, "type": "printed_text", "text": text, "author": "printed", "confidence": 0.95}],
        "diagrams": [],
        "uncertain": [{"image": 1, "text": item, "alternatives": [], "reason": "blurred", "affects_answer": True}
                      for item in unclear],
        "clarification_question": "", "summary": summary, "language": "de",
    }


# ---------------------------------------------------------------------- pure: prompts
class TutoringLayerTests(unittest.TestCase):
    def test_every_preset_carries_the_teaching_layer_and_the_topic_rule(self):
        for name in presets.PRESET_NAMES:
            prompt = presets.system_prompt(name, language="German")
            self.assertIn("TEACHING\nYou are tutoring a school student.", prompt, name)
            self.assertIn("Always end with one last line: TOPIC:", prompt, name)
            self.assertIn("CHIPS:", prompt, name)
            # The layer sits after the preset's own rules and before the language line.
            self.assertLess(prompt.index(presets.PRESETS[name]["rules"]), prompt.index("TEACHING\n"), name)
            self.assertLess(prompt.index("TEACHING\n"), prompt.index("LANGUAGE\n"), name)

    def test_the_layer_is_compact(self):
        self.assertLessEqual(conversation.estimate_tokens(presets.TUTORING_LAYER), 420)
        bare = presets.system_prompt("general", language="German")
        without = bare.replace(presets.TUTORING_LAYER + "\n\n", "")
        self.assertLessEqual(conversation.estimate_tokens(bare) - conversation.estimate_tokens(without), 420)

    def test_the_rules_the_spec_asks_for_are_all_there(self):
        layer = presets.TUTORING_LAYER
        for phrase in (
            "A simple factual question: answer directly", "No quiz",
            "you may end with ONE short check", "Never more than one question per reply",
            "say what is right (quote them), name the exact step that went wrong",
            "give a hint or the next step first", "explain again differently",
            "A difficult question is not evidence of advanced knowledge",
            "Define each technical term the first time", "never ask for what it already says",
        ):
            self.assertIn(phrase, layer)

    def test_the_bare_prompt_never_names_the_learner_block(self):
        # test_assistant.py pins that the learner block is appended, never interpolated.
        self.assertNotIn("LEARNER", presets.system_prompt("general", language="German"))

    def test_study_coach_no_longer_quizzes_every_reply(self):
        prompt = presets.system_prompt("study_coach")
        self.assertNotIn("End with one short question", prompt)
        self.assertIn("When it helps, end with one short question that checks the idea", prompt)
        for kept in ("not the whole solution", "Being withholding is not the same as being helpful",
                     "Teach rather than solve"):
            self.assertIn(kept, prompt)

    def test_other_presets_keep_their_focus(self):
        self.assertIn("what the evidence shows", presets.system_prompt("research"))
        self.assertIn("Explain one thing well", presets.system_prompt("explain"))
        self.assertIn("capable, direct general assistant", presets.system_prompt("general"))


class StudentContextTests(unittest.TestCase):
    def test_everything_known_is_listed_under_one_header(self):
        block = presets.student_context(
            school_class="Grade 8 (Gymnasium)", subject="Mathematics", topic="Quadratic equations",
            level="EXPLANATION LEVEL: 2 of 5", weak_spots=[("Fractions", 31.6), ("Powers", 50.0)],
            project='Their Learnova project in Mathematics: "Klassenarbeit".', curriculum="BILDUNGSPLAN: x")
        lines = block.splitlines()
        self.assertEqual(lines[0], presets.STUDENT_CONTEXT_HEADER)
        self.assertIn("Class: Grade 8 (Gymnasium)", lines)
        self.assertIn("Subject: Mathematics | Topic: Quadratic equations", lines)
        self.assertIn("Known weak spots in Mathematics: Fractions (32%), Powers (50%)", lines)
        self.assertIn("EXPLANATION LEVEL: 2 of 5", block)
        self.assertIn("BILDUNGSPLAN: x", block)

    def test_weak_spots_need_a_subject_and_stop_at_three(self):
        spots = [("A", 1), ("B", 2), ("C", 3), ("D", 4)]
        self.assertNotIn("weak spots", presets.student_context(weak_spots=spots))
        line = next(item for item in presets.student_context(subject="Biology", weak_spots=spots).splitlines()
                    if item.startswith("Known weak spots"))
        self.assertNotIn("D (4%)", line)
        self.assertIn("C (3%)", line)

    def test_an_empty_record_says_so(self):
        block = presets.student_context()
        self.assertIn("Nothing is recorded yet", block)
        self.assertNotIn("Subject:", block)


# --------------------------------------------------------------------- pure: trailers
class TrailerTests(unittest.TestCase):
    def test_both_trailers_are_cut_off_in_either_order(self):
        for tail in ("CHIPS: Ja | Nein\nTOPIC: Mathematik | Quadratische Gleichungen",
                     "TOPIC: Mathematik | Quadratische Gleichungen\n\nCHIPS: Ja | Nein"):
            text, chips, topic = split_reply_trailers(f"Die Lösung ist \\(x = 2\\).\n\n{tail}\n")
            self.assertEqual(text, "Die Lösung ist \\(x = 2\\).")
            self.assertEqual(chips, ["Ja", "Nein"])
            self.assertEqual(topic, ("Mathematik", "Quadratische Gleichungen"))

    def test_none_and_markdown_wrapped_keywords(self):
        self.assertEqual(split_reply_trailers("Hallo!\n**TOPIC:** none"), ("Hallo!", [], ("", "none")))
        self.assertEqual(split_reply_trailers("Hi.\n`TOPIC: none`")[2], ("", "none"))
        self.assertEqual(split_reply_trailers("Gut.\n---\nTOPIC: Biology | Zelle")[0], "Gut.")

    def test_chips_are_trimmed_bounded_and_unique(self):
        _text, chips, _topic = split_reply_trailers(
            "Frage?\nCHIPS: [Ja] | \"Nein\" | ja | " + "x" * 60 + " | vier")
        self.assertEqual(chips, ["Ja", "Nein", "x" * 40])

    def test_topic_is_bounded_and_extra_bars_stay_in_the_topic(self):
        _text, _chips, found = split_reply_trailers("A\nTOPIC: Maths | Algebra | lineare " + "y" * 300)
        topic = present(found, "topic")
        self.assertEqual(topic[0], "Maths")
        self.assertTrue(topic[1].startswith("Algebra, lineare"))
        self.assertLessEqual(len(topic[1]), 160)

    def test_a_pipe_inside_a_formula_stays_in_its_chip(self):
        _text, chips, _topic = split_reply_trailers("Welche?\nCHIPS: $|x|$ | $x^2$ | \\(|y| + 1\\)")
        self.assertEqual(chips, ["$|x|$", "$x^2$", "\\(|y| + 1\\)"])
        self.assertEqual(split_reply_trailers("Was?\nCHIPS: $$a|b$$ | c")[1], ["$$a|b$$", "c"])
        # A formula's own closing delimiter is never trimmed as a bracket; a price is no formula.
        self.assertEqual(split_reply_trailers("x\nCHIPS: \\(x\\) | (ja)")[1], ["\\(x\\)", "ja"])
        self.assertEqual(split_reply_trailers("x\nCHIPS: 5$ | 3")[1], ["5$", "3"])

    def test_trailers_wrapped_as_list_items_headings_comments_or_a_fence_are_cut_off(self):
        for tail in ("- TOPIC: Maths | Brüche", "* TOPIC: Maths | Brüche", "1. TOPIC: Maths | Brüche",
                     "### TOPIC: Maths | Brüche", "<!-- TOPIC: Maths | Brüche -->", "> TOPIC: Maths | Brüche",
                     "- **TOPIC:** Maths | Brüche", "```\nTOPIC: Maths | Brüche\n```",
                     "```text\nCHIPS: Ja | Nein\nTOPIC: Maths | Brüche\n```"):
            with self.subTest(tail=tail):
                text, _chips, topic = split_reply_trailers(f"Kürze den Bruch.\n\n{tail}")
                self.assertEqual(text, "Kürze den Bruch.")
                self.assertEqual(topic, ("Maths", "Brüche"))

    def test_a_repeated_trailer_line_goes_too_and_the_last_one_counts(self):
        text, chips, topic = split_reply_trailers(
            "Antwort.\nTOPIC: Physics | Kraft\nCHIPS: a | b\nTOPIC: Mathematics | Brüche\nCHIPS: c | d")
        self.assertEqual(text, "Antwort.")
        self.assertEqual((chips, topic), (["c", "d"], ("Mathematics", "Brüche")))

    def test_ordinary_lines_are_never_taken_for_trailers(self):
        for text in ("Topic: revision", "Summary\nTOPIC: in the middle\nand more text",
                     "```\nTOPIC: in code\n```"):
            self.assertEqual(split_reply_trailers(text), (text.rstrip(), [], None), text)

    def test_postprocess_normalises_math_first_then_cuts_the_trailers(self):
        with application.app.app_context():
            text, chips, topic = application.postprocess_reply(
                "Es gilt $\\\\frac{1}{2}$.\nCHIPS: Ja | Nein\nTOPIC: Mathematics | Brüche")
        self.assertEqual(text, "Es gilt $\\frac{1}{2}$.")
        self.assertEqual((chips, topic), (["Ja", "Nein"], ("Mathematics", "Brüche")))


# ----------------------------------------------------------------------- pure: window
class PinFirstTests(unittest.TestCase):
    def long_thread(self, turns=20, words=300):
        return thread(("user", "MEINE ERSTE FRAGE: quadratische Gleichungen"),
                      *[("assistant" if index % 2 == 0 else "user", f"turn{index} " + "word " * words)
                        for index in range(turns)])

    def test_the_first_user_turn_survives_a_long_thread(self):
        history = self.long_thread()
        plain = conversation.build_window(history, token_budget=3000)
        pinned = conversation.build_window(history, token_budget=3000, pin_first=True)
        self.assertNotIn("MEINE ERSTE FRAGE", plain.messages[0]["content"])
        self.assertEqual(pinned.messages[0]["content"], history[0]["content"])
        self.assertEqual(pinned.messages[-1]["content"], history[-1]["content"])
        self.assertTrue(pinned.pinned_first)
        # Dropped counts the turns after the pinned one that were left out.
        self.assertEqual(pinned.dropped_messages, len(history) - len(pinned.messages))
        transcript = conversation.render_transcript(pinned)
        self.assertIn(f"[… {pinned.dropped_messages} earlier messages left out]", transcript)
        self.assertLess(transcript.index("MEINE ERSTE FRAGE"), transcript.index("left out]"))

    def test_a_thread_that_fits_is_sent_exactly_as_without_the_pin(self):
        history = thread(("user", "a"), ("assistant", "b"), ("user", "c"))
        self.assertEqual(conversation.build_window(history, token_budget=1000, pin_first=True),
                         conversation.build_window(history, token_budget=1000))
        self.assertFalse(conversation.build_window(history, token_budget=1000, pin_first=True).pinned_first)

    def test_a_long_first_turn_is_shortened_with_the_marker(self):
        history = thread(("user", "Q" * 5000), *[("assistant", "a " * 400), ("user", "next " * 400)] * 4)
        window = conversation.build_window(history, token_budget=2500, pin_first=True)
        self.assertTrue(window.messages[0]["content"].startswith("Q" * conversation.PIN_FIRST_CHARACTERS))
        self.assertTrue(window.messages[0]["content"].endswith(conversation.TRUNCATION_MARKER))
        self.assertGreaterEqual(window.truncated_messages, 1)

    def test_no_reply_to_a_dropped_question_follows_the_pinned_turn(self):
        window = conversation.build_window(self.long_thread(turns=21), token_budget=2000, pin_first=True)
        self.assertEqual(window.messages[1]["role"], "user")

    def test_the_newest_turn_still_wins_when_nothing_fits_beside_the_pin(self):
        history = thread(("user", "first " * 400), ("assistant", "x"), ("user", "the question " * 400))
        window = conversation.build_window(history, token_budget=400, pin_first=True)
        self.assertIn("the question", window.messages[-1]["content"])
        self.assertFalse(window.pinned_first)

    def test_the_default_keeps_every_old_behaviour(self):
        history = self.long_thread()
        window = conversation.build_window(history, token_budget=3000)
        self.assertFalse(window.pinned_first)
        self.assertNotIn("left out", conversation.render_transcript(window))


# ------------------------------------------------------------------------ routes
class FakeAI:
    """Patches app.create_response; answers assistant_chat with the given replies."""

    def __init__(self, *replies: str, error: Exception | None = None):
        self.replies = list(replies) or ["Here is a clear answer.\nTOPIC: none"]
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self._patch = patch.object(application, "create_response", side_effect=self)

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["task_type"] != "assistant_chat":
            raise AssertionError(f"unexpected AI task {kwargs['task_type']}")
        if self.error is not None:
            raise self.error
        text = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]

        class Usage:
            input_tokens, output_tokens, total_tokens = 100, 50, 150

        class Response:
            output_text, model, usage = text, "test-model", Usage()

        return Response()

    @property
    def last(self) -> dict[str, Any]:
        return self.calls[-1]

    def __enter__(self):
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()


class LearningRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.original = dict(application.app.config)
        application.app.config.update(
            TESTING=True, FEATURE_ASSISTANT_CHAT=True, FEATURE_CHAT_IMAGES=True,
            ASSISTANT_MODEL="openai/gpt-oss-20b", ASSISTANT_DEEP_MODEL="openai/gpt-oss-120b",
            AI_CACHE_DIR=tempfile.mkdtemp(prefix="learnova-wp4-cache-"))
        application.SESSIONS.clear()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = self.register("alice")

    def tearDown(self):
        application.app.config.clear()
        application.app.config.update(self.original)

    def register(self, name, grade="8"):
        client = application.app.test_client()
        client.post("/register", data={"username": name, "email": f"{name}@example.com",
                                       "password": PASSWORD, "grade": grade})
        return client

    def user_id(self, name="alice"):
        with application.app.app_context():
            return db.session.scalar(db.select(application.User.id).where(application.User.username == name))

    def conversation(self, **body):
        response = self.client.post("/api/assistant/conversations", json=body)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()["conversation"]["id"]

    def send(self, conversation_id, message):
        response = self.client.post(f"/api/assistant/conversations/{conversation_id}/messages",
                                    json={"message": message})
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()

    def stored(self, conversation_id):
        with application.app.app_context():
            row = fetch(db.session, application.Conversation, conversation_id)
            db.session.expunge(row)
            return row

    def set_columns(self, conversation_id, **values):
        with application.app.app_context():
            db.session.execute(application.update(application.Conversation).where(
                application.Conversation.id == conversation_id).values(**values))
            db.session.commit()

    def add_mastery(self, subject, concept, score, attempts=3):
        with application.app.app_context():
            db.session.add(application.ConceptMastery(
                user_id=self.user_id(), subject=subject, concept=concept, mastery_score=score,
                attempts=attempts, total_score=int(score * attempts)))
            db.session.commit()


class RequestTests(LearningRouteTestCase):
    def test_the_input_ends_with_the_student_context_after_the_transcript(self):
        conversation_id = self.conversation()
        with FakeAI() as ai:
            self.send(conversation_id, "Was ist eine quadratische Gleichung?")
        sent = ai.last["input"]
        self.assertTrue(sent.startswith("Learner: Was ist eine quadratische Gleichung?"))
        context = sent[sent.rindex(presets.STUDENT_CONTEXT_HEADER):]
        self.assertGreater(sent.rindex(presets.STUDENT_CONTEXT_HEADER), sent.rindex("Learner:"))
        self.assertIn("Class: Grade 8", context)
        self.assertIn("TEACHING\nYou are tutoring a school student.", ai.last["instructions"])
        self.assertNotIn(presets.STUDENT_CONTEXT_HEADER, ai.last["instructions"])

    def test_the_instructions_stay_byte_identical_from_turn_to_turn(self):
        conversation_id = self.conversation()
        with FakeAI("Antwort.\nTOPIC: Mathematics | Quadratische Gleichungen") as ai:
            self.send(conversation_id, "Was ist eine quadratische Gleichung?")
            self.send(conversation_id, "versteh ich nicht")
        self.assertEqual(ai.calls[0]["instructions"], ai.calls[1]["instructions"])
        self.assertNotEqual(ai.calls[0]["input"], ai.calls[1]["input"])

    def test_a_known_subject_adds_the_explanation_level_and_the_topic(self):
        conversation_id = self.conversation()
        self.set_columns(conversation_id, subject="Mathematics", topic="Quadratic equations")
        with FakeAI() as ai:
            self.send(conversation_id, "Und wie geht die pq-Formel?")
        context = ai.last["input"][ai.last["input"].rindex(presets.STUDENT_CONTEXT_HEADER):]
        self.assertIn("Subject: Mathematics | Topic: Quadratic equations", context)
        self.assertIn("EXPLANATION LEVEL", context)

    def test_versteh_ich_nicht_brings_the_confused_directive_and_it_sticks_after_the_reply(self):
        conversation_id = self.conversation()
        self.set_columns(conversation_id, subject="Mathematics", topic="Brüche")
        with FakeAI("Noch einmal anders erklärt.\nTOPIC: Mathematics | Brüche") as ai:
            self.send(conversation_id, "Ich versteh das nicht")
        self.assertIn(REQUEST_DIRECTIVES["confused"], ai.last["input"])
        teaching = json.loads(self.stored(conversation_id).teaching_json)
        self.assertEqual(teaching["topics"]["brüche"]["offset"], -1)
        self.assertEqual(teaching["topics"]["brüche"]["last_request"], "confused")

    @staticmethod
    def level(sent: str) -> int:
        match = re.search(r"EXPLANATION LEVEL[^:\n]*: (\d) of 5", sent)
        assert match, sent
        return int(match.group(1))

    def test_a_confusion_lowers_the_level_for_later_turns_and_a_second_one_explains_differently(self):
        conversation_id = self.conversation()
        self.set_columns(conversation_id, subject="Mathematics", topic="Brüche")
        reply = "Erklärung.\nTOPIC: Mathematics | Brüche"
        with FakeAI(reply) as ai:
            self.send(conversation_id, "Wie addiere ich 1/3 und 1/4?")
            before = self.level(ai.last["input"])
            self.send(conversation_id, "Ich versteh das nicht")
            self.assertIn(REPRESENTATIONS[0], ai.last["input"])
            self.send(conversation_id, "Und 2/5 + 1/10?")
            after = self.level(ai.last["input"])
            self.send(conversation_id, "Ich verstehe es immer noch nicht")
            self.assertIn(REPRESENTATIONS[1], ai.last["input"], "the second confusion gets another representation")
        self.assertLess(after, before)

    def test_a_hard_question_does_not_raise_the_level(self):
        simple, hard = self.conversation(), self.conversation()
        for conversation_id in (simple, hard):
            self.set_columns(conversation_id, subject="Mathematics", topic="Analysis")
        with FakeAI() as ai:
            self.send(simple, "Was ist eine Ableitung?")
            simple_level = self.level(ai.last["input"])
            self.send(hard, "Beweise den Hauptsatz der Differential- und Integralrechnung mit Riemann-Summen "
                            "und erkläre die Rolle der gleichmäßigen Stetigkeit.")
            hard_level = self.level(ai.last["input"])
        self.assertEqual(simple_level, hard_level)

    def test_a_failed_reply_does_not_move_the_level_or_the_topic(self):
        conversation_id = self.conversation()
        with FakeAI(error=ai_service.AIProviderError("provider_timeout", "t")):
            self.send(conversation_id, "versteh ich nicht")
        row = self.stored(conversation_id)
        self.assertEqual((row.teaching_json, row.topic, row.subject), ("{}", "", ""))

    def test_weak_spots_come_from_this_subject_only(self):
        self.add_mastery("Mathe", "Bruchrechnung", 30)        # an old spelling of the same subject
        self.add_mastery("Mathematics", "Potenzen", 50)
        self.add_mastery("Mathematics", "Terme", 90)          # secure: not a weak spot
        self.add_mastery("Mathematics", "Wurzeln", 10, attempts=0)  # never practised
        self.add_mastery("Physics", "Kräfte", 5)              # another subject
        conversation_id = self.conversation()
        self.set_columns(conversation_id, subject="Mathematics", topic="Gleichungen")
        with FakeAI() as ai:
            self.send(conversation_id, "Wie löse ich 2x + 3 = 7?")
        line = next(item for item in ai.last["input"].splitlines() if item.startswith("Known weak spots"))
        self.assertEqual(line, "Known weak spots in Mathematics: Bruchrechnung (30%), Potenzen (50%)")
        self.assertNotIn("Kräfte", ai.last["input"])

    def test_no_weak_spots_without_a_subject(self):
        self.add_mastery("Mathematics", "Bruchrechnung", 30)
        conversation_id = self.conversation()
        with FakeAI() as ai:
            self.send(conversation_id, "Hallo, ich habe eine Frage zu Brüchen.")
        self.assertNotIn("Known weak spots", ai.last["input"])

    def test_the_current_project_of_the_subject_is_named(self):
        with application.app.app_context():
            project = application.LearningProject(user_id=self.user_id(), title="Klassenarbeit Funktionen",
                                                  subject="Mathe", topics_confirmed=True)
            db.session.add(project)
            db.session.flush()
            db.session.add(application.LearningSection(project_id=project.id, position=1, title="Lineare Funktionen"))
            db.session.commit()
        conversation_id = self.conversation()
        self.set_columns(conversation_id, subject="Mathematics", topic="Steigung")
        with FakeAI() as ai:
            self.send(conversation_id, "Was ist die Steigung?")
        self.assertIn('Their Learnova project in Mathematics', ai.last["input"])
        self.assertIn('"Klassenarbeit Funktionen"', ai.last["input"])
        self.assertIn("Lineare Funktionen", ai.last["input"])

    def test_no_account_identifier_reaches_the_provider(self):
        self.add_mastery("Mathematics", "Bruchrechnung", 30)
        conversation_id = self.conversation()
        self.set_columns(conversation_id, subject="Mathematics", topic="Brüche")
        with FakeAI() as ai:
            self.send(conversation_id, "Wie kürze ich 4/8?")
        payload = f"{ai.last['instructions']}\n{ai.last['input']}"
        for identifier in ("alice", "alice@example.com"):
            self.assertNotIn(identifier, payload)

    def test_the_request_is_labelled_with_the_new_prompt_version(self):
        # v5 (CHAT_FIX2): table rows on one line, decimal commas as {,}, partial photo readings.
        self.assertEqual(PROMPT_VERSIONS["assistant_chat"], "assistant_chat:v5")


class PhotoCompactionTests(LearningRouteTestCase):
    def add_photo_turn(self, conversation_id, text, photo_reading):
        with application.app.app_context():
            message = application.ConversationMessage(conversation_id=conversation_id, role="user", content=text)
            db.session.add(message)
            db.session.flush()
            db.session.add(application.ChatAttachment(
                user_id=self.user_id(), conversation_id=conversation_id, message_id=message.id, purpose="chat",
                reading_json=json.dumps(photo_reading, ensure_ascii=False), read_status="ready",
                sent_at=application.utcnow()))
            db.session.add(application.ConversationMessage(conversation_id=conversation_id, role="assistant",
                                                           content=f"Antwort auf {text}"))
            db.session.commit()

    def test_older_photo_turns_are_short_and_the_newest_is_full(self):
        conversation_id = self.conversation()
        old_text = "ALTE AUFGABE " + "lang " * 400 + "ENDE DER ALTEN AUFGABE"
        self.add_photo_turn(conversation_id, "Erstes Foto", reading(
            old_text, summary="Altes Arbeitsblatt zu Brüchen.", unclear=("3/[?4]",)))
        new_text = "NEUE AUFGABE " + "voll " * 300 + "ENDE DER NEUEN AUFGABE"
        self.add_photo_turn(conversation_id, "Zweites Foto", reading(new_text, summary="Neues Blatt."))
        with FakeAI() as ai:
            self.send(conversation_id, "Und Aufgabe 2?")
        sent = ai.last["input"]
        self.assertIn("[Photo 1: Altes Arbeitsblatt zu Brüchen.] ALTE AUFGABE", sent)
        self.assertIn('Unclear: "3/[?4]"', sent)
        self.assertNotIn("ENDE DER ALTEN AUFGABE", sent, "the older reading is shortened")
        self.assertIn("ENDE DER NEUEN AUFGABE", sent, "the newest reading is sent whole")
        older = sent[sent.index("Erstes Foto"):sent.index("Zweites Foto")]
        self.assertLessEqual(len(older), application.ASSISTANT_PHOTO_BRIEF_CHARACTERS + 200)

    def test_a_single_photo_turn_is_the_newest_and_stays_whole(self):
        conversation_id = self.conversation()
        self.add_photo_turn(conversation_id, "Mein Foto", reading("3x + 5 = [?20]"))
        with FakeAI() as ai:
            self.send(conversation_id, "Und der nächste Schritt?")
        self.assertIn("3x + 5 = [?20]", ai.last["input"])
        self.assertIn("What was read from it", ai.last["input"])

    def test_the_first_message_survives_a_long_thread_in_the_request(self):
        application.app.config.update(ASSISTANT_CONTEXT_TOKEN_BUDGET=2600, ASSISTANT_REPLY_TOKEN_RESERVE=600)
        conversation_id = self.conversation()
        with application.app.app_context():
            db.session.add(application.ConversationMessage(
                conversation_id=conversation_id, role="user", content="ERSTE FRAGE: Photosynthese"))
            for index in range(12):
                db.session.add(application.ConversationMessage(
                    conversation_id=conversation_id, role="assistant" if index % 2 == 0 else "user",
                    content=f"turn {index} " + "wort " * 200))
            db.session.commit()
        with FakeAI() as ai:
            self.send(conversation_id, "Und die Dunkelreaktion?")
        sent = ai.last["input"]
        self.assertTrue(sent.startswith("Learner: ERSTE FRAGE: Photosynthese"))
        self.assertIn("earlier messages left out]", sent)
        self.assertIn("Und die Dunkelreaktion?", sent)
        # The STUDENT CONTEXT is paid for out of the same budget (labels and separators aside).
        self.assertLessEqual(conversation.estimate_tokens(sent), 2600 - 600 + 60)


class TrailerStorageTests(LearningRouteTestCase):
    REPLY = ("Eine quadratische Gleichung hat die Form \\(ax^2 + bx + c = 0\\).\n\nWelche Form hat \\(x^2 = 9\\)?\n"
             "CHIPS: Normalform | Allgemeine Form\nTOPIC: Mathematik | Quadratische Gleichungen")

    def test_trailers_are_stripped_from_what_is_stored_and_the_chips_returned(self):
        conversation_id = self.conversation()
        with FakeAI(self.REPLY):
            data = self.send(conversation_id, "hallo")
        reply = data["reply"]
        self.assertNotIn("CHIPS:", reply["content"])
        self.assertNotIn("TOPIC:", reply["content"])
        self.assertTrue(reply["content"].endswith("Welche Form hat \\(x^2 = 9\\)?"))
        self.assertEqual(reply["chips"], ["Normalform", "Allgemeine Form"])
        with application.app.app_context():
            stored = fetch(db.session, application.ConversationMessage, reply["id"])
            self.assertNotIn("TOPIC", stored.content)
            self.assertEqual(json.loads(stored.chips_json), ["Normalform", "Allgemeine Form"])
        reopened = self.client.get(f"/api/assistant/conversations/{conversation_id}").get_json()["conversation"]
        self.assertEqual(reopened["messages"][-1]["chips"], ["Normalform", "Allgemeine Form"])
        self.assertNotIn("TOPIC", reopened["messages"][-1]["content"])

    def test_the_topic_names_subject_topic_and_a_placeholder_title(self):
        conversation_id = self.conversation()
        with FakeAI(self.REPLY):
            data = self.send(conversation_id, "hallo")
        row = self.stored(conversation_id)
        self.assertEqual((row.subject, row.topic), ("Mathematics", "Quadratische Gleichungen"))
        self.assertEqual((row.title, row.title_source), ("Quadratische Gleichungen", "topic"))
        self.assertEqual(data["conversation"]["subject"], "Mathematics")
        self.assertEqual(data["conversation"]["topic"], "Quadratische Gleichungen")

    def test_a_weak_title_is_upgraded_by_the_topic(self):
        conversation_id = self.conversation()
        with FakeAI(self.REPLY):
            self.send(conversation_id, "Kannst du mir helfen?")
        row = self.stored(conversation_id)
        self.assertEqual((row.title, row.title_source), ("Quadratische Gleichungen", "topic"))

    def test_a_title_the_student_chose_or_a_real_first_message_is_kept(self):
        named = self.conversation(title="Meine Mathe")
        with FakeAI(self.REPLY):
            self.send(named, "hallo")
        self.assertEqual((self.stored(named).title, self.stored(named).title_source), ("Meine Mathe", "user"))
        real = self.conversation()
        with FakeAI(self.REPLY):
            self.send(real, "Wie löse ich x^2 - 9 = 0?")
        self.assertEqual(self.stored(real).title_source, "auto")
        self.assertEqual(self.stored(real).topic, "Quadratische Gleichungen")

    def test_topic_none_never_wipes_a_known_topic(self):
        conversation_id = self.conversation()
        with FakeAI(self.REPLY, "Gern geschehen!\nTOPIC: none"):
            self.send(conversation_id, "Wie löse ich x^2 - 9 = 0?")
            self.send(conversation_id, "danke")
        row = self.stored(conversation_id)
        self.assertEqual((row.subject, row.topic), ("Mathematics", "Quadratische Gleichungen"))

    def test_topic_none_is_recorded_only_while_nothing_is_known(self):
        conversation_id = self.conversation()
        with FakeAI("Hier ein Gedicht.\nTOPIC: none"):
            data = self.send(conversation_id, "Schreib mir ein Geburtstagsgedicht für Oma")
        self.assertEqual(self.stored(conversation_id).topic, "none")
        self.assertEqual(data["conversation"]["topic"], "")
        self.assertEqual(data["conversation"]["continue_learning"]["reason"], "not_educational")
        self.assertFalse(data["conversation"]["continue_learning"]["available"])
        with FakeAI("Gern.\nTOPIC: Biology | Fotosynthese"):
            self.send(conversation_id, "Und wie funktioniert Fotosynthese?")
        self.assertEqual(self.stored(conversation_id).topic, "Fotosynthese")

    def test_an_unknown_or_other_subject_is_not_stored(self):
        conversation_id = self.conversation()
        with FakeAI("Antwort.\nTOPIC: Underwater basket weaving | Körbe"):
            self.send(conversation_id, "Wie flechte ich einen Korb unter Wasser?")
        row = self.stored(conversation_id)
        self.assertEqual(row.subject, "")
        self.assertEqual(row.topic, "Körbe")

    def test_a_reply_without_trailers_keeps_what_was_known(self):
        conversation_id = self.conversation()
        with FakeAI(self.REPLY, "Eine Antwort ohne Zusatzzeilen."):
            self.send(conversation_id, "Wie löse ich x^2 - 9 = 0?")
            data = self.send(conversation_id, "Und x^2 = 16?")
        self.assertEqual(data["reply"]["chips"], [])
        row = self.stored(conversation_id)
        self.assertEqual((row.subject, row.topic), ("Mathematics", "Quadratische Gleichungen"))

    def test_the_send_answer_carries_the_continue_learning_state(self):
        conversation_id = self.conversation()
        with FakeAI(self.REPLY):
            data = self.send(conversation_id, "Ich verstehe quadratische Gleichungen überhaupt nicht.")
        state = data["conversation"]["continue_learning"]
        self.assertTrue(state["enabled"])
        self.assertTrue(state["available"])
        self.assertEqual(state["reason"], "")
        self.assertEqual(state["subject"], "Mathematics")
        self.assertEqual(state["topic"], "Quadratische Gleichungen")
        self.assertEqual(state["source_message_id"], data["reply"]["id"])
        self.assertEqual(state["status"], "none")
        listed = self.client.get("/api/assistant/conversations").get_json()["conversations"][0]
        self.assertNotIn("continue_learning", listed, "never in the list")


# ------------------------------------------------------------- opt-in live behaviour
@pytest.mark.live_ai
@unittest.skipUnless(os.getenv("RUN_LIVE_AI_TEST") == "1", "live tutoring behaviour needs RUN_LIVE_AI_TEST=1")
class LiveTutoringBehaviourTests(LearningRouteTestCase):  # pragma: no cover - needs a provider and approval
    def setUp(self):
        super().setUp()
        application.app.config.update(AI_MODE="live", ALLOW_LIVE_AI=True, AI_USAGE_PATH="")

    def live_send(self, conversation_id, message):
        response = self.client.post(f"/api/assistant/conversations/{conversation_id}/messages",
                                    json={"message": message})
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()["reply"]["content"]

    def test_a_direct_question_gets_a_direct_answer_without_a_question(self):
        reply = self.live_send(self.conversation(), "What is the capital of France?")
        self.assertFalse(reply.rstrip().endswith("?"), reply)
        self.assertNotIn("TOPIC:", reply)

    def test_a_misconception_gets_one_check(self):
        reply = self.live_send(self.conversation(), "Is 1/3 + 1/4 = 2/7? I add top and bottom.")
        self.assertLessEqual(reply.count("?"), 2, reply)


if __name__ == "__main__":
    unittest.main()
