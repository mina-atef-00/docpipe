"""sqlite-vec vector storage and search.

Embeddings live in the same SQLite file as the term index, inside a sqlite-vec
``vec0`` virtual table. The table is created at build time with the embedder's
dimension and a cosine distance metric; search returns ``distance = 1 - cosine``
so a similarity score is ``1 - distance``.

sqlite-vec is a real dependency here, not a claim: it is imported and loaded at
build and query time. If the extension cannot be loaded the operation raises a
clear error rather than silently falling back to something else.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .embed import Embedder

VECTOR_TABLE = "chunk_vectors"


class VectorUnavailableError(Exception):
    """sqlite-vec could not be loaded."""


def vec_available() -> bool:
    """Return True when the sqlite_vec module is importable."""
    try:
        import sqlite_vec  # noqa: F401

        return True
    except ImportError:
        return False


def load_vec(conn: sqlite3.Connection) -> None:
    """Load the sqlite-vec extension into *conn*, raising on failure."""
    try:
        import sqlite_vec
    except ImportError as exc:
        raise VectorUnavailableError(
            "sqlite-vec is not installed; install it with `pip install sqlite-vec` "
            "or build the index with a local environment that has it"
        ) from exc
    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    except Exception as exc:  # load failures surface as various sqlite3 errors
        raise VectorUnavailableError(f"failed to load sqlite-vec extension: {exc}") from exc


def vector_schema(dim: int) -> str:
    """Return the ``CREATE VIRTUAL TABLE`` statement for the vector table."""
    return (
        f"CREATE VIRTUAL TABLE {VECTOR_TABLE} USING vec0("
        f"embedding float[{dim}] distance_metric=cosine, "
        f"+chunk_id text"
        f")"
    )


def write_vectors(
    conn: sqlite3.Connection,
    chunks: list[dict[str, Any]],
    embedder: Embedder,
) -> int:
    """Embed every chunk and insert its vector into the vector table.

    Chunks are embedded in a single batch and inserted in ``chunk_id`` order so
    the table is deterministic. Returns the number of vectors written.
    """
    if not chunks:
        return 0
    load_vec(conn)
    dim = embedder.dim
    conn.execute(vector_schema(dim))
    texts = [chunk["text"] for chunk in chunks]
    vectors = embedder.embed(texts)
    if len(vectors) != len(chunks):
        raise VectorUnavailableError(
            f"embedder returned {len(vectors)} vectors for {len(chunks)} chunks"
        )
    rows = sorted(zip(chunks, vectors, strict=True), key=lambda pair: pair[0]["chunk_id"])
    for rowid, (chunk, vector) in enumerate(rows, start=1):
        conn.execute(
            f"INSERT INTO {VECTOR_TABLE}(rowid, embedding, chunk_id) VALUES (?, ?, ?)",
            (rowid, json.dumps(vector, separators=(",", ":")), chunk["chunk_id"]),
        )
    return len(rows)


def vector_search(
    conn: sqlite3.Connection, query_vector: list[float], limit: int = 20
) -> list[dict[str, Any]]:
    """Return the top *limit* chunks nearest to *query_vector* by cosine.

    Each hit carries a ``score`` in ``[-1, 1]`` computed as ``1 - distance``.
    The KNN query runs against the vector table alone; joining to ``chunks`` and
    ``documents`` happens in a second query, because sqlite-vec rejects any
    extra constraint on an auxiliary column inside a KNN query.
    """
    load_vec(conn)
    query = json.dumps(query_vector, separators=(",", ":"))
    knn = conn.execute(
        f"""
        SELECT chunk_id, distance
        FROM {VECTOR_TABLE}
        WHERE embedding MATCH ? AND k = ?
        ORDER BY distance
        """,
        (query, limit),
    ).fetchall()
    if not knn:
        return []
    chunk_ids = [row["chunk_id"] for row in knn]
    placeholders = ",".join("?" for _ in chunk_ids)
    info = conn.execute(
        f"""
        SELECT c.chunk_id, c.doc_id, d.rel_path, c.chunk_index
        FROM chunks c
        JOIN documents d ON d.doc_id = c.doc_id
        WHERE c.chunk_id IN ({placeholders})
        """,
        chunk_ids,
    ).fetchall()
    by_id = {row["chunk_id"]: row for row in info}
    results: list[dict[str, Any]] = []
    for row in knn:
        meta = by_id.get(row["chunk_id"])
        if meta is None:
            continue
        results.append(
            {
                "doc_id": meta["doc_id"],
                "rel_path": meta["rel_path"],
                "chunk_index": meta["chunk_index"],
                "chunk_id": row["chunk_id"],
                "score": 1.0 - row["distance"],
            }
        )
    return results


def has_vectors(conn: sqlite3.Connection) -> bool:
    """Return True when the index contains a vector table."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (VECTOR_TABLE,),
    ).fetchone()
    return row is not None


def vector_dim(conn: sqlite3.Connection) -> int | None:
    """Return the recorded embedding dimension, or None when not present."""
    row = conn.execute("SELECT value FROM meta WHERE key = 'embed_dim'").fetchone()
    if row is None:
        return None
    return int(row["value"])
