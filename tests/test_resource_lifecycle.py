"""Generic resource protocol learning and deterministic path verification."""

from pathlib import Path

import pytest

from smartbench.analysis import (
    AcquireMatchMode,
    ResourceLifecycleAnalyzer,
    ResourceProtocolMiner,
)
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


def test_method_shape_generalizes_receiver_name_with_member_evidence(tmp_path: Path):
    reference = """
package sample

func load(client *Client, req *Request) error {
    response, err := client.Do(req)
    if err != nil { return err }
    defer response.Body.Close()
    return decode(response.Body)
}
""".strip()
    target = reference.replace("client.Do", "transport.Do").replace(
        "    defer response.Body.Close()\n", ""
    )
    fixed = target.replace(
        "    return decode(response.Body)",
        "    defer response.Body.Close()\n    return decode(response.Body)",
    )

    reference_ir = _parse(tmp_path, reference)
    exact = ResourceProtocolMiner().learn(reference_ir)
    generalized = ResourceProtocolMiner().learn(
        reference_ir,
        generalize_method_shapes=True,
    )

    assert exact[0].acquire_match_mode == AcquireMatchMode.EXACT
    assert generalized[0].acquire_match_mode == AcquireMatchMode.METHOD_SHAPE
    assert generalized[0].resource_member_path == "Body"
    assert ResourceLifecycleAnalyzer().analyze(_parse(tmp_path, target), exact).findings == []
    assert len(
        ResourceLifecycleAnalyzer().analyze(
            _parse(tmp_path, target), generalized
        ).findings
    ) == 1
    assert (
        ResourceLifecycleAnalyzer().analyze(
            _parse(tmp_path, fixed), generalized
        ).findings
        == []
    )


def test_method_shape_abstains_without_matching_resource_member(tmp_path: Path):
    reference = """
package sample

func load(client *Client, req *Request) error {
    response, err := client.Do(req)
    if err != nil { return err }
    defer response.Body.Close()
    return decode(response.Body)
}
""".strip()
    generalized = ResourceProtocolMiner().learn(
        _parse(tmp_path, reference),
        generalize_method_shapes=True,
    )
    unrelated = """
package sample

func calculate(worker *Worker, req *Request) error {
    result, err := worker.Do(req)
    if err != nil { return err }
    return consume(result.Payload)
}
""".strip()

    result = ResourceLifecycleAnalyzer().analyze(
        _parse(tmp_path, unrelated), generalized
    )

    assert result.findings == []
    assert result.abstentions == 1
    assert "no reachable resource use" in result.unknown_reasons[0]
