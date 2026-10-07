"""Read-only database access: schema introspection and guarded query execution."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from sql_agent.guardrails import check_query, read_only_authorizer


class QueryTimeoutError(RuntimeError):
    pass


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool
    elapsed_ms: float

    def to_markdown(self, max_rows: int = 25, max_cell: int = 60) -> str:
        """Compact table for the model. Shows at most max_rows rows."""

        def cell(v: object) -> str:
            s = "NULL" if v is None else str(v)
            s = s.replace("|", "\\|").replace("\n", " ")
            return s if len(s) <= max_cell else s[: max_cell - 1] + "…"

        lines = [
            "| " + " | ".join(self.columns) + " |",
            "|" + "---|" * len(self.columns),
        ]
        lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in self.rows[:max_rows]]
        shown = min(len(self.rows), max_rows)
        more = "+" if self.truncated else ""
        lines.append(f"\n({shown} of {len(self.rows)}{more} rows shown, {self.elapsed_ms:.0f} ms)")
        return "\n".join(lines)


class Database:
    def __init__(self, path: str | Path, timeout_s: float = 5.0, max_rows: int = 200):
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"Database not found at {path}. Run `python scripts/download_chinook.py` first."
            )
        # Layer 3: read-only at the connection level.
        self.conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        self.timeout_s = timeout_s
        self.max_rows = max_rows

    # ---------- schema introspection (trusted, internal SQL only) ----------

    def table_names(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows]

    def list_tables(self) -> list[tuple[str, int]]:
        return [
            (t, self.conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) for t in self.table_names()
        ]

    def describe(self, tables: list[str], sample_rows: int = 3) -> str:
        known = {t.lower(): t for t in self.table_names()}
        parts: list[str] = []
        for requested in tables:
            name = known.get(requested.lower())
            if name is None:
                parts.append(f"-- Unknown table '{requested}'. Valid tables: {', '.join(known.values())}")
                continue
            (ddl,) = self.conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
            cur = self.conn.execute(f'SELECT * FROM "{name}" LIMIT {int(sample_rows)}')
            sample = QueryResult([d[0] for d in cur.description], cur.fetchall(), False, 0)
            parts.append(f"{ddl.strip()}\n\n-- Sample rows from {name}:\n{sample.to_markdown()}")
        return "\n\n".join(parts)

    # ---------- untrusted SQL from the model ----------

    def run_query(self, sql: str) -> QueryResult:
        sql = check_query(sql)  # Layer 1
        deadline = time.monotonic() + self.timeout_s

        def _progress() -> int:  # returning non-zero aborts the query
            return 1 if time.monotonic() > deadline else 0

        start = time.perf_counter()
        self.conn.set_authorizer(read_only_authorizer)  # Layer 2
        self.conn.set_progress_handler(_progress, 10_000)
        try:
            cur = self.conn.execute(sql)
            rows = cur.fetchmany(self.max_rows + 1)
        except sqlite3.OperationalError as e:
            if "interrupted" in str(e).lower():
                raise QueryTimeoutError(
                    f"Query exceeded {self.timeout_s:.0f}s timeout. Simplify it or add filters."
                ) from e
            raise
        finally:
            self.conn.set_progress_handler(None, 0)
            self.conn.set_authorizer(None)
        elapsed = (time.perf_counter() - start) * 1000
        columns = [d[0] for d in cur.description] if cur.description else []
        truncated = len(rows) > self.max_rows
        return QueryResult(columns, rows[: self.max_rows], truncated, elapsed)

    def close(self) -> None:
        self.conn.close()
