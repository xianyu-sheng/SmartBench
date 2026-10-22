"""Local staging queue for third-party consent requests.

A scan never publishes a third-party trajectory. When a saved trajectory has
findings and the project has a GitHub ``origin``, the repository is added to a
local queue as ``staged``. Nothing in this file is committed or uploaded.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

QUEUE_SCHEMA = "smartbench.consent.queue/v1"
QUEUE_ENV = "SMARTBENCH_CONSENT_QUEUE"
SELF_REPOSITORIES = {"xianyu-sheng/smartbench"}

_GITHUB_URL = re.compile(
    r"^(?:git@github\.com:|https?://github\.com/|ssh://git@github\.com/)"
    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


def default_queue_path() -> Path:
    override = os.environ.get(QUEUE_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".smartbench" / "consent" / "outreach.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_github_repository(url: str) -> Optional[str]:
    """Return ``owner/repo`` for a GitHub remote URL, else ``None``."""
    match = _GITHUB_URL.match((url or "").strip())
    if not match:
        return None
    owner, repository = match.groups()
    return f"{owner}/{repository}"


def discover_github_repository(project_path: str | Path) -> Optional[str]:
    """Read ``remote.origin.url`` from a local git checkout."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(Path(project_path).expanduser()), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return parse_github_repository(completed.stdout)


def _empty_queue() -> Dict[str, Any]:
    return {"schema_version": QUEUE_SCHEMA, "requests": []}


def load_queue(path: str | Path | None = None) -> Dict[str, Any]:
    destination = Path(path).expanduser() if path else default_queue_path()
    if not destination.is_file():
        return _empty_queue()
    try:
        document = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_queue()
    if not isinstance(document, dict) or not isinstance(document.get("requests"), list):
        return _empty_queue()
    return document


def _write_queue(path: Path, document: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def stage(
    repository: str,
    trajectory_id: str = "",
    *,
    project_name: str = "",
    queue_path: str | Path | None = None,
) -> Dict[str, Any]:
    """Add or update a staged consent request for ``owner/repo``."""
    repository = (repository or "").strip()
    if repository.count("/") != 1:
        raise ValueError("repository must be owner/repo")
    path = Path(queue_path).expanduser() if queue_path else default_queue_path()
    document = load_queue(path)
    requests: List[Dict[str, Any]] = document["requests"]
    stamp = now_iso()
    entry = next(
        (
            item
            for item in requests
            if str(item.get("repository", "")).lower() == repository.lower()
        ),
        None,
    )
    if entry is None:
        entry = {
            "repository": repository,
            "project_name": project_name,
            "trajectory_ids": [],
            "status": "staged",
            "created_at": stamp,
            "updated_at": stamp,
            "requested_at": None,
            "contact_source": None,
            "message_id": None,
            "note": "",
        }
        requests.append(entry)
    if trajectory_id and trajectory_id not in entry["trajectory_ids"]:
        entry["trajectory_ids"].append(trajectory_id)
    entry["updated_at"] = stamp
    if project_name and not entry.get("project_name"):
        entry["project_name"] = project_name
    _write_queue(path, document)
    return entry


def update_request(
    repository: str,
    *,
    queue_path: str | Path | None = None,
    **changes: Any,
) -> Optional[Dict[str, Any]]:
    path = Path(queue_path).expanduser() if queue_path else default_queue_path()
    document = load_queue(path)
    entry = next(
        (
            item
            for item in document["requests"]
            if str(item.get("repository", "")).lower() == repository.lower()
        ),
        None,
    )
    if entry is None:
        return None
    entry.update(changes)
    entry["updated_at"] = now_iso()
    _write_queue(path, document)
    return entry


def stage_trajectory(
    document: Dict[str, Any],
    project_path: str | Path,
    *,
    queue_path: str | Path | None = None,
) -> Optional[Dict[str, Any]]:
    """Stage a saved trajectory if it is a third-party GitHub project with findings."""
    if not document.get("findings"):
        return None
    repository = discover_github_repository(project_path)
    if not repository or repository.lower() in SELF_REPOSITORIES:
        return None
    entry = stage(
        repository,
        str(document.get("trajectory_id") or ""),
        project_name=str((document.get("project") or {}).get("name") or ""),
        queue_path=queue_path,
    )
    if os.environ.get("SMARTBENCH_CONSENT_AUTOSEND", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        from smartbench.consent.email import send_pending

        send_pending(queue_path=queue_path, dry_run=False, limit=1)
    return entry
