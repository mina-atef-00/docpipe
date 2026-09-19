"""Vector search, hybrid ranking, eval metrics and the regression gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docpipe import eval as eval_module
from docpipe import search as search_module
from docpipe import vectors
from docpipe.embed import make_local_embedder
from docpipe.hashing import sha256_file
from docpipe.indexer import build_index, connect_readonly
from docpipe.ingest import ingest_corpus, write_manifest
from docpipe.parse import parse_manifest, write_chunks


def _build_index(corpus: Path, work: Path, index: Path) -> None:
    manifest = ingest_corpus(corpus, work / "quarantine")
    manifest_path = work / "manifest.json"
    write_manifest(manifest, manifest_path)
    chunks, _report = parse_manifest(manifest, corpus, pdf_enabled=True)
    chunks_path = work / "chunks.jsonl"
    write_chunks(chunks, chunks_path)
    build_index(
        manifest,
        chunks,
        sha256_file(manifest_path),
        sha256_file(chunks_path),
        index,
    )


def test_index_records_vectors(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _m, _c, index = pipeline
    conn = connect_readonly(index)
    try:
        assert vectors.has_vectors(conn)
        assert vectors.vector_dim(conn) == 384
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        assert meta["embedder"] == "hashed-ngram"
        assert int(meta["vector_count"]) > 0
    finally:
        conn.close()


def test_vector_search_orders_by_similarity(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _m, _c, index = pipeline
    conn = connect_readonly(index)
    try:
        embedder = make_local_embedder()
        hits = search_module.run_search(conn, "alpha beta gamma", "vector", embedder, limit=10)
    finally:
        conn.close()
    assert hits
    # The chunk containing "alpha beta gamma" must rank first.
    assert hits[0]["rel_path"] == "a/one.md"
    assert hits[0]["score"] > hits[-1]["score"]


def test_vector_search_scores_are_cosine_in_unit_range(
    pipeline: tuple[Path, Path, Path, Path],
) -> None:
    _corpus, _m, _c, index = pipeline
    conn = connect_readonly(index)
    try:
        embedder = make_local_embedder()
        hits = search_module.run_search(conn, "epsilon zeta", "vector", embedder, limit=10)
    finally:
        conn.close()
    assert hits
    scores = [hit["score"] for hit in hits]
    assert all(-1.0 - 1e-6 <= s <= 1.0 + 1e-6 for s in scores)
    assert scores == sorted(scores, reverse=True)


def test_hybrid_search_ranks_expected_doc_first(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _m, _c, index = pipeline
    conn = connect_readonly(index)
    try:
        embedder = make_local_embedder()
        hits = search_module.run_search(
            conn, "alpha beta gamma delta", "hybrid", embedder, limit=10
        )
    finally:
        conn.close()
    assert hits
    assert hits[0]["rel_path"] == "a/one.md"
    assert hits[0]["score"] >= hits[-1]["score"]


def test_eval_metrics_hand_computed(monkeypatch: pytest.MonkeyPatch) -> None:
    queries = [
        {"query": "q0", "rel_doc": "a"},
        {"query": "q1", "rel_doc": "b"},
        {"query": "q2", "rel_doc": "z"},
    ]

    def fake_run_search(conn, text, mode, embedder, limit, alpha=0.5):
        results = {
            "q0": ["a", "b", "c"],
            "q1": ["x", "b", "y"],
            "q2": ["a", "b", "c"],
        }[text]
        return [{"rel_path": r} for r in results]

    monkeypatch.setattr(eval_module, "run_search", fake_run_search)
    metrics = eval_module.evaluate(None, queries, make_local_embedder(), k=3, mode="term")  # type: ignore[arg-type]

    # recall@3 = 2/3, precision@3 = 2/(3*3) = 2/9, MRR = (1 + 1/2 + 0)/3 = 0.5
    assert metrics["recall_at_k"] == pytest.approx(2 / 3, abs=1e-6)
    assert metrics["precision_at_k"] == pytest.approx(2 / 9, abs=1e-6)
    assert metrics["mrr"] == pytest.approx(0.5, abs=1e-6)


def test_eval_gate_passes_on_matching_baseline(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _m, _c, index = pipeline
    conn = connect_readonly(index)
    queries = [{"query": "alpha beta", "rel_doc": "a/one.md"}]
    try:
        metrics = eval_module.evaluate(conn, queries, make_local_embedder(), k=2, mode="hybrid")
    finally:
        conn.close()
    baseline = {
        "recall_at_k": metrics["recall_at_k"],
        "precision_at_k": metrics["precision_at_k"],
        "mrr": metrics["mrr"],
    }
    passed, regressions = eval_module.check_against_baseline(metrics, baseline)
    assert passed
    assert regressions == []


def test_eval_gate_fails_on_degraded_ranking(
    generated_corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = generated_corpus
    index = tmp_path / "index.sqlite"
    _build_index(corpus, tmp_path, index)
    queries = [
        {"query": "How do I create a new widget?", "rel_doc": "api/widget_api.md"},
        {"query": "How are bearer tokens issued and validated?", "rel_doc": "api/auth_api.md"},
        {
            "query": "How does the queue service deliver widget change notifications?",
            "rel_doc": "api/queue_api.md",
        },
        {
            "query": "What are the rules for a widget name and its labels?",
            "rel_doc": "specs/data-model.md",
        },
    ]
    conn = connect_readonly(index)
    embedder = make_local_embedder()
    try:
        good = eval_module.evaluate(conn, queries, embedder, k=3, mode="hybrid")
    finally:
        conn.close()
    assert good["recall_at_k"] > 0

    real = search_module.run_search

    def reversed_search(conn, text, mode, embedder, limit, alpha=0.5):
        return list(reversed(real(conn, text, mode, embedder, limit, alpha=alpha)))

    monkeypatch.setattr(eval_module, "run_search", reversed_search)
    conn = connect_readonly(index)
    try:
        degraded = eval_module.evaluate(conn, queries, embedder, k=3, mode="hybrid")
    finally:
        conn.close()

    baseline = {
        "recall_at_k": good["recall_at_k"],
        "precision_at_k": good["precision_at_k"],
        "mrr": good["mrr"],
    }
    passed, regressions = eval_module.check_against_baseline(degraded, baseline)
    assert not passed
    assert regressions
    assert degraded["mrr"] < good["mrr"] or degraded["recall_at_k"] < good["recall_at_k"]


def test_hybrid_scores_between_zero_and_one(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _m, _c, index = pipeline
    conn = connect_readonly(index)
    try:
        embedder = make_local_embedder()
        hits = search_module.run_search(conn, "alpha beta", "hybrid", embedder, limit=10)
    finally:
        conn.close()
    assert hits
    assert all(0.0 - 1e-6 <= hit["score"] <= 1.0 + 1e-6 for hit in hits)


def test_run_search_rejects_unknown_mode(pipeline: tuple[Path, Path, Path, Path]) -> None:
    """An unknown mode must raise, never silently downgrade to term search."""
    _corpus, _m, _c, index = pipeline
    conn = connect_readonly(index)
    try:
        embedder = make_local_embedder()
        with pytest.raises(ValueError, match="unknown search mode"):
            search_module.run_search(conn, "alpha beta", "banana", embedder, limit=5)
    finally:
        conn.close()


def test_eval_cli_rejects_unknown_mode(pipeline: tuple[Path, Path, Path, Path]) -> None:
    """``docpipe eval --mode <typo>`` must exit 2, not report term-search numbers."""
    from typer.testing import CliRunner

    from docpipe.cli import app

    _corpus, _manifest, _chunks, index = pipeline
    queries = index.parent / "queries.json"
    queries.write_text(
        json.dumps({"queries": [{"query": "alpha beta", "rel_doc": "a/one.md"}]}), encoding="utf-8"
    )
    result = CliRunner().invoke(
        app, ["eval", "--index", str(index), "--queries", str(queries), "--mode", "banana"]
    )
    assert result.exit_code == 2
    assert "unknown mode" in result.output
    assert "recall" not in result.output
