"""Global test safety: tests cannot reach the network, or a real database.

**The database guard has to run here, and it has to run first.** Most test modules set
`DATABASE_URL` with `os.environ.setdefault(...)` before importing `app`, but a handful
never did — `test_ai_gateway.py`, `test_ai_observability.py`, `test_ai_live.py`. pytest
imports test modules alphabetically, so `test_ai_gateway.py` was the first module to
`import app`, and it configured the entire session against the *default* database:
`instance/numeri.db`, the one a developer actually uses. Every later `setdefault` was
then a no-op, because the variable was already set and the app already configured.

`test_ai_observability.py` and many `setUp` methods then call `db.drop_all()`. The result
was that running the suite silently emptied the developer's own database — and it really
happened, repeatedly, before this guard existed.

conftest.py is imported before any test module, so setting the variable here happens
before the first `import app` no matter which file pytest reaches first. It is set
unconditionally rather than with `setdefault`: a test must not be able to opt back into
a real database by accident.
"""

import os
import socket
import tempfile
from pathlib import Path

import pytest

# One scratch database for the whole session. Every test class already recreates its own
# schema in setUp, so sharing the file costs nothing and matches what the suite did
# before - except that the file is now disposable.
TEST_DATABASE_PATH = Path(tempfile.gettempdir()) / "learnova_pytest_session.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE_PATH.as_posix()}"

# Keep the rest of the run away from real instance data too: usage accounting and the
# response cache both default into instance/ when unset.
os.environ.setdefault("AI_USAGE_PATH", str(Path(tempfile.gettempdir()) / "learnova_pytest_usage.jsonl"))
os.environ.setdefault("AI_CACHE_DIR", tempfile.mkdtemp(prefix="learnova-pytest-cache-"))
# Uploaded flashcard and vocabulary photos default to instance/flashcard_imports. Tests that
# never set their own directory used to leave hundreds of orphan images there, so the
# session gets a scratch directory unconditionally (tests may still point app.config at
# their own temporary folder).
os.environ["FLASHCARD_IMPORT_STORAGE_DIR"] = tempfile.mkdtemp(prefix="learnova-pytest-imports-")

# Where the real development data lives. The suite must never write there; the paths above
# are checked against it in pytest_configure, and its contents are compared before and
# after the session (pytest_terminal_summary).
INSTANCE_DIR = Path(__file__).resolve().parent.parent / "instance"
SCRATCH_PATH_SETTINGS = ("FLASHCARD_IMPORT_STORAGE_DIR", "AI_CACHE_DIR", "AI_USAGE_PATH")


def sqlite_foreign_keys_on(dbapi_connection, _connection_record=None):
    """Pool `connect` listener: enforce foreign keys on a SQLite connection, as PostgreSQL does.

    SQLite ignores FOREIGN KEY clauses unless this is set on every connection, so a suite
    that only runs on SQLite never sees the IntegrityError production raises when a parent
    row is deleted before its children. Other drivers are left alone.
    """

    if "sqlite" in type(dbapi_connection).__module__:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# Opt-in for the whole session: LEARNOVA_TEST_SQLITE_FK=1 python -m pytest ...
# Run the suite once with it before a release (FIX_PLAN PF5). Registered on the Engine class
# before any test module imports the app, so the app's engine gets it from its first
# connection on.
SQLITE_FOREIGN_KEYS = os.environ.get("LEARNOVA_TEST_SQLITE_FK", "").strip().lower() in {"1", "true", "yes", "on"}
if SQLITE_FOREIGN_KEYS:
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    event.listen(Engine, "connect", sqlite_foreign_keys_on)


def path_is_inside(value, parent) -> bool:
    """True when `value` (a path string) lies in `parent` or is `parent`, compared resolved."""

    if not value:
        return False
    child = os.path.normcase(os.path.realpath(os.path.abspath(str(value))))
    base = os.path.normcase(os.path.realpath(os.path.abspath(str(parent))))
    return child == base or child.startswith(base.rstrip("\\/") + os.sep)


def scratch_paths_inside_instance(environment=None, instance_dir=INSTANCE_DIR) -> list[str]:
    """The scratch-path settings that point into instance/ (empty when all is well)."""

    environment = os.environ if environment is None else environment
    return [name for name in SCRATCH_PATH_SETTINGS if path_is_inside(environment.get(name, ""), instance_dir)]


def instance_snapshot(root=INSTANCE_DIR) -> dict[str, tuple[int, int]]:
    """{relative path: (size, mtime_ns)} of every file under `root`; read-only."""

    files: dict[str, tuple[int, int]] = {}
    root = Path(root)
    if not root.is_dir():
        return files
    for directory, _subdirectories, names in os.walk(root):
        for name in names:
            path = Path(directory) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            files[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return files


def instance_changes(before: dict, after: dict) -> dict[str, list[str]]:
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed": sorted(name for name in set(before) & set(after) if before[name] != after[name]),
    }


_INSTANCE_BEFORE: dict[str, tuple[int, int]] = {}


def pytest_sessionstart(session):
    _INSTANCE_BEFORE.clear()
    _INSTANCE_BEFORE.update(instance_snapshot())


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Report (never fail on) any change under instance/ during the session.

    A development server may legitimately write there in parallel, so a change is a prompt
    to look, not proof that a test wrote it.
    """

    changes = instance_changes(_INSTANCE_BEFORE, instance_snapshot())
    write = terminalreporter.write_line
    if SQLITE_FOREIGN_KEYS:
        write("learnova: SQLite foreign keys were enforced on every connection (LEARNOVA_TEST_SQLITE_FK=1).")
    if not any(changes.values()):
        write(f"learnova: instance/ unchanged during the session ({len(_INSTANCE_BEFORE)} files).")
        return
    write("learnova: instance/ CHANGED during the session (reported, not failed; a dev server may "
          "write there too):", yellow=True)
    for kind in ("added", "removed", "changed"):
        for name in changes[kind][:20]:
            write(f"  {kind}: {name}", yellow=True)
        if len(changes[kind]) > 20:
            write(f"  ... and {len(changes[kind]) - 20} more {kind}", yellow=True)


def pytest_configure(config):
    """Refuse to run if anything still points the suite at a real database.

    A belt-and-braces check on top of the assignment above: if a future change reorders
    imports, or a module overrides the variable, the suite stops instead of dropping
    somebody's tables.
    """

    configured = os.environ.get("DATABASE_URL", "")
    if Path(TEST_DATABASE_PATH).as_posix() not in configured.replace("\\", "/"):
        raise pytest.UsageError(
            f"Tests must run against the scratch database, not {configured!r}. "
            "conftest.py sets DATABASE_URL; something overrode it.")
    for forbidden in ("numeri.db", "learnova.db"):
        if forbidden in configured:
            raise pytest.UsageError(
                f"Refusing to run: DATABASE_URL points at {forbidden}, which is real data.")
    inside = scratch_paths_inside_instance()
    if inside:
        raise pytest.UsageError(
            f"Refusing to run: {', '.join(inside)} point(s) into {INSTANCE_DIR}, which holds real "
            "development data. conftest.py sets scratch paths; something overrode them.")


@pytest.fixture(autouse=True)
def block_unmocked_network(monkeypatch, request):
    live_requested = (
        request.node.get_closest_marker("live_ai") is not None
        and os.getenv("RUN_LIVE_AI_TEST") == "1"
    )
    if live_requested:
        return

    def blocked(*_args, **_kwargs):
        raise AssertionError("Unmocked network request attempted during automated tests")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
