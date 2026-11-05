"""The interactive tutorial: the registry, who sees what, the stored progress and its API.

learnova/tutorial/registry.py is the single source of truth: a quick tour (one step per
function at its place in the navigation) and short feature chapters that belong to one
page each and start by themselves the first time the student uses that feature. The server
filters them by the student's capabilities (flags, plan - never role), translates them and
sends JSON to static/js/tour.js. Progress lives in tutorial_progress (migration 042), one
row per student and section, written only for current_user; the "Show tips automatically"
switch is a reserved row of the same table. The browser side is
tests/test_tutorial_browser.py; the engine's pure rules are tests/js/tour-rules.test.mjs
(run by tests/test_js_browser.py).
"""

import hashlib
import hmac
import json
import os
import re
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(tempfile.gettempdir(), "learnova-tutorial-test.db"))
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from sqlalchemy import event, inspect, text  # noqa: E402

import app as application  # noqa: E402
from learnova.translations import catalog  # noqa: E402
from learnova.tutorial import progress as rules  # noqa: E402
from learnova.tutorial import registry, visibility  # noqa: E402
from tests.config_isolation import keep_attribute, keep_config  # noqa: E402
from tests.conftest import sqlite_foreign_keys_on  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FR = json.loads((ROOT / "learnova/translations/data/fr.json").read_text(encoding="utf-8"))
ES = json.loads((ROOT / "learnova/translations/data/es.json").read_text(encoding="utf-8"))
PLACEHOLDER = re.compile(r"\{[^{}\s]*\}")
PASSWORD = "correct-horse-battery"
db = application.db
M = application
# The generic sentence that used to stand in for a missing element (2026-11-04: "always" shown
# to new students). It must never reach a student again; its msgid stays in the catalogue.
OLD_MISSING = {"en": "This appears once you have used it for the first time.",
               "de": "Das erscheint, sobald du es zum ersten Mal benutzt hast.",
               "fr": "Cela apparaît dès que tu l'as utilisé une première fois.",
               "es": "Esto aparece en cuanto lo hayas usado por primera vez."}
# Config keys these tests change; every test puts them back (tests/config_isolation.py).
CONFIG_KEYS = ("TESTING", "WTF_CSRF_ENABLED", "FEATURE_ASSISTANT_CHAT", "FEATURE_CHAT_IMAGES",
               "FEATURE_CONTINUE_LEARNING", "FEATURE_GAMIFICATION", "FEATURE_LEAGUES", "FEATURE_COMMUNITY_LIBRARY",
               "FEATURE_PRIVATE_FLASHCARDS", "PREMIUM_ADMIN_IDENTIFIERS", "AI_DIAGNOSTICS_ADMINS",
               "COMMUNITY_MODERATORS", "SUBSCRIPTION_QUOTAS_ENFORCED", "FEATURE_COMMUNITY_PUBLISHING",
               "FEATURE_COMMUNITY_MODERATION", "FEATURE_FLASHCARD_LEARN_MODE", "FEATURE_FLASHCARD_TEST_MODE",
               "FEATURE_FLASHCARD_MATCH_GAME", "FEATURE_FLASHCARD_BLAST_GAME", "FEATURE_FLASHCARD_BLOCKS_GAME",
               "FEATURE_FLASHCARD_GAMES", "FEATURE_FLASHCARD_PDF_IMPORT", "FEATURE_FLASHCARD_IMAGE_IMPORT",
               "FEATURE_VOCABULARY_TRAINER", "FEATURE_DAILY_GOALS", "SECRET_KEY")
# Where each page key is checked: a path, or "@token" = the seeded student's own record
# (GET /api/tutorial/progress context).
HOOK_PAGES = {"chat": "@chat_thread", "*": "/dashboard", "index": "/lesson", "dashboard": "/dashboard",
              "projects": "/projects", "project": "@project", "project-review": "@project_review",
              "flashcards": "/flashcards", "flashcard-set": "@flashcard_set", "flashcard-mode": "@flashcard_study",
              "practice-today": "/practice/today", "mistakes": "/insights/mistakes", "progress": "/progress",
              "profile": "/profile", "leaderboard": "/leaderboard", "community": "/community", "settings": "/settings",
              "project-draft": "@project_draft", "exam-results": "@exam_result", "study-session": "@study_session"}
# Hooks drawn by a script (the chat's answers, a community set opened in place): checked in
# the script that draws them.
JS_SOURCES = ("static/js/assistant.js", "static/js/community.js")
FORBIDDEN = ("/admin", "/internal", "moderation", "premium-keys", "contact-messages", "ai-diagnostics")
# Every flag that gates a step (development: all on).
DEV_FLAGS = {name: True for name in (
    "FEATURE_ASSISTANT_CHAT", "FEATURE_CHAT_IMAGES", "FEATURE_CONTINUE_LEARNING", "FEATURE_GAMIFICATION",
    "FEATURE_LEAGUES", "FEATURE_COMMUNITY_LIBRARY", "FEATURE_COMMUNITY_PUBLISHING", "FEATURE_COMMUNITY_MODERATION",
    "FEATURE_PRIVATE_FLASHCARDS", "FEATURE_FLASHCARD_LEARN_MODE", "FEATURE_FLASHCARD_TEST_MODE",
    "FEATURE_FLASHCARD_MATCH_GAME", "FEATURE_FLASHCARD_BLAST_GAME", "FEATURE_FLASHCARD_BLOCKS_GAME",
    "FEATURE_FLASHCARD_GAMES", "FEATURE_FLASHCARD_PDF_IMPORT", "FEATURE_FLASHCARD_IMAGE_IMPORT",
    "FEATURE_VOCABULARY_TRAINER", "FEATURE_DAILY_GOALS")}
# Production (render.yaml + config.py production defaults, docs/TUTORIAL.md): the study
# modes, the imports and the vocabulary trainer are off.
PROD_FLAGS = {**DEV_FLAGS, **{name: False for name in (
    "FEATURE_FLASHCARD_LEARN_MODE", "FEATURE_FLASHCARD_TEST_MODE", "FEATURE_FLASHCARD_MATCH_GAME",
    "FEATURE_FLASHCARD_BLAST_GAME", "FEATURE_FLASHCARD_BLOCKS_GAME", "FEATURE_FLASHCARD_GAMES",
    "FEATURE_FLASHCARD_PDF_IMPORT", "FEATURE_FLASHCARD_IMAGE_IMPORT", "FEATURE_VOCABULARY_TRAINER")}}
# Without a study mode besides Flashcards, the mode list has nothing to choose from.
PROD_HIDDEN = {"practice-import", "practice-vocabulary", "practice-modes"}
PREMIUM_ONLY = {"projects-create-premium", "account-status-premium", "profile-premium"}
FREE_ONLY = {"projects-create-free", "account-status-free"}
# Templates whose msgids the tutorial added or relies on (all must be translated).
TUTORIAL_TEMPLATES = ("templates/tutorial.html", "templates/components/_tour.html",
                      "templates/components/_tutorial_tips.html", "templates/components/_navbar.html",
                      "templates/components/_footer.html", "templates/settings.html", "templates/dashboard.html",
                      "templates/components/_subscription.html", "templates/flashcards_overview.html")
# Step texts that are the same word in German (proper names and loan words).
SAME_IN_GERMAN = {"Chat", "Archive", "XP and levels", "Challenges", "Test", "Blast", "Community"}
# The chapters a brand-new student has no record for, and what the hub says instead of Replay.
NEEDS_A_RECORD = {"chat-answers", "chat-history", "lesson-inside", "lesson-tutor", "lesson-test", "project-draft",
                  "project-workspace", "project-progress", "project-review", "exam-results", "study-session",
                  "flashcard-set", "flashcard-study", "community-set"}


def no_ai(**_kwargs):
    raise RuntimeError("no live AI in the tutorial tests")


def auto_of(html):
    match = re.search(r'data-tutorial-auto="([^"]*)"', html)
    return match.group(1).split() if match else None


class TutorialCase(unittest.TestCase):
    def setUp(self):
        keep_config(self, application.app.config, *CONFIG_KEYS)
        keep_attribute(self, application.limiter, "enabled")
        application.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        application.SESSIONS.clear()
        application._tutorial_payload_cached.cache_clear()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = application.app.test_client()

    def register(self, name="alice", language="en", client=None, grade="8", settle=False):
        client = client or self.client
        response = client.post("/register", data={
            "username": name, "email": f"{name}@example.com", "password": PASSWORD,
            "language": language, "grade": grade})
        self.assertIn(response.status_code, (302, 303), response.get_data(as_text=True)[:300])
        if settle:
            client.post("/api/tour/complete", json={})
        return client

    def user(self, name="alice"):
        return db.session.scalar(db.select(M.User).where(M.User.username == name))

    def steps_payload(self, client=None):
        response = (client or self.client).get("/api/tutorial/steps")
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def visible_ids(self, client=None):
        return {step["id"] for step in self.steps_payload(client)["steps"]}

    def progress(self, client=None):
        response = (client or self.client).get("/api/tutorial/progress")
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def save(self, section, step, status="in_progress", client=None):
        return (client or self.client).post("/api/tutorial/progress",
                                            json={"section": section, "step": step, "status": status})

    def last(self, section_id):
        return [step["id"] for step in self.steps_payload()["steps"] if step["section"] == section_id][-1]

    def flags(self, values):
        application.app.config.update(values)
        application._tutorial_payload_cached.cache_clear()

    def premium(self, name="alice"):
        with application.app.app_context():
            db.session.add(M.PremiumEntitlement(user_id=self.user(name).id, status="active",
                                                expires_at=application.utcnow() + timedelta(days=30)))
            db.session.commit()

    def seed(self, name="alice"):
        """The student's own records every chapter needs: a chat with a reply, a saved
        lesson, a draft project (stage B, one page to review), a learned project (stage C
        with a plan), a handed-in practice exam, a flashcard set, and a community set someone
        else published. Projects are typed topics through the real routes; the AI is never
        called for real."""

        application.app.config.update(SUBSCRIPTION_QUOTAS_ENFORCED=False)
        with patch.object(application, "create_response", side_effect=no_ai):
            draft = self.client.post("/projects", data={"subject": "Physics", "title": "Draft",
                                                         "topics": "Kraft\nEnergie\nImpuls"})
            learned = self.client.post("/projects", data={"subject": "Physics", "title": "Learned",
                                                           "topics": "Kraft\nEnergie\nImpuls",
                                                           "exam_date": (date.today() + timedelta(days=30)).isoformat()})
            ids = [int(response.headers["Location"].split("#")[0].rstrip("/").rsplit("/", 1)[1])
                   for response in (draft, learned)]
            confirmed = self.client.post(f"/projects/{ids[1]}/topics/confirm", data={"choice": "auto"})
            self.assertEqual(confirmed.status_code, 302)
        with application.app.app_context():
            me = self.user(name).id
            other = M.User(username="publisher", email="publisher@example.com")
            other.set_password(PASSWORD)
            db.session.add(other)
            conversation = M.Conversation(user_id=me, title="Potenzen", message_count=2)
            older = M.Conversation(user_id=me, title="Wurzeln", message_count=2)
            exam = M.FinalExam(project_id=ids[1], question_count=1, duration_minutes=10, difficulty_mode="mixed",
                               included_section_ids_json="[]", question_types_json="[]", status="submitted",
                               expires_at=application.utcnow() + timedelta(hours=1), submitted_at=application.utcnow(),
                               score=40.0, result_json=json.dumps({
                                   "correct": 0, "partial": 0, "wrong": 1, "unanswered": 0,
                                   "difficulty_scores": {}, "section_scores": {},
                                   "knowledge": [{"concept": "Kraft", "known": False, "knowledge": 40}]}))
            lesson = M.Lesson(user_id=me, session_id="t" * 20, subject="Mathematics", title="Potenzen",
                              content_json="{}")
            cards = M.FlashcardSet(user_id=me, title="Physik")
            file = M.ProjectFile(project_id=ids[0], original_filename="notes.pdf", mime_type="application/pdf",
                                 original_data=b"%PDF", sha256="tutorial-notes")
            db.session.add_all([older, conversation, exam, lesson, cards, file])
            db.session.flush()
            db.session.add_all([
                M.StudySession(lesson_id=lesson.id, state_json="{}"),
                M.Flashcard(set_id=cards.id, front="Kraft", back="F = m · a"),
                M.ProjectPage(project_id=ids[0], file_id=file.id, page_number=1, page_order=1,
                              extracted_text="Kraft ist Masse mal Beschleunigung.", extraction_status="ready"),
                M.PublicFlashcardSet(creator_id=other.id, title="Bewegung", status="approved",
                                     moderation_decision="allow", card_count=1,
                                     cards_json=json.dumps([{"front": "v", "back": "s / t"}])),
            ])
            db.session.commit()
            exam_id = exam.id
        return {"draft": ids[0], "learned": ids[1], "exam": exam_id}


# ------------------------------------------------------------------------------- registry

class RegistryIntegrityTests(unittest.TestCase):
    """The rules the quick tour and every chapter obey."""

    def test_ids_are_unique_kebab_case_short_and_never_a_retired_section_id(self):
        seen = set()
        for section in registry.SECTIONS:
            self.assertRegex(section.id, r"^[a-z]+(-[a-z]+)*$")
            self.assertLessEqual(len(section.id), 40)
            self.assertNotIn(section.id, registry.OLD_SECTION_IDS,
                             "a retired id would read an old row (a finished old section) as this one")
            self.assertFalse(section.id.startswith("_"), "ids starting with _ are reserved rows")
            for step in section.steps:
                self.assertNotIn(step.id, seen, step.id)
                seen.add(step.id)
                self.assertRegex(step.id, r"^[a-z0-9]+(-[a-z0-9]+)*$")
                self.assertLessEqual(len(step.id), 60)
        self.assertEqual(len({section.id for section in registry.SECTIONS}), len(registry.SECTIONS))
        self.assertTrue(registry.AUTO_TIPS_SECTION.startswith("_"))

    def test_the_whole_tutorial_has_70_to_100_steps(self):
        # The user's request of 2026-11-04: "around 70-100 steps", not 150.
        low, high = registry.STEP_TOTAL
        self.assertEqual((low, high), (70, 100))
        total = len(registry.all_steps())
        self.assertGreaterEqual(total, low)
        self.assertLessEqual(total, high)
        for flags in ({}, {"gamification_enabled": True, "leagues": True}):
            for premium in (False, True):
                prod = {name: True for name in ("assistant_chat", "private_flashcards", "community_library",
                                                "community_publishing", "community_moderation",
                                                "gamification_enabled", "daily_goals_enabled")}
                caps = visibility.capabilities({**prod, **flags}, chat_images=True, continue_learning=True,
                                               leagues=True, premium=premium)
                shown = sum(len(ids) for ids in visibility.visible_map(caps).values())
                with self.subTest(premium=premium, flags=flags):
                    self.assertGreaterEqual(shown, low, "a production student sees a full tutorial")
                    self.assertLessEqual(shown, high)

    def test_the_quick_tour_is_short_runs_everywhere_and_ends_at_the_tutorial_link(self):
        tour = registry.section(registry.OVERVIEW_ID)
        self.assertIs(registry.SECTIONS[0], tour)
        self.assertEqual(tour.kind, "overview")
        self.assertEqual(tour.steps[-1].targets, ('[data-tour-target="tutorial"]',),
                         "its last step shows where the Tutorial lives")
        self.assertTrue(all(step.page == "*" for step in tour.steps), "it is shown on the page it starts on")
        caps = visibility.capabilities({name.removeprefix("FEATURE_").lower(): True for name in DEV_FLAGS},
                                       chat_images=True, continue_learning=True, leagues=True)
        shown = visibility.visible_map(caps)[registry.OVERVIEW_ID]
        self.assertGreaterEqual(len(shown), 8)
        self.assertLessEqual(len(shown), 12)
        hooks = {re.findall(r'data-tour-target="([^"]+)"', step.targets[0])[0] for step in tour.steps if step.targets}
        self.assertEqual(hooks, {"chat", "overview", "new-lesson", "projects", "flashcards", "practice-today",
                                 "community", "profile", "leagues", "settings", "xp", "tutorial"},
                         "every function at its real entry point in the navigation")

    def test_every_chapter_belongs_to_one_page_with_an_anchor_and_a_group(self):
        groups = {item.id for item in registry.GROUPS}
        tokens = set(registry.CONTEXT_TOKENS)
        for section in registry.chapters():
            with self.subTest(chapter=section.id):
                self.assertIn(section.page, set(registry.PAGES) | set(registry.PAGE_BY_ENDPOINT.values()))
                self.assertNotIn(section.page, ("*", "tutorial", "onboarding", "other"))
                self.assertTrue(section.anchor)
                self.assertIn(section.group, groups)
                self.assertGreaterEqual(len(section.steps), 1)
                self.assertLessEqual(len(section.steps), 6, "a chapter is a few tips, not a tour")
                self.assertTrue(all(step.page == section.page for step in section.steps),
                                "a chapter never sends the student to another page")
                self.assertTrue(section.url.startswith("/") or section.url[1:] in tokens, section.url)
                if section.url.startswith("@"):
                    self.assertTrue(section.when.startswith("Starts "), "says when it starts by itself")
                    self.assertTrue(section.where.startswith("/"), "and where that record is made")
                else:
                    self.assertEqual((section.when, section.where), ("", ""), "always reachable: Replay")
        self.assertEqual({section.id for section in registry.chapters() if section.url.startswith("@")},
                         NEEDS_A_RECORD)

    def test_kinds_advances_viewports_and_pages_are_valid(self):
        for section, step in registry.all_steps():
            with self.subTest(step=step.id):
                self.assertIn(step.kind, registry.KINDS)
                self.assertIn(step.advance, registry.ADVANCES)
                self.assertIn(step.viewport, registry.VIEWPORTS)
                if step.kind == "action":
                    self.assertIn(step.advance, ("click", "change", "input"))
                else:
                    self.assertEqual(step.advance, "next")
                if step.kind == "card":
                    self.assertEqual(step.targets, ())
                else:
                    self.assertTrue(step.targets, "a lit step needs a target")
                self.assertGreater(step.wait_ms, 0)
                if step.create:
                    self.assertEqual(len(step.create), 2)
                    self.assertTrue(step.create[1].startswith("/"))
                if step.shortcut:
                    self.assertIn(step.shortcut[1], {item.id for item in section.steps})

    def test_no_step_touches_an_admin_moderation_or_internal_page(self):
        for section in registry.SECTIONS:
            for value in (section.anchor, section.url, section.where):
                for word in FORBIDDEN:
                    self.assertNotIn(word, value.lower(), section.id)
        for _section, step in registry.all_steps():
            for value in step.targets + step.also + (step.busy,) + tuple(step.create[1:]):
                for word in FORBIDDEN:
                    self.assertNotIn(word, value.lower(), f"{step.id}: {value}")
        for page in registry.PAGES.values():
            for word in FORBIDDEN:
                self.assertNotIn(word, page.url.lower())

    def test_texts_are_short(self):
        for section in registry.SECTIONS:
            self.assertLessEqual(len(section.title.split()), 4, section.title)
        for section, step in registry.all_steps():
            with self.subTest(step=step.id):
                # 1-4 words; a question title may take a fifth ("Not sure what to ask?").
                self.assertLessEqual(len(step.title.split()), 5 if step.title.endswith("?") else 4, step.title)
                sentences = [part for part in re.split(r"(?<=[.!?])\s+", step.text.strip()) if part]
                self.assertLessEqual(len(sentences), 3, step.text)
                self.assertLessEqual(len(step.text), 260, step.text)

    def test_spending_publishing_and_deleting_are_never_done_for_the_student(self):
        # Next never presses these; only the student's own tap does (or nothing).
        for step_id in ("projects-create-free", "projects-create-premium", "community-publish", "account-code",
                        "projects-mock-exam", "projects-start-learning", "lessons-saved"):
            self.assertEqual(registry.step(step_id).kind, "read", step_id)
        # Action steps only ever listen to the student's own tap; none of them spends, pays,
        # publishes, deletes or logs out (Build my lesson is the student's own choice).
        actions = {step.id for _section, step in registry.all_steps() if step.kind == "action"}
        self.assertEqual(actions, {"lessons-subject", "lessons-goal", "lessons-build", "lessons-tutor",
                                   "lessons-start-test", "practice-start", "chat-menu"})
        targets = " ".join(" ".join(step.targets) for _section, step in registry.all_steps())
        for never in ("#asstDelete", "logout", "#asstRename", "#asstArchive"):
            self.assertNotIn(never, targets)

    def test_the_old_lesson_tour_sentences_are_kept_and_the_engine_has_no_texts(self):
        texts = {step.text for section in registry.chapters() if section.group == "lessons" for step in section.steps}
        for reused in ("Tap a subject - whatever you want to learn today.",
                       "Tap Build my lesson. Learnova writes an explanation and a short test for you.",
                       "Here is your lesson. Read the explanation, then scroll down.",
                       "Stuck? Your tutor chat is right here, any time during a lesson."):
            self.assertIn(reused, texts)
        rules_js = (ROOT / "static/js/tour-rules.js").read_text(encoding="utf-8")
        self.assertNotIn("export const STEPS", rules_js, "no step texts in the engine")
        self.assertEqual([key for key in catalog.FRONTEND_MESSAGES if key.startswith("tour")], [],
                         "the retired tour* frontend keys are gone")

    def test_the_generic_missing_sentence_is_gone_but_its_msgid_is_kept(self):
        self.assertNotIn("missing", registry.UI)
        self.assertNotIn(OLD_MISSING["en"], registry.all_msgids())
        engine = (ROOT / "static/js/tour.js").read_text(encoding="utf-8")
        self.assertNotIn('ui("missing")', engine)
        self.assertNotIn("current.missing", engine)
        for language, sentence in OLD_MISSING.items():
            self.assertNotIn(sentence, engine)
            if language != "en":
                self.assertEqual(catalog.translate(OLD_MISSING["en"], language), sentence,
                                 "the msgid stays in the catalogue (unused)")
        self.assertNotIn("missing", {field.name for field in registry.Step.__dataclass_fields__.values()})


# ------------------------------------------------------------------------------ visibility

class CapabilityTests(unittest.TestCase):
    def test_capabilities_come_from_flags_switches_and_the_plan(self):
        caps = visibility.capabilities({"assistant_chat": True, "vocabulary_trainer": False,
                                        "flashcard_image_import": True},
                                       chat_images=True, continue_learning=False, leagues=True, premium=False)
        self.assertEqual(caps, frozenset({"assistant_chat", "flashcard_image_import", "flashcard_import",
                                          "chat_images", "leagues", "leagues_opted_in", "free"}))
        self.assertIn("premium", visibility.capabilities({}, premium=True))
        self.assertNotIn("free", visibility.capabilities({}, premium=True))
        opted_out = visibility.capabilities({}, leagues=True, leagues_opted_in=False)
        self.assertIn("leagues", opted_out)
        self.assertNotIn("leagues_opted_in", opted_out, "switched off in Settings: no league card to point at")

    def test_a_bang_requirement_means_the_capability_is_absent(self):
        self.assertTrue(visibility.meets(("!gamification_enabled",), {"free"}))
        self.assertFalse(visibility.meets(("!gamification_enabled",), {"free", "gamification_enabled"}))
        on = visibility.visible_map(visibility.capabilities({"gamification_enabled": True}))["my-profile"]
        off = visibility.visible_map(visibility.capabilities({}))["my-profile"]
        self.assertIn("profile-avatars", on)
        self.assertNotIn("profile-avatars-pick", on)
        self.assertIn("profile-avatars-pick", off, "avatars without XP: the choosing variant")
        self.assertNotIn("profile-avatars", off)

    def test_a_chapter_needs_its_requirements_and_one_visible_step(self):
        full = visibility.capabilities({"assistant_chat": True}, chat_images=True, continue_learning=True)
        bare = visibility.capabilities({})
        self.assertIn("chat-basics", visibility.visible_map(full))
        for chapter in ("chat-basics", "chat-answers", "chat-history", "flashcards-library", "community-library",
                        "progress", "leagues"):
            self.assertNotIn(chapter, visibility.visible_map(bare), chapter)
        tour = visibility.visible_map(bare)[registry.OVERVIEW_ID]
        for gone in ("overview-chat", "overview-flashcards", "overview-community", "overview-leagues", "overview-xp"):
            self.assertNotIn(gone, tour)
        self.assertIn("overview-lesson", tour)
        no_photos = visibility.capabilities({"assistant_chat": True}, continue_learning=True)
        self.assertNotIn("chat-photo", visibility.visible_map(no_photos)["chat-basics"])
        self.assertIn("chat-continue", visibility.visible_map(no_photos)["chat-answers"])

    def test_the_chapters_of_a_page_come_in_registry_order(self):
        caps = visibility.capabilities({"assistant_chat": True, "community_library": True})
        self.assertEqual([item.id for item in visibility.chapters_for_page("chat", caps)],
                         ["chat-basics", "chat-answers", "chat-history"])
        self.assertEqual([item.id for item in visibility.chapters_for_page("community", caps)],
                         ["community-library", "community-set"])
        self.assertEqual(visibility.chapters_for_page("tutorial", caps), [])
        groups = [group.id for group, _members in visibility.visible_groups(caps)]
        self.assertEqual(groups, ["chat", "lessons", "projects", "practice", "profile", "community", "account"])
        self.assertNotIn("community", [group.id for group, _ in visibility.visible_groups(visibility.capabilities({}))])

    def test_the_payload_is_translated_filled_and_signed(self):
        caps = visibility.capabilities({"assistant_chat": True}, chat_images=True)
        english = visibility.payload(caps, "en", translate=catalog.translate, facts={"price": "3,00"})
        german = visibility.payload(caps, "de", translate=catalog.translate, facts={"price": "3,00"})
        self.assertEqual(set(english), {"v", "sig", "language", "hub", "ui", "sections", "steps", "containers"})
        self.assertEqual(english["v"], 6)
        self.assertEqual(set(english["ui"]), {"previous", "next", "finish", "exit", "skip", "stepOf", "busy", "lit",
                                              "offer"})
        self.assertNotIn("Still loading", json.dumps(english), "nothing flashes while a step is awaited")
        self.assertEqual(set(english["ui"]["offer"]), {"title", "text", "question", "quick", "explore"})
        self.assertNotEqual(english["sig"], german["sig"], "the cache key changes with the language")
        other = visibility.payload(visibility.capabilities({}), "en", translate=catalog.translate)
        self.assertNotEqual(english["sig"], other["sig"], "and with what the student can see")
        self.assertEqual(german["ui"]["previous"], "Zurück", "Previous, never the card-side msgid Back")
        self.assertEqual(german["ui"]["skip"], "Überspringen")
        self.assertEqual(german["ui"]["exit"], "Überblick beenden", "the quick tour, not the hub (Rundgang)")
        tour = next(item for item in german["sections"] if item["id"] == "overview")
        self.assertEqual(tour["title"], "Kurzer Überblick")
        self.assertEqual(german["ui"]["offer"]["quick"], "Zeig mir alles (1 Minute)")
        english_texts = {(step["id"], key): step[key] for step in english["steps"] for key in ("title", "text")}
        for step in german["steps"]:
            for key in ("title", "text"):
                source = english_texts[(step["id"], key)]
                if source in SAME_IN_GERMAN:
                    continue
                self.assertNotEqual(step[key], source, f"de leaks English: {step['id']} {key}")
        chat = next(item for item in english["sections"] if item["id"] == "chat-basics")
        self.assertEqual({key: chat[key] for key in ("kind", "page", "anchor", "group")},
                         {"kind": "chapter", "page": "chat", "anchor": "#asstInput", "group": "chat"})
        first = english["steps"][0]
        self.assertEqual(set(first) - {"also", "busy", "create", "shortcut", "icon", "viewport", "wait"},
                         {"id", "section", "order", "page", "kind", "advance", "targets", "title", "text"})

    def test_facts_fill_placeholders_and_unknown_ones_stay(self):
        self.assertEqual(visibility.fill("Premium costs {price} € a month.", {"price": "3,00"}),
                         "Premium costs 3,00 € a month.")
        self.assertEqual(visibility.fill("Step {current} of {total}", {"price": "3,00"}), "Step {current} of {total}")
        self.assertEqual(visibility.fill("no braces", {}), "no braces")

    def test_the_study_mode_step_needs_a_mode_to_choose(self):
        one = visibility.capabilities({"private_flashcards": True})
        self.assertNotIn("practice-modes", visibility.visible_map(one)["flashcard-set"])
        for flag in ("flashcard_learn_mode", "flashcard_test_mode", "flashcard_match_game", "flashcard_blast_game",
                     "flashcard_blocks_game"):
            caps = visibility.capabilities({"private_flashcards": True, flag: True})
            self.assertIn("practice-modes", visibility.visible_map(caps)["flashcard-set"], flag)


# ----------------------------------------------------------------------------- progress rules

class ProgressRuleTests(unittest.TestCase):
    VISIBLE = {"chat-basics": ["chat-input", "chat-photo", "chat-send", "chat-style"],
               "overview": ["overview-lesson", "overview-done"]}

    def test_updates_are_checked_against_what_the_student_sees(self):
        self.assertEqual(rules.validate_update({"section": "chat-basics", "step": "chat-send"}, self.VISIBLE),
                         ("chat-basics", "chat-send", "in_progress"))
        self.assertEqual(rules.validate_update([], self.VISIBLE), "bad_request")
        self.assertEqual(rules.validate_update({"section": 3, "step": "x"}, self.VISIBLE), "bad_request")
        self.assertEqual(rules.validate_update({"section": "chat-basics"}, self.VISIBLE), "bad_request")
        self.assertEqual(rules.validate_update({"section": "admin", "step": "x"}, self.VISIBLE), "unknown_section")
        self.assertEqual(rules.validate_update({"section": "_auto_tips", "step": "off"}, self.VISIBLE),
                         "unknown_section", "the reserved row is not a section")
        self.assertEqual(rules.validate_update({"section": "chat-basics", "step": "overview-done"}, self.VISIBLE),
                         "unknown_step")
        self.assertEqual(rules.validate_update({"section": "chat-basics", "step": "chat-send", "status": "done"},
                                               self.VISIBLE), "bad_status")
        self.assertEqual(rules.validate_update({"section": "chat-basics", "step": "chat-send", "status": "skipped"},
                                               self.VISIBLE), ("chat-basics", "chat-send", "skipped"),
                         "Skip is allowed on any step")
        self.assertEqual(rules.validate_update({"section": "chat-basics", "step": "chat-style", "status": "completed"},
                                               self.VISIBLE), ("chat-basics", "chat-style", "completed"))
        self.assertEqual(rules.validate_update({"section": "chat-basics", "step": "chat-send", "status": "completed"},
                                               self.VISIBLE), "bad_status", "only the last step completes a section")
        self.assertEqual(rules.validate_reset({"section": "all"}, self.VISIBLE), ("all",))
        self.assertEqual(rules.validate_reset({"section": "nope"}, self.VISIBLE), "unknown_section")
        self.assertEqual(rules.validate_reset("all", self.VISIBLE), "bad_request")

    def test_a_write_never_makes_a_section_less_done(self):
        self.assertEqual(rules.merge_status(None, "in_progress"), "in_progress")
        self.assertEqual(rules.merge_status("in_progress", "skipped"), "skipped")
        self.assertEqual(rules.merge_status("skipped", "in_progress"), "skipped", "a replay keeps it out of auto start")
        self.assertEqual(rules.merge_status("skipped", "completed"), "completed")
        self.assertEqual(rules.merge_status("completed", "skipped"), "completed", "skipped never overwrites completed")
        self.assertEqual(rules.merge_status("completed", "in_progress"), "completed")
        self.assertEqual(rules.merge_status("off", "in_progress"), "in_progress", "an unknown stored value")

    def test_states_for_the_hub(self):
        ids = self.VISIBLE["chat-basics"]
        order = {step: index for index, step in enumerate(ids)}
        self.assertEqual(rules.section_state(ids, None, order)["status"], "not_started")
        state = rules.section_state(ids, {"status": "in_progress", "step": "chat-send"}, order)
        self.assertEqual((state["step"], state["current"], state["total"]), ("chat-send", 3, 4))
        self.assertEqual(rules.section_state(ids, {"status": "skipped", "step": "chat-send"}, order)["status"],
                         "skipped")
        done = rules.section_state(ids, {"status": "completed", "step": "chat-style"}, order)
        self.assertEqual((done["status"], done["current"]), ("completed", 4))
        # chat-photo was hidden by a flag since: the run continues at the next step by order.
        hidden = ["chat-input", "chat-send", "chat-style"]
        state = rules.section_state(hidden, {"status": "in_progress", "step": "chat-photo"}, order)
        self.assertEqual(state["step"], "chat-send")

    def test_percent_is_the_mean_over_visible_sections_and_skipped_is_not_done(self):
        rows = {"chat-basics": {"status": "in_progress", "step": "chat-send"}, "overview": {"status": "completed"}}
        self.assertEqual(rules.percent(self.VISIBLE, rows), round(100 * (2 / 4 + 1) / 2))
        self.assertEqual(rules.percent(self.VISIBLE, {}), 0)
        self.assertEqual(rules.percent({}, rows), 0)
        self.assertEqual(rules.percent(self.VISIBLE, {"chat-basics": {"status": "skipped"}}), 0)
        self.assertEqual(rules.percent(self.VISIBLE, {"chat": {"status": "completed"}}), 0, "old rows are ignored")


# ----------------------------------------------------------------------------- the server

class MigrationTests(TutorialCase):
    def test_no_new_migration_the_table_holds_the_new_statuses(self):
        with application.app.app_context():
            application.ensure_database()
            application.ensure_database()
            self.assertIn("tutorial_progress", inspect(db.engine).get_table_names())
            self.assertIsNotNone(db.session.get(M.SchemaMigration, "042_tutorial_progress"))
            self.assertIsNone(db.session.get(M.SchemaMigration, "043_tutorial_tips"), "no migration was needed")
            columns = {column["name"]: column for column in inspect(db.engine).get_columns("tutorial_progress")}
            self.assertEqual(set(columns), {"id", "user_id", "section", "step", "status", "started_at", "updated_at",
                                            "completed_at"})
            self.assertGreaterEqual(M.TutorialProgress.__table__.c.status.type.length, len("in_progress"))
            self.assertGreaterEqual(M.TutorialProgress.__table__.c.section.type.length,
                                    max(len(section.id) for section in registry.SECTIONS))

    def test_an_existing_database_from_before_gets_the_table(self):
        self.register()
        with application.app.app_context():
            application.ensure_database()
            alice = self.user().id
            with db.engine.begin() as connection:
                connection.execute(text("DROP TABLE tutorial_progress"))
                connection.execute(text("DELETE FROM schema_migration WHERE version = '042_tutorial_progress'"))
            application.ensure_database()
            self.assertIsNotNone(db.session.get(M.SchemaMigration, "042_tutorial_progress"))
            self.assertEqual(self.user().id, alice, "nothing existing was touched")
        self.assertEqual(self.save("overview", "overview-lesson").status_code, 200)


class ApiTests(TutorialCase):
    def test_guests_get_401_json_everywhere(self):
        for method, path in (("get", "/api/tutorial/steps"), ("get", "/api/tutorial/progress"),
                             ("post", "/api/tutorial/progress"), ("post", "/api/tutorial/reset"),
                             ("post", "/api/tour/complete")):
            with self.subTest(path=path):
                response = getattr(self.client, method)(path, json={})
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.get_json()["code"], "login_required")
        self.assertEqual(self.client.post("/tutorial/tips", data={"auto_tips": "on"}).status_code, 302)

    def test_a_new_account_has_nothing_seen_no_context_and_tips_on(self):
        self.flags(DEV_FLAGS)
        self.register()
        data = self.progress()
        self.assertEqual(data["percent"], 0)
        self.assertFalse(data["offer_settled"])
        self.assertTrue(data["auto_tips"])
        self.assertEqual(set(data["context"]), set(registry.CONTEXT_TOKENS))
        self.assertTrue(all(value is None for value in data["context"].values()))
        self.assertEqual({entry["status"] for entry in data["sections"].values()}, {"not_started"})
        self.assertEqual(set(data["sections"]), {section.id for section in registry.SECTIONS})
        with application.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count()).select_from(M.TutorialProgress)), 0,
                             "reading never creates rows")

    def test_saving_resuming_skipping_and_finishing(self):
        self.register()
        body = self.save("chat-basics", "chat-send").get_json()
        self.assertTrue(body["ok"])
        self.assertEqual((body["section"]["status"], body["section"]["step"]), ("in_progress", "chat-send"))
        self.assertGreater(body["percent"], 0)
        self.save("chat-basics", "chat-style")
        state = self.progress()["sections"]["chat-basics"]
        self.assertEqual((state["status"], state["step"]), ("in_progress", "chat-style"))
        skipped = self.save("chat-answers", "chat-reply", "skipped").get_json()
        self.assertEqual(skipped["section"]["status"], "skipped")
        self.save("chat-answers", "chat-tools")
        self.assertEqual(self.progress()["sections"]["chat-answers"]["status"], "skipped",
                         "a replay of a skipped chapter never makes it start by itself again")
        self.assertEqual(self.save("chat-answers", self.last("chat-answers"), "completed").get_json()["section"]["status"],
                         "completed", "finishing a replay completes it")
        with application.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count()).select_from(M.TutorialProgress)), 2,
                             "one row per student and section")

    def test_skipped_never_overwrites_completed_and_completion_keeps_its_first_time(self):
        self.register()
        last = self.last("lesson-start")
        done = self.save("lesson-start", last, "completed").get_json()
        with application.app.app_context():
            row = db.session.scalar(db.select(M.TutorialProgress))
            row.completed_at = row.completed_at - timedelta(days=3)
            db.session.commit()
            first = row.completed_at
        for status in ("skipped", "in_progress", "completed"):
            again = self.save("lesson-start", "lessons-goal" if status != "completed" else last, status).get_json()
            self.assertEqual(again["section"]["status"], "completed", status)
            self.assertEqual(again["percent"], done["percent"], "the percent does not drop")
        with application.app.app_context():
            row = db.session.scalar(db.select(M.TutorialProgress))
            self.assertEqual(application.as_utc(row.completed_at), application.as_utc(first))
        self.client.post("/api/tutorial/reset", json={"section": "lesson-start"})
        self.assertEqual(self.save("lesson-start", "lessons-goal").get_json()["section"]["status"], "in_progress",
                         "after Restart the chapter runs as new")

    def test_invalid_updates_are_refused_with_their_code(self):
        self.register()
        cases = [
            ({"section": "nope", "step": "x"}, "unknown_section"),
            ({"section": "chat", "step": "chat-input"}, "unknown_section"),
            ({"section": "chat-basics", "step": "overview-done"}, "unknown_step"),
            ({"section": "chat-basics", "step": "chat-input", "status": "done"}, "bad_status"),
            ({"section": "chat-basics", "step": "chat-input", "status": "completed"}, "bad_status"),
            ({"section": "chat-basics", "step": 4}, "bad_request"),
            (["chat-basics"], "bad_request"),
        ]
        for body, code in cases:
            with self.subTest(code=code, body=body):
                response = self.client.post("/api/tutorial/progress", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["code"], code)
                self.assertEqual(response.get_json()["error"], "This tutorial step does not exist.")
        response = self.client.post("/api/tutorial/progress", data="not json", content_type="application/json")
        self.assertEqual(response.get_json()["code"], "bad_request")

    def test_a_hidden_chapter_is_unknown_and_a_hidden_step_resumes_further_on(self):
        self.register()
        self.save("chat-basics", "chat-photo")
        application.app.config.update(FEATURE_CHAT_IMAGES=False)
        self.assertEqual(self.progress()["sections"]["chat-basics"]["step"], "chat-send", "the next step by order")
        self.assertEqual(self.save("chat-basics", "chat-photo").get_json()["code"], "unknown_step")
        application.app.config.update(FEATURE_ASSISTANT_CHAT=False)
        self.assertEqual(self.save("chat-basics", "chat-input").get_json()["code"], "unknown_section")
        self.assertNotIn("chat-basics", self.progress()["sections"])

    def test_old_rows_are_kept_and_ignored(self):
        self.register()
        with application.app.app_context():
            me = self.user().id
            for section in ("chat", "lessons", "intro"):
                db.session.add(M.TutorialProgress(user_id=me, section=section, step=f"{section}-done",
                                                  status="completed", completed_at=application.utcnow()))
            db.session.commit()
        data = self.progress()
        self.assertEqual(data["percent"], 0, "a finished old section finishes nothing new")
        self.assertFalse({"chat", "lessons", "intro"} & set(data["sections"]))
        self.client.post("/api/tutorial/reset", json={"section": "all"})
        with application.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count()).select_from(M.TutorialProgress)), 0)
        self.assertEqual(self.client.post("/api/tutorial/reset", json={"section": "chat"}).get_json()["code"],
                         "unknown_section")

    def test_reset_one_section_or_everything_but_never_the_tips_switch(self):
        self.register()
        self.save("chat-basics", "chat-send")
        self.save("overview", "overview-done", "completed")
        self.client.post("/tutorial/tips", data={})
        self.assertEqual(self.client.post("/api/tutorial/reset", json={"section": "chat-basics"}).status_code, 200)
        sections = self.progress()["sections"]
        self.assertEqual(sections["chat-basics"]["status"], "not_started")
        self.assertEqual(sections["overview"]["status"], "completed")
        self.assertEqual(self.client.post("/api/tutorial/reset", json={"section": "all"}).get_json(),
                         {"ok": True, "percent": 0})
        data = self.progress()
        self.assertEqual(data["sections"]["overview"]["status"], "not_started")
        self.assertFalse(data["auto_tips"], "Restart tutorial does not switch the tips back on")
        response = self.client.post("/api/tutorial/reset", json={"section": "admin"})
        self.assertEqual((response.status_code, response.get_json()["code"]), (400, "unknown_section"))
        with application.app.app_context():
            self.assertIsNone(self.user().tour_completed_at, "a reset never touches the first-time offer")

    def test_two_accounts_never_see_each_others_progress(self):
        self.register("alice")
        bob = self.register("bob", client=application.app.test_client())
        self.save("lesson-start", "lessons-build")
        self.client.post("/tutorial/tips", data={})
        self.assertEqual(self.progress(bob)["sections"]["lesson-start"]["status"], "not_started")
        self.assertTrue(self.progress(bob)["auto_tips"], "Alice's switch is hers")
        self.save("lesson-start", "lessons-goal", client=bob)
        self.client.post("/api/tutorial/reset", json={"section": "all"})
        self.assertEqual(self.progress(bob)["sections"]["lesson-start"]["step"], "lessons-goal")

    def test_csrf_is_required_on_every_write(self):
        self.register()
        application.app.config.update(TESTING=False, WTF_CSRF_ENABLED=True)
        application.limiter.enabled = False
        for path, body in (("/api/tutorial/progress", {"section": "overview", "step": "overview-lesson"}),
                           ("/api/tutorial/reset", {"section": "all"}), ("/api/tour/complete", {})):
            with self.subTest(path=path):
                refused = self.client.post(path, json=body)
                self.assertEqual(refused.status_code, 400)
                self.assertEqual(refused.get_json()["code"], "csrf_failed")
        refused = self.client.post("/tutorial/tips", data={})   # a form: "Your form expired", sent back
        self.assertEqual(refused.status_code, 302)
        self.assertNotIn("/tutorial#tips", refused.headers["Location"])
        self.assertTrue(self.progress()["auto_tips"], "the form needs its token")
        page = self.client.get("/dashboard").get_data(as_text=True)
        token = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
        accepted = self.client.post("/api/tutorial/progress", json={"section": "overview", "step": "overview-lesson"},
                                    headers={"X-CSRFToken": token})
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(self.client.post("/tutorial/tips", data={"csrf_token": token}).status_code, 302)
        self.assertFalse(self.progress()["auto_tips"])

    def test_rate_limits_count_per_account(self):
        self.register("alice")
        bob = self.register("bob", client=application.app.test_client())
        application.app.config.update(TESTING=False)
        application.limiter.enabled = True
        application.limiter.reset()
        self.addCleanup(application.limiter.reset)
        codes = [self.client.post("/api/tour/complete", json={}).status_code for _ in range(31)]
        self.assertNotIn(429, codes[:30])
        self.assertEqual(codes[30], 429)
        self.assertEqual(bob.post("/api/tour/complete", json={}).status_code, 200, "another account is unaffected")
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        for route, limit in (('@app.get("/api/tutorial/steps")', "60 per minute"),
                             ('@app.get("/api/tutorial/progress")', "60 per minute"),
                             ('@app.post("/api/tutorial/progress")', "120 per minute"),
                             ('@app.post("/api/tutorial/reset")', "20 per minute"),
                             ('@app.post("/api/tour/complete")', "30 per minute"),
                             ('@app.post("/tutorial/tips")', "30 per minute")):
            self.assertIn(f'{route}\n@limiter.limit("{limit}", key_func=user_rate_key)\n@login_required', source, route)

    def test_context_links_point_only_at_the_callers_own_records(self):
        self.register("alice")
        self.register("bob", client=application.app.test_client())
        with application.app.app_context():
            alice, bob = self.user("alice").id, self.user("bob").id
            for owner, archived, count in ((bob, False, 4), (alice, True, 4), (alice, False, 1)):
                db.session.add(M.Conversation(user_id=owner, title="x", archived=archived, message_count=count))
            bob_lesson = M.Lesson(user_id=bob, session_id="s" * 20, subject="Mathematics", title="t", content_json="{}")
            db.session.add(bob_lesson)
            db.session.flush()
            db.session.add(M.StudySession(lesson_id=bob_lesson.id, state_json="{}"))
            db.session.add(M.LearningProject(user_id=bob, title="p", subject="Physics", topics_confirmed=True))
            db.session.commit()
        context = self.progress()["context"]
        self.assertTrue(all(value is None for value in context.values()), context)
        with application.app.app_context():
            mine = M.Conversation(user_id=alice, title="mine", message_count=2)
            lesson = M.Lesson(user_id=alice, session_id="a" * 20, subject="Mathematics", title="t", content_json="{}")
            project = M.LearningProject(user_id=alice, title="p", subject="Physics", topics_confirmed=True)
            draft = M.LearningProject(user_id=alice, title="d", subject="Physics")
            cards = M.FlashcardSet(user_id=alice, title="cards")
            db.session.add_all([mine, lesson, project, draft, cards])
            db.session.flush()
            db.session.add_all([M.StudySession(lesson_id=lesson.id, state_json="{}"),
                                M.LearningSection(project_id=draft.id, position=0, title="Topic"),
                                M.Flashcard(set_id=cards.id, front="a", back="b")])
            db.session.commit()
            expected = {"chat_thread": f"/assistant/c/{mine.id}", "lesson": f"/lessons/{lesson.id}/resume",
                        "project": f"/projects/{project.id}", "project_draft": f"/projects/{draft.id}",
                        "flashcard_set": f"/flashcards/{cards.id}", "flashcard_study": f"/flashcards/{cards.id}/study"}
            project_id = project.id
            bob_project = db.session.scalar(db.select(M.LearningProject.id).where(M.LearningProject.user_id == bob))
            db.session.add(M.FinalExam(project_id=bob_project, question_count=1, duration_minutes=5,
                                       difficulty_mode="mixed", included_section_ids_json="[]",
                                       question_types_json="[]", status="submitted",
                                       expires_at=application.utcnow(), submitted_at=application.utcnow()))
            bob_plan = M.StudyPlan(user_id=bob, project_id=bob_project, exam_date=date.today() + timedelta(days=9),
                                   target_grade="2", daily_minutes=20)
            db.session.add(bob_plan)
            db.session.flush()
            db.session.add(M.StudyPlanSession(study_plan_id=bob_plan.id, date=date.today() + timedelta(days=1)))
            db.session.commit()
        context = self.progress()["context"]
        for token, url in expected.items():
            self.assertEqual(context[token], url, token)
        self.assertTrue(context["chat_history"].startswith("/assistant/c/"),
                        "two chats of her own in the list: Your chats can be replayed")
        self.assertIsNone(context["exam_result"], "another student's exam is not a context link")
        self.assertIsNone(context["study_session"], "nor another student's study day")
        with application.app.app_context():
            exam = M.FinalExam(project_id=project_id, question_count=1, duration_minutes=5, difficulty_mode="mixed",
                               included_section_ids_json="[]", question_types_json="[]", status="submitted",
                               expires_at=application.utcnow(), submitted_at=application.utcnow())
            plan = M.StudyPlan(user_id=alice, project_id=project_id, exam_date=date.today() + timedelta(days=9),
                               target_grade="2", daily_minutes=20)
            db.session.add_all([exam, plan])
            db.session.flush()
            done = M.StudyPlanSession(study_plan_id=plan.id, date=date.today() + timedelta(days=1), status="completed")
            planned = M.StudyPlanSession(study_plan_id=plan.id, date=date.today() + timedelta(days=2))
            db.session.add_all([done, planned])
            db.session.commit()
            mine_exam, mine_day, mine_plan = exam.id, planned.id, plan.id
        context = self.progress()["context"]
        self.assertEqual(context["exam_result"], f"/exams/{mine_exam}/results")
        self.assertEqual(context["study_session"], f"/study-plans/{mine_plan}/sessions/{mine_day}",
                         "the next day still to do, not one already done")
        application.app.config.update(FEATURE_ASSISTANT_CHAT=False, FEATURE_PRIVATE_FLASHCARDS=False)
        context = self.progress()["context"]
        self.assertIsNone(context["chat_thread"], "a switched-off feature has no link")
        self.assertIsNone(context["flashcard_set"])

    def test_deleting_an_account_deletes_its_progress(self):
        self.register()
        self.save("overview", "overview-lesson")
        self.client.post("/tutorial/tips", data={})
        with application.app.app_context():
            engine = db.engine
            event.listen(engine, "connect", sqlite_foreign_keys_on)
            engine.dispose()
            try:
                self.assertEqual(db.session.execute(text("PRAGMA foreign_keys")).scalar(), 1)
                user_id = self.user().id
                inspector = inspect(db.engine)
                for table in inspector.get_table_names():
                    names = {column["name"] for column in inspector.get_columns(table)}
                    if table != "tutorial_progress" and "user_id" in names:
                        db.session.execute(text(f'DELETE FROM "{table}" WHERE user_id = :id'), {"id": user_id})
                db.session.execute(text('DELETE FROM "user" WHERE id = :id'), {"id": user_id})
                db.session.commit()
                self.assertEqual(db.session.scalar(db.select(db.func.count()).select_from(M.TutorialProgress)), 0)
            finally:
                db.session.remove()
                event.remove(engine, "connect", sqlite_foreign_keys_on)
                engine.dispose()


class AutoStartServerTests(TutorialCase):
    """#tourRoot[data-tutorial-auto]: which chapters of a page may still start by themselves."""

    def page_auto(self, path, client=None):
        response = (client or self.client).get(path)
        self.assertEqual(response.status_code, 200, path)
        return auto_of(response.get_data(as_text=True))

    def test_every_page_lists_its_own_chapters_also_under_the_first_time_offer(self):
        self.flags(DEV_FLAGS)
        self.register()
        # tour.js waits while the offer is open; a first answer after "Explore on my own"
        # still gets its tips on the same page load.
        self.assertEqual(self.page_auto("/"), ["chat-basics", "chat-answers", "chat-history"])
        self.client.post("/api/tour/complete", json={})
        expected = {"/": ["chat-basics", "chat-answers", "chat-history"],
                    "/lesson": ["lesson-start", "lesson-inside", "lesson-tutor", "lesson-test"],
                    "/projects": ["projects-start"], "/flashcards": ["flashcards-library"],
                    "/practice/today": ["todays-practice"], "/insights/mistakes": ["mistake-insights"],
                    "/dashboard": ["dashboard"], "/progress": ["progress"], "/profile": ["my-profile"],
                    "/leaderboard": ["leagues"], "/community": ["community-library", "community-set"],
                    "/settings": ["settings", "settings-plan"], "/tutorial": [], "/about": []}
        for path, chapters in expected.items():
            with self.subTest(path=path):
                self.assertEqual(self.page_auto(path), chapters)
        with application.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count()).select_from(M.TutorialProgress)), 0,
                             "listing them writes nothing")

    def test_skipped_and_finished_chapters_never_start_again_and_one_in_progress_resumes(self):
        self.register(settle=True)
        self.save("chat-basics", "chat-send", "skipped")
        self.save("chat-answers", self.last("chat-answers"), "completed")
        self.save("chat-history", "chat-menu")
        self.assertEqual(self.page_auto("/"), ["chat-history:chat-menu"])
        self.client.post("/api/tutorial/reset", json={"section": "chat-basics"})
        self.assertEqual(self.page_auto("/"), ["chat-basics", "chat-history:chat-menu"], "Restart makes it new")

    def test_switching_tips_off_stops_every_chapter_from_starting_by_itself(self):
        self.register(settle=True)
        response = self.client.post("/tutorial/tips", data={"back": "settings"})
        self.assertEqual(response.headers["Location"], "/settings#help")
        for path in ("/", "/lesson", "/projects", "/settings"):
            self.assertEqual(self.page_auto(path), [], path)
        self.assertFalse(self.progress()["auto_tips"])
        self.assertNotIn("checked", self.tips_box(self.client.get("/tutorial").get_data(as_text=True)))
        self.assertNotIn("checked", self.tips_box(self.client.get("/settings").get_data(as_text=True)))
        response = self.client.post("/tutorial/tips", data={"auto_tips": "on"})
        self.assertEqual(response.headers["Location"], "/tutorial#tips")
        self.assertEqual(self.page_auto("/"), ["chat-basics", "chat-answers", "chat-history"])
        self.assertIn("checked", self.tips_box(self.client.get("/tutorial").get_data(as_text=True)))
        self.client.post("/tutorial/tips", data={})
        self.client.post("/tutorial/tips", data={})
        with application.app.app_context():
            rows = db.session.scalars(db.select(M.TutorialProgress)).all()
            self.assertEqual([(row.section, row.status) for row in rows], [("_auto_tips", "off")], "one row, idempotent")

    @staticmethod
    def tips_box(html):
        form = html[html.index("data-tutorial-tips"):]
        return form[:form.index("</label>")].split('name="auto_tips"', 1)[1].split(">", 1)[0]


class PayloadEndpointTests(TutorialCase):
    def test_the_steps_endpoint_matches_the_page_signature_and_is_not_cached(self):
        self.register()
        response = self.client.get("/api/tutorial/steps")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        payload = response.get_json()
        page = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn(f'data-tutorial-sig="{payload["sig"]}"', page)
        self.assertEqual(payload["language"], "en")
        ids = [step["id"] for step in payload["steps"]]
        self.assertEqual(len(ids), len(set(ids)))
        for section in payload["sections"]:
            self.assertTrue(set(section["steps"]) <= set(ids))
        self.assertEqual(payload["hub"], "/tutorial")

    def test_the_signature_follows_language_and_capabilities(self):
        self.register()
        english = self.steps_payload()["sig"]
        self.client.post("/settings/language", data={"language": "de"})
        german = self.steps_payload()
        self.assertEqual(german["language"], "de")
        self.assertNotEqual(german["sig"], english)
        application.app.config.update(FEATURE_GAMIFICATION=False)
        without = self.steps_payload()
        self.assertNotEqual(without["sig"], german["sig"])
        self.assertNotIn("overview-xp", {step["id"] for step in without["steps"]})

    def test_free_and_premium_each_get_their_own_variants(self):
        self.register()
        free = self.visible_ids()
        self.assertTrue(FREE_ONLY <= free)
        self.assertFalse(PREMIUM_ONLY & free)
        self.premium()
        application._tutorial_payload_cached.cache_clear()
        premium = self.visible_ids()
        self.assertTrue(PREMIUM_ONLY <= premium)
        self.assertFalse(FREE_ONLY & premium, "a Premium student is never shown the Free plan's limits")
        self.assertEqual(free - FREE_ONLY, premium - PREMIUM_ONLY, "everything else is the same for both")

    def test_an_admin_gets_exactly_the_student_payload(self):
        self.register("alice")
        student = self.steps_payload()
        application.app.config.update(PREMIUM_ADMIN_IDENTIFIERS={"alice"}, AI_DIAGNOSTICS_ADMINS={"alice"},
                                      COMMUNITY_MODERATORS={"alice"})
        admin = self.steps_payload()
        self.assertEqual(admin, student)
        dumped = json.dumps(admin)
        for word in FORBIDDEN:
            self.assertNotIn(word, dumped)

    def test_the_old_sentence_never_reaches_any_student_in_any_language(self):
        self.flags(DEV_FLAGS)
        self.register()
        seen = []
        for language in ("en", "de", "fr", "es"):
            self.client.post("/settings/language", data={"language": language})
            for plan in ("free", "premium"):
                if plan == "premium" and language == "en":
                    self.premium()
                application._tutorial_payload_cached.cache_clear()
                dumped = json.dumps(self.steps_payload(), ensure_ascii=False)
                hub = self.client.get("/tutorial").get_data(as_text=True)
                for sentence in OLD_MISSING.values():
                    self.assertNotIn(sentence, dumped, f"{language} {plan}")
                    self.assertNotIn(sentence.replace("'", "&#39;"), hub)
                seen.append(language)
        self.assertEqual(len(seen), 8)


class PageTests(TutorialCase):
    def test_every_page_says_which_page_it_is(self):
        self.register()
        expected = {"/": "chat", "/assistant": "chat", "/lesson": "index", "/?goal=x": "index",
                    "/dashboard": "dashboard", "/projects": "projects", "/settings": "settings",
                    "/practice/today": "practice-today", "/about": "about"}
        for path, key in expected.items():
            with self.subTest(path=path):
                self.assertIn(f'data-tour-page="{key}"', self.client.get(path).get_data(as_text=True))
        seeded = self.seed()
        self.assertIn('data-tour-page="project-draft"', self.client.get(f"/projects/{seeded['draft']}").get_data(
            as_text=True), "a project waiting for its topic check is its own page for the tutorial")
        self.assertIn('data-tour-page="project"', self.client.get(f"/projects/{seeded['learned']}").get_data(
            as_text=True))
        self.assertIn('data-tour-page="exam-results"', self.client.get(f"/exams/{seeded['exam']}/results").get_data(
            as_text=True))
        grade_less = self.register("nograde", client=application.app.test_client(), grade="")
        page = grade_less.get("/onboarding/grade").get_data(as_text=True)
        self.assertIn('data-tour-page="onboarding"', page)
        self.assertIn('data-tutorial-auto=""', page)

    def test_the_first_time_offer_is_inline_in_the_students_language_until_settled(self):
        self.register(language="de")
        landing = self.client.get("/").get_data(as_text=True)
        self.assertIn('data-tour-autostart="true"', landing)
        self.assertIn('data-tour-page="chat"', landing)
        data = re.search(r'<script type="application/json" id="tourData" nonce="[^"]+">(.*?)</script>', landing, re.S)
        payload = json.loads(data.group(1))
        self.assertEqual(payload["ui"]["offer"]["title"], "Willkommen bei Learnova")
        self.assertEqual(payload["ui"]["offer"]["quick"], "Zeig mir alles (1 Minute)")
        self.assertEqual(payload["ui"]["offer"]["explore"], "Selbst entdecken")
        self.assertNotIn("full", payload["ui"]["offer"], "no Full tutorial any more")
        self.assertEqual(payload["language"], "de")
        self.client.post("/api/tour/complete", json={})
        settled = self.client.get("/").get_data(as_text=True)
        self.assertIn('data-tour-autostart="false"', settled)
        self.assertNotIn('id="tourData"', settled, "afterwards the steps are fetched once per tab")

    def test_the_shell_has_previous_next_exit_and_a_polite_announcer(self):
        self.register()
        page = self.client.get("/dashboard").get_data(as_text=True)
        shell = page.split('id="tourRoot"', 1)[1].split('id="tourAnnounce"', 1)[0]
        for marker in ("data-tour-prev", "data-tour-next", "data-tour-skip", 'id="tourChoices"', "data-tutorial-auto=",
                       'aria-labelledby="tourTitle"', 'aria-describedby="tourText"', "Exit tutorial", "Previous"):
            self.assertIn(marker, shell)
        self.assertNotIn("data-tour-go", shell, "no Take me there card inside a run any more")
        self.assertNotIn('aria-live', shell.split('<section class="tour-panel"', 1)[1].split(">", 1)[0],
                         "the panel itself is not a live region; #tourAnnounce is")
        self.assertIn('id="tourAnnounce" class="visually-hidden" role="status" aria-live="polite"', page)
        # Review UI M1: .visually-hidden is defined for every page, and the tour has its own copy.
        components = (ROOT / "static/css/learnova-components.css").read_text(encoding="utf-8")
        tour_css = (ROOT / "static/css/tour.css").read_text(encoding="utf-8")
        self.assertRegex(components, r"\n\.visually-hidden \{ position: absolute; width: 1px; height: 1px;")
        self.assertIn(".tour-root .visually-hidden { position: absolute; width: 1px; height: 1px;", tour_css)

    def test_the_tab_storage_is_tagged_per_account_and_names_nobody(self):
        self.register("alice")
        bob = self.register("bob", client=application.app.test_client())
        tag = re.compile(r'data-tutorial-user="([0-9a-f]{16})"')
        alice_tag = tag.search(self.client.get("/dashboard").get_data(as_text=True)).group(1)
        bob_tag = tag.search(bob.get("/dashboard").get_data(as_text=True)).group(1)
        self.assertNotEqual(alice_tag, bob_tag)
        self.assertEqual(alice_tag, tag.search(self.client.get("/").get_data(as_text=True)).group(1), "stable")
        with application.app.app_context():
            alice_id = self.user("alice").id
        self.assertNotEqual(str(alice_id), alice_tag)
        secret = str(application.app.config["SECRET_KEY"]).encode()
        self.assertEqual(alice_tag, hmac.new(secret, f"tutorial-user:{alice_id}".encode(), hashlib.sha256)
                         .hexdigest()[:16], "derived from the secret key")
        source = (ROOT / "static/js/tour.js").read_text(encoding="utf-8")
        self.assertIn("parseRun(storageGet(RUN_KEY), data, user)", source)
        self.assertIn("offerDismissed(storageGet(OFFER_KEY), user)", source)
        self.assertIn("[RUN_KEY, OFFER_KEY, PAYLOAD_KEY, HUB_FOCUS_KEY, INPUT_KEY, ENDED_KEY].forEach(storageRemove)", source,
                      "logging out clears the tab's tutorial keys")


class HookTests(TutorialCase):
    """Every hook a step or chapter anchor lights exists on its page (or in the script that
    draws it), so a template change cannot silently break the tutorial."""

    @staticmethod
    def hooks(selector):
        found = re.findall(r'\[data-tour-target="([^"]+)"\]', selector)
        if found:
            return [("target", found[-1])]
        out = []
        for part in selector.split(","):
            match = re.fullmatch(r"#([A-Za-z][\w-]*)(?::not\([^)]*\))?(?:\s.*)?", part.strip())
            if match:
                out.append(("id", match.group(1)))
        return out

    def test_every_lit_hook_and_anchor_is_on_its_page(self):
        self.flags(DEV_FLAGS)
        self.register()
        seeded = self.seed()
        context = self.progress()["context"]
        self.assertEqual(context["project_draft"], f"/projects/{seeded['draft']}")
        self.assertEqual(context["project"], f"/projects/{seeded['learned']}")
        self.assertIsNotNone(context["community_set"], "someone else's published set is readable")
        self.assertIsNotNone(context["study_session"], "the learned project has a planned day")

        def address(value):
            return context[value[1:]] if value.startswith("@") else value

        pages = {}
        for url in {address(value) for value in HOOK_PAGES.values()}:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            pages[url] = response.get_data(as_text=True)
        scripts = "".join((ROOT / name).read_text(encoding="utf-8") for name in JS_SOURCES)
        checked_steps, checked_sections = set(), set()
        for section in registry.SECTIONS:
            html = pages[address(HOOK_PAGES[section.page])]
            selectors = [(None, section.anchor)] if section.anchor else []
            selectors += [(step.id, selector) for step in section.steps for selector in step.targets[:1] + step.also]
            for step_id, selector in selectors:
                for kind, name in self.hooks(selector):
                    with self.subTest(section=section.id, step=step_id, selector=selector):
                        if kind == "id":
                            self.assertTrue(f'id="{name}"' in html or f'id="{name}"' in scripts
                                            or f'"{name}"' in scripts, name)
                        else:
                            self.assertTrue(f'data-tour-target="{name}"' in html
                                            or f'tourTarget = "{name}"' in scripts, name)
                        if step_id:
                            checked_steps.add(step_id)
                        checked_sections.add(section.id)
        self.assertGreater(len(checked_steps), 80)
        self.assertEqual(checked_sections, {section.id for section in registry.SECTIONS})

    def test_the_same_hook_serves_the_desktop_menus_and_the_phone_menu(self):
        self.flags(DEV_FLAGS)
        self.register()
        page = self.client.get("/dashboard").get_data(as_text=True)
        mobile = page.split('id="mobileAppMenu"', 1)[1].split("</nav>", 1)[0]
        more = page.split('id="moreMenu"', 1)[1].split("</div>", 1)[0]
        account = page.split('id="profileMenu"', 1)[1].split("</div>", 1)[0]
        desktop = page.split('<nav class="desktop-app-nav', 1)[1].split("</nav>", 1)[0]
        for name in ("flashcards", "community", "practice-today", "mistake-insights", "tutorial"):
            with self.subTest(name=name):
                self.assertIn(f'data-tour-target="{name}"', more)
                self.assertIn(f'data-tour-target="{name}"', mobile)
        for name in ("profile", "leagues", "settings", "tutorial"):
            with self.subTest(name=name):
                self.assertIn(f'data-tour-target="{name}"', account)
                self.assertIn(f'data-tour-target="{name}"', mobile)
        for name in ("chat", "overview", "new-lesson", "projects"):
            self.assertIn(f'data-tour-target="{name}"', desktop)
            self.assertIn(f'data-tour-target="{name}"', mobile)
        guest = application.app.test_client().get("/about").get_data(as_text=True)
        self.assertNotIn("data-tour-target", guest, "the guest menu is not touched")

    def test_the_script_drawn_hooks_and_the_menu_guards_are_kept(self):
        self.register()
        lesson = self.client.get("/lesson").get_data(as_text=True)
        self.assertIn('<div id="lessonParts" class="lesson-parts hidden">', lesson, "the engine-lesson fallback target")
        self.assertIn('id="loading"', lesson)
        script = (ROOT / "static/js/assistant.js").read_text(encoding="utf-8")
        for name in ("chat-copy", "chat-regenerate", "chat-retry", "chat-reply", "chat-chips", "chat-continue",
                     "chat-row-menu"):
            self.assertIn(f'tourTarget = "{name}"', script)
        self.assertIn('closest("#tourRoot")', script, "a tap on the tour card keeps the ⋯ menu open")
        navigation = (ROOT / "static/js/navigation.js").read_text(encoding="utf-8")
        self.assertIn('closest("#tourRoot")', navigation)


class SectionVisibilityTests(TutorialCase):
    """Who sees which chapter and step: flags (development and production values), the
    switches outside the flag dict, the plan - never the role."""

    def test_development_flags_show_everything_but_one_plan_variant(self):
        self.flags(DEV_FLAGS)
        self.register()
        payload = self.steps_payload()
        self.assertEqual([section["id"] for section in payload["sections"]], [s.id for s in registry.SECTIONS])
        switched_off_variants = {step.id for _section, step in registry.all_steps()
                                 if any(name.startswith("!") for name in step.requires)}
        self.assertEqual(switched_off_variants, {"profile-avatars-pick"})
        expected = {step.id for _section, step in registry.all_steps()} - PREMIUM_ONLY - switched_off_variants
        self.assertEqual({step["id"] for step in payload["steps"]}, expected)

    def test_production_flags_hide_the_imports_and_the_vocabulary_trainer_and_lead_nowhere_dead(self):
        self.flags(PROD_FLAGS)
        self.register()
        ids = self.visible_ids()
        self.assertFalse(PROD_HIDDEN & ids, PROD_HIDDEN & ids)
        for kept in ("practice-set", "practice-edit", "practice-study", "practice-start", "community-publish",
                     "community-report", "profile-league", "chat-photo", "chat-continue", "overview-xp"):
            self.assertIn(kept, ids)
        seeded = self.seed()
        hub = self.client.get("/tutorial").get_data(as_text=True)
        links = set(re.findall(r'<a class="(?:primary|secondary)-button" href="([^"]+)" data-tutorial-replay', hub))
        self.assertGreater(len(links), 15)
        for url in links:
            with self.subTest(url=url):
                self.assertIn(self.client.get(url.split("#")[0]).status_code, (200, 302), url)
        overview = self.client.get(f"/flashcards/{self.own_set_id()}").get_data(as_text=True)
        self.assertNotIn('data-tour-target="mode-', overview, "no mode card exists to point at")
        self.assertIn('data-tour-target="flashcard-modes"', overview, "the mode list step still has its list")
        self.assertIn(f'/projects/{seeded["draft"]}', hub)

    def own_set_id(self):
        with application.app.app_context():
            return db.session.scalar(db.select(M.FlashcardSet.id).where(M.FlashcardSet.user_id == self.user().id))

    def test_each_switch_removes_exactly_its_steps(self):
        self.flags(DEV_FLAGS)
        self.register()
        everything = self.visible_ids()
        cases = {
            "FEATURE_GAMIFICATION": {"overview-xp", "practice-progress", "practice-goal", "profile-challenges",
                                     "profile-avatars", "profile-league", "leagues-ladder", "leagues-standing",
                                     "overview-leagues", "profile-leagues-optout"},
            "FEATURE_LEAGUES": {"profile-league", "leagues-ladder", "leagues-standing", "overview-leagues",
                                "profile-leagues-optout"},
            "FEATURE_DAILY_GOALS": {"practice-goal"},
            "FEATURE_COMMUNITY_PUBLISHING": {"community-publish"},
            "FEATURE_COMMUNITY_MODERATION": {"community-report"},
            "FEATURE_CHAT_IMAGES": {"chat-photo"},
            "FEATURE_CONTINUE_LEARNING": {"chat-continue"},
            "FEATURE_VOCABULARY_TRAINER": {"practice-vocabulary"},
        }
        for flag, gone in cases.items():
            with self.subTest(flag=flag):
                self.flags({**DEV_FLAGS, flag: False})
                self.assertEqual(everything - self.visible_ids(), gone)
        self.flags({**DEV_FLAGS, "FEATURE_GAMIFICATION": False})
        self.assertIn("profile-avatars-pick", self.visible_ids(), "avatars still exist without XP")
        self.flags({**DEV_FLAGS, "FEATURE_PRIVATE_FLASHCARDS": False})
        payload = self.steps_payload()
        sections = {section["id"] for section in payload["sections"]}
        self.assertFalse({"flashcards-library", "flashcard-set", "flashcard-study"} & sections)
        self.assertNotIn("overview-flashcards", {step["id"] for step in payload["steps"]})
        self.flags({**DEV_FLAGS, "FEATURE_COMMUNITY_LIBRARY": False})
        payload = self.steps_payload()
        self.assertFalse({"community-library", "community-set"} & {section["id"] for section in payload["sections"]})
        self.assertNotIn("overview-community", {step["id"] for step in payload["steps"]})
        self.assertNotIn("community-library", self.progress()["sections"])

    def test_flashcards_switched_off_are_never_named(self):
        self.flags({**DEV_FLAGS, "FEATURE_PRIVATE_FLASHCARDS": False})
        self.register()
        # The community's sets are flashcard sets whether or not private sets are on.
        community = {"community-library", "community-set"}

        def named(word):
            return [step["id"] for step in self.steps_payload()["steps"] if step["section"] not in community
                    and step["id"] != "overview-community" and word in (step["title"] + " " + step["text"]).lower()]

        self.assertEqual(named("flashcard"), [])
        self.client.post("/settings/language", data={"language": "de"})
        self.assertEqual(named("karteikarte"), [])

    def test_a_student_who_switched_leagues_off_is_not_shown_a_league_card(self):
        self.flags(DEV_FLAGS)
        self.register()
        self.assertIn("profile-league", self.visible_ids())
        with application.app.app_context():
            self.user().leaderboard_enabled = False
            db.session.commit()
        application._tutorial_payload_cached.cache_clear()
        ids = self.visible_ids()
        self.assertNotIn("profile-league", ids)
        for kept in ("leagues-ladder", "leagues-standing", "profile-leagues-optout"):
            self.assertIn(kept, ids)
        self.assertNotIn('data-tour-target="league-card"', self.client.get("/profile").get_data(as_text=True))

    def test_an_admin_or_moderator_gets_the_student_tutorial_and_hub(self):
        self.flags(DEV_FLAGS)
        self.register("alice")
        student = self.steps_payload()
        hub = self.client.get("/tutorial").get_data(as_text=True)
        application.app.config.update(PREMIUM_ADMIN_IDENTIFIERS={"alice"}, AI_DIAGNOSTICS_ADMINS={"alice"},
                                      COMMUNITY_MODERATORS={"alice"})
        self.assertEqual(self.steps_payload(), student)
        admin_hub = self.client.get("/tutorial").get_data(as_text=True)
        main = admin_hub.split('id="main-content"', 1)[1].split("</main>", 1)[0]
        for word in FORBIDDEN:
            self.assertNotIn(word, main)
        self.assertEqual(re.findall(r'data-tutorial-section="([^"]+)"', admin_hub),
                         re.findall(r'data-tutorial-section="([^"]+)"', hub))


class HubTests(TutorialCase):
    """GET /tutorial: the quick tour, the feature groups with their chapters (Not seen /
    Skipped / Done, Replay or when it starts by itself), percent, Restart, Mark as done and
    the automatic-tips switch (static/js/tour.js runs the buttons)."""

    def hub(self, client=None):
        response = (client or self.client).get("/tutorial")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    @staticmethod
    def card(html, section):
        start = html.index(f'data-tutorial-section="{section}"')
        end = html.find("</li>", start)
        return html[start:end if end > 0 and section != "overview" else html.index("</section>", start)]

    def test_guests_are_sent_to_log_in(self):
        response = self.client.get("/tutorial")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_a_brand_new_student_sees_when_each_chapter_starts_instead_of_a_dead_end(self):
        self.flags(DEV_FLAGS)
        self.register()
        html = self.hub()
        self.assertIn('data-tour-page="tutorial"', html)
        self.assertIn("Welcome to Learnova", html)
        self.assertIn('role="progressbar" aria-valuenow="0" aria-valuemin="0" aria-valuemax="100"', html)
        self.assertIn("0% done", html)
        self.assertIn("data-tutorial-restart-all", html)
        self.assertEqual(re.findall(r'data-tutorial-section="([^"]+)"', html), [s.id for s in registry.SECTIONS])
        self.assertEqual(re.findall(r'data-tutorial-group="([^"]+)"', html),
                         ["chat", "lessons", "projects", "practice", "profile", "community", "account"])
        self.assertEqual(len(re.findall(r'<details class="tutorial-group" data-tutorial-group="[^"]+" open>', html)), 7,
                         "every group is open while something in it is left to do")
        tour = self.card(html, "overview")
        self.assertIn("Quick tour", tour)
        self.assertIn('data-tutorial-start="overview" data-tutorial-from=""', tour)
        self.assertIn(">Start</button>", tour)
        for section in registry.chapters():
            card = self.card(html, section.id)
            with self.subTest(chapter=section.id):
                self.assertIn('data-tutorial-status="not_started"', card)
                self.assertIn("Not seen", card)
                self.assertNotIn("data-tutorial-restart=", card)
                self.assertIn(f'data-tutorial-done="{section.id}"', card)
                self.assertIn(f'aria-describedby="tutorialSection-{section.id}"', card)
                if section.id in NEEDS_A_RECORD:
                    self.assertIn(f"<p class=\"tutorial-when\" data-tutorial-when>{section.when}</p>", card)
                    self.assertIn(f'href="{section.where}" aria-describedby', card, "Take me there")
                    self.assertIn("Take me there", card)
                    self.assertNotIn("data-tutorial-replay", card, "nothing to replay yet: no dead end")
                else:
                    self.assertIn(f'href="{section.url}" data-tutorial-replay="{section.id}"', card)
                    self.assertNotIn("data-tutorial-when", card)
        self.assertIn("Starts after your first question in the chat.", html)
        self.assertIn("Starts when you open a project whose topics are confirmed.", html)
        for line in ("Ask your AI tutor anything, also about a photo of your notes.",
                     "Find and share flashcard sets.", "Earn XP, unlock avatars and climb the leagues."):
            self.assertIn(line, html)
        self.assertIn('id="tourData"', html, "the hub always carries the steps inline")
        self.assertIn('name="auto_tips" checked', html)
        with application.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count()).select_from(M.TutorialProgress)), 0,
                             "the hub only reads")

    def test_with_the_students_own_records_every_chapter_can_be_replayed(self):
        self.flags(DEV_FLAGS)
        self.register()
        seeded = self.seed()
        html = self.hub()
        self.assertNotIn("data-tutorial-when", html, "only claimed when the record is really absent")
        context = self.progress()["context"]
        for section in registry.chapters():
            url = context[section.url[1:]] if section.url.startswith("@") else section.url
            with self.subTest(chapter=section.id):
                self.assertIn(f'href="{url}" data-tutorial-replay="{section.id}"', self.card(html, section.id))
        self.assertIn(f'href="/projects/{seeded["learned"]}" data-tutorial-replay="project-workspace"', html)

    def test_states_buttons_and_percent_follow_the_saved_progress(self):
        self.flags(DEV_FLAGS)
        self.register()
        self.save("lesson-start", "lessons-goal")
        self.save("projects-start", "projects-upload", "skipped")
        body = self.save("overview", self.last("overview"), "completed").get_json()
        html = self.hub()
        resumed = self.card(html, "lesson-start")
        state = self.progress()["sections"]["lesson-start"]
        self.assertIn('data-tutorial-status="in_progress"', resumed)
        self.assertIn(f"In progress · step {state['current']} of {state['total']}", resumed)
        self.assertIn('data-tutorial-replay="lesson-start" data-tutorial-from="lessons-goal"', resumed)
        self.assertIn(">Resume</a>", resumed)
        self.assertIn('data-tutorial-restart="lesson-start"', resumed)
        skipped = self.card(html, "projects-start")
        self.assertIn('data-tutorial-status="skipped"', skipped)
        self.assertIn("Skipped", skipped)
        self.assertIn(">Replay</a>", skipped)
        self.assertIn('data-tutorial-from=""', skipped, "a replay of a skipped chapter starts at the beginning")
        self.assertIn('data-tutorial-done="projects-start"', skipped)
        tour = self.card(html, "overview")
        self.assertIn('data-tutorial-status="completed"', tour)
        self.assertIn("Done", tour)
        self.assertIn(">Replay</button>", tour)
        self.assertNotIn("data-tutorial-done=", tour, "a finished section has nothing to mark")
        self.assertIn(f"{body['percent']}% done", html)
        self.assertIn(f'aria-valuenow="{body["percent"]}"', html)
        self.save("settings", self.last("settings"), "completed")
        self.assertIn('data-tutorial-status="completed"', self.card(self.hub(), "settings"))
        self.client.post("/api/tutorial/reset", json={"section": "lesson-start"})
        self.assertIn('data-tutorial-status="not_started"', self.card(self.hub(), "lesson-start"))
        self.client.post("/api/tutorial/reset", json={"section": "all"})
        html = self.hub()
        self.assertEqual(set(re.findall(r'data-tutorial-status="([^"]+)"', html)), {"not_started"})
        self.assertIn("0% done", html)
        with application.app.app_context():
            self.assertIsNone(self.user().tour_completed_at, "the hub never settles the offer on the server")

    def test_free_and_premium_see_the_same_chapters_with_their_own_counts(self):
        self.flags(DEV_FLAGS)
        self.register()
        free = self.hub()
        self.premium()
        application._tutorial_payload_cached.cache_clear()
        premium = self.hub()
        sections = re.compile(r'data-tutorial-section="([^"]+)"')
        self.assertEqual(sections.findall(free), sections.findall(premium))
        self.assertIn("Tips: 4", self.card(free, "projects-start"))
        self.assertIn("Tips: 4", self.card(premium, "projects-start"))
        self.assertIn("Tips: 4", self.card(free, "my-profile"))
        self.assertIn("Tips: 5", self.card(premium, "my-profile"), "the Premium badge tip")

    def test_switched_off_features_have_no_card_and_no_promise(self):
        self.flags({**DEV_FLAGS, "FEATURE_COMMUNITY_LIBRARY": False, "FEATURE_ASSISTANT_CHAT": False,
                    "FEATURE_GAMIFICATION": False, "FEATURE_PRIVATE_FLASHCARDS": False})
        self.register()
        html = self.hub()
        sections = re.findall(r'data-tutorial-section="([^"]+)"', html)
        for gone in ("community-library", "community-set", "chat-basics", "chat-answers", "flashcards-library",
                     "progress", "leagues"):
            self.assertNotIn(gone, sections)
        self.assertNotIn('data-tutorial-group="chat"', html)
        self.assertNotIn('data-tutorial-group="community"', html)
        main = html.split('id="main-content"', 1)[1].split("</main>", 1)[0]
        for line in ("Ask your AI tutor", "flashcard sets", "Earn XP", "Study with flashcards"):
            self.assertNotIn(line, main)
        self.assertIn("Build a short lesson with a test on any topic.", main)

    def test_the_hub_speaks_the_students_language(self):
        self.flags(DEV_FLAGS)
        self.register(language="de")
        html = self.hub()
        for german in ("Willkommen bei Learnova", "Eine Funktion nach der anderen", "Kurzer Überblick",
                       "Alle Tipps zurücksetzen", "Erledigt: 0 von 3", "Noch nicht gesehen", "Projektmodus", "Was Learnova kann",
                       "0 % geschafft", "Startet nach deiner ersten Frage im Chat.", "Tipps automatisch zeigen",
                       "Bring mich hin", "Tipps: 4"):
            self.assertIn(german, html)
        main = html.split('id="main-content"', 1)[1].split("</main>", 1)[0]
        self.assertNotIn("Kurzer Rundgang", html, "the quick tour and the hub (Rundgang) have their own names")
        self.assertIn("Damit setzt du alle Tipps", html, "Reset all tips asks first")
        for english in ("Learn one feature at a time", "Not seen", "Mark as done", "Quick tour", "Starts when",
                        "Starts after", "Show tips automatically", "Take me there", "Tips: "):
            self.assertNotIn(english, main)


class EntryPointTests(TutorialCase):
    """The hub is reachable from the navigation, the Overview, Settings ("Help and support")
    and the footer; every entry point is a plain link marked data-tutorial-link."""

    def test_five_entry_points_on_the_overview(self):
        self.register()
        page = self.client.get("/dashboard").get_data(as_text=True)
        self.assertEqual(page.count("data-tutorial-link"), 5)
        more = page.split('id="moreMenu"', 1)[1].split("</div>", 1)[0]
        account = page.split('id="profileMenu"', 1)[1].split("</div>", 1)[0]
        mobile = page.split('id="mobileAppMenu"', 1)[1].split("</nav>", 1)[0]
        footer = page.split('<footer class="global-footer">', 1)[1].split("</footer>", 1)[0]
        for part in (more, account, mobile, footer):
            self.assertIn('href="/tutorial"', part)
            self.assertIn("data-tutorial-link", part)
        self.assertIn('<a class="text-button tour-invite" href="/tutorial" data-tutorial-link>', page)
        self.assertIn("New here? Open the tutorial", page)
        hub = self.client.get("/tutorial").get_data(as_text=True)
        self.assertIn('href="/tutorial" aria-current="page" data-tutorial-link', hub, "the menus say where you are")

    def test_settings_help_and_support_opens_the_tutorial_first_and_has_the_tips_switch(self):
        self.register()
        page = self.client.get("/settings").get_data(as_text=True)
        section = page[page.index('id="help"'):]
        section = section[:section.index("</section>")]
        self.assertIn("Help and support", section)
        self.assertIn('<a class="primary-button" href="/tutorial" data-tutorial-link>Open the tutorial</a>', section)
        self.assertLess(section.index('href="/tutorial"'), section.index('href="/about"'))
        self.assertIn('href="/contact"', section)
        self.assertIn('action="/tutorial/tips"', section)
        self.assertIn('name="back" value="settings"', section)
        self.assertIn("Show tips automatically", section)
        self.assertEqual(application.app.test_client().get("/settings").status_code, 302, "Settings is for students")

    def test_the_footer_links_the_hub_only_for_signed_in_students(self):
        guest = self.client.get("/about").get_data(as_text=True)
        self.assertNotIn('href="/tutorial"', guest.split('<footer class="global-footer">', 1)[1])
        self.register()
        page = self.client.get("/about").get_data(as_text=True)
        self.assertIn('href="/tutorial" data-tutorial-link', page.split('<footer class="global-footer">', 1)[1])


class SmallFixTests(TutorialCase):
    """PLAN 7.5: what the tutorial points at says the right thing."""

    def make_set(self):
        created = self.client.post("/api/flashcards/sets", json={
            "title": "Physik", "subject": "Physics", "cards": [{"type": "question_answer", "front": "v", "back": "s/t"}]})
        self.assertIn(created.status_code, (200, 201), created.get_data(as_text=True)[:200])
        return created.get_json()["id"]

    def test_mode_cards_describe_their_mode_in_every_language(self):
        self.flags(DEV_FLAGS)
        self.register()
        set_id = self.make_set()
        expected = {"en": "Adaptive practice from recognising to recalling.",
                    "de": "Adaptives Üben von Erkennen bis Abrufen.",
                    "fr": FR["Adaptive practice from recognising to recalling."],
                    "es": ES["Adaptive practice from recognising to recalling."]}
        for language, sentence in expected.items():
            with self.subTest(language=language):
                self.client.post("/settings/language", data={"language": language})
                page = self.client.get(f"/flashcards/{set_id}").get_data(as_text=True)
                self.assertNotIn("mode description", page)
                self.assertIn(sentence, page)

    def test_review_mistakes_never_opens_a_switched_off_mode(self):
        self.flags({**DEV_FLAGS, "FEATURE_FLASHCARD_LEARN_MODE": False})
        self.register()
        set_id = self.make_set()
        page = self.client.get(f"/flashcards/{set_id}/study").get_data(as_text=True)
        self.assertIn(f'id="reviewWeak" class="ghost-button" href="/flashcards/{set_id}/study?objective=weak"', page)
        self.assertEqual(self.client.get(f"/flashcards/{set_id}/learn").status_code, 404, "the old link's target")

    def test_each_workspace_stage_has_one_topics_section_and_its_own_hook(self):
        self.flags(DEV_FLAGS)
        self.register()
        seeded = self.seed()
        draft = self.client.get(f"/projects/{seeded['draft']}").get_data(as_text=True)
        learned = self.client.get(f"/projects/{seeded['learned']}").get_data(as_text=True)
        for html, hook, other in ((draft, "project-topic-review", "project-topic-list"),
                                  (learned, "project-topic-list", "project-topic-review")):
            self.assertEqual(html.count('id="topics"'), 1)
            self.assertIn(f'data-tour-target="{hook}"', html)
            self.assertNotIn(f'data-tour-target="{other}"', html)
        self.assertIn('data-tour-target="project-edit-topics"', draft)
        self.assertNotIn('data-tour-target="project-edit-topics"', learned, "stage C's copy has no hook")


class TranslationTests(unittest.TestCase):
    CALL = re.compile(r"""_\(\s*(['"])((?:\\.|(?!\1).)*)\1""")
    MODE = re.compile(r"""\('(?:learn|test|match|blast|blocks)','[A-Za-z]+','[^']+',feature_flags\.\w+,'([^']+)'\)""")

    def template_msgids(self):
        found = set()
        for name in TUTORIAL_TEMPLATES:
            text = (ROOT / name).read_text(encoding="utf-8")
            found |= {match.group(2).replace("\\'", "'") for match in self.CALL.finditer(text)}
            found |= set(self.MODE.findall(text))
        return found

    def test_every_msgid_of_the_tutorial_templates_is_translated(self):
        msgids = self.template_msgids()
        for expected in ("Learn one feature at a time", "Show tips automatically", "Tips: {count}", "Not seen",
                         "Replay"):
            self.assertIn(expected, msgids)
        for msgid in sorted(msgids):
            for language, table in (("de", catalog.GERMAN), ("fr", FR), ("es", ES)):
                with self.subTest(language=language, msgid=msgid):
                    self.assertIn(msgid, table)
                    self.assertEqual(sorted(PLACEHOLDER.findall(msgid)), sorted(PLACEHOLDER.findall(table[msgid])))

    def test_no_english_step_text_reaches_a_german_french_or_spanish_student(self):
        flags = {name: True for name in application.feature_flag_values()}
        facts = {"price": "3,00", "league_up": "10", "league_down": "10", "league_size": "30", "username_days": "30",
                 "pages": "40", "languages": "English, Deutsch"}
        for premium in (False, True):
            caps = visibility.capabilities(flags, chat_images=True, continue_learning=True, leagues=True,
                                           premium=premium)
            english = visibility.payload(caps, "en", translate=catalog.translate, facts=facts)
            for language in ("de", "fr", "es"):
                other = visibility.payload(caps, language, translate=catalog.translate, facts=facts)
                source = {(step["id"], key): step[key] for step in english["steps"] for key in ("title", "text")}
                # "Avatars" and "Flashcards" are also the French interface words.
                allowed = SAME_IN_GERMAN | ({"Avatars", "Flashcards"} if language == "fr" else {"Avatars"})
                same = [(step["id"], key) for step in other["steps"] for key in ("title", "text")
                        if step[key] == source[(step["id"], key)] and step[key] not in allowed]
                sections = {item["id"]: item["title"] for item in english["sections"]}
                titles = [item["id"] for item in other["sections"]
                          if item["title"] == sections[item["id"]] and item["title"] not in allowed]
                with self.subTest(language=language, premium=premium):
                    self.assertEqual(same, [])
                    self.assertEqual(titles, [])
                    self.assertEqual([key for key, value in other["ui"].items()
                                      if isinstance(value, str) and value == english["ui"][key]], [])
                    self.assertTrue(all("{" not in step["text"] for step in other["steps"]), "every fact is filled")

    def test_every_tutorial_msgid_is_translated_with_the_same_placeholders(self):
        msgids = registry.all_msgids()
        self.assertGreater(len(msgids), 120)
        for msgid in sorted(msgids):
            for language, table in (("de", catalog.GERMAN), ("fr", FR), ("es", ES)):
                with self.subTest(language=language, msgid=msgid):
                    self.assertIn(msgid, table)
                    self.assertEqual(sorted(PLACEHOLDER.findall(msgid)), sorted(PLACEHOLDER.findall(table[msgid])))
                    visibility.fill(catalog.translate(msgid, language), {"price": "3,00", "page": "x"})
        self.assertEqual(catalog.SUPPORTED_LANGUAGES, ("en", "de", "fr", "es"))

    def test_the_buttons_say_previous_skip_and_exit(self):
        self.assertEqual(registry.UI["previous"], "Previous")
        self.assertEqual(catalog.translate("Previous", "fr"), "Précédent")
        self.assertEqual(catalog.translate("Back", "de"), "Rückseite", "the msgid the button must not use")
        self.assertEqual(catalog.translate("Exit tutorial", "de"), "Rundgang beenden")
        self.assertEqual(catalog.translate("Skip", "de"), "Überspringen")

    def test_french_and_spanish_speak_to_the_student_informally(self):
        for msgid in ("Type your question here. Ask about your homework, a topic or an exam, and add follow-up "
                      "questions whenever something is unclear.", "Tap to answer", "How would you like to start?",
                      "Show me around (1 minute)", "Starts after your first question in the chat.",
                      "When you open a feature for the first time, a few short tips explain it."):
            self.assertNotRegex(FR[msgid], r"\bvous\b", msgid)
            self.assertNotRegex(ES[msgid], r"\busted\b", msgid)
            self.assertNotRegex(catalog.GERMAN[msgid], r"\bSie\b", msgid)


if __name__ == "__main__":
    unittest.main()
