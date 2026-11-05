"""Subject teaching strategies and lesson planning (learnova/lessons/). Pure: no app import."""

import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from learnova.lessons import lesson_plan
from learnova.lessons.lesson_plan import (
    PARTS_SHAPE, PROMPT_BLOCK_LIMIT, RECAP_PREFIX, REVIEW_OBJECTIVE, ensure_teaching_first, exercise_type,
    explanation_from_parts, knowledge_state, lesson_prompt_block, plan_steps, repair_parts, teaching_order_ok,
)
from learnova.lessons.strategies import (
    REGISTRY, SUBJECT_STRATEGY_KEYS, strategy_for, strategy_key_for, tutor_rules_block,
)
from learnova.lessons.strategies.base import (
    EXPLANATION_KINDS, ITEM_KINDS, KNOWLEDGE_STATES, PART_KINDS, PLAN_MODES, QUESTION_TYPES, RETEACH_ONLY,
    SERVER_PART_KINDS, LessonStep, SubjectStrategy, combine,
)
from learnova.lessons.subjects import SUBJECT_CHOICES, canonical_subject, is_language_subject

ALL_ITEMS = ITEM_KINDS


def kinds(strategy, mode="learn", state="new", items=ALL_ITEMS):
    return [step.kind for step in plan_steps(strategy, mode=mode, state=state, item_kinds=items)]


class SubjectAliasTests(unittest.TestCase):
    def test_german_and_short_spellings(self):
        cases = {
            "Ethik": "Ethics", "ethics": "Ethics", "Werte und Normen": "Ethics",
            "Philosophie": "Philosophy", "NwT": "Science and Technology", "BNT": "Science and Technology",
            "NwT (Naturwissenschaft und Technik)": "Science and Technology",
            "Naturwissenschaft und Technik": "Science and Technology",
            "Gemeinschaftskunde": "Politics", "GK": "Politics", "Sozialkunde": "Politics",
            "WBS": "Economics", "Wirtschaft": "Economics", "Ökonomie": "Economics", "Oekonomie": "Economics",
            "Mathe": "Mathematics", "Mathematik": "Mathematics", "maths": "Mathematics",
            "Erdkunde": "Geography", "Geo": "Geography", "Bildende Kunst": "Art", "BK": "Art",
            "Sport": "Physical Education", "PE": "Physical Education", "P.E.": "Physical Education",
            "Italienisch": "Italian", "Russisch": "Russian", "Türkisch": "Turkish", "Tuerkisch": "Turkish",
            "Griechisch": "Greek", "Chinesisch": "Chinese",
            "Französisch": "French", "Franzoesisch": "French", "Franzosisch": "French", "Français": "French",
            "Spanisch": "Spanish", "Español": "Spanish", "Latein": "Latin", "Deutsch": "German",
            "Englisch": "English", "Physik": "Physics", "Chemie": "Chemistry", "Bio": "Biology",
            "Biologie": "Biology", "Geschichte": "History", "Informatik": "Computer Science", "IT": "Computer Science",
            "Technik": "Technology", "Musik": "Music", "Kunst": "Art", "Reli": "Religion",
            "Evangelische Religion": "Religion", "Katholische Religion": "Religion", "Religionslehre": "Religion",
            "Politics and Society": "Politics", "Anderes": "Other",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(canonical_subject(raw), expected)

    def test_case_punctuation_and_spacing_do_not_matter(self):
        self.assertEqual(canonical_subject("  ETHIK  "), "Ethics")
        self.assertEqual(canonical_subject("evangelische   religion"), "Religion")
        self.assertEqual(canonical_subject("Gemeinschafts-Kunde"), "Gemeinschafts-Kunde")   # unknown stays
        self.assertEqual(canonical_subject("science-and-technology"), "Science and Technology")

    def test_subject_inside_a_longer_phrase(self):
        self.assertEqual(canonical_subject("Ethik Klasse 10"), "Ethics")
        self.assertEqual(canonical_subject("Bio LK"), "Biology")
        # "GK" is also Gemeinschaftskunde, but the longer alias wins.
        self.assertEqual(canonical_subject("Mathe GK"), "Mathematics")
        self.assertEqual(canonical_subject("Geschichte der Philosophie"), "Philosophy")
        self.assertEqual(canonical_subject("Englisch Vokabeln Unit 3"), "English")

    def test_unknown_and_empty(self):
        self.assertEqual(canonical_subject(""), "Other")
        self.assertEqual(canonical_subject(None), "Other")
        self.assertEqual(canonical_subject("   "), "Other")
        self.assertEqual(canonical_subject("???"), "Other")
        self.assertEqual(canonical_subject("  Astro   nomie AG "), "Astro nomie AG")
        # Non-Latin names are kept as typed, not thrown away as "punctuation".
        self.assertEqual(canonical_subject("Математика"), "Математика")
        # At most 80 characters, cut at a word boundary (no "Zauberku" fragment).
        self.assertEqual(canonical_subject("Zauberkunde " * 20), " ".join(["Zauberkunde"] * 6))
        self.assertEqual(len(canonical_subject("Z" * 200)), 80)

    def test_canonical_is_idempotent(self):
        long_unknown = "x" * 77 + " Chronik"       # a cut at 80 would leave "… Ch" = Chemistry
        for raw in (*SUBJECT_CHOICES, "Ethik", "Italienisch", "Astronomie AG", "Mathe GK", "", long_unknown,
                    "Zauberkunde " * 20, "a" * 78 + " it-Kurs"):
            with self.subTest(raw=raw):
                once = canonical_subject(raw)
                self.assertEqual(canonical_subject(once), once)
                self.assertEqual(strategy_key_for(once), strategy_key_for(raw))
        self.assertNotEqual(canonical_subject(canonical_subject(long_unknown)), "Chemistry")

    def test_displayed_labels_map_back_to_their_subject(self):
        # A student who types the label the form shows gets the same subject back.
        german = ("Mathematik", "Deutsch", "Englisch", "Französisch", "Spanisch", "Latein", "Physik", "Chemie",
                  "Biologie", "NwT (Naturwissenschaft und Technik)", "Geschichte", "Geographie",
                  "Gemeinschaftskunde", "Wirtschaft", "Ethik", "Philosophie", "Religion", "Informatik", "Technik",
                  "Musik", "Kunst", "Sport", "Anderes")
        french = ("Mathématiques", "Allemand", "Anglais", "Français", "Espagnol", "Latin", "Physique", "Chimie",
                  "Biologie", "Sciences et technologie", "Histoire", "Géographie", "Éducation civique", "Économie",
                  "Éthique", "Philosophie", "Religion", "Informatique", "Technologie", "Musique", "Arts plastiques",
                  "Éducation physique et sportive", "Autre")
        spanish = ("Matemáticas", "Alemán", "Inglés", "Francés", "Español", "Latín", "Física", "Química",
                   "Biología", "Ciencia y tecnología", "Historia", "Geografía", "Política", "Economía", "Ética",
                   "Filosofía", "Religión", "Informática", "Tecnología", "Música", "Educación artística",
                   "Educación física", "Otro")
        for labels in (german, french, spanish):
            self.assertEqual(len(labels), len(SUBJECT_CHOICES))
            for label, subject in zip(labels, SUBJECT_CHOICES):
                with self.subTest(label=label):
                    self.assertEqual(canonical_subject(label), subject)

    def test_every_choice_resolves_to_itself_and_a_strategy(self):
        self.assertEqual(SUBJECT_CHOICES[0], "Mathematics")
        self.assertEqual(SUBJECT_CHOICES[-1], "Other")
        self.assertEqual(len(SUBJECT_CHOICES), 23)
        for subject in SUBJECT_CHOICES:
            with self.subTest(subject=subject):
                self.assertEqual(canonical_subject(subject), subject)
                self.assertIn(subject, SUBJECT_STRATEGY_KEYS)
                self.assertIn(SUBJECT_STRATEGY_KEYS[subject], REGISTRY)
                self.assertIs(strategy_for(subject), REGISTRY[SUBJECT_STRATEGY_KEYS[subject]])

    def test_strategy_lookup_by_any_spelling(self):
        self.assertEqual(strategy_for("Ethik").key, "ethics")
        self.assertEqual(strategy_for("Philosophie").key, "ethics")
        self.assertEqual(strategy_for("Religion").key, "religion")
        self.assertEqual(strategy_for("Mathe").key, "mathematics")
        self.assertEqual(strategy_for("Physik").key, "physics")
        self.assertEqual(strategy_for("Chemie").key, "sciences")
        self.assertEqual(strategy_for("Bio").key, "sciences")
        self.assertEqual(strategy_for("NwT").key, "nwt")
        self.assertEqual(strategy_for("Latein").key, "latin")
        self.assertEqual(strategy_for("Englisch").key, "languages")
        self.assertEqual(strategy_for("Italienisch").key, "languages")
        self.assertEqual(strategy_for("Deutsch").key, "german")
        self.assertEqual(strategy_for("WBS").key, "politics")
        self.assertEqual(strategy_for("Gemeinschaftskunde").key, "politics")
        self.assertEqual(strategy_for("Astronomie AG").key, "general")
        self.assertEqual(strategy_for(None).key, "general")
        self.assertEqual(strategy_key_for("Sport"), "physical_education")

    def test_language_subjects(self):
        for subject in ("English", "Französisch", "Latein", "Italienisch", "Deutsch", "Spanish"):
            with self.subTest(subject=subject):
                self.assertTrue(is_language_subject(canonical_subject(subject)))
        for subject in ("Mathematics", "Ethik", "History", "Other", "", None, "Astronomie AG"):
            with self.subTest(subject=subject):
                self.assertFalse(is_language_subject(subject))


class StrategyDataTests(unittest.TestCase):
    def test_every_strategy_uses_known_vocabulary(self):
        for key, strategy in REGISTRY.items():
            with self.subTest(strategy=key):
                self.assertEqual(strategy.key, key)
                self.assertTrue(set(strategy.step_kinds()) <= set(PART_KINDS))
                self.assertFalse(set(strategy.step_kinds()) & set(SERVER_PART_KINDS))
                self.assertTrue(set(strategy.extraction_focus) <= set(ITEM_KINDS))
                self.assertTrue(set(strategy.card_kinds) <= set(ITEM_KINDS))
                self.assertEqual(strategy.card_kinds[0], "term")
                self.assertTrue(set(strategy.question_types) <= set(QUESTION_TYPES))
                self.assertEqual(strategy.question_types[0], "multiple_choice")
                self.assertLessEqual(len(strategy.rules), 6)
                self.assertTrue(strategy.priorities and strategy.rules)
                self.assertTrue(strategy.feedback_rule and strategy.exam_style and strategy.name)
                for step in strategy.steps:
                    self.assertTrue(set(step.when_items) <= set(ITEM_KINDS))
                    self.assertTrue(step.modes <= set(PLAN_MODES) and step.states <= set(KNOWLEDGE_STATES))
                    # 1-2 sentences for the model, not an essay.
                    self.assertLessEqual(len(step.instruction), 320, step.instruction)

    def test_invalid_data_fails_at_construction(self):
        with self.assertRaises(ValueError):
            LessonStep("worked_exampel", "typo")
        with self.assertRaises(ValueError):
            LessonStep("example", "x", when_items=("experimnt",))
        with self.assertRaises(ValueError):
            replace(REGISTRY["general"], card_kinds=("rule", "term"))

    def test_registry_covers_the_design_keys(self):
        self.assertEqual(set(REGISTRY), {
            "languages", "latin", "mathematics", "sciences", "physics", "nwt", "ethics", "religion", "german",
            "history", "geography", "politics", "computer_science", "technology", "music", "art",
            "physical_education", "general",
        })

    def test_shared_constants_match_topics_module(self):
        from learnova.lessons.strategies import base
        from learnova.projects import topics
        for name in ("ITEM_KINDS", "PART_KINDS", "SERVER_PART_KINDS", "KNOWLEDGE_STATES"):
            with self.subTest(name=name):
                # Same tuple, same order: both modules show the kinds to the model in order.
                self.assertIsInstance(getattr(topics, name), tuple)
                self.assertEqual(getattr(topics, name), getattr(base, name))
                self.assertEqual(len(set(getattr(base, name))), len(getattr(base, name)))

    def test_flashcard_table_matches_the_strategies(self):
        # Lessons and flashcards must agree on which items a subject teaches; the two
        # tables are separate literals, so drift has to fail here.
        from learnova.projects.terms import CARD_KINDS_BY_STRATEGY
        self.assertEqual(set(CARD_KINDS_BY_STRATEGY), set(REGISTRY))
        for key, strategy in REGISTRY.items():
            with self.subTest(strategy=key):
                self.assertEqual(tuple(strategy.card_kinds), CARD_KINDS_BY_STRATEGY[key])

    def test_extraction_looks_for_what_cards_and_conditional_steps_need(self):
        # A card kind or a step's when_items kind the topic extraction is never asked to
        # look for would almost never appear in the topic map: the card or the step
        # would be dead weight. (Mathematics' theorems, Latin authors, Physics' rules.)
        for key, strategy in REGISTRY.items():
            with self.subTest(strategy=key):
                self.assertTrue(set(strategy.card_kinds) <= set(strategy.extraction_focus),
                                sorted(set(strategy.card_kinds) - set(strategy.extraction_focus)))
                for step in strategy.steps:
                    if step.when_items:
                        self.assertTrue(set(step.when_items) & set(strategy.extraction_focus),
                                        (step.kind, step.when_items))

    def test_terms_come_from_the_notes(self):
        # Fachbegriffe are the ones in the student's material, spelled as there, so the
        # coverage check can find them in the lesson and nothing is invented.
        for key, strategy in REGISTRY.items():
            terms = [step for step in strategy.steps if step.kind == "terms"]
            with self.subTest(strategy=key):
                self.assertTrue(terms)
                for step in terms:
                    self.assertIn("from the notes", step.instruction)
                    self.assertIn("exactly as in the notes", step.instruction)

    def test_tutor_rules_block(self):
        for key, strategy in REGISTRY.items():
            with self.subTest(strategy=key):
                block = tutor_rules_block(strategy)
                self.assertLessEqual(len(block), 900)
                self.assertTrue(block.startswith(f"SUBJECT METHOD ({strategy.name}):"))
                self.assertIn("Wrong answers:", block)
        long = replace(REGISTRY["general"], rules=tuple("A long rule about teaching. " * 8 for _ in range(6)))
        block = tutor_rules_block(long)
        self.assertLessEqual(len(block), 900)
        self.assertIn(long.feedback_rule, block)
        # Long priorities are shortened; the wrong-answer rule still survives whole.
        wordy = replace(REGISTRY["general"], priorities=tuple("a very long priority phrase " * 3 for _ in range(12)))
        block = tutor_rules_block(wordy)
        self.assertLessEqual(len(block), 900)
        self.assertTrue(block.endswith(f" Wrong answers: {wordy.feedback_rule}"))
        # Real strategies keep every rule and at least their first two priorities.
        for key, strategy in REGISTRY.items():
            with self.subTest(strategy=key):
                block = tutor_rules_block(strategy)
                for rule in strategy.rules:
                    self.assertIn(rule, block)
                self.assertIn("priorities: " + "; ".join(strategy.priorities[:2]), block)
                self.assertTrue(block.endswith(f" Wrong answers: {strategy.feedback_rule}"))

    def test_pure_module_does_not_import_app(self):
        code = ("import sys, learnova.lessons.lesson_plan, learnova.lessons.strategies, learnova.lessons.subjects; "
                "print(sorted(m for m in ('app', 'flask', 'sqlalchemy', 'learnova.extensions') if m in sys.modules))")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                                cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "[]")


class TeachingFirstPropertyTests(unittest.TestCase):
    def test_every_strategy_mode_state_and_item_set(self):
        item_sets = (ALL_ITEMS, (), None, ("experiment",), ("thought_experiment", "theory"), ("event", "source"))
        for key, strategy in REGISTRY.items():
            for mode in PLAN_MODES:
                for state in KNOWLEDGE_STATES:
                    for items in item_sets:
                        with self.subTest(strategy=key, mode=mode, state=state, items=items):
                            steps = plan_steps(strategy, mode=mode, state=state, item_kinds=items)
                            got = [step.kind for step in steps]
                            self.assertTrue(got)
                            self.assertEqual(got[0], "objective")
                            self.assertEqual(got.count("objective"), 1)
                            self.assertIn("guided_exercise", got)
                            self.assertEqual(got[-1], "summary")
                            self.assertTrue(set(got) <= set(PART_KINDS))
                            if mode == "review":
                                self.assertEqual(set(got), {"objective", "terms", "guided_exercise", "summary"})
                            else:
                                first_guided = got.index("guided_exercise")
                                self.assertTrue(any(kind in EXPLANATION_KINDS for kind in got[:first_guided]))
                            if mode == "reteach":
                                self.assertIn("mistakes", got)
                                self.assertIn("example", got)

    def test_learn_new_sequences_follow_the_subject_method(self):
        expected = {
            "languages": ["objective", "rule", "structure", "example", "mistakes", "terms", "guided_exercise",
                          "summary"],
            "latin": ["objective", "rule", "structure", "example", "mistakes", "terms", "source_analysis",
                      "guided_exercise", "summary"],
            "mathematics": ["objective", "why", "explanation", "rule", "worked_example", "mistakes",
                            "guided_exercise", "summary"],
            "sciences": ["objective", "terms", "explanation", "experiment", "process", "diagram", "worked_example",
                         "mistakes", "guided_exercise", "summary"],
            "ethics": ["objective", "terms", "thought_experiment", "theory", "argument", "perspectives", "example",
                       "mistakes", "guided_exercise", "summary"],
            "german": ["objective", "explanation", "terms", "structure", "rule", "model_analysis", "mistakes",
                       "guided_exercise", "summary"],
            "history": ["objective", "explanation", "timeline", "terms", "cause_effect", "perspectives",
                        "source_analysis", "guided_exercise", "summary"],
            "geography": ["objective", "explanation", "terms", "process", "diagram", "cause_effect", "example",
                          "guided_exercise", "summary"],
            "politics": ["objective", "terms", "explanation", "perspectives", "argument", "example",
                         "guided_exercise", "summary"],
            "general": ["objective", "explanation", "terms", "example", "mistakes", "guided_exercise", "summary"],
        }
        for key, sequence in expected.items():
            with self.subTest(strategy=key):
                self.assertEqual(kinds(REGISTRY[key]), sequence)
        self.assertEqual(kinds(REGISTRY["physics"]), expected["sciences"])

    def test_ethics_retells_before_it_argues(self):
        got = kinds(strategy_for("Ethik"), items=("thought_experiment", "theory", "term"))
        self.assertLess(got.index("terms"), got.index("thought_experiment"))
        self.assertLess(got.index("thought_experiment"), got.index("guided_exercise"))
        self.assertLess(got.index("theory"), got.index("argument"))
        # Without a thought experiment or text in the notes there is nothing to retell.
        self.assertNotIn("thought_experiment", kinds(strategy_for("Ethik"), items=("term",)))
        retell = next(s for s in strategy_for("Ethik").steps if s.kind == "thought_experiment")
        self.assertIn("additional interpretation", retell.instruction)

    def test_maths_shows_a_worked_example_before_the_student_tries(self):
        got = kinds(strategy_for("Mathe"), items=())
        self.assertLess(got.index("explanation"), got.index("worked_example"))
        self.assertLess(got.index("worked_example"), got.index("guided_exercise"))

    def test_sciences_experiment_only_with_an_experiment_item(self):
        biology = strategy_for("Biologie")
        self.assertIn("experiment", kinds(biology, items=("experiment", "term")))
        self.assertNotIn("experiment", kinds(biology, items=("term", "process")))
        self.assertIn("process", kinds(biology, items=("process",)))
        self.assertNotIn("worked_example", kinds(biology, items=("experiment",)))
        self.assertIn("worked_example", kinds(biology, items=("formula",)))
        experiment = next(s for s in biology.steps if s.kind == "experiment")
        for word in ("hypothesis", "observation", "interpretation"):
            self.assertIn(word, experiment.instruction)

    def test_history_timeline_before_causes(self):
        got = kinds(strategy_for("Geschichte"), items=("event", "date", "source"))
        self.assertLess(got.index("timeline"), got.index("cause_effect"))
        self.assertLess(got.index("cause_effect"), got.index("perspectives"))
        self.assertIn("source_analysis", got)
        self.assertNotIn("timeline", kinds(strategy_for("Geschichte"), items=("person",)))

    def test_geography_process_and_diagram(self):
        got = kinds(strategy_for("Erdkunde"), items=("diagram",))
        self.assertLess(got.index("process"), got.index("diagram"))
        self.assertIn("diagram", kinds(strategy_for("Erdkunde"), items=("place",)))

    def test_german_has_a_model_analysis(self):
        got = kinds(strategy_for("Deutsch"), items=())
        self.assertIn("model_analysis", got)
        self.assertLess(got.index("rule"), got.index("model_analysis"))
        self.assertLess(got.index("model_analysis"), got.index("guided_exercise"))

    def test_languages_rule_structure_example_mistakes(self):
        got = kinds(strategy_for("Englisch"), items=())
        self.assertEqual(got[1:5], ["rule", "structure", "example", "mistakes"])
        self.assertIn("terms", got)
        self.assertIn("source_analysis", kinds(strategy_for("Latein"), items=()))
        self.assertNotIn("source_analysis", got)

    def test_question_types_match_the_design(self):
        self.assertEqual(REGISTRY["languages"].question_types,
                         ("multiple_choice", "dropdown", "text", "checkboxes", "text", "ordering"))
        self.assertEqual(REGISTRY["mathematics"].question_types,
                         ("multiple_choice", "text", "text", "dropdown", "text", "ordering"))
        self.assertEqual(REGISTRY["ethics"].question_types,
                         ("multiple_choice", "text", "checkboxes", "text", "ordering", "text"))
        self.assertEqual(REGISTRY["history"].question_types,
                         ("multiple_choice", "ordering", "text", "checkboxes", "dropdown", "text"))


class CombineTests(unittest.TestCase):
    def test_nwt_is_sciences_plus_technology(self):
        nwt = REGISTRY["nwt"]
        sciences, technology = REGISTRY["sciences"], REGISTRY["technology"]
        self.assertEqual(nwt.name, "Science and Technology")
        got = kinds(nwt)
        self.assertEqual(got, ["objective", "terms", "explanation", "experiment", "process", "diagram",
                               "worked_example", "rule", "example", "mistakes", "guided_exercise", "summary"])
        self.assertEqual(nwt.question_types, sciences.question_types)
        self.assertEqual(nwt.card_kinds[0], "term")
        self.assertTrue(set(sciences.card_kinds) | set(technology.card_kinds) <= set(nwt.card_kinds))
        self.assertTrue(set(sciences.extraction_focus) | set(technology.extraction_focus)
                        <= set(nwt.extraction_focus))
        self.assertLessEqual(len(nwt.rules), 6)
        self.assertTrue(set(nwt.rules) & set(technology.rules))
        # The cap must not cost the rule that keeps experiments to what the notes say.
        self.assertIn("Describe experiments only as the notes give them; mark anything added.", nwt.rules)
        self.assertIn(sciences.feedback_rule, nwt.feedback_rule)
        self.assertIn(technology.feedback_rule, nwt.feedback_rule)
        # The technology application part comes from technology, the science reteach parts stay.
        safety = next(s for s in nwt.steps if s.kind == "rule")
        self.assertIn("safety", safety.instruction)
        self.assertEqual(kinds(nwt, mode="reteach"), kinds(sciences, mode="reteach"))

    def test_steps_are_inserted_before_the_practice_phase(self):
        first = SubjectStrategy(
            key="a", name="A", priorities=("p",), extraction_focus=("term",), question_types=("multiple_choice",),
            card_kinds=("term",), rules=("r1",), feedback_rule="fa", exam_style="ea",
            steps=(LessonStep("objective", "o"), LessonStep("explanation", "e"), LessonStep("guided_exercise", "g"),
                   LessonStep("summary", "s")))
        second = replace(first, key="b", rules=("r2",), feedback_rule="fb", exam_style="eb",
                         steps=(LessonStep("objective", "o2"), LessonStep("timeline", "t"),
                                LessonStep("cause_effect", "c")))
        both = combine("ab", "AB", first, second)
        self.assertEqual(both.step_kinds(), ("objective", "explanation", "timeline", "cause_effect",
                                             "guided_exercise", "summary"))
        self.assertEqual(both.rules, ("r1", "r2"))
        self.assertEqual(both.feedback_rule, "fa fb")


class PlanModeTests(unittest.TestCase):
    def test_review_is_a_short_reminder(self):
        for key in ("ethics", "mathematics", "history"):
            with self.subTest(strategy=key):
                steps = plan_steps(REGISTRY[key], mode="review", state="developing", item_kinds=ALL_ITEMS)
                self.assertEqual([s.kind for s in steps], ["objective", "terms", "guided_exercise", "summary"])
                self.assertEqual(steps[0].instruction, REVIEW_OBJECTIVE)

    def test_reteach_explains_differently(self):
        for key, strategy in REGISTRY.items():
            with self.subTest(strategy=key):
                steps = plan_steps(strategy, mode="reteach", state="developing", item_kinds=ALL_ITEMS)
                self.assertEqual([s.kind for s in steps][2:], ["mistakes", "example", "guided_exercise", "summary"])
                self.assertIn(steps[1].kind, EXPLANATION_KINDS)
                # Every strategy brings its own "explain it another way" and simpler example.
                self.assertEqual(steps[1].modes, RETEACH_ONLY)
                self.assertEqual(steps[3].modes, RETEACH_ONLY)
        history = [s.kind for s in plan_steps(REGISTRY["history"], mode="reteach", state="new", item_kinds=())]
        self.assertIn("mistakes", history)
        self.assertNotIn("mistakes", kinds(REGISTRY["history"]))

    def test_reteach_without_own_steps_reuses_the_learn_steps(self):
        strategy = replace(REGISTRY["general"], steps=tuple(s for s in REGISTRY["general"].steps
                                                             if s.modes != RETEACH_ONLY))
        steps = plan_steps(strategy, mode="reteach", state="new", item_kinds=())
        self.assertTrue(steps[1].instruction.startswith("Explain it again in a different way"))
        self.assertTrue(steps[3].instruction.startswith("Use a simpler example"))

    def test_secure_student_is_not_lectured(self):
        maths_new = kinds(REGISTRY["mathematics"], state="new")
        maths_secure = kinds(REGISTRY["mathematics"], state="secure")
        self.assertIn("why", maths_new)
        self.assertNotIn("why", maths_secure)
        self.assertNotIn("explanation", maths_secure)
        for kind in ("mistakes", "guided_exercise", "summary", "worked_example"):
            self.assertIn(kind, maths_secure)
        ethics = kinds(REGISTRY["ethics"], state="secure")
        self.assertNotIn("thought_experiment", ethics)
        self.assertIn("terms", ethics)
        lecturing = {"why", "explanation", "thought_experiment", "theory"}
        for key, strategy in REGISTRY.items():
            for items in (ALL_ITEMS, ()):
                with self.subTest(strategy=key, items=items):
                    new = plan_steps(strategy, mode="learn", state="new", item_kinds=items)
                    secure = plan_steps(strategy, mode="learn", state="secure", item_kinds=items)
                    self.assertLessEqual(len(secure), len(new))
                    self.assertNotIn("why", [s.kind for s in secure])
                    for kind in ("terms", "mistakes", "guided_exercise", "summary"):
                        if kind in [s.kind for s in new]:
                            self.assertIn(kind, [s.kind for s in secure])
                    # A long lecture part survives only as a short recap that teaching-first
                    # needed because nothing else explains before the exercise.
                    for step in secure:
                        if step.kind in lecturing:
                            self.assertTrue(step.instruction.startswith(RECAP_PREFIX), (key, step.kind))
        # The sciences skip the principle lecture when the experiment, process or
        # calculation parts teach; without them it comes back as a recap.
        sciences = plan_steps(REGISTRY["sciences"], mode="learn", state="secure", item_kinds=ALL_ITEMS)
        self.assertNotIn("explanation", [s.kind for s in sciences])
        recap = plan_steps(REGISTRY["sciences"], mode="learn", state="secure", item_kinds=())
        self.assertEqual([s.kind for s in recap], ["objective", "terms", "explanation", "mistakes", "guided_exercise",
                                                   "summary"])
        self.assertTrue(recap[2].instruction.startswith(RECAP_PREFIX))

    def test_secure_without_any_explaining_part_gets_one_back(self):
        strategy = replace(REGISTRY["general"], steps=(
            LessonStep("objective", "o"), LessonStep("explanation", "e", states=frozenset({"new"})),
            LessonStep("guided_exercise", "g"), LessonStep("summary", "s")))
        self.assertEqual(kinds(strategy, state="secure"), ["objective", "explanation", "guided_exercise", "summary"])

    def test_restored_explanation_keeps_its_place_in_the_sequence(self):
        # General's explanation comes before terms and example; restoring it for a
        # secure student must not put it after the example it explains.
        steps = plan_steps(REGISTRY["general"], mode="learn", state="secure", item_kinds=())
        self.assertEqual([s.kind for s in steps], ["objective", "explanation", "terms", "example", "mistakes",
                                                   "guided_exercise", "summary"])
        self.assertTrue(steps[1].instruction.startswith(RECAP_PREFIX))
        new = plan_steps(REGISTRY["general"], mode="learn", state="new", item_kinds=())
        self.assertFalse(new[1].instruction.startswith(RECAP_PREFIX))

    def test_item_kinds_as_a_string_or_item_dicts(self):
        sciences = REGISTRY["sciences"]
        self.assertIn("experiment", kinds(sciences, items="experiment"))
        self.assertEqual(kinds(sciences, items="experiment"), kinds(sciences, items=("experiment",)))
        items = [{"kind": "Experiment", "name": "Iodprobe"}, {"kind": "term", "name": "Stärke"}, {"name": "x"}]
        self.assertEqual(kinds(sciences, items=items), kinds(sciences, items=("experiment", "term")))

    def test_review_without_a_terms_step_still_recalls_the_terms(self):
        strategy = replace(REGISTRY["general"], steps=tuple(s for s in REGISTRY["general"].steps
                                                             if s.kind != "terms"))
        steps = plan_steps(strategy, mode="review", state="developing", item_kinds=())
        self.assertEqual([s.kind for s in steps], ["objective", "terms", "guided_exercise", "summary"])
        self.assertIn("from the notes", steps[1].instruction)

    def test_objective_is_moved_or_added_first(self):
        strategy = replace(REGISTRY["general"], steps=(
            LessonStep("guided_exercise", "g"), LessonStep("explanation", "e"), LessonStep("summary", "s")))
        self.assertEqual(kinds(strategy), ["objective", "explanation", "guided_exercise", "summary"])

    def test_never_empty(self):
        strategy = replace(REGISTRY["general"], steps=(LessonStep("experiment", "x", when_items=("experiment",)),))
        self.assertEqual(kinds(strategy, items=()), ["objective", "explanation", "guided_exercise", "summary"])

    def test_unknown_item_kinds_keep_conditional_steps_with_a_skip_note(self):
        steps = plan_steps(REGISTRY["ethics"], mode="learn", state="new", item_kinds=None)
        retell = next(s for s in steps if s.kind == "thought_experiment")
        self.assertIn("Leave this part out if the material has nothing for it.", retell.instruction)
        terms = next(s for s in steps if s.kind == "terms")
        self.assertNotIn("Leave this part out", terms.instruction)

    def test_unknown_mode_and_state_fall_back(self):
        self.assertEqual(kinds(REGISTRY["general"], mode="weird", state="??"), kinds(REGISTRY["general"]))

    def test_knowledge_state(self):
        self.assertEqual(knowledge_state(attempts=0, mastery_score=95), "new")
        self.assertEqual(knowledge_state(attempts=0, mastery_score=None), "new")
        self.assertEqual(knowledge_state(attempts=3, mastery_score=80), "secure")
        self.assertEqual(knowledge_state(attempts=3, mastery_score=79.9), "developing")
        self.assertEqual(knowledge_state(attempts=1, mastery_score=None), "developing")


class PromptBlockTests(unittest.TestCase):
    def test_block_fits_and_lists_every_step(self):
        # The longest grade label a caller can pass (it is cut at 80 characters).
        grades = ("", "Klasse 10", "Klasse 10 (Gymnasium, Baden-Württemberg), erhöhtes Anforderungsniveau, G8 " * 2)
        for key, strategy in REGISTRY.items():
            for mode in PLAN_MODES:
                for state in KNOWLEDGE_STATES:
                    for items in (ALL_ITEMS, None, ()):
                        steps = plan_steps(strategy, mode=mode, state=state, item_kinds=items)
                        for grade in grades:
                            with self.subTest(strategy=key, mode=mode, state=state, items=items, grade=grade):
                                block = lesson_prompt_block(strategy, steps, mode=mode, state=state,
                                                            grade_label=grade)
                                self.assertLessEqual(len(block), PROMPT_BLOCK_LIMIT)
                                self.assertTrue(block.startswith(f"TEACHING METHOD — {strategy.name}."))
                                for number, step in enumerate(steps, 1):
                                    # Real plans fit with every instruction in full.
                                    self.assertIn(f"\n{number}. {step.kind} — {step.instruction}\n", block)
                                self.assertTrue(block.endswith(PARTS_SHAPE))
                                self.assertIn("from_notes true only when", block)
                                self.assertIn(f"(state {state})", block)
                                # The exam style is only in this block (priorities and rules
                                # also reach the tutor via tutor_rules_block), so it is
                                # never the one dropped.
                                self.assertIn("Exam style: " + strategy.exam_style, block)

    def test_extras_keep_their_order(self):
        strategy = strategy_for("Mathe")
        block = lesson_prompt_block(strategy, plan_steps(strategy, mode="learn", state="new", item_kinds=()),
                                    mode="learn", state="new")
        self.assertLess(block.index("Priorities:"), block.index("Rules:"))
        self.assertLess(block.index("Rules:"), block.index("Exam style:"))
        self.assertLess(block.index("Exam style:"), block.index("PARTS SHAPE:"))

    def test_block_with_more_steps_than_room_still_ends_with_the_shape(self):
        steps = [LessonStep("explanation", "Explain one more aspect of the topic in detail.")] * 200
        block = lesson_prompt_block(REGISTRY["general"], steps, mode="learn", state="new")
        self.assertLessEqual(len(block), PROMPT_BLOCK_LIMIT)
        self.assertTrue(block.endswith(PARTS_SHAPE))
        self.assertTrue(block.startswith("TEACHING METHOD — General."))
        self.assertIn("\n1. explanation — ", block)

    def test_block_content(self):
        strategy = strategy_for("Ethik")
        steps = plan_steps(strategy, mode="learn", state="new", item_kinds=("thought_experiment", "theory"))
        block = lesson_prompt_block(strategy, steps, mode="learn", state="new", grade_label="Klasse 10")
        self.assertIn("Teach first, then test", block)
        self.assertIn("Klasse 10", block)
        self.assertIn("Priorities:", block)
        self.assertIn("Rules:", block)
        self.assertIn("1. objective — ", block)
        self.assertIn("3. thought_experiment — ", block)
        review = lesson_prompt_block(strategy, plan_steps(strategy, mode="review", state="secure", item_kinds=()),
                                     mode="review", state="secure")
        self.assertIn("REVIEW", review)
        reteach = lesson_prompt_block(strategy, plan_steps(strategy, mode="reteach", state="developing",
                                                           item_kinds=()), mode="reteach", state="developing")
        self.assertIn("RETEACH", reteach)

    def test_overlong_custom_strategy_is_trimmed_but_complete(self):
        wordy = "Explain this part in great detail with many words. " * 6
        strategy = replace(REGISTRY["general"], steps=tuple(
            LessonStep(kind, wordy, modes=frozenset(PLAN_MODES))
            for kind in ("objective", "explanation", "terms", "rule", "structure", "example", "theory",
                         "argument", "perspectives", "mistakes", "guided_exercise", "summary")))
        steps = plan_steps(strategy, mode="learn", state="new", item_kinds=())
        block = lesson_prompt_block(strategy, steps, mode="learn", state="new")
        self.assertLessEqual(len(block), PROMPT_BLOCK_LIMIT)
        for number, kind in enumerate(("objective", "explanation", "terms", "rule", "structure", "example", "theory",
                                       "argument", "perspectives", "mistakes", "guided_exercise", "summary"), 1):
            self.assertIn(f"\n{number}. {kind} — Explain", block)
        self.assertTrue(block.endswith(PARTS_SHAPE))
        # 40 steps of 300 characters: every one still listed, the shape intact.
        many = [LessonStep("example", wordy)] * 40
        block = lesson_prompt_block(strategy, many, mode="learn", state="new")
        self.assertLessEqual(len(block), PROMPT_BLOCK_LIMIT)
        self.assertIn("\n40. example — ", block)
        self.assertTrue(block.endswith(PARTS_SHAPE))


class RepairPartsTests(unittest.TestCase):
    def test_canonical_shape(self):
        parts = repair_parts([{"kind": "terms", "title": "  Key   terms ", "body": "Line one\n\n\n\nLine  two",
                               "items": [{"term": "Pflicht", "definition": "what one must do"}, "Maxime"],
                               "from_notes": True, "source_pages": [3, "4", True, -1, 3.5, 3]}])
        self.assertEqual(parts, [{
            "kind": "terms", "title": "Key terms", "body": "Line one\n\nLine two",
            "items": [{"label": "Pflicht", "text": "what one must do"}, {"label": "", "text": "Maxime"}],
            "answer": "", "from_notes": True, "source_pages": [3, 4],
        }])

    def test_wrappers_and_kind_aliases(self):
        aliases = {
            "Fachbegriffe": "terms", "definitions": "terms", "Glossary": "terms", "vocabulary": "terms",
            "Lernziel": "objective", "learning objective": "objective", "goal": "objective",
            "why it matters": "why", "importance": "why", "Zusammenfassung": "summary", "recap": "summary",
            "conclusion": "summary", "Übung": "guided_exercise", "exercise": "guided_exercise",
            "practice": "guided_exercise", "Typische Fehler": "mistakes", "common-mistakes": "mistakes",
            "pitfalls": "mistakes", "Zeitleiste": "timeline", "chronology": "timeline",
            "Ursachen": "cause_effect", "causes": "cause_effect", "Versuch": "experiment",
            "Gedankenexperiment": "thought_experiment", "Musteranalyse": "model_analysis",
            "example analysis": "model_analysis", "Worked Example": "worked_example", "GUIDED_EXERCISE": "guided_exercise",
            "something new": "explanation", "": "explanation",
        }
        for wrapper in ("parts", "steps", "sections"):
            raw = {wrapper: [{"kind": alias, "body": "text"} for alias in aliases]}
            with self.subTest(wrapper=wrapper):
                self.assertEqual([p["kind"] for p in repair_parts(raw, limit=100)], list(aliases.values()))

    def test_server_kinds_and_empty_parts_are_dropped(self):
        parts = repair_parts([
            {"kind": "flashcards", "items": [{"label": "a", "text": "b"}]},
            {"kind": "explanation", "body": "   ", "items": []},
            {"kind": "summary", "items": [{"label": "", "text": ""}, {}, None, 7]},
            "a bare string becomes an explanation",
            42,
            None,
        ])
        self.assertEqual([p["kind"] for p in parts], ["summary", "explanation"])
        self.assertEqual(parts[0]["items"], [{"label": "", "text": "7"}])
        self.assertEqual(parts[1]["body"], "a bare string becomes an explanation")

    def test_item_variants(self):
        parts = repair_parts([{"kind": "timeline", "items": [
            {"date": "1789", "event": "Revolution"}, {"cause": "A", "effect": "B"}, {"step": 1, "text": "first"},
            {"name": "Kant"}, {"label": ["x"], "text": {"y": 1}}, "- bulleted line",
        ]}, {"kind": "terms", "items": {"Pflicht": "duty", "Neigung": "inclination"}},
            {"kind": "terms", "items": "one\ntwo\n\n"}])
        self.assertEqual(parts[0]["items"], [
            {"label": "1789", "text": "Revolution"}, {"label": "A", "text": "B"}, {"label": "1", "text": "first"},
            {"label": "", "text": "Kant"}, {"label": "", "text": "bulleted line"}])
        self.assertEqual(parts[1]["items"], [{"label": "Pflicht", "text": "duty"},
                                             {"label": "Neigung", "text": "inclination"}])
        self.assertEqual([i["text"] for i in parts[2]["items"]], ["one", "two"])

    def test_from_notes_variants(self):
        values = ((True, True), ("true", True), ("TRUE", True), ("yes", True), (1, True), (False, False),
                  ("false", False), ("no", False), (0, False), (2, False), (None, False), ("1", False),
                  ("notes", False))
        for value, expected in values:
            with self.subTest(value=value):
                self.assertIs(repair_parts([{"kind": "summary", "body": "b", "from_notes": value}])[0]["from_notes"],
                              expected)

    def test_limits(self):
        parts = repair_parts([{"kind": "explanation", "title": "t" * 500, "body": "b" * 9000,
                               "items": [f"item {i}" for i in range(30)], "answer": "a"}
                              for _ in range(20)])
        self.assertEqual(len(parts), 14)
        self.assertEqual(len(parts[0]["title"]), 160)
        self.assertLessEqual(len(parts[0]["body"]), 4000)
        self.assertEqual(len(parts[0]["items"]), 12)
        self.assertEqual(len(repair_parts([{"kind": "summary", "body": "x"}] * 5, limit=2)), 2)
        guided = repair_parts([{"kind": "guided_exercise", "body": "Try it", "answer": "s" * 3000}])[0]
        self.assertEqual(len(guided["answer"]), 2000)

    def test_answer_outside_a_guided_exercise_stays_visible(self):
        worked = repair_parts([{"kind": "worked_example", "body": "Solve 2x = 4.",
                                "items": [{"label": "Divide by 2", "text": "x = 2"}], "answer": "x = 2"}])[0]
        self.assertEqual(worked["answer"], "")
        self.assertEqual(worked["items"][-1], {"label": "", "text": "x = 2"})
        plain = repair_parts([{"kind": "example", "body": "Example.", "answer": "Result."}])[0]
        self.assertEqual(plain["body"], "Example.\n\nResult.")

    def test_unusable_input(self):
        for value in (None, "parts", 3, {}, {"parts": "nope"}, [], {"lesson": []}):
            with self.subTest(value=value):
                self.assertEqual(repair_parts(value), [])
        self.assertEqual(repair_parts([{"kind": "summary", "body": "x"}], limit=0), [])
        self.assertEqual(repair_parts([{"kind": "summary", "body": "x"}], limit=-3), [])

    def test_field_names_in_other_spellings(self):
        parts = repair_parts({"Parts": [
            {"Kind": "Summary", "Title": "Kurz", "Body": "b", "fromNotes": True, "sourcePages": [2]},
            {"Typ": "Fachbegriffe", "Titel": "Begriffe", "Inhalt": "Die wichtigsten Begriffe.",
             "Punkte": [{"Begriff": "Pflicht", "Bedeutung": "was man tun muss"}], "Seiten": "3, 4"},
            {"kind": "guided_exercise", "body": "Erkläre.", "Lösung": "Weil …", "Hinweise": None,
             "hints": ["Denk an Kant."]},
        ]})
        self.assertEqual(parts[0], {"kind": "summary", "title": "Kurz", "body": "b", "items": [], "answer": "",
                                    "from_notes": True, "source_pages": [2]})
        self.assertEqual((parts[1]["kind"], parts[1]["title"], parts[1]["source_pages"]), ("terms", "Begriffe", [3, 4]))
        self.assertEqual(parts[1]["items"], [{"label": "Pflicht", "text": "was man tun muss"}])
        self.assertEqual((parts[2]["answer"], parts[2]["items"]), ("Weil …", [{"label": "", "text": "Denk an Kant."}]))

    def test_term_items_are_never_lost_to_an_unexpected_shape(self):
        # A terms part whose items all came back empty was dropped entirely before:
        # the Fachbegriffe of the lesson silently disappeared.
        parts = repair_parts([{"kind": "terms", "items": [
            {"Pflicht": "what one must do"},                            # one pair: term -> definition
            ["Neigung", "what one wants"],                              # a pair as a list
            {"term": "Maxime", "bedeutung_einfach": "a personal rule"},  # meaning under an unknown field
            {"wrong_sentence": "He go", "corrected_sentence": "He goes"},  # unknown field names
            {"mistake": "I has", "correction": "I have"},
            {"name": "Kant", "role": "philosopher"},
        ]}, {"kind": "terms", "items": {"Autonomie": {"definition": "self-legislation"}, "Würde": "dignity"}}])
        self.assertEqual(parts[0]["items"], [
            {"label": "Pflicht", "text": "what one must do"},
            {"label": "Neigung", "text": "what one wants"},
            {"label": "Maxime", "text": "a personal rule"},
            {"label": "", "text": "wrong sentence: He go; corrected sentence: He goes"},
            {"label": "I has", "text": "I have"},
            {"label": "Kant", "text": "philosopher"},
        ])
        self.assertEqual(parts[1]["items"], [{"label": "Autonomie", "text": "self-legislation"},
                                             {"label": "Würde", "text": "dignity"}])

    def test_unknown_kind_labels_are_matched_by_their_words(self):
        cases = {
            "Guided practice task": "guided_exercise", "Übungsaufgabe": "guided_exercise",
            "Practice exercise": "guided_exercise", "Common misconceptions": "mistakes",
            "Typical mistakes": "mistakes", "Key vocabulary": "terms", "Terms and definitions": "terms",
            "Worked exercise": "worked_example", "Final recap": "summary", "Causes and effects": "cause_effect",
            "Lernziele der Stunde": "objective", "Theoretical background": "theory", "Why this matters": "why",
            "Gedankenexperiment: Der Ring des Gyges": "thought_experiment", "Kurzer Versuch": "experiment",
            "Introduction": "explanation", "Something else": "explanation",
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(repair_parts([{"kind": label, "body": "b"}])[0]["kind"], expected)
        # The point of it: an exercise with an odd label keeps its solution hidden.
        part = repair_parts([{"kind": "Practice task", "body": "Try it.", "answer": "The solution."}])[0]
        self.assertEqual((part["kind"], part["body"], part["answer"]), ("guided_exercise", "Try it.",
                                                                        "The solution."))

    def test_code_keeps_its_indentation(self):
        # Common indentation goes, relative indentation (Python's meaning) stays; tabs count 4.
        code = "    def total(xs):\n        s = 0\n        for x in xs:\n\t\t\ts  +=  x\n        return s\n"
        part = repair_parts([{"kind": "worked_example", "body": code}])[0]
        self.assertEqual(part["body"], "def total(xs):\n    s = 0\n    for x in xs:\n        s += x\n    return s")
        traced = repair_parts([{"kind": "worked_example", "body": "Trace it:\n\n" + code}])[0]
        self.assertEqual(traced["body"], "Trace it:\n\n    def total(xs):\n        s = 0\n        for x in xs:\n"
                                         "            s += x\n        return s")
        guided = repair_parts([{"kind": "guided_exercise", "body": "Fix it.", "answer": "def f():\n    return 1"}])
        self.assertEqual(guided[0]["answer"], "def f():\n    return 1")

    def test_source_page_variants(self):
        values = (([3, 4], [3, 4]), ("3, 4", [3, 4]), ("5", [5]), (7, [7]), (2.0, [2]), ([1.0, "2", 2], [1, 2]),
                  ("2/6", []), ("S. 3", []), (None, []), ("", []), ({"page": 1}, []))
        for value, expected in values:
            with self.subTest(value=value):
                self.assertEqual(repair_parts([{"kind": "summary", "body": "b", "source_pages": value}])[0]
                                 ["source_pages"], expected)


class TeachingOrderTests(unittest.TestCase):
    def part(self, kind):
        return {"kind": kind, "title": "", "body": kind, "items": [], "answer": "", "from_notes": False,
                "source_pages": []}

    def test_guided_exercise_moves_after_the_first_explanation(self):
        parts = [self.part(k) for k in ("objective", "guided_exercise", "terms", "theory", "argument", "summary")]
        fixed = ensure_teaching_first(parts)
        self.assertEqual([p["kind"] for p in fixed],
                         ["objective", "terms", "theory", "guided_exercise", "argument", "summary"])
        self.assertTrue(teaching_order_ok(fixed))
        self.assertFalse(teaching_order_ok(parts))
        self.assertEqual([p["kind"] for p in parts][1], "guided_exercise")   # input not mutated

    def test_correct_order_is_untouched(self):
        parts = [self.part(k) for k in ("objective", "explanation", "guided_exercise", "summary")]
        self.assertEqual(ensure_teaching_first(parts), parts)
        self.assertTrue(teaching_order_ok(parts))
        self.assertTrue(teaching_order_ok([]))

    def test_no_explanation_reports_a_gap(self):
        parts = [self.part(k) for k in ("guided_exercise", "terms", "summary")]
        self.assertEqual(ensure_teaching_first(parts), parts)
        self.assertFalse(teaching_order_ok(parts))
        review = [self.part(k) for k in ("objective", "terms", "guided_exercise", "summary")]
        self.assertFalse(teaching_order_ok(review))
        self.assertTrue(teaching_order_ok(review, mode="review"))

    def test_explanation_from_parts(self):
        parts = [self.part(k) for k in ("objective", "terms", "explanation", "guided_exercise", "summary")]
        self.assertEqual(explanation_from_parts(parts), "objective\n\nexplanation\n\nsummary")
        long = [{"kind": "explanation", "body": "A sentence. " * 600}, {"kind": "summary", "body": "end"}]
        text = explanation_from_parts(long)
        self.assertLessEqual(len(text), lesson_plan.MAX_EXPLANATION)
        self.assertTrue(text.endswith("."))
        self.assertEqual(explanation_from_parts([]), "")

    def test_explanation_from_parts_with_items_only(self):
        # The required `explanation` field must not come out empty while the lesson has
        # content: a theory given as premise items, or only terms and an exercise.
        theory = repair_parts([{"kind": "theory", "items": [{"label": "Premise", "text": "All humans are mortal"},
                                                            "Conclusion"]},
                               {"kind": "guided_exercise", "body": "Explain.", "answer": "SECRET"}])
        self.assertEqual(explanation_from_parts(theory), "Premise: All humans are mortal\nConclusion")
        only_terms = repair_parts([{"kind": "terms", "items": {"Pflicht": "duty"}},
                                   {"kind": "guided_exercise", "body": "Explain duty.", "answer": "SECRET"}])
        text = explanation_from_parts(only_terms)
        self.assertIn("Pflicht: duty", text)
        self.assertNotIn("SECRET", text)

    def test_odd_entries_do_not_crash_the_order_helpers(self):
        parts = [None, "text", self.part("guided_exercise"), self.part("explanation")]
        self.assertEqual([p.get("kind") if isinstance(p, dict) else p for p in ensure_teaching_first(parts)],
                         [None, "text", "explanation", "guided_exercise"])
        self.assertFalse(teaching_order_ok(parts))
        self.assertEqual(explanation_from_parts(parts), "explanation")


class ExerciseTypeTests(unittest.TestCase):
    def test_rotation(self):
        ethics = REGISTRY["ethics"]
        got = [exercise_type(ethics, n) for n in range(1, 14)]
        self.assertEqual(got[:6], list(ethics.question_types))
        self.assertEqual(got[6:12], list(ethics.question_types))
        self.assertEqual(got[12], "multiple_choice")
        maths = REGISTRY["mathematics"]
        self.assertEqual([exercise_type(maths, n) for n in (1, 2, 3, 4, 5, 6)],
                         ["multiple_choice", "text", "text", "dropdown", "text", "ordering"])

    def test_first_slot_is_always_multiple_choice(self):
        odd = replace(REGISTRY["general"], question_types=("text", "ordering"))
        self.assertEqual(exercise_type(odd, 1), "multiple_choice")
        self.assertEqual(exercise_type(odd, 2), "ordering")
        self.assertEqual(exercise_type(odd, 3), "text")
        self.assertEqual(exercise_type(odd, 0), "multiple_choice")
        self.assertEqual(exercise_type(odd, -5), "multiple_choice")



# ---------------------------------------------------------------------- FIX2 (spec audit)

class EthicsSequenceTests(unittest.TestCase):
    """The user's 13 Ethics steps are explicit in the plan (P2-ETH-1/2, y)."""

    def test_the_notes_example_is_retold_whatever_label_the_extraction_gave_it(self):
        ethics = REGISTRY["ethics"]
        for label in ("thought_experiment", "example", "work", "source"):
            with self.subTest(label=label):
                self.assertIn("thought_experiment", kinds(ethics, items=(label, "term")))
        self.assertNotIn("thought_experiment", kinds(ethics, items=("theory", "term")),
                         "a topic with only a theory has nothing to retell")

    def test_every_step_of_the_sequence_is_asked_for(self):
        steps = {step.kind: step.instruction for step in plan_steps(REGISTRY["ethics"], mode="learn", state="new",
                                                                    item_kinds=("thought_experiment", "theory"))}
        self.assertIn("why it matters", steps["objective"])                      # 1 + default step 2
        self.assertIn("simple words", steps["terms"])                             # 2, 3
        self.assertIn("completely and in order", steps["thought_experiment"])     # 5, 6
        self.assertIn("what it is meant to show", steps["thought_experiment"])    # 7
        self.assertIn("learning objective", steps["thought_experiment"])          # 10
        self.assertIn("central philosophical question", steps["argument"])        # 8
        self.assertIn("interpretations", steps["perspectives"])                   # 9
        self.assertIn("additional", steps["perspectives"])
        self.assertIn("comprehension", steps["guided_exercise"])                  # 11, 12
        # 11-13: the graded questions move from comprehension to an exam task.
        self.assertEqual(REGISTRY["ethics"].question_stages,
                         ("comprehension", "comprehension", "interpretation", "discussion", "exam"))

    def test_every_lesson_says_why_its_topic_matters(self):
        # Default lesson structure, step 2 (P4-06): a "why" part (mathematics) or a sentence
        # of the objective; a secure student's short lesson and a review leave it out.
        for key, strategy in REGISTRY.items():
            for mode in ("learn", "reteach"):
                with self.subTest(strategy=key, mode=mode):
                    steps = plan_steps(strategy, mode=mode, state="new", item_kinds=None)
                    objective = next(step for step in steps if step.kind == "objective")
                    self.assertTrue(any(step.kind == "why" for step in steps)
                                    or "why it matters" in objective.instruction)
        review = plan_steps(REGISTRY["history"], mode="review", state="secure", item_kinds=None)
        self.assertEqual(review[0].instruction, REVIEW_OBJECTIVE)

    def test_a_key_terms_example_the_notes_do_not_give_is_marked_as_added(self):
        # P3-01: a terms part is shown as from the notes; an example Learnova wrote into it
        # must say it is an addition, in every subject whose terms step asks for one.
        for key, strategy in REGISTRY.items():
            for step in strategy.steps:
                if step.kind == "terms" and "example" in step.instruction:
                    with self.subTest(strategy=key):
                        self.assertIn("additional example", step.instruction)


class QuestionStageTests(unittest.TestCase):
    def test_the_stages_are_spread_over_the_session_and_end_with_the_exam_task(self):
        ethics = REGISTRY["ethics"]
        stages = [lesson_plan.question_stage(ethics, n, minimum=3, planned=7) for n in range(1, 9)]
        self.assertEqual(stages, ["comprehension", "comprehension", "interpretation", "discussion", "exam",
                                  "exam", "exam", "exam"])
        short = [lesson_plan.question_stage(ethics, n, minimum=3, planned=3) for n in range(1, 4)]
        self.assertEqual(short, ["comprehension", "interpretation", "exam"],
                         "a three-question test still ends with the exam task")
        for key, strategy in REGISTRY.items():
            with self.subTest(strategy=key):
                self.assertEqual(lesson_plan.question_stage(strategy, 3, minimum=3, planned=3), "exam")

    def test_the_stage_rule_names_the_next_stage_and_the_exam_style(self):
        ethics = REGISTRY["ethics"]
        early = lesson_plan.question_stage_rule(ethics, current=1, upcoming=2, minimum=3, planned=3)
        self.assertIn("interpretation question", early)
        self.assertIn("stays at this question's stage (comprehension)", early)
        self.assertNotIn("Exam style", early)
        late = lesson_plan.question_stage_rule(ethics, current=2, upcoming=3, minimum=3, planned=3)
        self.assertIn("exam-style task", late)
        self.assertIn("Exam style in Ethics and Philosophy: " + ethics.exam_style, late)

    def test_a_strategy_must_end_its_stages_with_the_exam(self):
        with self.assertRaises(ValueError):
            replace(REGISTRY["general"], question_stages=("comprehension",))


class CombinedSubjectTests(unittest.TestCase):
    """Subjects that combine several get a combined strategy (P2-MIX-6)."""

    def test_typed_and_known_composites(self):
        cases = {
            "Geographie-Wirtschaft-Gemeinschaftskunde": "geography+politics",
            "GWG": "geography+politics",
            "EWG": "geography+politics",
            "Geschichte/Gemeinschaftskunde": "history+politics",
            "Physik und Mathematik": "physics+mathematics",
            "Informatik, Mathematik, Physik": "computer_science+mathematics+physics",
            "IMP": "computer_science+mathematics+physics",
            "Sachunterricht": "nwt+geography+history",
        }
        for subject, key in cases.items():
            with self.subTest(subject=subject):
                self.assertEqual(strategy_key_for(subject), key)
                strategy = strategy_for(subject)
                self.assertEqual(strategy.key, key)
                self.assertLessEqual(len(plan_steps(strategy, mode="learn", state="new", item_kinds=None)),
                                     lesson_plan.MAX_PLAN_STEPS)
        timeline = [step.kind for step in strategy_for("Geschichte/Gemeinschaftskunde").steps]
        self.assertIn("timeline", timeline)
        self.assertIn("argument", timeline, "the politics part is taught too")
        self.assertIs(strategy_for("GWG"), strategy_for("EWG"), "a combination is built once")

    def test_single_subjects_and_course_levels_stay_single(self):
        for subject, key in {"Mathe GK": "mathematics", "Mathe-GK": "mathematics", "Bio LK": "sciences",
                             "Geschichte der Philosophie": "ethics", "Naturwissenschaft und Technik": "nwt",
                             "Werte und Normen": "ethics", "Bio-Chemie": "sciences", "Ethik": "ethics"}.items():
            with self.subTest(subject=subject):
                self.assertEqual(strategy_key_for(subject), key)


class KindAliasTests(unittest.TestCase):
    def test_parables_and_dilemmas_are_retold_and_cases_are_examples(self):
        from learnova.projects.topics import item_kind

        self.assertEqual([item_kind(label) for label in ("Dilemma", "Parabel", "Gedankenspiel", "Gleichnis")],
                         ["thought_experiment"] * 4)
        self.assertEqual([item_kind(label) for label in ("Fallbeispiel", "Szenario")], ["example"] * 2)


class NotesGuardTests(unittest.TestCase):
    """Interpretations the notes do not support are marked as added (P2-NOINV-4)."""

    CAVE = "Menschen sitzen gefesselt in einer Höhle und sehen Schatten an der Wand."

    def part(self, kind, body, items=()):
        return {"kind": kind, "title": kind, "body": body, "items": list(items), "answer": "", "from_notes": True,
                "source_pages": [1]}

    def test_a_reading_the_notes_do_not_give_is_not_from_the_notes(self):
        from learnova.lessons.lesson_context import guard_parts

        parts = [self.part("thought_experiment", "Die Sonne steht für die Idee des Guten; das Feuer steht für "
                                                 "die Medien."),
                 self.part("perspectives", "Die Höhle steht für Social Media.")]
        out = guard_parts(parts, item_kinds=["thought_experiment", "term"], source_text=self.CAVE)
        self.assertEqual([(part["kind"], part["from_notes"]) for part in out],
                         [("thought_experiment", False), ("perspectives", False)])

    def test_the_retelling_stays_and_only_the_added_reading_moves(self):
        from learnova.lessons.lesson_context import guard_parts

        retold = self.part("thought_experiment", "Menschen sitzen gefesselt in einer Höhle. Sie sehen nur Schatten "
                                                 "an der Wand. Das Feuer steht für die Medien von heute.")
        own, added = guard_parts([retold], item_kinds=["thought_experiment"], source_text=self.CAVE)
        self.assertTrue(own["from_notes"])
        self.assertIn("Schatten an der Wand", own["body"])
        self.assertNotIn("Medien", own["body"])
        self.assertFalse(added["from_notes"])
        self.assertEqual(added["body"], "Das Feuer steht für die Medien von heute.")
        self.assertEqual(added["source_pages"], [])
        items = self.part("argument", "", items=[{"label": "Schatten", "text": "Menschen sehen Schatten an der Wand"},
                                                 {"label": "Medien", "text": "Heute manipulieren Medien alle"}])
        kept, moved = guard_parts([items], item_kinds=None, source_text=self.CAVE)
        self.assertEqual([item["label"] for item in kept["items"]], ["Schatten"])
        self.assertEqual([item["label"] for item in moved["items"]], ["Medien"])

    NOTES = ("Menschen sind von Kindheit an in einer unterirdischen Höhle gefesselt. Sie können nur nach vorne auf "
             "die Wand schauen. Hinter ihnen brennt ein Feuer. Zwischen Feuer und Gefangenen tragen Leute "
             "Gegenstände vorbei. Die Gefangenen sehen nur die Schatten an der Wand und halten sie für die "
             "Wirklichkeit. Ein Gefangener wird befreit, sieht das Feuer und wird geblendet. Er wird nach draußen "
             "gebracht und erkennt nach und nach die Dinge im Licht der Sonne. Er kehrt zurück, um die anderen zu "
             "befreien, aber sie lachen ihn aus.")
    PARAPHRASE = ("Stell dir vor, Menschen sitzen seit ihrer Kindheit gefesselt in einer Höhle und blicken nur auf "
                  "eine Wand. Hinter ihrem Rücken lodert ein Feuer, und davor werden Gegenstände vorbeigetragen. An "
                  "der Wand erscheinen deshalb nur Schatten, und die Gefangenen glauben, das sei die ganze Welt. Einer "
                  "wird losgebunden, dreht sich um und ist vom Feuer geblendet. Draußen gewöhnt er sich langsam an "
                  "das Sonnenlicht und erkennt die echten Dinge. Als er zurückgeht, um die anderen zu befreien, "
                  "verspotten sie ihn.")

    def test_a_retelling_in_the_models_own_words_stays_whole_and_from_the_notes(self):
        # FIXR review: a faithful retelling in other words (every event is in the notes)
        # lost half its sentences to an "added" part, and the story was told out of order.
        from learnova.lessons.lesson_context import guard_parts

        out = guard_parts([self.part("thought_experiment", self.PARAPHRASE)], item_kinds=["thought_experiment"],
                          source_text=self.NOTES)
        self.assertEqual([(part["from_notes"], part["body"]) for part in out], [(True, self.PARAPHRASE)])

    def test_a_retelling_is_kept_when_the_extraction_called_its_story_a_theory(self):
        # FIXR review (P2-ETH-1, "same item as theory"): the notes' allegory tagged
        # "theory" made the guard drop the model's retelling of it, although every event
        # in it is on the page. What decides is the page, not the label.
        from learnova.lessons.lesson_context import guard_parts

        retold = self.part("thought_experiment", self.PARAPHRASE)
        out = guard_parts([retold], item_kinds=["theory", "term"], source_text=self.NOTES)
        self.assertEqual([(part["kind"], part["from_notes"]) for part in out], [("thought_experiment", True)])
        invented = self.part("thought_experiment", "Stell dir vor, du findest auf dem Schulhof ein Portemonnaie mit "
                                                   "zwanzig Euro. Niemand sieht dich. Behältst du es oder gibst du "
                                                   "es ab, und was würde ein Freund dazu sagen?")
        self.assertEqual(guard_parts([invented], item_kinds=["theory", "term"], source_text=self.NOTES), [],
                         "a story the notes do not contain is still not retold as theirs")

    def test_the_ethics_theory_step_retells_a_story_the_notes_tell_the_theory_with(self):
        from learnova.lessons.lesson_plan import plan_steps

        steps = plan_steps(strategy_for("Ethik"), mode="learn", state="new", item_kinds=["theory", "term"])
        theory = next(step for step in steps if step.kind == "theory")
        self.assertIn("retell", theory.instruction.casefold())

    def test_an_invented_scene_or_reading_inside_a_retelling_still_moves_out(self):
        from learnova.lessons.lesson_context import guard_parts

        body = (self.PARAPHRASE + " Heute starren wir alle auf Bildschirme und jagen Likes hinterher. "
                "Das Feuer steht eigentlich für die sozialen Medien. "
                "Die Gefangenen sind wie Menschen, die nur noch Schatten auf ihrem Handy sehen.")
        own, added = guard_parts([self.part("thought_experiment", body)], item_kinds=["thought_experiment"],
                                 source_text=self.NOTES)
        self.assertEqual((own["from_notes"], own["body"]), (True, self.PARAPHRASE))
        self.assertFalse(added["from_notes"])
        self.assertIn("Bildschirme", added["body"])
        self.assertIn("sozialen Medien", added["body"])
        self.assertIn("Handy", added["body"], "a comparison the notes do not draw is an added reading")

    def test_short_lines_and_lessons_in_another_language_are_not_judged(self):
        from learnova.lessons.lesson_context import guard_parts

        short = guard_parts([self.part("theory", "Kant sagt Nein.")], item_kinds=None, source_text=self.CAVE)
        self.assertTrue(short[0]["from_notes"])
        english = self.part("thought_experiment", "People are chained in a cave and they see only the shadows on "
                                                  "the wall, and this is what the story is about.")
        source = "Die Menschen sitzen in der Höhle und sehen nicht das Licht, sie sehen nur die Schatten."
        out = guard_parts([english], item_kinds=None, source_text=source)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["from_notes"], "an English lesson on German notes cannot be checked word by word")


class PartPagesTests(unittest.TestCase):
    """Parts name the lesson's own pages, not an ambiguous "p. 1" (P4-12, q)."""

    def test_a_page_number_two_files_share_is_resolved_by_content_or_named_as_both(self):
        from learnova.lessons.lesson_context import SourcePage, resolve_part_pages

        pages = [SourcePage(11, "[A.pdf, p. 1]", "Pflicht ist die Notwendigkeit einer Handlung."),
                 SourcePage(21, "[B.pdf, p. 1]", "Der Utilitarismus fragt nach dem Nutzen für alle."),
                 SourcePage(22, "[B.pdf, p. 2]", "Bentham und Mill.")]
        numbers = {11: 1, 21: 1, 22: 2}
        display = {11: "A.pdf p. 1", 21: "B.pdf p. 1", 22: "B.pdf p. 2"}
        parts = [{"kind": "theory", "title": "Utilitarismus", "body": "Der Nutzen für alle zählt.",
                  "source_pages": [1, 2]},
                 {"kind": "summary", "title": "Kurz", "body": "Zusammenfassung.", "source_pages": [1]},
                 {"kind": "summary", "title": "x", "body": "y", "source_pages": [7]},
                 {"kind": "objective", "title": "Ziel", "body": "Ziel.", "source_pages": []}]
        out = resolve_part_pages(parts, pages, numbers=numbers, display=display)
        self.assertEqual(out[0]["source_page_ids"], [21, 22])
        self.assertEqual(out[0]["source_labels"], ["B.pdf p. 1", "B.pdf p. 2"])
        self.assertEqual(out[1]["source_page_ids"], [], "nothing singles one file out")
        self.assertEqual(out[1]["source_labels"], ["A.pdf p. 1 / B.pdf p. 1"])
        self.assertEqual((out[2]["source_page_ids"], out[2]["source_labels"]), ([], []),
                         "a page the lesson does not have is not shown")
        self.assertNotIn("source_labels", out[3])


if __name__ == "__main__":
    unittest.main()
