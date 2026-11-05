"""PF4 L3/L5/L6: lessons keep the subject-defining step their notes contain, and only what
the notes say feeds the hidden explanation (offline, every model call mocked).

Live evidence (scratchpad prod/live_lessons.md §2):
* L3: the topic map came back without an item of the defining kind, so `plan_steps` dropped
  the step although the notes have it under their own headings - NwT (Fragestellung,
  Vermutung, Aufbau, Durchführung, Beobachtung, Ergebnis, Erklärung: no experiment part),
  History ("Quelle: Art. 48 ...": no source analysis), Geography ("Kartenbeschreibung": no map).
  The synthetic notes of that run are in tests/fixtures/notes_2026-11-01/.
* L5: the model's own `explanation` was kept whenever it was not empty; it is never guarded
  and feeds the question generator (the Ethics "Feuer" reading reached a graded rubric).
* L6: "Das Experiment soll zeigen, ..." was moved to "added" against the notes' own
  "Das Gedankenexperiment soll zeigen, ..." (a compound's head was not accepted); and why,
  explanation, rule and cause-effect parts and term texts were never held to the notes
  ("Kosten-Gewinn-Diagramme", "Handschuhe", "Hyperinflation … Radikalisierung", "Waffen").
"""

import os
import tempfile
import unittest
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_lesson_grounding_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.lessons import lesson_context, lesson_plan  # noqa: E402
from learnova.lessons.strategies import strategy_for  # noqa: E402
from tests import test_lesson_teaching_engine as teaching  # noqa: E402

NOTES = Path(__file__).parent / "fixtures" / "notes_2026-11-01"


def notes(name):
    return (NOTES / f"{name}.txt").read_text(encoding="utf-8")


NWT, HISTORY, GEOGRAPHY = notes("NwT_Waermedaemmung_Versuch"), notes("Geschichte_Weimarer_Verfassung"), notes(
    "Geographie_Tsunami_Japan")
ETHICS, ENGLISH, MATHS = notes("Ethik_Hoehlengleichnis"), notes("Englisch_Present_Perfect"), notes(
    "Mathe_Lineare_Funktionen")
TERMS_ONLY_MAP = {"terms": [teaching.term_entry("Wärmeleitung", "Wärme wird weitergegeben", 1)], "items": []}


class HeadingKindTests(unittest.TestCase):
    def test_heading_kinds_of_the_live_notes(self):
        self.assertIn("experiment", lesson_context.heading_kinds(NWT))
        self.assertIn("source", lesson_context.heading_kinds(HISTORY))
        self.assertIn("place", lesson_context.heading_kinds(GEOGRAPHY))

    def test_heading_kinds_add_nothing_where_the_notes_have_none(self):
        for name, text in (("Ethik", ETHICS), ("Englisch", ENGLISH), ("Mathe", MATHS)):
            with self.subTest(notes=name):
                self.assertEqual(lesson_context.heading_kinds(text), set())

    def test_heading_shapes(self):
        self.assertEqual(lesson_context.heading_kinds("Hypothese\nDas Eis schmilzt schneller.\nErgebnis:\nStimmt."),
                         {"experiment"})
        self.assertEqual(lesson_context.heading_kinds("1. Fragestellung: Was passiert?\n- Beobachtung: Es blubbert."),
                         {"experiment"})
        self.assertEqual(lesson_context.heading_kinds("Ergebnis: Die Reform scheiterte."), set(),
                         "one phase word is no experiment")
        self.assertEqual(lesson_context.heading_kinds("M1: Rede von Otto von Bismarck, 1862"), {"source"})
        quoted = "„" + " ".join(["Wort"] * 16) + "“"
        self.assertEqual(lesson_context.heading_kinds(quoted), {"source"})
        self.assertEqual(lesson_context.heading_kinds("Die Lage von Kenia\nKenia liegt in Ostafrika."), {"place"})
        self.assertEqual(lesson_context.heading_kinds(""), set())

    def test_plan_item_kinds_adds_the_heading_kinds(self):
        self.assertEqual(lesson_context.plan_item_kinds(TERMS_ONLY_MAP), ["term"])
        kinds = lesson_context.plan_item_kinds(TERMS_ONLY_MAP, source_text=NWT)
        self.assertIn("experiment", kinds)
        self.assertIn("term", kinds)
        self.assertIsNone(lesson_context.plan_item_kinds({}, source_text=NWT), "an unchecked map stays None")
        self.assertIsNone(lesson_context.plan_item_kinds(None, source_text=NWT))

    def test_the_nwt_plan_with_an_empty_item_list_teaches_the_experiment(self):
        strategy = strategy_for("NwT")
        without = lesson_plan.plan_steps(strategy, mode="learn", state="new",
                                         item_kinds=lesson_context.plan_item_kinds(TERMS_ONLY_MAP))
        self.assertNotIn("experiment", [step.kind for step in without], "the live defect")
        fixed = lesson_plan.plan_steps(strategy, mode="learn", state="new",
                                       item_kinds=lesson_context.plan_item_kinds(TERMS_ONLY_MAP, source_text=NWT))
        self.assertIn("experiment", [step.kind for step in fixed])

    def test_history_and_geography_plans_get_their_source_and_map_steps(self):
        for subject, text, kind in (("Geschichte", HISTORY, "source_analysis"), ("Geographie", GEOGRAPHY, "diagram")):
            with self.subTest(subject=subject):
                kinds = lesson_context.plan_item_kinds({"terms": [], "items": []}, source_text=text)
                steps = lesson_plan.plan_steps(strategy_for(subject), mode="learn", state="new", item_kinds=kinds)
                self.assertIn(kind, [step.kind for step in steps])


# Review B B-2 (prodrev_b/probes/exp_fp.py): a chemistry calculation sheet.
CHEMISTRY_SHEET = ("[Chemie.pdf, p. 1]\nStöchiometrie\nAufgabe 1: Berechne die Masse von 2 mol Wasser.\nErgebnis: 36 g\n"
                   "Erklärung: Die molare Masse von Wasser ist 18 g/mol, also ergibt sich 2 · 18 g = 36 g.\n"
                   "Aufgabe 2: Berechne die Stoffmenge in 88 g Kohlenstoffdioxid.\nErgebnis: 2 mol\n"
                   "Erklärung: Die molare Masse von CO2 ist 44 g/mol.")
INVENTED_EXPERIMENT = teaching.part("experiment", "", title="Versuch", items=[
    {"label": "Fragestellung", "text": "Wie viel wiegt Wasser beim Verdampfen?"},
    {"label": "Durchführung", "text": "Wir erhitzen 36 g Wasser im Becherglas mit dem Bunsenbrenner."},
    {"label": "Beobachtung", "text": "Das Wasser verdampft vollständig nach zehn Minuten."}])


class GenericHeadingTests(unittest.TestCase):
    """Review B B-2: "Ergebnis", "Erklärung", "Ziel der Stunde" or "Material" head worksheets of
    every subject. Two of them switched the experiment step on, and an invented experiment was
    then kept as the notes' own."""

    def test_generic_lesson_headings_are_no_experiment(self):
        for text in (CHEMISTRY_SHEET,
                     "Bruchrechnung\nZiel der Stunde: Brüche addieren\nErgebnis: Erst gleichnamig machen.",
                     "Zelle\nMaterial: Mikroskop\nErgebnis der Stunde: Zellen haben eine Membran.",
                     "Jahreszeiten\nFragestellung: Warum gibt es Jahreszeiten?\nErklärung: Die Erdachse ist geneigt.",
                     "Entstehung eines Tsunamis (Ablauf)\nErgebnis: Eine Welle läuft auf die Küste zu.",
                     "Lineare Funktionen\nAufgabe: Zeichne die Gerade.\nMethode: Steigungsdreieck\nErgebnis: m = 2"):
            with self.subTest(text=text.splitlines()[0]):
                self.assertNotIn("experiment", lesson_context.heading_kinds(text))
        self.assertEqual(lesson_context.heading_kinds("Vokabeln Unit 3\nKarteikarten\nhouse - Haus"), set(),
                         "flashcards are no map")
        self.assertEqual(lesson_context.heading_kinds("Wetterkarte\nEin Tief zieht auf."), {"place"})

    def test_a_distinctive_phase_still_makes_an_experiment(self):
        for text in (NWT, "Vermutung: Eis schmilzt im Salzwasser schneller.\nErgebnis: Stimmt.",
                     "Versuchsaufbau: Becherglas, Thermometer\nAuswertung: Die Temperatur sinkt.",
                     "Versuchsdurchführung: Wasser erhitzen.\nErgebnis: Es siedet bei 100 °C.",
                     "Material: Kerze, Glas\nBeobachtung: Die Flamme erlischt.",
                     "Hypothesis: Wool insulates best.\nResults: Styrofoam 62 °C."):
            with self.subTest(text=text.splitlines()[0]):
                self.assertIn("experiment", lesson_context.heading_kinds(text))

    def test_the_chemistry_sheet_plans_no_experiment_and_drops_an_invented_one(self):
        topic_map = {"terms": [teaching.term_entry("molare Masse", "Masse eines Mols", 1)], "items": []}
        kinds = lesson_context.plan_item_kinds(topic_map, source_text=CHEMISTRY_SHEET)
        for subject in ("Chemie", "Biologie", "Physik", "NwT"):
            with self.subTest(subject=subject):
                steps = lesson_plan.plan_steps(strategy_for(subject), mode="learn", state="new", item_kinds=kinds)
                self.assertNotIn("experiment", [step.kind for step in steps])
        guarded = lesson_context.guard_parts([INVENTED_EXPERIMENT], item_kinds=kinds, source_text=CHEMISTRY_SHEET)
        self.assertEqual([entry["kind"] for entry in guarded], [], "an experiment the notes do not have is dropped")

    def test_an_experiment_known_only_from_headings_is_kept_only_when_retold_from_the_pages(self):
        kinds = lesson_context.plan_item_kinds(TERMS_ONLY_MAP, source_text=NWT)
        retold = teaching.part("experiment", "", title="Versuch", items=[
            {"label": "Fragestellung", "text": "Welches Material hält heißes Wasser am längsten warm?"},
            {"label": "Aufbau", "text": "Vier gleiche Bechergläser mit 200 ml Wasser von 70 °C: ohne Hülle, Alufolie, "
                                        "Wollschal und Styropor-Hülle."},
            {"label": "Beobachtung", "text": "Nach 20 Minuten: ohne Hülle 48 °C, Alufolie 52 °C, Wolle 60 °C, "
                                             "Styropor 62 °C."}])
        for part, kept in ((retold, True), (INVENTED_EXPERIMENT, False)):
            with self.subTest(kept=kept):
                guarded = lesson_context.guard_parts([part], item_kinds=kinds, source_text=NWT,
                                                     heading_only_kinds={"experiment"})
                self.assertEqual([entry["kind"] for entry in guarded], ["experiment"] if kept else [])
        listed = lesson_context.guard_parts([INVENTED_EXPERIMENT], item_kinds=["experiment"], source_text=NWT)
        self.assertEqual([entry["kind"] for entry in listed], ["experiment"],
                         "a checked experiment item in the topic map is trusted as before")


class CompoundTests(unittest.TestCase):
    def test_compound_head_is_supported(self):
        page = ("Gedankenexperiment Transplantationsarzt: Ein Arzt könnte einen Gesunden töten und fünf Leben "
                "retten. Das Gedankenexperiment soll zeigen, dass der Utilitarismus die Rechte des Einzelnen nicht "
                "genug schützt.")
        part = teaching.part("thought_experiment", "Ein Arzt könnte einen Gesunden töten und fünf Leben retten. "
                             "Das Experiment soll zeigen, dass der Utilitarismus die Rechte des Einzelnen nicht genug "
                             "schützt.")
        guarded = lesson_context.guard_parts([part], item_kinds=["thought_experiment"], source_text=page)
        self.assertEqual(len(guarded), 1, [entry.get("body") for entry in guarded])
        self.assertTrue(guarded[0]["from_notes"])
        self.assertIn("Das Experiment soll zeigen", guarded[0]["body"])

    def test_a_reading_the_notes_do_not_give_still_moves(self):
        page = "Die Gefangenen sehen nur Schatten an der Wand. Draußen scheint die Sonne."
        part = teaching.part("thought_experiment", "Die Gefangenen sehen nur Schatten an der Wand. "
                             "Das Feuer steht für die Medien von heute.")
        guarded = lesson_context.guard_parts([part], item_kinds=["thought_experiment"], source_text=page)
        added = [entry for entry in guarded if not entry["from_notes"]]
        self.assertTrue(added and "Feuer" in added[0]["body"])


class ExplainingPartsTests(unittest.TestCase):
    """L6 extension: why, explanation, rule and cause-effect parts and the texts of from-notes
    key terms are held to the pages too. The part texts are the live model output of
    2026-11-01 (prod_live_lessons/out/raw/002, 020, 024, 034), judged against the same notes."""

    @staticmethod
    def own_text(parts):
        return " ".join(f"{entry.get('body') or ''} " + " ".join(
            f"{item.get('label')} {item.get('text')}" for item in entry.get("items") or [])
            for entry in parts if entry["from_notes"])

    def test_a_why_part_the_notes_do_not_give_is_marked_as_added(self):
        part = teaching.part("why", "Lineare Funktionen beschreiben viele Alltagsbeziehungen, z. B. "
                             "Kosten‑Gewinn‑Diagramme oder Geschwindigkeit‑Zeit‑Graphen. Sie bilden die Basis für "
                             "weitere Themen wie quadratische Funktionen und lineare Gleichungssysteme.")
        guarded = lesson_context.guard_parts([part], item_kinds=["term"], source_text=MATHS)
        self.assertNotIn("Kosten", self.own_text(guarded))
        self.assertNotIn("quadratische", self.own_text(guarded))
        self.assertTrue(any("Kosten" in entry["body"] and not entry["from_notes"] for entry in guarded))

    def test_invented_safety_rules_move_and_the_notes_rule_stays(self):
        part = teaching.part("rule", "Beim Experiment mit heißem Wasser sollten immer hitzebeständige Handschuhe "
                             "getragen werden, um Verbrennungen zu vermeiden. Wichtig: Nur das Hüllmaterial wird "
                             "verändert, Wassermenge, Starttemperatur und Raumtemperatur bleiben gleich.")
        guarded = lesson_context.guard_parts([part], item_kinds=["experiment"], source_text=NWT)
        self.assertNotIn("Handschuhe", self.own_text(guarded))
        self.assertIn("Hüllmaterial", self.own_text(guarded))

    def test_an_invented_cause_moves_out_of_a_from_notes_cause_effect_part(self):
        part = teaching.part("cause_effect", items=[
            {"label": "Fehlende Sperrklausel",
             "text": "Viele kleine Parteien kamen in den Reichstag, was Regierungen schwer machte, eine stabile "
                     "Mehrheit zu finden."},
            {"label": "Hyperinflation", "text": "Sie zerstörte die Wirtschaft, führte zu Unzufriedenheit und "
                                                "politischer Radikalisierung."}])
        guarded = lesson_context.guard_parts([part], item_kinds=["source"], source_text=HISTORY)
        self.assertIn("Sperrklausel", self.own_text(guarded))
        self.assertNotIn("Radikalisierung", self.own_text(guarded))

    def test_an_invented_example_leaves_the_term_its_definition(self):
        page = ("Mill und die Freiheit: Mill schrieb außerdem das Buch Über die Freiheit (1859). Darin steht das "
                "Schadensprinzip: Der Staat darf die Freiheit eines Menschen nur einschränken, um Schaden von "
                "anderen abzuwenden.")
        part = teaching.part("terms", items=[{
            "label": "Schadensprinzip", "text": "Der Staat darf Freiheit nur einschränken, um Schaden von anderen "
                                                "abzuwenden. Beispiel: Verbot von gefährlichen Waffen."}])
        guarded = lesson_context.guard_parts([part], item_kinds=["term"], source_text=page)
        own = [entry for entry in guarded if entry["from_notes"]]
        added = [entry for entry in guarded if not entry["from_notes"]]
        self.assertEqual(own[0]["items"][0]["text"],
                         "Der Staat darf Freiheit nur einschränken, um Schaden von anderen abzuwenden.")
        self.assertEqual(added[0]["items"][0], {"label": "Schadensprinzip",
                                                "text": "Beispiel: Verbot von gefährlichen Waffen."})

    def test_abbreviations_do_not_end_a_sentence(self):
        self.assertEqual(lesson_context._sentences("Fehlerquellen, z. B. ein Deckel. Am 11. März 2011 bebte es."),
                         ["Fehlerquellen, z. B. ein Deckel.", "Am 11. März 2011 bebte es."])


class GroundedLessonTests(teaching.TeachingEngineCase):
    def respond(self, *, task_type, **kwargs):
        if task_type == "question_generation":
            # A question the gate accepts (not a repeat of the current one).
            self.calls.append({"task_type": task_type, **kwargs})
            return teaching.FakeResponse({"question": {
                **teaching.QUESTION, "id": f"g{len(self.calls)}", "type": "text", "options": [],
                "prompt": f"Neue Frage {len(self.calls)}: Was sehen die Gefangenen an der Wand?",
                "expected_answer": "Schatten"}})
        return super().respond(task_type=task_type, **kwargs)

    def test_the_nwt_lesson_plans_and_keeps_the_experiment_of_its_notes(self):
        _, section_id, _ = self.make_project("NwT", [("NwT-Versuch.pdf", [NWT])], lambda ids: {
            "title": "Wärmedämmung", "summary": "Ein Versuch zur Wärmedämmung.",
            "terms": [teaching.term_entry("Wärmeleitung", "Wärme wird weitergegeben", ids[0])], "items": [],
            "exam_questions": []})
        experiment = teaching.part("experiment", items=[
            {"label": "Fragestellung", "text": "Welches Material hält heißes Wasser am längsten warm?"},
            {"label": "Ergebnis", "text": "Styropor dämmt in unserem Versuch am besten, knapp vor Wolle."},
            {"label": "Vermutung", "text": "Wolle dämmt am besten, weil Winterkleidung aus Wolle ist."}])
        self.lesson_parts = [teaching.part("objective", "Du kannst den Versuch erklären."), experiment,
                             teaching.part("guided_exercise", "Erkläre das Ergebnis.", answer="Lufteinschlüsse"),
                             teaching.part("summary", "Styropor dämmt am besten.")]
        session_id = self.start(section_id)
        self.assertIn("experiment", self.planned_kinds(self.prompt()))
        parts = self.lesson(session_id)["parts"]
        kept = next(entry for entry in parts if entry["kind"] == "experiment")
        self.assertEqual([item["label"] for item in kept["items"]], ["Fragestellung", "Vermutung", "Ergebnis"],
                         "kept, in lab-report order")

    def test_explanation_from_guarded_parts(self):
        _, section_id, _ = self.make_project("Ethik", [("Ethik-Blatt.pdf", [teaching.CAVE])],
                                             teaching.cave_topic("thought_experiment"))
        parts = [teaching.part("objective", "Du kannst das Höhlengleichnis nacherzählen."),
                 teaching.part("thought_experiment", "Menschen sitzen gefesselt in einer Höhle und sehen nur Schatten."),
                 teaching.part("perspectives", "Das Feuer steht für die Medien von heute.", from_notes=False,
                               title="Zusätzliche Deutung"),
                 teaching.part("summary", "Ein Befreiter sieht draußen die Sonne.")]
        self.lesson_parts = lambda _kwargs: parts

        def lesson_with_fire(*, task_type, **kwargs):
            response = self.respond(task_type=task_type, **kwargs)
            if task_type == "lesson_generation":
                payload = teaching.lesson_payload(parts, explanation="Das Feuer steht für die begrenzte Sicht.")
                return teaching.FakeResponse(payload)
            return response

        from unittest.mock import patch

        from flask_login import login_user
        with application.app.test_request_context():
            login_user(teaching.db.session.get(application.User, self.user_id))
            section = teaching.db.session.get(application.LearningSection, section_id)
            with patch.object(application, "create_response", side_effect=lesson_with_fire):
                session_id = application.start_section_lesson(section, "learn")
        explanation = self.lesson(session_id)["explanation"]
        self.assertNotIn("Feuer", explanation)
        self.assertIn("Höhle", explanation)
        # The question generator reads the explanation as the lesson's context.
        payload = teaching.evaluation(20)
        payload["next_question"] = {"id": "q2", "concept": "Gerechtigkeit"}       # incomplete: the gate writes one
        self.answers = [payload]
        application.SESSIONS[session_id]["teaching_first"] = False
        self.answer(session_id, "b")
        generated = [call for call in self.calls if call["task_type"] == "question_generation"]
        self.assertTrue(generated, "the gate's own generator wrote the next question")
        self.assertNotIn("Feuer", str(generated[-1]["input"]))


if __name__ == "__main__":
    unittest.main()
