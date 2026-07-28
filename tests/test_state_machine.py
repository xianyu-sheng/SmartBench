"""Language-neutral state-machine invariant analysis."""

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


def test_call_guard_is_proven_on_event_to_action_path(tmp_path: Path):
    source = """
def run(request, stack, response):
    try:
        body = request.form()
        stack.push_async_callback(body.close)
    except Exception:
        raise
    response.headers.raw.extend([])
""".strip()
    (tmp_path / "handler.py").write_text(source, encoding="utf-8")
    ir = PythonAdapter().parse_semantic_project(tmp_path)
    invariant = StateInvariant(
        invariant_id="form-cleanup-before-response",
        kind=InvariantKind.REQUIRE_GUARD_BEFORE_ACTION,
        event=OperationSelector.of(OperationKind.CALL, contains_all=("request.form",)),
        guard=OperationSelector.of(
            OperationKind.CALL, contains_all=("push_async_callback",)
        ),
        action=OperationSelector.of(
            OperationKind.CALL, contains_all=("response.headers.raw.extend",)
        ),
        message="form parsing must register cleanup before response",
    )

    result = StateMachineAnalyzer().analyze(ir, [invariant])

    assert result.violations == []

    (tmp_path / "handler.py").write_text(
        source.replace("        stack.push_async_callback(body.close)\n", ""),
        encoding="utf-8",
    )
    before_ir = PythonAdapter().parse_semantic_project(tmp_path)
    before = StateMachineAnalyzer().analyze(before_ir, [invariant])
    assert len(before.violations) == 1
    assert before.violations[0].missing == "guard"


def test_go_switch_and_if_initializer_preserve_cleanup_path(tmp_path: Path):
    source = """
package sample

func load(format string) error {
    switch format {
    case "file":
        file, err := os.Open(format)
        if err != nil {
            return err
        }
        defer file.Close()
        if _, err = parseTemplate(file); err != nil {
            return err
        }
    }
    return nil
}
""".strip()
    path = tmp_path / "loader.go"
    path.write_text(source, encoding="utf-8")
    invariant = StateInvariant(
        invariant_id="template-file-cleanup",
        kind=InvariantKind.REQUIRE_GUARD_BEFORE_ACTION,
        event=OperationSelector.of(OperationKind.CALL, contains_all=("os.Open",)),
        guard=OperationSelector.of(
            OperationKind.DEFER, contains_all=("file.Close",)
        ),
        action=OperationSelector.of(
            OperationKind.ASSIGN, contains_all=("parseTemplate",)
        ),
        message="opened template file must register cleanup before parsing",
    )

    after = StateMachineAnalyzer().analyze(
        GoAdapter().parse_semantic_project(tmp_path), [invariant]
    )
    assert after.violations == []

    path.write_text(source.replace("        defer file.Close()\n", ""), encoding="utf-8")
    before = StateMachineAnalyzer().analyze(
        GoAdapter().parse_semantic_project(tmp_path), [invariant]
    )
    assert len(before.violations) == 1
    assert before.violations[0].missing == "guard"
