import sqlite3
import unittest

from sql_agent.db import Database, QueryTimeoutError
from sql_agent.guardrails import UnsafeQueryError, check_query
from tests.fakes import make_db


class CheckQueryTest(unittest.TestCase):
    def test_allows_reads(self):
        for sql in [
            "SELECT 1",
            "select * from Track;",
            "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
            "SELECT ';' AS semi -- a comment; with a semicolon",
            "SELECT 1 UNION SELECT 2",
        ]:
            with self.subTest(sql=sql):
                self.assertTrue(check_query(sql))

    def test_rejects_writes_and_tricks(self):
        for sql in [
            "",
            "DELETE FROM Track",
            "UPDATE Track SET Name='x'",
            "DROP TABLE Track",
            "SELECT 1; DROP TABLE Track",
            "PRAGMA table_info(Track)",
            "ATTACH DATABASE 'x.db' AS x",
            "SELECT load_extension('evil')",
            "/* hi */ INSERT INTO Genre VALUES (3, 'Pop')",
        ]:
            with self.subTest(sql=sql), self.assertRaises(UnsafeQueryError):
                check_query(sql)


class DatabaseSafetyTest(unittest.TestCase):
    def setUp(self):
        self.db = Database(make_db(), timeout_s=0.3, max_rows=2)

    def tearDown(self):
        self.db.close()

    def test_authorizer_blocks_write_that_passes_static_check(self):
        # Starts with WITH, so layer 1 lets it through; layer 2 must stop it.
        with self.assertRaises(sqlite3.DatabaseError):
            self.db.run_query("WITH x AS (SELECT 1) DELETE FROM Track")
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM Track").fetchone()[0], 3)

    def test_connection_is_read_only(self):
        # Even our own trusted connection cannot write (layer 3).
        with self.assertRaises(sqlite3.OperationalError):
            self.db.conn.execute("INSERT INTO Genre VALUES (9, 'x')")

    def test_timeout(self):
        runaway = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c"
        with self.assertRaises(QueryTimeoutError):
            self.db.run_query(runaway)

    def test_row_cap(self):
        res = self.db.run_query("SELECT * FROM Track")
        self.assertEqual(len(res.rows), 2)
        self.assertTrue(res.truncated)


if __name__ == "__main__":
    unittest.main()
