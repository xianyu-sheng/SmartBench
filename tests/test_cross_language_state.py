"""The same invariant should run unchanged across semantic frontends."""

from pathlib import Path

import pytest

from smartbench.analysis import (
    InvariantKind,
    OperationSelector,
    StateInvariant,
    StateMachineAnalyzer,
)
from smartbench.core.adapters import GoAdapter, PythonAdapter
from smartbench.graph.tree_parser import get_parser
from smartbench.ir import OperationKind

GO_SOURCE = """
package sample

func run(output string) error {
    if !ready(output) {
        retries++
        continue
    }
    return nil
}
""".strip()


PYTHON_SOURCE = """
def run(output: str):
    if not ready(output):
        retries += 1
        continue_work()
    return None
""".strip()


def _invariant() -> StateInvariant:
    return StateInvariant(
        invariant_id="terminal-before-retry",
        kind=InvariantKind.REQUIRE_GUARD_BEFORE_ACTION,
        event=OperationSelector.of(OperationKind.BRANCH, contains_all=("ready",)),
        guard=OperationSelector.of(OperationKind.BRANCH, contains_all=("completed",)),
        action=OperationSelector.of(OperationKind.UPDATE, contains_all=("retries",)),
        message="retry requires a terminal-state guard",
    )


@pytest.mark.skipif(get_parser("go") is None, reason="tree-sitter Go unavailable")
def test_one_invariant_detects_equivalent_go_and_python_transitions(tmp_path: Path):
    go_root = tmp_path / "go"
    python_root = tmp_path / "python"
    go_root.mkdir()
    python_root.mkdir()
    (go_root / "agent.go").write_text(GO_SOURCE, encoding="utf-8")
    (python_root / "agent.py").write_text(PYTHON_SOURCE, encoding="utf-8")

    go_result = StateMachineAnalyzer().analyze(
        GoAdapter().parse_semantic_project(go_root),
        [_invariant()],
    )
    python_result = StateMachineAnalyzer().analyze(
        PythonAdapter().parse_semantic_project(python_root),
        [_invariant()],
    )

    assert len(go_result.violations) == 1
    assert len(python_result.violations) == 1
    assert go_result.violations[0].event.language == "go"
    assert python_result.violations[0].event.language == "python"
