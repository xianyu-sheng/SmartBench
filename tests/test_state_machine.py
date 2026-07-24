"""Language-neutral state-machine invariant analysis."""

from pathlib import Path

import pytest

from smartbench.analysis import (
    InvariantKind,
    OperationSelector,
    StateInvariant,
    StateMachineAnalyzer,
)
from smartbench.core.adapters import GoAdapter
from smartbench.graph.tree_parser import get_parser
from smartbench.ir import OperationKind

pytestmark = pytest.mark.skipif(get_parser("go") is None, reason="tree-sitter Go unavailable")


PRE_FIX = '''
package sample

func run(output string) error {
    for {
        if !ready(output) {
            retries++
            continue
        }
        return nil
    }
}
'''.strip()


POST_FIX = '''
package sample

func run(output string) error {
    for {
        if !ready(output) {
            if completed(output) {
                return nil
            }
            retries++
            continue
        }
        return nil
    }
}
'''.strip()


CONVERGING_GUARD = '''
package sample

func run(output string) error {
    for {
        if !ready(output) {
            if completed(output) {
                log("completed")
            }
            retries++
            continue
        }
        return nil
    }
}
'''.strip()


def _invariant() -> StateInvariant:
    return StateInvariant(
        invariant_id="terminal-before-retry",
        kind=InvariantKind.REQUIRE_GUARD_BEFORE_ACTION,
        event=OperationSelector.of(OperationKind.BRANCH, contains_all=("ready",)),
        guard=OperationSelector.of(OperationKind.BRANCH, contains_all=("completed",)),
        action=OperationSelector.of(OperationKind.CONTINUE),
        message="retry requires a terminal-state guard",
    )


def _analyze(tmp_path: Path, source: str):
    (tmp_path / "agent.go").write_text(source, encoding="utf-8")
    ir = GoAdapter().parse_semantic_project(tmp_path)
    return StateMachineAnalyzer().analyze(ir, [_invariant()])


def test_state_invariant_distinguishes_pre_and_post_fix(tmp_path: Path):
    before = _analyze(tmp_path, PRE_FIX)
    after = _analyze(tmp_path, POST_FIX)

    assert len(before.violations) == 1
    assert before.violations[0].missing == "guard"
    assert after.violations == []


def test_state_violation_produces_source_backed_evidence(tmp_path: Path):
    result = _analyze(tmp_path, PRE_FIX)
    pack = result.to_evidence_pack("terminal state before retry", "graph-v1")

    assert len(pack.facts) == 1
    assert len(pack.evidence) == 2
    assert {ref.source for ref in pack.evidence} == {"go_frontend"}
    assert pack.graph_version == "graph-v1"


def test_converging_guard_does_not_hide_a_retry_violation(tmp_path: Path):
    result = _analyze(tmp_path, CONVERGING_GUARD)

    assert len(result.violations) == 1
    assert result.violations[0].missing == "guard"
