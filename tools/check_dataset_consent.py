#!/usr/bin/env python3
"""Consent gate for published audit trajectories.

- ``check_dataset``: fails if a published real-world document lacks a granted
  consent with evidence, or the consent ledger is malformed.
- ``purge_declined``: deletes local trajectories for repositories marked
  ``declined`` and rebuilds the store index.

Run after any dataset change (CI does this in ``consent.yml``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = ROOT / "trajectories" / "v0"
CONSENT_PATH = DATASET_DIR / "consent.json"
DATASET_PATH = DATASET_DIR / "trajectories.jsonl"
CONSENT_SCHEMA = "smartbench.consent/v1"
STATUSES = {"pending", "granted", "declined"}


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read {path}: {exc}") from exc


def check_dataset(
    dataset_path: Path = DATASET_PATH,
    consent_path: Path = CONSENT_PATH,
) -> List[str]:
    errors: List[str] = []
    consent = load_json(consent_path)
    if not isinstance(consent, dict) or consent.get("schema_version") != CONSENT_SCHEMA:
        return [f"consent ledger must use schema {CONSENT_SCHEMA}"]
    cases = consent.get("cases")
    if not isinstance(cases, list):
        return ["consent ledger must contain a cases list"]
    by_id: Dict[str, Dict[str, Any]] = {}
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            errors.append(f"cases[{index}] must be an object")
            continue
        case_id = str(case.get("id") or "").strip()
        repository = str(case.get("repository") or "").strip()
        status = str(case.get("status") or "").strip()
        if not case_id or not repository:
            errors.append(f"cases[{index}] needs id and repository")
            continue
        if case_id in by_id:
            errors.append(f"duplicate consent id: {case_id}")
        if status not in STATUSES:
            errors.append(f"{case_id}: invalid status {status!r}")
        if status == "granted" and not str(case.get("evidence") or "").strip():
            errors.append(f"{case_id}: granted consent needs evidence")
        by_id[case_id] = case

    if dataset_path.is_file():
        for line_number, line in enumerate(
            dataset_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            try:
                document = json.loads(line)
            except json.JSONDecodeError:
                errors.append(f"dataset line {line_number}: invalid JSON")
                continue
            if not isinstance(document, dict):
                continue
            config = document.get("config") or {}
            if not isinstance(config, dict) or config.get("split") != "real_world":
                continue
            case_id = str(config.get("case_id") or "")
            case = by_id.get(case_id)
            if case is None:
                errors.append(f"published real-world case has no consent entry: {case_id}")
            elif case.get("status") != "granted":
                errors.append(
                    f"published real-world case {case_id} is not consented "
                    f"(status={case.get('status')})"
                )
            elif not str(case.get("evidence") or "").strip():
                errors.append(f"published real-world case {case_id} has no evidence link")
    return errors


def purge_declined(
    trajectory_dir: str | Path,
    consent_path: Path = CONSENT_PATH,
    *,
    dry_run: bool = False,
) -> List[str]:
    """Delete local trajectories for repositories marked ``declined``."""
    consent = load_json(consent_path)
    declined = {
        str(case.get("repository") or "")
        for case in consent.get("cases", [])
        if isinstance(case, dict) and case.get("status") == "declined"
    }
    declined_names = {name for name in declined if name}
    declined_basenames = {name.split("/")[-1] for name in declined_names}
    root = Path(trajectory_dir).expanduser()
    removed: List[str] = []
    if not root.is_dir():
        return removed
    for path in sorted(root.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        project = document.get("project") if isinstance(document, dict) else None
        name = str((project or {}).get("name") or "")
        if name in declined_names or name in declined_basenames:
            removed.append(name)
            if not dry_run:
                path.unlink()
    if not dry_run and (root / "index.jsonl").exists():
        entries = []
        for path in sorted(root.glob("*.json")):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            entries.append(
                {
                    "trajectory_id": document.get("trajectory_id", ""),
                    "recorded_at": document.get("recorded_at", ""),
                    "project": (document.get("project") or {}).get("name", ""),
                    "findings": len(document.get("findings") or []),
                    "schema_version": document.get("schema_version", ""),
                }
            )
        (root / "index.jsonl").write_text(
            "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries),
            encoding="utf-8",
        )
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=str(DATASET_PATH))
    parser.add_argument("--consent", default=str(CONSENT_PATH))
    parser.add_argument("--purge-declined", action="store_true")
    parser.add_argument("--trajectory-dir", default="")
    parser.add_argument("--yes", action="store_true", help="Actually delete files")
    args = parser.parse_args()

    errors = check_dataset(Path(args.dataset), Path(args.consent))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("consent check: ok")

    if args.purge_declined:
        if not args.trajectory_dir:
            print("ERROR: --purge-declined needs --trajectory-dir", file=sys.stderr)
            return 1
        removed = purge_declined(
            args.trajectory_dir,
            Path(args.consent),
            dry_run=not args.yes,
        )
        action = "removed" if args.yes else "would remove"
        print(f"{action} {len(removed)} local trajectory file(s)")
        for name in removed:
            print(f"- {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
