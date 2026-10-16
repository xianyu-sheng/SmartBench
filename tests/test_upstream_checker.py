"""Upstream issue-history checker tests."""

import httpx
import pytest

from smartbench.frontends.upstream_checker import (
    GitHubUpstreamChecker,
    extract_findings,
)

FINDING = {
    "rule_id": "retry_without_backoff",
    "file_path": "server.go",
    "line_start": 10,
    "metadata": {"trigger_operation": "l.Accept"},
}


def _checker(handler, **kwargs) -> GitHubUpstreamChecker:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GitHubUpstreamChecker("owner/repo", client=client, min_interval=0.0, **kwargs)


def _response(items, remaining="9", status_code=200):
    return httpx.Response(
        status_code,
        json={"items": items},
        headers={"x-ratelimit-remaining": remaining},
    )


def test_query_uses_trigger_file_and_rule_terms():
    seen = []

    def handler(request):
        seen.append(request.url.params["q"])
        return _response([])

    result = _checker(handler).check([FINDING])[0]

    assert result.status == "none"
    assert seen == [
        "repo:owner/repo Accept server retry",
        "repo:owner/repo Accept",
    ]
    assert result.queries == seen
    assert result.query == "repo:owner/repo Accept"


def test_relaxed_query_finds_matches_when_precise_is_empty():
    seen = []

    def handler(request):
        query = request.url.params["q"]
        seen.append(query)
        if len(seen) == 1:
            return _response([])
        return _response(
            [
                {
                    "html_url": "https://github.com/owner/repo/issues/2",
                    "title": "accept loop spins",
                    "state": "open",
                    "body": "some body",
                }
            ]
        )

    result = _checker(handler).check([FINDING])[0]

    assert result.status == "mentioned"
    assert len(seen) == 2


def test_closed_item_with_intent_markers_is_discussed():
    def handler(request):
        return _response(
            [
                {
                    "html_url": "https://github.com/owner/repo/pull/1",
                    "title": "retry loop without backoff",
                    "state": "closed",
                    "body": "The maintainers decided this is by design — not planned.",
                    "pull_request": {"url": "x"},
                }
            ]
        )

    result = _checker(handler).check([FINDING])[0]

    assert result.status == "discussed"
    assert result.matches[0].intent_hint == "by design"
    assert result.matches[0].is_pull_request is True


def test_open_item_is_mentioned():
    def handler(request):
        return _response(
            [
                {
                    "html_url": "https://github.com/owner/repo/issues/2",
                    "title": "accept loop spins",
                    "state": "open",
                    "body": "some body",
                }
            ]
        )

    result = _checker(handler).check([FINDING])[0]

    assert result.status == "mentioned"
    assert result.matches[0].intent_hint == ""


def test_rate_limit_marks_the_current_and_remaining_findings():
    calls = []

    def handler(request):
        calls.append(request.url.params["q"])
        return httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={"x-ratelimit-remaining": "0"},
        )

    results = _checker(handler).check([FINDING, FINDING, FINDING])

    assert [result.status for result in results] == [
        "rate_limited",
        "rate_limited",
        "rate_limited",
    ]
    assert len(calls) == 1


def test_transport_error_is_reported():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    result = _checker(handler).check([FINDING])[0]

    assert result.status == "error"
    assert "request failed" in result.error


def test_non_github_shape_is_skipped():
    finding = {"rule_id": "", "file_path": "", "line_start": 0, "metadata": {}}
    result = _checker(lambda request: _response([])).check([finding])[0]

    assert result.status == "error"
    assert result.error == "no distinctive terms to search"


def test_extract_findings_handles_unified_and_quick_reports():
    unified = {
        "findings": [
            {
                "rule_id": "retry_without_backoff",
                "location": {"file_path": "a.go", "line_start": 3},
                "metadata": {"trigger_operation": "Accept"},
            }
        ]
    }
    quick = {
        "final_suggestions": [
            {
                "title": "x",
                "location": "b.go:7",
            }
        ]
    }

    assert extract_findings(unified)[0]["file_path"] == "a.go"
    assert extract_findings(unified)[0]["metadata"]["trigger_operation"] == "Accept"
    assert extract_findings(quick)[0]["file_path"] == "b.go"
    assert extract_findings(quick)[0]["line_start"] == 7
    assert extract_findings({"other": 1}) == []


def test_command_rejects_bad_repo_name():
    with pytest.raises(ValueError):
        GitHubUpstreamChecker("not-a-repo")


def test_cli_json_output_is_valid(tmp_path, monkeypatch):
    import json

    from typer.testing import CliRunner

    from smartbench.cli.main import app
    from smartbench.frontends.upstream_checker import UpstreamCheckResult

    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "findings": [
                    {
                        "rule_id": "retry_without_backoff",
                        "location": {"file_path": "server.go", "line_start": 10},
                        "metadata": {},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def fake_check(self, findings):
        return [
            UpstreamCheckResult(
                rule_id="retry_without_backoff",
                file_path="server.go",
                line_start=10,
                query="repo:owner/repo Accept",
                status="discussed",
            )
        ]

    monkeypatch.setattr(GitHubUpstreamChecker, "check", fake_check)
    result = CliRunner().invoke(
        app,
        [
            "check-upstream",
            "--input",
            str(report),
            "--repo",
            "owner/repo",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"] == {"total": 1, "discussed": 1}
