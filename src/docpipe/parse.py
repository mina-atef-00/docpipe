"""Text extraction and chunking over the ingest manifest.

PDF extraction uses pymupdf when it is installed and degrades with a clear
message when it is not. Chunk output is deterministic: files are processed in
manifest order and chunks in document order.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .chunking import chunk_text


def pymupdf_available() -> bool:
    """Return True when the pymupdf module is importable."""
    try:
        import pymupdf  # noqa: F401

        return True
    except ImportError:
        return False


def extract_pdf_text(path: Path) -> str:
    """Extract plain text from a PDF via pymupdf, one page per block."""
    import pymupdf

    document = pymupdf.open(str(path))
    try:
        pages: list[str] = [str(page.get_text("text")) for page in document]
    finally:
        document.close()
    return "\n\n".join(pages)


def parse_manifest(
    manifest: dict,
    corpus_root: Path,
    *,
    pdf_enabled: bool,
) -> tuple[list[dict], dict[str, Any]]:
    """Extract and chunk every ``ok`` document in *manifest*.

    Returns ``(chunks, report)``. *chunks* is a list of dicts with ``doc_id``,
    ``rel_path``, ``chunk_index`` and ``text``, sorted by document then index.
    *report* carries per-run counts and any per-document errors.
    """
    chunks: list[dict] = []
    report: dict[str, Any] = {
        "documents": 0,
        "chunks": 0,
        "pdf_ok": 0,
        "pdf_skipped": 0,
        "empty": 0,
        "errors": [],
    }
    for entry in manifest["files"]:
        if entry["status"] != "ok":
            continue
        report["documents"] += 1
        rel_path = entry["rel_path"]
        kind = entry["kind"]
        path = corpus_root / rel_path
        try:
            if kind == "pdf":
                if not pdf_enabled:
                    report["pdf_skipped"] += 1
                    continue
                text = extract_pdf_text(path)
                report["pdf_ok"] += 1
            else:
                text = path.read_text(encoding="utf-8")
        except Exception as exc:  # surfaced verbatim in the report, never swallowed
            report["errors"].append(
                {"rel_path": rel_path, "error": f"{exc.__class__.__name__}: {exc}"}
            )
            continue
        pieces = chunk_text(text)
        if not pieces:
            report["empty"] += 1
        for index, piece in enumerate(pieces):
            chunks.append(
                {
                    "doc_id": entry["sha256"],
                    "rel_path": rel_path,
                    "chunk_index": index,
                    "text": piece,
                }
            )
        report["chunks"] += len(pieces)

    chunks.sort(key=lambda c: (c["doc_id"], c["chunk_index"]))
    return chunks, report


def write_chunks(chunks: list[dict], out_path: Path) -> None:
    """Write chunks as newline-delimited JSON to *out_path*."""
    lines = [json.dumps(c, sort_keys=True) for c in chunks]
    out_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def load_chunks(path: Path) -> list[dict]:
    """Read a chunks JSONL file back into a list of dicts."""
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows
