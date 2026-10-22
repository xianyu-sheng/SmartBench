"""Tests for the generated data-contributor leaderboard."""

import json
from pathlib import Path

from tools.build_contributors import render

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "trajectories" / "v0" / "consent.json"
OUTPUT = ROOT / "trajectories" / "CONTRIBUTORS.md"


def test_committed_leaderboard_is_current():
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    assert OUTPUT.read_text(encoding="utf-8") == render(ledger)
    assert "No third-party data contributors" in OUTPUT.read_text(encoding="utf-8")


def test_granted_contributor_is_rendered_after_consent():
    ledger = {
        "cases": [
            {
                "id": "case-1",
                "repository": "owner/repo",
                "status": "granted",
                "granted_at": "2026-10-23",
                "trajectory_ids": ["abc12345"],
                "display_name": "Repo Maintainer",
                "contribution": "Audit trajectory + repro",
            }
        ]
    }

    markdown = render(ledger)

    assert "[Repo Maintainer](https://github.com/owner/repo)" in markdown
    assert "`abc12345`" in markdown
    assert "Audit trajectory + repro" in markdown
    assert "2026-10-23" in markdown
