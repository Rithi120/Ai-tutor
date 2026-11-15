"""A card's Type - Word / Sentence / Other (spec Phase 4, "Vocabulary type selector").

The Type is set with one tap in the editor's row menu and stored as one reserved tag in
Flashcard.tags_json, first, so the 8-tag cap can never drop it (no migration). The API
exposes it as `kind`. These tests pin that it survives create, edit, reload, duplicate,
community publish and save-copy; that a legacy card without a kind still works and is
never rewritten; and that study adapts: the scopes, the multiple-choice distractors and
sentence grading follow the kind.
"""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_flashcard_kind_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.flashcards import modes  # noqa: E402
from learnova.flashcards import service  # noqa: E402
from tests.config_isolation import keep_config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def card(card_id, front, back, tags=(), **extra):
    values = dict(id=card_id, front=front, back=back, tags_json=json.dumps(list(tags)), weakness_score=0,
                  repetition_count=0, origin_key="", reverse_answer="", type="question_answer",
                  options_json="[]")
    values.update(extra)
    return SimpleNamespace(**values)


# ------------------------------------------------------------------ storage (pure)
class NormalizeKindTests(unittest.TestCase):
    def test_the_kind_goes_first_so_the_tag_cap_never_drops_it(self):
        stored = service.normalize_card({"front": "a", "back": "b", "kind": "sentence",
                                         "tags": [f"t{n}" for n in range(8)]})
        self.assertEqual(stored["tags"][0], "sentence")
        self.assertEqual(len(stored["tags"]), 8)

    def test_a_new_kind_replaces_the_old_one(self):
        stored = service.normalize_card({"front": "a", "back": "b", "kind": "other",
                                         "tags": ["vocabulary", "fr", "word"]})
        self.assertEqual(stored["tags"], ["other", "vocabulary", "fr"])
        self.assertEqual(service.card_kind(stored["tags"]), "other")

    def test_phrase_is_kept_when_sent(self):
        stored = service.normalize_card({"front": "a", "back": "b", "kind": "phrase",
                                         "tags": ["vocabulary", "phrase"]})
        self.assertEqual(stored["tags"], ["phrase", "vocabulary"])

    def test_an_empty_kind_clears_it(self):
        stored = service.normalize_card({"front": "a", "back": "b", "kind": "",
                                         "tags": ["cells", "sentence"]})
        self.assertEqual(stored["tags"], ["cells"])

    def test_without_the_key_tags_are_kept_exactly(self):
        # AI generation, project sync, community copies and the vocabulary importer.
        tags = ["vocabulary", "fr", "sentence"]
        self.assertEqual(service.normalize_card({"front": "a", "back": "b", "tags": tags})["tags"], tags)

    def test_an_unknown_kind_is_ignored(self):
        stored = service.normalize_card({"front": "a", "back": "b", "kind": "banana", "tags": ["word", "x"]})
        self.assertEqual(stored["tags"], ["word", "x"])
        self.assertNotIn("kind", stored)   # the tag is the storage

    def test_card_kind(self):
        self.assertEqual(service.card_kind(["vocabulary", "fr", "word"]), "word")
        self.assertEqual(service.card_kind([]), "")
        self.assertEqual(service.card_kind(None), "")
        self.assertEqual(service.CARD_KINDS, ("word", "phrase", "sentence", "other"))


# ------------------------------------------------------------------ study (pure)
class ScopeTests(unittest.TestCase):
    CARDS = [card(1, "w", "w", ["vocabulary", "fr", "word"]), card(2, "s", "s", ["sentence"]),
             card(3, "o", "o", ["other"]), card(4, "u", "u", []), card(5, "p", "p", ["phrase"])]

    def ids(self, objective):
        return [c.id for c in modes.select_cards(self.CARDS, objective, None, 10)]

    def test_words_only_leaves_out_sentences_and_other_cards(self):
        self.assertEqual(self.ids("words"), [1, 4, 5])

    def test_sentences_only_is_only_sentences(self):
        self.assertEqual(self.ids("sentences"), [2])

    def test_all_includes_every_kind(self):
        self.assertEqual(self.ids("all"), [1, 2, 3, 4, 5])

    def test_content_kind(self):
        self.assertEqual([modes.content_kind(c) for c in self.CARDS], ["", "sentence", "other", "", ""])


class DistractorTests(unittest.TestCase):
    def mixed_set(self):
        words = [card(n, f"mot{n}", f"word{n}", ["word"]) for n in range(1, 9)]
        sentences = [card(10 + n, f"Il pleut {n}.", f"Es regnet heute {n}.", ["sentence"]) for n in range(1, 5)]
        others = [card(20 + n, f"Formel {n}", f"Inhalt {n}", ["other"]) for n in range(1, 5)]
        return words, sentences, others

    def test_options_come_from_the_same_kind_in_both_directions(self):
        words, sentences, others = self.mixed_set()
        cards = words + sentences + others
        groups = {"": words, "sentence": sentences, "other": others}
        for direction in ("front_to_back", "back_to_front"):
            for seed in range(5):
                items = modes.build_items(cards, "learn", seed, len(cards), direction=direction)
                for item in items:
                    if item["question_type"] != "multiple_choice":
                        continue
                    owner = next(c for c in cards if c.id == item["card_id"])
                    group = groups[modes.content_kind(owner)]
                    side = (lambda c: c.back) if direction == "front_to_back" else modes.reverse_side
                    allowed = {side(c) for c in group}
                    self.assertTrue(set(item["options"]) <= allowed, (direction, owner.front, item["options"]))

    def test_one_same_kind_alternative_is_enough_for_a_sentence(self):
        words = [card(n, f"mot{n}", f"word{n}") for n in range(1, 9)]
        sentences = [card(11, "Il pleut.", "Es regnet.", ["sentence"]), card(12, "Il neige.", "Es schneit.", ["sentence"])]
        items = modes.build_items(words + sentences, "test", 3, 10, direction="front_to_back",
                                  question_types=["multiple_choice"])
        sentence_items = [item for item in items if item["card_id"] in (11, 12)]
        self.assertEqual(len(sentence_items), 2)
        for item in sentence_items:
            self.assertEqual(set(item["options"]), {"Es regnet.", "Es schneit."})

    def test_with_no_same_kind_alternative_the_whole_set_is_used(self):
        words = [card(n, f"mot{n}", f"word{n}") for n in range(1, 6)]
        lone = card(11, "Il pleut.", "Es regnet.", ["sentence"])
        items = modes.build_items(words + [lone], "test", 1, 6, direction="front_to_back",
                                  question_types=["multiple_choice"])
        item = next(item for item in items if item["card_id"] == 11)
        self.assertEqual(len(item["options"]), 4)

    def test_a_students_own_cards_in_a_project_set_keep_the_old_pool(self):
        # No card has a Sentence or Other type: two student-made cards among project cards
        # still get four options (the whole set), exactly as before the Type existed.
        project = [card(n, f"Was ist Begriff {n}?", f"Erklärung {n}", origin_key=f"topic:term:k{n}")
                   for n in range(1, 7)]
        own = [card(11, "Zelle", "Baustein des Lebens"), card(12, "Gewebe", "Verband gleicher Zellen")]
        items = modes.build_items(project + own, "test", 2, 8, direction="front_to_back",
                                  question_types=["multiple_choice"])
        own_items = [item for item in items if item["card_id"] in (11, 12)]
        self.assertEqual(len(own_items), 2)
        for item in own_items:
            self.assertEqual(len(item["options"]), 4, item)

    def test_untagged_sets_are_unchanged(self):
        plain = [card(n, f"q{n}", f"a{n}") for n in range(1, 7)]
        items = modes.build_items(plain, "test", 7, 6, direction="front_to_back", question_types=["multiple_choice"])
        self.assertTrue(all(len(item["options"]) == 4 for item in items))


class GradingTests(unittest.TestCase):
    EXPECTED = "Ich gehe, wenn es regnet, nach Hause."

    def test_a_sentence_is_graded_on_its_words(self):
        self.assertTrue(modes.answer_is_correct("Ich gehe wenn es regnet nach Hause", self.EXPECTED, "written",
                                                kind="sentence"))
        self.assertTrue(modes.answer_is_correct("¿Dónde está la estación", "¿Dónde está la estación?", "written",
                                                kind="sentence"))
        self.assertTrue(modes.answer_is_correct("I don’t know", "I don't know.", "written", kind="sentence"))

    def test_spelling_and_accents_still_count_in_a_sentence(self):
        self.assertFalse(modes.answer_is_correct("Ich geh wenn es regnet nach Hause", self.EXPECTED, "written",
                                                 kind="sentence"))
        self.assertFalse(modes.answer_is_correct("Donde esta la estacion", "¿Dónde está la estación?", "written",
                                                 kind="sentence"))
        self.assertFalse(modes.answer_is_correct("", self.EXPECTED, "written", kind="sentence"))

    def test_words_other_and_legacy_cards_keep_the_strict_rule(self):
        for kind in ("", "other"):
            self.assertFalse(modes.answer_is_correct("Ich gehe wenn es regnet nach Hause", self.EXPECTED, "written",
                                                     kind=kind))
        self.assertTrue(modes.answer_is_correct("la maison.", "la maison", "written"))
        self.assertFalse(modes.answer_is_correct("la maisson", "la maison", "written", kind=""))

    def test_strict_question_types_ignore_the_kind(self):
        self.assertFalse(modes.answer_is_correct("a b", "a, b", "formula", kind="sentence"))


# ------------------------------------------------------------------ API round trips
class KindApiTests(unittest.TestCase):
    CARDS = [
        {"front": "la maison", "back": "das Haus", "kind": "word"},
        {"front": "Il pleut, je reste.", "back": "Es regnet, ich bleibe.", "kind": "sentence"},
        {"front": "E = mc²", "back": "Energie und Masse", "kind": "other"},
        {"front": "le chat", "back": "die Katze"},   # a card made before the Type existed
    ]

    def setUp(self):
        keep_config(self, application.app.config, "FEATURE_PRIVATE_FLASHCARDS", "FEATURE_FLASHCARD_LEARN_MODE",
                    "FEATURE_FLASHCARD_TEST_MODE", "FEATURE_COMMUNITY_PUBLISHING", "FEATURE_COMMUNITY_LIBRARY")
        application.app.config.update(TESTING=True, FEATURE_PRIVATE_FLASHCARDS=True, FEATURE_FLASHCARD_LEARN_MODE=True,
                                      FEATURE_FLASHCARD_TEST_MODE=True, FEATURE_COMMUNITY_PUBLISHING=True,
                                      FEATURE_COMMUNITY_LIBRARY=True)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = self.register("alice")
        response = self.client.post("/api/flashcards/sets", json={
            "title": "Französisch", "subject": "French", "cards": self.CARDS})
        self.assertEqual(response.status_code, 201, response.data)
        self.set_id = response.get_json()["id"]

    @staticmethod
    def register(username):
        client = application.app.test_client()
        client.post("/register", data={"username": username, "email": f"{username}@example.com",
                                       "password": "correct-horse-battery"})
        return client

    def cards(self, set_id=None, client=None):
        response = (client or self.client).get(f"/api/flashcards/sets/{set_id or self.set_id}")
        self.assertEqual(response.status_code, 200)
        return response.get_json()["set"]["cards"]

    def kinds(self, set_id=None, client=None):
        return {c["front"]: c["kind"] for c in self.cards(set_id, client)}

    def test_create_and_reload_keep_each_kind(self):
        self.assertEqual(self.kinds(), {"la maison": "word", "Il pleut, je reste.": "sentence",
                                        "E = mc²": "other", "le chat": ""})
        for c in self.cards():
            kind_tags = [tag for tag in c["tags"] if tag in service.KIND_TAGS]
            self.assertEqual(kind_tags, [c["kind"]] if c["kind"] else [])

    def test_editing_changes_and_keeps_kinds_and_card_ids(self):
        before = {c["front"]: c for c in self.cards()}
        edited = []
        for c in before.values():
            body = {key: c[key] for key in ("id", "type", "front", "back", "explanation", "hint", "tags",
                                            "options", "difficulty", "kind")}
            if c["front"] == "E = mc²":
                body["kind"] = "word"
            if c["front"] == "la maison":
                body["back"] = "das Haus (n.)"
            edited.append(body)
        response = self.client.put(f"/api/flashcards/sets/{self.set_id}", json={"cards": edited})
        self.assertEqual(response.status_code, 200, response.data)
        after = {c["front"]: c for c in self.cards()}
        self.assertEqual({front: c["kind"] for front, c in after.items()},
                         {"la maison": "word", "Il pleut, je reste.": "sentence", "E = mc²": "word", "le chat": ""})
        self.assertEqual({front: c["id"] for front, c in after.items()},
                         {front: c["id"] for front, c in before.items()})
        # The legacy card was sent back with kind "" and was not given a tag.
        self.assertEqual(after["le chat"]["tags"], [])

    def test_duplicate_keeps_kinds(self):
        response = self.client.post(f"/api/flashcards/sets/{self.set_id}/duplicate")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.kinds(response.get_json()["id"]), self.kinds())

    def test_publish_and_save_copy_keep_kinds(self):
        from tests.test_flashcards_publish import REVIEW_HIGH, ai_responder
        with ai_responder(REVIEW_HIGH):
            published = self.client.post("/api/community/publish", json={
                "source_set_id": self.set_id, "title": "Französisch", "subject": "French", "topic": "Wetter",
                "grade": "8", "difficulty": "medium", "language": "en", "tags": ["vokabeln"],
                "author_display": "username", "confirm": True})
        self.assertEqual(published.status_code, 201, published.data)
        public_id = published.get_json()["id"]
        bob = self.register("bob")
        saved = bob.post(f"/api/community/sets/{public_id}/save")
        self.assertLess(saved.status_code, 300, saved.data)
        copy_id = saved.get_json().get("set_id") or saved.get_json().get("id")
        self.assertEqual(self.kinds(copy_id, bob), self.kinds())

    def start(self, mode, **extra):
        response = self.client.post(f"/api/flashcards/sets/{self.set_id}/sessions", json={
            "mode": mode, "resume": False, **extra})
        return response

    def test_scopes_follow_the_kind(self):
        words = self.start("learn", objective="words")
        self.assertEqual(words.status_code, 201, words.data)
        fronts = {c["id"]: c["front"] for c in self.cards()}
        studied = {fronts[item["card_id"]] for item in words.get_json()["session"]["items"]}
        self.assertEqual(studied, {"la maison", "le chat"})
        sentences = self.start("test", objective="sentences")
        studied = {fronts[item["card_id"]] for item in sentences.get_json()["session"]["items"]}
        self.assertEqual(studied, {"Il pleut, je reste."})

    def test_session_items_carry_the_kind(self):
        session = self.start("learn").get_json()["session"]
        fronts = {c["id"]: c["front"] for c in self.cards()}
        kinds = {fronts[item["card_id"]]: item["kind"] for item in session["items"]}
        self.assertEqual(kinds["Il pleut, je reste."], "sentence")
        self.assertEqual(kinds["E = mc²"], "other")
        self.assertEqual(kinds["le chat"], "")

    def test_a_sentence_answer_without_its_commas_is_correct_in_learn_and_test(self):
        sentence_id = next(c["id"] for c in self.cards() if c["kind"] == "sentence")
        word_id = next(c["id"] for c in self.cards() if c["front"] == "la maison")
        # Learn: one written item per card, asked front to back.
        session = self.start("learn", direction="front_to_back").get_json()["session"]
        with application.app.app_context():
            for row in application.db.session.scalars(application.db.select(application.FlashcardSessionItem).where(
                    application.FlashcardSessionItem.session_id == session["id"])):
                row.question_type, row.options_json = "written", "[]"
            application.db.session.commit()
        items = {item["card_id"]: item for item in session["items"]}
        answer = self.client.post(f"/api/flashcards/sessions/{session['id']}/items/{items[sentence_id]['id']}/answer",
                                  json={"answer": "es regnet ich bleibe", "response_ms": 3000, "request_id": "s1"})
        self.assertTrue(answer.get_json()["correct"], answer.data)
        answer = self.client.post(f"/api/flashcards/sessions/{session['id']}/items/{items[word_id]['id']}/answer",
                                  json={"answer": "das haus.", "response_ms": 3000, "request_id": "w1"})
        self.assertTrue(answer.get_json()["correct"])
        # Test: graded at the end, the same way.
        test = self.start("test", objective="sentences", direction="front_to_back",
                          question_types=["written"]).get_json()["session"]
        item = test["items"][0]
        self.client.post(f"/api/flashcards/sessions/{test['id']}/items/{item['id']}/answer",
                         json={"answer": "Es regnet ich bleibe", "response_ms": 3000, "request_id": "t1"})
        done = self.client.post(f"/api/flashcards/sessions/{test['id']}/complete", json={})
        self.assertEqual(done.status_code, 200, done.data)
        self.assertTrue(done.get_json()["session"]["items"][0]["correct"])


# ------------------------------------------------------------------ editor and study UI (source)
class EditorSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.create_js = (ROOT / "static/js/flashcards/create.js").read_text(encoding="utf-8")
        cls.rules_js = (ROOT / "static/js/flashcards/editor-rules.js").read_text(encoding="utf-8")
        cls.pages_css = (ROOT / "static/css/learnova-pages.css").read_text(encoding="utf-8")

    def test_the_type_group_is_inside_the_row_menu(self):
        menu = self.create_js.split("card-menu-body", 1)[1].split("</details>", 1)[0]
        self.assertIn('role="group" aria-label="${label("vocabularyType")}"', menu)
        self.assertIn("KIND_CHOICES.map(choice =>", menu)
        self.assertIn('data-kind-choice="${choice}" aria-pressed="${choice === kind}"', menu)
        self.assertIn('export const KIND_CHOICES = ["word", "sentence", "other"];', self.rules_js)

    def test_the_editor_keeps_the_kind_apart_from_the_tags_and_sends_it(self):
        normalize = self.create_js.split("function normalize(card)", 1)[1].split("\n}", 1)[0]
        self.assertIn("const { kind, tags } = splitKind(card);", normalize)
        self.assertIn("kind: c.kind || \"\"", self.create_js.split("function collectBody()", 1)[1].split("\n}", 1)[0])
        self.assertIn('field === "tags" ? typedTags(event.target.value)', self.create_js)
        self.assertIn("tapKind(cards[index].kind, kindChoice.dataset.kindChoice)", self.create_js)

    def test_each_type_button_is_one_tap_target_and_uses_tokens(self):
        rule = self.pages_css.split(".card-kind-option {", 1)[1].split("}", 1)[0]
        self.assertIn("min-block-size: var(--ln-tap)", rule)
        self.assertNotRegex(rule, r"#[0-9a-fA-F]{3,6}\b")
        # test_creator_ux.test_row_icon_rules_do_not_reach_the_menu_items guards the rest.

    def test_the_study_screen_marks_a_sentence(self):
        template = (ROOT / "templates/flashcards_mode.html").read_text(encoding="utf-8")
        self.assertIn('<span id="modeKind" class="mode-kind hidden"></span>', template)
        engine = (ROOT / "static/js/flashcards/mode-engine.js").read_text(encoding="utf-8")
        self.assertIn('item.kind === "sentence" ? t("vocabularyKind_sentence")', engine)

    def test_type_labels_are_translated(self):
        from learnova.translations import catalog
        expected = {"de": ("Wort", "Satz", "Anderes", "Art", "Karte duplizieren"),
                    "fr": ("Mot", "Phrase", "Autre", "Type", "Dupliquer la carte"),
                    "es": ("Palabra", "Frase", "Otro", "Tipo", "Duplicar tarjeta")}
        for language, labels in expected.items():
            messages = catalog.frontend_catalog(language)
            self.assertEqual(tuple(messages[key] for key in ("vocabularyKind_word", "vocabularyKind_sentence",
                                                             "vocabularyKind_other", "vocabularyType",
                                                             "fcDuplicateCard")), labels, language)

    def test_ai_generated_cards_get_no_type(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        generate = source.split('@app.post("/api/flashcards/generate")', 1)[1].split("\n@app.", 1)[0]
        self.assertIn('card["tags"] = flashcards.without_kind(card["tags"])', generate)
        self.assertEqual(len(re.findall(r'card\["tags"\] = flashcards\.without_kind\(card\["tags"\]\)', source)), 2)


if __name__ == "__main__":
    unittest.main()
