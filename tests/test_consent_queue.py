"""Tests for the local consent staging queue."""

import subprocess

from smartbench.consent.queue import (
    load_queue,
    parse_github_repository,
    stage,
    stage_trajectory,
)


def test_parse_github_repository_variants():
    assert parse_github_repository("git@github.com:owner/repo.git") == "owner/repo"
    assert parse_github_repository("https://github.com/owner/repo") == "owner/repo"
    assert parse_github_repository("https://github.com/owner/repo.git") == "owner/repo"
    assert parse_github_repository("ssh://git@github.com/owner/repo.git") == "owner/repo"
    assert parse_github_repository("https://gitlab.com/owner/repo") is None
    assert parse_github_repository("") is None


def test_stage_is_idempotent_and_keeps_ids(tmp_path):
    queue = tmp_path / "outreach.json"

    stage("owner/repo", "trajectory-a", queue_path=queue)
    entry = stage("owner/repo", "trajectory-b", queue_path=queue)

    assert entry["repository"] == "owner/repo"
    assert entry["trajectory_ids"] == ["trajectory-a", "trajectory-b"]
    assert len(load_queue(queue)["requests"]) == 1


def test_stage_trajectory_discovers_origin_and_skips_clean(tmp_path, monkeypatch):
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "remote",
            "add",
            "origin",
            "git@github.com:owner/repo.git",
        ],
        check=True,
    )
    queue = tmp_path / "outreach.json"
    monkeypatch.setenv("SMARTBENCH_CONSENT_QUEUE", str(queue))
    document = {
        "trajectory_id": "abc12345",
        "findings": [{"rule_id": "R1"}],
        "project": {"name": "repo"},
    }

    entry = stage_trajectory(document, repository)

    assert entry is not None
    assert entry["repository"] == "owner/repo"
    assert load_queue(queue)["requests"][0]["trajectory_ids"] == ["abc12345"]
    assert stage_trajectory({**document, "findings": []}, repository) is None
