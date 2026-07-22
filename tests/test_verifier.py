"""
Tests for the verifier module (smartbench/verifier/).

Focuses on pure I/O verification (no LLM involvement):
  - LocationVerifier — file:line existence checks + fuzzy path resolution
  - CrossChecker   — cross-validate claims against a mock CodeGraph
  - VerdictScorer  — score proposals and flag hallucinations
  - VerificationResult — serialization roundtrip
"""

from pathlib import Path

import pytest

from smartbench.graph.retriever import GraphRetriever

# Re-use graph types for the mock CodeGraph
from smartbench.graph.schema import (
    CodeEdge,
    CodeGraph,
    CodeNode,
    EdgeType,
    NodeType,
)
from smartbench.verifier import VerificationResult, VerificationStatus
from smartbench.verifier.cross_checker import CrossChecker
from smartbench.verifier.location import LocationVerifier
from smartbench.verifier.sandbox import SandboxVerifier
from smartbench.verifier.scorer import VerdictScorer
from smartbench.verifier.verifier import Verifier

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

    def test_string_line_is_normalized_and_malformed_line_is_unverifiable(
        self, tmp_path: Path
    ):
        root = _create_project(tmp_path, {"main.py": "value = 1\n"})
        verifier = LocationVerifier(str(root))

        valid = verifier.verify("main.py", line="1")
        invalid = verifier.verify("main.py", line="not-a-line")

        assert valid.status == VerificationStatus.VERIFIED
        assert valid.resolved_line == 1
        assert invalid.status == VerificationStatus.UNVERIFIABLE
        assert "格式无效" in invalid.detail


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

    def test_duplicate_filename_requires_unambiguous_path_context(
        self, tmp_path: Path
    ):
        root = _create_project(tmp_path, {
            "api/helper.py": "api = True\n",
            "worker/helper.py": "worker = True\n",
        })
        verifier = LocationVerifier(str(root))

        assert verifier._fuzzy_resolve("helper.py") is None
        assert verifier._fuzzy_resolve("worker/missing/helper.py") == (
            "worker/helper.py"
        )


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

    def test_find_function_declared_on_first_line(self, tmp_path: Path):
        root = _create_project(tmp_path, {
            "app.py": "def first():\n    return 1\n",
        })
        verifier = LocationVerifier(str(root))

        result = verifier.verify("app.py", line=1, function_name="first")

        assert result.actual_function == "first"
        assert result.status == VerificationStatus.VERIFIED

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

    def test_malformed_nested_claims_do_not_abort_verification(
        self, mock_graph, mock_graph_retriever, mock_project
    ):
        checker = CrossChecker(mock_graph, str(mock_project), mock_graph_retriever)
        proposals = [{
            "title": "malformed evidence",
            "location": "server.py:1",
            "problem": ["not", "text"],
            "implementation_steps": "not-a-list",
            "evidence_claims": None,
        }]

        result = checker.verify_proposals(proposals)
        critique = checker.verify_critique(
            {"verdicts": "not-a-list"}, result
        )

        assert result[0]["__verification"]["claim_count"] == 1
        assert critique["__verification"]["verdicts_checked"] == 0


def test_verification_stats_tolerate_malformed_scores(
    mock_graph, mock_graph_retriever, mock_project
):
    verifier = Verifier(str(mock_project), mock_graph, mock_graph_retriever)

    stats = verifier.get_verification_stats([
        {"__verification": {
            "verdict": "verified",
            "verification_score": "not-a-number",
        }},
        {"__verification": "not-an-object"},
    ])

    assert stats["verified"] == 1
    assert stats["overall_score"] == 0.0


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


# ======================================================================
# Patch sandbox verifier
# ======================================================================


def _create_testable_python_project(tmp_path: Path, expected: int = 1) -> Path:
    return _create_project(tmp_path, {
        "pyproject.toml": "[project]\nname='sandbox-fixture'\nversion='0.0.0'\n",
        "sample.py": "def value():\n    return 1\n",
        "tests/test_sample.py": (
            "from sample import value\n\n"
            f"def test_value():\n    assert value() == {expected}\n"
        ),
    })


class TestSandboxVerifier:
    """Only explicit unified diffs may be reported as test-verified."""

    def test_copy_keeps_legacy_source_files(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        (project / "legacy").mkdir()
        (project / "legacy" / "old.py").write_text("value = 1\n")
        destination = tmp_path / "copy"
        destination.mkdir()

        SandboxVerifier(str(project))._copy_project(destination)

        assert (destination / "legacy" / "old.py").read_text() == "value = 1\n"

    def test_skips_natural_language_only_suggestion(self, tmp_path: Path):
        project = _create_testable_python_project(tmp_path)
        verifier = SandboxVerifier(str(project))

        result = verifier.verify_fix(
            "sample.py", 1, "Change the return value", patch=""
        )

        assert result["status"] == "skipped"
        assert result["patch_applied"] is False
        assert "unified diff" in result["error"]

    def test_applies_patch_and_runs_tests(self, tmp_path: Path):
        project = _create_testable_python_project(tmp_path)
        patch = """--- a/sample.py
+++ b/sample.py
@@ -1,2 +1,3 @@
 def value():
+    \"\"\"Return the fixture value.\"\"\"
     return 1
"""
        result = SandboxVerifier(str(project)).verify_fix(
            "sample.py", 1, "Add documentation", patch=patch
        )

        assert result["status"] == "passed"
        assert result["patch_applied"] is True
        assert result["error"] is None
        assert result["sandbox_path"]
        assert not Path(result["sandbox_path"]).exists()

    def test_reports_failure_after_patch(self, tmp_path: Path):
        project = _create_testable_python_project(tmp_path)
        patch = """--- a/sample.py
+++ b/sample.py
@@ -1,2 +1,2 @@
 def value():
-    return 1
+    return 2
"""
        result = SandboxVerifier(str(project)).verify_fix(
            "sample.py", 2, "Change behavior", patch=patch
        )

        assert result["status"] == "failed"
        assert result["patch_applied"] is True
        assert "1 failed" in result["test_output"]

    def test_refuses_to_judge_when_baseline_fails(self, tmp_path: Path):
        project = _create_testable_python_project(tmp_path, expected=2)
        patch = """--- a/sample.py
+++ b/sample.py
@@ -1,2 +1,3 @@
 def value():
+    # proposed change
     return 1
"""
        result = SandboxVerifier(str(project)).verify_fix(
            "sample.py", 1, "Add comment", patch=patch
        )

        assert result["status"] == "baseline_failed"
        assert result["patch_applied"] is False

    def test_rejects_paths_outside_project(self, tmp_path: Path):
        project = _create_testable_python_project(tmp_path)
        (tmp_path / "outside.py").write_text("secret = True\n", encoding="utf-8")

        result = SandboxVerifier(str(project)).verify_fix(
            "../outside.py", 1, "Modify outside file", patch="not relevant"
        )

        assert result["status"] == "skipped"
        assert "outside the project" in result["error"]

    def test_rejects_traversal_in_patch_headers(self, tmp_path: Path):
        project = _create_testable_python_project(tmp_path)
        patch = """--- a/sample.py
+++ b/../outside.py
@@ -1,2 +1,2 @@
-old
+new
"""
        result = SandboxVerifier(str(project)).verify_fix(
            "sample.py", 1, "Unsafe patch", patch=patch
        )

        assert result["status"] == "skipped"
        assert "outside the project" in result["error"]

    def test_rejects_shell_string_test_command(self, tmp_path: Path):
        project = _create_testable_python_project(tmp_path)
        patch = """--- a/sample.py
+++ b/sample.py
@@ -1,2 +1,3 @@
 def value():
+    # safe
     return 1
"""
        result = SandboxVerifier(str(project)).verify_fix(
            "sample.py",
            1,
            "Add comment",
            patch=patch,
            test_command="pytest || true",
        )

        assert result["status"] == "skipped"
        assert "argument list" in result["error"]

    def test_batch_isolates_malformed_proposal_fields(self, tmp_path: Path):
        project = _create_testable_python_project(tmp_path)
        proposals = [
            {"location": None, "patch": ""},
            {"location": "sample.py:1", "patch": ["not", "text"]},
        ]

        result = SandboxVerifier(str(project)).verify_all_proposals(proposals)

        assert result[0]["__sandbox_verification"]["status"] == "skipped"
        assert result[1]["__sandbox_verification"]["status"] == "skipped"
        assert "unified-diff string" in result[1]["__sandbox_verification"]["error"]

    def test_rejects_non_string_test_command_elements(self, tmp_path: Path):
        project = _create_testable_python_project(tmp_path)
        result = SandboxVerifier(str(project)).verify_fix(
            "sample.py",
            1,
            "test",
            patch="--- a/sample.py\n+++ b/sample.py\n",
            test_command=["pytest", None],
        )

        assert result["status"] == "skipped"
        assert "No test command" in result["error"]
