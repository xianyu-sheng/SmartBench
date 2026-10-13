"""Mutation oracles for the Python async/exception lowering contract."""

from __future__ import annotations

import ast

import pytest

from smartbench.core.adapters import PythonAdapter
from smartbench.frontends.python import _FlowFragment, _PythonFileLowerer
from smartbench.ir import OperationEdgeKind, OperationKind

SOURCE = """
async def worker(client):
    try:
        await client.fetch()
    except TimeoutError as exc:
        raise RuntimeError("retry") from exc
    finally:
        await client.close()
""".strip()


def _oracle(tmp_path):
    (tmp_path / "worker.py").write_text(SOURCE, encoding="utf-8")
    ir = PythonAdapter().parse_semantic_project(tmp_path)
    calls = {operation.target: operation for operation in ir.operations if operation.kind == OperationKind.CALL}
    raised = next(
        (operation for operation in ir.operations if operation.kind == OperationKind.RAISE),
        None,
    )
    assert raised is not None
    cleanup = calls["client.close"]
    assert calls["client.fetch"].attributes["awaited"] is True
    assert cleanup.attributes["awaited"] is True
    assert any(
        edge.source_id == raised.id
        and edge.target_id == cleanup.id
        and edge.kind == OperationEdgeKind.NEXT
        for edge in ir.operation_edges
    )


def test_mutant_removing_raise_lowering_is_killed(tmp_path, monkeypatch):
    original = _PythonFileLowerer._lower_statement

    def mutant(self, node, scope_id):
        if isinstance(node, ast.Raise):
            return _FlowFragment()
        return original(self, node, scope_id)

    monkeypatch.setattr(_PythonFileLowerer, "_lower_statement", mutant)
    with pytest.raises(AssertionError):
        _oracle(tmp_path)


def test_mutant_dropping_terminal_tracking_is_killed(tmp_path, monkeypatch):
    original = _PythonFileLowerer._lower_statement

    def mutant(self, node, scope_id):
        result = original(self, node, scope_id)
        if isinstance(node, ast.Raise):
            return _FlowFragment(
                entry_id=result.entry_id,
                fallthroughs=result.fallthroughs,
                breaks=result.breaks,
                continues=result.continues,
            )
        return result

    monkeypatch.setattr(_PythonFileLowerer, "_lower_statement", mutant)
    with pytest.raises(AssertionError):
        _oracle(tmp_path)


def test_mutant_dropping_await_marker_is_killed(tmp_path, monkeypatch):
    monkeypatch.setattr(_PythonFileLowerer, "_is_awaited_call", lambda self, node: False)
    with pytest.raises(AssertionError):
        _oracle(tmp_path)
