"""Terminal rendering must treat model and repository strings as plain text."""

from io import StringIO
from pathlib import Path

from rich.console import Console

from smartbench.cli.display import (
    display_diagnosis_results,
    display_fingerprint,
    show_debate_round,
)
from smartbench.detector.fingerprint import ProjectFingerprint
from smartbench.engine.debate import DebateResult
from smartbench.terminal import safe_terminal_text


def _console() -> tuple[Console, StringIO]:
    stream = StringIO()
    return Console(file=stream, color_system=None, width=120), stream


def test_raw_model_markup_is_rendered_literally():
    console, stream = _console()

    show_debate_round(
        console,
        "proposer",
        None,
        "[link=https://evil.example]spoofed link[/link]",
    )

    assert "[link=https://evil.example]spoofed link[/link]" in stream.getvalue()


def test_structured_model_markup_is_rendered_literally():
    console, stream = _console()
    response = {
        "analysis": {
            "root_cause": "[bold red]fake severity[/bold red]",
            "impact_assessment": "normal",
        },
        "proposals": [{
            "title": "[link=https://evil.example]click[/link]",
            "problem": "[conceal]hidden[/conceal]",
            "location": "src/[bold]fake.py[/bold]:1",
            "risk_level": "high",
        }],
    }

    show_debate_round(console, "proposer", response, "")

    output = stream.getvalue()
    assert "[bold red]fake severity[/bold red]" in output
    assert "[link=https://evil.example]click[/link]" in output
    assert "[conceal]hidden[/conceal]" in output


def test_repository_paths_and_final_report_markup_are_literal():
    console, stream = _console()
    fingerprint = ProjectFingerprint(
        project_path=Path("/tmp/project"),
        entry_points=["[bold red]main.py[/bold red]"],
        hot_files=["[link=https://evil.example]src.py[/link]"],
        has_readme=True,
        readme_path="[conceal]README.md[/conceal]",
        is_git_repo=True,
        git_remote_url="https://example.com/[bold]repo[/bold]",
    )
    result = DebateResult(final_suggestions=[{
        "title": "[bold red]spoof[/bold red]",
        "description": "[link=https://evil.example]description[/link]",
        "implementation": "[conceal]command[/conceal]",
        "priority": 5,
        "location": "[bold]main.py:1[/bold]",
    }])

    display_fingerprint(console, fingerprint)
    display_diagnosis_results(console, result, fingerprint)

    output = stream.getvalue()
    assert "[bold red]main.py[/bold red]" in output
    assert "[link=https://evil.example]src.py[/link]" in output
    assert "[bold red]spoof[/bold red]" in output
    assert "[conceal]command[/conceal]" in output


def test_incomplete_review_is_not_rendered_as_a_clean_result():
    console, stream = _console()
    fingerprint = ProjectFingerprint(project_path=Path("/tmp/project"))
    result = DebateResult(
        unreviewed_suggestions=[{"title": "hypothesis"}],
        unreviewed_source="proposer",
        review_status="partial",
    )

    display_diagnosis_results(console, result, fingerprint)

    output = stream.getvalue()
    assert "review incomplete" in output
    assert "not a clean result" in output
    assert "1 unreviewed proposer hypotheses" in output
    assert "No supported Agent findings" not in output


def test_complete_empty_review_reports_no_supported_agent_findings():
    console, stream = _console()
    fingerprint = ProjectFingerprint(project_path=Path("/tmp/project"))
    result = DebateResult(review_status="complete")

    display_diagnosis_results(console, result, fingerprint)

    output = stream.getvalue()
    assert "No supported Agent findings" in output
    assert "not a clean result" not in output


def test_terminal_control_sequences_are_removed():
    malicious = (
        "\x1b[31mred\x1b[0m "
        "\x1b]8;;https://evil.example\x1b\\click\x1b]8;;\x1b\\"
    )

    rendered = safe_terminal_text(malicious)

    assert rendered == "red click"
    assert "\x1b" not in rendered
    assert "evil.example" not in rendered


def test_malformed_verification_payload_does_not_break_rendering():
    console, stream = _console()

    show_debate_round(
        console,
        "verifier",
        {
            "type": "proposer_check",
            "proposals": [{
                "title": "unsafe [bold]title[/bold]",
                "__verification": {
                    "verdict": "verified",
                    "verification_score": "not-a-number",
                    "verified_locations": "main.py:1",
                },
            }],
        },
        "",
    )

    output = stream.getvalue()
    assert "unsafe [bold]title[/bold]" in output
    assert "得分: 0%" in output
