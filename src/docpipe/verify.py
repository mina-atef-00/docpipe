"""The provenance verification gate.

Every row in the index must trace back to a real source file and its content
hash. The gate fails loudly with a non-zero exit code on any row that cannot be
traced, and reports every exemption explicitly. There is no silent path.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .hashing import sha256_file


def load_exemptions(path: Path | None) -> set[str]:
    """Load exempted rel_paths from a JSON file, or return the empty set.

    The file has the shape ``{"rel_paths": ["a.md", ...]}``.
    """
    if path is None:
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data.get("rel_paths", []))


def verify_index(
    index_path: Path,
    corpus_root: Path,
    manifest: dict,
    exemptions: set[str],
) -> tuple[bool, list[str], dict[str, Any]]:
    """Check the index against the corpus and manifest.

    Returns ``(ok, failures, summary)``. *failures* is empty when every row
    traces to a real source file.
    """
    conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    failures: list[str] = []
    summary: dict[str, Any] = {"exempted": 0}
    try:
        # Referential integrity inside the index.
        orphan_chunks = conn.execute(
            "SELECT COUNT(*) AS n FROM chunks c"
            " WHERE NOT EXISTS (SELECT 1 FROM documents d WHERE d.doc_id = c.doc_id)"
        ).fetchone()["n"]
        if orphan_chunks:
            failures.append(f"{orphan_chunks} chunk(s) reference a missing document")

        orphan_terms = conn.execute(
            "SELECT COUNT(*) AS n FROM terms t"
            " WHERE NOT EXISTS (SELECT 1 FROM chunks c WHERE c.chunk_id = t.chunk_id)"
        ).fetchone()["n"]
        if orphan_terms:
            failures.append(f"{orphan_terms} term(s) reference a missing chunk")

        dup_paths = conn.execute(
            "SELECT rel_path, COUNT(*) AS n FROM documents GROUP BY rel_path HAVING n > 1"
        ).fetchall()
        if dup_paths:
            failures.append(
                "duplicate rel_path in documents: " + ", ".join(r["rel_path"] for r in dup_paths)
            )

        manifest_files = manifest["files"]
        manifest_hash_counts: dict[str, int] = {}
        for entry in manifest_files:
            if entry["status"] in ("ok", "duplicate"):
                manifest_hash_counts[entry["sha256"]] = (
                    manifest_hash_counts.get(entry["sha256"], 0) + 1
                )

        docs = conn.execute(
            "SELECT doc_id, rel_path, sha256, kind FROM documents ORDER BY rel_path"
        ).fetchall()

        # Forward provenance: every document row -> a real file with the same hash.
        for row in docs:
            doc_id = row["doc_id"]
            rel_path = row["rel_path"]
            stored = row["sha256"]
            if doc_id != stored:
                failures.append(f"document {rel_path}: doc_id {doc_id} != stored sha256 {stored}")
            if rel_path in exemptions:
                summary["exempted"] += 1
                continue
            path = corpus_root / rel_path
            if not path.exists():
                failures.append(f"document {rel_path}: source file missing from disk")
                continue
            try:
                actual = sha256_file(path)
            except OSError as exc:
                failures.append(f"document {rel_path}: unreadable ({exc.__class__.__name__})")
                continue
            if actual != stored:
                failures.append(
                    f"document {rel_path}: hash mismatch (stored {stored}, disk {actual})"
                )
            if actual != doc_id:
                failures.append(f"document {rel_path}: doc_id does not match content hash")

        # Completeness: every manifest hash -> exactly one canonical document row.
        for digest in sorted(manifest_hash_counts):
            rows = conn.execute(
                "SELECT rel_path FROM documents WHERE doc_id = ?", (digest,)
            ).fetchall()
            if len(rows) != 1:
                failures.append(f"hash {digest}: expected 1 document row, found {len(rows)}")
                continue
            canonical = next(
                entry["rel_path"]
                for entry in manifest_files
                if entry["sha256"] == digest and entry["status"] == "ok"
            )
            if rows[0]["rel_path"] != canonical:
                failures.append(
                    f"hash {digest}: index rel_path {rows[0]['rel_path']} != "
                    f"manifest canonical {canonical}"
                )

        # No phantom documents: every document row must map to a manifest hash.
        for row in docs:
            if row["doc_id"] not in manifest_hash_counts:
                failures.append(
                    f"document {row['rel_path']}: doc_id {row['doc_id']} not in manifest"
                )

        # Runs table must be a single row whose counts match the live tables.
        runs = conn.execute(
            "SELECT run_id, doc_count, chunk_count, term_count FROM runs"
        ).fetchall()
        if len(runs) != 1:
            failures.append(f"expected exactly 1 run row, found {len(runs)}")
        else:
            run = runs[0]
            live_docs = conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
            live_chunks = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
            live_terms = conn.execute("SELECT COUNT(*) AS n FROM terms").fetchone()["n"]
            if run["doc_count"] != live_docs:
                failures.append(f"runs.doc_count {run['doc_count']} != live documents {live_docs}")
            if run["chunk_count"] != live_chunks:
                failures.append(
                    f"runs.chunk_count {run['chunk_count']} != live chunks {live_chunks}"
                )
            if run["term_count"] != live_terms:
                failures.append(f"runs.term_count {run['term_count']} != live terms {live_terms}")
            summary["run_id"] = run["run_id"]
            summary["documents"] = live_docs
            summary["chunks"] = live_chunks
            summary["terms"] = live_terms
    finally:
        conn.close()

    summary["failures"] = len(failures)
    return not failures, failures, summary
