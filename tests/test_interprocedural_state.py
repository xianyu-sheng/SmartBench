"""Cross-function state proofs must be explicit and conservative."""

from pathlib import Path

from smartbench.analysis import (
    InvariantKind,
    OperationSelector,
    SemanticLinker,
    StateInvariant,
    StateMachineAnalyzer,
    StateScope,
    load_state_rule_file,
)
from smartbench.core.adapters import PythonAdapter
from smartbench.core.rules.state_machine import DeclarativeStateRule
from smartbench.ir import OperationKind


def _invariant() -> StateInvariant:
    return StateInvariant(
        invariant_id="event-before-retry",
        kind=InvariantKind.REQUIRE_GUARD_BEFORE_ACTION,
        event=OperationSelector.of(OperationKind.CALL, contains_all=("event",)),
        guard=OperationSelector.of(OperationKind.BRANCH, contains_all=("completed",)),
        action=OperationSelector.of(OperationKind.CALL, contains_all=("retry",)),
        scope=StateScope.INTERPROCEDURAL,
        max_call_depth=2,
        message="retry requires a completed-state guard",
    )


def _analyze(tmp_path: Path, source: str):
    (tmp_path / "app.py").write_text(source.strip(), encoding="utf-8")
    ir = PythonAdapter().parse_semantic_project(tmp_path)
    link = SemanticLinker().link(ir)
    SemanticLinker.apply(ir, link)
    return StateMachineAnalyzer().analyze(ir, [_invariant()])


def test_interprocedural_state_rule_detects_cross_function_violation(tmp_path: Path):
    result = _analyze(
        tmp_path,
        """
def worker():
    retry()

def run():
    event()
    worker()
""",
    )

    assert len(result.violations) == 1
    violation = result.violations[0]
    assert violation.missing == "guard"
    assert violation.path is not None
    assert result.interprocedural_paths == 1
    assert result.interprocedural_unknowns == 0
    fact = violation.to_fact()
    assert fact.attributes["proof_scope"] == "interprocedural"
    assert "call_enter" in fact.attributes["path_arcs"]


def test_interprocedural_state_rule_accepts_controlling_callee_guard(tmp_path: Path):
    result = _analyze(
        tmp_path,
        """
def worker():
    if completed():
        return
    retry()

def run():
    event()
    worker()
""",
    )

    assert result.violations == []
    assert result.interprocedural_paths == 1
    assert result.interprocedural_unknowns == 0


def test_unproven_caller_guard_abstains_instead_of_reporting(tmp_path: Path):
    result = _analyze(
        tmp_path,
        """
def worker():
    retry()

def run():
    event()
    if completed():
        worker()
    else:
        worker()
""",
    )

    assert result.violations == []
    assert result.interprocedural_paths >= 1
    assert result.interprocedural_unknowns >= 1


def test_declarative_interprocedural_scope_reaches_rule_bridge(tmp_path: Path):
    rule_path = tmp_path / "rule.yaml"
    rule_path.write_text(
        """
version: smartbench.state-rules/v1
rules:
  - id: cross-function-event-retry
    name: Cross-function event retry
    languages: [python]
    severity: error
    message: retry requires a completed-state guard
    invariant:
      scope: interprocedural
      max_call_depth: 2
      kind: require_guard_before_action
      event: {kinds: [call], contains_all: [event]}
      guard: {kinds: [branch], contains_all: [completed]}
      action: {kinds: [call], contains_all: [retry]}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        """
def worker():
    retry()

def run():
    event()
    worker()
""".strip(),
        encoding="utf-8",
    )
    definitions = load_state_rule_file(rule_path)
    ir = PythonAdapter().parse_semantic_project(tmp_path)
    link = SemanticLinker().link(ir)
    SemanticLinker.apply(ir, link)
    findings = DeclarativeStateRule(definitions[0]).analyze(ir)

    assert len(findings) == 1
    assert findings[0].metadata["scope"] == "interprocedural"
    assert findings[0].metadata["max_call_depth"] == 2
