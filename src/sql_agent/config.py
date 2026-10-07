"""Runtime settings, read from environment variables (and .env if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# USD per million tokens: (input, output). Used for cost reporting only.
# Check https://docs.claude.com/en/docs/about-claude/pricing and update as needed.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


@dataclass(frozen=True)
class Settings:
    model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
    db_path: str = field(default_factory=lambda: _env("SQL_AGENT_DB", "data/chinook.sqlite"))
    max_steps: int = field(default_factory=lambda: int(_env("SQL_AGENT_MAX_STEPS", "12")))
    max_tokens: int = 2048
    query_timeout_s: float = field(default_factory=lambda: float(_env("SQL_AGENT_QUERY_TIMEOUT_S", "5")))
    # Hard cap on rows fetched from any query.
    max_rows: int = field(default_factory=lambda: int(_env("SQL_AGENT_MAX_ROWS", "200")))
    # Rows shown to the model per tool result (keeps context small).
    preview_rows: int = 25
    # Stop if the model keeps producing failing SQL.
    max_consecutive_errors: int = 4

    def cost_usd(self, input_tokens: int, output_tokens: int) -> float | None:
        price = PRICING.get(self.model)
        if price is None:
            return None
        return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000
