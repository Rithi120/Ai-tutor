"""Boot-time migration safety and the PostgreSQL connection pool (FIX_PLAN PF5, R4/R5/R9).

PostgreSQL is not available to the suite, so the PostgreSQL side is proven with a fake engine
that records the SQL it is given, a real SQLite engine for SQLAlchemy's event machinery, and
configure_app with a PostgreSQL URL (configuring never connects). Nothing here can tell
whether a real server honours the lock and the timeout: that is the owner's run of
R:/ai_tutor_suite_tmp/prod_pg/run_pg_migration_test.py against a disposable PostgreSQL.
"""

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

import app as application
from learnova import db_ops
from learnova.config import configure_app

LOCK = "SELECT pg_advisory_lock(:key)"
UNLOCK = "SELECT pg_advisory_unlock(:key)"
KEY = {"key": db_ops.MIGRATION_LOCK_KEY}


class FakeConnection:
    def __init__(self, log, fail_on=""):
        self.log = log
        self.fail_on = fail_on

    def detach(self):
        self.log.append("detach")

    def execute(self, statement, parameters=None):
        sql = str(statement)
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("the server went away")
        self.log.append((sql, dict(parameters or {})))

    def commit(self):
        self.log.append("commit")

    def close(self):
        self.log.append("close")


class FakeDialect:
    def __init__(self, name):
        self.name = name


class FakeEngine:
    def __init__(self, dialect_name, fail_on=""):
        self.dialect = FakeDialect(dialect_name)
        self.log = []
        self.connections = 0
        self.fail_on = fail_on

    def connect(self):
        self.connections += 1
        return FakeConnection(self.log, self.fail_on)


class MigrationLockTests(unittest.TestCase):
    """R4: one process migrates at a time on PostgreSQL."""

    def test_advisory_lock_and_timeout_postgresql_takes_and_releases_the_lock(self):
        engine = FakeEngine("postgresql")
        with db_ops.migration_lock(engine):
            engine.log.append("migrations")
        self.assertEqual(engine.log, [
            "detach", (LOCK, KEY), "commit",          # its own server session, not left in a transaction
            "migrations",
            (UNLOCK, KEY), "commit", "close",
        ])
        self.assertEqual(engine.connections, 1)

    def test_the_lock_is_released_when_a_migration_fails(self):
        engine = FakeEngine("postgresql")
        with self.assertRaises(ValueError):
            with db_ops.migration_lock(engine):
                raise ValueError("lock timeout on ALTER TABLE")
        self.assertEqual(engine.log[-3:], [(UNLOCK, KEY), "commit", "close"])

    def test_a_failed_unlock_still_closes_and_keeps_the_original_error(self):
        # Closing the detached connection ends the server session, which releases the lock.
        engine = FakeEngine("postgresql", fail_on="pg_advisory_unlock")
        with self.assertRaises(ValueError):
            with db_ops.migration_lock(engine):
                raise ValueError("the migration's own error")
        self.assertEqual(engine.log[-1], "close")

    def test_the_lock_lives_on_its_own_detached_connection_of_a_real_engine(self):
        # A real SQLAlchemy engine (SQLite, with the two PostgreSQL functions registered as
        # recorders) under a PostgreSQL name: proves the Connection calls are real API, the
        # lock is not on a pooled connection, and closing really closes it.
        engine = create_engine("sqlite://")
        calls = []

        @event.listens_for(engine, "connect")
        def register(dbapi_connection, _record):
            for name in ("pg_advisory_lock", "pg_advisory_unlock"):
                dbapi_connection.create_function(
                    name, 1, lambda key, name=name, connection=dbapi_connection: calls.append(
                        (name, key, id(connection))) or 1)

        class AsPostgresql:
            dialect = FakeDialect("postgresql")

            def connect(self):
                return engine.connect()

        with db_ops.migration_lock(AsPostgresql()):
            with engine.connect() as other:          # the migrations' own connection
                other.execute(text("SELECT 1"))
                migration_connection = id(other.connection.dbapi_connection)
        self.assertEqual([(name, key) for name, key, _ in calls],
                         [("pg_advisory_lock", db_ops.MIGRATION_LOCK_KEY),
                          ("pg_advisory_unlock", db_ops.MIGRATION_LOCK_KEY)])
        self.assertEqual(calls[0][2], calls[1][2], "locked and unlocked on the same connection")
        self.assertNotEqual(calls[0][2], migration_connection, "never the connection that migrates")
        engine.dispose()

    def test_sqlite_is_a_no_op(self):
        engine = FakeEngine("sqlite")
        with db_ops.migration_lock(engine):
            engine.log.append("migrations")
        self.assertEqual(engine.log, ["migrations"])
        self.assertEqual(engine.connections, 0)


class LockTimeoutTests(unittest.TestCase):
    """R5: a boot that would queue behind a busy table gives up instead."""

    def test_the_statement_only_on_postgresql_and_only_when_on(self):
        self.assertEqual(db_ops.lock_timeout_statement("postgresql", 10000), "SET LOCAL lock_timeout = '10000ms'")
        self.assertEqual(db_ops.lock_timeout_statement("postgresql", "2500"), "SET LOCAL lock_timeout = '2500ms'")
        self.assertIsNone(db_ops.lock_timeout_statement("sqlite", 10000))
        self.assertIsNone(db_ops.lock_timeout_statement("postgresql", 0))
        self.assertIsNone(db_ops.lock_timeout_statement("postgresql", -5))

    def recorded(self, engine):
        statements = []
        event.listen(engine, "before_cursor_execute",
                     lambda _conn, _cursor, statement, *_rest: statements.append(statement))
        return statements

    def test_every_transaction_inside_the_block_starts_with_the_timeout_and_none_after(self):
        # A real engine, so SQLAlchemy's own `begin` event and nested execution are what is
        # tested; only the statement is swapped for one SQLite understands.
        engine = create_engine("sqlite://")
        statements = self.recorded(engine)
        probe = "SELECT 'lock-timeout-probe'"
        with patch.object(db_ops, "lock_timeout_statement", return_value=probe) as statement:
            with db_ops.migration_lock_timeout(engine, 10000):
                with engine.begin() as connection:              # how the migrations run their DDL
                    connection.execute(text("SELECT 1"))
                with engine.connect() as connection:            # autobegin (reflection, inspect)
                    connection.execute(text("SELECT 2"))
                    connection.commit()
                with Session(engine) as session:                # the ORM session (publication backfill)
                    session.execute(text("SELECT 3"))
                    session.commit()
        statement.assert_called_once_with("sqlite", 10000)
        with engine.begin() as connection:
            connection.execute(text("SELECT 4"))
        self.assertEqual(statements, [probe, "SELECT 1", probe, "SELECT 2", probe, "SELECT 3", "SELECT 4"])
        engine.dispose()

    def test_the_listener_is_removed_when_the_block_raises(self):
        engine = create_engine("sqlite://")
        statements = self.recorded(engine)
        with patch.object(db_ops, "lock_timeout_statement", return_value="SELECT 'probe'"):
            with self.assertRaises(RuntimeError):
                with db_ops.migration_lock_timeout(engine, 10000):
                    raise RuntimeError("canceling statement due to lock timeout")
        with engine.begin() as connection:
            connection.execute(text("SELECT 5"))
        self.assertEqual(statements, ["SELECT 5"])
        engine.dispose()

    def test_sqlite_and_zero_register_nothing(self):
        engine = create_engine("sqlite://")
        statements = self.recorded(engine)
        for milliseconds in (10000, 0):
            with db_ops.migration_lock_timeout(engine, milliseconds):
                with engine.begin() as connection:
                    connection.execute(text("SELECT 6"))
        self.assertEqual(statements, ["SELECT 6", "SELECT 6"])
        engine.dispose()


class EnsureDatabaseTests(unittest.TestCase):
    """ensure_database() runs inside the lock and the timeout, and stays idempotent."""

    def setUp(self):
        application.app.config.update(TESTING=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
            application.ensure_database()

    def test_advisory_lock_and_timeout_wrap_the_whole_migration_body(self):
        events = []

        @contextmanager
        def lock(engine):
            events.append(("lock", engine is application.db.engine))
            yield
            events.append("unlock")

        @contextmanager
        def timeout(engine, milliseconds):
            events.append(("timeout", engine is application.db.engine, milliseconds))
            yield
            events.append("timeout off")

        original_create_all = application.db.create_all

        def create_all(*args, **kwargs):
            events.append("create_all")
            return original_create_all(*args, **kwargs)

        with patch.object(application.db_ops, "migration_lock", lock), \
                patch.object(application.db_ops, "migration_lock_timeout", timeout), \
                patch.object(application.db, "create_all", create_all):
            application.ensure_database()
        configured = application.app.config["DB_MIGRATION_LOCK_TIMEOUT_MS"]
        self.assertEqual(configured, 10000, "the documented default")
        self.assertEqual(events, [("lock", True), ("timeout", True, configured), "create_all",
                                  "timeout off", "unlock"])

    def test_a_second_run_executes_no_ddl_or_dml(self):
        statements = []

        def record(_conn, _cursor, statement, *_rest):
            statements.append(" ".join(statement.split()))

        with application.app.app_context():
            engine = application.db.engine
            event.listen(engine, "before_cursor_execute", record)
            try:
                application.ensure_database()
            finally:
                event.remove(engine, "before_cursor_execute", record)
        reads = ("SELECT", "PRAGMA", "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE", "WITH")
        writes = [sql for sql in statements if sql.split(" ", 1)[0].upper() not in reads]
        self.assertTrue(statements, "the recorder saw the run")
        self.assertEqual(writes, [])


def configured(**environment):
    keys = ("DATABASE_URL", "DB_POOL_RECYCLE_SECONDS", "DB_MIGRATION_LOCK_TIMEOUT_MS")
    with patch.dict(os.environ, {"APP_ENV": "testing", "AI_MODE": "cached"}):
        for name in keys:
            os.environ.pop(name, None)
        os.environ.update(environment)
        flask_app = Flask("pf5-db-config-test", instance_path=tempfile.mkdtemp())
        configure_app(flask_app, "testing")
        return flask_app


class EnginePoolTests(unittest.TestCase):
    """R9: idle PostgreSQL connections are revalidated; SQLite is left alone."""

    def test_engine_options_for_postgresql(self):
        flask_app = configured(DATABASE_URL="postgresql://user:secret@db.example.test/learnova_test")
        self.assertEqual(flask_app.config["SQLALCHEMY_ENGINE_OPTIONS"],
                         {"hide_parameters": True, "pool_pre_ping": True, "pool_recycle": 280})
        # Flask-SQLAlchemy hands them to the engine (created lazily; nothing connects).
        extension = SQLAlchemy()
        extension.init_app(flask_app)
        with flask_app.app_context():
            engine = extension.engine
            self.assertEqual(engine.dialect.name, "postgresql")
            self.assertTrue(engine.pool._pre_ping)
            self.assertEqual(engine.pool._recycle, 280)
            engine.dispose()

    def test_the_retired_postgres_alias_gets_them_too(self):
        flask_app = configured(DATABASE_URL="postgres://user:secret@db.example.test/learnova_test")
        self.assertTrue(flask_app.config["SQLALCHEMY_ENGINE_OPTIONS"]["pool_pre_ping"])

    def test_sqlite_gets_no_pool_options(self):
        flask_app = configured(DATABASE_URL="sqlite:///" + (Path(tempfile.gettempdir()) / "pf5-config.db").as_posix())
        # Only the dialect-independent option (HiddenParameterTests); no pre-ping, no recycling.
        self.assertEqual(flask_app.config["SQLALCHEMY_ENGINE_OPTIONS"], {"hide_parameters": True})
        self.assertEqual(flask_app.config["DB_MIGRATION_LOCK_TIMEOUT_MS"], 10000)

    def test_recycling_can_be_turned_off_and_bad_values_are_refused(self):
        flask_app = configured(DATABASE_URL="postgresql://u:p@h/learnova_test", DB_POOL_RECYCLE_SECONDS="0",
                               DB_MIGRATION_LOCK_TIMEOUT_MS="0")
        self.assertEqual(flask_app.config["SQLALCHEMY_ENGINE_OPTIONS"], {"hide_parameters": True, "pool_pre_ping": True})
        self.assertEqual(flask_app.config["DB_MIGRATION_LOCK_TIMEOUT_MS"], 0)
        for name, value in (("DB_POOL_RECYCLE_SECONDS", "soon"), ("DB_POOL_RECYCLE_SECONDS", "-1"),
                            ("DB_MIGRATION_LOCK_TIMEOUT_MS", "900000")):
            with self.assertRaises(RuntimeError, msg=f"{name}={value}"):
                configured(DATABASE_URL="postgresql://u:p@h/learnova_test", **{name: value})

    def test_options_are_set_before_the_extension_reads_them(self):
        source = Path(application.__file__).read_text(encoding="utf-8")
        self.assertLess(source.index("APP_ENV = configure_app(app)"), source.index("db.init_app(app)"))


class HiddenParameterTests(unittest.TestCase):
    """A database error never carries its bound parameters - answers, notes, names - into the
    log or an error report, on any dialect (final docs review)."""

    def test_every_dialect_hides_bound_parameters(self):
        for url in ("postgresql://user:secret@db.example.test/learnova_test",
                    "sqlite:///" + (Path(tempfile.gettempdir()) / "cleanup-hidden.db").as_posix()):
            with self.subTest(url=url.split(":", 1)[0]):
                flask_app = configured(DATABASE_URL=url)
                self.assertIs(flask_app.config["SQLALCHEMY_ENGINE_OPTIONS"]["hide_parameters"], True)
                extension = SQLAlchemy()
                extension.init_app(flask_app)
                with flask_app.app_context():
                    engine = extension.engine
                    self.assertTrue(engine.hide_parameters)
                    if engine.dialect.name == "postgresql":     # the pool options are still there
                        self.assertTrue(engine.pool._pre_ping)
                        self.assertEqual(engine.pool._recycle, 280)
                    engine.dispose()

    def test_the_app_engine_hides_them(self):
        with application.app.app_context():
            self.assertTrue(application.db.engine.hide_parameters)

    def test_a_failed_statement_does_not_show_what_was_written(self):
        flask_app = configured(DATABASE_URL="sqlite://")
        extension = SQLAlchemy()
        extension.init_app(flask_app)
        with flask_app.app_context():
            with extension.engine.begin() as connection:
                connection.execute(text("CREATE TABLE note (id INTEGER PRIMARY KEY, body TEXT UNIQUE)"))
                connection.execute(text("INSERT INTO note (body) VALUES (:body)"), {"body": "Lea's private answer"})
            with self.assertRaises(Exception) as caught:
                with extension.engine.begin() as connection:
                    connection.execute(text("INSERT INTO note (body) VALUES (:body)"), {"body": "Lea's private answer"})
            extension.engine.dispose()
        self.assertIn("UNIQUE", str(caught.exception))
        self.assertNotIn("private answer", str(caught.exception))
        self.assertIn("hide_parameters=True", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
