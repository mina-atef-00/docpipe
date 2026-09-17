"""Query operations return correct, deterministic results."""

from __future__ import annotations

from pathlib import Path

from docpipe import query as query_module
from docpipe.indexer import connect_readonly


def test_search_returns_hits(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _manifest, _chunks, index = pipeline
    conn = connect_readonly(index)
    try:
        hits = query_module.search(conn, "alpha")
    finally:
        conn.close()
    assert hits
    assert hits[0]["rel_path"] == "a/one.md"
    assert 1 in hits[0]["positions"]


def test_search_no_hits(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _manifest, _chunks, index = pipeline
    conn = connect_readonly(index)
    try:
        hits = query_module.search(conn, "zzzznone")
    finally:
        conn.close()
    assert hits == []


def test_get_document(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _manifest, _chunks, index = pipeline
    conn = connect_readonly(index)
    try:
        docs = query_module.list_documents(conn)
        doc = query_module.get_document(conn, docs[0]["doc_id"])
    finally:
        conn.close()
    assert doc is not None
    assert doc["doc_id"] == docs[0]["doc_id"]
    assert doc["chunks"]


def test_get_document_missing(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _manifest, _chunks, index = pipeline
    conn = connect_readonly(index)
    try:
        doc = query_module.get_document(conn, "f" * 64)
    finally:
        conn.close()
    assert doc is None


def test_list_documents_sorted(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _manifest, _chunks, index = pipeline
    conn = connect_readonly(index)
    try:
        docs = query_module.list_documents(conn)
    finally:
        conn.close()
    rels = [d["rel_path"] for d in docs]
    assert rels == sorted(rels)


def test_chunk_context(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _manifest, _chunks, index = pipeline
    conn = connect_readonly(index)
    try:
        docs = query_module.list_documents(conn)
        target = next(d for d in docs if d["rel_path"] == "a/one.md")
        context = query_module.chunk_context(conn, target["doc_id"], 0, window=1)
    finally:
        conn.close()
    assert any(c["chunk_index"] == 0 for c in context)


def test_stats(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _manifest, _chunks, index = pipeline
    conn = connect_readonly(index)
    try:
        current = query_module.stats(conn)
    finally:
        conn.close()
    assert current["documents"] > 0
    assert len(current["run_id"]) == 64
