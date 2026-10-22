"""Deterministic shard plans for large-repository review.

Shards are directory groups with a file cap. The plan is stable for the same
input (sorted files, sorted groups, deterministic ids) and does not assign a
file to two shards in v1. Import-graph clustering is deliberately out of scope:
the plan is a bounded, auditable partition, not a semantic proof.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List

SKIP_PARTS = {
    ".git",
    ".smartbench",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "target",
    "build",
    "dist",
}
DEFAULT_MAX_FILES_PER_SHARD = 40
DEFAULT_MAX_SHARDS = 20


@dataclass(frozen=True)
class Shard:
    shard_id: str
    files: tuple[str, ...]
    reason: str = "directory"

    def to_dict(self) -> dict[str, object]:
        return {
            "shard_id": self.shard_id,
            "files": list(self.files),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ShardPlan:
    shards: tuple[Shard, ...]
    total_files: int
    max_files_per_shard: int

    def to_dict(self) -> dict[str, object]:
        return {
            "shards": [shard.to_dict() for shard in self.shards],
            "total_files": self.total_files,
            "max_files_per_shard": self.max_files_per_shard,
        }


def build_shard_plan(
    files: Iterable[str],
    *,
    max_files_per_shard: int = DEFAULT_MAX_FILES_PER_SHARD,
    max_shards: int = DEFAULT_MAX_SHARDS,
) -> ShardPlan:
    """Partition files by top-level directory, with a bounded shard count."""
    max_files = max(1, int(max_files_per_shard))
    shard_cap = max(1, int(max_shards))
    normalized = sorted(
        {
            _normalize(path)
            for path in files
            if _normalize(path) and not _skipped(path)
        }
    )
    groups: dict[str, List[str]] = {}
    for path in normalized:
        groups.setdefault(_group_key(path), []).append(path)

    merged: dict[str, List[str]] = {}
    for group, group_files in groups.items():
        merged[group] = list(group_files)

    def chunk_count() -> int:
        return sum(
            math.ceil(len(group_files) / max_files)
            for group_files in merged.values()
        )

    while chunk_count() > shard_cap and len(merged) > 1:
        smallest = min(merged, key=lambda name: (len(merged[name]), name))
        carried = merged.pop(smallest)
        merged.setdefault("misc", []).extend(carried)

    shards: List[Shard] = []
    for group in sorted(merged):
        group_files = sorted(merged[group])
        chunks = [
            group_files[index : index + max_files]
            for index in range(0, len(group_files), max_files)
        ]
        for index, chunk in enumerate(chunks):
            shard_id = group if len(chunks) == 1 else f"{group}:{index + 1}"
            shards.append(
                Shard(
                    shard_id=shard_id,
                    files=tuple(chunk),
                    reason="merged-directory" if group == "misc" else "directory",
                )
            )

    return ShardPlan(
        shards=tuple(shards),
        total_files=len(normalized),
        max_files_per_shard=max_files,
    )


def _normalize(path: str) -> str:
    text = str(path or "").replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text


def _skipped(path: str) -> bool:
    parts = set(_normalize(path).split("/"))
    return bool(parts & SKIP_PARTS)


def _group_key(path: str) -> str:
    return path.split("/", 1)[0] if "/" in path else "root"
