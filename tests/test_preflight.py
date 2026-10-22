"""Unified pre-submission check tests."""

import json

from typer.testing import CliRunner

from smartbench.cli.main import app
from smartbench.frontends.git_branch_checker import BranchCheckResult
from smartbench.frontends.upstream_checker import (
    GitHubUpstreamChecker,
    UpstreamCheckResult,
    UpstreamMatch,
)

REPORT = {
    "findings": [
        {
            "rule_id": "retry_without_backoff",
            "location": {"file_path": "server.go", "line_start": 10},
            "metadata": {},
        },
        {
            "rule_id": "resource_leak",
            "location": {"file_path": "other.go", "line_start": 5},
            "metadata": {},
        },
        {
            "rule_id": "null_dereference",
            "location": {"file_path": "third.go", "line_start": 1},
            "metadata": {},
        },
    ]
}

MEMORY = """version: smartbench.intent/v1
entries:
  - path: "other.go"
    label: intentional
    reason: "the project documents this as fail-stop"
    source: "https://example.invalid/discussion"
"""


def _write_report(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(REPORT), encoding="utf-8")
    return path


def _preflight_args(report, memory, extra=None):
    args = [
        "preflight",
        "--input",
        str(report),
        "--memory-file",
        str(memory),
        "--json",
    ]
    return args + list(extra or [])


def test_preflight_verdicts(tmp_path, monkeypatch):
    from smartbench.frontends import git_branch_checker

    class FakeChecker:
        def __init__(self, repo_path):
            self.repo = repo_path

        def check_findings(self, findings):
            return [
                BranchCheckResult(
                    file_path="server.go",
                    line_start=10,
                    line_end=10,
                    already_fixed_on=["origin/dev"],
                )
            ]

    def fake_upstream_check(self, findings):
        return [
            UpstreamCheckResult(
                rule_id=str(finding.get("rule_id", "")),
                file_path=str(finding.get("file_path", "")),
                line_start=int(finding.get("line_start", 0) or 0),
                status="discussed" if index == 2 else "none",
                matches=(
                    [
                        UpstreamMatch(
                            url="https://example.invalid/pr/9",
                            title="retained trade-off",
                            state="closed",
                            is_pull_request=True,
                            intent_hint="trade-off",
                        )
                    ]
                    if index == 2
                    else []
                ),
            )
            for index, finding in enumerate(findings)
        ]

    monkeypatch.setattr(git_branch_checker, "GitBranchChecker", FakeChecker)
    monkeypatch.setattr(GitHubUpstreamChecker, "check", fake_upstream_check)

    report = _write_report(tmp_path)
    memory = tmp_path / "intent.yaml"
    memory.write_text(MEMORY, encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)

    result = CliRunner().invoke(
        app,
        _preflight_args(
            report,
            memory,
            ["--repo", str(repo), "--upstream", "owner/repo", "--no-record"],
        ),
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    verdicts = [row["verdict"] for row in payload["findings"]]
    assert verdicts == ["already_fixed", "declared", "discussed"]
    assert payload["summary"] == {
        "total": 3,
        "already_fixed": 1,
        "declared": 1,
        "discussed": 1,
    }


def test_preflight_submit_when_clean(tmp_path, monkeypatch):
    report = _write_report(tmp_path)
    memory = tmp_path / "intent.yaml"

    result = CliRunner().invoke(
        app,
        _preflight_args(report, memory, ["--no-branches"]),
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [row["verdict"] for row in payload["findings"]] == ["submit"] * 3
    assert payload["summary"]["submit"] == 3


def test_preflight_feedback_ranks_without_dropping(tmp_path):
    from smartbench.feedback import FeedbackStore, make_event

    report = _write_report(tmp_path)
    memory = tmp_path / "intent.yaml"
    events_path = tmp_path / "events.jsonl"
    store = FeedbackStore(events_path)
    for index in range(2):
        store.append(
            make_event(
                label="actionable",
                rule_id="retry_without_backoff",
                language="go",
                confidence=0.8,
                reason=f"case {index}",
            )
        )

    result = CliRunner().invoke(
        app,
        [
            "preflight",
            "--input",
            str(report),
            "--memory-file",
            str(memory),
            "--no-branches",
            "--no-record",
            "--feedback",
            "--feedback-file",
            str(events_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["findings"]) == 3
    assert payload["findings"][0]["rule_id"] == "retry_without_backoff"
    assert all("feedback" in row for row in payload["findings"])
    assert payload["findings"][0]["feedback"]["support"] == 2
    assert payload["summary"]["submit"] == 3
