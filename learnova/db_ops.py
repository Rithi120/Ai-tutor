"""Boot-time database safety: one migrating process at a time, and no endless lock waits.

`app.ensure_database()` runs at import in every process that loads the app. Two processes
starting together (more gunicorn workers, a second instance, `flask init-db` during a
deploy) used to race in `create_all`, `ALTER TABLE ... ADD COLUMN` and the
`schema_migration` insert, and the loser crashed at import (reproduced on SQLite with a
start barrier, docs: postgres report R4). On PostgreSQL the helpers below serialise the
migrations behind a session-level advisory lock, so the second process waits, then finds
every migration recorded and runs no DDL at all.

`ALTER TABLE` needs an ACCESS EXCLUSIVE lock. While an old instance is still serving a long
request that holds a transaction on the same table, the ALTER would queue behind it and
every later query on that table would queue behind the ALTER (R5). `lock_timeout` makes the
booting process give up instead: PostgreSQL rolls the migration's transaction back, the
boot fails without changing anything, and the next start retries. Deploy at a quiet time.

Every other dialect (SQLite in development and tests) gets a no-op: SQLite has neither
advisory locks nor `lock_timeout`, and one developer process migrates its own file.

Pure module: no Flask, no app import, so it is tested with a fake engine.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import event, text

# Any constant works as long as every process uses the same one; it only has to be
# distinct from other advisory locks taken on the same database (there are none today).
MIGRATION_LOCK_KEY = 726110


def _is_postgresql(engine: Any) -> bool:
    return getattr(getattr(engine, "dialect", None), "name", "") == "postgresql"


@contextmanager
def migration_lock(engine: Any) -> Iterator[None]:
    """Hold PostgreSQL's advisory lock `MIGRATION_LOCK_KEY` for the duration of the block.

    The lock lives on a connection of its own, never the ORM session's: it is taken with
    `engine.connect()`, committed straight away (a session-level advisory lock survives the
    commit, and the connection is not left "idle in transaction" for the whole migration),
    and detached from the pool so that closing it really ends the server session. If the
    explicit unlock fails, closing the connection still releases the lock, and a pooled
    connection can never keep it alive for the next boot. Other dialects: no-op.
    """

    if not _is_postgresql(engine):
        yield
        return
    connection = engine.connect()
    try:
        connection.detach()
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        connection.commit()
        try:
            yield
        finally:
            try:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})
                connection.commit()
            except Exception:  # noqa: BLE001 - closing the detached connection releases it
                pass
    finally:
        connection.close()


def lock_timeout_statement(dialect_name: str, milliseconds: int) -> str | None:
    """`SET LOCAL lock_timeout` for one PostgreSQL transaction; None when off or not PostgreSQL."""

    value = int(milliseconds)
    if dialect_name != "postgresql" or value <= 0:
        return None
    return f"SET LOCAL lock_timeout = '{value}ms'"


@contextmanager
def migration_lock_timeout(engine: Any, milliseconds: int) -> Iterator[None]:
    """While the block runs, start every new transaction on `engine` with the lock timeout.

    A `begin` listener covers the DDL transactions (`engine.begin()`), reflection, and the
    ORM session's transactions alike, so the publication backfill is bounded too. The
    listener is removed again however the block ends. No statement (SQLite, or 0 ms):
    nothing is registered at all.
    """

    statement = lock_timeout_statement(getattr(engine.dialect, "name", ""), milliseconds)
    if statement is None:
        yield
        return

    def apply_lock_timeout(connection):
        connection.exec_driver_sql(statement)

    event.listen(engine, "begin", apply_lock_timeout)
    try:
        yield
    finally:
        event.remove(engine, "begin", apply_lock_timeout)
