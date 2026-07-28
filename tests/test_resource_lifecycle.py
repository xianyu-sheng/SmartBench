"""Generic resource protocol learning and deterministic path verification."""

from pathlib import Path

import pytest

from smartbench.analysis import ResourceLifecycleAnalyzer, ResourceProtocolMiner
from smartbench.core.adapters import GoAdapter
from smartbench.graph.tree_parser import get_parser

pytestmark = pytest.mark.skipif(get_parser("go") is None, reason="tree-sitter Go unavailable")


SAFE = """
package sample

func load(path string) error {
    file, err := os.Open(path)
    if err != nil {
        return err
    }
    defer file.Close()
    return parse(file)
}
""".strip()


LEAK = SAFE.replace("    defer file.Close()\n", "")


CONDITIONAL_CLEANUP = """
package sample

func load(path string, cleanup bool) error {
    file, err := os.Open(path)
    if err != nil {
        return err
    }
    if cleanup {
        defer file.Close()
    }
    return parse(file)
}
""".strip()


def _parse(tmp_path: Path, source: str):
    (tmp_path / "loader.go").write_text(source, encoding="utf-8")
    return GoAdapter().parse_semantic_project(tmp_path)


def test_reference_protocol_generalizes_without_project_specific_action(tmp_path: Path):
    safe_ir = _parse(tmp_path, SAFE)
    protocols = ResourceProtocolMiner().learn(safe_ir)

    assert [(item.acquire_symbol, item.cleanup_methods) for item in protocols] == [
        ("os.Open", ("Close",))
    ]
    assert ResourceLifecycleAnalyzer().analyze(safe_ir, protocols).findings == []

    leak_ir = _parse(tmp_path, LEAK)
    result = ResourceLifecycleAnalyzer().analyze(leak_ir, protocols)
    assert len(result.findings) == 1
    assert result.findings[0].resource_binding == "file"
    assert result.findings[0].first_unprotected_use.value == "parse(file)"
    assert result.findings[0].to_fact().attributes["proof"] == (
        "cfg_dominance_between_acquire_and_use"
    )


def test_conditional_cleanup_does_not_hide_unprotected_path(tmp_path: Path):
    protocols = ResourceProtocolMiner().learn(_parse(tmp_path, SAFE))
    result = ResourceLifecycleAnalyzer().analyze(
        _parse(tmp_path, CONDITIONAL_CLEANUP), protocols
    )

    assert len(result.findings) == 1
    assert len(result.findings[0].cleanup_candidates) == 1


def test_returned_resource_transfers_ownership_instead_of_reporting(tmp_path: Path):
    protocols = ResourceProtocolMiner().learn(_parse(tmp_path, SAFE))
    source = """
package sample

func open(path string) (*File, error) {
    file, err := os.Open(path)
    return file, err
}
""".strip()
    result = ResourceLifecycleAnalyzer().analyze(_parse(tmp_path, source), protocols)

    assert result.findings == []
    assert result.abstentions == 1
    assert "ownership of file is transferred" in result.unknown_reasons[0]


def test_resource_used_then_returned_transfers_ownership(tmp_path: Path):
    protocols = ResourceProtocolMiner().learn(_parse(tmp_path, SAFE))
    source = """
package sample

func open(path string) (*File, error) {
    file, err := os.Open(path)
    inspect(file)
    return file, err
}
""".strip()
    result = ResourceLifecycleAnalyzer().analyze(_parse(tmp_path, source), protocols)

    assert result.findings == []
    assert result.abstentions == 1
    assert "ownership of file is transferred" in result.unknown_reasons[0]
