"""Regression tests for issue #27 item 2/3: docs must describe real behavior."""

from __future__ import annotations

from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs" / "USAGE_GUIDE.md"


def test_usage_guide_no_longer_references_deleted_legacy_directory():
    text = DOCS.read_text(encoding="utf-8")

    assert "`legacy/` 中的旧 Raft 压测命令" not in text
    assert "CHANGELOG.md" in text


def test_usage_guide_documents_exit_codes_for_ci_commands():
    text = DOCS.read_text(encoding="utf-8")

    assert "## 退出码与 CI 门禁" in text
    assert "smartbench check-branches" in text
    # The three CI-relevant commands and the `--fail-on` gate must be described.
    for command in ("`smartbench unified run`", "`smartbench quick`", "`smartbench check-branches`"):
        assert command in text
    assert "--fail-on" in text
