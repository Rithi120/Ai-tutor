"""Topic analysis in short steps, one analysis per project at a time (FIX_PLAN PF2).

The production risks this file pins (USER_SPEC_PROD risk 6 and 7, docrel DR-07):

* the whole topic analysis of a 40-page project ran inside one web request (10+ AI calls,
  165-440 s with real latencies). A reverse proxy answers 504 long before that, the
  start page offered "Try again", and the second tap started a second, parallel paid run;
* nothing stopped two analyses of one project at once (a second tab, a retry): every
  batch was paid twice.

What is checked, through the real routes with `app.create_response` patched:

* POST /projects/<id>/analysis/step makes at most ANALYSIS_STEP_CALLS extraction calls,
  stores a consistent topic state each time and says {done, total, finished};
* the steps end with the same pages and topics a one-shot run finds, and quick-start
  afterwards pays nothing;
* a bisected batch pauses and resumes from its split marker, and costs what the one-shot
  run costs; a page that failed alone is not asked again in the same round;
* a per-project lease: a second analysis while one runs gets 202 analysis_running (JSON)
  or a calm message (HTML) and makes no call; a lease left behind by a killed request is
  taken over once it expires; two concurrent requests -> exactly one runs;
* a provider stop keeps the topics found so far, is not knocked on again for 5 minutes,
  and resumes after; a fresh project whose first step is rate-limited creates nothing;
* the lease is released when the step fails;
* "Update topics" goes through the start page (then=topics) and ends on the topics, never
  in a lesson; new topics of a round are shown before the next lesson;
* the no-JS quick-start still completes in one request;
* migration 041 adds the lease columns to an existing database, once.
"""

import json
import re
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import event, inspect, text

import app as application  # noqa: E402  (conftest set DATABASE_URL first)
from learnova.ai_services import service as ai_service
from tests.test_project_extraction import (
    ETHICS, PASSWORD, SIX_DOCUMENTS, ExtractionTestCase, batch_of, page_text,
)

db = application.db
BUSY = "Learnova is already working out the topics of this project. This page updates by itself."
STEP_HEADERS = {"Accept": "application/json"}
LIST_KEYS = ("analysed_page_ids", "failed_page_ids", "not_analysed_page_ids", "uncovered_page_ids",
             "no_topic_page_ids")


def forty_texts():
    """40 distinct pages (10 batches of four): the twelve Ethics pages again and again, each
    with its own notes, so no page is a duplicate of another."""

    texts = []
    for number in range(40):
        extra = " ".join(f"Notiz{number}wort{index}" for index in range(45))
        texts.append(f"{page_text(number % len(ETHICS))}\nZusatz {number}: {extra}.")
    return texts


class StepTestCase(ExtractionTestCase):
    def setUp(self):
        super().setUp()
        saved = {key: application.app.config.get(key) for key in (
            "ANALYSIS_STEP_CALLS", "ANALYSIS_LEASE_SECONDS", "WTF_CSRF_ENABLED", "TESTING")}
        application.app.config.update(ANALYSIS_STEP_CALLS=2, ANALYSIS_LEASE_SECONDS=150)
        self.addCleanup(application.app.config.update, saved)

    # ---------------------------------------------------------------- helpers
    def step(self, project_id, **body):
        """(response, json, extraction calls this request made)."""

        before = self.extraction_calls()
        response = self.post(f"/projects/{project_id}/analysis/step", json=body, headers=STEP_HEADERS)
        return response, response.get_json(silent=True) or {}, self.extraction_calls() - before

    def run_steps(self, project_id, *, limit=40, **body):
        """Steps until one says finished: [(calls, json)] per step."""

        rounds = []
        for _ in range(limit):
            response, data, calls = self.step(project_id, **body)
            self.assertEqual(response.status_code, 200, data)
            rounds.append((calls, data))
            if data.get("finished"):
                return rounds
        self.fail(f"the steps never finished: {rounds[-3:]}")

    def quick_start_json(self, project_id, **body):
        before = self.extraction_calls()
        response = self.post(f"/projects/{project_id}/quick-start", json=body, headers=STEP_HEADERS)
        return response, response.get_json(silent=True) or {}, self.extraction_calls() - before

    def make_texts_project(self, texts, *, per_file=4):
        """A project of read pages with exactly these texts (files of `per_file` pages)."""

        with application.app.app_context():
            project = application.LearningProject(user_id=self.user_id(), title="Ethik", subject="Ethics",
                                                  status="uploaded")
            db.session.add(project)
            db.session.flush()
            page_ids = []
            for start in range(0, len(texts), per_file):
                file = application.ProjectFile(project_id=project.id, original_filename=f"teil-{start}.pdf",
                                               mime_type="application/pdf", original_data=b"%PDF", sha256=str(start))
                db.session.add(file)
                db.session.flush()
                for offset, value in enumerate(texts[start:start + per_file]):
                    page = application.ProjectPage(
                        project_id=project.id, file_id=file.id, page_number=offset + 1, page_order=start + offset + 1,
                        extracted_text=value, extraction_status="ready", confidence_status="high",
                        recognition_confidence=0.95, review_status="pending",
                        recognition_json=json.dumps({"text_source": "text_layer"}))
                    db.session.add(page)
                    db.session.flush()
                    page_ids.append(page.id)
            db.session.commit()
            return project.id, page_ids

    def add_pages(self, project_id, indexes):
        """Pages added later to a project that has topics (a new document)."""

        with application.app.app_context():
            project = self.project(project_id)
            order = max(page.page_order for page in project.pages)
            file = application.ProjectFile(project_id=project.id, original_filename="neu.pdf",
                                           mime_type="application/pdf", original_data=b"%PDF", sha256="neu")
            db.session.add(file)
            db.session.flush()
            added = []
            for number, index in enumerate(indexes, start=1):
                page = application.ProjectPage(
                    project_id=project.id, file_id=file.id, page_number=number, page_order=order + number,
                    extracted_text=page_text(index), extraction_status="ready", confidence_status="high",
                    recognition_confidence=0.95, review_status="pending",
                    recognition_json=json.dumps({"text_source": "text_layer"}))
                db.session.add(page)
                db.session.flush()
                added.append(page.id)
            application.mark_project_pages_changed(project)
            db.session.commit()
            return added

    def hold_lease(self, project_id, *, token="another-tab", seconds=120):
        """Another request holds the project's analysis lease (or held it, when seconds < 0)."""

        with application.app.app_context():
            project = self.project(project_id)
            project.analysis_lease_token = token
            project.analysis_lease_until = application.utcnow() + timedelta(seconds=seconds)
            db.session.commit()

    def lease(self, project_id):
        with application.app.app_context():
            project = self.project(project_id)
            return project.analysis_lease_token, project.analysis_lease_until

    def age_analysis(self, project_id, minutes):
        """Move every stored analysis time `minutes` into the past (time passing)."""

        def older(value):
            parsed = datetime.fromisoformat(str(value))
            return (parsed - timedelta(minutes=minutes)).isoformat()

        with application.app.app_context():
            project = self.project(project_id)
            analysis = json.loads(project.analysis_json)
            for key in ("analyzed_at", "analysis_stopped_at"):
                if analysis.get(key):
                    analysis[key] = older(analysis[key])
            for key in ("analysis_round", "analysis_progress"):
                for name in ("at", "started"):
                    if isinstance(analysis.get(key), dict) and analysis[key].get(name):
                        analysis[key][name] = older(analysis[key][name])
            project.analysis_json = json.dumps(analysis)
            db.session.commit()

    def flashes(self):
        with self.client.session_transaction() as session:
            return [message for _category, message in session.get("_flashes", [])]

    def comparable(self, project_id, page_ids):
        """Page lists (as page positions) and topics (title, key, page positions)."""

        position = {page_id: index for index, page_id in enumerate(page_ids)}
        analysis = self.analysis(project_id)
        lists = {key: [position[page_id] for page_id in analysis.get(key, [])] for key in LIST_KEYS}
        with application.app.app_context():
            project = self.project(project_id)
            topics = [(section.title, section.topic_key, [position[value] for value in json.loads(
                section.source_page_ids_json)], section.excluded)
                for section in sorted(project.sections, key=lambda item: item.position)]
        return lists, topics


class StepBudgetTests(StepTestCase):
    def test_three_batches_take_three_one_call_steps_and_quick_start_then_pays_nothing(self):
        application.app.config["ANALYSIS_STEP_CALLS"] = 1
        project_id, page_ids = self.make_project(SIX_DOCUMENTS)
        rounds = self.run_steps(project_id)
        self.assertEqual([calls for calls, _ in rounds], [1, 1, 1], "one batch per request")
        self.assertEqual([(data["done"], data["total"], data["finished"]) for _, data in rounds],
                         [(1, 3, False), (2, 3, False), (3, 3, True)])
        self.assertTrue(all(data["has_topics"] for _, data in rounds), "each step leaves usable topics")
        self.assertEqual(len(self.sections(project_id)), 4 * 3)
        response, data, calls = self.step(project_id)
        self.assertEqual((response.status_code, data["finished"], data["needed"], calls), (200, True, False, 0))
        response, data, calls = self.quick_start_json(project_id)
        self.assertEqual(response.status_code, 200, data)
        self.assertEqual(calls, 0, "quick-start finds every batch analysed")
        self.assertIn("#topics", data["redirect"], "the student sees the topics first")
        # The same input analysed in one request ends in the same state.
        other_id, other_pages = self.make_project(SIX_DOCUMENTS)
        self.quick_start(other_id)
        self.assertEqual(self.comparable(project_id, page_ids), self.comparable(other_id, other_pages))

    def test_forty_pages_never_take_more_than_the_step_budget_per_request(self):
        project_id, page_ids = self.make_texts_project(forty_texts())
        rounds = self.run_steps(project_id)
        calls = [count for count, _ in rounds]
        self.assertLessEqual(max(calls), 2, f"no request does more than ANALYSIS_STEP_CALLS: {calls}")
        self.assertEqual(sum(calls), 10, "ten batches, each paid once")
        self.assertEqual(rounds[-1][1]["total"], 10)
        self.assertEqual(rounds[-1][1]["done"], 10)
        analysis = self.analysis(project_id)
        self.assertEqual(analysis["analysed_page_ids"], page_ids)
        self.assertEqual(analysis["not_analysed_page_ids"], [])
        self.assertNotIn("analysis_paused", analysis)
        self.assertEqual(analysis.get("analysis_stopped"), "", "a budget pause is never a provider stop")
        response, data, quick_calls = self.quick_start_json(project_id)
        self.assertEqual((response.status_code, quick_calls), (200, 0), data)

    def test_a_paused_step_is_not_a_provider_stop(self):
        project_id, _ = self.make_project(SIX_DOCUMENTS)
        _response, data, calls = self.step(project_id)
        self.assertEqual((calls, data["finished"]), (2, False))
        analysis = self.analysis(project_id)
        self.assertEqual(analysis["analysis_paused"], "step_budget")
        self.assertFalse(analysis.get("analysis_stopped"))
        with application.app.app_context():
            self.assertFalse(application.topic_analysis_recently_stopped(analysis))
        self.assertEqual(len(analysis["not_analysed_page_ids"]), 4, "the third batch waits for the next step")
        self.assertNotIn("analysis_progress", analysis, "nothing claims to be running between steps")

    def test_a_bisected_batch_pauses_resumes_from_its_split_marker_and_costs_what_one_run_costs(self):
        project_id, page_ids = self.make_project(SIX_DOCUMENTS)
        self.broken_pages = {page_ids[5]}            # batch 2 fails, then its half, then the page alone
        rounds = self.run_steps(project_id)
        calls = [count for count, _ in rounds]
        self.assertEqual(calls[0], 2)
        self.assertFalse(rounds[0][1]["finished"], "the step pauses after two calls")
        self.assertLessEqual(max(calls), 2)
        step_total = sum(calls)
        analysis = self.analysis(project_id)
        self.assertEqual(analysis["failed_page_ids"], [page_ids[5]])
        _response, data, again = self.step(project_id)
        self.assertEqual((again, data["finished"]), (0, True), "the failed page is not asked again this round")
        _response, data, quick_calls = self.quick_start_json(project_id)
        self.assertEqual(quick_calls, 0, data)
        # The one-shot run on the same input.
        other_id, other_pages = self.make_project(SIX_DOCUMENTS)
        self.broken_pages = {other_pages[5]}
        before = self.extraction_calls()
        self.quick_start(other_id)
        self.assertEqual(step_total, self.extraction_calls() - before, "the steps pay what one run pays")
        self.assertEqual(self.comparable(project_id, page_ids), self.comparable(other_id, other_pages))

    def test_a_killed_step_resumes_without_paying_again_for_the_batch_it_finished(self):
        project_id, _ = self.make_project(SIX_DOCUMENTS)
        original = self.respond

        def dying(*, task_type, **kwargs):
            if task_type == "project_section_generation" and self.calls.count(task_type) >= 1:
                self.calls.append(task_type)
                raise RuntimeError("the worker was killed")
            return original(task_type=task_type, **kwargs)

        self.respond = dying
        response, _data, calls = self.step(project_id)
        self.assertEqual(calls, 2)
        self.assertGreaterEqual(response.status_code, 500)
        self.assertEqual(self.lease(project_id), ("", None), "a failed step releases the lease")
        self.assertEqual(len(self.analysis(project_id)["batches"]), 1, "the answer that arrived is kept")
        self.respond = original
        rounds = self.run_steps(project_id)
        self.assertEqual(sum(count for count, _ in rounds), 2, "only the two batches that never answered")
        self.assertEqual(len(self.sections(project_id)), 12)

    def test_the_step_is_owner_only_and_needs_the_csrf_token(self):
        project_id, _ = self.make_project([[0, 1]])
        other = application.app.test_client()
        other.post("/register", data={"username": "max", "email": "max@example.com", "password": PASSWORD,
                                      "grade": "10"})
        with patch.object(application, "create_response", side_effect=self.respond):
            refused = other.post(f"/projects/{project_id}/analysis/step", json={}, headers=STEP_HEADERS)
        self.assertEqual(refused.status_code, 404)
        self.assertEqual(self.extraction_calls(), 0)
        application.app.config.update(TESTING=False, WTF_CSRF_ENABLED=True)
        response, data, calls = self.step(project_id)
        self.assertEqual((response.status_code, data.get("code"), calls), (400, "csrf_failed", 0))
        page = self.client.get(f"/projects/{project_id}/start").get_data(as_text=True)
        found = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
        assert found is not None, "the page carries the CSRF token for scripts"
        token = found.group(1)
        with patch.object(application, "create_response", side_effect=self.respond):
            accepted = self.client.post(f"/projects/{project_id}/analysis/step", json={},
                                        headers={**STEP_HEADERS, "X-CSRFToken": token})
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.headers.get("Cache-Control"), "no-store")


class LeaseTests(StepTestCase):
    def test_a_second_analysis_while_one_runs_gets_202_and_makes_no_call(self):
        project_id, _ = self.make_project(SIX_DOCUMENTS)
        self.hold_lease(project_id)
        response, data, calls = self.step(project_id)
        self.assertEqual((response.status_code, data.get("code"), calls), (202, "analysis_running", 0))
        self.assertEqual(response.headers.get("Retry-After"), "3")
        self.assertEqual(set(data["details"]), {"done", "total"})
        response, data, calls = self.quick_start_json(project_id)
        self.assertEqual((response.status_code, data.get("code"), calls), (202, "analysis_running", 0))
        # Without JavaScript: a calm message and the start page, which updates by itself.
        page = self.post(f"/projects/{project_id}/quick-start")
        self.assertEqual(page.status_code, 302)
        self.assertIn(f"/projects/{project_id}/start", page.headers["Location"])
        self.assertIn("auto=1", page.headers["Location"])
        self.assertIn(BUSY, self.flashes())
        self.assertEqual(self.extraction_calls(), 0)
        self.assertEqual(self.lease(project_id)[0], "another-tab", "the other request keeps its lease")

    def test_update_topics_and_exam_planning_wait_for_a_running_analysis_too(self):
        project_id, _ = self.make_project([[0, 1], [2, 3]])
        self.quick_start(project_id)
        self.add_pages(project_id, [4, 5])
        self.hold_lease(project_id)
        before = self.extraction_calls()
        response = self.post(f"/projects/{project_id}/reanalyze")
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/projects/{project_id}/start", response.headers["Location"])
        self.assertIn(BUSY, self.flashes())
        exam = (application.date.today() + timedelta(days=20)).isoformat()
        response = self.post(f"/projects/{project_id}/autopilot", data={"exam_date": exam})
        self.assertEqual(response.status_code, 302)
        self.assertIn(BUSY, self.flashes())
        self.assertEqual(self.extraction_calls(), before)

    def test_a_lease_left_by_a_killed_request_is_taken_over_once_it_expires(self):
        project_id, _ = self.make_project(SIX_DOCUMENTS)
        self.hold_lease(project_id, token="killed-request", seconds=-1)
        response, data, calls = self.step(project_id)
        self.assertEqual((response.status_code, calls), (200, 2), data)
        self.assertEqual(self.lease(project_id), ("", None), "released after the step")

    def test_the_lease_is_renewed_before_each_extraction_call(self):
        project_id, _ = self.make_project(SIX_DOCUMENTS)
        seen = []
        original = self.respond

        def watching(*, task_type, **kwargs):
            if task_type == "project_section_generation":
                with application.app.app_context():
                    token, until = self.lease(project_id)
                seen.append((bool(token), until is not None and application.as_utc(until) > application.utcnow()))
            return original(task_type=task_type, **kwargs)

        self.respond = watching
        self.step(project_id)
        self.assertEqual(seen, [(True, True), (True, True)], "held and fresh during every call")

    def test_two_concurrent_steps_one_runs_and_the_other_gets_202(self):
        project_id, _ = self.make_project(SIX_DOCUMENTS)
        second = application.app.test_client()
        second.post("/login", data={"identifier": "lea", "password": PASSWORD})
        barrier = threading.Barrier(2)
        refused = threading.Event()
        original = self.respond

        def slow(*, task_type, **kwargs):
            # The winner waits inside its first AI call until the other request has its answer.
            refused.wait(10)
            return original(task_type=task_type, **kwargs)

        results = {}

        def worker(name, client):
            barrier.wait()
            response = client.post(f"/projects/{project_id}/analysis/step", json={}, headers=STEP_HEADERS)
            results[name] = (response.status_code, response.get_json(silent=True) or {})
            if response.status_code == 202:
                refused.set()

        with patch.object(application, "create_response", side_effect=slow):
            threads = [threading.Thread(target=worker, args=("a", self.client)),
                       threading.Thread(target=worker, args=("b", second))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(60)
        refused.set()
        self.assertEqual(sorted(code for code, _ in results.values()), [200, 202], results)
        self.assertEqual(self.extraction_calls(), 2, "only the request holding the lease paid")

    def test_a_second_quick_start_during_the_first_analysis_does_not_pay_twice(self):
        # docrel DR-07 (repro_defects.py::test_DR07_*): a proxy 504 or a second tab, and the
        # student taps "Try again" while the first request still analyses.
        project_id, _ = self.make_project([[0, 1, 2, 3], [4, 5, 6, 7]])
        original = self.respond
        state = {}

        def second_tab(*, task_type, **kwargs):
            if task_type == "project_section_generation" and "second" not in state:
                state["second"] = True
                response = self.client.post(f"/projects/{project_id}/quick-start", json={}, headers=STEP_HEADERS)
                state["second_http"] = response.status_code
                state["second_code"] = (response.get_json(silent=True) or {}).get("code")
            return original(task_type=task_type, **kwargs)

        self.respond = second_tab
        response = self.post(f"/projects/{project_id}/quick-start", json={}, headers=STEP_HEADERS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual((state["second_http"], state["second_code"]), (202, "analysis_running"))
        self.assertEqual(self.extraction_calls(), 2, "two batches, paid once")
        self.assertEqual(len(self.sections(project_id)), 8)

    def test_the_lease_is_re_entrant_within_one_request(self):
        project_id, _ = self.make_project([[0, 1]])
        with application.app.test_request_context():
            project = self.project(project_id)
            with application.analysis_lease(project):
                with application.analysis_lease(project):
                    self.assertTrue(self.project(project_id).analysis_lease_token)
                self.assertTrue(self.project(project_id).analysis_lease_token, "the inner block keeps it")
            db.session.expire_all()
            self.assertEqual(self.project(project_id).analysis_lease_token, "")

    def test_waiting_for_another_tab_does_not_use_up_the_hourly_steps(self):
        # Review A F6: every 202 poll counted against the step's 120 an hour, so a tab that
        # waited for another one ran into 429 and the start page fell back to one long request.
        project_id, _ = self.make_project([[0, 1]])
        self.hold_lease(project_id, seconds=600)
        application.app.config.update(TESTING=False, WTF_CSRF_ENABLED=False)
        application.limiter.enabled = True
        application.limiter.reset()
        try:
            busy = [self.step(project_id)[0].status_code for _ in range(125)]
            self.hold_lease(project_id, token="", seconds=-1)
            response, data, calls = self.step(project_id)
        finally:
            application.app.config.update(TESTING=True)
            application.limiter.enabled = False
            application.limiter.reset()
        self.assertEqual(busy, [202] * 125)
        self.assertEqual((response.status_code, calls), (200, 1), data)
        source = (Path(application.__file__).parent / "app.py").read_text(encoding="utf-8")
        limits = source.split('@app.post("/projects/<int:project_id>/analysis/step")\n', 1)[1].split("\ndef ", 1)[0]
        self.assertIn("deduct_when=", limits, "only real steps count against the 120 an hour")
        self.assertEqual(limits.count("key_func=user_rate_key"), limits.count("@limiter.limit("))


class StopTests(StepTestCase):
    def test_a_provider_stop_keeps_the_topics_waits_five_minutes_and_resumes(self):
        project_id, page_ids = self.make_project(SIX_DOCUMENTS)
        self.limit_after = 1                          # batch 2 is rate-limited
        response, data, calls = self.step(project_id)
        self.assertEqual(response.status_code, 503, data)
        self.assertEqual((data["stopped"], data["has_topics"], data["code"]), (True, True, "ai_provider_busy"))
        self.assertEqual(calls, 2, "the stop ends the step")
        self.assertEqual(len(self.sections(project_id)), 4, "batch 1's topics are kept")
        # Within five minutes nothing knocks again: not a step, not quick-start.
        response, data, calls = self.step(project_id)
        self.assertEqual((response.status_code, data.get("stopped"), calls), (503, True, 0))
        quick = self.post(f"/projects/{project_id}/quick-start")
        self.assertEqual(quick.status_code, 302)
        self.assertEqual(self.extraction_calls(), 2)
        self.assertIn("Learnova could not analyse 8 page(s) yet because the AI is busy. "
                      "It tries again the next time you continue.", self.flashes())
        # Six minutes later the provider is back: the step resumes where it stopped.
        self.limit_after = None
        self.age_analysis(project_id, 6)
        response, data, calls = self.step(project_id)
        self.assertEqual((response.status_code, calls, data["finished"]), (200, 2, True), data)
        self.assertEqual(self.analysis(project_id)["analysed_page_ids"], page_ids)

    def test_a_fresh_project_whose_first_step_is_rate_limited_creates_nothing_and_succeeds_later(self):
        project_id, _ = self.make_project(SIX_DOCUMENTS)
        self.limit_after = 0
        response, data, calls = self.step(project_id)
        self.assertEqual(response.status_code, 503, data)
        self.assertEqual((data["stopped"], data["has_topics"], calls), (True, False, 1))
        self.assertEqual(self.sections(project_id), [])
        response, data, calls = self.step(project_id)
        self.assertEqual((response.status_code, data.get("stopped"), calls), (503, True, 0),
                         "no knocking again within five minutes")
        self.assertEqual(self.lease(project_id), ("", None))
        self.limit_after = None
        self.age_analysis(project_id, 6)
        rounds = self.run_steps(project_id)
        self.assertEqual(sum(count for count, _ in rounds), 3)
        self.assertEqual(len(self.sections(project_id)), 12)


class UpdateTopicsTests(StepTestCase):
    def confirmed_project_with_new_pages(self):
        project_id, _ = self.make_project([[0, 1, 2, 3], [4, 5, 6, 7]])
        self.quick_start(project_id)
        with application.app.app_context():
            project = self.project(project_id)
            project.topics_confirmed = True
            db.session.commit()
        added = self.add_pages(project_id, [8, 9, 10, 11])
        return project_id, added

    def test_update_topics_is_a_link_to_the_start_page(self):
        project_id, _ = self.confirmed_project_with_new_pages()
        page = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        self.assertRegex(page, rf'href="/projects/{project_id}/start\?auto=1&(amp;)?then=topics"')
        self.assertNotIn(f'action="/projects/{project_id}/reanalyze"', page)
        start = self.client.get(f"/projects/{project_id}/start?auto=1&then=topics").get_data(as_text=True)
        self.assertIn(f'data-analysis-step-url="/projects/{project_id}/analysis/step"', start)
        self.assertIn('data-then="topics"', start)
        self.assertIn('<input type="hidden" name="then" value="topics">', start)

    def test_steps_then_quick_start_with_then_topics_end_on_the_topics_never_in_a_lesson(self):
        project_id, added = self.confirmed_project_with_new_pages()
        before = self.extraction_calls()
        rounds = self.run_steps(project_id, then="topics")
        self.assertEqual(sum(count for count, _ in rounds), 1, "only the new batch is read")
        self.assertEqual(rounds[-1][1]["new_topics"], 4)
        response, data, calls = self.quick_start_json(project_id, then="topics")
        self.assertEqual((response.status_code, calls), (200, 0), data)
        self.assertTrue(data["redirect"].endswith(f"/projects/{project_id}#topics"), data)
        self.assertNotIn("lesson_generation", self.calls, "Update topics never opens a lesson")
        self.assertEqual(self.extraction_calls() - before, 1)

    def test_new_topics_of_a_round_are_shown_before_the_next_lesson(self):
        project_id, _ = self.confirmed_project_with_new_pages()
        self.run_steps(project_id)
        response, data, _calls = self.quick_start_json(project_id)
        self.assertEqual(response.status_code, 200, data)
        self.assertTrue(data.get("review"), data)
        self.assertIn("#topics", data["redirect"])
        self.assertEqual(data.get("new_topics"), 4)
        self.assertNotIn("lesson_generation", self.calls)
        # Once seen, the next tap opens the lesson.
        response, data, _calls = self.quick_start_json(project_id)
        self.assertEqual(response.status_code, 200, data)
        self.assertIn("session_id", data, data)

    def test_the_no_js_update_topics_form_ends_on_the_topics(self):
        project_id, _ = self.confirmed_project_with_new_pages()
        response = self.post(f"/projects/{project_id}/quick-start", data={"then": "topics"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith(f"/projects/{project_id}#topics"))
        self.assertIn("Your topics are up to date.", self.flashes())
        self.assertNotIn("lesson_generation", self.calls)
        self.assertEqual(len(self.sections(project_id)), 12)

    def test_update_topics_retries_a_failed_page_once_per_round(self):
        project_id, page_ids = self.make_project([[0, 1], [2, 3]])
        self.broken_pages = {page_ids[1]}
        self.quick_start(project_id)
        self.assertEqual(self.analysis(project_id)["failed_page_ids"], [page_ids[1]])
        self.broken_pages = set()
        # Plain visits never ask about a failed page again ...
        _response, data, calls = self.step(project_id)
        self.assertEqual((calls, data["needed"]), (0, False))
        # ... "Update topics" does, once: only that page (its text fragments, which is how the
        # failing run had asked about it), never the pages whose answers are stored.
        asked_before = len(self.prompts)
        rounds = self.run_steps(project_id, then="topics")
        self.assertGreaterEqual(sum(count for count, _ in rounds), 1)
        asked = {entry["page_id"] for prompt in self.prompts[asked_before:] for entry in batch_of(prompt)}
        self.assertEqual(asked, {page_ids[1]})
        self.assertEqual(self.analysis(project_id)["failed_page_ids"], [])
        _response, data, calls = self.step(project_id, then="topics")
        self.assertEqual(calls, 0)

    def test_a_page_that_fails_again_is_asked_once_per_update_round(self):
        project_id, page_ids = self.make_project([[0, 1], [2, 3]])
        self.broken_pages = {page_ids[1]}
        self.quick_start(project_id)
        one_shot_leaf_calls = self.calls.count("project_section_generation")
        application.app.config["ANALYSIS_STEP_CALLS"] = 1
        asked_before = len(self.prompts)
        rounds = self.run_steps(project_id, then="topics")
        asked = [entry["page_id"] for prompt in self.prompts[asked_before:] for entry in batch_of(prompt)]
        self.assertEqual(set(asked), {page_ids[1]}, "only the failed page")
        self.assertLess(sum(count for count, _ in rounds), one_shot_leaf_calls,
                        "its stored halves are not asked again, and no fragment twice in the round")
        self.assertEqual(len(asked), len(set(map(str, self.prompts[asked_before:]))), "no prompt repeats")
        self.assertEqual(self.analysis(project_id)["failed_page_ids"], [page_ids[1]])


class StartPageTests(StepTestCase):
    def test_the_start_page_carries_the_step_url_and_resumes_a_round_after_a_reload(self):
        project_id, _ = self.make_project(SIX_DOCUMENTS)
        page = self.client.get(f"/projects/{project_id}/start").get_data(as_text=True)
        self.assertIn(f'data-analysis-step-url="/projects/{project_id}/analysis/step"', page)
        self.assertIn('data-then=""', page)
        self.assertIn('data-auto="false"', page)
        self.step(project_id)                       # the round starts; the student reloads
        page = self.client.get(f"/projects/{project_id}/start").get_data(as_text=True)
        self.assertIn('data-auto="true"', page, "an unfinished round goes on by itself after a reload")
        rounds = self.run_steps(project_id)
        self.assertEqual(sum(count for count, _ in rounds), 1, "the reload costs no batch twice")
        self.assertEqual(self.extraction_calls(), 3)
        page = self.client.get(f"/projects/{project_id}/start").get_data(as_text=True)
        self.assertIn('data-auto="false"', page)

    def test_the_no_js_quick_start_still_completes_in_one_request(self):
        project_id, page_ids = self.make_project(SIX_DOCUMENTS)
        response = self.quick_start(project_id)
        self.assertIn("#topics", response.headers["Location"])
        self.assertEqual(self.extraction_calls(), 3)
        self.assertEqual(self.analysis(project_id)["analysed_page_ids"], page_ids)
        self.assertEqual(self.lease(project_id), ("", None))


class MigrationTests(unittest.TestCase):
    def setUp(self):
        with application.app.app_context():
            db.drop_all()
            db.create_all()

    def tearDown(self):
        with application.app.app_context():
            db.session.remove()
            db.drop_all()
            db.create_all()

    def test_041_adds_the_lease_columns_to_an_existing_database_once(self):
        with application.app.app_context():
            # The project's owner exists first: the release gate runs with SQLite foreign keys
            # on (LEARNOVA_TEST_SQLITE_FK=1), where a project of no user is refused.
            owner = application.User(username="alt", email="alt@example.com", preferred_language="en", grade="")
            owner.set_password(PASSWORD)
            db.session.add(owner)
            db.session.commit()
            owner_id = int(owner.id)
            with db.engine.begin() as connection:
                connection.execute(text("ALTER TABLE learning_project DROP COLUMN analysis_lease_token"))
                connection.execute(text("ALTER TABLE learning_project DROP COLUMN analysis_lease_until"))
                connection.execute(text(
                    "INSERT INTO learning_project (user_id, title, subject, status, topics_confirmed, topics_stale, "
                    "analysis_json, created_at, updated_at, grade) VALUES (:owner, 'Alt', 'Ethics', 'planned', 0, 0, "
                    "'{}', '2026-01-01 00:00:00', '2026-01-01 00:00:00', '')"), {"owner": owner_id})
        application.ensure_database()
        with application.app.app_context():
            columns = {column["name"] for column in inspect(db.engine).get_columns("learning_project")}
            self.assertLessEqual({"analysis_lease_token", "analysis_lease_until"}, columns)
            row = db.session.execute(text(
                "SELECT analysis_lease_token, analysis_lease_until FROM learning_project")).one()
            self.assertEqual(tuple(row), ("", None), "an existing project gets the empty lease")
            self.assertIsNotNone(db.session.get(application.SchemaMigration, "041_analysis_lease"))
            statements = []

            def record(_conn, _cursor, statement, *_args):
                statements.append(statement)

            event.listen(db.engine, "before_cursor_execute", record)
            try:
                application.ensure_database()
            finally:
                event.remove(db.engine, "before_cursor_execute", record)
        self.assertFalse([statement for statement in statements
                          if "ALTER TABLE" in statement.upper() and "learning_project" in statement],
                         "a second start adds nothing")


class LeaseHelperTests(unittest.TestCase):
    def test_the_step_settings_are_validated(self):
        self.assertEqual(application.app.config["ANALYSIS_LEASE_SECONDS"], 150)
        self.assertEqual(application.app.config["ANALYSIS_STEP_CALLS"], 2)
        from learnova import config

        for name, value in (("ANALYSIS_STEP_CALLS", "9"), ("ANALYSIS_STEP_CALLS", "0"),
                            ("ANALYSIS_LEASE_SECONDS", "10"), ("ANALYSIS_LEASE_SECONDS", "x")):
            with self.subTest(name=name, value=value), patch.dict("os.environ", {name: value}):
                with self.assertRaises(RuntimeError):
                    config.configure_app(application.Flask("check", instance_path=tempfile.mkdtemp()), "testing")

    def test_the_busy_error_has_the_status_and_code_of_the_contract(self):
        self.assertTrue(issubclass(application.AnalysisRunning, Exception))
        self.assertFalse(issubclass(application.AnalysisRunning, (ValueError, ai_service.AIGatewayError)),
                         "no existing handler may swallow it as a failure")


if __name__ == "__main__":
    unittest.main()
