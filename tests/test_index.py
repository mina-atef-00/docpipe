"""Index determinism and content-keyed identity."""

from __future__ import annotations

import hashlib
import sqlite3
import subprocess
from pathlib import Path

from docpipe.hashing import sha256_file
from docpipe.indexer import build_index, connect_readonly
from docpipe.ingest import ingest_corpus, write_manifest
from docpipe.parse import parse_manifest, write_chunks


def _dump_hash(path: Path) -> str:
    result = subprocess.run(
        ["sqlite3", str(path), ".dump"],
        capture_output=True,
        check=True,
        text=True,
    )
    return hashlib.sha256(result.stdout.encode("utf-8")).hexdigest()


def _build(corpus: Path, work: Path, index: Path) -> None:
    manifest = ingest_corpus(corpus, work / "q")
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


def test_index_rebuild_identical(generated_corpus: Path, tmp_path: Path) -> None:
    idx_a = tmp_path / "a.sqlite"
    idx_b = tmp_path / "b.sqlite"
    _build(generated_corpus, tmp_path / "wa", idx_a)
    _build(generated_corpus, tmp_path / "wb", idx_b)
    assert _dump_hash(idx_a) == _dump_hash(idx_b)
    assert idx_a.read_bytes() == idx_b.read_bytes()


def test_doc_id_is_content_hash(pipeline: tuple[Path, Path, Path, Path]) -> None:
    corpus, _manifest, _chunks, index = pipeline
    conn = connect_readonly(index)
    try:
        rows = conn.execute("SELECT rel_path, doc_id, sha256 FROM documents").fetchall()
        assert len(rows) > 0
        for row in rows:
            assert row["doc_id"] == row["sha256"]
            assert row["doc_id"] == sha256_file(corpus / row["rel_path"])
    finally:
        conn.close()


def test_no_wallclock_in_schema(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _manifest, _chunks, index = pipeline
    conn = sqlite3.connect(index)
    try:
        schema = "\n".join(
            r[0] for r in conn.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")
        )
    finally:
        conn.close()
    lowered = schema.lower()
    assert "timestamp" not in lowered
    assert "datetime" not in lowered
    assert "julianday" not in lowered
    assert "current_time" not in lowered
