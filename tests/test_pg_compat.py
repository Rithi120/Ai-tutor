"""The models compile to DDL that PostgreSQL accepts (FIX_PLAN PF5, postgres report R1).

`LearningSection.review_step` was an INTEGER column with `server_default=false()`. SQLite took
`DEFAULT 0`-ish without complaint, so the suite was green, but PostgreSQL compiles it to
`review_step INTEGER DEFAULT false NOT NULL` and rejects it ("column is of type integer but
default expression is of type boolean"): `create_all()` aborted on every empty PostgreSQL
database - a new environment, staging, or a restore into a blank database. Upgrades were not
affected (031 adds the column with DEFAULT 0).

Compiling needs the dialect only, not a server. Executing the DDL on a real PostgreSQL is the
owner's run of R:/ai_tutor_suite_tmp/prod_pg/run_pg_migration_test.py (step I).
"""

import re
import unittest

from sqlalchemy import Integer
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.sql.elements import False_, True_

import app as application

INTEGER_WITH_BOOLEAN_DEFAULT = re.compile(r"\b(INTEGER|BIGINT|SMALLINT)\b[^,]*\bDEFAULT (false|true)\b", re.I)


def postgresql_ddl(table) -> str:
    return str(CreateTable(table).compile(dialect=postgresql.dialect()))


class PostgresqlDdlTests(unittest.TestCase):
    def test_every_column_default_compiles_for_postgresql(self):
        offenders = []
        for table in application.db.metadata.sorted_tables:
            for line in postgresql_ddl(table).splitlines():
                if INTEGER_WITH_BOOLEAN_DEFAULT.search(line):
                    offenders.append(f"{table.name}: {line.strip()}")
        self.assertEqual(offenders, [])
        learning_section = postgresql_ddl(application.db.metadata.tables["learning_section"])
        self.assertIn("review_step INTEGER DEFAULT 0 NOT NULL", learning_section)

    def test_no_integer_column_has_a_boolean_server_default(self):
        offenders = [
            f"{table.name}.{column.name}"
            for table in application.db.metadata.sorted_tables for column in table.columns
            if isinstance(column.type, Integer) and column.server_default is not None
            and isinstance(getattr(column.server_default, "arg", None), (False_, True_))
        ]
        self.assertEqual(offenders, [])

    def test_every_table_and_index_compiles_for_postgresql(self):
        tables = application.db.metadata.sorted_tables
        self.assertGreater(len(tables), 60)
        for table in tables:
            self.assertTrue(postgresql_ddl(table).startswith("\nCREATE TABLE"), table.name)
            for index in table.indexes:
                ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
                self.assertIn(" ON ", ddl, index.name)
        # The 037/038 partial unique indexes keep their WHERE on PostgreSQL.
        flashcard_set = application.db.metadata.tables["flashcard_set"]
        index = next(item for item in flashcard_set.indexes if item.name == "uq_flashcard_set_project")
        self.assertIn("WHERE project_id IS NOT NULL",
                      str(CreateIndex(index).compile(dialect=postgresql.dialect())))


if __name__ == "__main__":
    unittest.main()
