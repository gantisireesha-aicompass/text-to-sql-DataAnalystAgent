"""Execution-accuracy scoring: does the agent's final query return the same
result as the gold query? (The metric used by Spider/BIRD.)

Comparison rules:
- Values are normalized: floats rounded to 2 dp, ints/floats compared numerically,
  strings stripped.
- Row order is ignored unless the item sets order_matters.
- Column order is ignored, and extra columns are tolerated ("superset" match):
  if the question asks for name + count and the agent also returns an id, the
  answer is still correct. Both match types are reported separately.
"""

from __future__ import annotations

from collections import Counter
from itertools import combinations, permutations
from typing import Literal

MatchType = Literal["exact", "superset"] | None


def _norm(v: object) -> object:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        f = round(float(v), 2)
        return int(f) if f.is_integer() else f
    if isinstance(v, str):
        return v.strip()
    return v


def _rows(rows: list[tuple], cols: tuple[int, ...]) -> list[tuple]:
    return [tuple(_norm(r[i]) for i in cols) for r in rows]


def _same(a: list[tuple], b: list[tuple], ordered: bool) -> bool:
    return a == b if ordered else Counter(a) == Counter(b)


def results_match(
    gold: list[tuple], pred: list[tuple], order_matters: bool = False, max_cols: int = 8
) -> MatchType:
    if len(gold) != len(pred):
        return None
    if not gold:
        return "exact"
    k, m = len(gold[0]), len(pred[0])
    if m < k or m > max_cols:
        return None
    gold_n = _rows(gold, tuple(range(k)))
    # Try every way of picking k predicted columns, in every order.
    for chosen in combinations(range(m), k):
        for perm in permutations(chosen):
            if _same(gold_n, _rows(pred, perm), order_matters):
                return "exact" if m == k else "superset"
    return None
