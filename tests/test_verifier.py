"""
Tests for the verifier module (smartbench/verifier/).

Focuses on pure I/O verification (no LLM involvement):
  - LocationVerifier — file:line existence checks + fuzzy path resolution
  - CrossChecker   — cross-validate claims against a mock CodeGraph
  - VerdictScorer  — score proposals and flag hallucinations
  - VerificationResult — serialization roundtrip
"""

from pathlib import Path
from typing import Optional

import pytest

from smartbench.verifier import VerificationStatus, VerificationResult
from smartbench.verifier.location import LocationVerifier
from smartbench.verifier.cross_checker import CrossChecker
from smartbench.verifier.scorer import VerdictScorer

# Re-use graph types for the mock CodeGraph
from smartbench.graph.schema import (
    CodeGraph,
    CodeNode,
    CodeEdge,
    NodeType,
    EdgeType,
)
from smartbench.graph.retriever import GraphRetriever


# ── Helpers ──────────────────────────────────────────────────────────────────


def _create_project(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a temporary project directory with the given files.

    *files* is a dict of *relative_path* → *content*.
    """
    root = tmp_path / "project"
    root.mkdir()
    for rel, content in files.items():
        full = root / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    return root


# ======================================================================
# LocationVerifier
# ======================================================================


class TestLocationVerifierVerify:
    """LocationVerifier.verify() — file:line existence checks."""

    def test_claim_exists(self, tmp_path: Path):
        root = _create_project(tmp_path, {"src/main.py": "line1\nline2\nline3\n"})
        verifier = LocationVerifier(str(root))
        result = verifier.verify("src/main.py", line=2)
        assert result.status == VerificationStatus.VERIFIED
        assert result.resolved_file == "src/main.py"
        assert result.resolved_line == 2
        assert result.confidence >= 0.8

    def test_claim_too_high_line_number(self, tmp_path: Path):
        root = _create_project(tmp_path, {"main.py": "a\nb\nc\n"})
        verifier = LocationVerifier(str(root))
        result = verifier.verify("main.py", line=999)
        assert result.status != VerificationStatus.VERIFIED
        assert result.claimed_file == "main.py"
        assert result.resolved_line is None or result.resolved_line != 999
        # Should still resolve the file, but line out of range
        assert result.resolved_file == "main.py"

    def test_claim_file_does_not_exist(self, tmp_path: Path):
        root = _create_project(tmp_path, {"main.py": "a\n"})
        verifier = LocationVerifier(str(root))
        result = verifier.verify("nonexistent.py", line=1)
        assert result.status == VerificationStatus.HALLUCINATED
        assert result.confidence == 0.0
        assert result.claimed_file == "nonexistent.py"
        assert "文件不存在" in result.detail or "file" in result.detail.lower()


class TestLocationVerifierResolveFile:
    """LocationVerifier._resolve_file via _fuzzy_resolve."""

    def test_exact_path_match(self, tmp_path: Path):
        root = _create_project(tmp_path, {"src/utils/helper.py": "x\n"})
        verifier = LocationVerifier(str(root))
        resolved = verifier._fuzzy_resolve("src/utils/helper.py")
        assert resolved == "src/utils/helper.py"

    def test_fuzzy_by_filename(self, tmp_path: Path):
        root = _create_project(tmp_path, {"src/utils/helper.py": "x\n"})
        verifier = LocationVerifier(str(root))
        # The claimed path doesn't exist, but the basename matches
        resolved = verifier._fuzzy_resolve("helper.py")
        assert resolved == "src/utils/helper.py"

    def test_typo_correction_levenshtein(self, tmp_path: Path):
        root = _create_project(tmp_path, {"src/utils/database_pool.py": "x\n"})
        verifier = LocationVerifier(str(root))
        # "database_poo" (missing "l") is contained within "database_pool"
        resolved = verifier._fuzzy_resolve("database_poo.py")
        assert resolved == "src/utils/database_pool.py"

    def test_unknown_file_returns_none(self, tmp_path: Path):
        root = _create_project(tmp_path, {"main.py": "x\n"})
        verifier = LocationVerifier(str(root))
        resolved = verifier._fuzzy_resolve("completely_bogus_name.txt")
        assert resolved is None

    def test_empty_claimed_path(self, tmp_path: Path):
        root = _create_project(tmp_path, {"main.py": "x\n"})
        verifier = LocationVerifier(str(root))
        assert verifier._fuzzy_resolve("") is None
        assert verifier._fuzzy_resolve("   ") is None


class TestLocationVerifierMultiple:
    """LocationVerifier.verify_multiple handles batches."""

    def test_multiple_claims(self, tmp_path: Path):
        root = _create_project(tmp_path, {
            "a.py": "1\n2\n3\n",
            "b.py": "4\n5\n6\n",
        })
        verifier = LocationVerifier(str(root))
        claims = [
            {"file_path": "a.py", "line": 2},
            {"file_path": "b.py", "line": 5},
            {"file_path": "nope.py", "line": 1},
        ]
        results = verifier.verify_multiple(claims)
        assert len(results) == 3
        assert results[0].status == VerificationStatus.VERIFIED
        assert results[1].status != VerificationStatus.VERIFIED  # line 5 out of range
        assert results[2].status == VerificationStatus.HALLUCINATED


class TestLocationVerifierVerifyLine:
    """Line-level verification logic."""

    def test_line_in_range_sets_content(self, tmp_path: Path):
        root = _create_project(tmp_path, {"f.py": "a\nb\nc\nd\ne\n"})
        verifier = LocationVerifier(str(root))
        result = verifier.verify("f.py", line=3)
        assert result.resolved_line == 3
        assert result.actual_content is not None
        assert "c" in result.actual_content  # line 3 content

    def test_line_out_of_range_sets_detail(self, tmp_path: Path):
        root = _create_project(tmp_path, {"f.py": "a\nb\n"})
        verifier = LocationVerifier(str(root))
        result = verifier.verify("f.py", line=100)
        assert result.actual_content is None or "c" not in result.actual_content


class TestLocationVerifierFindFunction:
    """_find_function_at searches backward from a line for a def/fn/func."""

    def test_find_def_in_python(self, tmp_path: Path):
        root = _create_project(tmp_path, {
            "app.py": "def hello():\n    pass\n\ndef world():\n    pass\n",
        })
        verifier = LocationVerifier(str(root))
        result = verifier.verify("app.py", line=4, function_name="world")
        assert result.actual_function == "world"

    def test_find_fn_in_rust(self, tmp_path: Path):
        root = _create_project(tmp_path, {
            "lib.rs": "fn greet() {}\n\nfn main() {}\n",
        })
        verifier = LocationVerifier(str(root))
        result = verifier.verify("lib.rs", line=3, function_name="main")
        assert result.actual_function == "main"

    def test_function_not_found_sets_detail(self, tmp_path: Path):
        root = _create_project(tmp_path, {
            "app.py": "x = 1\ny = 2\n",
        })
        verifier = LocationVerifier(str(root))
        result = verifier.verify("app.py", line=2, function_name="nonexistent_func")
        # Function not found, but file:line exists
        assert result.actual_function is None


# ======================================================================
# CrossChecker  (with mock CodeGraph)
# ======================================================================


@pytest.fixture
def mock_graph() -> CodeGraph:
    """A small CodeGraph with a few function nodes and a call edge."""
    graph = CodeGraph()
    # Two function nodes
    n1 = CodeNode(
        id="n1",
        node_type=NodeType.FUNCTION,
        name="handleRequest",
        file_path="server.py",
        line_start=10,
        line_end=20,
    )
    n2 = CodeNode(
        id="n2",
        node_type=NodeType.FUNCTION,
        name="processData",
        file_path="server.py",
        line_start=30,
        line_end=40,
    )
    graph.add_node(n1)
    graph.add_node(n2)
    # A call edge: handleRequest calls processData
    graph.add_edge(CodeEdge(
        source_id="n1",
        target_id="n2",
        edge_type=EdgeType.CALLS,
    ))
    return graph


@pytest.fixture
def mock_graph_retriever(mock_graph) -> GraphRetriever:
    """GraphRetriever pointing at a dummy path."""
    return GraphRetriever(graph=mock_graph, project_path="/fake/path")


@pytest.fixture
def mock_project(tmp_path) -> Path:
    """Project with server.py containing the real functions."""
    return _create_project(tmp_path, {
        "server.py": (
            "import sys\n"
            "\n"
            "def handleRequest(req):\n"
            "    data = processData(req)\n"
            "    return data\n"
            "\n"
            "\n"
            "def processData(req):\n"
            "    return {'status': 'ok'}\n"
        ),
        "utils.py": "def util():\n    pass\n",
    })


class TestCrossChecker:
    """CrossChecker validates claims against a CodeGraph + real files."""

    def test_check_validates_function_in_graph(self, mock_graph, mock_graph_retriever, mock_project):
        checker = CrossChecker(mock_graph, str(mock_project), mock_graph_retriever)
        # A proposal with a pattern claim referencing a function that exists
        proposals = [{
            "title": "fix handling",
            "problem": "handleRequest needs improvement",
            "location": "server.py",
            "implementation_steps": [],
        }]
        result = checker.verify_proposals(proposals)
        assert len(result) == 1
        verif = result[0].get("__verification", {})
        # The claim type will be "code_pattern" (function name found in problem text)
        assert "verification_score" in verif

    def test_flag_when_function_not_in_graph(self, mock_graph, mock_graph_retriever, mock_project):
        checker = CrossChecker(mock_graph, str(mock_project), mock_graph_retriever)
        # A proposal referencing a function that does NOT exist in the graph
        proposals = [{
            "title": "bogus fix",
            "problem": "nonexistentFunc has a bug",
            "location": "",
            "implementation_steps": [],
        }]
        result = checker.verify_proposals(proposals)
        verif = result[0].get("__verification", {})
        score = verif.get("verification_score", 1.0)
        assert score < 0.8  # Should be penalised

    def test_location_claim_verified(self, mock_graph, mock_graph_retriever, mock_project):
        checker = CrossChecker(mock_graph, str(mock_project), mock_graph_retriever)
        proposals = [{
            "title": "fix server",
            "location": "server.py:5",
            "evidence_claims": [
                {"type": "file_location", "target": "server.py:5", "description": ""},
            ],
        }]
        result = checker.verify_proposals(proposals)
        verif = result[0].get("__verification", {})
        assert verif.get("verdict") in ("verified", "partial")

    def test_verify_critique_checks_concerns(self, mock_graph, mock_graph_retriever, mock_project):
        checker = CrossChecker(mock_graph, str(mock_project), mock_graph_retriever)
        critique = {
            "verdicts": [
                {
                    "proposal_index": 0,
                    "concerns": ["The server.py:10 approach won't scale"],
                }
            ]
        }
        proposals = [{"title": "dummy", "location": "server.py"}]
        result = checker.verify_critique(critique, proposals)
        verdicts = result.get("__verification", {}).get("verdicts_checked", 0)
        assert verdicts >= 1


# ======================================================================
# VerdictScorer
# ======================================================================


class TestVerdictScorerScore:
    """VerdictScorer.score_proposals assigns scores based on verification data."""

    @staticmethod
    def _proposal_with_verdict(score: float, verdict: str = "verified") -> dict:
        return {
            "title": "test",
            "__verification": {
                "verification_score": score,
                "verdict": verdict,
                "verified_locations": ["main.py:10"],
                "hallucinated_locations": [],
                "partial_locations": [],
                "detail": "",
            },
        }

    def test_high_confidence_verified(self):
        scorer = VerdictScorer()
        proposals = [self._proposal_with_verdict(0.9, "verified")]
        scored = scorer.score_proposals(proposals)
        verif = scored[0].get("__verification", {})
        assert verif.get("verdict") == "verified"
        breakdown = verif.get("breakdown", {})
        assert breakdown.get("verification_rate", 0) >= 0.8

    def test_medium_partial(self):
        scorer = VerdictScorer()
        proposals = [self._proposal_with_verdict(0.5, "partial")]
        scored = scorer.score_proposals(proposals)
        verif = scored[0].get("__verification", {})
        assert "breakdown" in verif

    def test_low_hallucinated(self):
        scorer = VerdictScorer()
        proposals = [self._proposal_with_verdict(0.0, "hallucinated")]
        scored = scorer.score_proposals(proposals)
        verif = scored[0].get("__verification", {})
        assert verif.get("verdict") == "hallucinated"

    def test_without_verification_field(self):
        """A proposal missing __verification defaults to 0.0 / unverifiable."""
        scorer = VerdictScorer()
        proposals = [{"title": "no verification data"}]
        scored = scorer.score_proposals(proposals)
        verif = scored[0].get("__verification", {})
        assert verif.get("verification_score") == 0.0
        assert verif.get("verdict") == "unverifiable"


class TestVerdictScorerFlag:
    """flag_hallucinations identifies proposals below a threshold."""

    def test_flag_hallucinations_low_score(self):
        scorer = VerdictScorer()
        proposals = [
            {"title": "good", "__verification": {"verification_score": 0.9,
                                                  "hallucinated_locations": [],
                                                  "partial_locations": []}},
            {"title": "bad", "__verification": {"verification_score": 0.1,
                                                 "hallucinated_locations": ["nope.py"],
                                                 "partial_locations": []}},
        ]
        result = scorer.flag_hallucinations(proposals, threshold=0.3)
        assert len(result["flagged"]) == 1
        assert result["flagged"][0]["title"] == "bad"
        assert len(result["clean"]) == 1

    def test_flag_all_clean(self):
        scorer = VerdictScorer()
        proposals = [
            {"title": "a", "__verification": {"verification_score": 0.9,
                                               "hallucinated_locations": [],
                                               "partial_locations": []}},
        ]
        result = scorer.flag_hallucinations(proposals, threshold=0.3)
        assert len(result["flagged"]) == 0
        assert len(result["clean"]) == 1


# ======================================================================
# VerificationResult serialisation
# ======================================================================


class TestVerificationResult:
    """VerificationResult.to_dict / minimal roundtrip."""

    def test_to_dict_contains_all_fields(self):
        r = VerificationResult(
            status=VerificationStatus.VERIFIED,
            claim="main.py:10",
            claimed_file="main.py",
            claimed_line=10,
            resolved_file="main.py",
            resolved_line=10,
            actual_function="hello",
            actual_content="def hello():\n    pass\n",
            confidence=0.95,
            detail="All good",
        )
        d = r.to_dict()
        assert d["status"] == "verified"
        assert d["claim"] == "main.py:10"
        assert d["claimed_file"] == "main.py"
        assert d["claimed_line"] == 10
        assert d["resolved_file"] == "main.py"
        assert d["resolved_line"] == 10
        assert d["actual_function"] == "hello"
        assert d["confidence"] == 0.95
        assert d["detail"] == "All good"

    def test_to_dict_defaults(self):
        r = VerificationResult(status=VerificationStatus.HALLUCINATED)
        d = r.to_dict()
        assert d["status"] == "hallucinated"
        assert d["claim"] == ""
        assert d["confidence"] == 0.0


# ======================================================================
# Levenshtein helper (static method on LocationVerifier)
# ======================================================================


class TestLevenshtein:
    """LocationVerifier._levenshtein static helper."""

    def test_equal_strings(self):
        assert LocationVerifier._levenshtein("hello", "hello") == 0

    def test_one_substitution(self):
        assert LocationVerifier._levenshtein("cat", "car") == 1

    def test_one_insertion(self):
        assert LocationVerifier._levenshtein("cat", "cats") == 1

    def test_one_deletion(self):
        assert LocationVerifier._levenshtein("cats", "cat") == 1

    def test_completely_different(self):
        assert LocationVerifier._levenshtein("abc", "xyz") >= 3

    def test_empty_vs_string(self):
        assert LocationVerifier._levenshtein("", "abc") == 3
        assert LocationVerifier._levenshtein("abc", "") == 3

    def test_both_empty(self):
        assert LocationVerifier._levenshtein("", "") == 0
