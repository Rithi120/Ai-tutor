"""Conversation titles, history date groups, search snippets and list cursors (pure).

learnova/assistant/titles.py and history.py are Flask-free, so every rule is asserted
here without a database, a request or a provider.
"""

import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from learnova import assistant
from learnova.assistant import history, titles

BERLIN = ZoneInfo("Europe/Berlin")
NEW_YORK = ZoneInfo("America/New_York")
PLACEHOLDERS = ("Please look at my photo.", "Please look at my photos.",
                "Bitte schau dir mein Foto an.", "Bitte schau dir meine Fotos an.")


def utc(*parts):
    return datetime(*parts, tzinfo=timezone.utc)


class TitleQualityTests(unittest.TestCase):
    def test_greetings_and_fillers_in_every_language_name_nothing(self):
        for text in ("hi", "Hi!", "hello", "Hey :)", "ok", "Okay.", "thanks", "Thank you!", "test", "?", "???",
                     "Hallo", "hallo!!", "Moin", "Servus", "Huhu", "Danke", "Guten Morgen", "ok danke",
                     "merci", "Bonjour !", "Salut", "hola", "¡Hola!", "Gracias", "Buenos días", "  ", ""):
            with self.subTest(text=text):
                self.assertEqual(titles.title_quality(text), "none")

    def test_the_photo_placeholder_names_nothing_in_any_language(self):
        for text in PLACEHOLDERS:
            with self.subTest(text=text):
                self.assertEqual(titles.title_quality(text, placeholders=PLACEHOLDERS), "none")
        self.assertEqual(titles.title_quality("Please look at my photo."), "good",
                         "only the placeholders the caller names are placeholders")

    def test_generic_requests_are_weak(self):
        for text in ("help", "Help me please", "Can you help me?", "could you help", "Kannst du mir helfen?",
                     "Hilfe", "Hilfe bitte!", "Ich verstehe das nicht", "I don't understand", "Explain this",
                     "Erklär mir das", "Erkläre das bitte", "Look at this", "Schau dir das an", "What is this?",
                     "Was ist das?", "Hi, can you help me?", "Hallo, kannst du mir helfen?", "Aide-moi",
                     "Peux-tu m'aider ?", "¿Me puedes ayudar?", "Ayúdame por favor", "Explica esto"):
            with self.subTest(text=text):
                self.assertEqual(titles.title_quality(text), "weak")

    def test_real_questions_are_good_even_when_short(self):
        for text in ("Explain photosynthesis. I keep forgetting it.", "First question", "2+2?",
                     "What is photosynthesis?", "Hilfe bei Bruchrechnung", "Test my knowledge on: cells",
                     "Erklär mir die Photosynthese", "Hi, what is a prime number?", "Qu'est-ce que la gravité ?"):
            with self.subTest(text=text):
                self.assertEqual(titles.title_quality(text), "good")


class NextTitleTests(unittest.TestCase):
    def call(self, source, message="", summary="", topic="", current="New conversation"):
        return titles.next_title(current=current, source=source, message=message, reading_summary=summary,
                                 topic=topic, placeholders=PLACEHOLDERS)

    def test_a_good_first_message_names_the_thread_with_the_pinned_wording(self):
        self.assertEqual(self.call("default", "Explain photosynthesis. I keep forgetting it."),
                         ("Explain photosynthesis.", "auto"))
        self.assertEqual(self.call("default", "First question"), ("First question", "auto"))

    def test_a_greeting_leaves_the_placeholder_and_the_next_good_message_names_it(self):
        self.assertIsNone(self.call("default", "hi"))
        self.assertEqual(self.call("default", "Explain photosynthesis"), ("Explain photosynthesis", "auto"))

    def test_a_generic_request_is_a_weak_title_until_a_better_message(self):
        self.assertEqual(self.call("default", "Can you help me?"), ("Can you help me?", "weak"))
        self.assertEqual(self.call("weak", "Quadratic equations with fractions", current="Can you help me?"),
                         ("Quadratic equations with fractions", "auto"))
        self.assertIsNone(self.call("weak", "Erklär mir das", current="Can you help me?"))
        self.assertIsNone(self.call("weak", "danke", current="Can you help me?"))

    def test_a_photo_only_message_is_named_after_its_reading_summary(self):
        summary = "Ein Arbeitsblatt zu linearen Gleichungen. Mit drei Aufgaben."
        self.assertEqual(self.call("default", "Please look at my photo.", summary),
                         ("Ein Arbeitsblatt zu linearen Gleichungen.", "auto"))
        self.assertEqual(self.call("default", "Bitte schau dir mein Foto an.", summary)[1], "auto")
        self.assertIsNone(self.call("default", "Please look at my photo."), "no summary, nothing to name it after")
        # A generic request with a photo: the worksheet is the better name.
        self.assertEqual(self.call("default", "Erklär mir das", summary)[1], "auto")
        self.assertEqual(self.call("weak", "Please look at my photo.", summary, current="Hilfe")[1], "auto")

    def test_a_topic_upgrades_only_default_and_weak_titles(self):
        self.assertEqual(self.call("default", "hi", topic="Quadratic equations"), ("Quadratic equations", "topic"))
        self.assertEqual(self.call("weak", "help", topic="Quadratische Gleichungen", current="help"),
                         ("Quadratische Gleichungen", "topic"))
        for source in ("auto", "topic", "user"):
            with self.subTest(source=source):
                self.assertIsNone(self.call(source, "hi", topic="Quadratic equations", current="Mine"))

    def test_a_title_the_student_chose_is_never_touched(self):
        for message, summary, topic in (("Explain photosynthesis", "", ""), ("hi", "A worksheet.", ""),
                                        ("Can you help?", "", "Algebra"), ("Please look at my photo.", "x", "y")):
            with self.subTest(message=message):
                self.assertIsNone(self.call("user", message, summary, topic, current="My algebra notes"))

    def test_an_automatic_title_stays_once_set(self):
        self.assertIsNone(self.call("auto", "A completely different second question", current="First question"))
        self.assertIsNone(self.call("auto", "Please look at my photo.", "A worksheet.", current="First question"))

    def test_an_unknown_source_is_treated_as_settled(self):
        self.assertIsNone(self.call("", "Explain photosynthesis"))
        self.assertIsNone(self.call("something", "Explain photosynthesis"))


class DateGroupTests(unittest.TestCase):
    def test_groups_follow_the_local_calendar_around_midnight_in_berlin(self):
        now = utc(2026, 10, 31, 23, 10)          # 00:10 on 1 November in Berlin (CET)
        self.assertEqual(history.date_group(utc(2026, 10, 31, 23, 5), now, BERLIN), "today")       # 00:05 local
        self.assertEqual(history.date_group(utc(2026, 10, 31, 22, 50), now, BERLIN), "yesterday")  # 23:50 local
        self.assertEqual(history.date_group(utc(2026, 10, 31, 22, 50), now, timezone.utc), "today",
                         "the same instants are one UTC day")

    def test_groups_follow_the_local_calendar_around_midnight_in_new_york(self):
        now = utc(2026, 11, 2, 5, 30)            # 00:30 on 2 November in New York (EST)
        self.assertEqual(history.date_group(utc(2026, 11, 2, 5, 1), now, NEW_YORK), "today")        # 00:01 local
        self.assertEqual(history.date_group(utc(2026, 11, 2, 4, 59), now, NEW_YORK), "yesterday")   # 23:59 local
        self.assertEqual(history.date_group(utc(2026, 11, 1, 4, 30), now, NEW_YORK), "yesterday")   # DST day

    def test_the_day_clocks_go_back_is_still_one_day(self):
        # 25 October 2026 has 25 hours in Berlin; 24 h before 23:30 CET is 00:30 CEST the same day.
        now = utc(2026, 10, 25, 22, 30)
        self.assertEqual(history.date_group(now - timedelta(hours=24), now, BERLIN), "today")
        self.assertEqual(history.date_group(now - timedelta(hours=25, minutes=1), now, BERLIN), "yesterday")

    def test_seven_days_is_the_previous_week_and_eight_is_older(self):
        now = utc(2026, 11, 1, 12, 0)
        expected = {0: "today", 1: "yesterday", 2: "previous_7_days", 7: "previous_7_days", 8: "older",
                    40: "older"}
        for days, group in expected.items():
            with self.subTest(days=days):
                self.assertEqual(history.date_group(now - timedelta(days=days), now, BERLIN), group)
        self.assertEqual(tuple(history.GROUPS), ("today", "yesterday", "previous_7_days", "older"))

    def test_a_moment_in_the_future_counts_as_today(self):
        now = utc(2026, 11, 1, 12, 0)
        self.assertEqual(history.date_group(now + timedelta(days=3), now, BERLIN), "today")

    def test_a_naive_value_is_a_utc_instant(self):
        now = utc(2026, 10, 31, 23, 10)
        self.assertEqual(history.date_group(datetime(2026, 10, 31, 22, 50), now, BERLIN), "yesterday")


class ZoneTests(unittest.TestCase):
    def test_a_valid_iana_name_is_used_and_anything_else_falls_back(self):
        self.assertEqual(history.resolve_zone("America/New_York", BERLIN), NEW_YORK)
        self.assertEqual(history.resolve_zone(" Europe/Berlin ", timezone.utc), BERLIN)
        for bad in (None, "", "Mars/Olympus", "../../etc/passwd", "Europe/../Berlin", "UTC; DROP", "A" * 65,
                    "https://example.com/zone", "Europe/Berlin\x00"):
            with self.subTest(bad=bad):
                self.assertIs(history.resolve_zone(bad, BERLIN), BERLIN)


class SnippetTests(unittest.TestCase):
    TEXT = ("Plants take in carbon dioxide and water. Using the energy of light, photosynthesis turns "
            "them into glucose and oxygen, which the plant releases into the air around it.")

    def test_the_match_is_shown_with_its_neighbourhood_and_cut_sides_are_marked(self):
        result = history.snippet(self.TEXT, "photosynthesis", radius=20)
        self.assertIn("photosynthesis", result)
        self.assertTrue(result.startswith("…") and result.endswith("…"))
        self.assertLessEqual(len(result), 2 * 20 + len("photosynthesis") + 2)

    def test_a_short_text_is_not_cut(self):
        self.assertEqual(history.snippet("Brüche kürzen", "kürzen"), "Brüche kürzen")

    def test_matching_ignores_case_including_umlauts(self):
        self.assertIn("ÄRGER", history.snippet("Viel ÄRGER mit den Hausaufgaben", "ärger"))
        self.assertIn("ärger", history.snippet("Viel ärger damit", "ÄRGER"))

    def test_whitespace_is_collapsed(self):
        self.assertEqual(history.snippet("one\n\n  two\tthree", "two"), "one two three")

    def test_without_a_match_the_start_is_shown(self):
        result = history.snippet(self.TEXT, "zebra", radius=10)
        self.assertTrue(result.startswith("Plants"))
        self.assertTrue(result.endswith("…"))
        self.assertEqual(history.snippet("", "x"), "")

    def test_regex_characters_in_the_query_are_literal(self):
        self.assertIn("a.b", history.snippet("xx a.b yy", "a.b"))
        self.assertTrue(history.snippet("50% off (today)", "(today)").endswith("(today)"))

    def test_markdown_and_latex_source_never_show_in_a_snippet(self):
        reply = ("**Gut!** Bei \\(x^2 - 2x - 3 = 0\\) hilft die Lösungsformel:\n\n"
                 "\\[\nx_{1,2} = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}\n\\]")
        result = history.snippet(reply, "Lösungsformel", radius=60)
        self.assertIn("Gut! Bei x² - 2x - 3 = 0 hilft die Lösungsformel", result)
        self.assertIn("x₁,₂ = (-b ± √(b² - 4ac))/2a", result)
        for markup in ("**", "\\(", "\\[", "\\frac", "\\sqrt", "{", "}", "^", "_"):
            self.assertNotIn(markup, result)

    def test_plain_text_keeps_the_words_of_lists_tables_links_and_code(self):
        text = ("## Brüche\n\n- *Zähler* oben\n- __Nenner__ unten\n\n| Bruch | Wert |\n|---|---|\n| $\\frac{1}{2}$ | $|x|$ |\n\n"
                "> Siehe [die Seite](https://example.com) und `snake_case` und ~~alt~~.\n"
                "```python\nprint('hi')\n```\nKosten: $5 und $10, $\\alpha \\le \\beta$, \\(\\text{m}^{3}\\), $\\ce{H2O}$")
        self.assertEqual(history.plain_text(text),
                         "Brüche Zähler oben Nenner unten Bruch · Wert 1/2 · |x| Siehe die Seite und snake_case und "
                         "alt. print('hi') Kosten: $5 und $10, α ≤ β, m³, H2O")
        self.assertEqual(history.plain_text("Die Menge $\\{1, 2\\}$ und 2*3*4"), "Die Menge {1, 2} und 2*3*4")


class CursorTests(unittest.TestCase):
    def test_a_cursor_round_trips(self):
        moment = utc(2026, 11, 1, 9, 30, 15, 123456)
        value = history.encode_cursor(moment, "8b0f-uuid")
        self.assertEqual(history.decode_cursor(value), (moment, "8b0f-uuid"))
        naive = history.encode_cursor(datetime(2026, 11, 1, 9, 30), "x")
        self.assertEqual(history.decode_cursor(naive), (utc(2026, 11, 1, 9, 30), "x"))

    def test_anything_else_is_no_cursor(self):
        for bad in ("", "nonsense", "2026-11-01T09:30:00|", "|abc", "not-a-date|abc", "x" * 200,
                    f"2026-11-01T09:30:00+00:00|{'a' * 37}"):
            with self.subTest(bad=bad):
                self.assertIsNone(history.decode_cursor(bad))


class PackageExportTests(unittest.TestCase):
    def test_the_new_helpers_are_exported(self):
        for name in ("title_quality", "next_title", "GREETINGS", "GENERIC_REQUESTS", "GROUPS", "date_group",
                     "snippet", "resolve_zone", "encode_cursor", "decode_cursor", "history", "titles"):
            self.assertIn(name, assistant.__all__)
            self.assertTrue(hasattr(assistant, name), name)


if __name__ == "__main__":
    unittest.main()
