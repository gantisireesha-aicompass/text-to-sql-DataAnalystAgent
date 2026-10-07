"""Run the agent over evals/dataset.jsonl and report execution accuracy.

    python evals/run_eval.py                    # full set
    python evals/run_eval.py --limit 5          # quick smoke run
    python evals/run_eval.py --ids h01 h02 -v   # specific items, print failures
    python evals/run_eval.py --model claude-haiku-4-5

Writes evals/results/<timestamp>.json (per-item detail) and prints a summary
table you can paste into the README.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import time
from dataclasses import replace
from pathlib import Path

from sql_agent.cli import build_agent
from sql_agent.config import Settings
from sql_agent.evaluation import results_match

HERE = Path(__file__).resolve().parent


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(HERE / "dataset.jsonl"))
    ap.add_argument("--model")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    settings = Settings()
    if args.model:
        settings = replace(settings, model=args.model)

    with open(args.dataset, encoding="utf-8") as f:
        items = [json.loads(line) for line in f if line.strip()]
    if args.ids:
        items = [it for it in items if it["id"] in set(args.ids)]
    if args.limit:
        items = items[: args.limit]

    gold_db = sqlite3.connect(f"file:{Path(settings.db_path).resolve()}?mode=ro", uri=True)
    agent = build_agent(settings, trace=True)
    records = []

    for it in items:
        gold_rows = gold_db.execute(it["gold_sql"]).fetchall()
        try:
            res = agent.run(it["question"])
            match = (
                results_match(gold_rows, res.rows, it.get("order_matters", False))
                if res.status == "answered" and res.sql
                else None
            )
            rec = {
                "id": it["id"],
                "difficulty": it["difficulty"],
                "status": res.status,
                "match": match,
                "correct": match is not None,
                "sql": res.sql,
                "answer": res.answer,
                "tool_calls": len(res.steps),
                "sql_errors": sum(s.is_error for s in res.steps),
                "llm_calls": res.llm_calls,
                "input_tokens": res.input_tokens,
                "output_tokens": res.output_tokens,
                "cost_usd": res.cost_usd,
                "latency_s": round(res.latency_s, 2),
            }
        except Exception as e:  # keep going; one API hiccup shouldn't kill the run
            rec = {
                "id": it["id"],
                "difficulty": it["difficulty"],
                "status": "exception",
                "correct": False,
                "error": repr(e),
            }
        records.append(rec)
        mark = "✓" if rec["correct"] else "✗"
        print(f"{mark} {it['id']:<4} {rec['status']:<20} {rec.get('latency_s', '-')}s")
        if args.verbose and not rec["correct"]:
            print(f"    Q: {it['question']}\n    gold: {it['gold_sql']}\n    pred: {rec.get('sql')}")

    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{settings.model}.json"
    out.write_text(json.dumps({"model": settings.model, "records": records}, indent=2))
    print_summary(records, settings.model)
    print(f"\nDetails: {out}")


def print_summary(records: list[dict], model: str) -> None:
    def acc(rs: list[dict]) -> str:
        if not rs:
            return "-"
        n = sum(r["correct"] for r in rs)
        return f"{n}/{len(rs)} ({100 * n / len(rs):.0f}%)"

    ok = [r for r in records if r["status"] != "exception"]
    lat = [r["latency_s"] for r in ok]
    costs = [r["cost_usd"] for r in ok if r.get("cost_usd") is not None]
    print(f"\n### Results — `{model}`\n")
    print("| Difficulty | Execution accuracy |\n|---|---|")
    for d in ("easy", "medium", "hard"):
        print(f"| {d} | {acc([r for r in records if r['difficulty'] == d])} |")
    print(f"| **overall** | **{acc(records)}** |\n")
    if ok:
        p95 = sorted(lat)[max(0, round(0.95 * len(lat)) - 1)]
        print(f"- Avg tool calls/question: {statistics.mean(r['tool_calls'] for r in ok):.1f}")
        print(
            f"- Questions that recovered from a SQL error: "
            f"{sum(1 for r in ok if r['sql_errors'] and r['correct'])}"
        )
        print(f"- Latency: median {statistics.median(lat):.1f}s, p95 {p95:.1f}s")
        if costs:
            print(f"- Cost: ${sum(costs):.3f} total, ${statistics.mean(costs):.4f}/question")
    print(f"- Superset matches (extra columns): {sum(r.get('match') == 'superset' for r in records)}")


if __name__ == "__main__":
    main()
