"""The provenance gate: passes clean, fails loudly on corruption."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from docpipe.ingest import load_manifest
from docpipe.verify import load_exemptions, verify_index


def _verify(
    pipeline: tuple[Path, Path, Path, Path],
    corpus: Path,
    exemptions: set[str] | None = None,
) -> tuple[bool, list[str], dict]:
    _corpus, manifest_path, _chunks, index_path = pipeline
    manifest = load_manifest(manifest_path)
    return verify_index(index_path, corpus, manifest, exemptions or set())


def test_verify_passes_clean(pipeline: tuple[Path, Path, Path, Path]) -> None:
    corpus, _m, _c, _i = pipeline
    ok, failures, summary = _verify(pipeline, corpus)
    assert ok
    assert failures == []
    assert summary["failures"] == 0


def test_verify_fails_orphan_chunk(pipeline: tuple[Path, Path, Path, Path]) -> None:
    corpus, _m, _c, index = pipeline
    conn = sqlite3.connect(index)
    conn.execute(
        "INSERT INTO chunks(chunk_id, doc_id, chunk_index, text) VALUES (?,?,?,?)",
        ("deadbeef", "f" * 64, 999, "orphan row"),
    )
    conn.commit()
    conn.close()
    ok, failures, _summary = _verify(pipeline, corpus)
    assert not ok
    assert any("reference a missing document" in f for f in failures)


def test_verify_fails_hash_mismatch(pipeline: tuple[Path, Path, Path, Path]) -> None:
    corpus, _m, _c, index = pipeline
    conn = sqlite3.connect(index)
    conn.execute("UPDATE documents SET sha256 = ? WHERE rel_path = 'a/one.md'", ("0" * 64,))
    conn.commit()
    conn.close()
    ok, failures, _summary = _verify(pipeline, corpus)
    assert not ok
    assert any("hash mismatch" in f or "doc_id" in f for f in failures)


def test_verify_fails_missing_file(pipeline: tuple[Path, Path, Path, Path]) -> None:
    corpus, _m, _c, _i = pipeline
    (corpus / "a" / "one.md").unlink()
    ok, failures, _summary = _verify(pipeline, corpus)
    assert not ok
    assert any("missing from disk" in f for f in failures)


def test_verify_exemption_counted(pipeline: tuple[Path, Path, Path, Path]) -> None:
    corpus, _m, _c, _i = pipeline
    (corpus / "a" / "one.md").unlink()
    ok, failures, summary = _verify(pipeline, corpus, exemptions={"a/one.md"})
    assert ok
    assert failures == []
    assert summary["exempted"] == 1


def test_load_exemptions(tmp_path: Path) -> None:
    path = tmp_path / "exemptions.json"
    path.write_text('{"rel_paths": ["a.md", "b.md"]}', encoding="utf-8")
    assert load_exemptions(path) == {"a.md", "b.md"}
    assert load_exemptions(None) == set()
