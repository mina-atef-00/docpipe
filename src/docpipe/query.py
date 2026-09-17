"""Read-only query operations over an index.

All functions take an open connection with ``row_factory = sqlite3.Row`` (open it
with :func:`docpipe.indexer.connect_readonly`). None of them mutate the database.
"""

from __future__ import annotations

import sqlite3
from typing import Any

_SNIPPET_RADIUS = 60


def _snippet(text: str, term: str, radius: int = _SNIPPET_RADIUS) -> str:
    low = text.lower()
    idx = low.find(term)
    start = max(0, idx - radius) if idx >= 0 else 0
    end = min(len(text), idx + len(term) + radius) if idx >= 0 else min(len(text), radius)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


def search(conn: sqlite3.Connection, term: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return chunks containing *term*, grouped per chunk with token positions."""
    normalized = term.lower()
    rows = conn.execute(
        """
        SELECT c.doc_id, d.rel_path, c.chunk_index, c.text, t.position
        FROM terms t
        JOIN chunks c ON t.chunk_id = c.chunk_id
        JOIN documents d ON c.doc_id = d.doc_id
        WHERE t.term = ?
        ORDER BY d.rel_path, c.chunk_index, t.position
        """,
        (normalized,),
    ).fetchall()

    grouped: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        key = (row["rel_path"], row["chunk_index"])
        hit = grouped.setdefault(
            key,
            {
                "doc_id": row["doc_id"],
                "rel_path": row["rel_path"],
                "chunk_index": row["chunk_index"],
                "text": row["text"],
                "positions": [],
            },
        )
        hit["positions"].append(row["position"])

    results = []
    for hit in grouped.values():
        results.append(
            {
                "doc_id": hit["doc_id"],
                "rel_path": hit["rel_path"],
                "chunk_index": hit["chunk_index"],
                "snippet": _snippet(hit["text"], normalized),
                "positions": hit["positions"][:10],
            }
        )
    results.sort(key=lambda h: (h["rel_path"], h["chunk_index"]))
    return results[:limit]


def get_document(conn: sqlite3.Connection, doc_id: str) -> dict[str, Any] | None:
    """Return a document's metadata and all of its chunks, or None when absent."""
    doc = conn.execute(
        "SELECT doc_id, rel_path, sha256, size, kind FROM documents WHERE doc_id = ?",
        (doc_id,),
    ).fetchone()
    if doc is None:
        return None
    chunks = conn.execute(
        "SELECT chunk_index, text FROM chunks WHERE doc_id = ? ORDER BY chunk_index",
        (doc_id,),
    ).fetchall()
    return {
        "doc_id": doc["doc_id"],
        "rel_path": doc["rel_path"],
        "sha256": doc["sha256"],
        "size": doc["size"],
        "kind": doc["kind"],
        "chunks": [{"chunk_index": c["chunk_index"], "text": c["text"]} for c in chunks],
    }


def list_documents(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    """Return documents ordered by rel_path, with their chunk counts."""
    rows = conn.execute(
        """
        SELECT d.doc_id, d.rel_path, d.kind, d.size, COUNT(c.chunk_id) AS nchunks
        FROM documents d
        LEFT JOIN chunks c ON d.doc_id = c.doc_id
        GROUP BY d.doc_id
        ORDER BY d.rel_path
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "doc_id": r["doc_id"],
            "rel_path": r["rel_path"],
            "kind": r["kind"],
            "size": r["size"],
            "chunks": r["nchunks"],
        }
        for r in rows
    ]


def chunk_context(
    conn: sqlite3.Connection, doc_id: str, chunk_index: int, window: int = 1
) -> list[dict[str, Any]]:
    """Return chunks around *chunk_index* within a document, for context display."""
    rows = conn.execute(
        """
        SELECT chunk_index, text FROM chunks
        WHERE doc_id = ? AND chunk_index BETWEEN ? AND ?
        ORDER BY chunk_index
        """,
        (doc_id, chunk_index - window, chunk_index + window),
    ).fetchall()
    return [{"chunk_index": r["chunk_index"], "text": r["text"]} for r in rows]


def stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return the run summary recorded at index build time."""
    run = conn.execute("SELECT run_id, doc_count, chunk_count, term_count FROM runs").fetchone()
    return {
        "documents": run["doc_count"],
        "chunks": run["chunk_count"],
        "terms": run["term_count"],
        "run_id": run["run_id"],
    }
