"""The test suite must never touch a real database.

This guards a failure that actually happened rather than a hypothetical one. Three test
modules never set `DATABASE_URL`, and pytest imports modules alphabetically, so
`test_ai_gateway.py` was the first to `import app` and configured the whole session
against the default database — `instance/numeri.db`, a developer's real one. Later
`setdefault` calls were no-ops. `test_ai_observability.py` and many `setUp` methods then
called `db.drop_all()`, so simply running the suite emptied every table of real data.

`tests/conftest.py` now sets `DATABASE_URL` to a scratch file before any test module is
imported. These tests prove the guard is in place and holds.
"""

import os
import unittest
from pathlib import Path

import app as application

from .conftest import TEST_DATABASE_PATH


class DatabaseIsolationTests(unittest.TestCase):
    def test_the_configured_database_is_the_scratch_file(self):
        configured = application.app.config["SQLALCHEMY_DATABASE_URI"]
        self.assertIn(TEST_DATABASE_PATH.as_posix(), configured.replace("\\", "/"))

    def test_the_app_is_not_pointed_at_real_instance_data(self):
        configured = application.app.config["SQLALCHEMY_DATABASE_URI"]
        for forbidden in ("numeri.db", "learnova.db"):
            self.assertNotIn(forbidden, configured)

    def test_the_scratch_database_is_not_inside_the_repository(self):
        # instance/ is where the real data lives; the scratch file belongs in temp.
        repository = Path(application.__file__).resolve().parent
        self.assertFalse(
            TEST_DATABASE_PATH.resolve().is_relative_to(repository),
            f"{TEST_DATABASE_PATH} is inside the repository")

    def test_dropping_tables_cannot_reach_instance_data(self):
        # The exact operation that caused the loss, run deliberately: it must land on the
        # scratch file and leave the real database alone.
        real = Path(application.app.instance_path) / "numeri.db"
        before = real.stat().st_mtime if real.exists() else None
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        if before is not None:
            self.assertEqual(real.stat().st_mtime, before,
                             "drop_all touched the real database")

    def test_usage_accounting_and_cache_stay_out_of_instance(self):
        for name in ("AI_USAGE_PATH", "AI_CACHE_DIR"):
            value = os.environ.get(name, "")
            self.assertNotIn("instance", value.replace("\\", "/").split("/"),
                             f"{name} writes into instance/: {value!r}")


class EveryTestModuleIsCoveredTests(unittest.TestCase):
    """The guard must not depend on individual modules remembering to set the variable."""

    def test_conftest_sets_the_variable_unconditionally(self):
        source = (Path(__file__).resolve().parent / "conftest.py").read_text(encoding="utf-8")
        self.assertIn('os.environ["DATABASE_URL"] =', source)
        self.assertNotIn('os.environ.setdefault("DATABASE_URL"', source)

    def test_a_refusal_fires_if_something_overrides_it(self):
        source = (Path(__file__).resolve().parent / "conftest.py").read_text(encoding="utf-8")
        self.assertIn("def pytest_configure", source)
        self.assertIn("Refusing to run", source)


if __name__ == "__main__":
    unittest.main()
