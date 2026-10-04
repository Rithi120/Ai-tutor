"""The tap-a-word scanner: photo -> local OCR -> language -> word -> sentence -> translation.

No language model anywhere on this path, and these tests prove it: the whole flow runs
with the AI gateway patched to fail loudly if anything calls it. The OCR engine is a
fake here (the real one is exercised once, below, when it is installed), and the
translation providers answer through a fake HTTP seam, so every shape - a quota
warning, a timeout, a provider down - is reproduced without the network.
"""

import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_vocabulary_scanner_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.ocr import local as local_ocr  # noqa: E402
from learnova.vocabulary import language, layout, translate  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


# ---- language detection -------------------------------------------------------------------

class LanguageDetectionTests(unittest.TestCase):
    SAMPLES = {
        "fr": "La boulangerie ouvre à sept heures. Il marche à travers le village et achète du pain pour sa mère.",
        "de": "Das Mädchen geht jeden Morgen zur Schule und kauft auf dem Weg ein Brötchen beim Bäcker.",
        "en": "The bakery opens at seven. He walks through the village and buys bread for his mother.",
        "es": "La panadería abre a las siete. Él camina por el pueblo y compra pan para su madre.",
    }

    def test_each_supported_language_is_recognised_from_a_few_sentences(self):
        for code, text in self.SAMPLES.items():
            detection = language.detect_language(text, ("en", "de", "fr", "es"))
            self.assertEqual(detection.language, code, text)
            self.assertGreater(detection.confidence, 0.3, (code, detection))

    def test_it_is_deterministic(self):
        first = language.detect_language(self.SAMPLES["fr"])
        self.assertEqual(first, language.detect_language(self.SAMPLES["fr"]))

    def test_nothing_to_go_on_is_unknown_not_a_guess(self):
        self.assertEqual(language.detect_language("").language, language.UNKNOWN)
        self.assertEqual(language.detect_language("12345 ??? ---").language, language.UNKNOWN)

    def test_a_short_caption_is_a_low_confidence_answer(self):
        detection = language.detect_language("la maison est grande", ("en", "de", "fr", "es"))
        self.assertEqual(detection.language, "fr")
        self.assertLess(detection.confidence, 0.5, "four words are a hint, not a certainty")
        self.assertEqual(language.detect_language("la").language, language.UNKNOWN, "a dead heat is no answer")

    def test_exclusive_letters_help_and_shared_accents_do_not(self):
        self.assertEqual(language.detect_language("Straße Mädchen Übung", ("en", "de", "fr", "es")).language, "de")
        self.assertEqual(language.detect_language("niño mañana", ("en", "de", "fr", "es")).language, "es")

    def test_elided_words_count_in_both_forms(self):
        self.assertIn("c'est", language.tokens("C’est bon"))
        self.assertIn("est", language.tokens("C’est bon"))


# ---- layout: blocks, sentences, the tapped word -------------------------------------------

def line(text, y, x0=0.1, x1=0.9, height=0.03):
    box = (x0, y, x1, y + height)
    return layout.Line(text, box, 0.98, layout.words_from_text(text, box))


class LayoutTests(unittest.TestCase):
    PARAGRAPH = [
        line("La boulangerie ouvre à sept heures. Il marche", 0.10),
        line("à travers le village et achète du pain. Elle est", 0.14),
        line("très gentille! Comment ça va ?", 0.18),
        line("Une légende sous une image.", 0.40),          # a gap: another block
    ]

    def test_adjacent_lines_form_a_block_and_a_gap_starts_another(self):
        blocks = layout.group_blocks(self.PARAGRAPH)
        self.assertEqual([block.lines for block in blocks], [[0, 1, 2], [3]])

    def test_the_sentence_around_a_word_spans_the_line_break(self):
        located = layout.locate(self.PARAGRAPH, 1, 0)   # "à" on line 2
        self.assertIsNotNone(located)
        assert located is not None
        self.assertEqual(located.sentence, "Il marche à travers le village et achète du pain.")
        self.assertEqual(located.clean, "à")

    def test_punctuation_is_stripped_from_the_word_but_kept_in_the_sentence(self):
        located = layout.locate(self.PARAGRAPH, 1, 7)   # "pain."
        assert located is not None
        self.assertEqual((located.word, located.clean), ("pain.", "pain"))
        self.assertTrue(located.sentence.endswith("pain."))

    def test_a_question_mark_with_a_french_space_still_ends_the_sentence(self):
        located = layout.locate(self.PARAGRAPH, 2, 1)   # "gentille!"
        assert located is not None
        self.assertEqual(located.sentence, "Elle est très gentille!")

    def test_a_caption_without_punctuation_returns_the_whole_block(self):
        lines = [line("le village", 0.5)]
        located = layout.locate(lines, 0, 1)
        assert located is not None
        self.assertEqual(located.sentence, "le village")

    def test_hyphenation_at_a_line_break_is_mended(self):
        joined, starts = layout.join_lines(["Il achète du bou-", "langerie du pain."])
        self.assertEqual(joined, "Il achète du boulangerie du pain.")
        self.assertEqual(starts, [0, 16])

    def test_abbreviations_do_not_split_sentences(self):
        spans = layout.sentences("Er kommt z. B. morgen. Dann gehen wir.")
        self.assertEqual([spans[0][0], len(spans)], [0, 2])

    def test_two_columns_read_left_column_first(self):
        lines = [line("right one", 0.10, 0.55, 0.9), line("left one", 0.10, 0.1, 0.45),
                 line("left two", 0.14, 0.1, 0.45), line("right two", 0.14, 0.55, 0.9)]
        blocks = layout.group_blocks(lines)
        self.assertEqual([block.lines for block in blocks], [[1, 2], [0, 3]])

    def test_payload_round_trip_keeps_words_and_boxes(self):
        payload = layout.lines_to_payload(self.PARAGRAPH[:1])
        rebuilt = layout.lines_from_payload(payload)
        self.assertEqual(rebuilt[0].text, self.PARAGRAPH[0].text)
        self.assertEqual(len(rebuilt[0].words), len(self.PARAGRAPH[0].words))
        self.assertEqual(payload[0]["words"][1]["clean"], "boulangerie")

    def test_a_tap_outside_the_page_is_none_not_a_crash(self):
        self.assertIsNone(layout.locate(self.PARAGRAPH, 9, 0))
        self.assertIsNone(layout.locate(self.PARAGRAPH, 0, 99))


# ---- translation providers behind a fake HTTP seam ----------------------------------------

class FakeHttp:
    """Answers in order (a list), or by the text being translated (a dict) - the latter
    because the word and its sentence are fetched on two threads at once."""

    def __init__(self, answers):
        self.answers = list(answers) if not isinstance(answers, dict) else []
        self.by_text = dict(answers) if isinstance(answers, dict) else {}
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.by_text:
            params = kwargs.get("params") or {}
            body = kwargs.get("json_body") or {}
            text = params.get("q") or body.get("q") or (body.get("text") or [""])[0]
            answer = self.by_text[text]
        else:
            answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def mymemory(text, matches=None, status=200, quota=False):
    return 200, json.dumps({"responseData": {"translatedText": text, "match": 0.9},
                            "responseStatus": status, "quotaFinished": quota, "matches": matches or []})


class TranslatorTests(unittest.TestCase):
    def test_mymemory_answers_without_a_key(self):
        http = FakeHttp([mymemory("die Bäckerei")])
        translator = translate.Translator([translate.MyMemoryProvider()], http=http)
        result = translator.translate("la boulangerie", "fr", "de")
        self.assertEqual((result.text, result.provider, result.ok), ("die Bäckerei", "mymemory", True))
        method, url, kwargs = http.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(kwargs["params"]["langpair"], "fr|de")
        self.assertEqual(kwargs["timeout"], translate.DEFAULT_TIMEOUT)

    def test_for_a_single_word_an_exact_memory_match_beats_the_machine_guess(self):
        http = FakeHttp([mymemory("Backerei fragment", matches=[
            {"segment": "la boulangerie", "translation": "die Bäckerei", "quality": "74"},
            {"segment": "la boulangerie du coin", "translation": "die Bäckerei an der Ecke", "quality": "90"}])])
        result = translate.Translator([translate.MyMemoryProvider()], http=http).translate("la boulangerie", "fr", "de")
        self.assertEqual(result.text, "die Bäckerei")

    def test_a_quota_warning_is_a_failure_not_a_translation(self):
        http = FakeHttp([mymemory("MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY")])
        result = translate.Translator([translate.MyMemoryProvider()], http=http).translate("pain", "fr", "de")
        self.assertFalse(result.ok)
        self.assertIn("quota", result.reason)

    def test_the_next_provider_is_tried_when_the_first_fails_or_times_out(self):
        http = FakeHttp([TimeoutError("slow"), mymemory("das Brot")])
        chain = [translate.DeepLProvider("key:fx"), translate.MyMemoryProvider()]
        result = translate.Translator(chain, http=http).translate("le pain", "fr", "de")
        self.assertEqual((result.text, result.provider), ("das Brot", "mymemory"))
        self.assertEqual(http.calls[0][1], "https://api-free.deepl.com/v2/translate")

    def test_deepl_sends_its_key_header_and_regional_english(self):
        http = FakeHttp([(200, json.dumps({"translations": [{"text": "the bakery"}]}))])
        translate.Translator([translate.DeepLProvider("key:fx")], http=http).translate("la boulangerie", "fr", "en")
        _method, _url, kwargs = http.calls[0]
        self.assertEqual(kwargs["headers"]["Authorization"], "DeepL-Auth-Key key:fx")
        self.assertEqual(kwargs["json_body"], {"text": ["la boulangerie"], "source_lang": "FR", "target_lang": "EN-GB"})

    def test_libretranslate_posts_to_its_instance(self):
        http = FakeHttp([(200, json.dumps({"translatedText": "das Brot"}))])
        provider = translate.LibreTranslateProvider("https://translate.example.org/", "secret")
        translate.Translator([provider], http=http).translate("le pain", "fr", "de")
        self.assertEqual(http.calls[0][1], "https://translate.example.org/translate")
        self.assertEqual(http.calls[0][2]["json_body"]["api_key"], "secret")

    def test_every_provider_down_is_a_calm_failure_with_reasons(self):
        http = FakeHttp([ConnectionError("dns"), (503, "down")])
        chain = [translate.DeepLProvider("k"), translate.MyMemoryProvider()]
        result = translate.Translator(chain, http=http).translate("le pain", "fr", "de")
        self.assertFalse(result.ok)
        self.assertIn("ConnectionError", result.reason)
        self.assertIn("503", result.reason)

    def test_an_answer_is_cached_and_a_second_call_costs_nothing(self):
        http = FakeHttp([mymemory("das Brot")])
        translator = translate.Translator([translate.MyMemoryProvider()], http=http)
        translator.translate("le pain", "fr", "de")
        again = translator.translate("Le pain", "fr", "de")
        self.assertTrue(again.cached)
        self.assertEqual(len(http.calls), 1, "case does not make it a new question")

    def test_the_word_and_its_sentence_are_fetched_together(self):
        http = FakeHttp([mymemory("das Brot"), mymemory("Er kauft Brot.")])
        translator = translate.Translator([translate.MyMemoryProvider()], http=http)
        results = translator.translate_many(["le pain", "Il achète du pain."], "fr", "de")
        self.assertEqual({r.text for r in results}, {"das Brot", "Er kauft Brot."})

    def test_a_word_echoed_back_unchanged_is_not_a_translation(self):
        http = FakeHttp([mymemory("pain")])
        result = translate.Translator([translate.MyMemoryProvider()], http=http).translate("pain", "fr", "de")
        self.assertFalse(result.ok)

    def test_providers_come_from_config_and_need_their_credentials(self):
        chain = translate.providers_from_config({"TRANSLATION_PROVIDERS": "deepl,libretranslate,mymemory"})
        self.assertEqual([p.name for p in chain], ["mymemory"], "no key, no DeepL; no URL, no LibreTranslate")
        chain = translate.providers_from_config({"DEEPL_API_KEY": "k", "LIBRETRANSLATE_URL": "https://x", "MYMEMORY_EMAIL": "me@x"})
        self.assertEqual([p.name for p in chain], ["deepl", "libretranslate", "mymemory"])


# ---- the whole flow through the app, with a fake engine -------------------------------------

def fake_result(lines_text, engine="fake"):
    lines = [line(text, 0.1 + 0.04 * index) for index, text in enumerate(lines_text)]
    return local_ocr.LocalOcrResult(lines, 1200, 900, engine, 12)


def photo(label="x"):
    image = Image.new("RGB", (800, 600), "white")
    ImageDraw.Draw(image).text((40, 40), label, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def no_llm(**_kwargs):
    raise AssertionError("the scanner must never call the AI gateway")


class ScannerFlowTests(unittest.TestCase):
    PAGE = ["La boulangerie ouvre à sept heures. Il marche",
            "à travers le village et achète du pain pour sa mère."]

    def setUp(self):
        application.app.config.update(
            TESTING=True, FEATURE_VOCABULARY_TRAINER=True, LOCAL_OCR_ENGINE="auto",
            TRANSLATION_PROVIDERS="mymemory", DEEPL_API_KEY="", LIBRETRANSLATE_URL="")
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
            application.ensure_database()
        application._TRANSLATION_MEMORY = translate.MemoryCache()
        self.client = application.app.test_client()
        self.client.post("/register", data={"username": "vocab", "email": "vocab@example.com",
                                            "password": "correct-horse-battery"})
        with application.app.app_context():
            user = application.db.session.scalar(application.db.select(application.User))
            user.preferred_language = "de"
            application.db.session.commit()
        self.http = FakeHttp([])

    def scan(self, lines=None, **form):
        with patch.object(application, "create_response", no_llm), \
                patch.object(local_ocr, "available", return_value=True), \
                patch.object(local_ocr, "recognize", return_value=fake_result(lines or self.PAGE)):
            return self.client.post("/api/vocabulary/scans", data={
                "file": (io.BytesIO(photo()), "page.png"), **form}, content_type="multipart/form-data")

    def lookup(self, scan_id, line_index, word_index, answers, query=""):
        self.http = FakeHttp(answers)
        with patch.object(application, "create_response", no_llm), \
                patch.object(translate, "_http_requests", self.http):
            return self.client.get(f"/api/vocabulary/scans/{scan_id}/words/{line_index}/{word_index}{query}")

    def test_photo_to_words_to_meaning_to_saved_entry_without_a_language_model(self):
        created = self.scan()
        self.assertEqual(created.status_code, 201, created.data)
        scan = created.get_json()["scan"]
        self.assertEqual(scan["source_language"], "fr", "detected from the page, not asked")
        self.assertEqual(scan["target_language"], "de", "from the profile, not asked")
        self.assertEqual(scan["word_count"], 19)
        self.assertEqual(scan["engine"], "fake")
        self.assertTrue(scan["preview_url"])

        # Tap "boulangerie" (line 0, word 1): word + sentence, two provider calls, together.
        looked = self.lookup(scan["id"], 0, 1, {
            "boulangerie": mymemory("die Bäckerei"),
            "La boulangerie ouvre à sept heures.": mymemory("Die Bäckerei öffnet um sieben Uhr.")})
        self.assertEqual(looked.status_code, 200, looked.data)
        lookup = looked.get_json()["lookup"]
        self.assertEqual(lookup["word"], "boulangerie")
        self.assertEqual(lookup["translation"], "die Bäckerei")
        self.assertEqual(lookup["sentence"], "La boulangerie ouvre à sept heures.")
        self.assertEqual(lookup["sentence_translation"], "Die Bäckerei öffnet um sieben Uhr.")
        self.assertEqual(len(self.http.calls), 2)

        # The same tap again: answered from the scan, no provider call at all.
        again = self.lookup(scan["id"], 0, 1, [])
        self.assertTrue(again.get_json()["cached"])
        self.assertEqual(self.http.calls, [])

        saved = self.client.post(f"/api/vocabulary/scans/{scan['id']}/words/save", json={
            "word": lookup["word"], "translation": lookup["translation"],
            "sentence": lookup["sentence"], "sentence_translation": lookup["sentence_translation"]})
        self.assertEqual(saved.status_code, 201, saved.data)
        body = saved.get_json()
        self.assertIn("FR → DE", body["list_title"], "one running list per language pair")
        listed = self.client.get(f"/api/vocabulary/lists/{body['list_id']}").get_json()["vocabulary_list"]
        entry = listed["entries"][0]
        self.assertEqual((entry["source_term"], entry["target_translation"]), ("boulangerie", "die Bäckerei"))
        self.assertEqual(entry["source_example_sentence"], "La boulangerie ouvre à sept heures.")
        self.assertEqual(entry["entry_kind"], "word")
        # Saving it again does not duplicate it.
        twice = self.client.post(f"/api/vocabulary/scans/{scan['id']}/words/save", json={
            "word": "Boulangerie", "translation": "die Bäckerei"})
        self.assertTrue(twice.get_json()["duplicate"])
        self.assertEqual(len(self.client.get(f"/api/vocabulary/lists/{body['list_id']}").get_json()["vocabulary_list"]["entries"]), 1)

    def test_a_sentence_spanning_two_lines_is_returned_whole(self):
        scan = self.scan().get_json()["scan"]
        looked = self.lookup(scan["id"], 1, 2, {
            "le": mymemory("der"),
            "Il marche à travers le village et achète du pain pour sa mère.": mymemory("Er geht durch das Dorf.")})
        self.assertEqual(looked.get_json()["lookup"]["sentence"],
                         "Il marche à travers le village et achète du pain pour sa mère.")

    def test_the_translation_cache_is_shared_across_students_and_scans(self):
        first = self.scan().get_json()["scan"]
        self.lookup(first["id"], 0, 1, {"boulangerie": mymemory("die Bäckerei"),
                                         "La boulangerie ouvre à sept heures.": mymemory("Satz.")})
        application._TRANSLATION_MEMORY = translate.MemoryCache()   # a fresh process
        second = self.scan().get_json()["scan"]
        looked = self.lookup(second["id"], 0, 1, [])
        self.assertEqual(looked.status_code, 200, looked.data)
        self.assertEqual(looked.get_json()["lookup"]["translation"], "die Bäckerei")
        self.assertEqual(self.http.calls, [], "answered from the database cache")

    def test_a_provider_outage_degrades_the_answer_not_the_scan(self):
        scan = self.scan().get_json()["scan"]
        looked = self.lookup(scan["id"], 0, 1, [ConnectionError("down"), ConnectionError("down")])
        self.assertEqual(looked.status_code, 200)
        lookup = looked.get_json()["lookup"]
        self.assertEqual(lookup["word"], "boulangerie")
        self.assertEqual(lookup["sentence"], "La boulangerie ouvre à sept heures.")
        self.assertFalse(lookup["translation_ok"])
        self.assertEqual(lookup["translation"], "")
        # The student types the meaning and still saves the word.
        saved = self.client.post(f"/api/vocabulary/scans/{scan['id']}/words/save", json={
            "word": "boulangerie", "translation": "Bäckerei", "sentence": lookup["sentence"]})
        self.assertEqual(saved.status_code, 201)
        # And a failed lookup is not remembered: the next tap asks again.
        retry = self.lookup(scan["id"], 0, 1, {"boulangerie": mymemory("die Bäckerei"),
                                                "La boulangerie ouvre à sept heures.": mymemory("Satz.")})
        self.assertTrue(retry.get_json()["lookup"]["translation_ok"])

    def test_the_languages_can_be_overridden_and_the_choice_sticks(self):
        scan = self.scan().get_json()["scan"]
        looked = self.lookup(scan["id"], 0, 1, {
            "boulangerie": mymemory("the bakery"),
            "La boulangerie ouvre à sept heures.": mymemory("The bakery opens at seven.")}, query="?target=en")
        self.assertEqual(looked.get_json()["lookup"]["translation"], "the bakery")
        self.assertEqual(self.http.calls[0][2]["params"]["langpair"], "fr|en")
        with application.app.app_context():
            stored = application.db.session.get(application.VocabularyScan, scan["id"])
            assert stored is not None
            self.assertEqual(stored.target_language, "en")

    def test_unknown_language_is_reported_and_the_student_picks_one(self):
        created = self.scan(["12 34 56", "---"])
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.get_json()["scan"]["source_language"], "")
        looked = self.lookup(created.get_json()["scan"]["id"], 0, 0, [])
        self.assertEqual(looked.status_code, 400)
        self.assertEqual(looked.get_json()["code"], "source_language_missing")
        chosen = self.scan(["12 34 56"], source_language="fr")
        self.assertEqual(chosen.get_json()["scan"]["source_language"], "fr")

    def test_a_photo_with_no_text_is_a_clear_answer(self):
        with patch.object(local_ocr, "available", return_value=True), \
                patch.object(local_ocr, "recognize", return_value=fake_result([])):
            response = self.client.post("/api/vocabulary/scans", data={
                "file": (io.BytesIO(photo()), "blank.png")}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["code"], "no_text_found")

    def test_without_an_engine_the_scanner_says_so(self):
        with patch.object(local_ocr, "available", return_value=False):
            response = self.client.post("/api/vocabulary/scans", data={
                "file": (io.BytesIO(photo()), "page.png")}, content_type="multipart/form-data")
            page = self.client.get("/vocabulary/scan")
        self.assertEqual(response.status_code, 503)
        self.assertIn("nicht verfügbar", page.get_data(as_text=True), "this student reads German")

    def test_another_student_cannot_read_the_scan(self):
        scan = self.scan().get_json()["scan"]
        stranger = application.app.test_client()
        stranger.post("/register", data={"username": "stranger", "email": "stranger@example.com", "password": "correct-horse-battery"})
        self.assertEqual(stranger.get(f"/api/vocabulary/scans/{scan['id']}/words/0/1").status_code, 404)
        self.assertEqual(stranger.get(scan["preview_url"]).status_code, 404)

    def test_the_page_speaks_german_and_links_from_the_library(self):
        with patch.object(local_ocr, "available", return_value=True):
            page = self.client.get("/vocabulary/scan").get_data(as_text=True)
        self.assertIn("Ein Wort scannen", page)
        self.assertIn("Foto aufnehmen", page)
        self.assertIn("/vocabulary/scan", self.client.get("/vocabulary").get_data(as_text=True))

    def test_expired_photos_take_their_scans_with_them(self):
        scan = self.scan().get_json()["scan"]
        with application.app.app_context():
            document = application.db.session.scalar(application.db.select(application.FlashcardImport))
            document.expires_at = application.utcnow()
            application.db.session.commit()
            application.cleanup_expired_flashcard_imports()
            self.assertIsNone(application.db.session.get(application.VocabularyScan, scan["id"]))


# ---- the real engine, when it is installed ---------------------------------------------------

def rendered_page(lines):
    image = Image.new("RGB", (1400, 200 + 90 * len(lines)), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 40)
    except OSError:
        font = ImageFont.load_default()
    for index, text in enumerate(lines):
        draw.text((60, 60 + 90 * index), text, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@unittest.skipUnless(local_ocr.available({"LOCAL_OCR_ENGINE": "auto"}), "rapidocr is not installed")
class RealEngineTests(unittest.TestCase):
    def test_a_printed_page_is_read_with_accents_and_word_boxes_in_well_under_two_seconds(self):
        config = {"LOCAL_OCR_ENGINE": "auto", "LOCAL_OCR_LANGUAGE": "de"}
        local_ocr.warm_up(config)
        started = time.monotonic()
        result = local_ocr.recognize(rendered_page([
            "La boulangerie ouvre à sept heures.", "Das Mädchen geht zur Schule."]), config)
        elapsed = time.monotonic() - started
        self.assertEqual([line.text for line in result.lines],
                         ["La boulangerie ouvre à sept heures.", "Das Mädchen geht zur Schule."])
        self.assertEqual([word.clean for word in result.lines[0].words][:3], ["La", "boulangerie", "ouvre"])
        first = result.lines[0].words[1].box
        self.assertTrue(0 < first[0] < first[2] < 1 and 0 < first[1] < first[3] < 1, first)
        self.assertLess(elapsed, 2.0, f"{elapsed:.2f}s")
        self.assertEqual(result.engine, "rapidocr")


# ---- the gateway no longer waits forever ----------------------------------------------------

class BoundedWaitTests(unittest.TestCase):
    def test_vision_and_text_requests_get_bounded_timeouts_and_no_sdk_retries(self):
        from learnova.ai_services import service
        with application.app.test_request_context():
            application.app.config.update(AI_PROVIDER_TIMEOUT_SECONDS=40.0, AI_VISION_TIMEOUT_SECONDS=75.0)
            self.assertEqual(service._request_timeout({"input": "text"}), 40.0)
            self.assertEqual(service._request_timeout({"input": [{"role": "user", "content": [
                {"type": "input_image", "image_url": "data:..."}]}]}), 75.0)
        source = (ROOT / "learnova/ai_services/service.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("max_retries=0"), 4, "every SDK client is built without retries")


class ScannerPageWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "static/js/vocabulary-scan.js").read_text(encoding="utf-8")
        cls.template = (ROOT / "templates/vocabulary_scan.html").read_text(encoding="utf-8")

    def test_the_camera_opens_first_and_the_gallery_stays_available(self):
        self.assertIn('id="scanCamera" type="file" accept="image/*" capture="environment"', self.template)
        self.assertIn('id="scanGallery" type="file" accept="image/*"', self.template)
        self.assertNotIn('id="scanGallery" type="file" accept="image/*" capture', self.template)

    def test_every_hook_the_script_uses_is_in_the_template(self):
        for hook in ("scanStart", "scanResult", "scanLookup", "scanWords", "scanSource", "scanTarget",
                     "lookupWord", "lookupTranslation", "lookupSentence", "lookupSentenceTranslation",
                     "lookupSave", "lookupSpeak", "scanAgain", "scanPhoto"):
            self.assertIn(f'id="{hook}"', self.template, hook)
            self.assertIn(f"#{hook}", self.js, hook)

    def test_the_meaning_box_is_editable_so_a_failed_lookup_can_still_be_saved(self):
        self.assertIn('<input id="lookupTranslation"', self.template)
        self.assertIn('t("scanTranslationFailed")', self.js)


if __name__ == "__main__":
    unittest.main()
