"""
Tests for SARIF output module.
"""

import json
import tempfile
from pathlib import Path

import pytest

from smartbench.core.rules.base import Finding, Location, Severity
from smartbench.core.sarif import (
    finding_to_sarif_result,
    save_sarif_log,
    to_sarif_log,
    severity_to_sarif_level,
    severity_to_sarif_rank,
)


def test_severity_to_sarif_level():
    assert severity_to_sarif_level(Severity.ERROR) == "error"
    assert severity_to_sarif_level(Severity.WARNING) == "warning"
    assert severity_to_sarif_level(Severity.INFO) == "note"


def test_severity_to_sarif_rank():
    assert severity_to_sarif_rank(Severity.ERROR) == 4.0
    assert severity_to_sarif_rank(Severity.WARNING) == 2.0
    assert severity_to_sarif_rank(Severity.INFO) == 0.5


def test_finding_to_sarif_result():
    finding = Finding(
        rule_id="test_rule",
        rule_name="Test Rule",
        severity=Severity.WARNING,
        location=Location(
            file_path="test.py",
            line_start=10,
            line_end=12,
        ),
        message="Test message",
        confidence=0.9,
        metadata={"key": "value"},
    )

    result = finding_to_sarif_result(finding, 0, Path("/tmp"))
    assert result["ruleId"] == "test_rule"
    assert result["ruleIndex"] == 0
    assert result["level"] == "warning"
    assert result["message"]["text"] == "Test message"
    assert result["rank"] == 2.0
    assert len(result["locations"]) == 1
    assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "test.py"
    assert result["locations"][0]["physicalLocation"]["region"]["startLine"] == 10
    assert result["locations"][0]["physicalLocation"]["region"]["endLine"] == 12


def test_to_sarif_log():
    findings = [
        Finding(
            rule_id="test_rule1",
            rule_name="Test Rule 1",
            severity=Severity.ERROR,
            location=Location(file_path="test.py", line_start=10),
            message="Error 1",
        ),
        Finding(
            rule_id="test_rule2",
            rule_name="Test Rule 2",
            severity=Severity.WARNING,
            location=Location(file_path="test.py", line_start=20),
            message="Warning 1",
        ),
    ]

    log = to_sarif_log(findings, Path("/tmp/project"))

    assert log["version"] == "2.1.0"
    assert "$schema" in log
    assert len(log["runs"]) == 1
    assert len(log["runs"][0]["results"]) == 2
    assert log["runs"][0]["tool"]["driver"]["name"] == "SmartBench"


def test_save_sarif_log():
    findings = [
        Finding(
            rule_id="test_rule",
            rule_name="Test Rule",
            severity=Severity.INFO,
            location=Location(file_path="test.py", line_start=10),
            message="Test message",
        ),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "results.sarif"
        project_path = Path(tmpdir)

        saved_path = save_sarif_log(findings, project_path, output_path)

        assert saved_path.exists()
        assert saved_path == output_path

        # Verify we can load it back
        with open(saved_path, "r") as f:
            data = json.load(f)
        assert data["version"] == "2.1.0"
        assert len(data["runs"][0]["results"]) == 1


def test_sarif_with_evidence():
    finding = Finding(
        rule_id="test_rule",
        rule_name="Test Rule",
        severity=Severity.WARNING,
        location=Location(file_path="test.py", line_start=10),
        message="Test message",
        evidence=[
            Location(file_path="test.py", line_start=5),
            Location(file_path="test.py", line_start=15),
        ],
    )

    result = finding_to_sarif_result(finding, 0, Path("/tmp"))
    assert len(result["relatedLocations"]) == 2
