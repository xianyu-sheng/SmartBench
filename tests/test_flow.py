"""Tests for deterministic data-flow analysis and rule integration."""

from pathlib import Path

import pytest

from smartbench.core.rules.flow import DataFlowSecurityRule
from smartbench.flow.analyzer import HAS_TREE_SITTER, DataFlowAnalyzer
from smartbench.flow.schema import (
    AbstractValue,
    FindingEvidence,
    SourceLocation,
    TaintState,
    TraceStep,
)
from smartbench.graph.schema import CodeGraph, CodeNode, NodeType
from smartbench.graph.tree_parser import get_parser

HAS_FLOW_PARSERS = HAS_TREE_SITTER and all(
    get_parser(language) is not None for language in ("python", "typescript")
)


def make_location(file_path: str = "test.ts") -> SourceLocation:
    return SourceLocation(
        file_path=file_path,
        start_byte=0,
        end_byte=10,
        start_row=1,
        start_column=0,
        end_row=1,
        end_column=10,
    )


class TestSchema:
    def test_location_round_trip(self):
        location = make_location()
        assert SourceLocation.from_dict(location.to_dict()) == location

    def test_unicode_snippet_uses_byte_offsets(self):
        source = "甲 = 'value'"
        location = SourceLocation(
            file_path="unicode.py",
            start_byte=0,
            end_byte=len("甲".encode()),
            start_row=1,
            start_column=0,
            end_row=1,
            end_column=1,
        )
        assert location.get_source_snippet(source) == "甲"

    def test_taint_state_combinations(self):
        assert TaintState.TAINTED.combine(TaintState.UNKNOWN) == TaintState.TAINTED
        assert TaintState.UNKNOWN.combine(TaintState.NOT_TAINTED) == TaintState.UNKNOWN
        assert TaintState.NOT_TAINTED.combine(TaintState.NOT_TAINTED) == TaintState.NOT_TAINTED

    def test_abstract_value_round_trip(self):
        location = make_location()
        step = TraceStep(location, "source", "req.query")
        value = AbstractValue(
            location=location,
            taint_state=TaintState.TAINTED,
            taint_trace=(step,),
            operations=("member access",),
        )
        assert AbstractValue.from_dict(value.to_dict()) == value

    def test_evidence_round_trip(self):
        location = make_location()
        step = TraceStep(location, "source", "req.query")
        evidence = FindingEvidence(
            sink_snippet="db.query(query)",
            sink_location=location,
            taint_trace=(step,),
            source_snippet="req.query",
            source_location=location,
        )
        assert FindingEvidence.from_dict(evidence.to_dict()) == evidence


@pytest.mark.skipif(not HAS_FLOW_PARSERS, reason="tree-sitter graph extra unavailable")
class TestDataFlowAnalyzer:
    def analyze(self, source: str, language: str = "typescript"):
        suffix = "py" if language == "python" else "ts"
        return DataFlowAnalyzer().analyze_file(
            f"sample.{suffix}",
            source,
            language,
        )

    def test_known_request_source_is_high_confidence(self):
        result = self.analyze(
            """
async function lookup(req) {
  const id = req.query.id;
  return db.query(`SELECT * FROM users WHERE id = ${id}`);
}
"""
        )
        assert result.success is True
        assert len(result.findings) == 1
        finding = result.findings[0]
        assert finding.rule_id == "sql_injection_flow"
        assert finding.confidence == 0.95
        assert finding.metadata["taint_state"] == "tainted"
        assert finding.evidence.taint_trace

    def test_function_parameter_is_reported_as_unproven(self):
        result = self.analyze(
            """
class Index {
  async remove(rows: any[]) {
    const ids = rows.map((row) => row.id).join(",");
    return db.run(`DELETE FROM items WHERE id IN (${ids})`);
  }
}
"""
        )
        assert result.success is True
        assert len(result.findings) == 1
        finding = result.findings[0]
        assert finding.confidence == 0.75
        assert finding.severity == "warning"
        assert "caller control is unproven" in finding.metadata["reason"]

    def test_parameterized_query_is_not_reported(self):
        result = self.analyze(
            'async function lookup(id) { return db.query("SELECT * FROM users WHERE id = ?", [id]); }'
        )
        assert result.success is True
        assert result.findings == []

    def test_generated_placeholders_are_not_tainted(self):
        result = self.analyze(
            """
async function lookup(values) {
  const placeholders = values.map(() => "?").join(",");
  const query = `SELECT * FROM users WHERE id IN (${placeholders})`;
  return db.query(query, values);
}
"""
        )
        assert result.success is True
        assert result.findings == []

    def test_non_database_get_is_not_a_sql_sink(self):
        result = self.analyze("function lookup(input) { return listeners.get(input); }")
        assert result.findings == []

    def test_relative_import_is_not_path_traversal(self):
        result = self.analyze('import { readFile } from "../../../files";')
        assert result.findings == []

    def test_request_path_reaching_fs_is_reported(self):
        result = self.analyze("function read(req) { return fs.readFile(req.query.path); }")
        assert [finding.rule_id for finding in result.findings] == ["path_traversal_flow"]
        assert result.findings[0].confidence == 0.95

    def test_request_command_reaching_exec_is_reported(self):
        result = self.analyze("function run(req) { return child_process.exec(req.body.command); }")
        assert [finding.rule_id for finding in result.findings] == ["command_injection_flow"]

    def test_python_request_source_is_tracked(self):
        result = self.analyze(
            """
def lookup(request):
    user_id = request.args.get("id")
    query = f"SELECT * FROM users WHERE id = {user_id}"
    cursor.execute(query)
""",
            language="python",
        )
        assert result.success is True
        assert len(result.findings) == 1
        assert result.findings[0].confidence == 0.95

    def test_python_parameterized_query_is_not_reported(self):
        result = self.analyze(
            """
def lookup(user_id):
    cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
""",
            language="python",
        )
        assert result.findings == []

    def test_unsupported_language_is_explicit_failure(self):
        result = self.analyze("package main", language="go")
        assert result.success is False
        assert result.error_message == "Language not supported: go"


@pytest.mark.skipif(not HAS_FLOW_PARSERS, reason="tree-sitter graph extra unavailable")
def test_rule_integration_reads_graph_files(tmp_path: Path):
    source_file = tmp_path / "route.ts"
    source_file.write_text(
        "function read(req) { return fs.readFile(req.query.path); }",
        encoding="utf-8",
    )
    graph = CodeGraph(meta={"project_path": str(tmp_path)})
    graph.add_node(
        CodeNode(
            id="route",
            node_type=NodeType.FUNCTION,
            name="read",
            file_path="route.ts",
            language="typescript",
        )
    )

    findings = DataFlowSecurityRule().analyze(graph)

    assert len(findings) == 1
    assert findings[0].rule_id == "path_traversal_flow"
    assert "evidence" in findings[0].metadata
    assert "fix_suggestion" in findings[0].metadata
