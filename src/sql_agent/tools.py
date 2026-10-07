"""Tool definitions (JSON schemas the model sees) and their implementations."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from sql_agent.db import Database, QueryResult, QueryTimeoutError
from sql_agent.guardrails import UnsafeQueryError

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_tables",
        "description": "List every table in the database with its row count. Call this first.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "describe_tables",
        "description": (
            "Show the CREATE TABLE statement (columns, types, keys) and 3 sample rows "
            "for each requested table."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"tables": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
            "required": ["tables"],
        },
    },
    {
        "name": "run_sql",
        "description": (
            "Execute ONE read-only SQLite SELECT/WITH query and return the result as a table "
            "(first 25 rows shown). Use it to explore values and to compute answers."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "purpose": {"type": "string", "description": "One line: why you run this."},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "final_answer",
        "description": "Deliver the final answer to the user. Ends the task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "answer": {"type": "string", "description": "Direct answer quoting key numbers."},
                "sql": {
                    "type": "string",
                    "description": "The single query that supports the answer ('' if none).",
                },
            },
            "required": ["answer", "sql"],
        },
    },
    {
        "name": "ask_clarification",
        "description": "Ask the user ONE short question when ambiguity would change the result.",
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
]

TERMINAL_TOOLS = frozenset({"final_answer", "ask_clarification"})


@dataclass
class ToolOutput:
    content: str
    is_error: bool = False
    result: QueryResult | None = None


class ToolExecutor:
    """Runs non-terminal tools. Errors become tool results, never exceptions,
    so the model sees what went wrong and can correct itself."""

    def __init__(self, db: Database, preview_rows: int = 25):
        self.db = db
        self.preview_rows = preview_rows

    def run(self, name: str, args: dict[str, Any]) -> ToolOutput:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return ToolOutput(f"Unknown tool '{name}'.", is_error=True)
        try:
            return handler(**args)
        except TypeError as e:  # bad/missing arguments from the model
            return ToolOutput(f"Invalid arguments for {name}: {e}", is_error=True)

    def execute_sql(self, sql: str) -> ToolOutput:
        try:
            result = self.db.run_query(sql)
        except UnsafeQueryError as e:
            return ToolOutput(f"Rejected by guardrail: {e}", is_error=True)
        except QueryTimeoutError as e:
            return ToolOutput(str(e), is_error=True)
        except sqlite3.DatabaseError as e:
            if "not authorized" in str(e).lower():  # Layer 2 (authorizer) fired
                return ToolOutput(
                    "Blocked by read-only policy: the query tried a non-read action.",
                    is_error=True,
                )
            return ToolOutput(
                f"SQLite error: {e}. Check table/column names with describe_tables.",
                is_error=True,
            )
        return ToolOutput(result.to_markdown(self.preview_rows), result=result)

    # ----- tool implementations -----

    def _tool_list_tables(self) -> ToolOutput:
        lines = [f"- {name} ({count} rows)" for name, count in self.db.list_tables()]
        return ToolOutput("\n".join(lines))

    def _tool_describe_tables(self, tables: list[str]) -> ToolOutput:
        return ToolOutput(self.db.describe(tables))

    def _tool_run_sql(self, sql: str, purpose: str = "") -> ToolOutput:
        return self.execute_sql(sql)
