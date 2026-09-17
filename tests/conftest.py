"""Shared fixtures and helpers for the docpipe test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import make_corpus  # noqa: E402
from docpipe.hashing import sha256_file  # noqa: E402
from docpipe.indexer import build_index  # noqa: E402
from docpipe.ingest import ingest_corpus, write_manifest  # noqa: E402
from docpipe.parse import parse_manifest, write_chunks  # noqa: E402


def build_small_corpus(root: Path) -> Path:
    """Create a small, controlled corpus with text, a duplicate and a binary."""
    corpus = root / "corpus"
    (corpus / "a").mkdir(parents=True, exist_ok=True)
    (corpus / "a" / "one.md").write_text("# One\n\nAlpha beta gamma delta.\n", encoding="utf-8")
    (corpus / "a" / "two.txt").write_text("epsilon zeta eta theta.\n", encoding="utf-8")
    (corpus / "a" / "dup.txt").write_text("shared content block.\n", encoding="utf-8")
    (corpus / "a" / "dup2.txt").write_text("shared content block.\n", encoding="utf-8")
    (corpus / "a" / "binary.bin").write_bytes(b"\x00\x01\x02\xff\xfe\x89PNG")
    return corpus


def run_pipeline(corpus: Path, work: Path) -> tuple[Path, Path, Path]:
    """Run ingest -> parse -> index and return (manifest, chunks, index) paths."""
    manifest_path = work / "manifest.json"
    chunks_path = work / "chunks.jsonl"
    index_path = work / "index.sqlite"
    manifest = ingest_corpus(corpus, work / "quarantine")
    write_manifest(manifest, manifest_path)
    chunks, _report = parse_manifest(manifest, corpus, pdf_enabled=True)
    write_chunks(chunks, chunks_path)
    build_index(
        manifest,
        chunks,
        sha256_file(manifest_path),
        sha256_file(chunks_path),
        index_path,
    )
    return manifest_path, chunks_path, index_path


@pytest.fixture
def small_corpus(tmp_path: Path) -> Path:
    return build_small_corpus(tmp_path)


@pytest.fixture
def pipeline(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Return (corpus, manifest, chunks, index) for a small corpus."""
    corpus = build_small_corpus(tmp_path)
    manifest_path, chunks_path, index_path = run_pipeline(corpus, tmp_path)
    return corpus, manifest_path, chunks_path, index_path


@pytest.fixture
def generated_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    make_corpus.generate_corpus(corpus, seed=make_corpus.SEED)
    return corpus
