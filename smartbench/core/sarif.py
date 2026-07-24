"""
SARIF output for SmartBench findings.

SARIF (Static Analysis Results Interchange Format) is a standard format
for output from static analysis tools.

Specification: https://docs.oasis-open.org/sarif/sarif/v2.1.0/
"""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from smartbench.core.rules.base import Finding, Severity

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"


def severity_to_sarif_level(severity: Severity) -> str:
    """Convert SmartBench severity to SARIF level."""
    mapping = {
        Severity.ERROR: "error",
        Severity.WARNING: "warning",
        Severity.INFO: "note",
    }
    return mapping.get(severity, "note")


def severity_to_sarif_rank(severity: Severity) -> float:
    """Convert SmartBench severity to SARIF rank (-1.0 to 5.0)."""
    mapping = {
        Severity.ERROR: 4.0,
        Severity.WARNING: 2.0,
        Severity.INFO: 0.5,
    }
    return mapping.get(severity, 0.5)


def finding_to_sarif_result(
    finding: Finding,
    rule_index: int,
    src_root: Path,
) -> Dict[str, Any]:
    """Convert a SmartBench Finding to a SARIF Result."""
    # Main location
    artifact_loc = {
        "uri": str(Path(finding.location.file_path).as_posix()),
        "uribaseId": "%SRCROOT%",
    }
    region = {
        "startLine": finding.location.line_start,
    }
    if finding.location.line_end:
        region["endLine"] = finding.location.line_end
    if finding.location.column_start:
        region["startColumn"] = finding.location.column_start
    if finding.location.column_end:
        region["endColumn"] = finding.location.column_end

    physical_loc = {
        "artifactLocation": artifact_loc,
        "region": region,
    }
    location = {
        "physicalLocation": physical_loc,
    }

    # Related locations (evidence)
    related_locations = []
    for evidence_loc in finding.evidence:
        evidence_artifact = {
            "uri": str(Path(evidence_loc.file_path).as_posix()),
            "uribaseId": "%SRCROOT%",
        }
        evidence_region = {
            "startLine": evidence_loc.line_start,
        }
        if evidence_loc.line_end:
            evidence_region["endLine"] = evidence_loc.line_end
        evidence_physical = {
            "artifactLocation": evidence_artifact,
            "region": evidence_region,
        }
        related_locations.append({
            "physicalLocation": evidence_physical,
        })

    # Properties
    properties = {
        "confidence": finding.confidence,
    }
    if finding.metadata:
        properties.update(finding.metadata)

    return {
        "ruleId": finding.rule_id,
        "ruleIndex": rule_index,
        "level": severity_to_sarif_level(finding.severity),
        "rank": severity_to_sarif_rank(finding.severity),
        "message": {
            "text": finding.message,
        },
        "locations": [location],
        "relatedLocations": related_locations,
        "properties": properties,
    }


def build_sarif_rules(
    unique_rule_ids: List[str],
    rule_registry=None,
) -> Dict[str, Any]:
    """Build SARIF rules dictionary from unique rule IDs."""
    rules = {}
    for rule_id in unique_rule_ids:
        description = rule_id.replace("_", " ").title()
        rules[rule_id] = {
            "id": rule_id,
            "shortDescription": {
                "text": description,
            },
            "fullDescription": {
                "text": description,
            },
        }
    return rules


def to_sarif_log(
    findings: List[Finding],
    project_path: Path,
    tool_name: str = "SmartBench",
    tool_version: str = "0.7.0",
    invocation_start: Optional[datetime] = None,
    invocation_end: Optional[datetime] = None,
    rule_registry=None,
) -> Dict[str, Any]:
    """
    Convert findings to a SARIF log.

    Args:
        findings: List of findings to include
        project_path: Root path of the project
        tool_name: Name of the tool
        tool_version: Version of the tool
        invocation_start: Start time of the analysis
        invocation_end: End time of the analysis
        rule_registry: Optional rule registry for rule metadata

    Returns:
        SARIF log as a dictionary
    """
    if not invocation_start:
        invocation_start = datetime.now()
    if not invocation_end:
        invocation_end = datetime.now()

    # Get unique rule IDs
    unique_rule_ids = list({f.rule_id for f in findings})

    # Convert findings to results
    results = []
    for finding in findings:
        rule_index = unique_rule_ids.index(finding.rule_id)
        result = finding_to_sarif_result(finding, rule_index, project_path)
        results.append(result)

    # Build tool structure
    tool = {
        "driver": {
            "name": tool_name,
            "version": tool_version,
            "informationUri": "https://github.com/xianyu-sheng/SmartBench",
            "rules": build_sarif_rules(unique_rule_ids, rule_registry),
        }
    }

    # Build invocation
    invocation = {
        "executionSuccessful": True,
        "startTimeUtc": invocation_start.isoformat() + "Z",
        "endTimeUtc": invocation_end.isoformat() + "Z",
    }

    # Build run
    run = {
        "tool": tool,
        "results": results,
        "invocations": [invocation],
    }

    # Build SARIF log
    sarif_log = {
        "version": SARIF_VERSION,
        "$schema": SARIF_SCHEMA,
        "runs": [run],
    }

    return sarif_log


def save_sarif_log(
    findings: List[Finding],
    project_path: Path,
    output_path: Path,
    tool_name: str = "SmartBench",
    tool_version: str = "0.7.0",
    rule_registry=None,
) -> Path:
    """
    Save findings to a SARIF file.

    Args:
        findings: List of findings to include
        project_path: Root path of the project
        output_path: Path to save the SARIF file
        tool_name: Name of the tool
        tool_version: Version of the tool
        rule_registry: Optional rule registry for rule metadata

    Returns:
        Path to the saved file
    """
    sarif_log = to_sarif_log(
        findings,
        project_path,
        tool_name=tool_name,
        tool_version=tool_version,
        rule_registry=rule_registry,
    )

    # Write atomically
    output_path = output_path.expanduser().absolute()
    parent = output_path.parent
    if not parent.is_dir():
        parent.mkdir(parents=True, exist_ok=True)

    temporary_path = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            dir=parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            text=True,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(sarif_log, handle, ensure_ascii=False, indent=2)
        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path:
            try:
                Path(temporary_path).unlink(missing_ok=True)
            except OSError:
                pass

    return output_path
