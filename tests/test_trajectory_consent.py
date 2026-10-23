"""Consent gate tests for published audit trajectories."""

import json
from pathlib import Path

from tools.check_dataset_consent import (
    check_dataset,
    legacy_basename_candidates,
    purge_declined,
)

ROOT = Path(__file__).resolve().parent.parent
CONSENT = ROOT / "trajectories" / "v0" / "consent.json"


def test_committed_dataset_passes_consent_gate():
    assert check_dataset() == []


def _write_consent(path: Path, cases):
    path.write_text(
        json.dumps({"schema_version": "smartbench.consent/v1", "cases": cases}),
        encoding="utf-8",
    )


def test_unconsented_published_case_is_rejected(tmp_path):
    dataset = tmp_path / "data.jsonl"
    dataset.write_text(
        json.dumps({"config": {"split": "real_world", "case_id": "r1"}}) + "\n",
        encoding="utf-8",
    )
    consent = tmp_path / "consent.json"
    _write_consent(
        consent,
        [{"id": "r1", "repository": "o/r", "status": "pending"}],
    )

    errors = check_dataset(dataset, consent)

    assert any("not consented" in error for error in errors)


def test_granted_consent_requires_evidence(tmp_path):
    consent = tmp_path / "consent.json"
    _write_consent(
        consent,
        [{"id": "r1", "repository": "o/r", "status": "granted"}],
    )

    errors = check_dataset(tmp_path / "missing.jsonl", consent)

    assert any("needs evidence" in error for error in errors)


def test_purge_declined_does_not_delete_same_basename(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    for trajectory_id, repository in (
        ("a", "o/repo"),
        ("b", "other/repo"),
    ):
        (store / f"{trajectory_id}.json").write_text(
            json.dumps(
                {
                    "trajectory_id": trajectory_id,
                    "recorded_at": "2026-10-22",
                    "project": {"name": "repo", "repository": repository},
                    "findings": [],
                    "schema_version": "smartbench.trajectory/v1",
                }
            ),
            encoding="utf-8",
        )
    (store / "index.jsonl").write_text("stale\n", encoding="utf-8")
    consent = tmp_path / "consent.json"
    _write_consent(
        consent,
        [{"id": "r1", "repository": "o/repo", "status": "declined"}],
    )

    removed = purge_declined(store, consent)

    assert removed == ["repo"]
    assert not (store / "a.json").exists()
    assert (store / "b.json").exists()
    lines = (store / "index.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["trajectory_id"] == "b"


def test_legacy_document_is_not_deleted_without_explicit_flag(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    (store / "legacy.json").write_text(
        json.dumps(
            {
                "trajectory_id": "legacy",
                "project": {"name": "repo"},
                "findings": [],
            }
        ),
        encoding="utf-8",
    )
    consent = tmp_path / "consent.json"
    _write_consent(
        consent,
        [{"id": "r1", "repository": "o/repo", "status": "declined"}],
    )

    assert legacy_basename_candidates(store, consent) == ["legacy.json"]
    assert purge_declined(store, consent) == []
    assert (store / "legacy.json").exists()

    removed = purge_declined(store, consent, include_legacy_basename=True)

    assert removed == ["repo"]
    assert not (store / "legacy.json").exists()
