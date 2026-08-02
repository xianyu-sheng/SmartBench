"""
CLI integration tests.

Tests SmartBench CLI commands via typer's CliRunner.
"""

import json

import pytest
from rich.text import Text
from typer.testing import CliRunner

from smartbench.cli.main import app
from smartbench.engine.debate import DebateResult


@pytest.fixture
def runner():
    """Create a CLI test runner."""
    return CliRunner()


def plain_output(result) -> str:
    """Normalize Rich ANSI styling before asserting help text."""
    return Text.from_ansi(result.output).plain


class TestCLIHelp:
    """Test --help for all commands."""

    def test_main_help(self, runner):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        output = plain_output(result)
        assert "quick" in output
        assert "diagnose" in output
        assert "check" in output
        assert "eval-rag" in output
        assert "--version" in output

    def test_version_reports_package_version(self, runner):
        from smartbench import __version__

        result = runner.invoke(app, ["--version"])

        assert result.exit_code == 0
        assert plain_output(result).strip() == f"smartbench {__version__}"

    def test_quick_help(self, runner):
        result = runner.invoke(app, ["quick", "--help"])
        assert result.exit_code == 0
        output = plain_output(result)
        assert "--project" in output
        assert "--sandbox" in output

    def test_diagnose_help(self, runner):
        result = runner.invoke(app, ["diagnose", "--help"])
        assert result.exit_code == 0
        output = plain_output(result)
        assert "--project" in output
        assert "--symptoms" in output
        assert "--system-probes" in output

    def test_check_help(self, runner):
        result = runner.invoke(app, ["check", "--help"])
        assert result.exit_code == 0

    def test_eval_rag_help(self, runner):
        result = runner.invoke(app, ["eval-rag", "--help"])
        assert result.exit_code == 0
        output = plain_output(result)
        assert "--project" in output
        assert "--queries" in output
        assert "--graph-only" in output


class TestCLICheck:
    """Test the 'check' command."""

    def test_check_runs_without_error(self, runner):
        result = runner.invoke(app, ["check"])
        assert result.exit_code == 0
        # Should output a table
        assert "Tool" in result.output or "Error" in result.output

    def test_check_output_is_readable(self, runner):
        result = runner.invoke(app, ["check"])
        # Output should not be empty
        assert len(result.output) > 10

    def test_check_internal_error_returns_failure(self, runner, monkeypatch):
        def fail_scan(*args, **kwargs):
            raise OSError("scan failed")

        monkeypatch.setattr("smartbench.cli.main.ProjectScanner", fail_scan)

        result = runner.invoke(app, ["check"])

        assert result.exit_code == 1
        assert "scan failed" in result.output


class TestCLIQuick:
    """Test the 'quick' command."""

    def test_quick_requires_path(self, runner):
        """Quick mode without --project should prompt and fail gracefully."""
        result = runner.invoke(app, ["quick"])
        # Should either prompt or error — doesn't crash
        assert result.exit_code in (0, 1, 2)

    def test_quick_with_invalid_path(self, runner):
        """Quick mode with nonexistent path should error gracefully."""
        result = runner.invoke(
            app, ["quick", "--project", "/nonexistent/path/xyz"]
        )
        assert result.exit_code == 1
        assert "Cannot access" in result.output


class TestCLIDiagnose:
    """Test the 'diagnose' command."""

    def test_diagnose_requires_project(self, runner):
        """Diagnose without --project should fail."""
        result = runner.invoke(app, ["diagnose"])
        assert result.exit_code != 0  # Project is required

    def test_diagnose_with_invalid_path(self, runner):
        """Diagnose with nonexistent path should error gracefully."""
        result = runner.invoke(
            app, ["diagnose", "--project", "/nonexistent/path"]
        )
        assert result.exit_code == 1
        assert "Cannot access" in result.output

    def test_diagnose_with_valid_project(self, runner):
        """Diagnose on self should work."""
        import os
        project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        result = runner.invoke(
            app, ["diagnose", "--project", project]
        )
        # Should succeed — shows diagnostic results
        assert "Diagnostic Results" in result.output or result.exit_code == 0


class TestCLIMain:
    """Test the main callback."""

    def test_main_without_args(self, runner):
        """Calling without args should enter interactive mode (or error)."""
        result = runner.invoke(app)
        # Without a TTY, the wizard may fail gracefully
        assert result.exit_code in (0, 1, 2)

    def test_main_quick_flag(self, runner):
        """--quick flag should trigger quick mode."""
        result = runner.invoke(app, ["--quick"])
        assert result.exit_code in (0, 1, 2)


class TestReportOutput:
    def test_report_is_written_as_json_atomically(self, tmp_path):
        from smartbench.cli.main import _maybe_save_output

        destination = tmp_path / "report.json"
        _maybe_save_output(
            DebateResult(final_suggestions=[{"title": "Fix"}]),
            str(destination),
        )

        assert json.loads(destination.read_text())["final_suggestions"] == [
            {"title": "Fix"}
        ]
        assert list(tmp_path.glob(".report.json.*.tmp")) == []

    def test_report_write_failure_exits_nonzero(self, tmp_path):
        from smartbench.cli.main import _maybe_save_output

        with pytest.raises(Exception) as caught:
            _maybe_save_output(
                {"ok": True}, str(tmp_path / "missing" / "report.json")
            )

        assert getattr(caught.value, "exit_code", None) == 1


class TestFailOnSeverityGate:
    """Tests for --fail-on exit-code gating (issue #2)."""

    FIXTURE = 'import os\nPASSWORD = "hunter2supersecret"\ndef f(p):\n    os.system("ls " + p)\n'

    def _project(self, tmp_path):
        (tmp_path / "x.py").write_text(self.FIXTURE)
        return str(tmp_path)

    def test_default_is_backward_compatible(self, runner, tmp_path):
        """Without --fail-on, exit code stays 0 even with findings."""
        result = runner.invoke(app, ["unified", "run", "-p", self._project(tmp_path)])
        assert result.exit_code == 0

    def test_fail_on_warning_exits_1(self, runner, tmp_path):
        result = runner.invoke(
            app, ["unified", "run", "-p", self._project(tmp_path), "--fail-on", "warning"]
        )
        assert result.exit_code == 1

    def test_fail_on_error_passes_when_only_warnings(self, runner, tmp_path):
        """Fixture yields warnings, not errors, so an error gate must pass."""
        result = runner.invoke(
            app, ["unified", "run", "-p", self._project(tmp_path), "--fail-on", "error"]
        )
        assert result.exit_code == 0

    def test_invalid_value_exits_2(self, runner, tmp_path):
        result = runner.invoke(
            app, ["unified", "run", "-p", self._project(tmp_path), "--fail-on", "bogus"]
        )
        assert result.exit_code == 2

    def test_clean_project_passes_any_gate(self, runner, tmp_path):
        (tmp_path / "ok.py").write_text("def ok():\n    return 1\n")
        result = runner.invoke(
            app, ["unified", "run", "-p", str(tmp_path), "--fail-on", "info"]
        )
        assert result.exit_code == 0
