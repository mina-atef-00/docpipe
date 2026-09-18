"""Grounded answers: extractive composition with citations that trace to chunks."""

from __future__ import annotations

from pathlib import Path

import pytest

from docpipe import answer as answer_module
from docpipe.embed import make_local_embedder
from docpipe.indexer import connect_readonly


@pytest.fixture
def hybrid_index(pipeline: tuple[Path, Path, Path, Path]) -> tuple[object, object]:
    _corpus, _manifest, _chunks, index = pipeline
    conn = connect_readonly(index)
    return conn, make_local_embedder()


def _results(conn: object, query: str, limit: int = 5) -> list[dict]:
    from docpipe.search import term_search

    return term_search(conn, query, limit)  # type: ignore[arg-type]


def test_answer_cites_retrieved_chunks(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _manifest, _chunks, index = pipeline
    conn = connect_readonly(index)
    try:
        embedder = make_local_embedder()
        result = answer_module.answer(conn, "alpha beta gamma", embedder, mode="term", k=3)
    finally:
        conn.close()
    assert result["refused"] is False
    assert result["answer"]
    hits = result["hits"]
    identifiers = {(hit["doc_id"], hit["chunk_index"]) for hit in hits}
    assert identifiers, "citations must trace to retrieved chunks"
    for citation in result["citations"]:
        assert result["answer"].find(citation["marker"]) >= 0
        assert (citation["doc_id"], citation["chunk_index"]) in identifiers


def test_answer_refuses_unsupported_query(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _manifest, _chunks, index = pipeline
    conn = connect_readonly(index)
    try:
        embedder = make_local_embedder()
        result = answer_module.answer(conn, "zzzzq qq zzzzq", embedder, mode="term", k=3)
    finally:
        conn.close()
    assert result["refused"] is True
    assert "cannot answer from the corpus" in result["answer"]
    assert result["citations"] == []


def test_answer_citations_exist_for_same_question(
    pipeline: tuple[Path, Path, Path, Path],
) -> None:
    _corpus, _manifest, _chunks, index = pipeline
    conn = connect_readonly(index)
    try:
        embedder = make_local_embedder()
        result = answer_module.answer(conn, "alpha beta gamma", embedder, mode="term", k=3)
    finally:
        conn.close()
    hits = result["hits"]
    retrieval_ids = {hit["chunk_id"] for hit in hits}
    citation_ids = {citation["chunk_id"] for citation in result["citations"]}
    assert citation_ids
    assert citation_ids <= retrieval_ids
    assert all(hit["chunk_id"] in retrieval_ids for hit in hits)


def test_answer_matches_query_result_shape(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _manifest, _chunks, index = pipeline
    conn = connect_readonly(index)
    try:
        embedder = make_local_embedder()
        result = answer_module.answer(conn, "alpha beta gamma", embedder, mode="term", k=3)
        hits = _results(conn, "alpha beta gamma", 3)
    finally:
        conn.close()
    assert {h["chunk_id"] for h in result["hits"]} <= {h["chunk_id"] for h in hits}
