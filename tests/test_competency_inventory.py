"""Kompetenzraster recognition end to end (spec Phase 1.1), every model call patched.

A grid page goes through the real topic analysis and the competency step that follows
it: every "Ich kann" row becomes its own Competency row, grounded on its page, linked to
its file and page, attached to one topic without ever being merged with a similar one,
and tracked by a derived status. What is pinned here:

* (a) a grid with three distinct topics plus a combined header row keeps all three as
  separate topics and competencies, in every arrival order;
* (b) a 15-competency grid gives 15 verified competencies, all in a topic;
* (c) a statement the page does not say is never stored;
* (d) a page with 15 rows answered with 12 is a gap the workspace shows, with one-tap
  "Add the missing rows" and "Read again" (only that page is asked again);
* (e) the project is complete only when every competency is practised or mastered;
* a provider stop parks the page as waiting (rows from the parser), never failed;
* deleting or merging topics keeps the links consistent; a file can be marked as the
  Kompetenzraster with one tap, or added to an existing project; notes alone make no call;
* migration 043 adds the columns to an old competency table once.
"""

import io
import itertools
import json
import re
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import pymupdf  # pyright: ignore[reportMissingImports]
from sqlalchemy import inspect, text

import app as application
from learnova.ai_services import service as ai_service
from tests.config_isolation import keep_config

db = application.db
PASSWORD = "correct-horse-battery"

THREE = [
    ("Lineare Funktionen", ["Ich kann die Steigung einer linearen Funktion bestimmen.",
                            "Ich kann den Graphen einer linearen Funktion zeichnen."]),
    ("Quadratische Funktionen", ["Ich kann den Scheitelpunkt einer quadratischen Funktion bestimmen.",
                                 "Ich kann eine Normalparabel verschieben."]),
    ("Lineare Gleichungssysteme", ["Ich kann ein lineares Gleichungssystem mit dem Einsetzungsverfahren lösen.",
                                   "Ich kann ein lineares Gleichungssystem grafisch lösen."]),
]
HEADER = "Lineare und quadratische Funktionen"
THREE_PAGE = (f"Kompetenzraster Mathematik Klasse 9\n{HEADER}\n" + "\n".join(
    f"{heading}\n" + "\n".join(f"☐ {row}" for row in rows) for heading, rows in THREE))

FIFTEEN = [
    ("Lineare Funktionen", ["Ich kann die Steigung einer linearen Funktion bestimmen.",
                            "Ich kann den Graphen einer linearen Funktion zeichnen.",
                            "Ich kann die Funktionsgleichung einer Geraden aufstellen."]),
    ("Quadratische Funktionen", ["Ich kann den Scheitelpunkt einer quadratischen Funktion bestimmen.",
                                 "Ich kann eine Normalparabel verschieben.",
                                 "Ich kann die Scheitelpunktform einer Parabel in die Normalform umwandeln."]),
    ("Lineare Gleichungssysteme", ["Ich kann ein lineares Gleichungssystem mit dem Einsetzungsverfahren lösen.",
                                   "Ich kann ein lineares Gleichungssystem grafisch lösen.",
                                   "Ich kann ein lineares Gleichungssystem mit dem Additionsverfahren lösen."]),
    ("Satz des Pythagoras", ["Ich kann mit dem Satz des Pythagoras fehlende Seitenlängen berechnen.",
                             "Ich kann beim Satz des Pythagoras die Hypotenuse erkennen.",
                             "Ich kann den Satz des Pythagoras im Raum anwenden."]),
    ("Wahrscheinlichkeitsrechnung", ["Ich kann Laplace-Wahrscheinlichkeiten berechnen.",
                                     "Ich kann ein Baumdiagramm für eine Wahrscheinlichkeit zeichnen.",
                                     "Ich kann die Pfadregeln der Wahrscheinlichkeitsrechnung anwenden."]),
]
FIFTEEN_PAGE = "Kompetenzraster Mathematik Klasse 9\n" + "\n".join(
    f"{heading}\n" + "\n".join(f"☐ {row}" for row in rows) for heading, rows in FIFTEEN)
NOTES_PAGE = ("Lineare Funktionen\nEine lineare Funktion hat die Form f(x) = mx + b. Die Steigung m sagt, wie steil "
              "die Gerade ist.")


class FakeResponse:
    usage = None
    model = "test-model"

    def __init__(self, payload):
        self.output_text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)


def topic(title, page_id, goals=()):
    return {"title": title, "main_topic": "Mathematik", "kind": "main", "summary": f"{title} in der Klasse 9.",
            "learning_goals": list(goals), "source_page_ids": [page_id], "terms": [], "items": [],
            "exam_questions": [f"Erkläre {title}."], "related_topics": [], "prerequisites": [],
            "difficulty": "medium", "importance": "medium", "estimated_minutes": 15}


def page_of(prompt, marker):
    match = re.search(marker, prompt, flags=re.DOTALL)
    assert match is not None
    return json.loads(match.group(1))


class InventoryTestCase(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, application.limiter, "enabled", application.limiter.enabled)
        keep_config(self, application.app.config, "AI_MODE", "CURRICULUM_CLEARED_SOURCES",
                    "SUBSCRIPTION_QUOTAS_ENFORCED", "RATELIMIT_ENABLED", "FEATURE_PRIVATE_FLASHCARDS")
        application.app.config.update(TESTING=True, AI_MODE="cached", CURRICULUM_CLEARED_SOURCES="",
                                      SUBSCRIPTION_QUOTAS_ENFORCED=False, RATELIMIT_ENABLED=False,
                                      FEATURE_PRIVATE_FLASHCARDS=False)
        application.limiter.enabled = False
        application.SESSIONS.clear()
        application._curriculum_index_cache.update(key=None, index=None)
        ai_service._RECENT_RESULTS.clear()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={"username": "mia", "email": "mia@example.com", "password": PASSWORD,
                                            "grade": "9"})
        self.calls: list[str] = []
        self.competency_prompts: list[str] = []
        self.topic_titles: list[str] | None = None     # the order the topic model answers in
        self.grid_rows: dict[str, list[tuple[str, str]]] | None = None   # page text marker -> rows
        self.extra_rows: list[dict] = []
        self.competency_error: Exception | None = None

    # ------------------------------------------------------------ the fake model
    def respond(self, *, task_type, **kwargs):
        self.calls.append(task_type)
        prompt = str(kwargs.get("input", ""))
        if task_type == "project_section_generation":
            batch = page_of(prompt, r"\nPages: (\[.*\])\nReturn JSON exactly as ")
            topics = []
            for entry in batch:
                if "Kompetenzraster" in entry["text"]:
                    titles = self.topic_titles or [heading for heading, _rows in self.rows_for(entry["text"])]
                    topics += [topic(title, entry["page_id"]) for title in titles]
                elif "lineare Funktion" in entry["text"]:
                    topics.append(topic("Lineare Funktionen", entry["page_id"]))
            return FakeResponse({"sections": topics})
        if task_type == "competency_extraction":
            self.competency_prompts.append(prompt)
            if self.competency_error is not None:
                raise self.competency_error
            entry = page_of(prompt, r"\nPage: (\{.*\})\nReturn JSON exactly as ")
            rows = self.rows_for(entry["text"])
            return FakeResponse({"competencies": [
                {"statement": row, "group_path": ["Kompetenzraster", heading], "level": "basic", "importance": 2,
                 "source_page_ids": [entry["page_id"]], "section_title": heading}
                for heading, items in rows for row in items] + self.extra_rows})
        if task_type in ("lesson_generation", "adaptive_practice"):
            return FakeResponse({"lesson_title": "Thema", "detected_level": "Klasse 9",
                                 "concepts": [{"name": "Thema", "evidence": "notes"}], "explanation": "Erklärung.",
                                 "worked_example": {"problem": "B", "steps": ["S"], "answer": "A"},
                                 "teacher_tips": [], "exceptions": [],
                                 "question": {"id": "q1", "concept": "Thema", "difficulty": 1,
                                              "type": "multiple_choice", "prompt": "Frage?", "hint": "Tipp",
                                              "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
                                              "expected_answer": "a"}})
        raise AssertionError(f"unexpected AI task {task_type}")

    def rows_for(self, page_text):
        source = self.grid_rows if self.grid_rows is not None else {"Satz des Pythagoras": FIFTEEN, "": THREE}
        for marker, rows in source.items():
            if marker in page_text:
                return rows
        return []

    def post(self, path, **kwargs):
        with patch.object(application, "create_response", side_effect=self.respond):
            return self.client.post(path, **kwargs)

    def get(self, path):
        with patch.object(application, "create_response", side_effect=self.respond):
            return self.client.get(path)

    # ------------------------------------------------------------ material
    def user_id(self):
        return db.session.scalar(db.select(application.User.id).where(application.User.username == "mia"))

    def make_project(self, *, grid_text=THREE_PAGE, notes=True, grid_kind="grid"):
        with application.app.app_context():
            project = application.LearningProject(user_id=self.user_id(), title="Mathe", subject="Mathematics",
                                                  status="uploaded")
            db.session.add(project)
            db.session.flush()
            order = 0
            ids = {}
            for name, kind, content in ((("notizen.pdf", "upload", NOTES_PAGE),) if notes else ()) + (
                    ("raster.pdf", grid_kind, grid_text),):
                file = application.ProjectFile(project_id=project.id, original_filename=name,
                                               mime_type="application/pdf", original_data=b"%PDF", sha256=name,
                                               source_kind=kind)
                db.session.add(file)
                db.session.flush()
                order += 1
                page = application.ProjectPage(
                    project_id=project.id, file_id=file.id, page_number=1, page_order=order, extracted_text=content,
                    extraction_status="ready", confidence_status="high", recognition_confidence=0.95,
                    review_status="pending", recognition_json=json.dumps({"text_source": "text_layer"}),
                    teacher_highlighted=kind == "grid", important=kind == "grid")
                db.session.add(page)
                db.session.flush()
                ids[name] = (file.id, page.id)
            db.session.commit()
            return project.id, ids

    def project(self, project_id):
        project = db.session.get(application.LearningProject, project_id)
        assert project is not None
        return project

    def quick_start(self, project_id):
        response = self.post(f"/projects/{project_id}/quick-start")
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])
        return response

    def inventory(self, project_id):
        with application.app.app_context():
            project = self.project(project_id)
            titles = {section.id: section.title for section in project.sections}
            return [{"id": row.id, "statement": row.statement, "topic": titles.get(row.section_id),
                     "verified": row.verified, "pages": json.loads(row.source_page_ids_json),
                     "file": row.source_file_id, "group": row.group_path, "state": row.state}
                    for row in sorted(project.competencies, key=lambda item: (item.position, item.id))]

    def analysis(self, project_id):
        with application.app.app_context():
            return json.loads(self.project(project_id).analysis_json)


class ExtractionAcceptanceTests(InventoryTestCase):
    def test_a_three_distinct_topics_with_a_combined_header_in_every_arrival_order(self):
        titles = [HEADER] + [heading for heading, _rows in THREE]
        for order in itertools.permutations(titles):
            with self.subTest(order=order):
                self.setUp()
                self.topic_titles = list(order)
                project_id, ids = self.make_project(notes=False)
                self.quick_start(project_id)
                with application.app.app_context():
                    sections = {section.title for section in self.project(project_id).sections}
                self.assertLessEqual({heading for heading, _rows in THREE}, sections,
                                     "no topic is silently absorbed by the header row")
                rows = self.inventory(project_id)
                self.assertEqual(len(rows), 6)
                expected = {row: heading for heading, items in THREE for row in items}
                self.assertEqual({row["statement"]: row["topic"] for row in rows}, expected,
                                 "each competency sits in its own topic, never the combined one")
                self.assertTrue(all(row["verified"] and row["pages"] == [ids["raster.pdf"][1]]
                                    and row["file"] == ids["raster.pdf"][0] for row in rows))
                self.assertEqual(self.calls.count("competency_extraction"), 1, "one call per grid page")

    def test_a_coverage_is_tracked_per_competency(self):
        project_id, _ids = self.make_project(notes=False)
        self.quick_start(project_id)
        with application.app.app_context():
            project = self.project(project_id)
            quadratic = next(section for section in project.sections if section.title == "Quadratische Funktionen")
            linear = next(section for section in project.sections if section.title == "Lineare Funktionen")
            parts = [{"kind": "explanation", "body": "Eine quadratische Funktion hat einen Scheitelpunkt. Wir "
                      "bestimmen den Scheitelpunkt und verschieben die Normalparabel."}]
            taught = application.record_competencies_taught(quadratic, parts, lesson_id=41)
            self.assertEqual(len(taught), 2)
            self.assertEqual(application.record_competencies_taught(linear, parts, lesson_id=41), [],
                             "explaining quadratic functions does not cover the linear ones")
            application.record_competencies_taught(quadratic, parts, lesson_id=41)
            db.session.commit()
            counts = {row.statement: row.taught_count for row in project.competencies}
        self.assertEqual(sum(counts.values()), 2, "each lesson counts once")
        self.assertEqual(counts["Ich kann den Scheitelpunkt einer quadratischen Funktion bestimmen."], 1)

    def test_b_fifteen_competencies_are_all_verified_and_in_a_topic(self):
        project_id, _ids = self.make_project(grid_text=FIFTEEN_PAGE, notes=False)
        self.grid_rows = {"": FIFTEEN}
        self.quick_start(project_id)
        rows = self.inventory(project_id)
        self.assertEqual(len(rows), 15)
        self.assertTrue(all(row["verified"] for row in rows))
        self.assertTrue(all(row["topic"] for row in rows), [row for row in rows if not row["topic"]])
        self.assertEqual({row["topic"] for row in rows}, {heading for heading, _rows in FIFTEEN})
        self.assertEqual(rows[0]["group"], "Kompetenzraster > Lineare Funktionen")
        self.assertEqual(self.analysis(project_id)["competency_pages"][str(_ids["raster.pdf"][1])]["expected"], 15)

    def test_c_an_invented_statement_is_not_stored(self):
        project_id, _ids = self.make_project(notes=False)
        self.extra_rows = [{"statement": "Ich kann Integrale mit der Substitutionsregel berechnen.",
                            "group_path": ["Analysis"], "section_title": "Lineare Funktionen"}]
        self.quick_start(project_id)
        statements = [row["statement"] for row in self.inventory(project_id)]
        self.assertEqual(len(statements), 6)
        self.assertNotIn("Ich kann Integrale mit der Substitutionsregel berechnen.", statements)
        page = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        self.assertIn("1 row(s) the AI read are not on the page", page)

    def test_d_twelve_of_fifteen_rows_is_a_gap_with_one_tap_fixes(self):
        project_id, ids = self.make_project(grid_text=FIFTEEN_PAGE, notes=False)
        self.grid_rows = {"": FIFTEEN[:4]}       # the model leaves out the last heading's three rows
        self.quick_start(project_id)
        self.assertEqual(len(self.inventory(project_id)), 12)
        page = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        self.assertIn("raster.pdf p. 1 lists 15 “Ich kann” rows, Learnova found 12.", page)
        # Read again: only this page is asked again, with its cached answer dropped.
        self.grid_rows = {"": FIFTEEN}
        before = len(self.competency_prompts)
        self.post(f"/projects/{project_id}/competencies/pages/{ids['raster.pdf'][1]}/reread")
        self.assertEqual(len(self.competency_prompts), before + 1)
        self.assertIn(f'"page_id": {ids["raster.pdf"][1]}', self.competency_prompts[-1])
        self.assertEqual(len(self.inventory(project_id)), 15)
        self.assertNotIn("lists 15 “Ich kann” rows", self.client.get(f"/projects/{project_id}").get_data(as_text=True))

    def test_d_add_the_missing_rows_closes_the_gap_without_another_call(self):
        project_id, ids = self.make_project(grid_text=FIFTEEN_PAGE, notes=False)
        self.grid_rows = {"": FIFTEEN[:4]}
        self.quick_start(project_id)
        calls = len(self.calls)
        self.post(f"/projects/{project_id}/competencies/pages/{ids['raster.pdf'][1]}/add-missing")
        self.assertEqual(len(self.calls), calls)
        rows = self.inventory(project_id)
        self.assertEqual(len(rows), 15)
        self.assertEqual({row["topic"] for row in rows if "Wahrscheinlich" in row["statement"]
                          or "Baumdiagramm" in row["statement"] or "Pfadregeln" in row["statement"]},
                         {None}, "no topic for them yet: shown with “Make it a topic”")
        page = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        self.assertNotIn("lists 15 “Ich kann” rows", page)
        self.assertIn("Make it a topic", page)
        loose = next(row for row in rows if row["topic"] is None)
        self.post(f"/projects/{project_id}/competencies/{loose['id']}/topic")
        self.assertTrue(next(row for row in self.inventory(project_id) if row["id"] == loose["id"])["topic"])

    def test_e_complete_only_when_every_competency_is_practised(self):
        project_id, _ids = self.make_project(notes=False)
        self.quick_start(project_id)
        self.post(f"/projects/{project_id}/topics/confirm", data={"choice": "auto"})
        with application.app.test_request_context():
            project = self.project(project_id)
            self.assertTrue(all(row.state == "confirmed" for row in project.competencies), "Start learning confirms")
            context = application.project_workspace_context(project)
            check = context["coverage"]["checks"][-1]
            self.assertEqual((check["code"], check["ok"], check["done"], check["total"]),
                             ("competencies_addressed", False, 0, 6))
            for row in project.competencies[:5]:
                row.answers, row.correct_answers = 1, 1
            db.session.commit()
            check = application.project_workspace_context(project)["coverage"]["checks"][-1]
            self.assertEqual((check["ok"], check["done"]), (False, 5))
            project.competencies[5].state = "removed"       # "Not in this exam", one tap
            db.session.commit()
            check = application.project_workspace_context(project)["coverage"]["checks"][-1]
            self.assertEqual((check["ok"], check["done"], check["total"]), (True, 5, 5))


class RobustnessTests(InventoryTestCase):
    def test_a_rate_limit_parks_the_page_as_waiting_with_the_parsers_rows(self):
        project_id, ids = self.make_project(notes=False)
        self.competency_error = ai_service.AIProviderError("provider_rate_limit", "rate limited")
        self.quick_start(project_id)
        record = self.analysis(project_id)["competency_pages"][str(ids["raster.pdf"][1])]
        self.assertEqual((record["state"], record["source"]), ("waiting", "parser"))
        self.assertEqual(len(self.inventory(project_id)), 6, "the rows are there, as the page writes them")
        with application.app.app_context():
            self.assertTrue(self.project(project_id).sections, "the topics are kept")
            self.assertFalse(application.competency_work_pending(self.project(project_id)),
                             "not knocked on again right away")
        page = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        self.assertIn("the AI is busy", page)

    def test_a_broken_answer_falls_back_to_the_rows_on_the_page(self):
        project_id, ids = self.make_project(notes=False)
        self.competency_error = ai_service.AIValidationError("schema_validation", "broken")
        self.quick_start(project_id)
        record = self.analysis(project_id)["competency_pages"][str(ids["raster.pdf"][1])]
        self.assertEqual(record["state"], "fallback")
        self.assertEqual(len(self.inventory(project_id)), 6)

    def test_notes_alone_make_no_competency_call(self):
        project_id, _ids = self.make_project(grid_kind="upload")
        self.quick_start(project_id)
        self.assertNotIn("competency_extraction", self.calls)
        self.assertEqual(self.inventory(project_id), [])
        self.assertNotIn('id="competencies"', self.client.get(f"/projects/{project_id}").get_data(as_text=True))

    def test_the_analysis_step_runs_the_competency_step_after_the_topics(self):
        keep_config(self, application.app.config, "ANALYSIS_STEP_CALLS")
        application.app.config["ANALYSIS_STEP_CALLS"] = 1
        project_id, _ids = self.make_project()
        replies = []
        for _ in range(6):
            reply = self.post(f"/projects/{project_id}/analysis/step").get_json()
            replies.append(reply)
            if reply.get("finished"):
                break
        self.assertTrue(replies[-1]["finished"])
        self.assertTrue(all(reply["calls"] <= 1 for reply in replies), "within the step budget")
        self.assertEqual(self.calls.count("competency_extraction"), 1)
        self.assertEqual(len(self.inventory(project_id)), 6)

    def test_deleting_and_merging_topics_keeps_the_links(self):
        project_id, _ids = self.make_project(notes=False)
        self.quick_start(project_id)
        with application.app.app_context():
            project = self.project(project_id)
            linear = next(section for section in project.sections if section.title == "Lineare Funktionen")
            quadratic = next(section for section in project.sections if section.title == "Quadratische Funktionen")
            linear_id, quadratic_id = linear.id, quadratic.id
            db.session.delete(linear)
            db.session.commit()
            orphans = [row for row in self.project(project_id).competencies if row.section_id is None]
            self.assertEqual(len(orphans), 2, "a deleted topic's competencies stay, without a topic")
        self.post(f"/projects/{project_id}/sections/merge", data={"section_ids": [str(quadratic_id), str(
            next(row["id"] for row in self.sections(project_id) if row["title"] == "Lineare Gleichungssysteme"))]})
        with application.app.app_context():
            project = self.project(project_id)
            remaining = {section.id for section in project.sections}
            self.assertTrue(all(row.section_id in remaining or row.section_id is None for row in project.competencies))
            self.assertEqual(len(project.competencies), 6, "merging topics never merges competencies")
            self.assertNotIn(linear_id, remaining)
            moved = {row.section_id for row in project.competencies
                     if "quadratischen" in row.statement or "Normalparabel" in row.statement
                     or "Gleichungssystem" in row.statement}
            self.assertEqual(len(moved), 1, "the merged topic holds both topics' competencies")
            self.assertEqual(len(remaining), 1, "three topics, one deleted, two merged")

    def test_not_in_this_exam_on_the_topic_review_never_starts_learning(self):
        # The topic review lists each topic's competencies inside the "Start learning" form.
        # A nested <form> is dropped by the HTML parser, so its button would submit the
        # outer form and confirm the topics: the button must carry its own formaction.
        from html.parser import HTMLParser

        project_id, _ids = self.make_project(notes=False)
        self.quick_start(project_id)
        html = self.client.get(f"/projects/{project_id}").get_data(as_text=True)

        class Forms(HTMLParser):
            def __init__(self):
                super().__init__()
                self.depth, self.nested, self.formactions = 0, 0, []

            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if tag == "form":
                    self.nested += int(self.depth > 0)
                    self.depth += 1
                elif tag == "button" and self.depth and str(values.get("formaction") or "").endswith("/exam"):
                    self.formactions.append(str(values["formaction"]))

            def handle_endtag(self, tag):
                if tag == "form":
                    self.depth = max(0, self.depth - 1)

        forms = Forms()
        forms.feed(html)
        self.assertEqual(forms.nested, 0, "no form inside another form")
        self.assertEqual(len(forms.formactions), 6, "each competency's tap posts to its own route")
        # What the browser sends: the outer form's fields, to the button's formaction.
        self.post(forms.formactions[0], data={"choice": "manual",
                                              "topic_ids": [str(row["id"]) for row in self.sections(project_id)]})
        with application.app.app_context():
            project = self.project(project_id)
            self.assertFalse(project.topics_confirmed, "the topics still wait for “Start learning”")
            self.assertEqual(sum(1 for row in project.competencies if row.state == "removed"), 1)

    def test_a_grid_page_not_read_yet_holds_the_check_open(self):
        project_id, ids = self.make_project(notes=False)
        self.quick_start(project_id)
        self.post(f"/projects/{project_id}/topics/confirm", data={"choice": "auto"})
        with application.app.test_request_context():
            project = self.project(project_id)
            for row in project.competencies:
                row.answers, row.correct_answers = 1, 1
            db.session.commit()
            check = application.project_workspace_context(project)["coverage"]["checks"][-1]
            self.assertEqual((check["code"], check["ok"]), ("competencies_addressed", True))
            # The page changes (a corrected reading adds a row): not read for this text yet.
            page = db.session.get(application.ProjectPage, ids["raster.pdf"][1])
            page.extracted_text += "\n☐ Ich kann eine Wertetabelle einer linearen Funktion anlegen."
            db.session.commit()
            self.assertTrue(application.competency_work_pending(project))
            check = application.project_workspace_context(project)["coverage"]["checks"][-1]
            self.assertFalse(check["ok"], "rows not read yet are never counted as covered")
        html = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        self.assertIn("raster.pdf p. 1 is not read yet", html)

    def test_marking_a_long_file_reads_within_the_step_budget(self):
        keep_config(self, application.app.config, "ANALYSIS_STEP_CALLS")
        application.app.config["ANALYSIS_STEP_CALLS"] = 1
        project_id, ids = self.make_project(grid_kind="upload")
        with application.app.app_context():
            db.session.add(application.ProjectPage(
                project_id=project_id, file_id=ids["raster.pdf"][0], page_number=2, page_order=3,
                extracted_text=FIFTEEN_PAGE, extraction_status="ready", confidence_status="high",
                recognition_confidence=0.95, review_status="pending",
                recognition_json=json.dumps({"text_source": "text_layer"})))
            db.session.commit()
        self.quick_start(project_id)
        self.post(f"/projects/{project_id}/files/{ids['raster.pdf'][0]}/grid")
        self.assertEqual(self.calls.count("competency_extraction"), 1, "one request, one step's budget")
        html = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        self.assertIn("p. 2 is not read yet", html)
        self.quick_start(project_id)     # the next quick start reads the rest
        self.assertEqual(self.calls.count("competency_extraction"), 2)
        self.assertNotIn("is not read yet", self.client.get(f"/projects/{project_id}").get_data(as_text=True))

    def test_deleting_a_project_with_an_inventory_leaves_nothing_behind(self):
        project_id, _ids = self.make_project()
        self.quick_start(project_id)
        with application.app.app_context():
            db.session.delete(self.project(project_id))
            db.session.commit()
            self.assertEqual(db.session.scalar(db.select(db.func.count(application.Competency.id))), 0)

    def sections(self, project_id):
        with application.app.app_context():
            return [{"id": section.id, "title": section.title} for section in self.project(project_id).sections]

    def test_one_tap_marks_an_existing_file_as_the_kompetenzraster(self):
        project_id, ids = self.make_project(grid_kind="upload")
        self.quick_start(project_id)
        self.assertNotIn("competency_extraction", self.calls)
        page = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        self.assertIn("This file is my Kompetenzraster", page)
        self.post(f"/projects/{project_id}/files/{ids['raster.pdf'][0]}/grid")
        self.assertEqual(self.calls.count("competency_extraction"), 1)
        self.assertEqual(len(self.inventory(project_id)), 6)
        with application.app.app_context():
            stored = db.session.get(application.ProjectFile, ids["raster.pdf"][0])
            self.assertEqual(stored.source_kind, "grid")
            self.assertTrue(all(page.teacher_highlighted for page in stored.pages))
        self.post(f"/projects/{project_id}/files/{ids['raster.pdf'][0]}/grid")
        self.assertEqual(self.inventory(project_id), [], "unmarking removes the rows read from it")

    def test_a_kompetenzraster_can_be_added_to_an_existing_project(self):
        project_id, _ids = self.make_project(grid_kind="upload")
        document = pymupdf.open()
        document.new_page().insert_textbox(pymupdf.Rect(40, 40, 560, 800), FIFTEEN_PAGE, fontsize=10)  # pyright: ignore
        data = document.tobytes()
        document.close()
        response = self.post(f"/projects/{project_id}/pages/add", data={
            "competency_grid": [(io.BytesIO(data), "raster-neu.pdf", "application/pdf")]},
            content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        with application.app.app_context():
            stored = next(file for file in self.project(project_id).files if file.original_filename == "raster-neu.pdf")
            self.assertEqual(stored.source_kind, "grid")
            self.assertTrue(all(page.teacher_highlighted for page in stored.pages))

    def test_adding_a_kompetenzraster_that_is_already_there_as_notes_marks_that_file(self):
        # The common case on an existing project: the grid was uploaded as notes before.
        # The duplicate is skipped as always; that file becomes the Kompetenzraster.
        import hashlib

        project_id, ids = self.make_project(grid_kind="upload")
        self.quick_start(project_id)
        data = b"%PDF-1.4 the same Kompetenzraster"
        with application.app.app_context():
            db.session.get(application.ProjectFile, ids["raster.pdf"][0]).sha256 = hashlib.sha256(data).hexdigest()
            db.session.commit()
        response = self.post(f"/projects/{project_id}/pages/add", data={
            "competency_grid": [(io.BytesIO(data), "raster.pdf", "application/pdf")]},
            content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("#competencies"), response.headers["Location"])
        with application.app.app_context():
            project = self.project(project_id)
            self.assertEqual(len(project.files), 2, "no second copy")
            stored = db.session.get(application.ProjectFile, ids["raster.pdf"][0])
            self.assertEqual(stored.source_kind, "grid")
        self.assertEqual(self.calls.count("competency_extraction"), 1)
        self.assertEqual(len(self.inventory(project_id)), 6)

    def test_the_create_form_marks_the_grid_and_keeps_commas_in_typed_topics(self):
        document = pymupdf.open()
        document.new_page().insert_textbox(pymupdf.Rect(40, 40, 560, 800), THREE_PAGE, fontsize=10)  # pyright: ignore
        data = document.tobytes()
        document.close()
        typed = "\n".join(["Ableitung (Summen-, Produktregel)"] + [f"Thema {number}" for number in range(44)])
        response = self.post("/projects", data={
            "subject": "Mathematics", "title": "Mathe", "topics": typed,
            "competency_grid": [(io.BytesIO(data), "raster.pdf", "application/pdf")]},
            content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:300])
        with application.app.app_context():
            project = db.session.scalar(db.select(application.LearningProject))
            self.assertEqual([file.source_kind for file in project.files], ["grid"])
            titles = [section.title for section in sorted(project.sections, key=lambda item: item.position)]
        self.assertEqual(titles[0], "Ableitung (Summen-, Produktregel)")
        self.assertEqual(len(titles), 40)
        follow = self.client.get(response.headers["Location"]).get_data(as_text=True)
        follow += self.client.get(f"/projects/{project.id}").get_data(as_text=True)
        self.assertIn("Only the first 40 topics were added (45 typed).", follow)


class EvidenceTests(InventoryTestCase):
    def test_answers_count_for_the_competency_their_question_is_about_once(self):
        project_id, _ids = self.make_project(notes=False)
        self.quick_start(project_id)
        with application.app.app_context():
            project = self.project(project_id)
            linear = next(section for section in project.sections if section.title == "Lineare Funktionen")
            other = next(row for row in project.competencies if row.section_id != linear.id)
            question = {"prompt": "Bestimme die Steigung der linearen Funktion f(x) = 3x + 2.", "concept": "Steigung",
                        "competency_id": other.id}     # another topic's id is never taken
            found = application.record_competency_answer(linear, question, score=100, hints_used=False, attempt_id=7)
            application.record_competency_answer(linear, question, score=100, hints_used=False, attempt_id=7)
            application.record_competency_answer(linear, dict(question), score=40, hints_used=False, attempt_id=8)
            db.session.commit()
            row = db.session.get(application.Competency, found)
            self.assertEqual(row.statement, "Ich kann die Steigung einer linearen Funktion bestimmen.")
            self.assertEqual((row.answers, row.correct_answers), (2, 1))
            self.assertEqual(other.answers, 0)
            block = application.section_competency_block(linear)
        self.assertIn("Ich kann die Steigung einer linearen Funktion bestimmen.", block)
        self.assertIn("Ich kann den Graphen einer linearen Funktion zeichnen.", block)

    def test_the_workspace_shows_the_inventory_in_german(self):
        project_id, _ids = self.make_project(notes=False)
        self.quick_start(project_id)
        self.client.post("/settings/language", data={"language": "de"})
        page = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        self.assertIn('id="competencies"', page)
        self.assertIn("Kompetenzraster · 6 Kompetenzen", page)
        self.assertIn("Gefunden – noch nicht bestätigt", page)
        self.assertIn("Nicht in dieser Prüfung", page)


class MigrationTests(unittest.TestCase):
    def test_043_adds_the_inventory_columns_to_an_old_competency_table_once(self):
        with application.app.app_context():
            db.drop_all()
            db.create_all()
            with db.engine.begin() as connection:
                connection.execute(text("DROP TABLE competency"))
                connection.execute(text(
                    "CREATE TABLE competency (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, section_id INTEGER, "
                    "statement TEXT NOT NULL, topic VARCHAR(120) NOT NULL, subtopic VARCHAR(120) NOT NULL, "
                    "level VARCHAR(20) NOT NULL, importance INTEGER NOT NULL, coverage VARCHAR(20) NOT NULL, "
                    "evidence TEXT NOT NULL, source_page_ids_json TEXT NOT NULL, created_at DATETIME NOT NULL)"))
                connection.execute(text("DELETE FROM schema_migration WHERE version = '043_competency_inventory'"))
        application.ensure_database()
        with application.app.app_context():
            columns = {column["name"] for column in inspect(db.engine).get_columns("competency")}
            self.assertLessEqual({"position", "statement_key", "source_file_id", "group_path", "verified", "state",
                                  "taught_count", "answers", "correct_answers", "last_evidence_at", "evidence_json"},
                                 columns)
            self.assertIsNotNone(db.session.get(application.SchemaMigration, "043_competency_inventory"))
        application.ensure_database()        # a second run changes nothing
        with application.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count(application.SchemaMigration.version)).where(
                application.SchemaMigration.version == "043_competency_inventory")), 1)
            db.drop_all()
            db.create_all()


if __name__ == "__main__":
    unittest.main()
