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


def test_mixed_language_diagnosis_combines_real_language_suggestions():
    registry = DiagnosticRegistry()
    registry.register(StaticAnalysisTool())

    results = registry.diagnose(
        Language.MIXED,
        ProblemCategory.CODE_QUALITY,
        ".",
        additional_languages=[Language.GO, Language.PYTHON, Language.GO],
    )

    commands = [item["command"] for item in results[0].suggestions]
    assert "go vet ./..." in commands
    assert "pip install ruff && ruff check ." in commands
    assert len(commands) == len(set(commands))


def test_mixed_language_tool_routing_uses_detected_languages(monkeypatch):
    registry = DiagnosticRegistry()
    go_tool = GoPProfTool()
    python_tool = PythonDiagTool()
    registry.register(go_tool)
    registry.register(python_tool)
    monkeypatch.setattr(go_tool, "is_available", lambda: True)

    tools = registry.find_tools(
        Language.MIXED,
        ProblemCategory.PERFORMANCE,
        additional_languages=[Language.GO, Language.PYTHON],
    )

    assert tools == [go_tool, python_tool]


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


def test_command_runner_bounds_noisy_process_output():
    result = PythonDiagTool()._run_command(
        [
            sys.executable,
            "-c",
            "print('A' * 400000); print('OUTPUT-END')",
        ]
    )

    assert result.returncode == 0
    assert len(result.stdout.encode()) < 300000
    assert "output bytes omitted" in result.stdout
    assert result.stdout.rstrip().endswith("OUTPUT-END")


def test_command_runner_preserves_bounded_output_on_timeout():
    result = PythonDiagTool()._run_command(
        [
            sys.executable,
            "-c",
            "import time; print('started', flush=True); time.sleep(5)",
        ],
        timeout=0.5,
    )

    assert result.returncode == -1
    assert "started" in result.stdout
    assert "TIMEOUT" in result.stderr


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


def test_python_startup_diagnostic_prunes_dependency_trees(tmp_path: Path):
    (tmp_path / "main.py").write_text("print('ok')\n")
    dependency = tmp_path / "node_modules" / "vendored"
    dependency.mkdir(parents=True)
    (dependency / "broken.py").write_text("def broken(:\n")

    result = PythonDiagTool().diagnose(
        str(tmp_path), ProblemCategory.STARTUP_FAILURE
    )

    assert result.success is True
    assert result.evidence == "Parsed 1 Python files without syntax errors"


def test_python_performance_diagnostic_uses_inferred_entry_point(tmp_path: Path):
    entry_point = tmp_path / "main.py"
    entry_point.write_text("print('ok')\n")

    result = PythonDiagTool().diagnose(
        str(tmp_path), ProblemCategory.PERFORMANCE
    )

    commands = [suggestion["command"] for suggestion in result.suggestions]
    assert all(str(entry_point) in command for command in commands)
    assert all(str(tmp_path) != command.rsplit(" ", 1)[-1] for command in commands)
    assert all("inferred" in item["description"] for item in result.suggestions)


def test_python_performance_diagnostic_marks_unknown_entry_point(tmp_path: Path):
    (tmp_path / "library.py").write_text("VALUE = 1\n")

    result = PythonDiagTool().diagnose(
        str(tmp_path), ProblemCategory.PERFORMANCE
    )

    assert all(
        "path/to/entry_script.py" in suggestion["command"]
        for suggestion in result.suggestions
    )
    assert all(
        "Replace path/to/entry_script.py" in suggestion["description"]
        for suggestion in result.suggestions
    )


def test_python_performance_diagnostic_quotes_entry_point(tmp_path: Path):
    project = tmp_path / "project; echo unsafe"
    project.mkdir()
    entry_point = project / "main.py"
    entry_point.write_text("print('ok')\n")

    result = PythonDiagTool().diagnose(
        str(project), ProblemCategory.PERFORMANCE
    )

    assert all(
        f"'{entry_point}'" in suggestion["command"]
        for suggestion in result.suggestions
    )


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
