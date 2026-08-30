"""Tests for FORBID_ACTION_AFTER_EVENT and REQUIRE_EXIT_AFTER_EVENT invariant kinds."""

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


# ---------------------------------------------------------------------------
# FORBID_ACTION_AFTER_EVENT fixtures
# ---------------------------------------------------------------------------

# Transaction double-finalize: Rollback() followed by Commit() is always wrong.
TX_DOUBLE_FINALIZE_BEFORE = """
package sample

import "database/sql"

func transfer(tx *sql.Tx, amount float64) error {
    if amount <= 0 {
        tx.Rollback()
        tx.Commit()
        return nil
    }
    return tx.Commit()
}
""".strip()

TX_DOUBLE_FINALIZE_AFTER = """
package sample

import "database/sql"

func transfer(tx *sql.Tx, amount float64) error {
    if amount <= 0 {
        tx.Rollback()
        return nil
    }
    return tx.Commit()
}
""".strip()

# A variant where Rollback and Commit are on unrelated paths (no violation).
TX_SEPARATE_PATHS = """
package sample

import "database/sql"

func transfer(tx *sql.Tx, ok bool) error {
    if !ok {
        return tx.Rollback()
    }
    return tx.Commit()
}
""".strip()


def _forbid_invariant() -> StateInvariant:
    return StateInvariant(
        invariant_id="no-commit-after-rollback",
        kind=InvariantKind.FORBID_ACTION_AFTER_EVENT,
        event=OperationSelector.of(OperationKind.CALL, contains_all=["Rollback"]),
        action=OperationSelector.of(OperationKind.CALL, contains_all=["Commit"]),
        message="Commit must not be called after Rollback on the same transaction",
    )


def _analyze_go(tmp_path: Path, source: str, invariants):
    (tmp_path / "main.go").write_text(source, encoding="utf-8")
    ir = GoAdapter().parse_semantic_project(tmp_path)
    return StateMachineAnalyzer().analyze(ir, invariants)


# ---------------------------------------------------------------------------
# FORBID_ACTION_AFTER_EVENT tests
# ---------------------------------------------------------------------------


def test_forbid_detects_commit_after_rollback(tmp_path: Path):
    result = _analyze_go(tmp_path, TX_DOUBLE_FINALIZE_BEFORE, [_forbid_invariant()])
    assert len(result.violations) >= 1
    assert result.violations[0].missing == "forbidden_action"


def test_forbid_no_violation_when_action_absent(tmp_path: Path):
    result = _analyze_go(tmp_path, TX_DOUBLE_FINALIZE_AFTER, [_forbid_invariant()])
    assert result.violations == []


def test_forbid_no_violation_on_separate_paths(tmp_path: Path):
    """Rollback and Commit on mutually exclusive if/else branches — no violation."""
    result = _analyze_go(tmp_path, TX_SEPARATE_PATHS, [_forbid_invariant()])
    assert result.violations == []


def test_forbid_violation_carries_both_locations(tmp_path: Path):
    result = _analyze_go(tmp_path, TX_DOUBLE_FINALIZE_BEFORE, [_forbid_invariant()])
    pack = result.to_evidence_pack("no commit after rollback")
    assert len(pack.facts) >= 1
    # Both event (Rollback) and action (Commit) lines are in evidence.
    assert len(pack.evidence) >= 2


def test_forbid_distinguishes_pre_and_post_fix(tmp_path: Path):
    before = _analyze_go(tmp_path, TX_DOUBLE_FINALIZE_BEFORE, [_forbid_invariant()])
    after = _analyze_go(tmp_path, TX_DOUBLE_FINALIZE_AFTER, [_forbid_invariant()])
    assert len(before.violations) >= 1
    assert after.violations == []


# ---------------------------------------------------------------------------
# REQUIRE_EXIT_AFTER_EVENT fixtures
# ---------------------------------------------------------------------------

# HTTP handler that calls http.Error but falls through to write JSON — classic
# "superfluous WriteHeader" / double-write bug.
HTTP_FALLTHROUGH_BEFORE = """
package sample

import "net/http"

func handleRequest(w http.ResponseWriter, r *http.Request) {
    data, err := fetchData(r)
    if err != nil {
        http.Error(w, "error", http.StatusInternalServerError)
    }
    writeJSON(w, data)
}
""".strip()

HTTP_FALLTHROUGH_AFTER = """
package sample

import "net/http"

func handleRequest(w http.ResponseWriter, r *http.Request) {
    data, err := fetchData(r)
    if err != nil {
        http.Error(w, "error", http.StatusInternalServerError)
        return
    }
    writeJSON(w, data)
}
""".strip()

# A variant where writeJSON is only reachable on the success path (no fallthrough).
HTTP_GATED = """
package sample

import "net/http"

func handleRequest(w http.ResponseWriter, r *http.Request) {
    data, err := fetchData(r)
    if err == nil {
        writeJSON(w, data)
        return
    }
    http.Error(w, "error", http.StatusInternalServerError)
}
""".strip()


def _require_exit_invariant() -> StateInvariant:
    return StateInvariant(
        invariant_id="return-after-http-error",
        kind=InvariantKind.REQUIRE_EXIT_AFTER_EVENT,
        event=OperationSelector.of(OperationKind.CALL, contains_all=["http.Error"]),
        action=OperationSelector.of(OperationKind.CALL, contains_all=["writeJSON"]),
        message="handler must return after writing an error response",
    )


# ---------------------------------------------------------------------------
# REQUIRE_EXIT_AFTER_EVENT tests
# ---------------------------------------------------------------------------


def test_require_exit_detects_fallthrough(tmp_path: Path):
    result = _analyze_go(tmp_path, HTTP_FALLTHROUGH_BEFORE, [_require_exit_invariant()])
    assert len(result.violations) >= 1
    assert result.violations[0].missing == "exit"


def test_require_exit_no_violation_when_return_present(tmp_path: Path):
    result = _analyze_go(tmp_path, HTTP_FALLTHROUGH_AFTER, [_require_exit_invariant()])
    assert result.violations == []


def test_require_exit_no_violation_when_action_gated(tmp_path: Path):
    """writeJSON only reachable on success path — http.Error and writeJSON never co-occur."""
    result = _analyze_go(tmp_path, HTTP_GATED, [_require_exit_invariant()])
    assert result.violations == []


def test_require_exit_distinguishes_pre_and_post_fix(tmp_path: Path):
    before = _analyze_go(tmp_path, HTTP_FALLTHROUGH_BEFORE, [_require_exit_invariant()])
    after = _analyze_go(tmp_path, HTTP_FALLTHROUGH_AFTER, [_require_exit_invariant()])
    assert len(before.violations) >= 1
    assert after.violations == []


def test_require_exit_violation_carries_both_locations(tmp_path: Path):
    result = _analyze_go(tmp_path, HTTP_FALLTHROUGH_BEFORE, [_require_exit_invariant()])
    pack = result.to_evidence_pack("return after http error")
    assert len(pack.facts) >= 1
    assert len(pack.evidence) >= 2


# ---------------------------------------------------------------------------
# Both invariants share the StateMachineAnalyzer result schema
# ---------------------------------------------------------------------------


def test_invariant_kind_reported_in_missing_field(tmp_path: Path):
    """FORBID reports 'forbidden_action', REQUIRE_EXIT reports 'exit'."""
    forbid = _analyze_go(tmp_path, TX_DOUBLE_FINALIZE_BEFORE, [_forbid_invariant()])
    (tmp_path / "main.go").write_text(HTTP_FALLTHROUGH_BEFORE, encoding="utf-8")
    ir = GoAdapter().parse_semantic_project(tmp_path)
    exit_result = StateMachineAnalyzer().analyze(ir, [_require_exit_invariant()])

    assert forbid.violations[0].missing == "forbidden_action"
    assert exit_result.violations[0].missing == "exit"


def test_invariants_evaluated_counter_increments(tmp_path: Path):
    result = _analyze_go(
        tmp_path,
        TX_DOUBLE_FINALIZE_BEFORE,
        [_forbid_invariant(), _require_exit_invariant()],
    )
    assert result.invariants_evaluated == 2


def test_python_forbid_action_after_event(tmp_path: Path):
    """FORBID_ACTION_AFTER_EVENT works on Python sources too."""
    source = """
def finalize(conn):
    conn.rollback()
    conn.commit()
""".strip()
    (tmp_path / "db.py").write_text(source, encoding="utf-8")
    ir = PythonAdapter().parse_semantic_project(tmp_path)
    invariant = StateInvariant(
        invariant_id="py-no-commit-after-rollback",
        kind=InvariantKind.FORBID_ACTION_AFTER_EVENT,
        event=OperationSelector.of(OperationKind.CALL, contains_all=["rollback"]),
        action=OperationSelector.of(OperationKind.CALL, contains_all=["commit"]),
        message="commit must not follow rollback",
    )
    result = StateMachineAnalyzer().analyze(ir, [invariant])
    assert len(result.violations) >= 1
    assert result.violations[0].missing == "forbidden_action"
