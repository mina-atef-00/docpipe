"""Path-safety guards for corpus walking.

The corpus walker must never read a file outside the corpus root. Symlinks and
relative traversal are the two ways a hostile or careless tree can smuggle in an
outside path, so both are resolved and checked against the root before any bytes
are read.
"""

from __future__ import annotations

from pathlib import Path


class UnsafePathError(Exception):
    """Raised when a path resolves outside the corpus root."""


def resolve_within_root(root: Path, candidate: Path) -> Path:
    """Resolve *candidate* and require it to sit inside the resolved *root*.

    Returns the resolved path. Raises :class:`UnsafePathError` when the resolved
    path is not a descendant of the resolved root, which covers both symlink
    escapes and ``..`` traversal that lands outside the tree.
    """
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    try:
        candidate_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise UnsafePathError(
            f"path escapes corpus root: {candidate} resolves to {candidate_resolved}"
        ) from exc
    return candidate_resolved


def is_within_root(root: Path, candidate: Path) -> bool:
    """Return True when *candidate* resolves inside the resolved *root*."""
    try:
        resolve_within_root(root, candidate)
    except UnsafePathError:
        return False
    return True
