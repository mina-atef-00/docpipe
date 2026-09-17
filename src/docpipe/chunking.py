"""Deterministic text chunking and tokenization.

Chunking is a pure function of the input text: the same bytes always produce the
same chunk sequence, which is what lets a rebuilt index match an earlier one
byte for byte.
"""

from __future__ import annotations

import re

DEFAULT_TARGET = 800
MIN_CHUNK = 200

_BLANK_RE = re.compile(r"\n[ \t]*\n+")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def normalize(text: str) -> str:
    """Normalize line endings to a single ``\\n`` form."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _split_blocks(text: str) -> list[str]:
    raw = _BLANK_RE.split(text)
    return [block.strip() for block in raw if block.strip()]


def _hard_split(block: str, target: int) -> list[str]:
    """Split a single oversized block at the nearest whitespace boundary."""
    if len(block) <= target:
        return [block]
    pieces: list[str] = []
    remaining = block
    while len(remaining) > target:
        window = remaining[:target]
        cut = max(window.rfind(" "), window.rfind("\n"), window.rfind("\t"))
        if cut <= target // 2:
            cut = target
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        pieces.append(remaining)
    return pieces


def chunk_text(text: str, target: int = DEFAULT_TARGET) -> list[str]:
    """Split *text* into chunks of roughly *target* characters.

    Blocks are separated on blank lines, which keeps paragraphs and markdown
    headings intact. Blocks accumulate into a chunk until *target* is reached;
    a single block larger than *target* is hard-split on word boundaries.
    """
    blocks = _split_blocks(normalize(text))
    chunks: list[str] = []
    buffer: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal buffer, size
        if buffer:
            chunks.append("\n\n".join(buffer))
            buffer = []
            size = 0

    for block in blocks:
        if size + len(block) + 2 > target and size >= MIN_CHUNK:
            flush()
        if len(block) > target:
            flush()
            chunks.extend(_hard_split(block, target))
            continue
        buffer.append(block)
        size += len(block) + 2
    flush()
    return chunks


def tokenize(text: str) -> list[str]:
    """Lowercase and split *text* into alphanumeric tokens."""
    return _TOKEN_RE.findall(text.lower())
