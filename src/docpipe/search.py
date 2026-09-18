"""Ranked search over the index: term, vector and hybrid modes.

Term mode scores chunks with BM25 over the positional term index. Vector mode
embeds the query with the recorded embedder and returns nearest neighbours by
cosine similarity. Hybrid mode merges both and re-ranks a candidate pool with a
weighted combination of the two normalised scores.

The hybrid formula is:

    hybrid_score(c) = alpha * vec_norm(c) + (1 - alpha) * term_norm(c)

where ``vec_score(c) = 1 - cosine_distance``, ``term_score(c) = BM25(k1=1.5,
b=0.75)``, and ``vec_norm`` / ``term_norm`` are min-max normalised to ``[0, 1]``
over the candidate pool (the union of the top ``4 * limit`` chunks from each
mode). A chunk that misses one side of the pool gets the lowest score observed
on that side, so a lexical-only match still ranks below a combined match.
"""

from __future__ import annotations

import math
import sqlite3
from typing import Any

from .chunking import tokenize
from .embed import Embedder
from .vectors import vector_search as _knn

_BM25_K1 = 1.5
_BM25_B = 0.75
_DEFAULT_ALPHA = 0.5
_CANDIDATE_MULTIPLIER = 4


def _snip(text: str, radius: int = 60) -> str:
    text = text.strip()
    if len(text) <= radius * 2 + 3:
        return text
    return f"{text[:radius]}...{text[-radius:]}"


def _snippets(conn: sqlite3.Connection, chunk_ids: list[str]) -> dict[str, str]:
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" for _ in chunk_ids)
    rows = conn.execute(
        f"SELECT chunk_id, text FROM chunks WHERE chunk_id IN ({placeholders})",
        chunk_ids,
    ).fetchall()
    return {row["chunk_id"]: row["text"] for row in rows}


def _positions(conn: sqlite3.Connection, chunk_id: str, tokens: list[str]) -> list[int]:
    if not tokens:
        return []
    placeholders = ",".join("?" for _ in tokens)
    rows = conn.execute(
        f"SELECT position FROM terms WHERE chunk_id = ? AND term IN ({placeholders})"
        " ORDER BY position LIMIT 10",
        [chunk_id, *tokens],
    ).fetchall()
    return [row["position"] for row in rows]


def _corpus_stats(conn: sqlite3.Connection) -> tuple[int, float, dict[str, int]]:
    """Return ``(num_chunks, avg_length, {chunk_id: length})``."""
    num_chunks = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
    lengths = {
        row["chunk_id"]: row["n"]
        for row in conn.execute(
            "SELECT chunk_id, COUNT(*) AS n FROM terms GROUP BY chunk_id"
        ).fetchall()
    }
    total = sum(lengths.values())
    avg_length = total / num_chunks if num_chunks else 0.0
    return num_chunks, avg_length, lengths


def _term_scores(
    conn: sqlite3.Connection,
    tokens: list[str],
    num_chunks: int,
    avg_length: float,
    lengths: dict[str, int],
) -> dict[str, float]:
    """Compute BM25 scores per matching chunk id for a set of query tokens."""
    if not tokens:
        return {}
    placeholders = ",".join("?" for _ in tokens)
    df_rows = conn.execute(
        f"SELECT term, COUNT(DISTINCT chunk_id) AS df FROM terms"
        f" WHERE term IN ({placeholders}) GROUP BY term",
        tokens,
    ).fetchall()
    df = {row["term"]: row["df"] for row in df_rows}
    idf = {
        term: math.log(1.0 + (num_chunks - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()
    }
    tf_rows = conn.execute(
        f"SELECT term, chunk_id, COUNT(*) AS tf FROM terms"
        f" WHERE term IN ({placeholders}) GROUP BY term, chunk_id",
        tokens,
    ).fetchall()
    scores: dict[str, float] = {}
    for row in tf_rows:
        term = row["term"]
        chunk_id = row["chunk_id"]
        tf = row["tf"]
        dl = lengths.get(chunk_id, 0)
        denom = tf + _BM25_K1 * (1.0 - _BM25_B + _BM25_B * dl / avg_length) if avg_length else tf
        contribution = idf.get(term, 0.0) * (tf * (_BM25_K1 + 1.0)) / (denom if denom else 1.0)
        scores[chunk_id] = scores.get(chunk_id, 0.0) + contribution
    return scores


def term_search(conn: sqlite3.Connection, text: str, limit: int = 20) -> list[dict[str, Any]]:
    """Rank chunks by BM25 for the tokens of *text*."""
    tokens = list(dict.fromkeys(tokenize(text)))
    if not tokens:
        return []
    num_chunks, avg_length, lengths = _corpus_stats(conn)
    scores = _term_scores(conn, tokens, num_chunks, avg_length, lengths)
    ranked = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))[:limit]
    chunk_ids = [chunk_id for chunk_id, _score in ranked]
    texts = _snippets(conn, chunk_ids)
    results: list[dict[str, Any]] = []
    for chunk_id, score in ranked:
        row = conn.execute(
            "SELECT c.doc_id, d.rel_path, c.chunk_index FROM chunks c"
            " JOIN documents d ON d.doc_id = c.doc_id WHERE c.chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        if row is None:
            continue
        results.append(
            {
                "doc_id": row["doc_id"],
                "rel_path": row["rel_path"],
                "chunk_index": row["chunk_index"],
                "chunk_id": chunk_id,
                "score": score,
                "snippet": _snip(texts.get(chunk_id, "")),
                "positions": _positions(conn, chunk_id, tokens),
            }
        )
    return results


def vector_search_query(
    conn: sqlite3.Connection, text: str, embedder: Embedder, limit: int = 20
) -> list[dict[str, Any]]:
    """Embed *text* and return nearest chunks by cosine similarity."""
    query_vector = embedder.embed_one(text)
    hits = _knn(conn, query_vector, limit)
    texts = _snippets(conn, [hit["chunk_id"] for hit in hits])
    for hit in hits:
        hit["snippet"] = _snip(texts.get(hit["chunk_id"], ""))
        hit["positions"] = []
    return hits


def _minmax(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    low = min(values.values())
    high = max(values.values())
    if high == low:
        # A constant axis carries no signal; give every candidate the same
        # neutral value so it contributes nothing to the weighted sum.
        return dict.fromkeys(values, 1.0)
    return {key: (value - low) / (high - low) for key, value in values.items()}


def hybrid_search(
    conn: sqlite3.Connection,
    text: str,
    embedder: Embedder,
    limit: int = 20,
    alpha: float = _DEFAULT_ALPHA,
) -> list[dict[str, Any]]:
    """Combine BM25 and cosine ranking with a weighted normalised score."""
    pool_size = max(limit * _CANDIDATE_MULTIPLIER, limit)
    term_hits = term_search(conn, text, pool_size)
    vec_hits = vector_search_query(conn, text, embedder, pool_size)

    term_scores = {hit["chunk_id"]: hit["score"] for hit in term_hits}
    vec_scores = {hit["chunk_id"]: hit["score"] for hit in vec_hits}

    chunk_ids = list(dict.fromkeys([*term_scores.keys(), *vec_scores.keys()]))
    vec_floor = min(vec_scores.values()) if vec_scores else -1.0
    term_floor = min(term_scores.values()) if term_scores else 0.0

    vec_full = {chunk_id: vec_scores.get(chunk_id, vec_floor) for chunk_id in chunk_ids}
    term_full = {chunk_id: term_scores.get(chunk_id, term_floor) for chunk_id in chunk_ids}

    vec_norm = _minmax(vec_full)
    term_norm = _minmax(term_full)

    combined = {
        chunk_id: alpha * vec_norm.get(chunk_id, 0.0) + (1.0 - alpha) * term_norm.get(chunk_id, 0.0)
        for chunk_id in chunk_ids
    }
    ranked = sorted(combined.items(), key=lambda pair: (-pair[1], pair[0]))[:limit]

    texts = _snippets(conn, [chunk_id for chunk_id, _score in ranked])
    tokens = list(dict.fromkeys(tokenize(text)))
    results: list[dict[str, Any]] = []
    for chunk_id, score in ranked:
        row = conn.execute(
            "SELECT c.doc_id, d.rel_path, c.chunk_index FROM chunks c"
            " JOIN documents d ON d.doc_id = c.doc_id WHERE c.chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        if row is None:
            continue
        results.append(
            {
                "doc_id": row["doc_id"],
                "rel_path": row["rel_path"],
                "chunk_index": row["chunk_index"],
                "chunk_id": chunk_id,
                "score": score,
                "snippet": _snip(texts.get(chunk_id, "")),
                "positions": _positions(conn, chunk_id, tokens),
            }
        )
    return results


def run_search(
    conn: sqlite3.Connection,
    text: str,
    mode: str,
    embedder: Embedder,
    limit: int = 20,
    alpha: float = _DEFAULT_ALPHA,
) -> list[dict[str, Any]]:
    """Dispatch a search in ``term``, ``vector`` or ``hybrid`` mode."""
    if mode == "vector":
        return vector_search_query(conn, text, embedder, limit)
    if mode == "hybrid":
        return hybrid_search(conn, text, embedder, limit, alpha)
    return term_search(conn, text, limit)
