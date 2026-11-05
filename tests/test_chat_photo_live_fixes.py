"""The defects the live check of 2026-11-02 found in chat photos and chat replies (D1-D8).

Every provider answer used here is the real one: tests/fixtures/live_chat_2026-11-02.json
holds the raw text Groq's qwen/qwen3.8-27b and gpt-oss returned in that run (synthetic
worksheets, no student data), replayed through the real routes and gateway with only the
OpenAI SDK client faked (the harness of tests/test_vision_limits.py).

- D1 a worksheet photo could not be read on a 1000-output-tokens-per-minute tier: the
  reading asked for 3500, was refused, the 950 retry cut the verbose JSON off after 8 of
  10 blocks and the student was told to send a sharper photo;
- D2 those failed readings were charged to the free chat allowance;
- D3 a table cell broken over two lines split the row (prompt rule; renderer: the browser
  tests in tests/test_chat_fix2_browser.py);
- D4 "TOPIC: Algebra | ..." left the conversation without a subject;
- D5 German decimal commas inside formulas rendered as "0, 34";
- D6 Flask-Limiter's hour-scale Retry-After replaced the 5 seconds of conversation_busy;
- D7 the learned output cap was forgotten by every new process;
- D8 a legible photo cropped to one exercise was told to "move closer".
"""

from __future__ import annotations

import io
import json
import random
import re
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_continue_learning import ContinueLearningTestCase
from tests.test_vision_limits import CASES, READING, FakeProvider, GatewayCase, count_images, groq_error

import app as application
from learnova.ai_services import contracts
from learnova.ai_services import prompts as ai_prompts
from learnova.ai_services import service as ai_service
from learnova.assistant import formatting, presets
from learnova.lessons.subjects import canonical_subject
from learnova.translations import translate
from learnova.vision import intake, reading as vision_reading
from learnova.vision import prompts as vision_prompts

db = application.db
FIXTURES = Path(__file__).resolve().parent / "fixtures"
LIVE = json.loads((FIXTURES / "live_chat_2026-11-02.json").read_text(encoding="utf-8"))
CROP = (FIXTURES / "live_chat_2026-11-02_crop.jpg").read_bytes()
# Groq counted 950 output tokens for 2039 characters of the live reading: the fewest
# characters per token measured in that run, so the safest way to turn characters into
# Groq output tokens without calling Groq.
CHARACTERS_PER_OUTPUT_TOKEN = 2039 / 950
VISION = ("groq", "qwen/qwen3.8-27b")


def live(name):
    return LIVE[name]["text"]


class ReplayProvider(FakeProvider):
    """FakeProvider whose readings are served from a queue of (text, output tokens)."""

    def __init__(self):
        super().__init__()
        self.reading_queue: list[tuple[str, int]] = []

    def __call__(self, **client_options):
        self.clients.append(client_options)
        provider = self

        class Responses:
            def create(self, **kwargs):
                provider.calls.append(kwargs)
                images = count_images(kwargs.get("input"))
                if images and provider.image_failures:
                    failure = provider.image_failures.pop(0)
                    if failure is not None:
                        raise failure
                if not images and provider.reply_failure is not None:
                    raise provider.reply_failure
                output_tokens = 200
                if images and provider.reading_queue:
                    text, output_tokens = provider.reading_queue.pop(0)
                elif images:
                    text = provider.reading if isinstance(provider.reading, str) else json.dumps(provider.reading)
                else:
                    text = provider.reply
                usage = SimpleNamespace(input_tokens=1000, output_tokens=output_tokens,
                                        total_tokens=1000 + output_tokens)
                return SimpleNamespace(output_text=text, model=kwargs.get("model"), usage=usage)

        return SimpleNamespace(responses=Responses())


class LiveCase(GatewayCase):
    def setUp(self):
        super().setUp()
        self.openai_patch.stop()
        self.provider = ReplayProvider()
        self.openai_patch = patch.object(ai_service, "OpenAI", self.provider)
        self.openai_patch.start()

    def reading_text(self, call):
        return json.dumps(call["input"], ensure_ascii=False)

    def stored_readings(self):
        return [(row.read_status, json.loads(row.reading_json) if row.reading_json else None)
                for row in self.rows(application.ChatAttachment)]

    def german(self):
        with application.app.app_context():
            user = db.session.scalar(db.select(application.User).where(application.User.username == "alice"))
            user.preferred_language = "de"
            db.session.commit()


# ------------------------------------------------------------------------- D1 (a)
class CompactChatReadingTests(LiveCase):
    def test_a_chat_photo_is_read_in_the_compact_shape_within_the_output_minute(self):
        response = self.send(self.conversation(), message="Aufgabe 2?", attachments=[self.attachment()])
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        (reading,) = self.provider.readings()
        request = self.reading_text(reading)
        self.assertIn('\\"lines\\"', request, "the chat reading asks for lines")
        for heavy in ("bbox", "author", "confidence"):
            self.assertNotIn(f'\\"{heavy}\\"', request, f"the chat reading does not ask for {heavy}")
            self.assertNotIn(f'"{heavy}"', reading["instructions"], "nor does the output contract")
        self.assertIn("PROMPT_VERSION: image_understanding:chat1", reading["instructions"])
        self.assertEqual(reading["max_output_tokens"], 900, "under Groq's 1000 output tokens per minute")
        record = next(item for item in self.usage_records() if item["task_type"] == "image_understanding")
        self.assertEqual(record["prompt_version"], "image_understanding:chat1+chat")
        self.assertEqual(ai_prompts.PROMPT_VARIANTS["image_understanding+chat"], "image_understanding:chat1")

    def test_the_shipped_chat_reading_samples_validate_and_read_back(self):
        for language in ("en", "de"):
            with self.subTest(language=language):
                fixture = json.loads((FIXTURES / "ai" / language / "valid.json").read_text(encoding="utf-8"))
                sample = fixture["image_understanding+chat"]["output_text"]
                contracts.validate_output("image_understanding", json.dumps(sample, ensure_ascii=False),
                                          "image_understanding:chat1", {"prompt_variant": "image_understanding+chat"})
                parsed = vision_reading.normalize_reading(sample, 1)
                self.assertEqual([(block.type, block.author) for block in parsed.blocks],
                                 [("task", "unknown"), ("formula", "student")])
                self.assertTrue(parsed.needs_clarification)
                self.assertIn("[?20]", vision_reading.reading_context(parsed))

    def test_lesson_material_keeps_the_full_reading_and_its_version(self):
        files = [(io.BytesIO(CASES["math_handwritten"].data), "page.jpg")]
        self.client.post("/api/analyze", data={"study_goal": "Gleichungen", "subject": "Mathematics",
                                               "images": files}, content_type="multipart/form-data")
        reading = self.provider.readings()[0]
        request = self.reading_text(reading)
        self.assertIn('\\"bbox\\"', request)
        self.assertIn('\\"author\\"', request)
        self.assertNotIn('\\"lines\\"', request)
        self.assertGreater(reading["max_output_tokens"], 950)
        record = next(item for item in self.usage_records() if item["task_type"] == "image_understanding")
        self.assertEqual(record["prompt_version"], "image_understanding:v2+lesson")
        self.assertEqual(ai_service.PROMPT_VERSIONS["image_understanding"], "image_understanding:v2")
        self.assertNotIn('"lines"', vision_prompts.reading_instructions("lesson"))

    def test_the_live_worksheet_fits_the_chat_cap_in_the_compact_shape(self):
        """Worksheet 1 of the live run, as the compact reading writes it, in Groq output tokens."""

        live_blocks = contracts.salvage_cut_off_json(live("reading_cut_off_one_photo"))[0]["blocks"]
        self.assertEqual(len(live_blocks), 7, "the v2 reading got 7 complete blocks into 950 tokens")
        lines = [
            {"type": "heading", "text": "Arbeitsblatt Mathe – Klasse 9b"},
            {"type": "task", "text": "Aufgabe 1: Kürze den Bruch", "latex": r"\frac{12}{18}"},
            {"type": "task", "text": "Aufgabe 2: Löse die Gleichung", "latex": r"\frac{x+3}{4} = \frac{2x-1}{3}"},
            {"type": "task", "text": "Aufgabe 3: Berechne", "latex": r"\frac{3}{4} + \frac{5}{6}"},
            {"type": "text", "text": "Meine Rechnung zu Aufgabe 2:"},
            {"type": "working", "latex": r"3 \cdot (x+3) = 4 \cdot (2x-1)"},
            {"type": "working", "latex": "3x + 9 = 8x - 4"},
            {"type": "working", "text": "??? wie weiter"},
        ]
        written = {line.get("latex") for line in lines}
        self.assertTrue({block["latex"] for block in live_blocks if block["latex"]} <= written,
                        "the same content the live reading had read")
        compact = {"images": [{"index": 1, "kind": "worksheet", "rotation": 0, "cut_off": False,
                               "legibility": "clear"}], "lines": lines, "diagrams": [], "uncertain": [],
                   "clarification_question": "",
                   "summary": "Arbeitsblatt mit drei Bruchaufgaben; die Rechnung zu Aufgabe 2 bricht ab.",
                   "language": "de"}
        text = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        tokens = len(text) / CHARACTERS_PER_OUTPUT_TOKEN
        self.assertLess(tokens, 600, f"{len(text)} characters, about {tokens:.0f} output tokens")
        self.assertLessEqual(tokens, application.app.config["AI_CHAT_PHOTO_READING_MAX_OUTPUT_TOKENS"])
        # The schema in the prompt is the shape that was measured, and it reads back fully.
        schema = vision_prompts.compact_schema(1)
        self.assertEqual(set(schema), set(compact))
        self.assertEqual(set(schema["lines"][0]), {"type", "text", "latex"})
        parsed = vision_reading.normalize_reading(json.loads(text), 1)
        self.assertEqual(len(parsed.blocks), 8)
        self.assertEqual([block.type for block in parsed.blocks][:5],
                         ["heading", "task", "task", "task", "printed_text"])
        working = [block for block in parsed.blocks if block.author == "student"]
        self.assertEqual([block.latex for block in working],
                         [r"3 \cdot (x+3) = 4 \cdot (2x-1)", "3x + 9 = 8x - 4", ""])
        self.assertTrue(all(block.text for block in parsed.blocks), "a maths-only line keeps its LaTeX as text")
        self.assertIn("(task) Aufgabe 2: Löse die Gleichung", vision_reading.reading_context(parsed))


# ------------------------------------------------------------------------- D1 (b), (d)
class CutOffReadingTests(LiveCase):
    def test_the_live_cut_off_reading_keeps_its_complete_lines(self):
        self.german()
        self.provider.reading_queue = [(live("reading_cut_off_one_photo"), 950)]
        response = self.send(self.conversation(), message="Kannst du mir bei Aufgabe 2 helfen?",
                             attachments=[self.attachment()])
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        ((status, stored),) = self.stored_readings()
        self.assertEqual(status, "ready")
        self.assertEqual(len(stored["blocks"]), 7)
        self.assertTrue(stored["images"][0]["partial"])
        self.assertNotIn("3x + 9 = 8x - 4", json.dumps(stored), "an incomplete line is never kept")
        notes = response.get_json()["image_notes"]
        self.assertEqual(notes["hints"][0], translate(
            "Only the first part of your photo could be read; the rest was not read. Crop it to the exercise "
            "you need, or send it again in a minute.", "de"))
        reply_input = json.dumps(self.provider.replies()[0]["input"], ensure_ascii=False)
        self.assertIn("only partly read", reply_input)
        self.assertIn("3 · (x + 3) = 4 · (2x - 1)", reply_input)
        self.assertIn("only partly read", self.provider.replies()[0]["instructions"])

    def test_a_cut_off_reading_is_never_cached(self):
        photo = CASES["math_handwritten"]
        self.provider.reading_queue = [(live("reading_cut_off_one_photo"), 950), (json.dumps(READING), 200)]
        first = self.send(self.conversation(), message="Eins", attachments=[self.attachment(photo.name)])
        self.assertEqual(first.status_code, 201)
        ai_service._RECENT_RESULTS.clear()          # a minute later, as the student is told
        again = self.send(self.conversation(), message="Eins", attachments=[self.attachment(photo.name)])
        self.assertEqual(again.status_code, 201)
        self.assertEqual(len(self.provider.readings()), 2, "the same photo is really read again")
        ((_s1, cut), (_s2, complete)) = self.stored_readings()
        self.assertEqual({cut["images"][0]["partial"], complete["images"][0]["partial"]}, {True, False})

    def test_the_live_two_photo_reading_says_which_photo_was_cut(self):
        self.provider.reading_queue = [(live("reading_cut_off_two_photos"), 950)]
        photos = [self.attachment("printed_de_clear"), self.attachment("right_triangle")]
        response = self.send(self.conversation(), message="Aufgabe 4 und 6?", attachments=photos)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertEqual(len(self.provider.readings()), 1, "no output limit known: read together")
        first, second = (reading for _status, reading in self.stored_readings())
        self.assertEqual((first["images"][0]["partial"], second["images"][0]["partial"]), (False, True))
        self.assertEqual((len(first["blocks"]), len(second["blocks"])), (6, 2))
        self.assertIn("Only the first part of photo 2 could be read", response.get_json()["image_notes"]["hints"][0])

    def test_a_cut_off_with_no_complete_line_fails_honestly(self):
        self.german()
        self.provider.reading = '{"blocks": [{"type": "formula", "text": "3x + 5'
        response = self.send(self.conversation(), message="Hilfe", attachments=[self.attachment()])
        self.assertEqual((response.status_code, response.get_json()["code"]), (422, "photo_read_failed"))
        message = response.get_json()["error"]
        self.assertNotIn("schärfer", message)
        self.assertEqual(message, translate(
            "The photo reader could not finish reading your photo this time. Crop it to the exercise you need, "
            "or try again in a minute.", "de"))
        self.assertNotEqual(message, "The photo reader could not finish reading your photo this time. Crop it to "
                                     "the exercise you need, or try again in a minute.", "translated")

    def test_salvage_is_only_for_the_chat_reading(self):
        cut = live("reading_cut_off_one_photo")
        with self.assertRaises(contracts.AIValidationError):
            contracts.validate_output("image_understanding", cut, "image_understanding:v2", {})
        chat = {"prompt_variant": "image_understanding+chat"}
        contracts.validate_output("image_understanding", cut, "image_understanding:chat1", chat)
        with self.assertRaises(contracts.AIValidationError):
            contracts.validate_output("image_understanding", '{"lines": [{"type": "task", "text": "Aufg',
                                      "image_understanding:chat1", chat)

    def test_salvage_keeps_complete_lines_only(self):
        payload, reached = contracts.salvage_cut_off_json(live("reading_cut_off_two_photos"))
        self.assertEqual([block["text"] for block in payload["blocks"]][-1],
                         "Rechtwinkliges Dreieck mit Katheten a und b sowie Hypotenuse c. Rechtswinkel ist markiert.")
        self.assertEqual(len(payload["blocks"]), 8)
        self.assertIn("images", reached)
        self.assertNotIn("blocks", reached)
        self.assertTrue(contracts.json_cut_off(live("reading_cut_off_two_photos")))
        self.assertFalse(contracts.json_cut_off(live("reading_complete_crop")))
        # A cut right after a complete line (the old two-closer repair accepted it silently).
        compact = '{"images":[{"index":1}],"lines":[{"type":"task","text":"Aufgabe 1"},{"type":"working","latex":"x=2"}'
        self.assertTrue(contracts.json_cut_off(compact))
        self.assertEqual(len(contracts.salvage_cut_off_json(compact)[0]["lines"]), 2)
        self.assertIsNone(contracts.salvage_cut_off_json('{"images":[{"index":1'))


# ------------------------------------------------------------------------- D1 (c)
class SeparatePhotoReadingTests(LiveCase):
    def learn(self, cap=950):
        ai_service._LEARNED_OUTPUT_CAPS[VISION] = (cap, time.monotonic() + 3600)

    def test_with_a_known_output_minute_two_photos_are_read_one_per_request(self):
        self.learn()
        self.provider.reading_queue = [(json.dumps(READING), 300), (json.dumps(READING), 250)]
        photos = [self.attachment("printed_de_clear"), self.attachment("math_handwritten")]
        response = self.send(self.conversation(), message="Beide?", attachments=photos)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        first, second = self.provider.readings()
        self.assertEqual((count_images(first["input"]), count_images(second["input"])), (1, 1))
        self.assertEqual(first["max_output_tokens"], 900)
        self.assertEqual(second["max_output_tokens"], 950 - 300, "what is left of the minute")
        self.assertEqual([status for status, _reading in self.stored_readings()], ["ready", "ready"])
        reply_input = json.dumps(self.provider.replies()[0]["input"], ensure_ascii=False)
        self.assertIn("[Image 1", reply_input)
        self.assertIn("[Image 2", reply_input)

    def test_a_refusal_of_the_second_photo_keeps_the_first_reading(self):
        self.learn()
        full = ("Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_x` service tier `on_demand` "
                "on output tokens per minute (OTPM): Limit 1000, Used 700, Requested 650. Please try again in 21s.")
        self.provider.image_failures = [None, groq_error(429, full, retry_after=21)]
        self.provider.reading_queue = [(json.dumps(READING), 300)]
        thread = self.conversation()
        photos = [self.attachment("printed_de_clear"), self.attachment("math_handwritten")]
        response = self.send(thread, message="Beide?", attachments=photos)
        self.assertEqual((response.status_code, response.get_json()["code"]), (503, "photo_reader_busy"))
        self.assertEqual(response.headers["Retry-After"], "21")
        self.assertEqual(sorted(status for status, _reading in self.stored_readings()), ["pending", "ready"])
        self.assertEqual(self.rows(application.ConversationMessage), [], "nothing of the message is stored")
        self.provider.calls.clear()
        ai_service._MODEL_COOLDOWNS.clear()
        again = self.send(thread, message="Beide?", attachments=photos)
        self.assertEqual(again.status_code, 201, again.get_data(as_text=True))
        self.assertEqual(len(self.provider.readings()), 1, "only the photo not yet read is read")

    def test_without_room_left_in_the_minute_the_second_photo_is_not_sent(self):
        self.learn()
        self.provider.reading_queue = [(json.dumps(READING), 800)]
        photos = [self.attachment("printed_de_clear"), self.attachment("math_handwritten")]
        response = self.send(self.conversation(), message="Beide?", attachments=photos)
        self.assertEqual((response.status_code, response.get_json()["code"]), (503, "photo_reader_busy"))
        self.assertEqual(len(self.provider.readings()), 1, "a request that cannot fit is not sent")
        self.assertLessEqual(int(response.headers["Retry-After"]), 60)

    def test_the_first_refusal_on_an_unknown_tier_teaches_the_split(self):
        refusal = LIVE["otpm_refusals"][0].replace("Requested 2274", "Requested 1800")
        self.provider.image_failures = [groq_error(429, refusal)]
        photos = [self.attachment("printed_de_clear"), self.attachment("math_handwritten")]
        first = self.send(self.conversation(), message="Beide?", attachments=photos)
        self.assertEqual(first.status_code, 201, first.get_data(as_text=True))
        self.assertEqual([call["max_output_tokens"] for call in self.provider.readings()], [1800, 950])
        self.provider.calls.clear()
        photos = [self.attachment("table_3x3"), self.attachment("handwriting_de_ruled")]
        second = self.send(self.conversation(), message="Und diese?", attachments=photos)
        self.assertEqual(second.status_code, 201, second.get_data(as_text=True))
        self.assertEqual([count_images(call["input"]) for call in self.provider.readings()], [1, 1])


# ------------------------------------------------------------------------- D2
class ReadingAllowanceTests(LiveCase):
    def chat_events(self):
        with application.app.app_context():
            return [(row.status, row.amount) for row in db.session.scalars(db.select(
                application.UsageQuotaEvent).where(application.UsageQuotaEvent.kind == "chat"))]

    def setUp(self):
        super().setUp()
        application.app.config.update(SUBSCRIPTION_QUOTAS_ENFORCED=True)

    def test_a_failed_reading_costs_no_allowance(self):
        self.provider.reading = '{"blocks": [{"type": "formula", "text": "3x + 5'
        response = self.send(self.conversation(), message="Hilfe", attachments=[self.attachment()])
        self.assertEqual(response.status_code, 422)
        events = self.chat_events()
        self.assertTrue(events, "the reading was reserved")
        self.assertEqual([status for status, _amount in events], ["released"] * len(events))
        self.assertEqual(sum(amount for _status, amount in events), 0)

    def test_a_reading_that_found_nothing_costs_no_allowance(self):
        self.provider.reading = json.dumps({"images": [{"index": 1, "legibility": "unreadable"}], "lines": [],
                                            "summary": "", "language": "de"})
        response = self.send(self.conversation(), attachments=[self.attachment()])
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertEqual(self.provider.replies(), [], "the fixed answer, no reply call")
        self.assertEqual(sum(amount for _status, amount in self.chat_events()), 0)

    def test_a_usable_reading_is_still_charged(self):
        self.provider.reading_queue = [(live("reading_cut_off_one_photo"), 950)]
        response = self.send(self.conversation(), message="Hilfe", attachments=[self.attachment()])
        self.assertEqual(response.status_code, 201)
        committed = [amount for status, amount in self.chat_events() if status == "committed"]
        self.assertEqual(len(committed), 2, "the partial reading and the reply")
        self.assertTrue(all(amount > 0 for amount in committed))


# ------------------------------------------------------------------------- D3, D5 prompts
class ReplyPromptRuleTests(unittest.TestCase):
    def test_tables_keep_each_row_on_one_line(self):
        for name, text in (("assistant", presets.SHARED_RULES), ("tutor", ai_prompts.TUTOR_CHAT_COACHING)):
            with self.subTest(prompt=name):
                self.assertIn("never break a line inside a table cell", text)
                self.assertIn("below the table", text)

    def test_decimal_commas_in_formulas(self):
        for name, text in (("assistant", presets.SHARED_RULES), ("tutor", application.CHAT_MATH_RULE)):
            with self.subTest(prompt=name):
                self.assertIn(r"\(0{,}34\)", text)

    def test_versions_were_bumped(self):
        self.assertEqual(ai_prompts.PROMPT_VERSIONS["assistant_chat"], "assistant_chat:v5")
        self.assertEqual(ai_prompts.PROMPT_VERSIONS["tutor_chat"], "tutor_chat:v6")
        for language in ("en", "de"):
            meta = json.loads((FIXTURES / "ai" / language / "valid.json").read_text(encoding="utf-8"))["_meta"]
            self.assertEqual(meta["prompt_versions"], ai_prompts.PROMPT_VERSIONS)
            self.assertEqual(meta["prompt_variants"], ai_prompts.PROMPT_VARIANTS)


# ------------------------------------------------------------------------- D5 normaliser
class DecimalCommaTests(unittest.TestCase):
    @staticmethod
    def convert(text):
        return formatting.decimal_commas_in_math(text)

    def test_digits_either_side_of_a_comma_inside_formulas(self):
        cases = [
            (r"\(x = 2,6\)", r"\(x = 2{,}6\)"),
            (r"\[ \frac{2,6+3}{4} = \frac{5,6}{4} = 1,4 \]", r"\[ \frac{2{,}6+3}{4} = \frac{5{,}6}{4} = 1{,}4 \]"),
            (r"$$c = 0,34\;\text{m} \times 500$$", r"$$c = 0{,}34\;\text{m} \times 500$$"),
            (r"\(P(1,5|2)\)", r"\(P(1{,}5|2)\)"),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(self.convert(source), expected)
                self.assertEqual(self.convert(expected), expected, "idempotent")

    def test_what_is_never_touched(self):
        for source in (
            "Die Welle hat λ = 0,34 m.",                       # prose
            "`x = 0,34` und\n```\n\\(y = 1,5\\)\n```",        # code
            r"\(f(x,y) = x + y\)",                            # letters
            r"\(f(1, 2)\)",                                   # a space
            r"\(f(1,2)\)", r"\(P(3,4)\)", r"\(x \in [1,5]\)", r"\(\{1,2\}\)",   # one group of two numbers
            r"\(\{1,2,3\}\)", r"\(1,2,3\)",                   # a list of numbers
            r"\(x_{1,2} = 3\)", r"\(a^{2,5}\)",               # indices
            r"\(\text{0,34 m}\)",                             # text inside a formula
            r"\(0{,}34\)",                                    # already done
            "Preis $1,50 und $2,50",                          # single $ is never read as a formula
            r"\\(1,5\\)",                                     # an escaped opener
            r"\(1,5",                                         # never closed
        ):
            with self.subTest(source=source):
                self.assertEqual(self.convert(source), source)

    def test_the_live_replies(self):
        f7 = formatting.split_reply_trailers(live("reply_f7_decimal_commas"))[0]
        fixed = self.convert(f7)
        self.assertIn(r"c = λ·f = 0{,}34\;\text{m}", fixed)
        self.assertIn(r"ω = 2π·10 ≈ 62{,}8\;\text{rad/s}", fixed)
        self.assertIn("und λ = 0,34 m:", fixed, "prose outside a formula is untouched")
        pd = self.convert(live("reply_pd_photo_answer"))
        self.assertIn(r"x = \frac{13}{5} = 2{,}6", pd)
        self.assertIn(r"\frac{2\cdot 2{,}6-1}{3}", pd)
        self.assertNotIn(", 6", pd.replace(r"2{,}6", ""))

    def test_fuzz_only_commas_between_digits_inside_formulas_change(self):
        rng = random.Random(20261102)
        alphabet = ["1", "2", "0", ",", ", ", "x", "f(", "(", ")", "[", "]", "_{", "^{", "{", "}", "\\(", "\\)",
                    "\\[", "\\]", "$$", "$", "`", "\n", "```\n", "\\text{", "\\{", "\\}", "|", " ", "{,}", "\\\\"]
        for _ in range(3000):
            source = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 40)))
            once = self.convert(source)
            self.assertEqual(self.convert(once), once, repr(source))
            # The only edit is "d,d" -> "d{,}d": undoing it gives the source back.
            self.assertEqual(re.sub(r"(?<=\d)\{,\}(?=\d)", ",", once),
                             re.sub(r"(?<=\d)\{,\}(?=\d)", ",", source), repr(source))
            added = len(once) - len(source)
            self.assertEqual((added % 2, once.count("{,}") - source.count("{,}")), (0, added // 2), repr(source))


class StoredReplyDecimalCommaTests(LiveCase):
    def test_a_german_reply_is_stored_with_braced_decimal_commas(self):
        self.german()
        self.provider.reply = live("reply_pd_photo_answer")
        response = self.send(self.conversation(), message="Löse (x+3)/4 = (2x-1)/3")
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        stored = response.get_json()["reply"]["content"]
        self.assertIn(r"x = \frac{13}{5} = 2{,}6", stored)

    def test_an_english_reply_is_left_alone(self):
        self.provider.reply = r"The point is \(P(1,5)\) and \(x = 1,5\)."
        response = self.send(self.conversation(), message="Where is P?")
        self.assertEqual(response.get_json()["reply"]["content"], r"The point is \(P(1,5)\) and \(x = 1,5\).")


# ------------------------------------------------------------------------- D4
class SubjectAliasTests(LiveCase):
    def test_areas_of_a_subject_are_that_subject(self):
        for raw, expected in (
            ("Algebra", "Mathematics"), ("Geometrie", "Mathematics"), ("Geometry", "Mathematics"),
            ("Analysis", "Mathematics"), ("Stochastik", "Mathematics"), ("Trigonometrie", "Mathematics"),
            ("Arithmetik", "Mathematics"), ("Lineare Algebra", "Mathematics"), ("Statistik", "Mathematics"),
            ("Wahrscheinlichkeitsrechnung", "Mathematics"), ("Bruchrechnung", "Mathematics"),
            ("Mechanik", "Physics"), ("Optik", "Physics"), ("Elektrizitätslehre", "Physics"),
            ("Organische Chemie", "Chemistry"), ("Stöchiometrie", "Chemistry"),
            ("Genetik", "Biology"), ("Ökologie", "Biology"), ("Zellbiologie", "Biology"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(canonical_subject(raw), expected)
        # Ambiguous words count only on their own, never inside a longer name.
        self.assertEqual(canonical_subject("Text analysis"), "Text analysis")
        self.assertEqual(canonical_subject("Gemeinschafts-Kunde"), "Gemeinschafts-Kunde")

    def test_the_live_topic_line_gives_the_conversation_its_subject(self):
        self.provider.reply = live("reply_pd_photo_answer")
        thread = self.conversation()
        response = self.send(thread, message="Löse die Gleichung")
        self.assertEqual(response.status_code, 201)
        with application.app.app_context():
            conversation = db.session.get(application.Conversation, thread)
            self.assertEqual(conversation.subject, "Mathematics")


# ------------------------------------------------------------------------- D6
class RetryAfterTests(LiveCase):
    def limited(self):
        """The rate limiter as production runs it (tests switch it off with TESTING)."""

        csrf = application.app.config.get("WTF_CSRF_ENABLED", True)
        application.app.config.update(TESTING=False, WTF_CSRF_ENABLED=False)
        self.addCleanup(application.app.config.update, TESTING=True, WTF_CSRF_ENABLED=csrf)

    def test_conversation_busy_keeps_its_own_five_seconds(self):
        thread = self.conversation()
        with application.app.app_context():
            conversation = db.session.get(application.Conversation, thread)
            conversation.reply_started_at = application.utcnow()
            db.session.commit()
        self.limited()
        response = self.send(thread, message="Noch da?")
        self.assertEqual((response.status_code, response.get_json()["code"]), (409, "conversation_busy"))
        self.assertTrue(response.headers.get("X-RateLimit-Limit"), "the limiter did run")
        self.assertEqual(response.headers["Retry-After"], "5")

    def test_a_busy_photo_reader_keeps_its_retry_after(self):
        tpm = ("Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_x` service tier `on_demand` on "
               "tokens per minute (TPM): Limit 8000, Used 6801, Requested 3099. Please try again in 14.25s.")
        self.provider.image_failures = [groq_error(429, tpm, retry_after=15)]
        photo = self.attachment()
        self.limited()
        response = self.send(self.conversation(), message="Foto", attachments=[photo])
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Retry-After"], "15")

    def test_a_response_without_its_own_retry_after_gets_none(self):
        self.limited()
        response = self.send(self.conversation(), message="Hallo")
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.headers.get("Retry-After"), "the hourly window is not a retry time")


class ContinueLearningRetryAfterTests(ContinueLearningTestCase):
    def test_a_pending_lesson_keeps_its_own_three_seconds(self):
        conversation_id = self.conversation()
        source_id = self.client.get(f"/api/assistant/conversations/{conversation_id}").get_json()[
            "conversation"]["continue_learning"]["source_message_id"]
        with application.app.app_context():
            db.session.add(application.ConversationLesson(
                user_id=self.user_id(), conversation_id=conversation_id, source_message_id=source_id,
                status="pending", updated_at=application.utcnow()))
            db.session.commit()
        csrf = application.app.config.get("WTF_CSRF_ENABLED", True)
        application.app.config.update(TESTING=False, WTF_CSRF_ENABLED=False)
        try:
            response = self.lesson(conversation_id)
        finally:
            application.app.config.update(TESTING=True, WTF_CSRF_ENABLED=csrf)
        self.assertEqual((response.status_code, response.get_json()["status"]), (202, "pending"))
        self.assertTrue(response.headers.get("X-RateLimit-Limit"), "the limiter did run")
        self.assertEqual(response.headers["Retry-After"], "3")


# ------------------------------------------------------------------------- D7
class PersistedOutputCapTests(LiveCase):
    def test_the_learned_cap_outlives_the_process(self):
        refusal = LIVE["otpm_refusals"][1]
        self.provider.image_failures = [groq_error(429, refusal)]
        with application.app.app_context():
            application.app.config["AI_CHAT_PHOTO_READING_MAX_OUTPUT_TOKENS"] = 3500
        response = self.send(self.conversation(), message="Foto", attachments=[self.attachment()])
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        with application.app.app_context():
            self.assertEqual(ai_service._learned_output_cap(*VISION), 950)
            dict.clear(ai_service._LEARNED_OUTPUT_CAPS)       # a new worker: nothing in memory
            self.assertEqual(ai_service._learned_output_cap(*VISION), 950, "read back from the cache directory")
            state = list(Path(application.app.config["AI_CACHE_DIR"]).rglob("*.state"))
            self.assertEqual(len(state), 1)
            self.assertNotIn("org_x", state[0].read_text(encoding="utf-8"))
            ai_service._LEARNED_OUTPUT_CAPS.clear()
            self.assertIsNone(ai_service._learned_output_cap(*VISION))
            self.assertEqual(list(Path(application.app.config["AI_CACHE_DIR"]).rglob("*.state")), [])

    def test_an_expired_cap_is_not_read_back(self):
        with application.app.app_context():
            ai_service._LEARNED_OUTPUT_CAPS[VISION] = (950, time.monotonic() + 0.05)
            dict.clear(ai_service._LEARNED_OUTPUT_CAPS)
            time.sleep(0.1)
            self.assertIsNone(ai_service._learned_output_cap(*VISION))


# ------------------------------------------------------------------------- D8
class SmallPhotoHintTests(unittest.TestCase):
    def issues(self, data):
        return intake.prepare(data, intake.IntakeLimits()).quality.issues

    def test_a_legible_crop_of_one_exercise_is_not_called_small(self):
        self.assertNotIn("low_resolution", self.issues(CROP), "the live crop, 1180x340, large writing")
        page = intake.decode(CASES["math_handwritten"].data, intake.IntakeLimits())[0]
        strip = page.crop((0, 0, page.width, page.height // 6))
        buffer = io.BytesIO()
        strip.save(buffer, "JPEG", quality=85)
        self.assertNotIn("low_resolution", self.issues(buffer.getvalue()))

    def test_small_writing_is_still_called_small(self):
        self.assertIn("low_resolution", self.issues(CASES["low_resolution"].data))
        page = intake.decode(CASES["math_handwritten"].data, intake.IntakeLimits())[0]
        for size in ((500, 707), (620, 146)):
            with self.subTest(size=size):
                small = page.resize(size) if size[1] > 200 else page.crop((0, 0, page.width, page.height // 6)).resize(size)
                buffer = io.BytesIO()
                small.save(buffer, "JPEG", quality=85)
                self.assertIn("low_resolution", self.issues(buffer.getvalue()))


if __name__ == "__main__":
    unittest.main()
