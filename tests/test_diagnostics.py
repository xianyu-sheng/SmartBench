"""Safety and execution-contract tests for local diagnostic tools."""

import subprocess
import sys
from pathlib import Path

from smartbench.diagnostics.registry import ProblemCategory
from smartbench.diagnostics.tools import GoPProfTool, PythonDiagTool


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


def test_python_diagnostic_passes_project_as_cwd(monkeypatch, tmp_path: Path):
    captured = {}
    tool = PythonDiagTool()

    def fake_run(command, timeout=30, cwd=None):
        captured.update(command=command, timeout=timeout, cwd=cwd)
        return subprocess.CompletedProcess(command, 0, "syntax OK\n", "")

    monkeypatch.setattr(tool, "_run_command", fake_run)
    malicious_path = str(tmp_path / "project; touch injected")
    result = tool.diagnose(malicious_path, ProblemCategory.STARTUP_FAILURE)

    assert result.success is True
    assert isinstance(captured["command"], list)
    assert malicious_path not in captured["command"]
    assert captured["cwd"] == malicious_path


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
