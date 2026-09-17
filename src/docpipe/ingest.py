"""Corpus ingestion: walk, hash, dedupe, and quarantine unsafe or unreadable inputs.

The walk is deterministic: files are visited in sorted order, hashes are content
derived, and the manifest is sorted before it is written. Nothing in the
manifest depends on wall-clock time.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .hashing import sha256_bytes, sha256_file
from .pathsafety import is_within_root

MARKDOWN_EXTS = {".md", ".markdown"}
PDF_EXTS = {".pdf"}
TEXT_EXTS = {
    ".txt",
    ".rst",
    ".json",
    ".yaml",
    ".yml",
    ".log",
    ".csv",
    ".toml",
    ".ini",
    ".cfg",
    ".py",
    ".js",
    ".ts",
    ".html",
    ".xml",
    ".sh",
}

SCHEMA_VERSION = 1


def detect_kind(path: Path) -> str:
    """Classify a file by extension: markdown, pdf, text, or unknown."""
    ext = path.suffix.lower()
    if ext in MARKDOWN_EXTS:
        return "markdown"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in TEXT_EXTS:
        return "text"
    return "unknown"


def _sanitize_name(rel_path: str) -> str:
    return rel_path.replace("/", "__").replace("\\", "__")


def _quarantine(
    quarantine_dir: Path,
    rel_path: str,
    reason: str,
    digest: str | None,
    data: bytes | None,
    records: list[dict[str, str | None]],
) -> None:
    records.append({"rel_path": rel_path, "reason": reason, "sha256": digest})
    if data is not None:
        target = quarantine_dir / _sanitize_name(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def ingest_corpus(root: Path, quarantine_dir: Path) -> dict:
    """Walk *root* and produce a deterministic manifest dict.

    Quarantined inputs are copied into *quarantine_dir* and logged with a
    reason. *quarantine_dir* is recreated on each run so stale artifacts from a
    previous run cannot leak into the next one.
    """
    root = root.resolve()
    quarantine_dir = quarantine_dir.resolve()
    if quarantine_dir.exists():
        shutil.rmtree(quarantine_dir)
    quarantine_dir.mkdir(parents=True, exist_ok=True)

    files: list[dict] = []
    quarantined: list[dict] = []
    skipped: list[dict] = []
    symlink_dirs_skipped = 0

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirpath_p = Path(dirpath)
        kept_dirs: list[str] = []
        for name in dirnames:
            dir_entry = dirpath_p / name
            if dir_entry.is_symlink():
                if is_within_root(root, dir_entry):
                    # A symlink that stays inside the root is skipped (never
                    # followed) so indexing stays deterministic and duplicate
                    # free across filesystem layouts.
                    symlink_dirs_skipped += 1
                else:
                    _quarantine(
                        quarantine_dir,
                        dir_entry.relative_to(root).as_posix(),
                        "symlink directory escapes corpus root",
                        None,
                        None,
                        quarantined,
                    )
                continue
            kept_dirs.append(name)
        dirnames[:] = kept_dirs

        for name in sorted(filenames):
            file_path = dirpath_p / name
            rel = file_path.relative_to(root)
            rel_str = rel.as_posix()
            if ".." in rel.parts:
                _quarantine(quarantine_dir, rel_str, "path traversal", None, None, quarantined)
                continue
            if file_path.is_symlink() and not is_within_root(root, file_path):
                _quarantine(
                    quarantine_dir,
                    rel_str,
                    "symlink escapes corpus root",
                    None,
                    None,
                    quarantined,
                )
                continue
            try:
                data = file_path.read_bytes()
            except OSError as exc:
                _quarantine(
                    quarantine_dir,
                    rel_str,
                    f"unreadable: {exc.__class__.__name__}",
                    None,
                    None,
                    quarantined,
                )
                continue
            digest = sha256_bytes(data)
            kind = detect_kind(file_path)
            if kind == "unknown":
                skipped.append(
                    {"rel_path": rel_str, "kind": kind, "reason": "unsupported file type"}
                )
                continue
            if kind in ("text", "markdown"):
                try:
                    data.decode("utf-8")
                except UnicodeDecodeError:
                    _quarantine(
                        quarantine_dir,
                        rel_str,
                        "not valid UTF-8 text",
                        digest,
                        data,
                        quarantined,
                    )
                    continue
            files.append(
                {
                    "rel_path": rel_str,
                    "sha256": digest,
                    "size": len(data),
                    "kind": kind,
                    "status": "ok",
                    "duplicate_of": None,
                }
            )

    files.sort(key=lambda f: f["rel_path"])
    by_hash: dict[str, list[dict]] = {}
    for file_entry in files:
        by_hash.setdefault(file_entry["sha256"], []).append(file_entry)

    duplicates: list[dict] = []
    for digest in sorted(by_hash):
        group = by_hash[digest]  # already sorted by rel_path
        canonical = group[0]
        for dup in group[1:]:
            dup["status"] = "duplicate"
            dup["duplicate_of"] = canonical["rel_path"]
        if len(group) > 1:
            duplicates.append(
                {
                    "sha256": digest,
                    "canonical": canonical["rel_path"],
                    "duplicates": [d["rel_path"] for d in group[1:]],
                }
            )

    quarantined.sort(key=lambda q: q["rel_path"])
    skipped.sort(key=lambda s: s["rel_path"])

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "corpus_root": str(root),
        "files": files,
        "quarantine": quarantined,
        "skipped": skipped,
        "duplicates": duplicates,
        "summary": {
            "files": len(files),
            "unique": len(by_hash),
            "duplicates": sum(len(d["duplicates"]) for d in duplicates),
            "quarantined": len(quarantined),
            "skipped": len(skipped),
            "symlink_dirs_skipped": symlink_dirs_skipped,
        },
    }
    _write_quarantine_log(quarantine_dir, quarantined)
    return manifest


def _write_quarantine_log(quarantine_dir: Path, records: list[dict]) -> None:
    log_path = quarantine_dir / "quarantine.log"
    lines = [f"{r['rel_path']}\t{r['reason']}\t{r['sha256'] or ''}" for r in records]
    log_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def write_manifest(manifest: dict, out_path: Path) -> None:
    """Write a manifest dict as sorted JSON to *out_path*."""
    out_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_manifest(path: Path) -> dict:
    """Read a manifest JSON file back into a dict."""
    return json.loads(path.read_text(encoding="utf-8"))


def manifest_sha256(path: Path) -> str:
    """Return the SHA-256 of the manifest file bytes."""
    return sha256_file(path)
