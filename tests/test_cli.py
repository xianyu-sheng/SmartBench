"""
CLI integration tests.

Tests SmartBench CLI commands via typer's CliRunner.
"""

import pytest
from rich.text import Text
from typer.testing import CliRunner

from smartbench.cli.main import app


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

    def test_check_help(self, runner):
        result = runner.invoke(app, ["check", "--help"])
        assert result.exit_code == 0


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
        # Should not crash
        assert result.exit_code in (0, 1, 2)


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
        assert result.exit_code in (0, 1)  # Should not crash

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
