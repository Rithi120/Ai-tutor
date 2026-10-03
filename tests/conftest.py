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
