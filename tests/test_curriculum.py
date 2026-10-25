"""Baden-Württemberg Bildungsplan grounding: sources gate, extraction, chunking, retrieval, prompts.

The fixture text below is written for these tests in the *shape* of a Bildungsplan
(numbered headings, band headings, competency statements); it is not quoted from one.
"""

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_curriculum_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova import curriculum  # noqa: E402
from learnova.curriculum import sources  # noqa: E402
from learnova.curriculum.extract import extract_html, extract_pdf, extract_text  # noqa: E402

FIXTURE = """# Mathematik Gymnasium (Testfassung)

3.1 Klassen 5/6

3.1.1 Zahl - Variable - Operation
Die Schülerinnen und Schüler können natürliche Zahlen addieren, subtrahieren und
schriftlich multiplizieren. Sie runden Zahlen sinnvoll und nutzen Überschlags-
rechnungen zur Kontrolle.

3.2 Klassen 7/8

3.2.1 Zahl - Variable - Operation
Die Schülerinnen und Schüler können Terme mit Variablen aufstellen, umformen und
lineare Gleichungen lösen. Sie begründen Umformungsschritte.

3.3 Klassen 9/10

3.3.1 Zahl - Variable - Operation
Die Schülerinnen und Schüler können Potenzen mit ganzzahligen Exponenten erklären und
die Potenzgesetze anwenden. Sie stellen sehr große und sehr kleine Zahlen in
wissenschaftlicher Schreibweise dar.
"""

FIXTURE_HTML = """<!doctype html><html><head><title>t</title><script>var x = 'Potenzgesetze';</script></head>
<body><nav>Startseite Bildungsplan Suche</nav>
<h1>Deutsch Sekundarstufe I</h1>
<h2>Klassen 7/8/9</h2>
<h3>3.2.1.1 Schreiben</h3>
<p>Die Schülerinnen und Schüler können eine Erörterung planen und Argumente ordnen.</p>
<ul><li>Stoffsammlung anlegen</li><li>Argumente gewichten</li></ul>
<footer>Impressum</footer></body></html>"""


class CatalogTests(unittest.TestCase):
    def test_band_headings(self):
        self.assertEqual(curriculum.band_from_text("3.2 Klassen 7/8"), (7, 8))
        self.assertEqual(curriculum.band_from_text("Klassen 7/8/9"), (7, 9))
        self.assertEqual(curriculum.band_from_text("Klasse 10"), (10, 10))
        self.assertEqual(curriculum.band_from_text("Klassen 5 und 6"), (5, 6))
        self.assertEqual(curriculum.band_from_text("Kursstufe (zweistündig)"), (11, 13))
        self.assertIsNone(curriculum.band_from_text("Prozessbezogene Kompetenzen"))
        self.assertEqual(curriculum.band_label(7, 8), "Klassen 7/8")
        self.assertEqual(curriculum.band_label(5, 10), "Klassen 5–10")
        self.assertEqual(curriculum.band_label(0, 0), "")

    def test_school_type_selects_plan_and_unset_type_still_grounds(self):
        self.assertEqual(curriculum.plans_for("realschule", 8), ("sek1",))
        self.assertEqual(curriculum.plans_for("gymnasium", 8), ("gymnasium",))
        self.assertEqual(curriculum.plans_for("", 3), ("grundschule",))
        self.assertEqual(curriculum.plans_for("", 8), ("gymnasium", "sek1"))
        self.assertEqual(curriculum.plans_for("outside_bw", 8), ())
        self.assertEqual(curriculum.plans_for("", None), ())

    def test_subject_mapping_follows_primary_and_bnt_structure(self):
        self.assertEqual(curriculum.subject_keys("Mathematics", 8), ("mathematik",))
        self.assertEqual(curriculum.subject_keys("Biology", 3), ("biologie", "sachunterricht"))
        self.assertEqual(curriculum.subject_keys("Physics", 6), ("physik", "bnt"))
        self.assertEqual(curriculum.subject_keys("Other", 8), ())


class ExtractionTests(unittest.TestCase):
    def test_text_keeps_heading_path_and_fixes_hyphenation(self):
        sections = extract_text(FIXTURE)
        top = sections[0]
        self.assertEqual(top.headings[-2:], ("3.1 Klassen 5/6", "3.1.1 Zahl - Variable - Operation"))
        self.assertIn("Überschlagsrechnungen", top.text)
        self.assertEqual(len(sections), 3)

    def test_html_skips_chrome_and_scripts(self):
        sections = extract_html(FIXTURE_HTML)
        text = " ".join(section.text for section in sections)
        self.assertNotIn("Startseite", text)
        self.assertNotIn("Impressum", text)
        self.assertNotIn("Potenzgesetze", text)
        self.assertEqual(sections[-1].headings,
                         ("Deutsch Sekundarstufe I", "Klassen 7/8/9", "3.2.1.1 Schreiben"))
        self.assertIn("- Argumente gewichten", sections[-1].text)

    def test_pdf_keeps_pages_and_drops_running_header(self):
        import fitz

        pdf = fitz.open()
        bodies = [
            "3.1 Klassen 5/6\nDie Schülerinnen und Schüler können Brüche vergleichen und ordnen.",
            "3.2 Klassen 7/8\nDie Schülerinnen und Schüler können lineare Gleichungen lösen.",
            "3.3 Klassen 9/10\nDie Schülerinnen und Schüler können Potenzgesetze anwenden.",
        ]
        for number, body in enumerate(bodies, start=1):
            page = pdf.new_page()  # pyright: ignore[reportAttributeAccessIssue]
            page.insert_text((72, 60), "Bildungsplan Testfassung")
            page.insert_text((72, 120), body)
            page.insert_text((72, 800), f"Seite {number}")
        sections = extract_pdf(pdf.tobytes())
        self.assertEqual([section.page for section in sections], [1, 2, 3])
        joined = " ".join(section.text for section in sections)
        self.assertNotIn("Testfassung", joined)
        self.assertNotIn("Seite", joined)
        self.assertEqual(sections[2].headings[-1], "3.3 Klassen 9/10")

    def test_format_detection(self):
        self.assertEqual(curriculum.detect_format("a.pdf"), "pdf")
        self.assertEqual(curriculum.detect_format("", "text/html; charset=utf-8"), "html")
        self.assertEqual(curriculum.detect_format("x", "", b"%PDF-1.7"), "pdf")
        self.assertEqual(curriculum.detect_format("notes.md"), "text")


class ChunkingTests(unittest.TestCase):
    def test_chunks_inherit_band_and_cite_their_heading(self):
        chunks = curriculum.chunk_sections(extract_text(FIXTURE), default_band=(5, 10))
        self.assertEqual([(chunk.grade_min, chunk.grade_max) for chunk in chunks], [(5, 6), (7, 8), (9, 10)])
        self.assertIn("3.3.1 Zahl - Variable - Operation", chunks[2].heading_path)
        # PDF-style line breaks inside a paragraph are joined for the prompt.
        self.assertNotIn("\n", chunks[2].text)

    def test_document_band_is_the_fallback(self):
        chunks = curriculum.chunk_sections(extract_text("Leitgedanken\nDas Fach Mathematik fördert Problemlösen und Argumentieren im Alltag."), default_band=(5, 10))
        self.assertEqual((chunks[0].grade_min, chunks[0].grade_max), (5, 10))

    def test_long_section_splits_below_the_maximum(self):
        long_text = "3.1 Klassen 5/6\n" + " ".join(f"Satz {n} über Brüche und Dezimalzahlen." for n in range(300))
        chunks = curriculum.chunk_sections(extract_text(long_text))
        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(len(chunk.text) <= 1500 for chunk in chunks))


def _index(chunks, plan="gymnasium", subject="mathematik"):
    return curriculum.CurriculumIndex(
        curriculum.IndexedChunk(
            id=number, document_id=1, plan=plan, subject=subject, grade_min=chunk.grade_min,
            grade_max=chunk.grade_max, heading_path=chunk.heading_path, text=chunk.text,
            page=chunk.page, citation="Testplan Mathematik")
        for number, chunk in enumerate(chunks, start=1))


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.index = _index(curriculum.chunk_sections(extract_text(FIXTURE)))

    def test_topic_words_find_their_band_with_german_folding(self):
        hits = self.index.search("Potenzgesetze anwenden", plans=["gymnasium"], subjects=["mathematik"], grade=8)
        self.assertEqual(hits[0].chunk.grade_min, 9)
        self.assertFalse(hits[0].at_grade)
        hits = self.index.search("lineare gleichungen", plans=["gymnasium"], subjects=["mathematik"], grade=8)
        self.assertEqual(hits[0].chunk.grade_min, 7)
        self.assertTrue(hits[0].at_grade)

    def test_learner_grade_breaks_ties_between_bands(self):
        text = (
            "3.1 Klassen 5/6\nBrüche vergleichen und ordnen.\n"
            "3.2 Klassen 7/8\nTerme aufstellen und umformen.\n"
            "3.3 Klassen 9/10\nBrüche vergleichen und ordnen.\n"
            "3.4 Kursstufe\nAbleitungen untersuchen.\n"
        )
        index = _index(curriculum.chunk_sections(extract_text(text)))
        for grade, band in ((6, 5), (10, 9)):
            hits = index.search("Brüche vergleichen", plans=["gymnasium"], subjects=["mathematik"], grade=grade)
            self.assertEqual([hit.chunk.grade_min for hit in hits][0], band)
            self.assertEqual(len(hits), 2)

    def test_filters_and_unrelated_queries_return_nothing(self):
        self.assertEqual(self.index.search("Potenzgesetze", plans=["sek1"], subjects=["mathematik"], grade=8), [])
        self.assertEqual(self.index.search("Potenzgesetze", plans=["gymnasium"], subjects=["deutsch"], grade=8), [])
        self.assertEqual(self.index.search("Photosynthese Chloroplast", plans=["gymnasium"], subjects=["mathematik"], grade=8), [])
        self.assertEqual(self.index.search("", plans=["gymnasium"], subjects=["mathematik"], grade=8), [])


class ContextBlockTests(unittest.TestCase):
    def test_block_cites_and_flags_a_later_grade(self):
        index = _index(curriculum.chunk_sections(extract_text(FIXTURE)))
        hits = index.search("Potenzgesetze", plans=["gymnasium"], subjects=["mathematik"], grade=8)
        block = curriculum.build_block(hits, grade=8, school_type="gymnasium")
        self.assertIn("BILDUNGSPLAN", block)
        self.assertIn("Klasse 8", block)
        self.assertIn("[1] Testplan Mathematik · Gymnasium · Klassen 9/10", block)
        self.assertIn("LATER than the learner's grade", block)
        self.assertIn("never invent", block)

    def test_budget_is_respected_and_empty_without_hits(self):
        long_text = "3.1 Klassen 5/6\n" + " ".join(f"Brüche Satz {n} über Brüche." for n in range(400))
        index = _index(curriculum.chunk_sections(extract_text(long_text)))
        hits = index.search("Brüche", plans=["gymnasium"], subjects=["mathematik"], grade=6, limit=3)
        block = curriculum.build_block(hits, grade=6, school_type="gymnasium", max_chars=1500)
        self.assertLessEqual(len(block), 1500)
        self.assertEqual(curriculum.build_block([], grade=6, school_type="gymnasium"), "")

    def test_sek1_block_names_the_niveau(self):
        index = _index(curriculum.chunk_sections(extract_text(FIXTURE)), plan="sek1")
        hits = index.search("lineare Gleichungen", plans=["sek1"], subjects=["mathematik"], grade=8)
        self.assertIn("Niveau M", curriculum.build_block(hits, grade=8, school_type="realschule"))


class _Response:
    def __init__(self, status=200, text="", content=b"", content_type="text/html"):
        self.status_code, self.text, self.content = status, text, content
        self.headers = {"Content-Type": content_type}

    def iter_content(self, size):
        yield self.content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Session:
    def __init__(self, robots, document=None):
        self.robots, self.document, self.requested = robots, document, []

    def get(self, url, **kwargs):
        self.requested.append(url)
        if url.endswith("/robots.txt"):
            return self.robots
        return self.document


BW_ROBOTS = "User-agent: *\nDisallow: */recommend/*\nDisallow: /*.pdf$\nCrawl-Delay: 2\n"


class SourcePolicyTests(unittest.TestCase):
    def test_uncleared_official_host_is_refused_with_the_known_reason(self):
        with self.assertRaises(curriculum.SourceNotCleared) as caught:
            curriculum.check_provenance("https://www.bildungsplaene-bw.de/x", frozenset())
        self.assertIn("robots.txt disallows *.pdf", str(caught.exception))
        with self.assertRaises(curriculum.SourceNotCleared) as caught:
            curriculum.check_provenance("https://www.schule-bw.de/x", frozenset())
        self.assertIn("shut down", str(caught.exception))

    def test_cleared_host_and_rights_noted_local_source_pass(self):
        cleared = curriculum.parse_cleared(" www.Bildungsplaene-BW.de , ")
        self.assertEqual(cleared, frozenset({"bildungsplaene-bw.de"}))
        curriculum.check_provenance("https://www.bildungsplaene-bw.de/x", cleared)
        with self.assertRaises(curriculum.SourceNotCleared):
            curriculum.check_provenance("Eigene Notizen Klasse 8", frozenset())
        curriculum.check_provenance("Eigene Notizen Klasse 8", frozenset(), rights_note="Own work, M. R.")
        with self.assertRaises(curriculum.SourceNotCleared):
            curriculum.check_provenance("", cleared)

    def test_robots_pdf_rule_blocks_fetch_even_when_cleared(self):
        session = _Session(_Response(text=BW_ROBOTS))
        with self.assertRaises(curriculum.RobotsDisallowed):
            curriculum.fetch("https://www.bildungsplaene-bw.de/doc/BP2016BW_M.pdf",
                             cleared=frozenset({"bildungsplaene-bw.de"}), session=session, sleep=lambda _: None)
        self.assertEqual(len(session.requested), 1)  # only robots.txt was requested

    def test_allowed_page_waits_for_crawl_delay(self):
        session = _Session(_Response(text=BW_ROBOTS), _Response(content=FIXTURE_HTML.encode()))
        delays = []
        download = curriculum.fetch("https://www.bildungsplaene-bw.de/,Lde/node/1", cleared=frozenset({"bildungsplaene-bw.de"}),
                                    session=session, sleep=delays.append)
        self.assertEqual(delays, [2.0])
        self.assertIn("Erörterung".encode(), download.content)

    def test_unreachable_robots_is_a_refusal_but_404_is_not(self):
        self.assertFalse(sources.robots_allows("https://example.org/a", session=_Session(_Response(status=503)))[0])
        self.assertTrue(sources.robots_allows("https://example.org/a", session=_Session(_Response(status=404)))[0])

    def test_uncleared_fetch_makes_no_request(self):
        session = _Session(_Response(text=""))
        with self.assertRaises(curriculum.SourceNotCleared):
            curriculum.fetch("https://example.org/plan.html", cleared=frozenset(), session=session)
        self.assertEqual(session.requested, [])


class _FakeResponse:
    def __init__(self, text):
        self.output_text = text


LESSON = """{"lesson_title":"Potenzen","detected_level":"Klasse 8","concepts":[{"name":"Potenzgesetze","evidence":"goal"}],
"explanation":"Erklärung","worked_example":{"problem":"$4^2 \\\\cdot 4^3$","steps":["Basen gleich"],"answer":"$4^5$"},
"teacher_tips":["Tipp"],"exceptions":[],"video_usefulness":0,"videos":[],"image_usefulness":0,"image_search_terms":[],
"question":{"id":"q1","concept":"Potenzgesetze","difficulty":1,"type":"multiple_choice","prompt":"Was ist 2^3?","hint":"h",
"options":[{"id":"a","label":"6"},{"id":"b","label":"8"},{"id":"c","label":"9"},{"id":"d","label":"5"}],"expected_answer":"b"}}"""


class CurriculumIntegrationTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True, CURRICULUM_CLEARED_SOURCES="", FEATURE_CURRICULUM=True)
        application.SESSIONS.clear()
        application._curriculum_index_cache.update(key=None, index=None)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()

    def _import(self, **overrides):
        values: dict[str, Any] = dict(content=FIXTURE.encode(), fmt="text", source_url="Testfixture",
                      rights_note="Written for the test suite", plan="gymnasium", subject="mathematik",
                      title="Mathematik Testplan", citation="Testplan Mathematik", grades=(5, 10))
        values.update(overrides)
        with application.app.app_context():
            document, created = application.import_curriculum_document(**values)
            return document.id, created, len(document.chunks)  # pyright: ignore[reportArgumentType]

    def _student(self, grade="8", school_type="gymnasium"):
        self.client.post("/register", data={"username": "lina", "email": "lina@example.com",
                                            "password": "correct-horse-battery"})
        self.client.post("/settings/grade", data={"grade": grade})
        if school_type:
            self.client.post("/settings/school-type", data={"school_type": school_type})

    def _lesson_instructions(self, goal="Potenzgesetze anwenden"):
        captured = {}

        def capture(**kwargs):
            captured.update(kwargs)
            return _FakeResponse(LESSON)

        with patch.object(application, "create_response", side_effect=capture):
            response = self.client.post("/api/analyze", data={"study_goal": goal, "subject": "Mathematics"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        return captured["instructions"]

    def test_import_gate_dedupe_and_web_source_refusal(self):
        document_id, created, chunks = self._import()
        self.assertTrue(created)
        self.assertEqual(chunks, 3)
        again_id, created, _ = self._import()
        self.assertEqual((again_id, created), (document_id, False))
        with self.assertRaises(curriculum.SourceNotCleared):
            self._import(source_url="https://www.bildungsplaene-bw.de/plan", content=b"other")
        with self.assertRaises(ValueError):
            self._import(plan="hauptschule", content=b"x")

    def test_lesson_prompt_carries_matching_excerpt_for_the_learner(self):
        self._import()
        self._student(grade="8", school_type="gymnasium")
        instructions = self._lesson_instructions()
        self.assertIn("BADEN-WÜRTTEMBERG BILDUNGSPLAN", instructions)
        self.assertIn("Potenzgesetze", instructions)
        self.assertIn("Klassen 9/10", instructions)
        self.assertIn("LATER than the learner's grade", instructions)
        # The notation mandate stays the last thing the model reads.
        self.assertLess(instructions.index("BILDUNGSPLAN"), instructions.index("MATH FORMATTING IS MANDATORY"))

    def test_no_documents_or_outside_bw_leaves_the_prompt_unchanged(self):
        self._student(grade="8", school_type="gymnasium")
        self.assertNotIn("BILDUNGSPLAN", self._lesson_instructions())
        self._import()
        self.client.post("/settings/school-type", data={"school_type": "outside_bw"})
        self.assertNotIn("BILDUNGSPLAN", self._lesson_instructions())

    def test_wrong_plan_or_flag_off_gives_no_excerpt(self):
        self._import()
        self._student(grade="8", school_type="realschule")
        self.assertNotIn("BILDUNGSPLAN", self._lesson_instructions())
        self.client.post("/settings/school-type", data={"school_type": "gymnasium"})
        application.app.config["FEATURE_CURRICULUM"] = False
        try:
            self.assertNotIn("BILDUNGSPLAN", self._lesson_instructions())
        finally:
            application.app.config["FEATURE_CURRICULUM"] = True

    def test_index_refreshes_after_a_new_import(self):
        self._student(grade="8", school_type="gymnasium")
        self._import()
        self.assertIn("Potenzgesetze", self._lesson_instructions())
        self._import(content=FIXTURE_HTML.encode(), fmt="html", subject="deutsch", plan="gymnasium",
                     title="Deutsch Testplan")
        captured = {}
        with patch.object(application, "create_response",
                          side_effect=lambda **kwargs: captured.update(kwargs) or _FakeResponse(LESSON)):
            self.client.post("/api/analyze", data={"study_goal": "Erörterung schreiben", "subject": "German"})
        self.assertIn("Erörterung", captured["instructions"])

    def test_school_type_setting_validates_and_renders(self):
        self._student(grade="8", school_type="")
        bad = self.client.post("/settings/school-type", data={"school_type": "hogwarts"})
        self.assertEqual(bad.status_code, 302)
        self.client.post("/settings/school-type", data={"school_type": "gemeinschaftsschule"})
        with application.app.app_context():
            user = application.db.session.execute(application.db.select(application.User)).scalar_one()
            self.assertEqual(user.school_type, "gemeinschaftsschule")
        page = self.client.get("/settings").get_data(as_text=True)
        self.assertIn('value="gemeinschaftsschule" selected', page)
        self.assertIn("Baden-Württemberg Bildungsplan", page)

    def test_cli_import_list_search_remove(self):
        runner = application.app.test_cli_runner()
        path = Path(tempfile.mkdtemp()) / "plan.md"
        path.write_text(FIXTURE, encoding="utf-8")
        refused = runner.invoke(args=["curriculum", "import", str(path), "--source-url",
                                      "https://www.bildungsplaene-bw.de/x", "--plan", "gymnasium",
                                      "--subject", "mathematik"])
        self.assertNotEqual(refused.exit_code, 0)
        self.assertIn("CURRICULUM_CLEARED_SOURCES", refused.output)
        imported = runner.invoke(args=["curriculum", "import", str(path), "--source-url", "Testfixture",
                                       "--rights-note", "test", "--plan", "gymnasium", "--subject",
                                       "mathematik", "--grades", "5-10", "--citation", "Testplan"])
        self.assertEqual(imported.exit_code, 0, imported.output)
        self.assertIn("3 chunks", imported.output)
        self.assertIn("gymnasium/mathematik", runner.invoke(args=["curriculum", "list"]).output)
        found = runner.invoke(args=["curriculum", "search", "Potenzgesetze", "--school-type", "gymnasium",
                                    "--grade", "9"])
        self.assertIn("at the learner's grade", found.output)
        self.assertEqual(runner.invoke(args=["curriculum", "remove", "1"]).exit_code, 0)
        self.assertIn("No matching", runner.invoke(args=["curriculum", "search", "Potenzgesetze",
                                                         "--grade", "9"]).output)


if __name__ == "__main__":
    unittest.main()
