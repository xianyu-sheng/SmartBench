"""Regenerate ``docs/sample_report.html`` (issue #22 review artefact).

Usage:
    python scripts/generate_sample_report.py           # write the sample
    python scripts/generate_sample_report.py --check   # verify it is current

The sample is deterministic: the timestamp is fixed and every evidence
location points at a stable benchmark fixture in this repository.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Sequence

# Allow ``python scripts/generate_sample_report.py`` from a source checkout
# without installing the package first.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smartbench.core.rules.base import Finding, Location, Severity  # noqa: E402
from smartbench.reporting import HTMLReportOptions, render_html_report  # noqa: E402

SAMPLE_PATH = REPO_ROOT / "docs" / "sample_report.html"
GENERATED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)

CHANNEL_FIXTURE = "benchmarks/real/channel_close_guard/before/main.go"
BUFIO_FIXTURE = "benchmarks/real/go_bufio_flush_guard/before/main.go"


def build_sample_findings() -> List[Finding]:
    """Build a small, deterministic finding set for visual review."""
    return [
        Finding(
            rule_id="go-channel-close",
            rule_name="Channel returned without close",
            severity=Severity.ERROR,
            location=Location(CHANNEL_FIXTURE, 8, 17),
            message=(
                "BatchSend returns a buffered channel that is never closed; "
                "any `for v := range ch` receiver blocks forever."
            ),
            evidence=[
                Location(CHANNEL_FIXTURE, 14, 16),
                Location(CHANNEL_FIXTURE, 21, 23),
            ],
            confidence=0.92,
            metadata={"evidence_fact_ids": ["fact-channel-close-guard"]},
        ),
        Finding(
            rule_id="go-bufio-flush",
            rule_name="Buffered writer never flushed",
            severity=Severity.WARNING,
            location=Location(BUFIO_FIXTURE, 8, 18),
            message="writeLog returns without calling w.Flush(), so buffered log lines are lost.",
            evidence=[Location(BUFIO_FIXTURE, 15, 17)],
            confidence=0.88,
        ),
        Finding(
            rule_id="js-heuristic-sink",
            rule_name="Unresolved heuristic claim",
            severity=Severity.INFO,
            location=Location("", 0, 0),
            message=(
                "A detector claim carried no file:line location; it degrades to "
                "plain text instead of a broken link."
            ),
            confidence=0.4,
        ),
    ]


def build_sample_facts() -> List[dict]:
    """Build the fact record referenced by the channel finding."""
    return [
        {
            "fact_id": "fact-channel-close-guard",
            "statement": "BatchSend produces a channel with no dominating close.",
            "evidence": [
                {
                    "file_path": CHANNEL_FIXTURE,
                    "line_start": 14,
                    "line_end": 16,
                }
            ],
        }
    ]


def render_sample_html() -> str:
    """Render the committed sample report body."""
    options = HTMLReportOptions(
        title="SmartBench diagnosis report - sample",
        project_root=REPO_ROOT,
        project_label="SmartBench",
        generated_at=GENERATED_AT,
    )
    return render_html_report(
        build_sample_findings(),
        options=options,
        facts=build_sample_facts(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Write or verify ``docs/sample_report.html``."""
    args = list(sys.argv[1:] if argv is None else argv)
    rendered = render_sample_html()
    if "--check" in args:
        current = SAMPLE_PATH.read_text(encoding="utf-8") if SAMPLE_PATH.exists() else ""
        if current != rendered:
            print(
                "docs/sample_report.html is out of date; "
                "run: python scripts/generate_sample_report.py"
            )
            return 1
        print("docs/sample_report.html is up to date")
        return 0
    SAMPLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SAMPLE_PATH.write_text(rendered, encoding="utf-8")
    print("wrote {}".format(SAMPLE_PATH))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
