"""Topic extraction v4 in Projektmodus - track DOC2, every model call mocked.

The user's bug: six Ethik papers, "only one of three topics". The causes this file pins:
28k-character batches answered within 4000 output tokens (cut-off JSON, the whole batch
skipped and only counted), header cleaning that deleted headings and saved the result
over the page text, topics typed by the student blocking the uploaded papers for good,
pages added later never analysed, and a merge rule that collapsed distinct topics.

What is checked here, through the real functions and routes:

* six documents / twelve pages: every page gets a topic, with page references and a
  structured topic map (terms with page ids and in_notes flags, items, exam questions);
* a cut-off answer is retried in halves; a page that fails alone is reported, the
  others still produce topics; the halves are cached, so a later run pays only for it;
* a provider rate limit stops the calls, leaves "not analysed" pages, and a later visit
  (ensure_project_sections via quick-start) analyses exactly those;
* duplicate pages are left out and recorded; failed pages never feed the extraction;
* distinct Ethics topics stay apart; teacher-marked pages survive the topic cap;
* typed topics no longer block the uploaded papers, and analysing them (or pages added
  later) costs no second project allowance;
* header lines stay in the stored text; line-end hyphenation and diagram labels count as
  what the notes say; one coverage-repair pass, and pages that still hold no topic.
"""

import io
import json
import os
import re
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import cast
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_project_extraction_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import pymupdf  # noqa: E402  pyright: ignore[reportMissingImports]

import app as application  # noqa: E402
from tests.config_isolation import keep_config  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.projects import topics as project_topics  # noqa: E402
from learnova.subscriptions.plans import SubscriptionSettings  # noqa: E402

db = application.db
PASSWORD = "correct-horse-battery"
HEADER = "Ethik Kursstufe - Frau Berger"

# (title, Fachbegriff, definition, person) per page; six documents of two pages each.
ETHICS = [
    ("Pflichtethik nach Kant", "Pflicht", "die Notwendigkeit einer Handlung aus Achtung vor dem Gesetz", "Kant"),
    ("Kategorischer Imperativ", "Maxime", "der subjektive Grundsatz des Wollens", "Kant"),
    ("Kritik am kategorischen Imperativ", "Rigorismus", "das starre Festhalten an Regeln ohne Ausnahme", "Constant"),
    ("Utilitarismus nach Bentham", "Hedonistisches Kalkuel", "eine Rechnung, die Freude und Leid gegeneinander abwaegt", "Bentham"),
    ("Utilitarismus nach Mill", "Hoehere Freuden", "geistige Freuden, die Mill ueber koerperliche stellt", "Mill"),
    ("Verantwortungsethik nach Jonas", "Heuristik der Furcht", "das Bedenken der schlimmsten moeglichen Folgen", "Jonas"),
    ("Hoehlengleichnis", "Schattenbilder", "die Abbilder, die die Gefangenen fuer die Wirklichkeit halten", "Platon"),
    ("Ideenlehre", "Idee", "das unveraenderliche Urbild der Dinge", "Platon"),
    ("Gerechtigkeit nach Rawls", "Urzustand", "eine gedachte Lage vor jeder Gesellschaftsordnung", "Rawls"),
    ("Schleier des Nichtwissens", "Nichtwissen", "die Unkenntnis der eigenen Stellung in der Gesellschaft", "Rawls"),
    ("Freiheit und Determinismus", "Determinismus", "die These, dass jedes Ereignis vollstaendig verursacht ist", "Spinoza"),
    ("Werte und Normen", "Norm", "eine Regel, die ein Sollen fuer das Handeln ausdrueckt", "Durkheim"),
]


def page_text(index, *, header=False):
    title, term, definition, person = ETHICS[index]
    body = (f"{title}\n{term} – {definition}.\n"
            f"In dieser Stunde ging es um {title}. {person} hat dazu einen wichtigen Gedanken formuliert, "
            f"den wir mit Beispielen aus dem Alltag geprueft haben. Zum Thema {title} gehoert der Begriff "
            f"{term}; er wird in der Klausur oft abgefragt und soll mit eigenen Worten erklaert werden. "
            f"Notiere zu {term} ein Beispiel und eine kritische Rueckfrage an {person}.")
    return f"{HEADER}\n{body}" if header else body


class FakeResponse:
    usage = None
    model = "test-model"

    def __init__(self, payload):
        self.output_text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)


def lesson():
    return {"lesson_title": "Thema", "detected_level": "Klasse 10", "concepts": [{"name": "Thema", "evidence": "notes"}],
            "explanation": "Erklaerung.", "worked_example": {"problem": "Beispiel", "steps": ["Schritt"], "answer": "A"},
            "teacher_tips": [], "exceptions": [],
            "question": {"id": "q1", "concept": "Thema", "difficulty": 1, "type": "multiple_choice", "prompt": "Frage?",
                         "hint": "Tipp", "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
                         "expected_answer": "a"}}


def batch_of(prompt):
    """The pages one extraction call was given (the JSON between "Pages: " and the shape)."""

    match = re.search(r"\nPages: (\[.*\])\nReturn JSON exactly as ", prompt, flags=re.DOTALL)
    assert match is not None, "the prompt carries the pages as JSON"
    return json.loads(match.group(1))


def topic_for(entry):
    """What a well-behaved model answers for one page: its heading, Fachbegriff and person.

    The page is recognised by its heading anywhere in the text, because a PDF's text layer
    re-wraps the lines; a fragment without a heading gets no topic of its own."""

    text = " ".join(entry["text"].split())
    found = sorted((text.find(title), index) for index, (title, *_rest) in enumerate(ETHICS) if title in text)
    if not found:
        return None
    title, term, definition, person = ETHICS[found[0][1]]
    page_id = entry["page_id"]
    items = [{"kind": "person", "name": person, "detail": "formuliert einen wichtigen Gedanken", "page_id": page_id}]
    # Not in the notes: a person the page never names is not kept.
    items.append({"kind": "person", "name": "Sokrates", "detail": "Lehrer Platons", "page_id": page_id})
    return {"title": title, "main_topic": "Ethik", "kind": "main",
            "summary": f"{title}: was die Notizen dazu sagen.", "simple_summary": f"{title} kurz erklaert.",
            "learning_goals": [f"{title} erklaeren"], "source_page_ids": [page_id],
            "terms": [{"term": term, "definition": definition, "simple_definition": "einfach gesagt",
                       "example": "", "page_id": page_id, "quote": f"{term} – {definition}"}],
            "items": items, "exam_questions": [f"Erklaere {title}."], "related_topics": [],
            "prerequisites": [], "difficulty": "medium", "importance": "medium", "estimated_minutes": 15}


class ExtractionTestCase(unittest.TestCase):
    def setUp(self):
        # Put the limiter back after each test: left off, it stayed off for every later
        # module in the session, and test_chat_photo_live_fixes.py's Retry-After tests
        # (which need it to run) failed only in the full alphabetical pass.
        self.addCleanup(setattr, application.limiter, "enabled", application.limiter.enabled)
        # The same for the config this setUp changes (AI_MODE, the cleared curriculum sources
        # and the quota switch stayed changed for every later module too).
        keep_config(self, application.app.config, "AI_MODE", "CURRICULUM_CLEARED_SOURCES",
                    "SUBSCRIPTION_QUOTAS_ENFORCED", "RATELIMIT_ENABLED")
        application.app.config.update(TESTING=True, AI_MODE="cached", CURRICULUM_CLEARED_SOURCES="",
                                      SUBSCRIPTION_QUOTAS_ENFORCED=False, RATELIMIT_ENABLED=False)
        application.limiter.enabled = False
        application.SESSIONS.clear()
        application._curriculum_index_cache.update(key=None, index=None)
        ai_service._RECENT_RESULTS.clear()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={"username": "lea", "email": "lea@example.com", "password": PASSWORD,
                                            "grade": "10"})
        self.prompts: list[str] = []
        self.calls: list[str] = []
        # Knobs for the fake model.
        self.cut_off_pages: set[int] = set()     # a batch of several entries with one of these is cut off
        self.broken_pages: set[int] = set()      # fails even alone
        self.limit_after: int | None = None      # extraction calls before a provider rate limit
        self.silent_pages: set[int] = set()      # the first pass gives these no topic
        self.empty_pages: set[int] = set()       # no topic in any pass (a cover page)
        self.extra_topic = None                  # callable(entry) -> extra raw fields

    # ---------------------------------------------------------------- the fake model
    def respond(self, *, task_type, **kwargs):
        self.calls.append(task_type)
        if task_type == "project_section_generation":
            prompt = str(kwargs.get("input", ""))
            self.prompts.append(prompt)
            if self.limit_after is not None and self.calls.count(task_type) > self.limit_after:
                raise ai_service.AIProviderError("provider_rate_limit", "rate limited")
            batch = batch_of(prompt)
            ids = {entry["page_id"] for entry in batch}
            if ids & self.broken_pages:
                return FakeResponse('{"sections":[{"title":"Abgeschnitten')
            if len(batch) > 1 and ids & self.cut_off_pages:
                # The answer budget ran out mid-JSON.
                return FakeResponse('{"sections":[{"title":"Pflichtethik","source_page_ids":[1]},{"title":"Kat')
            repair = "These pages have no topic yet" in prompt
            topics = []
            for entry in batch:
                if entry["page_id"] in self.empty_pages:
                    continue
                if entry["page_id"] in self.silent_pages and not repair:
                    continue
                topic = topic_for(entry)
                if topic is None:
                    continue
                if self.extra_topic:
                    topic.update(self.extra_topic(entry) or {})
                topics.append(topic)
            return FakeResponse({"sections": topics})
        if task_type in ("lesson_generation", "adaptive_practice"):
            return FakeResponse(lesson())
        raise AssertionError(f"unexpected AI task {task_type}")

    def post(self, path, **kwargs):
        with patch.object(application, "create_response", side_effect=self.respond):
            return self.client.post(path, **kwargs)

    # ---------------------------------------------------------------- material
    def user_id(self):
        return db.session.scalar(db.select(application.User.id).where(application.User.username == "lea"))

    def make_project(self, documents, *, subject="Ethics", texts=None, header=False, typed=()):
        """Six documents of read pages (as the text layer reads a born-digital PDF).

        `documents` lists page indexes into ETHICS per document; `texts` overrides page
        text by index. Returns (project id, [page ids in order])."""

        with application.app.app_context():
            project = application.LearningProject(user_id=self.user_id(), title="Ethik", subject=subject,
                                                  status="uploaded")
            db.session.add(project)
            db.session.flush()
            order, page_ids = 0, []
            for number, indexes in enumerate(documents, start=1):
                file = application.ProjectFile(project_id=project.id, original_filename=f"ethik-{number}.pdf",
                                               mime_type="application/pdf", original_data=b"%PDF", sha256=f"{number}")
                db.session.add(file)
                db.session.flush()
                for page_number, index in enumerate(indexes, start=1):
                    order += 1
                    text = (texts or {}).get(index) or page_text(index, header=header)
                    page = application.ProjectPage(
                        project_id=project.id, file_id=file.id, page_number=page_number, page_order=order,
                        extracted_text=text, extraction_status="ready", confidence_status="high",
                        recognition_confidence=0.95, review_status="pending",
                        recognition_json=json.dumps({"text_source": "text_layer"}))
                    db.session.add(page)
                    db.session.flush()
                    page_ids.append(page.id)
            for title in typed:
                application.add_manual_topics(project, [title])
            db.session.commit()
            return project.id, page_ids

    def project(self, project_id) -> "application.LearningProject":
        project = db.session.get(application.LearningProject, project_id)
        assert project is not None
        return project

    def analysis(self, project_id):
        with application.app.app_context():
            return json.loads(self.project(project_id).analysis_json)

    def sections(self, project_id):
        with application.app.app_context():
            project = self.project(project_id)
            return [{"id": section.id, "title": section.title, "pages": json.loads(section.source_page_ids_json),
                     "map": json.loads(section.topic_map_json), "importance": section.importance,
                     "excluded": section.excluded, "user_edited": section.user_edited}
                    for section in sorted(project.sections, key=lambda item: item.position)]

    def quick_start(self, project_id):
        response = self.post(f"/projects/{project_id}/quick-start")
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])
        return response

    def extraction_calls(self):
        return self.calls.count("project_section_generation")


SIX_DOCUMENTS = [[0, 1], [2, 3], [4, 5], [6, 7], [8, 9], [10, 11]]


class TopicMapTests(ExtractionTestCase):
    def test_six_documents_twelve_pages_give_every_page_a_topic_with_page_references(self):
        project_id, page_ids = self.make_project(SIX_DOCUMENTS)
        found = self.quick_start(project_id)
        self.assertIn("#topics", found.headers["Location"], "the student sees the topics first")
        self.assertEqual(self.extraction_calls(), 3, "twelve pages in batches of four")
        sections = self.sections(project_id)
        self.assertEqual([section["title"] for section in sections], [entry[0] for entry in ETHICS])
        self.assertEqual([section["pages"] for section in sections], [[page_id] for page_id in page_ids])
        self.assertTrue(all(not section["excluded"] for section in sections), "every topic is included")
        analysis = self.analysis(project_id)
        self.assertEqual(analysis["analysis_version"], 4)
        self.assertEqual(analysis["analysed_page_ids"], page_ids)
        for key in ("failed_page_ids", "not_analysed_page_ids", "uncovered_page_ids", "no_topic_page_ids"):
            self.assertEqual(analysis[key], [], key)
        self.assertEqual(analysis["duplicates"], {})
        self.assertEqual(analysis["topics_dropped"], 0)
        with application.app.app_context(), application.app.test_request_context():
            report = application.project_material_report(self.project(project_id))
        self.assertTrue(report["complete"], report["issues"])
        self.assertEqual(report["documents_analysed"], 6)
        self.assertEqual(report["terms_total"], 12)

    def test_the_v4_prompt_shows_the_shape_and_the_subject_focus(self):
        project_id, _ = self.make_project([[0, 1]])
        self.quick_start(project_id)
        prompt = self.prompts[0]
        self.assertEqual(ai_service.PROMPT_VERSIONS["project_section_generation"], "project_section_generation:v4")
        self.assertIn(application.PROJECT_TOPIC_SHAPE, prompt)
        self.assertIn('"source_page_ids":[<page_id>]', prompt)
        self.assertIn("Every page with learning content belongs to at least one topic", prompt)
        self.assertIn("No invented interpretations", prompt)
        # Ethics focus: Fachbegriffe into terms (with a quote), other kinds into items.
        self.assertIn('put every Fachbegriff into "terms"', prompt)
        self.assertIn("thought_experiment", prompt.split("Pages:")[0])
        self.assertNotIn("These pages have no topic yet", prompt)

    def test_the_topic_map_is_stored_with_terms_carrying_page_ids_and_in_notes_flags(self):
        project_id, page_ids = self.make_project([[0, 1]])
        self.quick_start(project_id)
        first = self.sections(project_id)[0]
        topic_map = first["map"]
        self.assertEqual(set(topic_map), {"summary", "simple_summary", "kind", "terms", "items", "exam_questions",
                                          "related"})
        self.assertEqual(topic_map["kind"], "main")
        term = topic_map["terms"][0]
        self.assertEqual(term["term"], "Pflicht")
        self.assertEqual(term["page_id"], page_ids[0])
        self.assertEqual(term["page_ids"], [page_ids[0]])
        self.assertTrue(term["in_notes"])
        self.assertTrue(term["definition_from_notes"], "the quote is on the page")
        self.assertEqual([item["name"] for item in topic_map["items"]], ["Kant"],
                         "a person the notes never name is not part of them")
        self.assertTrue(topic_map["items"][0]["in_notes"])
        self.assertEqual(topic_map["exam_questions"], ["Erklaere Pflichtethik nach Kant."])
        with application.app.app_context():
            section = db.session.get(application.LearningSection, first["id"])
            assert section is not None
            self.assertEqual(json.loads(section.definitions_json),
                             ["Pflicht: die Notwendigkeit einer Handlung aus Achtung vor dem Gesetz"],
                             "the legacy columns are still filled")
            cards = [card.prompt for card in section.recall_cards]
            self.assertIn("What does “Pflicht” mean?", cards, "a verified definition is a recall card")

    def test_distinct_ethics_topics_stay_separate(self):
        project_id, _ = self.make_project([[0, 1, 2], [3, 4]])
        self.quick_start(project_id)
        titles = [section["title"] for section in self.sections(project_id)]
        self.assertEqual(titles, ["Pflichtethik nach Kant", "Kategorischer Imperativ",
                                  "Kritik am kategorischen Imperativ", "Utilitarismus nach Bentham",
                                  "Utilitarismus nach Mill"])

    def test_header_lines_stay_in_the_stored_text_and_leave_only_the_payload(self):
        project_id, page_ids = self.make_project([[0, 1, 2, 3]], header=True)
        self.quick_start(project_id)
        self.assertNotIn(HEADER, self.prompts[0], "a running header is not sent as content")
        titles = [section["title"] for section in self.sections(project_id)]
        self.assertEqual(titles, [entry[0] for entry in ETHICS[:4]], "the header did not become every title")
        with application.app.app_context():
            for page_id in page_ids:
                page = db.session.get(application.ProjectPage, page_id)
                assert page is not None
                self.assertTrue(page.extracted_text.startswith(HEADER), "the stored text keeps its header")
        # A second analysis strips nothing more either.
        self.post(f"/projects/{project_id}/reanalyze")
        with application.app.app_context():
            page = db.session.get(application.ProjectPage, page_ids[0])
            assert page is not None
            self.assertEqual(page.extracted_text, page_text(0, header=True))

    def test_hyphenated_words_and_diagram_labels_count_as_what_the_notes_say(self):
        text = page_text(0).replace("Notwendigkeit", "Notwen-\ndigkeit")
        project_id, page_ids = self.make_project([[0]], texts={0: text})
        with application.app.app_context():
            block = application.DocumentBlock(page_id=page_ids[0], block_order=1, block_type="diagram",
                                              content="Liniengleichnis: Schatten, Dinge, Ideen")
            db.session.add(block)
            db.session.commit()
        self.extra_topic = lambda entry: {
            "terms": [{"term": "Notwendigkeit", "definition": "was sein muss", "page_id": entry["page_id"],
                       "quote": ""}],
            "items": [{"kind": "diagram", "name": "Liniengleichnis", "detail": "Schatten, Dinge, Ideen",
                       "page_id": entry["page_id"]}]}
        self.quick_start(project_id)
        self.assertIn("Notwendigkeit", self.prompts[0], "the model reads the joined word")
        topic_map = self.sections(project_id)[0]["map"]
        self.assertEqual([term["term"] for term in topic_map["terms"]], ["Notwendigkeit"])
        self.assertTrue(topic_map["terms"][0]["in_notes"])
        self.assertEqual([(item["kind"], item["name"], item["in_notes"]) for item in topic_map["items"]],
                         [("diagram", "Liniengleichnis", True)])
        self.assertEqual(application.join_line_hyphenation("Nord-\nSued und Notwen-\ndigkeit"),
                         "Nord-Sued und Notwendigkeit")

    def test_teacher_marked_pages_survive_the_topic_cap(self):
        project_id, page_ids = self.make_project(SIX_DOCUMENTS)
        with application.app.app_context():
            page = db.session.get(application.ProjectPage, page_ids[11])
            assert page is not None
            page.review_status, page.important = "confirmed", True
            db.session.commit()
        with patch.object(project_topics, "MAX_TOPICS", 3):
            self.quick_start(project_id)
        sections = self.sections(project_id)
        self.assertEqual(len(sections), 3)
        self.assertIn("Werte und Normen", [section["title"] for section in sections],
                      "the teacher-marked page's topic is kept although it comes last")
        self.assertEqual(next(section["importance"] for section in sections if section["title"] == "Werte und Normen"), 3)
        analysis = self.analysis(project_id)
        self.assertEqual(analysis["topics_dropped"], 9, "the drop is recorded, never silent")
        self.assertEqual(len(analysis["topics_dropped_titles"]), 9)
        self.assertEqual(len(analysis["uncovered_page_ids"]), 9)
        self.assertFalse(any("These pages have no topic yet" in prompt for prompt in self.prompts),
                         "pages that lost their topic to the cap are not re-read")


class FailureTests(ExtractionTestCase):
    def test_a_cut_off_batch_is_read_in_halves_and_only_a_page_that_fails_alone_is_reported(self):
        project_id, page_ids = self.make_project([[0, 1, 2, 3], [4, 5, 6, 7]])
        self.cut_off_pages = {page_ids[2]}
        self.broken_pages = {page_ids[6]}
        self.quick_start(project_id)
        titles = [section["title"] for section in self.sections(project_id)]
        expected = [entry[0] for index, entry in enumerate(ETHICS[:8]) if index != 6]
        self.assertEqual(titles, expected, "the pages around a cut-off answer still produce topics")
        analysis = self.analysis(project_id)
        self.assertEqual(analysis["failed_page_ids"], [page_ids[6]])
        self.assertEqual(analysis["analysed_page_ids"], [page_id for page_id in page_ids if page_id != page_ids[6]])
        self.assertGreater(analysis["failed_batches"], 0)
        with application.app.app_context(), application.app.test_request_context():
            report = application.project_material_report(self.project(project_id))
        self.assertFalse(report["complete"])
        self.assertIn("analysis_failed", {issue["code"] for issue in report["issues"]})
        # The halves were cached: a later run pays only for the page that failed.
        before = self.extraction_calls()
        self.broken_pages = set()
        self.post(f"/projects/{project_id}/reanalyze")
        self.assertEqual(self.extraction_calls() - before, 2, "only page 7's two fragments are read again")
        self.assertEqual(len(self.sections(project_id)), 8)
        self.assertEqual(self.analysis(project_id)["failed_page_ids"], [])

    def test_a_validation_error_from_the_gateway_is_bisected_too(self):
        project_id, page_ids = self.make_project([[0, 1, 2, 3]])
        original = self.respond

        def strict(*, task_type, **kwargs):
            if task_type == "project_section_generation" and len(batch_of(str(kwargs.get("input", "")))) > 2:
                self.calls.append(task_type)
                raise application.ai_service.AIValidationError("invalid_json", "response was truncated")
            return original(task_type=task_type, **kwargs)

        self.respond = strict
        self.quick_start(project_id)
        self.assertEqual(self.extraction_calls(), 3, "one refused batch, then its two halves")
        self.assertEqual(len(self.sections(project_id)), 4)
        self.assertEqual(self.analysis(project_id)["analysed_page_ids"], page_ids)

    def test_a_rate_limit_leaves_pages_not_analysed_and_a_later_visit_analyses_them(self):
        project_id, page_ids = self.make_project(SIX_DOCUMENTS)
        self.limit_after = 1
        found = self.quick_start(project_id)
        self.assertIn("#topics", found.headers["Location"])
        self.assertEqual(self.extraction_calls(), 2, "the rate limit stops every further call")
        analysis = self.analysis(project_id)
        self.assertEqual(analysis["analysed_page_ids"], page_ids[:4])
        self.assertEqual(analysis["not_analysed_page_ids"], page_ids[4:])
        self.assertEqual(analysis["analysis_stopped"], "provider_rate_limit")
        self.assertEqual(len(self.sections(project_id)), 4)
        with application.app.app_context(), application.app.test_request_context():
            project = self.project(project_id)
            self.assertTrue(project.topics_stale, "Update topics stays visible")
            report = application.project_material_report(project)
            self.assertEqual(report["pages_not_analysed"], 8)
            self.assertEqual(application.project_pages_to_analyse(project), page_ids[4:])
        page = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        self.assertIn("could not analyse 8 page(s) yet", page)
        # Straight away again: the provider was busy moments ago, so nothing is spent.
        self.limit_after = None
        before = self.extraction_calls()
        self.quick_start(project_id)
        self.assertEqual(self.extraction_calls(), before, "no knocking on a busy provider at every visit")
        # Later: the provider is back, and the next visit reads exactly the missing pages.
        with application.app.app_context():
            project = self.project(project_id)
            stored = json.loads(project.analysis_json)
            stored["analyzed_at"] = (application.utcnow() - timedelta(minutes=10)).isoformat()
            project.analysis_json = json.dumps(stored)
            db.session.commit()
        self.quick_start(project_id)
        self.assertEqual(self.extraction_calls() - before, 2, "the first batch comes from the cache")
        analysis = self.analysis(project_id)
        self.assertEqual(analysis["analysed_page_ids"], page_ids)
        self.assertEqual(analysis["not_analysed_page_ids"], [])
        self.assertEqual(len(self.sections(project_id)), 12)

    def test_a_rate_limit_before_any_topic_is_an_ai_error_and_gives_the_allowance_back(self):
        application.app.config.update(SUBSCRIPTION_QUOTAS_ENFORCED=True, SUBSCRIPTION_SETTINGS=SubscriptionSettings())
        project_id, _ = self.make_project([[0, 1]])
        self.limit_after = 0
        response = self.post(f"/projects/{project_id}/quick-start", headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 503, response.get_data(as_text=True))
        self.client.get("/")
        with application.app.app_context():
            self.assertEqual(application.entitlements.allowance(self.user_id(), "project").used, 0)
            self.assertEqual(self.project(project_id).sections, [])

    def test_duplicate_pages_are_left_out_and_reported(self):
        project_id, page_ids = self.make_project([[0, 1], [2, 0]])
        self.quick_start(project_id)
        self.assertEqual(sum(len(batch_of(prompt)) for prompt in self.prompts), 3, "the copy is not sent")
        analysis = self.analysis(project_id)
        self.assertEqual(analysis["duplicates"], {str(page_ids[3]): page_ids[0]})
        self.assertNotIn(page_ids[3], analysis["analysed_page_ids"])
        self.assertEqual(len(self.sections(project_id)), 3)
        with application.app.app_context(), application.app.test_request_context():
            project = self.project(project_id)
            report = application.project_material_report(project)
            self.assertEqual(report["pages_duplicate"], 1)
            self.assertTrue(report["complete"], report["issues"])
            self.assertEqual(application.project_pages_to_analyse(project), [], "a copy never triggers analysis")

    def test_a_page_that_could_not_be_read_never_feeds_the_extraction(self):
        project_id, page_ids = self.make_project([[0, 1, 2]])
        with application.app.app_context():
            page = db.session.get(application.ProjectPage, page_ids[1])
            assert page is not None
            page.extraction_status = "failed"     # its PDF text layer still holds the words
            db.session.commit()
        self.quick_start(project_id)
        self.assertNotIn(ETHICS[1][0], "".join(self.prompts))
        self.assertNotIn(page_ids[1], self.analysis(project_id)["analysed_page_ids"])
        self.assertEqual([section["title"] for section in self.sections(project_id)],
                         [ETHICS[0][0], ETHICS[2][0]])


class ReanalysisSafetyTests(ExtractionTestCase):
    def test_a_rerun_that_finds_nothing_keeps_the_topics(self):
        project_id, _ = self.make_project([[0, 1]])
        self.quick_start(project_id)
        before = [section["id"] for section in self.sections(project_id)]
        self.empty_pages = set(range(1, 1000))      # the model now answers {"sections": []}
        with application.app.app_context():
            project = self.project(project_id)
            project.analysis_json = json.dumps({**json.loads(project.analysis_json), "batches": {}})
            db.session.commit()
        self.post(f"/projects/{project_id}/reanalyze")
        self.assertEqual([section["id"] for section in self.sections(project_id)], before)

    def test_topics_on_pages_a_stopped_run_could_not_read_are_kept(self):
        project_id, page_ids = self.make_project(SIX_DOCUMENTS)
        self.quick_start(project_id)
        self.assertEqual(len(self.sections(project_id)), 12)
        with application.app.app_context():
            project = self.project(project_id)
            project.analysis_json = json.dumps({**json.loads(project.analysis_json), "batches": {}})
            db.session.commit()
        self.limit_after = self.extraction_calls() + 1     # one batch, then the provider is busy
        self.post(f"/projects/{project_id}/reanalyze")
        self.assertEqual(len(self.sections(project_id)), 12, "unread pages are no evidence a topic is gone")
        self.assertEqual(self.analysis(project_id)["not_analysed_page_ids"], page_ids[4:])


class MergeAliasTests(ExtractionTestCase):
    def test_a_topic_merged_under_an_old_key_is_folded_into_the_merged_topic(self):
        project_id, page_ids = self.make_project([[3, 4]])
        self.quick_start(project_id)
        first, second = self.sections(project_id)
        merged = self.client.post(f"/projects/{project_id}/sections/merge",
                                  data={"section_ids": [str(first["id"]), str(second["id"])]})
        self.assertEqual(merged.status_code, 302)
        legacy = project_topics._legacy_key("Utilitarismus nach Mill")
        self.assertNotEqual(legacy, project_topics.topic_key("Utilitarismus nach Mill"),
                            "the first key version counted 'nach'")
        with application.app.app_context():
            project = self.project(project_id)
            # As a project merged before the key change stored it, and with the cache gone.
            project.analysis_json = json.dumps({**json.loads(project.analysis_json),
                                                "aliases": {legacy: first["id"]}, "batches": {}})
            db.session.commit()
        self.post(f"/projects/{project_id}/reanalyze")
        sections = self.sections(project_id)
        self.assertEqual([section["id"] for section in sections], [first["id"]], "the merge survives the scan")
        self.assertEqual(sorted(sections[0]["pages"]), sorted(page_ids), "the absorbed topic's page stays covered")
        self.assertEqual({term["term"] for term in sections[0]["map"]["terms"]},
                         {ETHICS[3][1], ETHICS[4][1]})


class CoverageTests(ExtractionTestCase):
    def test_one_coverage_repair_pass_and_pages_that_hold_no_topic(self):
        cover = ("Deckblatt\nEthik Mappe – Kursstufe 1\nName, Klasse und Datum hier eintragen. "
                 "Inhaltsverzeichnis folgt auf der naechsten Seite.")
        project_id, page_ids = self.make_project([[0, 1, 2, 3, 4]], texts={4: cover})
        self.silent_pages = {page_ids[1]}       # the first pass misses it
        self.empty_pages = {page_ids[4]}        # a cover page: no topic in any pass
        self.quick_start(project_id)
        repair_prompts = [prompt for prompt in self.prompts if "These pages have no topic yet" in prompt]
        self.assertEqual(len(repair_prompts), 1, "one repair call for both uncovered pages")
        self.assertEqual({entry["page_id"] for entry in batch_of(repair_prompts[0])}, {page_ids[1], page_ids[4]})
        titles = [section["title"] for section in self.sections(project_id)]
        self.assertIn(ETHICS[1][0], titles, "the repair pass found the missed topic")
        analysis = self.analysis(project_id)
        self.assertEqual(analysis["no_topic_page_ids"], [page_ids[4]])
        self.assertEqual(analysis["uncovered_page_ids"], [page_ids[4]])
        with application.app.app_context(), application.app.test_request_context():
            report = application.project_material_report(self.project(project_id))
        self.assertTrue(report["complete"], "a page confirmed to hold no topic is not missing")


class LaterPagesTests(ExtractionTestCase):
    def pdf(self, indexes):
        document = pymupdf.open()
        for index in indexes:
            page = document.new_page()  # pyright: ignore[reportAttributeAccessIssue]
            page.insert_textbox(pymupdf.Rect(40, 40, 560, 800), page_text(index), fontsize=10)
        data = document.tobytes()
        document.close()
        return data

    def test_typed_topics_no_longer_block_the_uploaded_papers_and_cost_no_second_allowance(self):
        application.app.config.update(SUBSCRIPTION_QUOTAS_ENFORCED=True, SUBSCRIPTION_SETTINGS=SubscriptionSettings())
        created = self.post("/projects", data={
            "subject": "Ethics", "title": "Ethik", "topics": "Glueck",
            "exam_date": (date.today() + timedelta(days=20)).isoformat(),
            "materials": [(io.BytesIO(self.pdf([0, 1])), "kant.pdf", "application/pdf"),
                          (io.BytesIO(self.pdf([3])), "bentham.pdf", "application/pdf")]},
            content_type="multipart/form-data")
        self.assertEqual(created.status_code, 302, created.get_data(as_text=True)[:300])
        with application.app.app_context():
            project_id = db.session.scalar(db.select(application.LearningProject.id))
            self.assertEqual(application.entitlements.allowance(self.user_id(), "project").used, 1)
        self.quick_start(project_id)
        self.assertNotIn("ocr_document_recognition", self.calls, "born-digital pages are read from their text")
        self.assertEqual(self.extraction_calls(), 1, "the uploaded papers are analysed")
        titles = [section["title"] for section in self.sections(project_id)]
        self.assertIn("Glueck", titles, "the typed topic stays")
        for expected in (ETHICS[0][0], ETHICS[1][0], ETHICS[3][0]):
            self.assertIn(expected, titles)
        with application.app.app_context():
            self.assertEqual(application.entitlements.allowance(self.user_id(), "project").used, 1,
                             "analysing the papers of a typed-topic project counts nothing more")

        # Confirm, then add a paper later: its topics are found and shown, not skipped.
        self.post(f"/projects/{project_id}/topics/confirm", data={"choice": "auto"})
        added = self.post(f"/projects/{project_id}/pages/add", data={
            "materials": [(io.BytesIO(self.pdf([8])), "rawls.pdf", "application/pdf")]},
            content_type="multipart/form-data")
        self.assertEqual(added.status_code, 302)
        found = self.quick_start(project_id)
        self.assertTrue(found.headers["Location"].endswith("#topics"),
                        "new topics are shown before the next lesson")
        self.assertEqual(self.extraction_calls(), 2, "only the new page's batch is read")
        sections = self.sections(project_id)
        rawls = next(section for section in sections if section["title"] == ETHICS[8][0])
        self.assertFalse(rawls["excluded"], "a new topic is included")
        page = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        self.assertIn("Learnova found 1 new topics in your new pages.", page)
        with application.app.app_context():
            project = self.project(project_id)
            self.assertEqual(application.project_pages_to_analyse(project), [])
            self.assertEqual(application.entitlements.allowance(self.user_id(), "project").used, 1)
        # Nothing new: the next visit goes straight on, without another analysis.
        self.quick_start(project_id)
        self.assertEqual(self.extraction_calls(), 2)

    def test_a_project_analysed_before_v4_only_analyses_pages_added_after_it(self):
        project_id, page_ids = self.make_project([[0, 1]])
        self.quick_start(project_id)
        with application.app.app_context():
            project = self.project(project_id)
            analysis = json.loads(project.analysis_json)
            for key in ("analysed_page_ids", "failed_page_ids", "not_analysed_page_ids", "duplicates"):
                analysis.pop(key, None)
            project.analysis_json = json.dumps(analysis)
            db.session.commit()
            self.assertEqual(application.project_pages_to_analyse(project), [],
                             "pages cited or present at the last analysis count as analysed")

    def test_an_old_analysis_that_skipped_batches_reads_the_pages_no_topic_cites(self):
        # The user's bug: a v3 analysis dropped whole batches and stored only a count.
        # Those pages are not "analysed"; the next visit reads them.
        project_id, page_ids = self.make_project([[0, 1, 2, 3]])
        self.empty_pages = {page_ids[2], page_ids[3]}
        self.quick_start(project_id)
        with application.app.app_context():
            project = self.project(project_id)
            analysis = json.loads(project.analysis_json)
            for key in ("analysed_page_ids", "failed_page_ids", "not_analysed_page_ids", "duplicates",
                        "no_topic_page_ids", "uncovered_page_ids", "analysis_version"):
                analysis.pop(key, None)
            analysis.update(failed_batches=1, batches={})
            project.analysis_json = json.dumps(analysis)
            db.session.commit()
            self.assertEqual(application.project_pages_to_analyse(project), page_ids[2:])
        self.empty_pages = set()
        before = self.extraction_calls()
        self.quick_start(project_id)
        self.assertGreater(self.extraction_calls(), before)
        self.assertEqual(len(self.sections(project_id)), 4, "the skipped pages now have their topics")


class ReviewFindingsTests(ExtractionTestCase):
    def test_a_page_that_failed_the_analysis_keeps_update_topics_visible(self):
        project_id, page_ids = self.make_project([[0, 1]])
        self.broken_pages = {page_ids[1]}
        self.quick_start(project_id)
        self.assertEqual(self.analysis(project_id)["failed_page_ids"], [page_ids[1]])
        with application.app.app_context():
            self.assertTrue(self.project(project_id).topics_stale,
                            "the only way to retry the page is Update topics, so it stays visible")
        page = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        # PF2: "Update topics" opens the start page, which works the topics out in steps.
        self.assertRegex(page, rf"/projects/{project_id}/start\?auto=1&(amp;)?then=topics")

    def test_a_rate_limit_in_the_coverage_repair_pass_leaves_the_page_for_a_later_visit(self):
        project_id, page_ids = self.make_project([[0, 1, 2, 3]])
        self.silent_pages = {page_ids[1]}
        self.limit_after = 1                 # the main pass, then the provider is busy
        self.quick_start(project_id)
        analysis = self.analysis(project_id)
        self.assertEqual(analysis["not_analysed_page_ids"], [page_ids[1]],
                         "the second look never happened: the page is not analysed yet")
        self.assertNotIn(page_ids[1], analysis["analysed_page_ids"])
        self.assertEqual(analysis["no_topic_page_ids"], [])
        with application.app.app_context():
            self.assertEqual(application.project_pages_to_analyse(self.project(project_id)), [page_ids[1]])
        self.limit_after = None
        with application.app.app_context():
            project = self.project(project_id)
            stored = json.loads(project.analysis_json)
            stored["analyzed_at"] = (application.utcnow() - timedelta(minutes=10)).isoformat()
            project.analysis_json = json.dumps(stored)
            db.session.commit()
        before = self.extraction_calls()
        self.quick_start(project_id)
        self.assertEqual(self.extraction_calls() - before, 1, "the main pass is cached; only the repair runs")
        self.assertIn(ETHICS[1][0], [section["title"] for section in self.sections(project_id)])
        analysis = self.analysis(project_id)
        self.assertEqual(analysis["analysed_page_ids"], page_ids)
        self.assertEqual(analysis["not_analysed_page_ids"], [])

    def test_a_run_stopped_before_the_repair_pass_leaves_its_uncovered_pages_for_later(self):
        project_id, page_ids = self.make_project(SIX_DOCUMENTS)
        self.silent_pages = {page_ids[1]}
        self.limit_after = 1                 # batch 1 answers, batch 2 is refused
        self.quick_start(project_id)
        analysis = self.analysis(project_id)
        self.assertIn(page_ids[1], analysis["not_analysed_page_ids"])
        self.assertNotIn(page_ids[1], analysis["uncovered_page_ids"])
        self.assertEqual(analysis["no_topic_page_ids"], [])


class CompletenessAuditTests(ExtractionTestCase):
    """Spec audit (FIX1): never report material as fully analysed when it was not."""

    def report(self, project_id):
        response = self.client.get(f"/projects/{project_id}/analysis")
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_a_typed_topic_claims_no_page_and_a_stopped_analysis_is_not_complete(self):
        # P1-01: a topic typed on the create form used to cite every page, so the report
        # said "Fully analysed" before any analysis ran, and after a rate-limited one.
        project_id, page_ids = self.make_project([[0, 1], [2]], typed=("Mein Thema",))
        self.assertEqual(self.sections(project_id)[0]["pages"], [], "a typed topic claims no page")
        report = self.report(project_id)
        self.assertFalse(report["complete"])
        self.assertEqual([file["state"] for file in report["files"]], ["not_analysed", "not_analysed"])
        self.limit_after = 0
        self.quick_start(project_id)
        self.assertEqual(self.analysis(project_id)["not_analysed_page_ids"], page_ids)
        report = self.report(project_id)
        self.assertFalse(report["complete"])
        self.assertEqual([file["message"] for file in report["files"]],
                         ["Document 1: Read, not analysed yet", "Document 2: Read, not analysed yet"])
        self.assertEqual(report["pages_not_analysed"], 3)
        # A topic the student adds on a page the stopped run never analysed does not
        # turn that page into "analysed" either: the explicit page lists win.
        self.post(f"/projects/{project_id}/topics/add", data={"title": "Pflicht", "page_ids": [str(page_ids[0])]})
        report = self.report(project_id)
        self.assertEqual(report["files"][0]["state"], "not_analysed")
        self.assertFalse(report["complete"])

    def test_kompetenzraster_pages_raise_importance_in_the_one_tap_flow(self):
        # P1-02: the grid upload marks its pages teacher-marked, but the high-priority
        # rule needed review_status "confirmed", which the one-tap flow never sets.
        # LaterPagesTests.pdf never reads self; borrowing it keeps one PDF builder.
        def pdf(indexes):
            return LaterPagesTests.pdf(cast(LaterPagesTests, self), indexes)

        created = self.post("/projects", data={
            "subject": "Ethics", "title": "Ethik",
            "materials": [(io.BytesIO(pdf([0])), "notes.pdf", "application/pdf")],
            "competency_grid": [(io.BytesIO(pdf([6])), "raster.pdf", "application/pdf")]},
            content_type="multipart/form-data")
        self.assertEqual(created.status_code, 302, created.get_data(as_text=True)[:300])
        with application.app.app_context():
            project_id = db.session.scalar(db.select(application.LearningProject.id))
        self.quick_start(project_id)
        importance = {section["title"]: section["importance"] for section in self.sections(project_id)}
        self.assertEqual(importance[ETHICS[6][0]], 3, "the Kompetenzraster topic is important for the exam")
        self.assertLess(importance[ETHICS[0][0]], 3)

    def test_model_guessed_block_marks_still_need_the_students_confirmation(self):
        project_id, page_ids = self.make_project([[0, 1]])
        with application.app.app_context():
            db.session.add(application.DocumentBlock(page_id=page_ids[1], block_order=1, block_type="printed_text",
                                                     content="Maxime", important=True, teacher_highlighted=True))
            db.session.commit()
        self.quick_start(project_id)
        self.assertEqual([section["importance"] for section in self.sections(project_id)], [2, 2],
                         "a highlight the vision model guessed is not a teacher's mark")

    def test_a_page_whose_repair_call_broke_is_failed_and_retried_by_update_topics(self):
        # P1-08: the second look at an uncovered page came back cut off. The page used to
        # stay "analysed" and "uncovered" with no retry anywhere.
        project_id, page_ids = self.make_project([[0, 1]])
        self.silent_pages = {page_ids[1]}
        original = self.respond

        def respond(*, task_type, **kwargs):
            if task_type == "project_section_generation" and "These pages have no topic yet" in str(
                    kwargs.get("input", "")):
                self.calls.append(task_type)
                return FakeResponse('{"sections":[{"title":"Abgeschn')
            return original(task_type=task_type, **kwargs)

        self.respond = respond
        self.quick_start(project_id)
        analysis = self.analysis(project_id)
        self.assertEqual(analysis["failed_page_ids"], [page_ids[1]])
        self.assertNotIn(page_ids[1], analysis["analysed_page_ids"])
        self.assertEqual(analysis["uncovered_page_ids"], [])
        with application.app.app_context():
            self.assertTrue(self.project(project_id).topics_stale, "Update topics stays visible")
        report = self.report(project_id)
        self.assertFalse(report["complete"])
        self.assertEqual(report["pages_analysis_failed"], 1)
        # "Update topics" asks again, and this time the model answers.
        self.respond = original
        self.silent_pages = set()
        self.post(f"/projects/{project_id}/reanalyze")
        analysis = self.analysis(project_id)
        self.assertEqual(analysis["failed_page_ids"], [])
        self.assertIn(ETHICS[1][0], [section["title"] for section in self.sections(project_id)])

    def test_a_topic_citing_page_order_numbers_is_mapped_to_its_pages_not_dropped(self):
        # P1-14: the payload carries page_order too; a model that cites those numbers
        # used to lose the topic silently.
        project_id, page_ids = self.make_project([[0, 1]])
        order = {}
        with application.app.app_context():
            for page_id in page_ids:
                page = db.session.get(application.ProjectPage, page_id)
                assert page is not None
                page.page_order += 100          # page_order and page id differ now
                order[page_id] = page.page_order
            db.session.commit()
        self.extra_topic = lambda entry: {"source_page_ids": [order[entry["page_id"]]]}
        self.quick_start(project_id)
        sections = self.sections(project_id)
        self.assertEqual([(section["title"], section["pages"]) for section in sections],
                         [(ETHICS[0][0], [page_ids[0]]), (ETHICS[1][0], [page_ids[1]])])
        self.assertEqual(self.analysis(project_id).get("topics_unplaced", 0), 0)

    def test_a_topic_citing_no_page_of_its_batch_is_counted_not_lost_silently(self):
        project_id, page_ids = self.make_project([[0, 1, 2]])
        self.extra_topic = lambda entry: ({"source_page_ids": [9999]} if entry["page_id"] != page_ids[0] else {})
        self.quick_start(project_id)
        analysis = self.analysis(project_id)
        self.assertEqual(analysis["topics_unplaced"], 2)
        self.assertEqual(analysis["topics_unplaced_titles"], [ETHICS[1][0], ETHICS[2][0]])
        page = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        self.assertIn("2 topic(s) the AI named could not be linked to a page", page)

    def test_topics_dropped_over_the_cap_keep_their_pages_for_the_one_tap_add(self):
        # P1-11: "+ title" restored a dropped topic without its pages, so it was taught as
        # standard school content although it came from the notes.
        project_id, page_ids = self.make_project([[0, 1, 2]])
        with patch.object(project_topics, "MAX_TOPICS", 2):
            self.quick_start(project_id)
        analysis = self.analysis(project_id)
        self.assertEqual(analysis["topics_dropped_titles"], [ETHICS[2][0]])
        self.assertEqual(analysis["topics_dropped_pages"], {ETHICS[2][0]: [page_ids[2]]})
        page = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        self.assertIn(f'name="page_ids" value="{page_ids[2]}"', page)
        self.post(f"/projects/{project_id}/topics/add", data={"title": ETHICS[2][0], "page_ids": [str(page_ids[2])]})
        added = next(section for section in self.sections(project_id) if section["title"] == ETHICS[2][0])
        self.assertEqual(added["pages"], [page_ids[2]])
        with application.app.app_context(), application.app.test_request_context():
            context = application.project_workspace_context(self.project(project_id))
        self.assertEqual(context["dropped"]["count"], 0, "an added-back topic is no longer listed as dropped")


    def test_each_batch_answer_is_stored_as_it_arrives_so_a_killed_run_resumes(self):
        # P1-07: the batch cache was written once, at the end; a request killed at the
        # worker timeout kept nothing and the next attempt started from zero.
        project_id, _page_ids = self.make_project(SIX_DOCUMENTS)        # three batches
        original = self.respond

        def dying(*, task_type, **kwargs):
            if task_type == "project_section_generation" and self.calls.count(task_type) >= 2:
                self.calls.append(task_type)
                raise RuntimeError("the worker was killed")
            return original(task_type=task_type, **kwargs)

        self.respond = dying
        response = self.post(f"/projects/{project_id}/quick-start")
        self.assertEqual(response.status_code, 302)
        stored = self.analysis(project_id)
        self.assertEqual(len(stored["batches"]), 2, "the two answers that arrived are kept")
        self.assertEqual((stored["analysis_progress"]["done"], stored["analysis_progress"]["total"]), (2, 3))
        report = self.client.get(f"/projects/{project_id}/analysis").get_json()
        self.assertEqual(report["analysis_progress"], {"done": 2, "total": 3})
        self.respond = original
        before = self.extraction_calls()
        self.quick_start(project_id)
        self.assertEqual(self.extraction_calls() - before, 1, "only the batch that never answered is asked")
        self.assertEqual(len(self.sections(project_id)), 12)
        self.assertNotIn("analysis_progress", self.analysis(project_id))
        self.assertIsNone(self.client.get(f"/projects/{project_id}/analysis").get_json()["analysis_progress"])



class LenientValidatorTests(unittest.TestCase):
    def test_null_sections_with_a_topics_list_pass_like_the_app_reads_them(self):
        # Concern h: the app reads {"sections": null, "topics": [...]} as the topic list,
        # but the gateway's lenient validator failed it (and paid a corrective retry).
        from learnova.ai_services.contracts import VALIDATORS, AIValidationError

        validate = VALIDATORS["project_section_generation"]
        context = {"lenient_topics": True, "allow_empty": True}
        validate({"sections": None, "topics": [{"title": "Pflicht"}]}, context)
        validate({"topics": []}, context)
        self.assertEqual(application._topic_sections({"sections": None, "topics": [{"title": "Pflicht"}]}),
                         [{"title": "Pflicht"}])
        with self.assertRaises(AIValidationError):
            validate({"sections": "kaputt"}, context)


if __name__ == "__main__":
    unittest.main()
