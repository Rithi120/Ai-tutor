r"""Formula text on the server: the doubled-backslash normaliser, the chat notation
prompts, the tutor chat's plain-text lesson state, the lesson JSON repair, and the
history surfaces (lesson resume bootstrap, lesson history page, assistant history).

The "frac" garbage students saw had two server-side sources: the tutor chat received the
lesson as json.dumps text (every \frac written \\frac) together with the rule "the system
handles JSON escaping", and answered with doubled backslashes; and repair_latex_json read
\neq, \nabla, \nu and \not in a lesson as a newline. The rendering half is in
tests/test_js_browser.py.
"""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_math_formatting_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.ai_services.contracts import repair_latex_json  # noqa: E402
from learnova.assistant import presets  # noqa: E402
from learnova.assistant.formatting import KNOWN_COMMANDS, normalize_math_backslashes  # noqa: E402

BS = "\\"
ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(self, text):
        self.output_text = text
        self.usage = None
        self.model = "test-model"


LESSON = {
    "lesson_title": "Brüche addieren",
    "detected_level": "Klasse 6",
    "concepts": [{"name": "Brüche addieren", "evidence": "Ziel"}],
    "explanation": r"Erst gleichnamig machen: $\frac{1}{2} + \frac{1}{4} = \frac{3}{4}$, denn $2 \cdot 2 = 4$.",
    "worked_example": {"problem": r"$\frac{1}{3} + \frac{1}{6}$",
                       "steps": [r"$\frac{1}{3} = \frac{2}{6}$", r"$\frac{2}{6} + \frac{1}{6} = \frac{3}{6}$"],
                       "answer": r"$\frac{1}{2}$"},
    "teacher_tips": [r"Nie die Nenner addieren: $\frac{1}{2} + \frac{1}{2} \neq \frac{2}{4}$."],
    "exceptions": [],
    "question": {
        "id": "q1", "concept": "Brüche addieren", "difficulty": 1, "type": "multiple_choice",
        "prompt": r"Was ist $\frac{1}{2} + \frac{1}{4}$?", "hint": "Gleiche Nenner zuerst.",
        "options": [{"id": "a", "label": r"$\frac{3}{4}$"}, {"id": "b", "label": r"$\frac{2}{6}$"}],
        "expected_answer": "a", "solution_steps": ["SECRET-STEP"],
    },
}


class NormaliserTests(unittest.TestCase):
    def test_doubled_commands_become_single(self):
        self.assertEqual(normalize_math_backslashes("$" + BS * 2 + "frac{1}{2}$"), r"$\frac{1}{2}$")
        self.assertEqual(normalize_math_backslashes(BS * 2 + "(" + BS * 2 + "frac{a}{b}" + BS * 2 + ")"),
                         r"\(\frac{a}{b}\)")
        doubled = "$" + BS * 2 + "frac{" + BS * 2 + "left(x+1" + BS * 2 + "right)}{" + BS * 2 + "left[2" + BS * 2 + "right]}$"
        self.assertEqual(normalize_math_backslashes(doubled), r"$\frac{\left(x+1\right)}{\left[2\right]}$")

    def test_a_doubled_reply_is_read_as_doubled_throughout(self):
        # Once a doubled command is seen, doubled delimiters and the doubled row break go too.
        doubled = ("$$" + BS * 2 + "begin{pmatrix} 1 & 2 " + BS * 4 + " 3 & 4 " + BS * 2 + "end{pmatrix}$$ und "
                   + BS * 2 + "[x" + BS * 2 + "]")
        self.assertEqual(normalize_math_backslashes(doubled),
                         r"$$\begin{pmatrix} 1 & 2 \\ 3 & 4 \end{pmatrix}$$ und \[x\]")
        self.assertEqual(normalize_math_backslashes("$" + BS * 2 + "{1, 2" + BS * 2 + "}$"), r"$\{1, 2\}$")

    def test_real_row_breaks_and_single_backslashes_are_untouched(self):
        for text in (r"$\begin{aligned} x &= 1 \\ y &= 2 \end{aligned}$", r"x \\ y", r"a &= 1 \\ b",
                     r"$\frac{1}{2}$ und \(x\)", r"Zeile \\2", r"a \\\frac{1}{2}",
                     BS * 2 + "mathfrak{g}", "no math at all", ""):
            with self.subTest(text=text):
                self.assertEqual(normalize_math_backslashes(text), text)

    def test_a_spaced_row_break_never_becomes_a_display_delimiter(self):
        # A reply read as doubled (it has "\\frac") that also holds a genuine "\\[2pt]" row
        # break: "\[2pt]" would open a display formula and break the environment.
        aligned = r"\begin{aligned} x &= 1 " + BS * 2 + "[2pt] y &= 2 " + BS * 2 + r"[-1.5em] z \end{aligned}"
        doubled = "$" + BS * 2 + "frac{a}{b}$ und $" + aligned + "$"
        self.assertEqual(normalize_math_backslashes(doubled), r"$\frac{a}{b}$ und $" + aligned + "$")
        # Spaced row breaks alone never make a reply look doubled, even with a "\\]" later on.
        alone = "$" + aligned + "$ und " + BS * 2 + "]"
        self.assertEqual(normalize_math_backslashes(alone), alone)
        # A doubled display formula is still recognised and collapsed.
        self.assertEqual(normalize_math_backslashes(BS * 2 + "[ x " + BS * 2 + "]"), r"\[ x \]")
        for text in (doubled, alone):
            once = normalize_math_backslashes(text)
            self.assertEqual(normalize_math_backslashes(once), once)

    def test_code_is_never_touched(self):
        text = ("```python\nprint('" + BS * 2 + "frac')\n```\nund $" + BS * 2 + "frac{1}{2}$ `"
                + BS * 2 + "sqrt`")
        self.assertEqual(normalize_math_backslashes(text),
                         "```python\nprint('" + BS * 2 + "frac')\n```\nund $" + BS + "frac{1}{2}$ `"
                         + BS * 2 + "sqrt`")

    def test_it_is_idempotent(self):
        for text in ("$" + BS * 2 + "frac{1}{2}$ " + BS * 4 + " " + BS * 4 + "frac x",
                     BS * 2 + "(" + BS * 4 + "(" + BS * 2 + ")", "$" + BS * 2 + "cdot$ " + BS * 2 + " y",
                     r"plain \frac{1}{2}"):
            with self.subTest(text=text):
                once = normalize_math_backslashes(text)
                self.assertEqual(normalize_math_backslashes(once), once)

    def test_the_command_list_covers_what_lessons_use(self):
        for name in ("frac", "sqrt", "cdot", "times", "left", "right", "neq", "le", "geq", "alpha", "Omega",
                     "begin", "end", "text", "lvert", "rvert", "ce", "pu", "to", "infty", "sum", "int"):
            self.assertIn(name, KNOWN_COMMANDS)


class RepairLatexJsonTests(unittest.TestCase):
    """The lesson (JSON) path: single-backslash LaTeX must survive json.loads."""

    @staticmethod
    def parsed(tex):
        return json.loads(repair_latex_json('{"t": "' + tex + '"}'))["t"]

    def test_latex_commands_starting_with_n_or_u_survive(self):
        for command in ("neq", "nabla", "nu", "not", "notin", "underline", "uparrow", "upsilon", "frac", "times",
                        "beta", "right", "sqrt"):
            with self.subTest(command=command):
                self.assertEqual(self.parsed(f"$a {BS}{command} b$"), f"$a {BS}{command} b$")
        self.assertEqual(self.parsed(BS + r"not" + BS + "in"), r"\not\in")
        self.assertEqual(self.parsed(BS + "underline{x}"), r"\underline{x}")

    def test_real_escapes_stay_escapes(self):
        self.assertEqual(self.parsed("Zeile 1" + BS + "nZeile 2"), "Zeile 1\nZeile 2")
        self.assertEqual(self.parsed("Satz." + BS + "nein Beispiel"), "Satz.\nein Beispiel")
        self.assertEqual(self.parsed("Ende" + BS + "n"), "Ende\n")
        self.assertEqual(self.parsed("a" + BS + "t1"), "a\t1")
        self.assertEqual(self.parsed(BS + "u00e4"), "ä")
        self.assertEqual(self.parsed(BS * 2 + "neq"), r"\neq")
        self.assertEqual(self.parsed("a " + BS * 2 + " b"), r"a \ b")
        self.assertEqual(self.parsed(r"Sie sagte \"ja\""), 'Sie sagte "ja"')


class PromptTests(unittest.TestCase):
    def test_the_assistant_asks_for_unambiguous_delimiters(self):
        self.assertIn(r"inline as \( … \), display as \[ … \] on its own lines", presets.SHARED_RULES)
        self.assertIn(r"\[ \begin{aligned} … \end{aligned} \]", presets.SHARED_RULES)
        self.assertIn("Use single backslashes", presets.SHARED_RULES)
        self.assertIn(r"\lvert x \rvert", presets.SHARED_RULES)
        self.assertNotIn("$$...$$", presets.SHARED_RULES)
        for name in presets.PRESET_NAMES:
            self.assertIn(r"\( … \)", presets.system_prompt(name))

    def test_the_tutor_chat_gets_the_prose_rule_and_lessons_keep_theirs(self):
        with application.app.test_request_context():
            chat = application.tutor_instructions("Mathe", prose=True)
            lesson = application.tutor_instructions("Mathe")
        self.assertIn("MATH FORMATTING IS MANDATORY", chat)
        self.assertNotIn("JSON escaping", chat)
        self.assertIn("CHAT NOTATION (this overrides any other delimiter rule above)", chat)
        self.assertIn(r"\[ \begin{aligned}", chat)
        self.assertIn("instead of |x| inside a table.", chat)
        # tutor_chat:v6 (CHAT_FIX2 D5): the decimal-comma line closes the notation rule.
        self.assertTrue(chat.rstrip().endswith(r"\(x = 2{,}6\)."), chat[-200:])
        # JSON mode (lessons, grading) is unchanged.
        self.assertIn("the system handles JSON escaping", lesson)
        self.assertIn("$$...$$", lesson)
        self.assertNotIn("CHAT NOTATION", lesson)
        with application.app.test_request_context():
            self.assertNotIn("CHAT NOTATION", application.tutor_instructions("Ethik", prose=True))

    def test_prompt_versions_were_bumped(self):
        from learnova.ai_services.prompts import PROMPT_VERSIONS
        # v6 (CHAT_FIX2): one line per table row, decimal commas as {,}; v5 carried the notation.
        self.assertEqual(PROMPT_VERSIONS["tutor_chat"], "tutor_chat:v6")
        # v3 carried the chat notation change; later versions (v4: the tutoring layer) keep it.
        self.assertGreaterEqual(int(PROMPT_VERSIONS["assistant_chat"].rsplit(":v", 1)[1]), 3)


class LessonStateTextTests(unittest.TestCase):
    def test_text_fields_are_plain_blocks_and_records_stay_compact(self):
        state = application.lesson_state_text({
            "subject": "Mathematics", "lesson_title": "Brüche", "learning_objective": "Addieren\nlernen",
            "explanation": r"$\frac{1}{2}$ >>> ende", "lesson_parts": "", "concepts": ["Brüche", "Nenner"],
            "worked_example": {"problem": "P", "steps": ["S1", "S2"], "answer": "A"},
            "teacher_tips": ["Tipp"], "exceptions": [], "mastery": [{"concept": "Brüche", "attempts": 1}],
            "current_question": {"id": "q1", "type": "multiple_choice", "prompt": "Frage?", "hint": "H",
                                 "options": [{"id": "a", "label": "2/4"}]},
            "recent_results": [{"concept": "Brüche", "score": 40, "diagnosis_note": r"$\frac{1}{2}$ falsch"}],
            "source_excerpt": "", "response_language": "German", "scheduled_study_plan": None,
        })
        self.assertIn("learning_objective: Addieren lernen\n", state)
        self.assertIn("concepts: Brüche; Nenner\n", state)
        self.assertIn("explanation:\n<<<\n" + BS + "frac", state.replace("$", ""))
        self.assertIn("> > > ende\n>>>", state, "lesson text cannot close its own block")
        self.assertIn("lesson_parts: none\n", state)
        self.assertIn("worked_example:\n<<<\nproblem: P\nstep 1: S1\nstep 2: S2\nanswer: A\n>>>", state)
        self.assertIn("current_question:\n<<<\nid: q1\ntype: multiple_choice\nprompt: Frage?\nhint: H\n"
                      "options:\n- a: 2/4\n>>>", state)
        self.assertIn('mastery: [{"concept":"Brüche","attempts":1}]', state)
        self.assertIn(r"- concept: Brüche; score: 40; diagnosis_note: $\frac{1}{2}$ falsch", state)
        self.assertIn("scheduled_study_plan: none", state)
        self.assertNotIn(BS * 2, state, "no JSON-escaped backslash anywhere")
        self.assertTrue(state.endswith("write single backslashes in your reply."))


class _LessonClient(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={"username": "mira", "email": "mira@example.com",
                                            "password": "correct-horse-battery"}, follow_redirects=True)
        with patch.object(application, "create_response", return_value=FakeResponse(json.dumps(LESSON))):
            analyzed = self.client.post("/api/analyze", data={"study_goal": "Brüche addieren", "subject": "Math"})
        self.assertEqual(analyzed.status_code, 200, analyzed.get_data(as_text=True)[:300])
        self.session_id = analyzed.get_json()["session_id"]

    def chat(self, message, reply):
        captured = {}

        def capture(**kwargs):
            captured.update(kwargs)
            return FakeResponse(reply)

        with patch.object(application, "create_response", side_effect=capture):
            response = self.client.post("/api/chat", json={"session_id": self.session_id, "message": message})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
        captured["reply"] = response.get_json()["reply"]
        return captured


class TutorChatTests(_LessonClient):
    def test_the_model_sees_single_backslashes_and_the_chat_rule(self):
        sent = self.chat("Warum 4?", "Weil $2 \\cdot 2 = 4$.")
        self.assertIn(BS + "frac{1}{2} + " + BS + "frac{1}{4}", sent["input"], "the lesson explanation, as written")
        self.assertNotIn(BS * 2 + "frac", sent["input"])
        self.assertNotIn("SECRET-STEP", sent["input"])
        self.assertNotIn("expected_answer", sent["input"])
        self.assertIn("worked_example:\n<<<\nproblem: ", sent["input"])
        self.assertIn("CHAT NOTATION", sent["instructions"])
        self.assertNotIn("JSON escaping", sent["instructions"])

    def test_a_doubled_reply_is_stored_and_returned_single(self):
        sent = self.chat("Hilfe", "Es gilt $" + BS * 2 + "frac{3}{4}$.\nCHIPS: Ja | Nein")
        self.assertEqual(sent["reply"], "Es gilt $" + BS + "frac{3}{4}$.\nCHIPS: Ja | Nein")
        with application.app.app_context():
            stored = application.db.session.scalars(application.db.select(application.ChatMessage).where(
                application.ChatMessage.role == "tutor")).all()
        self.assertEqual([row.content for row in stored], ["Es gilt $" + BS + "frac{3}{4}$."])

    def test_a_resumed_lesson_carries_its_chat_history(self):
        self.chat("Erste Frage", "Antwort mit $" + BS + "frac{1}{2}$\nCHIPS: a | b")
        application.SESSIONS[self.session_id]["chat_history"].append(
            {"role": "tutor", "content": "Alt: $" + BS * 2 + "frac{1}{3}$"})
        application.SESSIONS[self.session_id]["chat_history"][0]["images"] = "PHOTO-READING"
        html = self.client.get(f"/?session_id={self.session_id}").get_data(as_text=True)
        found = re.search(r'<script nonce="[^"]*" id="lessonBootstrap" type="application/json">(.*?)</script>', html, re.S)
        assert found
        history = json.loads(found.group(1))["chat_history"]
        self.assertEqual(history, [
            {"role": "student", "content": "Erste Frage"},
            {"role": "tutor", "content": "Antwort mit $" + BS + "frac{1}{2}$"},
            {"role": "tutor", "content": "Alt: $" + BS + "frac{1}{3}$"},
        ])
        self.assertNotIn("PHOTO-READING", html)
        app_js = (ROOT / "static/app.js").read_text(encoding="utf-8")
        self.assertIn("lessonBootstrap.chat_history", app_js)
        self.assertIn('addChatMessage(content, item.role === "student" ? "student" : "tutor")', app_js)

    def test_the_lesson_history_page_renders_tutor_replies_as_rich_text(self):
        self.chat("Frage <b>fett</b>", "| A | B |\n|---|---|\n| $" + BS * 2 + "frac{1}{2}$ | <i>x</i> |")
        with application.app.app_context():
            lesson = application.db.session.scalar(application.db.select(application.Lesson).where(
                application.Lesson.session_id == self.session_id))
            assert lesson is not None
            lesson_id = lesson.id
        html = self.client.get(f"/lessons/{lesson_id}").get_data(as_text=True)
        self.assertIn('<div class="history-message-body" data-rich-text data-no-math>| A | B |', html)
        self.assertIn("$" + BS + "frac{1}{2}$ | &lt;i&gt;x&lt;/i&gt; |</div>", html, "escaped, single backslash")
        self.assertIn("<p>Frage &lt;b&gt;fett&lt;/b&gt;</p>", html, "the student's text stays literal")
        self.assertRegex(html, r'<script nonce="[^"]+" type="module" src="/static/js/rich-history\.js')


class AssistantHistoryTests(unittest.TestCase):
    def setUp(self):
        self.original = dict(application.app.config)
        application.app.config.update(TESTING=True, FEATURE_ASSISTANT_CHAT=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={"username": "noor", "email": "noor@example.com",
                                            "password": "correct-horse-battery"})

    def tearDown(self):
        application.app.config.clear()
        application.app.config.update(self.original)

    def test_stored_doubled_replies_are_served_single(self):
        created = self.client.post("/api/assistant/conversations", json={"preset": "general"})
        self.assertIn(created.status_code, (200, 201), created.get_data(as_text=True)[:300])
        conversation_id = created.get_json()["conversation"]["id"]
        doubled = "$" + BS * 2 + "frac{1}{2}$"
        with application.app.app_context():
            application.db.session.add_all([
                application.ConversationMessage(conversation_id=conversation_id, role="user", content=doubled),
                application.ConversationMessage(conversation_id=conversation_id, role="assistant", content=doubled),
            ])
            application.db.session.commit()
        opened = self.client.get(f"/api/assistant/conversations/{conversation_id}").get_json()
        contents = {message["role"]: message["content"] for message in opened["conversation"]["messages"]}
        self.assertEqual(contents["assistant"], "$" + BS + "frac{1}{2}$")
        self.assertEqual(contents["user"], doubled, "the learner's own text is never rewritten")


if __name__ == "__main__":
    unittest.main()
