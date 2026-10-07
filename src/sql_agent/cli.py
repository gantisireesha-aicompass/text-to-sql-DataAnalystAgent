"""Command-line entry point.

sql-agent ask "Which 5 countries generate the most revenue?"
sql-agent chat            # multi-turn, supports clarifying questions
sql-agent ask "..." -v    # show every tool call
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace

from sql_agent.agent import AgentResult, SQLAgent
from sql_agent.config import Settings
from sql_agent.db import Database, QueryResult
from sql_agent.tools import ToolExecutor
from sql_agent.tracing import JsonlTracer


def build_agent(settings: Settings, trace: bool = True) -> SQLAgent:
    import anthropic  # imported here so tests never need the SDK or an API key

    db = Database(settings.db_path, settings.query_timeout_s, settings.max_rows)
    return SQLAgent(
        client=anthropic.Anthropic(),
        executor=ToolExecutor(db, settings.preview_rows),
        settings=settings,
        tracer=JsonlTracer() if trace else None,
    )


def print_result(result: AgentResult, verbose: bool) -> None:
    if verbose:
        for i, step in enumerate(result.steps, 1):
            flag = "  [error]" if step.is_error else ""
            arg = step.input.get("sql") or step.input.get("tables") or ""
            print(f"\n── step {i}: {step.tool}{flag}  {arg}")
            print(step.output[:1500])
        print()

    if result.status == "needs_clarification":
        print(f"? {result.clarification}")
    elif result.status == "answered":
        print(result.answer)
        if result.sql:
            print(f"\nSQL:\n{result.sql}\n")
            print(QueryResult(result.columns, result.rows, False, 0).to_markdown(max_rows=20))
    else:
        print(f"Stopped: {result.status}. Try rephrasing or run with -v to see what happened.")

    cost = f"${result.cost_usd:.4f}" if result.cost_usd is not None else "n/a"
    print(
        f"\n[{result.llm_calls} LLM calls · {len(result.steps)} tool calls · "
        f"{result.input_tokens}+{result.output_tokens} tokens · {cost} · {result.latency_s:.1f}s]"
    )


def main(argv: list[str] | None = None) -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(prog="sql-agent", description=__doc__.split("\n")[0])
    parser.add_argument("--db", help="Path to SQLite database")
    parser.add_argument("--model", help="Claude model ID")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show tool calls")
    parser.add_argument("--no-trace", action="store_true", help="Don't write traces/*.jsonl")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ask = sub.add_parser("ask", help="Ask one question")
    ask.add_argument("question")
    sub.add_parser("chat", help="Interactive session")
    args = parser.parse_args(argv)

    settings = Settings()
    if args.db:
        settings = replace(settings, db_path=args.db)
    if args.model:
        settings = replace(settings, model=args.model)
    agent = build_agent(settings, trace=not args.no_trace)

    if args.cmd == "ask":
        print_result(agent.run(args.question), args.verbose)
        return 0

    print("SQL analyst agent. Ask a question, or 'exit'.")
    previous: AgentResult | None = None
    while True:
        try:
            q = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if q.lower() in {"exit", "quit"}:
            return 0
        if q:
            previous = agent.run(q, previous)
            print_result(previous, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
