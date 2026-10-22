"""Tests for audit trajectories: schema, redaction, store, export and replay."""

import json
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from smartbench.cli.main import app
from smartbench.trajectory import (
    TrajectoryStore,
    build_trajectory,
    redact_text,
    replay_document,
    validate_document,
)


def _session():
    finding = SimpleNamespace(
        rule_id="resource_leak",
        rule_name="Potential resource leak",
        location=SimpleNamespace(file_path="src/main.py", line_start=10),
        confidence=0.6,
        severity="warning",
    )
    return SimpleNamespace(
        result=SimpleNamespace(findings=[finding], errors=[]),
        fingerprint=SimpleNamespace(languages=["python"]),
    )


def _debate():
    return SimpleNamespace(
        final_suggestions=[
            {
                "rule_id": "resource_leak",
                "title": "leaked key sk-abcdefghijklmnopqrstuvwxyz012345",
                "location": {"file_path": "src/main.py", "line_start": 10},
                "confidence": 0.8,
            }
        ],
        debate_log=[{"role": "proposer", "round": 1, "output": "bounded"}],
        review_status="completed",
        iterations=2,
        duration_ms=120,
        consensus_reached=True,
        stage_statuses={"proposer": "ok", "critique": "ok"},
        analysis_report={},
    )


def _document(tmp_path: Path):
    return build_trajectory(
        project_path=tmp_path,
        tool_version="0.0.0-test",
        config={"concern": "audit", "trials": 2, "temperature": 0.3, "seed": 7},
        session=_session(),
        debate_result=_debate(),
        usage={"total_tokens": 42, "stable": False},
    )


def test_build_trajectory_is_valid_and_redacted(tmp_path):
    document = _document(tmp_path)

    assert validate_document(document) == []
    assert document["project"]["name"] == tmp_path.name
    assert document["findings"][0]["file_path"] == "src/main.py"
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in json.dumps(document)
    assert "[REDACTED]" in document["findings"][0]["title"]
    assert str(tmp_path) not in json.dumps(document)
    assert document["deterministic"]["findings"][0]["rule_id"] == "resource_leak"
    assert document["usage"]["total_tokens"] == 42


def test_redact_text_masks_secrets_and_home(tmp_path):
    text = f"{tmp_path}/src/main.py sk-abcdefghijklmnopqrstuvwxyz012345"

    redacted = redact_text(text, project_root=tmp_path)

    assert "<project>" in redacted
    assert "[REDACTED]" in redacted
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in redacted


def test_store_roundtrip_list_and_export(tmp_path):
    store = TrajectoryStore(tmp_path / "trajectories")
    document = _document(tmp_path)

    path = store.write(document)
    assert path is not None and path.is_file()
    entries = store.list()
    assert len(entries) == 1
    assert entries[0]["trajectory_id"] == document["trajectory_id"]
    assert store.load(document["trajectory_id"])["schema_version"] == document["schema_version"]

    output = tmp_path / "export.jsonl"
    assert store.export(output) == 1
    line = output.read_text(encoding="utf-8").strip()
    assert json.loads(line)["trajectory_id"] == document["trajectory_id"]
    manifest = json.loads(
        (tmp_path / "export.jsonl.manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["documents"] == 1
    assert manifest["trajectory_ids"] == [document["trajectory_id"]]


def test_store_write_failure_is_non_fatal(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    store = TrajectoryStore(blocker)

    assert store.write(_document(tmp_path)) is None


def test_replay_structural_check(tmp_path):
    document = _document(tmp_path)

    result = replay_document(document)

    assert result["ok"] is True
    assert result["reproduced"] == "structural"
    assert result["stored_deterministic_findings"] == 1
    assert result["llm"].startswith("skipped")


def test_trajectory_cli_list_and_export(tmp_path):
    store = TrajectoryStore(tmp_path / "trajectories")
    document = _document(tmp_path)
    store.write(document)
    directory = str(tmp_path / "trajectories")

    listed = CliRunner().invoke(app, ["trajectory", "list", "--dir", directory, "--json"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.output)[0]["trajectory_id"] == document["trajectory_id"]

    output = tmp_path / "cli-export.jsonl"
    exported = CliRunner().invoke(
        app,
        [
            "trajectory",
            "export",
            str(output),
            "--dir",
            directory,
        ],
    )
    assert exported.exit_code == 0, exported.output
    assert len(output.read_text(encoding="utf-8").splitlines()) == 1


def test_trajectory_replay_cli_missing_id(tmp_path):
    result = CliRunner().invoke(
        app,
        ["trajectory", "replay", "does-not-exist", "--dir", str(tmp_path)],
    )

    assert result.exit_code == 1


def test_unified_cli_records_trajectory(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text(
        "def read(path):\n    with open(path) as handle:\n        return handle.read()\n",
        encoding="utf-8",
    )
    directory = tmp_path / "trajectories"

    result = CliRunner().invoke(
        app,
        [
            "unified",
            "run",
            "--project",
            str(project),
            "--trajectory-dir",
            str(directory),
        ],
    )

    assert result.exit_code == 0, result.output
    entries = [
        json.loads(line)
        for line in (directory / "index.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(entries) == 1
    assert entries[0]["project"] == "project"


def test_unified_cli_can_disable_trajectory(tmp_path):
    project = tmp_path / "project_no_trajectory"
    project.mkdir()
    (project / "main.py").write_text(
        "def ok():\n    return 1\n",
        encoding="utf-8",
    )
    directory = tmp_path / "disabled"

    result = CliRunner().invoke(
        app,
        [
            "unified",
            "run",
            "--project",
            str(project),
            "--no-trajectory",
            "--trajectory-dir",
            str(directory),
        ],
    )

    assert result.exit_code == 0, result.output
    assert not (directory / "index.jsonl").exists()


def test_quick_cli_auto_saves_trajectory(tmp_path, monkeypatch):
    from smartbench.cli import phases

    monkeypatch.setattr(phases, "load_api_keys_from_env", lambda: None)
    project = tmp_path / "quick_project"
    project.mkdir()
    (project / "main.py").write_text(
        "def ok():\n    return 1\n",
        encoding="utf-8",
    )
    directory = tmp_path / "quick_trajectories"

    result = CliRunner().invoke(
        app,
        [
            "quick",
            "--project",
            str(project),
            "--trajectory-dir",
            str(directory),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (directory / "index.jsonl").is_file()


def test_quick_cli_env_can_disable_trajectory(tmp_path, monkeypatch):
    from smartbench.cli import phases

    monkeypatch.setattr(phases, "load_api_keys_from_env", lambda: None)
    monkeypatch.setenv("SMARTBENCH_TRAJECTORY", "0")
    project = tmp_path / "quick_disabled"
    project.mkdir()
    (project / "main.py").write_text(
        "def ok():\n    return 1\n",
        encoding="utf-8",
    )
    directory = tmp_path / "quick_disabled_trajectories"

    result = CliRunner().invoke(
        app,
        [
            "quick",
            "--project",
            str(project),
            "--trajectory-dir",
            str(directory),
        ],
    )

    assert result.exit_code == 0, result.output
    assert not (directory / "index.jsonl").exists()
