"""The suite never writes into instance/ (FIX_PLAN PF5 §5.1).

Uploaded flashcard and vocabulary photos default to instance/flashcard_imports. Before
tests/conftest.py redirected FLASHCARD_IMPORT_STORAGE_DIR, every test that uploaded a photo
without its own directory left the file in the developer's real instance folder (256 orphan
files are still there; they are the owner's to judge and are never deleted by the suite).
"""

import ast
import importlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import app as application

from . import conftest

TESTS = Path(__file__).resolve().parent

# The base classes whose setUp changed app.config (or the limiter) for the rest of the session
# until the final cleanup (2026-11-03). Each now restores what it changes with addCleanup
# (tests/config_isolation.py). TESTING=True is the one value they all set and deliberately
# leave: it is the safe direction (no live AI, no rate limits), and later classes without a
# setUp of their own rely on it.
CONFIGURING_BASES = (
    ("test_assistant_ui", "RenderedPageTests"),
    ("test_community", "CommunityApiTests"),
    ("test_create_response_wrapper", "WrapperScopeTests"),
    ("test_create_response_wrapper", "AssistantEndToEndTests"),
    ("test_curriculum", "CurriculumIntegrationTests"),
    ("test_dashboard", "DashboardVocabularyWidgetTests"),
    ("test_document_processing", "DocumentWorkflowTests"),
    ("test_exam_autopilot_integration", "ExamAutopilotTests"),
    ("test_flashcard_imports", "FlashcardImportWorkflowTests"),
    ("test_flashcard_modes_gamification", "FlashcardModeWorkflowTests"),
    ("test_flashcards_publish", "PublishTests"),
    ("test_handwriting_second_look", "SecondLookFlowTests"),
    ("test_lesson_parts_frontend", "LessonPageTests"),
    ("test_ocr_cost", "DeterministicCachingTests"),
    ("test_pg_fk_integrity", "AppTestCase"),
    ("test_premium", "PremiumTests"),
    ("test_project_dashboard", "DashboardTests"),
    ("test_project_documents", "Base"),
    ("test_project_extraction", "ExtractionTestCase"),
    ("test_project_mode_v2", "ProjectModeV2Tests"),
    ("test_quick_start", "QuickStartTests"),
    ("test_release_fixes", "CommunityFixTests"),
    ("test_release_fixes", "ProjectFlowFixTests"),
    ("test_release_flow", "ReleaseFlowTests"),
    ("test_subscriptions", "AppTestCase"),
    ("test_upload_robustness", "Base"),
    ("test_vocabulary", "VocabularyWorkflowTests"),
    ("test_vocabulary", "ManualVocabularyEntryTests"),
    ("test_vocabulary_import_ui", "VocabularyImportPageTests"),
    ("test_vocabulary_kinds", "PracticeFilterTests"),
    ("test_vocabulary_review", "ValidateEndpointTests"),
    ("test_vocabulary_scanner", "ScannerFlowTests"),
    ("test_vocabulary_table", "LocalFirstImportTests"),
    ("test_vocabulary_textbook", "TextbookImportTests"),
)


def config_keys_set_in_setup(module_name: str, class_name: str) -> list[str]:
    """The app.config keys the class's own setUp sets (`*.config.update(...)`, `*.config[k] = ...`)."""

    tree = ast.parse((TESTS / f"{module_name}.py").read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    setup = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "setUp")
    keys: list[str] = []
    for node in ast.walk(setup):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "update" \
                and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "config":
            keys += [keyword.arg for keyword in node.keywords if keyword.arg]
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Attribute) \
                        and target.value.attr == "config" and isinstance(target.slice, ast.Constant):
                    keys.append(str(target.slice.value))
    return [key for key in dict.fromkeys(keys) if key != "TESTING"]


class _Planted:
    """A value no setUp can produce: if it is still there afterwards, the key was put back."""

    def __repr__(self) -> str:
        return "<planted>"


def inside(value, parent) -> bool:
    child = os.path.normcase(os.path.realpath(os.path.abspath(str(value))))
    base = os.path.normcase(os.path.realpath(os.path.abspath(str(parent))))
    return child == base or child.startswith(base.rstrip("\\/") + os.sep)


class ImportStorageIsolationTests(unittest.TestCase):
    def test_import_storage_is_temporary(self):
        # Test modules may point app.config at their own temporary folder; either way it is
        # temporary, never the instance folder.
        for value in (application.app.config["FLASHCARD_IMPORT_STORAGE_DIR"],
                      os.environ.get("FLASHCARD_IMPORT_STORAGE_DIR", "")):
            self.assertTrue(value, "the storage directory is configured")
            self.assertTrue(inside(value, tempfile.gettempdir()), value)
            self.assertFalse(inside(value, application.app.instance_path), value)

    def test_paths_into_instance_are_refused(self):
        self.assertEqual(Path(conftest.INSTANCE_DIR).resolve(), Path(application.app.instance_path).resolve())
        self.assertEqual(conftest.scratch_paths_inside_instance(), [], "this session is clean")
        environment = {
            "FLASHCARD_IMPORT_STORAGE_DIR": str(conftest.INSTANCE_DIR / "flashcard_imports"),
            "AI_CACHE_DIR": str(conftest.INSTANCE_DIR),
            "AI_USAGE_PATH": str(Path(tempfile.gettempdir()) / "usage.jsonl"),
        }
        self.assertEqual(conftest.scratch_paths_inside_instance(environment),
                         ["FLASHCARD_IMPORT_STORAGE_DIR", "AI_CACHE_DIR"])
        self.assertFalse(conftest.path_is_inside(str(conftest.INSTANCE_DIR) + "_copy", conftest.INSTANCE_DIR),
                         "a sibling whose name merely starts the same is not inside")
        self.assertFalse(conftest.path_is_inside("", conftest.INSTANCE_DIR))


class InstanceSnapshotTests(unittest.TestCase):
    def test_changes_are_reported_by_kind(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            (base / "kept.txt").write_text("same")
            (base / "edited.txt").write_text("before")
            (base / "gone.txt").write_text("x")
            before = conftest.instance_snapshot(base)
            (base / "edited.txt").write_text("after, longer")
            (base / "gone.txt").unlink()
            (base / "nested").mkdir()
            (base / "nested" / "new.png").write_bytes(b"png")
            changes = conftest.instance_changes(before, conftest.instance_snapshot(base))
        self.assertEqual(changes, {"added": ["nested/new.png"], "removed": ["gone.txt"], "changed": ["edited.txt"]})
        self.assertEqual(conftest.instance_snapshot(Path(tempfile.gettempdir()) / "pf5-missing-dir"), {})


class SetUpLeavesNoConfigBehindTests(unittest.TestCase):
    """Probe: after each base class's whole test lifecycle - setUpClass, setUp, a test,
    tearDown, cleanups, tearDownClass - every config key its setUp changes (and the limiter
    switch) is back. Run here, after the classes themselves, so it does not depend on the
    order pytest happens to collect the files in."""

    def test_every_configuring_base_class_restores_what_it_changes(self):
        self.maxDiff = None
        config = application.app.config
        pristine = dict(config)
        limiter_before = application.limiter.enabled
        self.addCleanup(setattr, application.limiter, "enabled", limiter_before)

        def put_back():
            config.clear()
            config.update(pristine)

        self.addCleanup(put_back)
        leaks = {}
        for module_name, class_name in CONFIGURING_BASES:
            base = getattr(importlib.import_module(f"tests.{module_name}"), class_name)
            probe = type(f"Probe{class_name}", (base,), {"test_probe": lambda self: None})
            probe.__module__ = base.__module__
            keys = config_keys_set_in_setup(module_name, class_name)
            self.assertTrue(keys, f"{module_name}.{class_name}: the scan found its config keys")
            planted = {key: _Planted() for key in keys}
            config.update(planted)
            limiter_planted = _Planted()
            application.limiter.enabled = limiter_planted   # truthy: limits on, TESTING exempts them
            result = unittest.TestResult()
            unittest.TestSuite([probe("test_probe")]).run(result)
            self.assertEqual(result.errors + result.failures, [], f"{module_name}.{class_name} ran")
            left = sorted(key for key, value in planted.items() if config.get(key) is not value)
            if application.limiter.enabled is not limiter_planted:
                left.append("limiter.enabled")
            if left:
                leaks[f"{module_name}.{class_name}"] = left
            put_back()
            application.limiter.enabled = limiter_before
        self.assertEqual(leaks, {}, "setUp changed these and never put them back")


class SqliteForeignKeyOptInTests(unittest.TestCase):
    def test_the_listener_enforces_foreign_keys_on_sqlite(self):
        connection = sqlite3.connect(":memory:")
        try:
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 0)
            conftest.sqlite_foreign_keys_on(connection, None)
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        finally:
            connection.close()

    def test_other_drivers_are_left_alone(self):
        class Driver:
            def cursor(self):
                raise AssertionError("must not touch a non-SQLite connection")

        conftest.sqlite_foreign_keys_on(Driver(), None)


if __name__ == "__main__":
    unittest.main()
