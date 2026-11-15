"""Spec 1.2/1.3: exam material in the chat becomes Short Lessons or a project, and every
Short Lesson teaches completely.

Pure parts (learnova/assistant/study_format.py, the EXAM trailer, the Short Lesson step
planner) are tested directly; the routes through Flask with `app.create_response` patched
(no AI call, scratch database). Covered: the format decision and its thresholds, the
hidden EXAM line, three topics -> three separate topic lessons (each scoped to its topic,
one row and one AI call per topic, completion tracked), twelve topics -> one project with
every topic through the existing creation path (idempotent, the weekly allowance
respected), a borderline exam -> a choice with one recommendation, the free plan's lesson
messaging, the PDF hand-off, the shared ConceptMastery seen by a project section, the
/api/analyze Short Lesson on the teaching engine, and migration 044 on an old database.
"""

import io
import json
import re
import unittest
from datetime import date, timedelta
from pathlib import Path

from PIL import Image

from tests.test_continue_learning import (  # noqa: E402 - sets DATABASE_URL first
    ContinueLearningTestCase, FakeAI, application, lesson_payload,
)
from learnova import assistant  # noqa: E402
from learnova.assistant import continue_learning as cl  # noqa: E402
from learnova.assistant import formatting, presets, study_format as sf  # noqa: E402
from learnova.ai_services.prompts import PROMPT_VERSIONS  # noqa: E402
from learnova.lessons import lesson_plan  # noqa: E402
from learnova.lessons.strategies import strategy_for  # noqa: E402
from tests.typing_helpers import fetch  # noqa: E402

db = application.db
ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 11, 13)

THREE = ["Bruchrechnung", "Prozentrechnung", "Zinsrechnung"]
TWELVE = ["Lineare Funktionen", "Steigung", "Nullstellen", "Quadratische Gleichungen", "Pythagoras",
          "Kreisberechnung", "Prismen", "Zylinder", "Wahrscheinlichkeit", "Baumdiagramme", "Terme", "Potenzen"]
SIX = ["Satzglieder", "Kommasetzung", "Erörterung", "Inhaltsangabe", "Konjunktiv", "Zeitformen"]


def exam_reply(topics, text="Super, das schaffen wir. Wo möchtest du anfangen?"):
    return f"{text}\nEXAM: {' | '.join(topics)}\nTOPIC: Mathematics | Klassenarbeit"


def png_bytes(color=(250, 250, 250)):
    output = io.BytesIO()
    Image.new("RGB", (320, 240), color).save(output, format="PNG")
    return output.getvalue()


# ------------------------------------------------------------------------ pure: decision
class DecisionTests(unittest.TestCase):
    def signals(self, titles, **extra):
        return sf.MaterialSignals(topics=tuple(sf.parse_exam_topics(titles)), today=TODAY, **extra)

    def test_three_manageable_topics_are_separate_lessons(self):
        decision = sf.decide_format(self.signals(THREE, photo_count=2, reading_chars=3000))
        self.assertEqual((decision.kind, decision.recommended, decision.reasons), ("lessons", "lessons", ("few_topics",)))
        self.assertEqual([item.title for item in decision.topics], THREE)

    def test_many_topics_or_a_large_volume_is_a_project(self):
        self.assertEqual(sf.decide_format(self.signals(TWELVE)).kind, "project")
        self.assertEqual(sf.decide_format(self.signals(TWELVE)).reasons[0], "many_topics")
        self.assertEqual(sf.decide_format(self.signals(THREE, photo_count=8)).kind, "project")
        self.assertEqual(sf.decide_format(self.signals(THREE, reading_chars=15000)).reasons[:2],
                         ("large_volume", "much_material"))

    def test_between_the_thresholds_is_a_choice_with_one_recommendation(self):
        far = sf.decide_format(self.signals(SIX, exam_date=TODAY + timedelta(days=14)))
        self.assertEqual((far.kind, far.recommended, far.reasons), ("choice", "project", ("exam_far",)))
        soon = sf.decide_format(self.signals(SIX[:5], exam_date=TODAY + timedelta(days=1)))
        self.assertEqual((soon.kind, soon.recommended, soon.reasons), ("choice", "lessons", ("exam_soon",)))
        short = sf.decide_format(self.signals(SIX[:4]))
        self.assertEqual((short.kind, short.recommended), ("choice", "lessons"))
        linked = sf.decide_format(self.signals(["Lineare Funktionen", "Quadratische Funktionen",
                                                "Funktionen zeichnen", "Funktionen ablesen"]))
        self.assertEqual((linked.kind, linked.recommended, linked.reasons), ("choice", "project", ("linked_topics",)))

    def test_one_topic_or_none_keeps_the_single_lesson_button(self):
        self.assertEqual(sf.decide_format(self.signals(["Bruchrechnung"])).kind, "none")
        self.assertEqual(sf.decide_format(self.signals([])).kind, "none")

    def test_thresholds_are_parameters_and_named_constants(self):
        self.assertEqual((sf.SMALL_MAX_TOPICS, sf.LARGE_MIN_TOPICS, sf.EXAM_SOON_DAYS, sf.EXAM_SOON_MAX_TOPICS,
                          sf.EXAM_FAR_DAYS, sf.EXAM_FAR_MIN_TOPICS), (3, 10, 2, 6, 7, 6))
        self.assertEqual(sf.decide_format(self.signals(SIX[:4]), small_max=4).kind, "lessons")
        self.assertEqual(sf.decide_format(self.signals(SIX), large_min=6).kind, "project")

    def test_known_topics_take_less_time(self):
        topics = tuple(sf.parse_exam_topics(SIX[:4]))
        new = sf.decide_format(sf.MaterialSignals(topics=topics, today=TODAY))
        secure = sf.decide_format(sf.MaterialSignals(topics=topics, today=TODAY,
                                                     knowledge={item.key: "secure" for item in topics}))
        self.assertLess(secure.estimated_minutes, new.estimated_minutes)

    def test_topics_are_deduplicated_by_key_and_keep_subtopics(self):
        topics = sf.parse_exam_topics("Brüche (kürzen, erweitern) | brüche | none | " + " | ".join(
            f"Thema {n}" for n in range(30)))
        self.assertEqual(topics[0].title, "Brüche")
        self.assertEqual(topics[0].subtopics, ("kürzen", "erweitern"))
        self.assertEqual(len(topics), sf.MAX_EXAM_TOPICS)
        self.assertEqual(sf.topics_from_json(sf.topics_to_json(topics)), topics)

    def test_the_exam_date_is_read_only_from_a_message_about_a_test(self):
        read = sf.exam_date_from_text
        self.assertEqual(read("Klassenarbeit am 2026-12-01", TODAY), date(2026, 12, 1))
        self.assertEqual(read("Wir schreiben am 1.12. eine Arbeit", TODAY), date(2026, 12, 1))
        self.assertEqual(read("Test morgen!", TODAY), TODAY + timedelta(days=1))
        self.assertEqual(read("Die Klausur ist in 2 Wochen", TODAY), TODAY + timedelta(days=14))
        self.assertIsNone(read("Am 1.9.1939 begann der Zweite Weltkrieg", TODAY))
        self.assertIsNone(read("Morgen gehe ich schwimmen", TODAY), "no test or exam named")
        self.assertIsNone(read("Klassenarbeit am 01.01.2026", TODAY), "a date in the past")
        # Review fixes: a greeting and section numbers are no exam date.
        self.assertEqual(read("Guten Morgen, ich habe nächste Woche eine Klassenarbeit", TODAY),
                         TODAY + timedelta(days=7))
        self.assertIsNone(read("Klassenarbeit über Kapitel 3.4. und 5.2.", TODAY))
        self.assertIsNone(read("Klausur: Aufgabe 2.3. bis 4.5.", TODAY))
        self.assertIsNone(read("Test über 1.2.3. im Buch", TODAY))
        self.assertEqual(read("Klausur am Mo. 1.12.", TODAY), date(2026, 12, 1))
        self.assertEqual(read("Kapitel 3.4. und Test am 1.12.", TODAY), date(2026, 12, 1))
        self.assertEqual(read("Mathearbeit am 1.12.", TODAY), date(2026, 12, 1), "a German compound")
        self.assertIsNone(read("Die Hausarbeit ist am 1.12. fällig", TODAY), "homework is no exam")

    def test_a_typed_goal_lists_topics_only_for_a_test(self):
        self.assertEqual([item.title for item in sf.goal_topics("Klassenarbeit: Brüche, Prozente, Zinsen")],
                         ["Brüche", "Prozente", "Zinsen"])
        self.assertEqual(sf.goal_topics("Ich verstehe Brüche, Prozente und Zinsen nicht"), [])
        hint = sf.material_hint(headings=[], goal="Klassenarbeit: " + ", ".join(TWELVE), photo_count=0,
                                reading_chars=0, today=TODAY)
        self.assertEqual((hint.kind, len(hint.topics)), ("project", 12))
        self.assertIsNone(sf.material_hint(headings=["Brüche"], goal="Brüche", photo_count=1, reading_chars=900))


# ------------------------------------------------------------------------ pure: trailer
class ExamTrailerTests(unittest.TestCase):
    def test_the_exam_line_is_cut_off_and_parsed(self):
        result = formatting.reply_trailers(exam_reply(THREE + ["bruchrechnung"]))
        self.assertEqual(result.text, "Super, das schaffen wir. Wo möchtest du anfangen?")
        self.assertEqual([item.title for item in result.exam_topics], THREE)
        self.assertEqual(result.topic, ("Mathematics", "Klassenarbeit"))
        self.assertNotIn("EXAM", result.text)

    def test_markdown_and_a_fence_around_the_exam_line_go_too(self):
        self.assertEqual(formatting.reply_trailers("Hallo\n**EXAM:** a | b").text, "Hallo")
        fenced = formatting.reply_trailers("Hallo\n```\nEXAM: a | b\nTOPIC: Maths | a\n```")
        self.assertEqual((fenced.text, [item.title for item in fenced.exam_topics]), ("Hallo", ["a", "b"]))
        self.assertEqual(formatting.reply_trailers("Hallo\nEXAM: none").exam_topics, [])

    def test_split_reply_trailers_keeps_its_three_values(self):
        self.assertEqual(assistant.split_reply_trailers(exam_reply(THREE)),
                         ("Super, das schaffen wir. Wo möchtest du anfangen?", [], ("Mathematics", "Klassenarbeit")))

    def test_the_layer_asks_for_the_exam_line_within_its_budget(self):
        self.assertIn("EXAM: topic | topic", presets.TUTORING_LAYER)
        self.assertLessEqual(assistant.estimate_tokens(presets.TUTORING_LAYER), 420)
        line = next(item for item in presets.TUTORING_LAYER.split("\n") if "EXAM:" in item)
        self.assertLessEqual(assistant.estimate_tokens("\n" + line), 22, "room for another lane's MEDIA line")
        self.assertEqual(PROMPT_VERSIONS["assistant_chat"], "assistant_chat:v6")


# ------------------------------------------------------------------------ pure: Short Lesson plan
class ShortLessonStepTests(unittest.TestCase):
    def kinds(self, subject, short):
        steps = lesson_plan.plan_steps(strategy_for(subject), mode="learn", state="new", item_kinds=None)
        return [step.kind for step in (lesson_plan.short_lesson_steps(steps) if short else steps)]

    def test_every_short_lesson_has_the_spec_parts(self):
        for subject in ("Mathematics", "History", "German", "Ethics", "Biology", "English", "Other"):
            kinds = self.kinds(subject, True)
            with self.subTest(subject=subject):
                self.assertEqual(kinds[0], "objective")
                for kind in ("terms", "mistakes", "guided_exercise", "summary"):
                    self.assertIn(kind, kinds)
                self.assertTrue(set(kinds) & {"example", "worked_example", "model_analysis", "source_analysis"})
                self.assertLess(kinds.index("mistakes"), kinds.index("guided_exercise"))
                self.assertLessEqual(len(kinds), lesson_plan.MAX_PLAN_STEPS)

    def test_the_project_strategies_are_unchanged(self):
        self.assertNotIn("terms", self.kinds("Mathematics", False))
        self.assertNotIn("mistakes", self.kinds("History", False))

    def test_the_summary_ends_with_an_understanding_check(self):
        steps = lesson_plan.short_lesson_steps(
            lesson_plan.plan_steps(strategy_for("Mathematics"), mode="learn", state="new", item_kinds=None))
        summary = next(step for step in steps if step.kind == "summary")
        self.assertIn("understanding check", summary.instruction)

    def test_the_series_line_names_the_topic_and_the_others(self):
        line = cl.series_line("Prozentrechnung", ["Bruchrechnung", "Zinsrechnung"], ["Grundwert"])
        self.assertIn('teach ONLY "Prozentrechnung"', line)
        self.assertIn("Bruchrechnung; Zinsrechnung", line)
        self.assertIn("It covers: Grundwert.", line)


# ------------------------------------------------------------------------ routes
class StudyFormatRouteCase(ContinueLearningTestCase):
    def exam_conversation(self, topics, *, message=None, photos=(), subject="Mathematics"):
        """A conversation whose newest reply listed these exam topics (EXAM line stored)."""

        message = message or ("Klassenarbeit nächste Woche: " + ", ".join(topics) + ". Hilf mir bitte.")
        conversation_id = self.conversation(("user", message), ("assistant", "Super, das schaffen wir."),
                                            subject=subject, topic="Klassenarbeit")
        with application.app.app_context():
            rows = db.session.scalars(db.select(application.ConversationMessage).where(
                application.ConversationMessage.conversation_id == conversation_id).order_by(
                application.ConversationMessage.id)).all()
            reply = rows[-1]
            reply.study_json = json.dumps({"exam_topics": sf.topics_to_json(sf.parse_exam_topics(topics))})
            for position, (reading, data) in enumerate(photos):
                db.session.add(application.ChatAttachment(
                    user_id=self.user_id(), conversation_id=conversation_id, message_id=rows[0].id, purpose="chat",
                    position=position, reading_json=json.dumps(reading, ensure_ascii=False), read_status="ready",
                    sent_at=application.utcnow(), data=data, mime_type="image/png"))
            db.session.commit()
        return conversation_id

    def state(self, conversation_id, client=None):
        return self.status(conversation_id, client).get_json()["continue_learning"]

    def project(self, conversation_id, client=None, **body):
        return (client or self.client).post(f"/api/assistant/conversations/{conversation_id}/project", json=body)

    def projects(self):
        with application.app.app_context():
            rows = db.session.scalars(db.select(application.LearningProject)).all()
            for row in rows:
                db.session.expunge(row)
            return rows


def reading(*blocks):
    return {"images": [{"index": 1, "kind": "worksheet", "legibility": "clear"}],
            "blocks": [{"image": 1, "type": kind, "text": text, "author": "printed", "confidence": 0.9}
                       for kind, text in blocks],
            "diagrams": [], "uncertain": [], "summary": "Arbeitsblatt.", "language": "de"}


class SendTests(StudyFormatRouteCase):
    def test_a_reply_with_an_exam_line_stores_its_topics_and_offers_three_lessons(self):
        conversation_id = self.client.post("/api/assistant/conversations", json={}).get_json()["conversation"]["id"]
        with FakeAI(chat=exam_reply(THREE)):
            sent = self.client.post(f"/api/assistant/conversations/{conversation_id}/messages",
                                    json={"message": "Morgen schreiben wir eine Klassenarbeit über Bruchrechnung, "
                                                     "Prozentrechnung und Zinsrechnung."})
        self.assertEqual(sent.status_code, 201, sent.get_data(as_text=True))
        body = sent.get_json()
        self.assertNotIn("EXAM", body["reply"]["content"])
        state = body["conversation"]["continue_learning"]
        self.assertEqual(state["format"]["kind"], "lessons")
        self.assertEqual([item["title"] for item in state["format"]["topics"]], THREE)
        self.assertEqual(state["format"]["exam_date"], (application.local_today() + timedelta(days=1)).isoformat())
        self.assertFalse(state["available"], "the plain single-lesson button is not shown beside the card")
        with application.app.app_context():
            stored = json.loads(db.session.scalars(db.select(application.ConversationMessage.study_json).where(
                application.ConversationMessage.role == "assistant")).one())
        self.assertEqual([item["title"] for item in stored["exam_topics"]], THREE)


    def test_the_card_does_not_depend_on_chat_photos(self):
        # Review fix: the page flag also required FEATURE_CHAT_IMAGES, so with photos off the
        # server hid the plain lesson button and the page drew no card in its place.
        saved = application.app.config.get("FEATURE_CHAT_IMAGES")
        application.app.config["FEATURE_CHAT_IMAGES"] = False
        try:
            page = self.client.get("/").get_data(as_text=True)
        finally:
            application.app.config["FEATURE_CHAT_IMAGES"] = saved
        self.assertIn('data-study-format="on"', page)
        self.assertNotIn('id="asstPdf"', page, "the PDF button sits in the photo controls")


class TopicLessonTests(StudyFormatRouteCase):
    def test_three_topics_give_three_separate_lessons_each_scoped_to_its_topic(self):
        percent = reading(("heading", "Prozentrechnung"), ("printed_text", "Grundwert G, Prozentwert W, Prozentsatz p"))
        interest = reading(("heading", "Zinsrechnung"), ("printed_text", "Kapital K und Zinssatz für ein Jahr"))
        conversation_id = self.exam_conversation(THREE, photos=[(percent, png_bytes()), (interest, png_bytes())])
        state = self.state(conversation_id)
        self.assertEqual(state["format"]["kind"], "lessons")
        keys = [item["key"] for item in state["format"]["topics"]]
        with FakeAI(lesson_payload("Prozentrechnung")) as ai:
            first = self.lesson(conversation_id, topic_key=keys[1])
            again = self.lesson(conversation_id, topic_key=keys[1])
        self.assertEqual(first.status_code, 201, first.get_data(as_text=True))
        self.assertEqual(again.status_code, 200, "a double tap opens the same lesson")
        self.assertEqual(len(ai.lesson_calls()), 1)
        call = ai.lesson_calls()[0]
        prompt = call["input"]
        self.assertIn('teach ONLY "Prozentrechnung"', prompt)
        self.assertIn("Bruchrechnung; Zinsrechnung", prompt)
        self.assertIn("SHORT LESSON:", prompt)
        self.assertIn("Grundwert", prompt, "the photo passage about this topic")
        self.assertNotIn("Kapital K", prompt, "the passage about another topic is left out")
        self.assertIn("terms", prompt)
        self.assertEqual(call["prompt_version"], PROMPT_VERSIONS["lesson_generation"] + "+chat2")
        with FakeAI(lesson_payload("Zinsrechnung")) as ai:
            second = self.lesson(conversation_id, topic_key=keys[2])
        self.assertEqual(second.status_code, 201)
        rows = self.lesson_rows(conversation_id)
        self.assertEqual(sorted(row.topic_key for row in rows), sorted(keys[1:]))
        self.assertEqual({row.position for row in rows}, {2, 3})
        topics = self.state(conversation_id)["format"]["topics"]
        self.assertEqual([item["status"] for item in topics], ["none", "ready", "ready"])
        self.assertTrue(topics[1]["url"].startswith("/?session_id="))
        with application.app.app_context():
            session = application.SESSIONS.get(first.get_json()["url"].split("=", 1)[1]) or {}
        self.assertEqual(session.get("session_kind"), "chat_topic_lesson")

    def test_completion_is_lasting_and_shown_on_the_card(self):
        conversation_id = self.exam_conversation(THREE)
        key = self.state(conversation_id)["format"]["topics"][0]["key"]
        with FakeAI():
            built = self.lesson(conversation_id, topic_key=key).get_json()
        self.assertFalse(self.state(conversation_id)["format"]["topics"][0]["completed"])
        session_id = built["url"].split("=", 1)[1]
        with application.app.app_context():
            db.session.add(application.LearningEvent(user_id=self.user_id(), event_type="quiz_completed",
                                                     idempotency_key=f"quiz-complete:{session_id}"))
            db.session.commit()
        self.assertTrue(self.state(conversation_id)["format"]["topics"][0]["completed"])
        page = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("lesson-completed", page)

    def test_an_unknown_topic_is_refused_and_the_plain_lesson_still_works(self):
        conversation_id = self.exam_conversation(THREE)
        refused = self.lesson(conversation_id, topic_key="no-such-topic")
        self.assertEqual((refused.status_code, refused.get_json()["code"]), (422, "topic_unknown"))
        with FakeAI():
            plain = self.lesson(conversation_id)
        self.assertEqual(plain.status_code, 201)
        self.assertEqual([row.topic_key for row in self.lesson_rows(conversation_id)], [""])

    def test_free_plan_says_which_topic_lessons_start_now(self):
        application.app.config.update(SUBSCRIPTION_QUOTAS_ENFORCED=True)
        self.use_free_lessons(1)
        conversation_id = self.exam_conversation(THREE)
        fmt = self.state(conversation_id)["format"]
        self.assertEqual((fmt["lessons_left"], fmt["lesson_limit"], fmt["plan"]), (1, 2, "free"))
        self.assertEqual([item["access"] for item in fmt["topics"]], ["now", "later", "later"])
        with FakeAI():
            self.assertEqual(self.lesson(conversation_id, topic_key=fmt["topics"][0]["key"]).status_code, 201)
            refused = self.lesson(conversation_id, topic_key=fmt["topics"][1]["key"])
        self.assertEqual(refused.status_code, 429)
        self.assertIn("upgrade_url", refused.get_json()["details"])
        self.assertEqual(self.lesson_allowance_used(), 2.0, "every topic lesson is a normal lesson")
        self.make_premium()
        fmt = self.state(conversation_id)["format"]
        self.assertEqual((fmt["lessons_left"], fmt["plan"]), (None, "premium"))
        self.assertEqual({item["access"] for item in fmt["topics"]}, {"now"})


class ProjectTests(StudyFormatRouteCase):
    def test_twelve_topics_make_one_project_with_every_topic_through_the_existing_path(self):
        conversation_id = self.exam_conversation(TWELVE)
        fmt = self.state(conversation_id)["format"]
        self.assertEqual((fmt["kind"], fmt["reasons"][0]), ("project", "many_topics"))
        exam = (application.local_today() + timedelta(days=21)).isoformat()
        created = self.project(conversation_id, exam_date=exam)
        self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
        again = self.project(conversation_id, exam_date=exam)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.get_json()["project_id"], created.get_json()["project_id"])
        projects = self.projects()
        self.assertEqual(len(projects), 1)
        project = projects[0]
        self.assertEqual((project.exam_date.isoformat(), project.subject, project.topics_confirmed),
                         (exam, "Mathematics", False))
        self.assertEqual(json.loads(project.analysis_json)["origin"]["conversation_id"], conversation_id)
        self.assertTrue(created.get_json()["url"].endswith("#topics"))
        with application.app.app_context():
            sections = db.session.scalars(db.select(application.LearningSection).where(
                application.LearningSection.project_id == project.id).order_by(
                application.LearningSection.position)).all()
            self.assertEqual([section.title for section in sections], TWELVE)
            self.assertTrue(all(section.user_edited for section in sections))
            self.assertEqual([section.topic_key for section in sections], [item["key"] for item in fmt["topics"]])
        state = self.state(conversation_id)["format"]["project"]
        self.assertEqual(state["status"], "ready")
        self.assertIn(f"/projects/{project.id}", state["url"])

    def test_chat_photos_go_to_the_project_as_pages_to_be_read_again(self):
        conversation_id = self.exam_conversation(TWELVE, photos=[(reading(("heading", "Pythagoras")), png_bytes()),
                                                                 (reading(("heading", "Prismen")), png_bytes((240, 240, 240)))])
        created = self.project(conversation_id)
        self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
        self.assertIn("/start?auto=1", created.get_json()["url"])
        with application.app.app_context():
            project = fetch(db.session, application.LearningProject, created.get_json()["project_id"])
            self.assertEqual(len(project.pages), 2)
            self.assertTrue(all(page.extraction_status != "ready" for page in project.pages),
                            "read again by Project recognition, never seeded from the chat reading")

    def test_a_used_free_project_is_said_and_nothing_is_made(self):
        application.app.config.update(SUBSCRIPTION_QUOTAS_ENFORCED=True)
        with application.app.app_context():
            application.entitlements.reserve(self.user_id(), "project").commit(reference="seeded")
        conversation_id = self.exam_conversation(TWELVE)
        fmt = self.state(conversation_id)["format"]
        self.assertFalse(fmt["project_allowed"])
        self.assertTrue(fmt["project_resets_at"])
        refused = self.project(conversation_id)
        self.assertEqual(refused.status_code, 429)
        self.assertIn("upgrade_url", refused.get_json()["details"])
        self.assertEqual(self.projects(), [])

    def test_a_chat_project_counts_as_the_weeks_project(self):
        application.app.config.update(SUBSCRIPTION_QUOTAS_ENFORCED=True)
        conversation_id = self.exam_conversation(TWELVE)
        self.assertTrue(self.state(conversation_id)["format"]["project_allowed"])
        self.assertEqual(self.project(conversation_id).status_code, 201)
        with application.app.app_context():
            self.assertEqual(application.entitlements.allowance(self.user_id(), "project").used, 1.0)

    def test_another_account_cannot_use_the_conversation(self):
        conversation_id = self.exam_conversation(TWELVE)
        other = self.register("bob")
        self.assertEqual(self.project(conversation_id, client=other).status_code, 404)
        self.assertEqual(self.projects(), [])

    def test_a_past_exam_date_is_refused(self):
        conversation_id = self.exam_conversation(TWELVE)
        response = self.project(conversation_id, exam_date="2020-01-01")
        self.assertEqual((response.status_code, response.get_json()["code"]), (422, "invalid_exam_date"))

    def test_the_no_date_chip_is_a_choice_and_only_a_missing_one_falls_back_to_the_chat(self):
        # Review fix: "No date yet" ("") used to be replaced by the date the chat named.
        conversation_id = self.exam_conversation(TWELVE)   # "Klassenarbeit nächste Woche: ..."
        self.assertEqual(self.project(conversation_id, exam_date="").status_code, 201)
        self.assertIsNone(self.projects()[0].exam_date)
        other = self.exam_conversation(TWELVE[:11])
        self.assertEqual(self.project(other).status_code, 201)
        dated = [item for item in self.projects() if item.exam_date is not None]
        self.assertEqual([item.exam_date for item in dated], [application.local_today() + timedelta(days=7)])

    def test_a_relative_date_counts_from_the_day_it_was_written(self):
        # Review fix: "Arbeit morgen" written a week ago is over, not tomorrow.
        conversation_id = self.exam_conversation(SIX, message="Deutscharbeit morgen: " + ", ".join(SIX))
        self.assertEqual(self.state(conversation_id)["format"]["days_left"], 1)
        with application.app.app_context():
            db.session.execute(db.update(application.ConversationMessage).where(
                application.ConversationMessage.conversation_id == conversation_id).values(
                created_at=application.utcnow() - timedelta(days=7)))
            db.session.commit()
        fmt = self.state(conversation_id)["format"]
        self.assertEqual((fmt["exam_date"], fmt["days_left"]), (None, None))

    def test_a_borderline_exam_is_a_choice_with_one_recommendation(self):
        conversation_id = self.exam_conversation(SIX, message="Deutscharbeit in 2 Wochen: " + ", ".join(SIX))
        fmt = self.state(conversation_id)["format"]
        self.assertEqual((fmt["kind"], fmt["recommended"]), ("choice", "project"))
        soon = self.exam_conversation(SIX[:5], message="Test morgen: " + ", ".join(SIX[:5]))
        fmt = self.state(soon)["format"]
        self.assertEqual((fmt["kind"], fmt["recommended"]), ("choice", "lessons"))


class PdfHandoffTests(StudyFormatRouteCase):
    def test_a_pdf_becomes_a_project_instead_of_a_dead_end(self):
        import pymupdf

        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "Kapitel 1: Lineare Funktionen")
        pdf = document.tobytes()
        document.close()
        conversation_id = self.client.post("/api/assistant/conversations", json={}).get_json()["conversation"]["id"]

        def send():
            return self.client.post(f"/api/assistant/conversations/{conversation_id}/project",
                                    data={"materials": (io.BytesIO(pdf), "Mathe-Arbeit.pdf"), "exam_date": ""},
                                    content_type="multipart/form-data")

        created = send()
        self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
        self.assertIn("/start?auto=1", created.get_json()["url"])
        self.assertEqual(send().get_json()["project_id"], created.get_json()["project_id"], "one project per file")
        projects = self.projects()
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].title, "Mathe-Arbeit")
        # The chat's photo intake stays photo-only.
        refused = self.client.post("/api/chat-attachments", data={"file": (io.BytesIO(pdf), "x.pdf")},
                                   content_type="multipart/form-data")
        self.assertEqual(refused.get_json()["code"], "pdf_not_image")


class SharedCompetencyTests(StudyFormatRouteCase):
    def test_a_project_section_sees_mastery_shown_in_the_chat(self):
        with application.app.app_context():
            user_id = self.user_id()
            db.session.add(application.ConceptMastery(user_id=user_id, subject="Mathematics",
                                                      concept="Prozentrechnung", mastery_score=85, attempts=4))
            project = application.LearningProject(user_id=user_id, title="Arbeit", subject="Mathematics")
            db.session.add(project)
            db.session.flush()
            section = application.LearningSection(project_id=project.id, position=1, title="Prozentrechnung",
                                                  main_topic="Prozentrechnung", topic_key="prozentrechnung")
            fresh = application.LearningSection(project_id=project.id, position=2, title="Zylinder",
                                                main_topic="Zylinder", topic_key="zylinder")
            db.session.add_all([section, fresh])
            db.session.commit()
            user = db.session.get(application.User, user_id)
            with application.app.test_request_context():
                application.login_user(user)
                self.assertEqual(application.section_knowledge_state(section), "secure")
                self.assertEqual(application.section_knowledge_state(fresh), "new")


# ------------------------------------------------------------------------ /api/analyze
class ShortLessonAnalyzeTests(ContinueLearningTestCase):
    def analyze(self, goal, subject="Mathematics"):
        with FakeAI(lesson_payload("Prozentrechnung", explanation="")) as ai:
            response = self.client.post("/api/analyze", data={"study_goal": goal, "subject": subject})
        return response, ai.lesson_calls()

    def test_the_short_lesson_runs_on_the_teaching_engine(self):
        response, calls = self.analyze("Prozentrechnung verstehen")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        prompt = calls[0]["input"][0]["content"][0]["text"]
        self.assertIn("TEACHING METHOD", prompt)
        self.assertIn("SHORT LESSON:", prompt)
        self.assertIn('"parts":[', prompt)
        self.assertIn("There are no notes", prompt, "no photo: the no-notes rule")
        self.assertIn('"image_search_terms"', prompt, "the media keys are still requested")
        for kind in ("objective", "terms", "mistakes", "guided_exercise", "summary"):
            self.assertRegex(prompt, rf"\d+\. {kind} — ", kind)
        self.assertEqual(calls[0]["prompt_version"], PROMPT_VERSIONS["lesson_generation"] + "+short1")
        lesson = response.get_json()["lesson"]
        self.assertEqual(lesson["parts"][0]["kind"], "objective")
        self.assertTrue(all(part["from_notes"] is False for part in lesson["parts"]))
        self.assertTrue(lesson["explanation"], "rebuilt from the parts")
        state = application.SESSIONS[response.get_json()["session_id"]]
        self.assertEqual((state["strategy_key"], state["teaching_first"], state["session_kind"]),
                         ("mathematics", True, "short_lesson"))
        self.assertIsNone(response.get_json()["format_hint"])

    def test_a_whole_exam_in_the_goal_gets_the_project_hint(self):
        response, _calls = self.analyze("Klassenarbeit: " + ", ".join(TWELVE))
        hint = response.get_json()["format_hint"]
        self.assertEqual((hint["kind"], hint["topics"]), ("project", 12))
        self.assertTrue(hint["url"].startswith("/projects"))


# ------------------------------------------------------------------------ migration 044
class MigrationTests(unittest.TestCase):
    """044_chat_study_format on a database from before it: the row kept, the key widened."""

    def test_old_rows_are_kept_and_a_second_topic_row_is_allowed(self):
        from tests import test_assistant_conversations as conversations_tests

        Legacy = conversations_tests.MigrationTests   # its DDL helpers (imported as a module: not collected twice)

        original = dict(application.app.config)
        application.app.config.update(TESTING=True)
        drop_lesson = ("topic_key", "position", "kind", "project_id")
        with application.app.app_context():
            db.drop_all()
            try:
                # The table as 040 made it: no 044 columns, the old two-column unique key.
                legacy = Legacy.legacy_ddl("conversation_lesson", drop_lesson)
                self.assertNotIn("topic_key", legacy)
                legacy = re.sub(r"\n\)\s*$", ",\n\tCONSTRAINT uq_conversation_lesson_source "
                                "UNIQUE (conversation_id, source_message_id)\n)", legacy)
                self.assertIn("UNIQUE (conversation_id, source_message_id)", legacy)
                with db.engine.begin() as connection:
                    connection.execute(application.text(Legacy.legacy_ddl("user", ())))
                    connection.execute(application.text(Legacy.legacy_ddl("conversation", ())))
                    connection.execute(application.text(Legacy.legacy_ddl("conversation_message", ("study_json",))))
                    connection.execute(application.text(legacy))
                    Legacy.legacy_insert(connection, "user", (), id=1, username="old", email="old@example.com",
                                         password_hash="x", preferred_language="de")
                    Legacy.legacy_insert(connection, "conversation", (), id="c-1", user_id=1, title="Alt")
                    Legacy.legacy_insert(connection, "conversation_message", ("study_json",), id=7,
                                         conversation_id="c-1", role="assistant", content="Antwort")
                    Legacy.legacy_insert(connection, "conversation_lesson", drop_lesson, id=3, user_id=1,
                                         conversation_id="c-1", source_message_id=7, status="ready",
                                         session_id="s" * 32, title="Alte Lektion")
                db.session.remove()
                application.ensure_database()
                row = db.session.get(application.ConversationLesson, 3)
                self.assertEqual((row.title, row.topic_key, row.kind, row.position, row.project_id),
                                 ("Alte Lektion", "", "lesson", 0, None))
                self.assertEqual(db.session.get(application.ConversationMessage, 7).study_json, "{}")
                db.session.add(application.ConversationLesson(user_id=1, conversation_id="c-1", source_message_id=7,
                                                              topic_key="bruch", status="pending"))
                db.session.commit()
                db.session.add(application.ConversationLesson(user_id=1, conversation_id="c-1", source_message_id=7,
                                                              topic_key="bruch", status="pending"))
                with self.assertRaises(application.IntegrityError):
                    db.session.commit()
                db.session.rollback()
                db.session.remove()
                application.ensure_database()          # idempotent
                self.assertEqual(db.session.scalar(db.select(db.func.count(application.ConversationLesson.id))), 2)
                self.assertEqual(db.session.scalar(db.select(db.func.count(application.SchemaMigration.version)).where(
                    application.SchemaMigration.version == "044_chat_study_format")), 1)
                calls = re.findall(r'apply_schema_migration\(\s*"044_chat_study_format"',
                                   (ROOT / "app.py").read_text(encoding="utf-8"))
                self.assertEqual(len(calls), 2, "the add-it and the already-there branch")
            finally:
                db.session.remove()
                db.drop_all()
                db.create_all()
                application.app.config.clear()
                application.app.config.update(original)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
