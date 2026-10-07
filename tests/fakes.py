"""Test doubles: a tiny SQLite DB and a scripted fake of the Anthropic client.

The fake lets us test the agent loop deterministically, offline, for free.
"""

from __future__ import annotations

import copy
import itertools
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_ids = itertools.count(1)


def make_db() -> Path:
    path = Path(tempfile.mkdtemp()) / "test.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE Genre (GenreId INTEGER PRIMARY KEY, Name TEXT);
        CREATE TABLE Track (TrackId INTEGER PRIMARY KEY, Name TEXT, GenreId INTEGER,
                            Milliseconds INTEGER);
        INSERT INTO Genre VALUES (1, 'Rock'), (2, 'Jazz');
        INSERT INTO Track VALUES (1, 'A', 1, 200000), (2, 'B', 1, 300000), (3, 'C', 2, 250000);
        """
    )
    conn.commit()
    conn.close()
    return path


def tool_use(name: str, input: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=f"tu_{next(_ids)}", name=name, input=input or {})


def text(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


def response(*blocks: SimpleNamespace, stop: str | None = None) -> SimpleNamespace:
    has_tool = any(b.type == "tool_use" for b in blocks)
    return SimpleNamespace(
        content=list(blocks),
        stop_reason=stop or ("tool_use" if has_tool else "end_turn"),
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
    )


class FakeClient:
    """Returns pre-scripted responses and records every request."""

    def __init__(self, script: list[SimpleNamespace]):
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        self.messages = self  # so client.messages.create(...) works

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.requests.append(copy.deepcopy(kwargs))
        if not self.script:
            raise AssertionError("FakeClient ran out of scripted responses")
        return self.script.pop(0)

    def last_user_content(self) -> list[dict[str, Any]]:
        return self.requests[-1]["messages"][-1]["content"]
