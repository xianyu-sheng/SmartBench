"""Tests for the single-file HTML report with evidence anchors (issue #22)."""

from __future__ import annotations

import importlib.util
import re
from datetime import datetime, timezone
from pathlib import Path

from smartbench.core.rules.base import Finding, Location, Severity
from smartbench.reporting import HTMLReportOptions, render_html_report, write_html_report

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_SCRIPT = REPO_ROOT / "scripts" / "generate_sample_report.py"
SAMPLE_REPORT = REPO_ROOT / "docs" / "sample_report.html"

EVIDENCE_HREF = re.compile(r'href="#(evidence-[0-9a-f]{12})"')
EVIDENCE_ID = re.compile(r'id="(evidence-[0-9a-f]{12})"')


def _options(
    root: Path | None = None,
    include_source: bool = True,
) -> HTMLReportOptions:
    return HTMLReportOptions(
        title="Test report",
        project_root=root,
        generated_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        include_source=include_source,
    )


def _finding(
    message: str = "boom",
    path: str = "src/app.py",
    line: int = 12,
    end: int = 12,
    evidence: tuple[Location, ...] = (),
    severity: Severity = Severity.ERROR,
    rule_id: str = "rule-1",
    metadata: dict | None = None,
) -> Finding:
    return Finding(
        rule_id=rule_id,
        rule_name="Rule One",
        severity=severity,
        location=Location(file_path=path, line_start=line, line_end=end),
        message=message,
        evidence=list(evidence),
        confidence=0.9,
        metadata=metadata or {},
    )


def test_evidence_links_resolve_to_embedded_anchors() -> None:
    html = render_html_report(
        [_finding(evidence=[Location("src/app.py", 20, 22)])],
        options=_options(),
    )

    hrefs = set(EVIDENCE_HREF.findall(html))
    anchors = set(EVIDENCE_ID.findall(html))

    assert hrefs
    assert hrefs <= anchors
    assert 'data-source-path="src/app.py"' in html
    assert 'data-line-start="20"' in html
    assert 'data-line-end="22"' in html


def test_unresolvable_evidence_degrades_to_plain_text() -> None:
    html = render_html_report(
        [
            _finding(
                evidence=[
                    Location("", 0, 0),
                    Location("/etc/passwd", 3, 3),
                    Location("src/app.py", 0, 0),
                ]
            )
        ],
        options=_options(),
    )

    # Count spans, not class-name mentions in the stylesheet.
    assert html.count('class="ev-ref ev-ref--unresolved"') == 3
    assert 'data-reason="missing-path"' in html
    assert 'data-reason="outside-project"' in html
    assert 'data-reason="no-line"' in html
    # Only the finding's own (resolvable) location becomes an anchor:
    # one reference in the finding body plus the section permalink.
    assert len(EVIDENCE_HREF.findall(html)) == 2


def test_report_is_a_single_self_contained_file() -> None:
    html = render_html_report(
        [_finding(evidence=[Location("src/app.py", 12, 14)])],
        options=_options(),
    )

    assert html.startswith("<!DOCTYPE html>")
    assert "<script" not in html.lower()
    assert "<link" not in html.lower()
    assert ' src="' not in html.lower()
    assert "http://" not in html.lower()
    assert "https://" not in html.lower()


def test_untrusted_text_is_escaped() -> None:
    html = render_html_report(
        [
            _finding(
                message='<script>alert("x")</script>',
                path="src/<img src=x>.py",
            )
        ],
        options=_options(),
    )

    assert "<script>alert" not in html
    assert "&lt;script&gt;alert" in html
    assert "<img src=x>" not in html


def test_identical_evidence_is_rendered_once() -> None:
    html = render_html_report(
        [
            _finding(evidence=[Location("src/app.py", 12, 12)]),
            _finding(rule_id="rule-2", evidence=[Location("src/app.py", 12, 12)]),
        ],
        options=_options(),
    )

    # One deduplicated evidence block...
    assert len(EVIDENCE_ID.findall(html)) == 1
    # ...but 2 finding locations + 2 evidence refs + 1 section permalink.
    assert len(EVIDENCE_HREF.findall(html)) == 5


def test_output_is_deterministic() -> None:
    first = render_html_report(
        [_finding(evidence=[Location("src/app.py", 20, 21)])],
        options=_options(),
    )
    second = render_html_report(
        [_finding(evidence=[Location("src/app.py", 20, 21)])],
        options=_options(),
    )

    assert first == second


def test_source_snippet_is_embedded_with_line_numbers(tmp_path: Path) -> None:
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text("first\nsecond\nthird\nfourth\n", encoding="utf-8")

    html = render_html_report(
        [
            _finding(
                path=str(source),
                line=1,
                end=1,
                evidence=[Location("src/app.py", 2, 3)],
            )
        ],
        options=_options(tmp_path),
    )

    # The finding's own location is line 1; select the evidence ref instead.
    match = re.search(
        r'href="#(evidence-[0-9a-f]{12})" data-source-path="src/app\.py"'
        r' data-line-start="2"',
        html,
    )
    assert match is not None
    anchor = match.group(1)
    assert 'id="{}"'.format(anchor) in html
    assert 'id="{}-L2"'.format(anchor) in html
    assert 'id="{}-L3"'.format(anchor) in html
    assert "second" in html
    assert "third" in html


def test_missing_source_file_keeps_anchor_but_marks_snippet(tmp_path: Path) -> None:
    html = render_html_report(
        [_finding(path="src/missing.py", line=5, end=5)],
        options=_options(tmp_path),
    )

    assert EVIDENCE_HREF.findall(html)
    assert "snippet-missing" in html


def test_windows_paths_are_normalised() -> None:
    html = render_html_report(
        [_finding(path="src\\app.py", line=4, end=4)],
        options=_options(),
    )

    assert 'data-source-path="src/app.py"' in html
    assert "src\\app.py" not in html


def test_fact_links_are_rendered_and_resolvable() -> None:
    html = render_html_report(
        [_finding(metadata={"evidence_fact_ids": ["fact-abc123"]})],
        options=_options(),
        facts=[
            {
                "fact_id": "fact-abc123",
                "statement": "channel is never closed",
                "evidence": [Location("src/app.py", 12, 12)],
            }
        ],
    )

    assert 'id="fact-abc123"' in html
    assert 'href="#fact-abc123"' in html
    assert 'class="fact-ref"' in html


def test_unknown_fact_id_stays_plain_text() -> None:
    html = render_html_report(
        [_finding(metadata={"evidence_fact_ids": ["fact-missing"]})],
        options=_options(),
    )

    assert "fact-ref--unlinked" in html
    assert 'href="#fact-missing"' not in html


def test_accepts_serialized_finding_dicts() -> None:
    payload = {
        "rule_id": "rule-dict",
        "rule_name": "Dict rule",
        "severity": "warning",
        "location": {"file_path": "src/app.py", "line_start": 3, "line_end": 4},
        "message": "dict finding",
        "evidence": [{"file_path": "src/app.py", "line_start": 3, "line_end": 4}],
        "confidence": 0.5,
        "metadata": {},
    }

    html = render_html_report([payload], options=_options())

    assert "badge--warning" in html
    assert "dict finding" in html
    assert EVIDENCE_HREF.findall(html)


def test_empty_report_is_still_valid() -> None:
    html = render_html_report([], options=_options())

    assert "No findings recorded." in html
    assert "No resolvable evidence locations." in html


def test_write_html_report_creates_parent_directories(tmp_path: Path) -> None:
    target = write_html_report(
        tmp_path / "out" / "nested" / "report.html",
        [_finding()],
        options=_options(),
    )

    assert target.exists()
    assert target.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_committed_sample_report_is_current() -> None:
    spec = importlib.util.spec_from_file_location(
        "generate_sample_report", SAMPLE_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert SAMPLE_REPORT.exists()
    assert SAMPLE_REPORT.read_text(encoding="utf-8") == module.render_sample_html()
