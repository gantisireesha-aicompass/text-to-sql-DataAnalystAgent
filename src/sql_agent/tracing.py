"""Minimal JSONL tracing: one file per run, one line per event.

Good enough to debug locally and to show in a README. Swap in Langfuse or
OpenTelemetry later by implementing the same `log(event)` method.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class JsonlTracer:
    def __init__(self, directory: str | Path = "traces"):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 10**6}.jsonl"

    def log(self, event: dict[str, Any]) -> None:
        event = {"ts": round(time.time(), 3), **event}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")
