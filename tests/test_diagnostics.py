"""Safety and execution-contract tests for local diagnostic tools."""

import subprocess
import sys
from pathlib import Path

from smartbench.detector.fingerprint import Language
from smartbench.diagnostics.registry import (
    DiagnosticRegistry,
    ProblemCategory,
    infer_problem_category,
)
from smartbench.diagnostics.tools import (
    GoPProfTool,
    ProcessTool,
    PythonDiagTool,
    StaticAnalysisTool,
)


def test_problem_category_inference_is_deterministic():
    assert infer_problem_category(["服务经常发生死锁"]) == ProblemCategory.DEADLOCK
    assert infer_problem_category(["possible SQL injection"]) == ProblemCategory.SECURITY
    assert infer_problem_category(["response is slow"]) == ProblemCategory.PERFORMANCE
    assert infer_problem_category(None) == ProblemCategory.CODE_QUALITY
    assert infer_problem_category(None, performance=True) == ProblemCategory.PERFORMANCE


def test_unknown_category_defaults_to_code_quality_and_preserves_language():
    registry = DiagnosticRegistry()
    registry.register(StaticAnalysisTool())

    results = registry.diagnose(
        Language.GO,
        ProblemCategory.UNKNOWN,
        ".",
        symptoms=["general review"],
    )

    assert len(results) == 1
    commands = [item["command"] for item in results[0].suggestions]
    assert "go vet ./..." in commands
    assert all("ruff" not in command for command in commands)


def test_system_probes_require_explicit_opt_in(monkeypatch):
    registry = DiagnosticRegistry()
    probe = ProcessTool()
    registry.register(probe)
    monkeypatch.setattr(probe, "is_available", lambda: True)

    default_tools = registry.find_tools(Language.PYTHON, ProblemCategory.PERFORMANCE)
    opted_in = registry.find_tools(
        Language.PYTHON,
        ProblemCategory.PERFORMANCE,
        include_system=True,
    )

    assert default_tools == []
    assert opted_in == [probe]


def test_command_runner_rejects_shell_strings(tmp_path: Path):
    marker = tmp_path / "should-not-exist"
    tool = PythonDiagTool()

    result = tool._run_command(f"touch {marker}")

    assert result.returncode == -1
    assert "argument list" in result.stderr
    assert not marker.exists()


def test_command_runner_treats_metacharacters_as_plain_arguments(tmp_path: Path):
    marker = tmp_path / "should-not-exist"
    payload = f"value; touch {marker}"
    result = PythonDiagTool()._run_command(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", payload]
    )

    assert result.returncode == 0
    assert result.stdout.strip() == payload
    assert not marker.exists()


def test_python_startup_diagnostic_parses_project_without_executing_it(
    tmp_path: Path
):
    project = tmp_path / "syntax-project"
    project.mkdir()
    (project / "broken.py").write_text("def broken(:\n    pass\n")
    marker = project / "executed"
    (project / "dangerous.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )

    result = PythonDiagTool().diagnose(
        str(project), ProblemCategory.STARTUP_FAILURE
    )

    assert result.success is True
    assert result.confidence == 1.0
    assert "broken.py:1" in result.evidence
    assert not marker.exists()


def test_python_dependency_diagnostic_does_not_check_host_environment(
    monkeypatch, tmp_path: Path
):
    tool = PythonDiagTool()

    def fail_if_called(*args, **kwargs):
        raise AssertionError("no external command should run")

    monkeypatch.setattr(tool, "_run_command", fail_if_called)
    result = tool.diagnose(str(tmp_path), ProblemCategory.DEPENDENCY)

    assert result.success is True
    assert "cannot safely infer" in result.evidence
    assert result.suggestions[0]["command"] == "python -m pip check"


def test_go_diagnostic_passes_project_as_cwd(monkeypatch, tmp_path: Path):
    calls = []
    tool = GoPProfTool()

    def fake_run(command, timeout=30, cwd=None):
        calls.append((command, timeout, cwd))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(tool, "_run_command", fake_run)
    malicious_path = str(tmp_path / "project && touch injected")
    result = tool.diagnose(malicious_path, ProblemCategory.PERFORMANCE)

    assert result.success is True
    assert calls[0][0] == ["go", "version"]
    assert calls[1][0] == ["go", "build", "./..."]
    assert calls[1][2] == malicious_path


def test_go_diagnostic_preserves_build_failure(monkeypatch, tmp_path: Path):
    tool = GoPProfTool()
    responses = iter([
        subprocess.CompletedProcess(["go", "version"], 0, "go1.test", ""),
        subprocess.CompletedProcess(
            ["go", "build", "./..."], 1, "", "compile failed"
        ),
    ])
    monkeypatch.setattr(tool, "_run_command", lambda *args, **kwargs: next(responses))

    result = tool.diagnose(str(tmp_path), ProblemCategory.PERFORMANCE)

    assert result.success is False
    assert result.error == "compile failed"


def test_go_diagnostic_preserves_race_test_failure(monkeypatch, tmp_path: Path):
    tool = GoPProfTool()
    responses = iter([
        subprocess.CompletedProcess(["go", "version"], 0, "go1.test", ""),
        subprocess.CompletedProcess(
            ["go", "test", "-race", "./..."], 1, "", "tests failed"
        ),
    ])
    monkeypatch.setattr(tool, "_run_command", lambda *args, **kwargs: next(responses))

    result = tool.diagnose(str(tmp_path), ProblemCategory.CONCURRENCY)

    assert result.success is False
    assert result.error == "tests failed"


def test_go_diagnostic_detects_race_warning_from_stderr(monkeypatch, tmp_path: Path):
    tool = GoPProfTool()
    responses = iter([
        subprocess.CompletedProcess(["go", "version"], 0, "go1.test", ""),
        subprocess.CompletedProcess(
            ["go", "test", "-race", "./..."],
            1,
            "",
            "WARNING: DATA RACE\nstack trace",
        ),
    ])
    monkeypatch.setattr(tool, "_run_command", lambda *args, **kwargs: next(responses))

    result = tool.diagnose(str(tmp_path), ProblemCategory.CONCURRENCY)

    assert result.success is True
    assert result.severity.value == "critical"
    assert result.symptoms == ["Data race detected"]


def test_registry_isolates_invalid_plugin_return_value(monkeypatch):
    tool = StaticAnalysisTool()
    registry = DiagnosticRegistry()
    registry.register(tool)
    monkeypatch.setattr(tool, "diagnose", lambda *args, **kwargs: "not-a-result")

    results = registry.diagnose(
        Language.PYTHON, ProblemCategory.CODE_QUALITY, "."
    )

    assert len(results) == 1
    assert results[0].success is False
    assert "expected DiagnosisResult" in results[0].error
