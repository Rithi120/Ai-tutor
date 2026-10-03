"""Telling a word from a sentence, and practising one without the other.

A scanned vocabulary page is not a list of word pairs. It is word pairs with example
sentences threaded between them:

    la médiathèque              die Mediathek
    Je vais à la médiathèque.   Ich gehe in die Mediathek.

The scanner used to turn both lines into entries, so the student got a flashcard whose
front was a whole sentence, and the word above it had an empty example box. These tests
pin the classifier that fixes that, the folding rule that puts the example where it
belongs, and the practice filter the labels exist to serve.

The corpus below is the measurement, kept as a test. It was built in two rounds: the
first scored 48/48 and proved little, because the rules and the examples were written
together. The second round added lines chosen to break them and found two real faults -
possessives ("my best friend") and time adverbs ("heute Abend essen") were being read as
sentence openings, which destroyed real entries. Both are now excluded, and the pronoun
rule needs four words rather than three so that "il y a", "es gibt viele" and "hay que"
survive.
"""

import os
import tempfile
import unittest
from pathlib import Path


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_vocabulary_kinds_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.vocabulary import service  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# (text, kind). Grouped by what it is, not by language.
CORPUS = [
    # single lexical items, with the decoration a vocabulary list prints around them
    ("environment", "word"), ("Umwelt (f)", "word"), ("die Mediathek", "word"),
    ("der Bahnhof, -höfe", "word"), ("das Haus", "word"), ("une marche", "word"),
    ("la médiathèque", "word"), ("le bâtiment", "word"), ("el medio ambiente", "word"),
    ("la casa", "word"), ("Geschwindigkeitsbegrenzung", "word"), ("schwierig", "word"),
    ("die Hand, -ä-e", "word"), ("groß / größer", "word"), ("der Lehrer", "word"),
    ("l'école", "word"), ("las vacaciones", "word"), ("die Umwelt.", "word"),
    ("etc.", "word"), ("U.S.A.", "word"), ("I", "word"), ("there", "word"),
    # fixed expressions learnt as a unit
    ("to look forward to", "phrase"), ("faire du sport", "phrase"), ("to run", "phrase"),
    ("sich die Zähne putzen", "phrase"), ("es gibt", "phrase"), ("look up (sth)", "phrase"),
    ("to take something into account", "phrase"), ("Guten Tag!", "phrase"),
    ("avoir besoin de", "phrase"), ("tener ganas de", "phrase"),
    ("auf dem Land", "phrase"), ("in the morning", "phrase"),
    ("hacer la compra", "phrase"), ("à côté de", "phrase"),
    ("my best friend", "phrase"), ("mein bester Freund", "phrase"),
    ("mon meilleur ami", "phrase"), ("mi mejor amigo", "phrase"),
    ("heute Abend essen", "phrase"), ("this weekend", "phrase"), ("these days", "phrase"),
    ("il y a", "phrase"), ("hay que", "phrase"), ("es gibt viele", "phrase"),
    ("aus dem Fenster schauen", "phrase"), ("der Mann mit dem Hut", "phrase"),
    ("a long time ago", "phrase"), ("the day after tomorrow", "phrase"),
    ("sich freuen auf", "phrase"), ("to look something up", "phrase"),
    # sentences: examples, not entries
    ("We must protect the environment.", "sentence"),
    ("Je vais à la médiathèque.", "sentence"),
    ("Ich putze mir die Zähne.", "sentence"),
    ("Le bâtiment est très ancien.", "sentence"),
    ("Wie geht es dir?", "sentence"), ("Das Haus ist sehr alt.", "sentence"),
    ("No hay de qué.", "sentence"),
    ("She looks forward to the holidays.", "sentence"),
    ("Hoy hace mucho calor.", "sentence"),
    ("Il y a beaucoup de monde ici.", "sentence"),
    ("Can you tell me the way to the station?", "sentence"),
    ("Mein Bruder wohnt in Berlin.", "sentence"),
    ("Nous allons au cinéma ce soir.", "sentence"),
    ("Where did you put my keys", "sentence"),
    ("Ich gehe heute Abend ins Kino", "sentence"),
    ("It is raining.", "sentence"), ("Was ist das?", "sentence"),
    ("¿Cómo te llamas?", "sentence"), ("Es tut mir leid.", "sentence"),
    ("es gibt viele Möglichkeiten", "sentence"),
    ("Guten Morgen, wie geht es dir?", "sentence"),
    ("Er arbeitet jeden Tag bis acht Uhr abends", "sentence"),
]


class ClassifierTests(unittest.TestCase):
    def test_the_whole_corpus_is_classified_correctly(self):
        wrong = [(text, kind, service.text_kind(text))
                 for text, kind in CORPUS if service.text_kind(text) != kind]
        self.assertEqual(wrong, [], f"{len(wrong)} of {len(CORPUS)} misclassified")

    def test_no_real_entry_is_ever_called_a_sentence(self):
        # The expensive direction: a word called a sentence gets folded into the line
        # above and disappears from the list.
        lost = [text for text, kind in CORPUS
                if kind != "sentence" and service.text_kind(text) == "sentence"]
        self.assertEqual(lost, [])

    def test_the_corpus_covers_all_four_languages_and_all_three_kinds(self):
        kinds = {kind for _, kind in CORPUS}
        self.assertEqual(kinds, set(service.ENTRY_KINDS))
        self.assertGreaterEqual(len(CORPUS), 70)

    def test_decoration_around_a_headword_is_ignored(self):
        for decorated, bare in (("Umwelt (f)", "Umwelt"), ("der Bahnhof, -höfe", "der Bahnhof"),
                                ("groß / größer", "groß"), ("Haus [formal]", "Haus")):
            self.assertEqual(service.headword(decorated), bare, decorated)

    def test_an_empty_side_is_not_a_sentence(self):
        for blank in ("", None, "   "):
            self.assertEqual(service.text_kind(blank), "word")

    def test_possessives_and_time_adverbs_are_not_sentence_openings(self):
        # Round two found these: both cost real entries.
        for opener in ("my", "mein", "mon", "mi", "heute", "hoy", "aujourd'hui", "this"):
            self.assertNotIn(opener, service.SENTENCE_OPENERS, opener)

    def test_a_pronoun_needs_four_words_to_make_a_sentence(self):
        self.assertEqual(service.text_kind("il y a"), "phrase")
        self.assertEqual(service.text_kind("il y a trois"), "sentence")


class SidesTests(unittest.TestCase):
    def test_a_word_opposite_a_sentence_is_a_mismatch(self):
        self.assertTrue(service.sides_disagree({
            "source_term": "die Mediathek",
            "target_translation": "Ich gehe in die Mediathek und lese."}))

    def test_two_words_or_two_sentences_agree(self):
        self.assertFalse(service.sides_disagree({
            "source_term": "die Mediathek", "target_translation": "la médiathèque"}))
        self.assertFalse(service.sides_disagree({
            "source_term": "Das Haus ist alt.", "target_translation": "La maison est vieille."}))

    def test_a_missing_translation_is_not_reported_as_a_mismatch(self):
        # It already has its own status; two complaints about one row is noise.
        self.assertFalse(service.sides_disagree({
            "source_term": "Das Haus ist alt.", "target_translation": ""}))

    def test_validation_flags_a_mismatch_for_the_student(self):
        checked = service.validate_entry(
            {"source_term": "die Mediathek",
             "target_translation": "Ich gehe in die Mediathek und lese dort."}, "de", "fr")
        self.assertEqual(checked["status"], "needs_review")
        self.assertIn("sentence", checked["validation_explanation"])

    def test_validation_labels_every_entry(self):
        checked = service.validate_entry(
            {"source_term": "Das Haus ist sehr alt.",
             "target_translation": "La maison est très vieille."}, "de", "fr")
        self.assertEqual(checked["entry_kind"], "sentence")


class FoldingTests(unittest.TestCase):
    PAGE = (
        "la médiathèque | die Mediathek\n"
        "Je vais à la médiathèque. | Ich gehe in die Mediathek.\n"
        "le bâtiment | das Gebäude\n"
        "Le bâtiment est très ancien. | Das Gebäude ist sehr alt.\n"
        "Comment ça va ? | Wie geht es dir?\n")

    def parsed(self, text=None):
        return service.parse_vocabulary_text(text if text is not None else self.PAGE)["entries"]

    def test_an_example_becomes_the_example_of_the_word_above_it(self):
        entries = self.parsed()
        self.assertEqual([entry["source_term"] for entry in entries],
                         ["la médiathèque", "le bâtiment", "Comment ça va ?"])
        self.assertEqual(entries[0]["source_example_sentence"], "Je vais à la médiathèque.")
        self.assertEqual(entries[0]["target_example_translation"], "Ich gehe in die Mediathek.")

    def test_every_entry_is_labelled(self):
        self.assertEqual([entry["entry_kind"] for entry in self.parsed()],
                         ["word", "word", "sentence"])

    def test_a_sentence_that_illustrates_nothing_is_kept_as_an_entry(self):
        # Dropping a scanned line is worse than keeping a questionable one: the student
        # may be learning whole sentences.
        entries = self.parsed()
        self.assertEqual(entries[2]["source_term"], "Comment ça va ?")

    def test_a_sentence_pair_is_not_folded_into_another_sentence(self):
        page = ("Das Haus ist alt. | La maison est vieille.\n"
                "Das Haus ist sehr alt. | La maison est très vieille.\n")
        self.assertEqual(len(self.parsed(page)), 2)

    def test_an_entry_that_already_has_an_example_keeps_it(self):
        page = ("la médiathèque | die Mediathek | Une médiathèque moderne.\n"
                "Je vais à la médiathèque. | Ich gehe in die Mediathek.\n")
        entries = self.parsed(page)
        self.assertEqual(entries[0]["source_example_sentence"], "Une médiathèque moderne.")
        self.assertEqual(len(entries), 2, "the second line must not be silently dropped")

    def test_a_sentence_only_folds_into_a_word_it_actually_contains(self):
        page = ("la médiathèque | die Mediathek\n"
                "Le chat dort sur le canapé. | Die Katze schläft auf dem Sofa.\n")
        self.assertEqual(len(self.parsed(page)), 2)

    def test_folding_survives_inflection_and_accents(self):
        self.assertTrue(service.illustrates("Ich putze mir die Zähne.", "sich die Zähne putzen"))
        self.assertTrue(service.illustrates("Je vais a la mediatheque.", "la médiathèque"))
        self.assertTrue(service.illustrates("She looks forward to it.", "to look forward to"))

    def test_folding_does_not_match_on_short_function_words(self):
        self.assertFalse(service.illustrates("Es ist kalt heute draussen.", "es gibt"))

    def test_the_first_line_of_a_page_is_never_folded_away(self):
        entries = self.parsed("Je vais à la médiathèque. | Ich gehe in die Mediathek.\n")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["entry_kind"], "sentence")

    def test_typed_rows_are_labelled_too(self):
        parsed = service.parse_manual_entries([
            {"source_term": "la médiathèque", "target_translation": "die Mediathek"},
            {"source_term": "Comment ça va ?", "target_translation": "Wie geht es dir?"},
        ])
        self.assertEqual([entry["entry_kind"] for entry in parsed["entries"]],
                         ["word", "sentence"])


class ScopeTests(unittest.TestCase):
    def test_words_means_words_and_fixed_phrases(self):
        self.assertEqual(service.kinds_in_scope("words"), frozenset({"word", "phrase"}))

    def test_sentences_means_only_sentences(self):
        self.assertEqual(service.kinds_in_scope("sentences"), frozenset({"sentence"}))

    def test_all_covers_every_kind(self):
        self.assertEqual(service.kinds_in_scope("all"), frozenset(service.ENTRY_KINDS))

    def test_an_unknown_scope_falls_back_to_everything(self):
        for junk in ("", None, "WORDS!", "../etc", 7):
            self.assertEqual(service.practice_scope(junk), "all", repr(junk))

    def test_scope_matching_is_case_insensitive_and_tolerates_a_bad_label(self):
        self.assertTrue(service.in_scope("WORD", "Words"))
        self.assertTrue(service.in_scope("nonsense", "words"), "an unlabelled row is a word")
        self.assertFalse(service.in_scope("sentence", "words"))


class PracticeFilterTests(unittest.TestCase):
    PAGE = (
        "la médiathèque | die Mediathek\n"
        "Je vais à la médiathèque. | Ich gehe in die Mediathek.\n"
        "le bâtiment | das Gebäude\n"
        "Comment ça va ? | Wie geht es dir?\n")

    def setUp(self):
        application.app.config.update(
            TESTING=True, FEATURE_VOCABULARY_TRAINER=True, FEATURE_GAMIFICATION=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "vocab", "email": "vocab@example.com",
            "password": "correct-horse-battery"})
        self.list_id = self._build_list()

    def _build_list(self):
        created = self.client.post("/api/vocabulary/imports", data={
            "source_kind": "text", "source_language": "fr", "target_language": "de",
            "text": self.PAGE}, headers={"Idempotency-Key": "kinds-one"})
        import_id = created.get_json()["vocabulary_import"]["id"]
        self.client.post(f"/api/vocabulary/imports/{import_id}/extract")
        checked = self.client.post(
            f"/api/vocabulary/imports/{import_id}/validate",
            json={"ai_validation": False}).get_json()
        entries = checked["vocabulary_import"]["entries"]
        for entry in entries:
            entry["user_confirmed"] = True
        self.client.put(f"/api/vocabulary/imports/{import_id}/entries", json={"entries": entries})
        generated = self.client.post(
            f"/api/vocabulary/imports/{import_id}/generate",
            json={"directions": ["source_to_target"]},
            headers={"Idempotency-Key": "kinds-generate"})
        self.assertEqual(generated.status_code, 200, generated.data)
        return generated.get_json()["list_id"]

    def practice(self, scope):
        return self.client.get(
            f"/api/vocabulary/lists/{self.list_id}/practice?scope={scope}").get_json()

    def test_the_label_survives_into_the_saved_list(self):
        listed = self.client.get(
            f"/api/vocabulary/lists/{self.list_id}").get_json()["vocabulary_list"]
        self.assertEqual([entry["entry_kind"] for entry in listed["entries"]],
                         ["word", "word", "sentence"])

    def test_words_only_leaves_the_sentences_out(self):
        prompts = [item["prompt"] for item in self.practice("words")["items"]]
        self.assertEqual(prompts, ["la médiathèque", "le bâtiment"])

    def test_sentences_only_leaves_the_words_out(self):
        prompts = [item["prompt"] for item in self.practice("sentences")["items"]]
        self.assertEqual(prompts, ["Comment ça va ?"])

    def test_both_covers_everything(self):
        self.assertEqual(len(self.practice("all")["items"]), 3)

    def test_an_unknown_scope_practises_everything_rather_than_failing(self):
        answered = self.practice("'; DROP TABLE vocabulary_entry; --")
        self.assertEqual(answered["scope"], "all")
        self.assertEqual(len(answered["items"]), 3)

    def test_the_counts_the_picker_shows_match_what_it_would_practise(self):
        counts = self.practice("all")["counts"]
        self.assertEqual(counts, {"all": 3, "words": 2, "sentences": 1})
        for scope, total in counts.items():
            self.assertEqual(len(self.practice(scope)["items"]), total, scope)

    def test_switching_scope_starts_a_fresh_session(self):
        # Resuming at position 4 of a two-item list would skip straight to the end.
        words = self.practice("words")["session_id"]
        sentences = self.practice("sentences")["session_id"]
        self.assertNotEqual(words, sentences)
        self.assertEqual(self.practice("words")["session_id"], words, "same scope resumes")

    def test_each_practice_item_says_what_kind_it_is(self):
        kinds = {item["prompt"]: item["entry_kind"] for item in self.practice("all")["items"]}
        self.assertEqual(kinds["Comment ça va ?"], "sentence")
        self.assertEqual(kinds["le bâtiment"], "word")


class ExistingDataTests(unittest.TestCase):
    """Lists that already exist were scanned before any of this, and must still work.

    The schema change runs against a real database in ensure_database; what is pinned
    here is the rule it relabels with and the fact that it relabels at all. Leaving old
    rows on the column default would make "sentences only" come back empty on every
    list a student already has.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "app.py").read_text(encoding="utf-8")

    def test_old_rows_are_relabelled_rather_than_left_on_the_default(self):
        migration = self.source.split("021_add_vocabulary_entry_kind", 1)[1].split(
            "022_add_practice_scope", 1)[0]
        self.assertIn("vocabulary.text_kind(row.source_term)", migration)

    def test_both_schema_changes_are_guarded_by_a_marker(self):
        for version in ("021_add_vocabulary_entry_kind", "022_add_practice_scope"):
            self.assertEqual(
                self.source.count(f'apply_schema_migration("{version}"'), 2,
                f"{version} needs the add-it and already-there branches")

    def test_the_relabelling_rule_is_the_one_the_scanner_uses(self):
        # Same function, so an old list and a new scan agree about what a word is.
        self.assertEqual(service.text_kind("la médiathèque"), "word")
        self.assertEqual(service.text_kind("Je vais à la médiathèque."), "sentence")
        self.assertEqual(service.text_kind("il y a"), "phrase")


class GapFillTests(unittest.TestCase):
    def test_an_entry_with_no_example_is_left_out_of_gap_fill(self):
        # A sentence entry illustrates nothing itself, so it has no example to blank
        # out; the old code would have shown an empty card.
        practice = (ROOT / "app.py").read_text(encoding="utf-8").split(
            'elif direction == "example":', 1)[1].split("result.append", 1)[0]
        self.assertIn("if not entry.source_example_sentence.strip():", practice)
        self.assertIn("continue", practice)


class StudyPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "templates/vocabulary_study.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "static/js/vocabulary.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "static/css/vocabulary.css").read_text(encoding="utf-8")

    def test_the_three_choices_are_offered_in_the_order_they_were_asked_for(self):
        import re
        values = re.findall(r'name="scope" value="(\w+)"', self.template)
        self.assertEqual(values, ["words", "sentences", "all"])
        self.assertIn('value="all" checked', self.template)

    def test_the_choice_reaches_the_server(self):
        self.assertIn("scope=${encodeURIComponent(scope)}", self.js)

    def test_an_option_with_nothing_behind_it_is_disabled_not_hidden(self):
        self.assertIn("radio.disabled = total === 0", self.js)
        self.assertIn(".scope-option.is-empty", self.css)

    def test_the_student_is_never_left_on_an_empty_choice(self):
        self.assertIn("if (radio.checked && total === 0)", self.js)
        self.assertIn("vocabularyNothingInScope", self.js)

    def test_the_counts_come_from_the_list_rather_than_a_second_endpoint(self):
        counter = self.js.split("async function labelScopeOptions", 1)[1].split("\n  }", 1)[0]
        self.assertIn("/api/vocabulary/lists/", counter)
        self.assertIn('entry.entry_kind || "word"', counter)

    def test_a_missing_count_does_not_stop_practice_starting(self):
        self.assertIn("labelScopeOptions().catch(", self.js)

    def test_the_picker_stacks_on_a_phone(self):
        phone = self.css.split("@media (max-width: 700px)", 1)[1].split("}\n}", 1)[0]
        self.assertIn(".scope-picker { grid-template-columns: 1fr; }", phone)


class ReviewPageTests(unittest.TestCase):
    """The classifier is a heuristic, so the student has to be able to overrule it."""

    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "static/js/vocabulary.js").read_text(encoding="utf-8")

    def review_change_handler(self):
        # Not the first "change" listener in the file - that one belongs to the import
        # form's method chooser.
        marker = '#vocabularyReviewRows").addEventListener("change"'
        self.assertIn(marker, self.js)
        return self.js.split(marker, 1)[1].split("});", 1)[0]

    def test_the_kind_can_be_changed_on_the_review_page(self):
        more = self.js.split("function moreHtml(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('data-field="entry_kind"', more)
        self.assertIn("vocabularyKind_${kind}", more)
        self.assertIn('["word", "phrase", "sentence"]', more)

    def test_a_select_is_read_on_change_not_on_input(self):
        # A <select> does not reliably fire "input", and the row's other fields do.
        self.assertIn('if (field === "entry_kind") return;', self.js)
        self.assertIn('event.target.dataset.field !== "entry_kind"', self.review_change_handler())

    def test_changing_the_kind_counts_as_confirming_the_row(self):
        self.assertIn("entry.user_confirmed = true", self.review_change_handler())


class TranslationTests(unittest.TestCase):
    NEW_KEYS = ("vocabularyType", "vocabularyKind_word", "vocabularyKind_phrase",
                "vocabularyKind_sentence", "vocabularyNothingInScope")

    def test_every_language_is_still_enabled(self):
        from learnova.translations import catalog
        self.assertEqual(catalog.SUPPORTED_LANGUAGES, ("en", "de", "fr", "es"))

    def test_the_new_strings_are_translated_everywhere(self):
        """A translation must be *supplied*, which is not the same as being different.

        "Type" is "Type" in French. Asserting that a translation differs from its
        English source would mark every cognate as missing, so this checks that the
        catalogue actually carries an entry for the source string.
        """

        from learnova.translations import catalog
        sources = [catalog.FRONTEND_MESSAGES[key] for key in self.NEW_KEYS]
        sources += ["Words", "Sentences", "Both", "What do you want to learn?"]
        for language in ("de", "fr", "es"):
            for source in sources:
                self.assertIn(source, catalog.CATALOGS[language],
                              f"{source!r} has no {language} translation")

    def test_the_translations_that_should_differ_do(self):
        from learnova.translations import catalog
        self.assertEqual(catalog.translate("Sentences", "de"), "Sätze")
        self.assertEqual(catalog.translate("Words", "es"), "Palabras")
        self.assertEqual(catalog.frontend_catalog("de")["vocabularyKind_sentence"], "Satz")


if __name__ == "__main__":
    unittest.main()
