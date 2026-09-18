"""Retrieval evaluation: recall@k, precision@k and mean reciprocal rank.

The harness evaluates a set of labelled queries against an index. Each query
maps a natural-language question to exactly one expected document (``rel_path``).
A retrieved chunk is "relevant" when its document is the expected one, so the
metrics measure whether the pipeline surfaces the right document in the top k
chunks.

Metrics, for a query set of size ``N`` and a cut-off ``k``:

* ``recall@k``: fraction of queries whose expected document appears in the
  top-k chunks. With one relevant document per query this is ``hits / N``.
* ``precision@k``: mean over queries of ``relevant_retrieved / k``; again with
  one relevant document this is ``hits / (N * k)``.
* ``MRR``: mean over queries of ``1 / rank``, where ``rank`` is the 1-based
  position of the first chunk from the expected document, and ``0`` when the
  document never appears.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .embed import Embedder
from .search import run_search

_EPSILON = 1e-9


def load_queries(path: Path) -> list[dict[str, str]]:
    """Load a labelled query set from JSON.

    Accepts either ``{"queries": [{"query": ..., "rel_doc": ...}]}`` or a bare
    list of the same objects.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    queries = data.get("queries", data if isinstance(data, list) else [])
    return [{"query": str(item["query"]), "rel_doc": str(item["rel_doc"])} for item in queries]


def _first_relevant_rank(rel_paths: list[str], expected: str) -> int:
    """Return the 1-based rank of the first chunk from *expected*, or 0."""
    for index, rel_path in enumerate(rel_paths, start=1):
        if rel_path == expected:
            return index
    return 0


def evaluate(
    conn: sqlite3.Connection,
    queries: list[dict[str, str]],
    embedder: Embedder,
    k: int = 5,
    mode: str = "hybrid",
) -> dict[str, Any]:
    """Run every query and compute recall@k, precision@k and MRR."""
    if k <= 0:
        raise ValueError("k must be positive")
    total = len(queries)
    hits = 0
    reciprocal_ranks: list[float] = []
    per_query: list[dict[str, Any]] = []
    for query in queries:
        results = run_search(conn, query["query"], mode, embedder, limit=k)
        rel_paths = [hit["rel_path"] for hit in results]
        rank = _first_relevant_rank(rel_paths, query["rel_doc"])
        found = rank > 0
        if found:
            hits += 1
            reciprocal_ranks.append(1.0 / rank)
        else:
            reciprocal_ranks.append(0.0)
        per_query.append(
            {
                "query": query["query"],
                "rel_doc": query["rel_doc"],
                "found": found,
                "rank": rank,
                "top": rel_paths,
            }
        )
    recall = hits / total if total else 0.0
    precision = hits / (total * k) if total else 0.0
    mrr = sum(reciprocal_ranks) / total if total else 0.0
    return {
        "k": k,
        "mode": mode,
        "n_queries": total,
        "recall_at_k": round(recall, 6),
        "precision_at_k": round(precision, 6),
        "mrr": round(mrr, 6),
        "per_query": per_query,
    }


def _metric_keys() -> tuple[str, ...]:
    return ("recall_at_k", "precision_at_k", "mrr")


def load_baseline(path: Path) -> dict[str, float]:
    """Load a baseline metrics file produced by ``evaluate``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {key: float(data[key]) for key in _metric_keys()}


def check_against_baseline(
    metrics: dict[str, Any], baseline: dict[str, float]
) -> tuple[bool, list[str]]:
    """Compare metrics to a baseline; return ``(passed, regressions)``.

    A regression is any metric that falls below the baseline by more than a
    tiny tolerance.
    """
    regressions: list[str] = []
    for key in _metric_keys():
        current = float(metrics[key])
        target = baseline[key]
        if current < target - _EPSILON:
            regressions.append(f"{key}: {current} < baseline {target}")
    return not regressions, regressions


def render_report(metrics: dict[str, Any]) -> str:
    """Return a human-readable report for the metrics dict."""
    lines = [
        f"eval: {metrics['n_queries']} queries, mode={metrics['mode']}, k={metrics['k']}",
        f"recall@{metrics['k']}:    {metrics['recall_at_k']}",
        f"precision@{metrics['k']}: {metrics['precision_at_k']}",
        f"mrr:           {metrics['mrr']}",
    ]
    for item in metrics["per_query"]:
        mark = "hit " if item["found"] else "miss"
        rank = str(item["rank"]) if item["found"] else "-"
        lines.append(f"  [{mark}] rank={rank:<2} {item['query']} -> {item['rel_doc']}")
    return "\n".join(lines)
