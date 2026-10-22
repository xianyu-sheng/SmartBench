"""Local, append-only store for audit trajectories.

The default location is ``~/.smartbench/trajectories`` (override with
``--trajectory-dir`` or ``SMARTBENCH_TRAJECTORY_DIR``). Each run is one JSON
document plus an index line; export concatenates documents into JSONL. A write
failure is logged and returns ``None`` — recording must never break an audit.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from smartbench.trajectory.schema import validate_document

logger = logging.getLogger(__name__)

INDEX_NAME = "index.jsonl"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def trajectory_enabled() -> bool:
    """Auto-recording is on unless ``SMARTBENCH_TRAJECTORY`` disables it."""
    raw = os.environ.get("SMARTBENCH_TRAJECTORY", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def default_directory() -> Path:
    override = os.environ.get("SMARTBENCH_TRAJECTORY_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".smartbench" / "trajectories"


class TrajectoryStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root).expanduser() if root else default_directory()

    def write(self, document: Dict[str, Any]) -> Optional[Path]:
        """Persist one trajectory; return the path or ``None`` on failure."""
        errors = validate_document(document)
        if errors:
            logger.warning("trajectory rejected: %s", "; ".join(errors))
            return None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / f"{document['trajectory_id']}.json"
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, path)
            entry = {
                "trajectory_id": document["trajectory_id"],
                "recorded_at": document.get("recorded_at", ""),
                "project": (document.get("project") or {}).get("name", ""),
                "findings": len(document.get("findings") or []),
                "schema_version": document.get("schema_version", ""),
            }
            with (self.root / INDEX_NAME).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return path
        except OSError as exc:
            logger.warning("trajectory write failed: %s", exc)
            return None

    def list(self) -> List[Dict[str, Any]]:
        index = self.root / INDEX_NAME
        if not index.is_file():
            return []
        entries: List[Dict[str, Any]] = []
        try:
            lines = index.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict) and entry.get("trajectory_id"):
                entries.append(entry)
        return sorted(entries, key=lambda item: str(item.get("recorded_at", "")), reverse=True)

    def load(self, trajectory_id: str) -> Optional[Dict[str, Any]]:
        if not _ID_RE.fullmatch(trajectory_id):
            return None
        path = self.root / f"{trajectory_id}.json"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return document if isinstance(document, dict) else None

    def iter_documents(self) -> Iterator[Dict[str, Any]]:
        for path in sorted(self.root.glob("*.json")):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(document, dict):
                yield document

    def export(self, output: str | Path, *, format: str = "jsonl") -> int:
        """Export every stored trajectory; returns the number written."""
        if format != "jsonl":
            raise ValueError("only the jsonl export format is supported")
        destination = Path(output).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        trajectory_ids: List[str] = []
        with destination.open("w", encoding="utf-8", newline="\n") as handle:
            for document in self.iter_documents():
                handle.write(
                    json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n"
                )
                trajectory_ids.append(str(document.get("trajectory_id", "")))
                count += 1
        manifest = {
            "schema_version": "smartbench.trajectory.export/v1",
            "documents": count,
            "trajectory_ids": trajectory_ids,
        }
        destination.with_name(destination.name + ".manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return count
