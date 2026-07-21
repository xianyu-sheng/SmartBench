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
