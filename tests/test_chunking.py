"""Chunking and tokenization are pure, deterministic functions."""

from __future__ import annotations

from docpipe.chunking import chunk_text, normalize, tokenize


def test_chunk_text_deterministic() -> None:
    text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
    assert chunk_text(text) == chunk_text(text)


def test_chunk_text_keeps_small_text_in_one_chunk() -> None:
    text = "One line.\n\nTwo lines."
    assert len(chunk_text(text, target=800)) == 1


def test_chunk_text_splits_oversized_blocks() -> None:
    text = ("A" * 900) + "\n\n" + ("B" * 900)
    chunks = chunk_text(text, target=800)
    assert len(chunks) >= 2


def test_tokenize_lowercases_and_splits() -> None:
    assert tokenize("Hello, WORLD! 42") == ["hello", "world", "42"]


def test_normalize_line_endings() -> None:
    assert normalize("a\r\nb\rc") == "a\nb\nc"
