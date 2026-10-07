"""The agent loop, written by hand (no framework) so every step is visible.

    user question
         │
         ▼
    ┌─────────────┐  tool_use   ┌──────────────┐
    │   Claude    │ ──────────▶ │ ToolExecutor │  list_tables / describe_tables / run_sql
    │ (Messages   │ ◀────────── │  + guardrails│
    │    API)     │ tool_result └──────────────┘
    └─────────────┘
         │ final_answer / ask_clarification   (terminal tools)
         ▼
    AgentResult (answer, sql, rows, steps, tokens, cost)

Budgets that stop runaway loops: max_steps, max_consecutive_errors, per-query timeout.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from sql_agent.config import Settings
from sql_agent.prompts import SYSTEM_PROMPT
from sql_agent.tools import TOOLS, ToolExecutor

Status = Literal["answered", "needs_clarification", "max_steps", "too_many_errors"]


class Tracer(Protocol):
    def log(self, event: dict[str, Any]) -> None: ...


@dataclass
class Step:
    tool: str
    input: dict[str, Any]
    output: str
    is_error: bool
    latency_ms: float


@dataclass
class AgentResult:
    status: Status
    answer: str = ""
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    clarification: str = ""
    steps: list[Step] = field(default_factory=list)
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    latency_s: float = 0.0
    # Conversation state so a chat UI can continue (e.g. after a clarification).
    messages: list[dict[str, Any]] = field(default_factory=list)
    pending_tool_results: list[dict[str, Any]] = field(default_factory=list)
    pending_tool_use_ids: list[str] = field(default_factory=list)


def _block_to_param(block: Any) -> dict[str, Any]:
    """Convert an SDK content block to a plain dict we can send back."""
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return block.model_dump()  # e.g. thinking blocks


class SQLAgent:
    def __init__(
        self,
        client: Any,  # anthropic.Anthropic or any object with .messages.create(...)
        executor: ToolExecutor,
        settings: Settings | None = None,
        tracer: Tracer | None = None,
    ):
        self.client = client
        self.executor = executor
        self.settings = settings or Settings()
        self.tracer = tracer

    def _trace(self, **event: Any) -> None:
        if self.tracer:
            self.tracer.log(event)

    def run(self, question: str, previous: AgentResult | None = None) -> AgentResult:
        s = self.settings
        start = time.perf_counter()
        result = AgentResult(status="max_steps")

        messages = list(previous.messages) if previous else []
        # Every tool_use must be answered by a tool_result. When continuing a chat,
        # close out the previous turn's terminal tool call with the user's new message.
        content: list[dict[str, Any]] = list(previous.pending_tool_results) if previous else []
        content += [
            {"type": "tool_result", "tool_use_id": tid, "content": f"User replied: {question}"}
            for tid in (previous.pending_tool_use_ids if previous else [])
        ]
        content.append({"type": "text", "text": question})
        messages.append({"role": "user", "content": content})
        self._trace(event="question", question=question, model=s.model)

        consecutive_errors = 0
        nudged = False

        for _ in range(s.max_steps):
            resp = self.client.messages.create(
                model=s.model,
                max_tokens=s.max_tokens,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )
            result.llm_calls += 1
            result.input_tokens += resp.usage.input_tokens
            result.output_tokens += resp.usage.output_tokens
            assistant_blocks = [_block_to_param(b) for b in resp.content]
            messages.append({"role": "assistant", "content": assistant_blocks})
            self._trace(event="llm_response", stop_reason=resp.stop_reason, content=assistant_blocks)

            tool_uses = [b for b in assistant_blocks if b["type"] == "tool_use"]
            if not tool_uses:
                # Model replied in prose. Nudge it once to use final_answer (we need the SQL).
                text = "\n".join(b["text"] for b in assistant_blocks if b["type"] == "text")
                if not nudged:
                    nudged = True
                    messages.append(
                        {"role": "user", "content": "Please deliver your answer via final_answer."}
                    )
                    continue
                result.status, result.answer = "answered", text
                break

            tool_results: list[dict[str, Any]] = []
            finished = False
            for tu in tool_uses:
                name, args = tu["name"], tu["input"] or {}

                if name == "ask_clarification":
                    result.status = "needs_clarification"
                    result.clarification = args.get("question", "")
                    finished = True
                    break

                if name == "final_answer":
                    out = self._finalize(tu["id"], args, result)
                    if out is None:  # valid answer -> done
                        finished = True
                        break
                    # Final SQL failed: hand the error back so the model can fix it.
                    tool_results.append(out)
                    consecutive_errors += 1
                    continue

                t0 = time.perf_counter()
                output = self.executor.run(name, args)
                latency = (time.perf_counter() - t0) * 1000
                result.steps.append(Step(name, args, output.content, output.is_error, latency))
                self._trace(
                    event="tool",
                    tool=name,
                    input=args,
                    is_error=output.is_error,
                    latency_ms=round(latency, 1),
                    output=output.content[:2000],
                )
                consecutive_errors = consecutive_errors + 1 if output.is_error else 0
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "content": output.content,
                        "is_error": output.is_error,
                    }
                )

            if finished:
                # Keep the protocol consistent for a possible follow-up turn: results we
                # already computed are carried over; the rest get the user's next message.
                answered = {r["tool_use_id"] for r in tool_results}
                result.pending_tool_results = tool_results
                result.pending_tool_use_ids = [t["id"] for t in tool_uses if t["id"] not in answered]
                break
            if consecutive_errors >= s.max_consecutive_errors:
                result.status = "too_many_errors"
                result.pending_tool_results = tool_results
                break
            messages.append({"role": "user", "content": tool_results})

        # A terminal tool_use is left without a tool_result on purpose: if the chat
        # continues, the next run() answers it with the user's reply (see top of run()).
        result.messages = messages
        result.latency_s = time.perf_counter() - start
        result.cost_usd = s.cost_usd(result.input_tokens, result.output_tokens)
        self._trace(
            event="result",
            status=result.status,
            answer=result.answer,
            sql=result.sql,
            llm_calls=result.llm_calls,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            latency_s=round(result.latency_s, 2),
        )
        return result

    def _finalize(self, tool_use_id: str, args: dict[str, Any], result: AgentResult) -> dict[str, Any] | None:
        """Re-run the final SQL so the returned rows are real. Returns an error
        tool_result if the SQL fails, or None on success (result is filled in)."""
        sql = (args.get("sql") or "").strip()
        answer = args.get("answer", "")
        if sql:
            output = self.executor.execute_sql(sql)
            if output.is_error:
                return {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": f"Your final SQL failed, fix it and call final_answer again. {output.content}",
                    "is_error": True,
                }
            assert output.result is not None
            result.columns, result.rows = output.result.columns, output.result.rows
        result.status, result.answer, result.sql = "answered", answer, sql
        return None
