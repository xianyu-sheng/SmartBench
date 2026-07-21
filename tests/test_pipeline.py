"""
End-to-end pipeline integration tests.

Tests the full SmartBench pipeline with mock LLM responses:
  Phase 1 → Phase 4 → Phase 3 → Phase 5
"""

import json
import pytest
from pathlib import Path
from io import StringIO

from rich.console import Console

from smartbench.detector.scanner import ProjectScanner
from smartbench.detector.fingerprint import Language
from smartbench.graph.builder import CodeGraphBuilder
from smartbench.graph.retriever import GraphRetriever
from smartbench.prompts.factory import PromptFactory
from smartbench.engine.debate import DebateEngine, DebateResult


# ═══════════════════════════════════════════════════════════════════════
# Mock LLM responses
# ═══════════════════════════════════════════════════════════════════════

STRATEGY_RESPONSE = json.dumps({
    "selected_strategy": "architecture_review",
    "confidence": 0.85,
    "reasoning": "Project has clear modular structure worth reviewing",
    "parameter_overrides": {"focus_areas": ["modularity"]},
    "alternative_strategies": ["correctness_audit"],
    "estimated_duration_minutes": 3,
})

PROPOSER_RESPONSE = json.dumps({
    "analysis": {
        "root_cause": "Large CLI module with mixed concerns",
        "impact_assessment": "Makes testing and extension difficult",
    },
    "proposals": [
        {
            "title": "Split CLI into modules",
            "location": "smartbench/cli/main.py:1",
            "problem": "Multiple concerns in single file",
            "solution": "Extract into separate submodules",
            "implementation_steps": [
                "Move display to cli/display.py",
                "Move phases to cli/phases.py",
            ],
            "evidence_claims": [
                {
                    "type": "file_location",
                    "target": "smartbench/cli/main.py:1",
                    "description": "Main CLI entry point",
                }
            ],
            "expected_improvement": "Better testability",
            "priority": 4,
            "risk_level": "low",
        }
    ],
})

CRITIQUE_RESPONSE = json.dumps({
    "verdicts": [
        {
            "proposal_title": "Split CLI into modules",
            "verdict": "accept",
            "concerns": ["Ensure backward compatibility"],
            "evidence_issues": [],
            "suggested_modifications": "Keep public API unchanged",
        }
    ],
    "overall_assessment": "Reasonable refactoring with low risk",
})

JUDGE_RESPONSE = json.dumps({
    "decision": "accepted",
    "reasoning": "Well-scoped improvement with clear implementation path",
    "final_suggestions": [
        {
            "title": "Split CLI into modules",
            "description": "Extract display, phases, and wizard from main.py",
            "implementation": "Create cli/display.py, cli/phases.py, cli/wizard.py",
            "location": "smartbench/cli/main.py",
            "evidence_status": "verified",
            "priority": 4,
            "risk_level": "low",
            "consensus": "high",
        }
    ],
    "rejected_proposals": [],
    "risk_summary": "No deployment risk — internal refactor only",
})


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def project_path():
    """Path to SmartBench itself — used as test project."""
    return str(Path(__file__).parent.parent.resolve())


@pytest.fixture(scope="module")
def fingerprint(project_path):
    """Cached project fingerprint."""
    scanner = ProjectScanner(project_path)
    return scanner.scan()


@pytest.fixture(scope="module")
def code_graph(project_path, fingerprint):
    """Cached code graph."""
    builder = CodeGraphBuilder(use_treesitter=True)
    return builder.build(project_path, fingerprint.primary_language)


@pytest.fixture
def mock_llm_factory():
    """Create a mock LLM function from a sequence of responses."""

    def _make(responses):
        queue = list(responses)

        def llm_fn(prompt, role=""):
            return queue.pop(0) if queue else "{}"

        return llm_fn

    return _make


@pytest.fixture
def null_console():
    """Console that writes to nowhere."""
    return Console(file=StringIO(), force_terminal=True)


# ═══════════════════════════════════════════════════════════════════════
# Phase 1: Fingerprint
# ═══════════════════════════════════════════════════════════════════════

class TestPhase1Fingerprint:
    """Phase 1: Deterministic project detection."""

    def test_detects_language(self, fingerprint):
        assert fingerprint.primary_language == Language.PYTHON
        assert fingerprint.language_confidence > 0.5

    def test_detects_framework(self, fingerprint):
        assert fingerprint.framework is not None

    def test_finds_source_files(self, fingerprint):
        assert fingerprint.source_files > 20

    def test_has_manifest(self, fingerprint):
        """Should detect at least one manifest file."""
        assert len(fingerprint.manifest_files) > 0

    def test_detects_git(self, fingerprint):
        assert fingerprint.is_git_repo is True

    def test_summary_is_string(self, fingerprint):
        summary = fingerprint.summary()
        assert isinstance(summary, str)
        assert len(summary) > 10


# ═══════════════════════════════════════════════════════════════════════
# Phase 4: Code Graph
# ═══════════════════════════════════════════════════════════════════════

class TestPhase4CodeGraph:
    """Phase 4: Code graph construction."""

    def test_graph_has_nodes(self, code_graph):
        from smartbench.graph.schema import NodeType
        assert len(code_graph.nodes) > 0
        funcs = [
            n for n in code_graph.nodes.values()
            if n.node_type == NodeType.FUNCTION
        ]
        assert len(funcs) > 50

    def test_graph_has_edges(self, code_graph):
        assert len(code_graph.edges) > 0

    def test_graph_builder_finds_key_functions(self, code_graph):
        from smartbench.graph.schema import NodeType
        func_names = {
            n.name
            for n in code_graph.nodes.values()
            if n.node_type == NodeType.FUNCTION
        }
        key_funcs = ["debate", "build", "retrieve", "scan"]
        for name in key_funcs:
            assert name in func_names, f"'{name}' should be in code graph"

    def test_graph_retriever_returns_context(self, code_graph, project_path):
        retriever = GraphRetriever(
            code_graph, project_path, max_tokens_estimate=4000
        )
        context = retriever.retrieve("debate")
        # Should find code containing "debate" — at minimum the debate engine itself
        assert len(context) > 50

    def test_graph_retriever_empty_query(self, code_graph, project_path):
        retriever = GraphRetriever(
            code_graph, project_path, max_tokens_estimate=4000
        )
        context = retriever.retrieve("")
        assert isinstance(context, str)


# ═══════════════════════════════════════════════════════════════════════
# Phase 3 + 5: Strategy + Debate (mocked LLM)
# ═══════════════════════════════════════════════════════════════════════

class TestFullDebatePipeline:
    """End-to-end: strategy selection → debate → result."""

    def test_full_pipeline_with_mock_llm(
        self, fingerprint, code_graph, project_path, mock_llm_factory
    ):
        """Simulate complete pipeline with controlled LLM responses."""
        factory = PromptFactory(fingerprint)
        llm = mock_llm_factory([
            STRATEGY_RESPONSE,
            PROPOSER_RESPONSE,
            CRITIQUE_RESPONSE,
            JUDGE_RESPONSE,
        ])

        # Strategy selection
        strategy_prompt = factory.build_strategy_prompt(
            "architecture review",
            [
                {"name": "architecture_review",
                 "description": "Design patterns, coupling",
                 "tools": ["code_graph"]},
            ],
        )
        strategy_raw = llm(strategy_prompt)
        strategy = json.loads(strategy_raw)
        assert strategy["selected_strategy"] == "architecture_review"

        # Build analysis context
        retriever = GraphRetriever(
            code_graph, project_path, max_tokens_estimate=4000
        )
        code_context = retriever.retrieve("architecture review")
        analysis_context = factory.build_analysis_context(
            code_context=code_context,
            user_symptoms="Review the architecture",
        )

        # Debate
        engine = DebateEngine(llm, prompt_factory=factory)
        result = engine.debate(
            analysis_context, target="architecture review"
        )

        # Assertions
        assert isinstance(result, DebateResult)
        assert result.consensus_reached is True
        assert result.iterations == 3
        assert len(result.final_suggestions) >= 1
        assert result.final_suggestions[0]["title"] == "Split CLI into modules"
        assert result.duration_ms >= 0

    def test_debate_with_verifier(
        self, fingerprint, code_graph, project_path, mock_llm_factory
    ):
        """Test debate with evidence verifier enabled."""
        from smartbench.verifier.verifier import Verifier
        from smartbench.graph.retriever import GraphRetriever

        factory = PromptFactory(fingerprint)
        llm = mock_llm_factory([
            PROPOSER_RESPONSE,
            CRITIQUE_RESPONSE,
            JUDGE_RESPONSE,
        ])

        retriever = GraphRetriever(
            code_graph, project_path, max_tokens_estimate=4000
        )
        verifier = Verifier(
            project_path=project_path,
            graph=code_graph,
            graph_retriever=retriever,
        )

        analysis_context = factory.build_analysis_context(
            user_symptoms="architecture review",
        )

        engine = DebateEngine(llm, prompt_factory=factory, verifier=verifier)
        result = engine.debate(analysis_context)

        assert result.consensus_reached is True
        # The proposer's file claim should be verified
        assert len(result.final_suggestions) >= 1

    def test_debate_log_complete(
        self, fingerprint, mock_llm_factory
    ):
        """Verify debate log captures all phases."""
        factory = PromptFactory(fingerprint)
        llm = mock_llm_factory([
            PROPOSER_RESPONSE,
            CRITIQUE_RESPONSE,
            JUDGE_RESPONSE,
        ])

        analysis_context = factory.build_analysis_context(
            user_symptoms="test",
        )
        engine = DebateEngine(llm, prompt_factory=factory)
        result = engine.debate(analysis_context)

        roles = [entry["role"] for entry in result.debate_log]
        assert roles == ["proposer", "critique", "judge"]
        for entry in result.debate_log:
            assert "input" in entry
            assert "output" in entry

    def test_pipeline_with_verifier_annotations(
        self, fingerprint, code_graph, project_path, mock_llm_factory
    ):
        """Verifier should annotate proposals with verification data."""
        from smartbench.verifier.verifier import Verifier
        from smartbench.graph.retriever import GraphRetriever

        factory = PromptFactory(fingerprint)
        llm = mock_llm_factory([
            PROPOSER_RESPONSE,
            CRITIQUE_RESPONSE,
            JUDGE_RESPONSE,
        ])

        retriever = GraphRetriever(
            code_graph, project_path, max_tokens_estimate=4000
        )
        verifier = Verifier(
            project_path=project_path,
            graph=code_graph,
            graph_retriever=retriever,
        )

        # Directly verify proposals
        proposals = json.loads(PROPOSER_RESPONSE)["proposals"]
        verified = verifier.verify_proposals(proposals)

        assert len(verified) == 1
        assert "__verification" in verified[0]
        verif = verified[0]["__verification"]
        assert "verdict" in verif
        assert "verification_score" in verif

        # The CLI main.py file should exist, so score should be reasonable
        assert verif["verification_score"] > 0


# ═══════════════════════════════════════════════════════════════════════
# Phase orchestration
# ═══════════════════════════════════════════════════════════════════════

class TestPhaseOrchestration:
    """Test the orchestration functions in cli.phases."""

    def test_resolve_project_path_local(self, null_console, project_path):
        from smartbench.cli.phases import resolve_project_path
        result = resolve_project_path(null_console, project_path)
        assert result == project_path

    def test_resolve_project_path_invalid(self, null_console):
        from smartbench.cli.phases import resolve_project_path
        result = resolve_project_path(
            null_console, "/nonexistent/path/xyz123"
        )
        assert result is None

    def test_phase1_integration(self, null_console, project_path):
        from smartbench.cli.phases import run_phase1_detection
        fp = run_phase1_detection(null_console, project_path)
        assert fp.primary_language == Language.PYTHON
        assert fp.source_files > 0

    def test_phase4_integration(self, null_console, project_path, fingerprint):
        from smartbench.cli.phases import run_phase4_graph
        graph, retriever = run_phase4_graph(
            null_console, project_path, fingerprint, build_rag=False
        )
        assert graph is not None
        assert len(graph.nodes) > 0
        # RAG should be None since we passed build_rag=False
        # (but it might still be None if dependencies are missing)

    def test_fallback_analysis_no_api(self, null_console, project_path, fingerprint):
        from smartbench.cli.phases import run_fallback_analysis
        # Should not crash when no API keys configured
        run_fallback_analysis(
            null_console, project_path, fingerprint, None, "test"
        )
        # If it doesn't raise, it passes


# ═══════════════════════════════════════════════════════════════════════
# Tool execution integration
# ═══════════════════════════════════════════════════════════════════════

class TestToolExecution:
    """Test that diagnostic tools execute and return formatted output."""

    @pytest.fixture
    def project_path(self):
        return str(Path(__file__).parent.parent.resolve())

    def test_tools_run_for_python(self, null_console, project_path):
        from smartbench.diagnostics.executor import run_tools_for_strategy
        from smartbench.detector.fingerprint import Language

        result = run_tools_for_strategy(
            null_console, project_path,
            Language.PYTHON, "performance_analysis",
        )
        assert len(result) > 100
        assert "建议" in result or "diagnostic" in result.lower() or "tool" in result.lower()

    def test_tools_empty_for_unknown_strategy(self, null_console, project_path):
        from smartbench.diagnostics.executor import run_tools_for_strategy
        from smartbench.detector.fingerprint import Language

        result = run_tools_for_strategy(
            null_console, project_path,
            Language.PYTHON, "nonexistent_strategy",
        )
        assert result == ""

    def test_all_strategies_produce_output(self, null_console, project_path):
        from smartbench.diagnostics.executor import run_tools_for_strategy
        from smartbench.detector.fingerprint import Language

        for strategy in [
            "performance_analysis", "correctness_audit",
            "security_scan", "architecture_review",
        ]:
            result = run_tools_for_strategy(
                null_console, project_path,
                Language.PYTHON, strategy,
            )
            assert len(result) > 50, f"{strategy} should produce output"


# ═══════════════════════════════════════════════════════════════════════
# RAG evaluation
# ═══════════════════════════════════════════════════════════════════════

class TestRAGPipeline:
    """Test the RAG pipeline end-to-end."""

    def test_index_and_retrieve(self, code_graph, fingerprint):
        from smartbench.rag.indexer import IndexPipeline
        from smartbench.rag.retriever import HybridRetriever

        project_path = str(Path(__file__).parent.parent.resolve())
        indexer = IndexPipeline(project_path, fingerprint)
        store, embedder = indexer.index_if_needed(code_graph)

        assert store.count() > 0, "Should have indexed some chunks"

        hybrid = HybridRetriever(
            code_graph, project_path, store, embedder, min_score=0.1
        )
        context = hybrid.retrieve("debate engine")
        assert len(context) > 100, "Should retrieve relevant context"

    def test_evaluator_loads_queries(self):
        from smartbench.rag.evaluator import RAGEvaluator
        import os

        queries_path = os.path.join(
            os.path.dirname(__file__), "fixtures", "rag_eval_queries.json"
        )
        project_path = str(Path(__file__).parent.parent.resolve())
        evaluator = RAGEvaluator(None, project_path)
        evaluator.load_queries(queries_path)
        assert len(evaluator.queries) == 12
        assert evaluator.queries[0].expected_file == "smartbench/engine/debate.py"
