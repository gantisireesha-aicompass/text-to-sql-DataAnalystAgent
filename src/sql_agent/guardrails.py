"""Defense-in-depth SQL safety for model-generated queries.

Layer 1 - static check (this module): one statement, must start with SELECT/WITH.
          Cheap, and gives the model a readable error so it can self-correct.
Layer 2 - engine authorizer (this module): SQLite itself refuses any action that
          is not a read, no matter how cleverly the SQL is written.
Layer 3 - connection (db.py): the database file is opened read-only (mode=ro).
Plus     - a wall-clock timeout and a row cap (db.py).

No single layer is trusted on its own. Layer 1 can be fooled by unusual syntax;
layers 2 and 3 cannot, but their errors are less helpful to the model.
"""

from __future__ import annotations

import re
import sqlite3


class UnsafeQueryError(ValueError):
    """Raised when a query is rejected before it reaches the database."""


DENIED_FUNCTIONS = frozenset({"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"})

# Matches a string literal or a comment so they can be blanked out before
# checking for ';' or the leading keyword (e.g. "SELECT ';'" is fine).
_LITERAL_OR_COMMENT = re.compile(r"'(?:[^']|'')*'|--[^\n]*|/\*.*?\*/", re.S)

_ALLOWED_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
        getattr(sqlite3, "SQLITE_RECURSIVE", 33),  # recursive CTEs
    }
)


def _blank_literals_and_comments(sql: str) -> str:
    return _LITERAL_OR_COMMENT.sub(lambda m: "''" if m.group(0).startswith("'") else " ", sql)


def check_query(sql: str) -> str:
    """Return the query (trailing ';' removed) or raise UnsafeQueryError."""
    cleaned = _blank_literals_and_comments(sql).strip().rstrip(";").strip()
    if not cleaned:
        raise UnsafeQueryError("Empty query.")
    if ";" in cleaned:
        raise UnsafeQueryError("Only one SQL statement is allowed per call.")
    first_word = cleaned.split(None, 1)[0].upper()
    if first_word not in {"SELECT", "WITH"}:
        raise UnsafeQueryError(
            f"Only read-only SELECT queries are allowed (got a statement starting with {first_word})."
        )
    lowered = cleaned.lower()
    for fn in DENIED_FUNCTIONS:
        if re.search(rf"\b{fn}\s*\(", lowered):
            raise UnsafeQueryError(f"Function {fn}() is not allowed.")
    return sql.strip().rstrip(";").strip()


def read_only_authorizer(action: int, arg1, arg2, db_name, trigger) -> int:
    """sqlite3 authorizer callback: allow reads, deny everything else."""
    if action == sqlite3.SQLITE_FUNCTION:
        if arg2 and arg2.lower() in DENIED_FUNCTIONS:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY
