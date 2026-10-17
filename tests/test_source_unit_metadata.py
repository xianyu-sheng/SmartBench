"""Regression tests for issue #27 item 1: source-unit metadata must be measured."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from smartbench.core.adapters import GoAdapter, PythonAdapter
from smartbench.ir import schema as ir_schema
from smartbench.ir.schema import measure_source_unit


def _go_project(tmp_path: Path) -> Path:
    (tmp_path / "main.go").write_text(
        "package main\n\nfunc main() {}\n",
        encoding="utf-8",
    )
    return tmp_path


def test_go_source_unit_reports_real_line_count_and_hash(tmp_path: Path):
    """``ir_source_units.<go file>`` must not be a zero/empty stub."""
    project = _go_project(tmp_path)

    ir = GoAdapter().parse_semantic_project(project)
    unit = ir.source_units["main.go"]

    payload = (project / "main.go").read_bytes()
    assert unit.line_count == 3
    assert unit.content_hash == f"sha256:{hashlib.sha256(payload).hexdigest()}"
    assert unit.metadata_status == "ok"
    assert unit.to_dict()["metadata_status"] == "ok"
    assert unit.to_dict()["line_count"] == 3


def test_source_unit_metadata_is_language_agnostic(tmp_path: Path):
    (tmp_path / "app.py").write_text("a = 1\nb = 2\n", encoding="utf-8")

    ir = PythonAdapter().parse_semantic_project(tmp_path)
    unit = ir.source_units["app.py"]

    assert (unit.line_count, unit.metadata_status) == (2, "ok")
    assert unit.content_hash.startswith("sha256:")


def test_line_count_ignores_trailing_newline(tmp_path: Path):
    target = tmp_path / "no_trailing_newline.go"
    target.write_text("package main\nfunc main() {}", encoding="utf-8")

    assert measure_source_unit(target)[0] == 2


def test_unmeasurable_file_reports_explicit_status():
    """Placeholder values are only emitted together with a machine-readable reason."""
    assert measure_source_unit(None) == (0, "", "unavailable:unreadable")
    assert measure_source_unit(Path("does-not-exist-issue27.go")) == (
        0,
        "",
        "unavailable:unreadable",
    )


def test_oversized_file_reports_explicit_status(tmp_path: Path, monkeypatch):
    target = tmp_path / "big.go"
    target.write_text("package main\n", encoding="utf-8")
    monkeypatch.setattr(ir_schema, "SOURCE_UNIT_METADATA_MAX_BYTES", 4)

    assert measure_source_unit(target) == (0, "", "unavailable:file-too-large")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
