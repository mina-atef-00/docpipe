"""Ingest behaviour: detection, dedupe, path safety and quarantine."""

from __future__ import annotations

from pathlib import Path

from docpipe.ingest import detect_kind, ingest_corpus


def test_detect_kind() -> None:
    assert detect_kind(Path("a.md")) == "markdown"
    assert detect_kind(Path("a.markdown")) == "markdown"
    assert detect_kind(Path("a.txt")) == "text"
    assert detect_kind(Path("a.pdf")) == "pdf"
    assert detect_kind(Path("a.bin")) == "unknown"


def test_dedupe_identical_content(small_corpus: Path) -> None:
    manifest = ingest_corpus(small_corpus, small_corpus.parent / "q")
    dup = [f for f in manifest["files"] if f["status"] == "duplicate"]
    assert len(dup) == 1
    assert dup[0]["rel_path"] == "a/dup2.txt"
    assert dup[0]["duplicate_of"] == "a/dup.txt"
    assert manifest["summary"]["duplicates"] == 1
    assert manifest["summary"]["unique"] == manifest["summary"]["files"] - 1


def test_symlink_file_escape_quarantined(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "ok.txt").write_text("fine\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside content\n", encoding="utf-8")
    (corpus / "leak.txt").symlink_to(outside)
    manifest = ingest_corpus(corpus, tmp_path / "q")
    reasons = [q["reason"] for q in manifest["quarantine"]]
    assert any("symlink escapes corpus root" in r for r in reasons)
    assert all(f["rel_path"] != "leak.txt" for f in manifest["files"])


def test_symlink_dir_escape_quarantined(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "x.txt").write_text("x\n", encoding="utf-8")
    (corpus / "escape").symlink_to(outside_dir, target_is_directory=True)
    manifest = ingest_corpus(corpus, tmp_path / "q")
    reasons = [q["reason"] for q in manifest["quarantine"]]
    assert any("symlink directory escapes corpus root" in r for r in reasons)


def test_non_utf8_text_quarantined(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "bad.txt").write_bytes(b"\xff\xfe\x00bad")
    manifest = ingest_corpus(corpus, tmp_path / "q")
    reasons = [q["reason"] for q in manifest["quarantine"]]
    assert any("not valid UTF-8 text" in r for r in reasons)


def test_unsupported_type_skipped(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    manifest = ingest_corpus(corpus, tmp_path / "q")
    assert len(manifest["skipped"]) == 1
    assert manifest["skipped"][0]["rel_path"] == "img.png"
    assert manifest["skipped"][0]["reason"] == "unsupported file type"


def test_manifest_deterministic(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.txt").write_text("hello\n", encoding="utf-8")
    (corpus / "b.txt").write_text("world\n", encoding="utf-8")
    m1 = ingest_corpus(corpus, tmp_path / "q1")
    m2 = ingest_corpus(corpus, tmp_path / "q2")
    assert m1["files"] == m2["files"]
    assert m1["summary"] == m2["summary"]


def test_quarantine_log_written(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "bad.txt").write_bytes(b"\xff\xfe")
    qdir = tmp_path / "q"
    ingest_corpus(corpus, qdir)
    log = (qdir / "quarantine.log").read_text(encoding="utf-8")
    assert "not valid UTF-8 text" in log
