"""Public library API contract."""

import json
from importlib.resources import files
from io import StringIO
from pathlib import Path

import jsonschema
from rich.console import Console

import smartbench
from smartbench.cli import phases
from smartbench.cli.unified import run_unified_diagnosis


def test_public_api_example_runs_verbatim(tmp_path):
    (tmp_path / "app.py").write_text("def ok():\n    return 1\n", encoding="utf-8")

    import smartbench

    session = smartbench.analyze(str(tmp_path))
    report = session.report()
    findings = report["findings"]

    assert isinstance(session, smartbench.AnalysisSession)
    assert isinstance(findings, list)


def test_public_exports_are_explicit():
    required = {"analyze", "AnalysisSession"}

    assert required <= set(smartbench.__all__)
    assert all(hasattr(smartbench, name) for name in required)


def test_public_report_has_a_versioned_envelope(tmp_path):
    session = smartbench.analyze(str(tmp_path))

    report = session.report()

    assert report["schema_version"] == "smartbench.report/v1"
    assert {key: value for key, value in report.items() if key != "schema_version"} == (
        session.report_dict()
    )


def test_real_public_report_matches_the_packaged_schema(tmp_path):
    (tmp_path / "leak.py").write_text(
        'stream = open("data.txt")\ndata = stream.read()\n',
        encoding="utf-8",
    )
    report = smartbench.analyze(str(tmp_path)).report()
    schema_resource = files("smartbench").joinpath("report-schema/v1.json")
    schema = json.loads(schema_resource.read_text(encoding="utf-8"))

    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(report)

    assert report["findings"]
    assert report["schema_version"] == "smartbench.report/v1"


def test_unified_cli_uses_the_public_analysis_api(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    calls = []
    sessions = []
    original = smartbench.analyze

    def tracked_analyze(*args, **kwargs):
        calls.append(args[0])
        session = original(*args, **kwargs)
        sessions.append(session)
        return session

    monkeypatch.setattr(smartbench, "analyze", tracked_analyze)
    output = tmp_path / "report.json"

    run_unified_diagnosis(
        Console(file=StringIO()),
        project=str(tmp_path),
        output=str(output),
    )

    assert len(calls) == 1
    assert Path(calls[0]) == tmp_path
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload == {
        **sessions[0].report_dict(),
        "project_path": str(tmp_path.resolve()),
    }
    assert "schema_version" not in payload


def test_quick_cli_uses_the_public_analysis_api(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    calls = []
    original = smartbench.analyze

    def tracked_analyze(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(smartbench, "analyze", tracked_analyze)
    monkeypatch.setattr(phases, "load_api_keys_from_env", lambda: None)

    phases.run_quick_mode(Console(file=StringIO()), project=str(tmp_path))

    assert len(calls) == 1
    assert Path(calls[0]) == tmp_path
