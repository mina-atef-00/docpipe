"""Answer-level eval: citation precision/recall on labelled queries."""

from __future__ import annotations

from pathlib import Path

from docpipe import answer as answer_module
from docpipe.embed import make_local_embedder
from docpipe.indexer import connect_readonly


def _labelled() -> list[dict[str, str]]:
    return [
        {"query": "alpha beta gamma", "rel_doc": "a/one.md"},
        {"query": "epsilon zeta eta theta", "rel_doc": "a/two.txt"},
    ]


def test_answer_eval_metrics_on_seeded_corpus(
    pipeline: tuple[Path, Path, Path, Path],
) -> None:
    _corpus, _manifest, _chunks, index = pipeline
    conn = connect_readonly(index)
    try:
        embedder = make_local_embedder()
        metrics = answer_module.evaluate_answers(conn, _labelled(), embedder, mode="term", k=3)
    finally:
        conn.close()
    assert metrics["n_queries"] == 2
    assert metrics["citation_precision"] == 1.0
    assert metrics["citation_recall"] == 1.0
    assert metrics["refusals"] == 0


def test_answer_eval_refusal_counts(
    pipeline: tuple[Path, Path, Path, Path],
) -> None:
    _corpus, _manifest, _chunks, index = pipeline
    conn = connect_readonly(index)
    try:
        embedder = make_local_embedder()
        metrics = answer_module.evaluate_answers(
            conn,
            [{"query": "zzzzq qq zzzzq", "rel_doc": "a/one.md"}],
            embedder,
            mode="term",
            k=3,
        )
    finally:
        conn.close()
    assert metrics["refusals"] == 1
    assert metrics["citation_recall"] == 0.0
