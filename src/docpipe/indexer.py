"""Deterministic SQLite index build.

Every identifier is content derived and every insert stream is sorted before it
is written. There are no wall-clock values anywhere in the database, so building
the index twice from the same manifest and chunk stream yields a byte-identical
file (verified by hashing the SQLite ``.dump``).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .chunking import tokenize
from .hashing import sha256_text

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE documents (
    doc_id   TEXT PRIMARY KEY,   -- SHA-256 of the source file bytes
    rel_path TEXT NOT NULL,      -- canonical relative path within the corpus
    sha256   TEXT NOT NULL,      -- duplicate of doc_id, kept for readability
    size     INTEGER NOT NULL,
    kind     TEXT NOT NULL       -- 'text' | 'markdown' | 'pdf'
);

CREATE TABLE chunks (
    chunk_id    TEXT PRIMARY KEY,  -- SHA-256 of doc_id + index + text
    doc_id      TEXT NOT NULL REFERENCES documents(doc_id),
    chunk_index INTEGER NOT NULL,
    text        TEXT NOT NULL,
    UNIQUE (doc_id, chunk_index)
);

CREATE TABLE terms (
    term     TEXT NOT NULL,
    chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id),
    position INTEGER NOT NULL,   -- token position within the chunk
    PRIMARY KEY (term, chunk_id, position)
);

CREATE TABLE runs (
    run_id          TEXT PRIMARY KEY,  -- SHA-256 of manifest hash + chunks hash
    manifest_sha256 TEXT NOT NULL,
    chunks_sha256   TEXT NOT NULL,
    doc_count       INTEGER NOT NULL,
    chunk_count     INTEGER NOT NULL,
    term_count      INTEGER NOT NULL
);

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def chunk_id(doc_id: str, index: int, text: str) -> str:
    """Content-bound chunk identifier."""
    return sha256_text(f"{doc_id}\x00{index}\x00{text}")


def document_rows(manifest: dict) -> list[tuple[str, str, str, int, str]]:
    rows = [
        (f["sha256"], f["rel_path"], f["sha256"], f["size"], f["kind"])
        for f in manifest["files"]
        if f["status"] == "ok"
    ]
    rows.sort(key=lambda r: r[1])
    return rows


def chunk_rows(chunks: list[dict]) -> list[tuple[str, str, int, str]]:
    rows = [
        (
            chunk_id(c["doc_id"], c["chunk_index"], c["text"]),
            c["doc_id"],
            c["chunk_index"],
            c["text"],
        )
        for c in chunks
    ]
    rows.sort(key=lambda r: (r[1], r[2]))
    return rows


def term_rows(chunk_rows_: list[tuple[str, str, int, str]]) -> list[tuple[str, str, int]]:
    rows: list[tuple[str, str, int]] = []
    for cid, _doc_id, _index, text in chunk_rows_:
        for position, term in enumerate(tokenize(text)):
            rows.append((term, cid, position))
    rows.sort()
    return rows


def run_id(manifest_sha: str, chunks_sha: str) -> str:
    return sha256_text(f"{manifest_sha}:{chunks_sha}")


def build_index(
    manifest: dict,
    chunks: list[dict],
    manifest_sha: str,
    chunks_sha: str,
    out_path: Path,
) -> dict[str, int]:
    """Build the index database at *out_path* and return row counts."""
    if out_path.exists():
        out_path.unlink()
    docs = document_rows(manifest)
    chks = chunk_rows(chunks)
    terms = term_rows(chks)

    conn = sqlite3.connect(out_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO documents(doc_id, rel_path, sha256, size, kind) VALUES (?,?,?,?,?)",
            docs,
        )
        conn.executemany(
            "INSERT INTO chunks(chunk_id, doc_id, chunk_index, text) VALUES (?,?,?,?)", chks
        )
        conn.executemany("INSERT INTO terms(term, chunk_id, position) VALUES (?,?,?)", terms)
        rid = run_id(manifest_sha, chunks_sha)
        conn.execute(
            "INSERT INTO runs(run_id, manifest_sha256, chunks_sha256, doc_count,"
            " chunk_count, term_count) VALUES (?,?,?,?,?,?)",
            (rid, manifest_sha, chunks_sha, len(docs), len(chks), len(terms)),
        )
        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?,?)",
            sorted(
                [
                    ("schema_version", str(SCHEMA_VERSION)),
                    ("run_id", rid),
                ]
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "documents": len(docs),
        "chunks": len(chks),
        "terms": len(terms),
    }


def connect_readonly(path: Path) -> sqlite3.Connection:
    """Open an index in read-only mode via a URI so it can never be mutated."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
