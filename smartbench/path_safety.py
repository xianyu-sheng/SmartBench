"""Filesystem boundary helpers for untrusted repository paths."""

from pathlib import Path
from typing import Optional, Union

PathLike = Union[str, Path]


def resolve_project_file(
    project_root: PathLike,
    relative_path: PathLike,
) -> Optional[Path]:
    """Resolve an existing regular file without escaping the project root.

    Absolute input paths and final-component symlinks are rejected. Parent
    symlinks are resolved and accepted only when their target remains inside
    the canonical project root.
    """
    root = Path(project_root).resolve()
    supplied = Path(relative_path)
    if supplied.is_absolute():
        return None

    candidate = root / supplied
    try:
        if candidate.is_symlink():
            return None
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def is_project_file(project_root: PathLike, candidate: PathLike) -> bool:
    """Return whether a discovered path is a regular file inside the root."""
    root = Path(project_root).resolve()
    path = Path(candidate)
    if not path.is_absolute():
        path = root / path
    try:
        if path.is_symlink():
            return False
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return False
    return resolved.is_file()


def read_text_bounded(
    file_path: PathLike,
    max_bytes: int = 2 * 1024 * 1024,
) -> Optional[str]:
    """Read a regular non-symlink file without exceeding a byte limit."""
    try:
        limit = int(max_bytes)
    except (TypeError, ValueError):
        return None
    if limit < 1:
        return None

    path = Path(file_path)
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > limit:
            return None
        with path.open("rb") as handle:
            payload = handle.read(limit + 1)
    except OSError:
        return None
    if len(payload) > limit:
        return None
    return payload.decode("utf-8", errors="ignore")


def read_text_prefix(
    file_path: PathLike,
    max_bytes: int = 64 * 1024,
) -> Optional[str]:
    """Read at most a byte prefix from a regular non-symlink file."""
    try:
        limit = int(max_bytes)
    except (TypeError, ValueError):
        return None
    if limit < 1:
        return None

    path = Path(file_path)
    try:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as handle:
            payload = handle.read(limit)
    except OSError:
        return None
    return payload.decode("utf-8", errors="ignore")
